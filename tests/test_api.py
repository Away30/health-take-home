from datetime import datetime, timedelta, timezone

import pytest

import prospective_harness as ph
from prospective_harness.api import ProspectiveHarness
from prospective_harness.exceptions import ImmutablePredictionError, PredictionNotFoundError, TemporalOrderingError
from prospective_harness.hashing import content_hash
from prospective_harness.models import PredictionId


class SequenceClock:
    def __init__(self, *values):
        self.values = list(values)

    def __call__(self):
        if len(self.values) == 1:
            return self.values[0]
        return self.values.pop(0)


def make_harness(tmp_path, *, clock=None, max_recording_delay=timedelta(minutes=5)):
    return ProspectiveHarness(
        sqlite_path=tmp_path / "api.sqlite3",
        clock=clock,
        max_recording_delay=max_recording_delay,
    )


def test_register_prediction_returns_id_and_persists_hash(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    harness = make_harness(tmp_path, clock=SequenceClock(registered_at))

    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})
    stored = harness.dao.get_prediction(pid)

    assert isinstance(pid, PredictionId)
    assert stored is not None
    assert stored.prediction_hash == content_hash({"model_id": "model-a", "dataset_hash": "dataset-1", "prediction": {"probability": 0.6}})


def test_record_outcome_accepts_observed_after_registration_and_near_recording_time(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    observed_at = registered_at + timedelta(seconds=1)
    recorded_at = registered_at + timedelta(seconds=2)
    harness = make_harness(tmp_path, clock=SequenceClock(registered_at, recorded_at))
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})

    harness.record_outcome(pid, {"label": 1}, observed_at)

    assert harness.dao.get_outcome(pid).outcome == {"label": 1}


def test_record_outcome_rejects_equal_observation_time(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    harness = make_harness(tmp_path, clock=SequenceClock(registered_at, registered_at + timedelta(seconds=2)))
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})

    with pytest.raises(TemporalOrderingError):
        harness.record_outcome(pid, {"label": 1}, registered_at)


def test_record_outcome_rejects_observation_before_registration(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    harness = make_harness(tmp_path, clock=SequenceClock(registered_at, registered_at + timedelta(seconds=2)))
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})

    with pytest.raises(TemporalOrderingError):
        harness.record_outcome(pid, {"label": 1}, registered_at - timedelta(seconds=1))


def test_record_outcome_rejects_future_observation_time(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    recorded_at = registered_at + timedelta(seconds=2)
    harness = make_harness(tmp_path, clock=SequenceClock(registered_at, recorded_at))
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})

    with pytest.raises(TemporalOrderingError, match="future"):
        harness.record_outcome(pid, {"label": 1}, datetime(2099, 1, 1, tzinfo=timezone.utc))


def test_record_outcome_rejects_backdated_observation_after_long_delay(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    recorded_at = registered_at + timedelta(days=2)
    harness = make_harness(tmp_path, clock=SequenceClock(registered_at, recorded_at))
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})

    with pytest.raises(TemporalOrderingError, match="recorded too late"):
        harness.record_outcome(pid, {"label": 1}, registered_at + timedelta(seconds=1))


def test_record_outcome_allows_configured_recording_delay(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    observed_at = registered_at + timedelta(hours=1)
    recorded_at = observed_at + timedelta(hours=2)
    harness = make_harness(
        tmp_path,
        clock=SequenceClock(registered_at, recorded_at),
        max_recording_delay=timedelta(hours=3),
    )
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})

    harness.record_outcome(pid, {"label": 1}, observed_at)

    assert harness.dao.get_outcome(pid).observed_at == observed_at


def test_record_outcome_rejects_unknown_prediction_id(tmp_path):
    harness = make_harness(tmp_path, clock=SequenceClock(datetime(2026, 1, 1, 12, tzinfo=timezone.utc)))

    with pytest.raises(PredictionNotFoundError):
        harness.record_outcome(PredictionId("missing"), {"label": 1}, datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc))


def test_record_outcome_rejects_duplicate_outcome(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    first_recorded = registered_at + timedelta(seconds=2)
    second_recorded = registered_at + timedelta(seconds=4)
    harness = make_harness(tmp_path, clock=SequenceClock(registered_at, first_recorded, second_recorded))
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})
    harness.record_outcome(pid, {"label": 1}, registered_at + timedelta(seconds=1))

    with pytest.raises(ImmutablePredictionError):
        harness.record_outcome(pid, {"label": 0}, registered_at + timedelta(seconds=3))


def test_api_rejects_invalid_prediction_and_outcome_payloads(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    harness = make_harness(tmp_path, clock=SequenceClock(registered_at, registered_at + timedelta(seconds=2)))

    with pytest.raises(ValueError):
        harness.register_prediction("model-a", "dataset-1", {"score": 0.6})

    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})

    with pytest.raises(ValueError):
        harness.record_outcome(pid, {"value": 1}, registered_at + timedelta(seconds=1))


def test_calibration_report_filters_model_and_window(tmp_path):
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    harness = make_harness(
        tmp_path,
        clock=SequenceClock(
            t0,
            t0 + timedelta(seconds=1),
            t0 + timedelta(seconds=2),
            t0 + timedelta(seconds=3),
            t0 + timedelta(seconds=4),
            t0 + timedelta(seconds=5),
        ),
    )
    p1 = harness.register_prediction("model-a", "dataset-1", {"probability": 0.25})
    p2 = harness.register_prediction("model-a", "dataset-2", {"probability": 0.75})
    p3 = harness.register_prediction("model-b", "dataset-3", {"probability": 0.95})
    r1 = harness.dao.get_prediction(p1).registered_at
    r2 = harness.dao.get_prediction(p2).registered_at
    r3 = harness.dao.get_prediction(p3).registered_at
    harness.record_outcome(p1, {"label": 0}, r1 + timedelta(milliseconds=500))
    harness.record_outcome(p2, {"label": 1}, r2 + timedelta(milliseconds=500))
    harness.record_outcome(p3, {"label": 1}, r3 + timedelta(milliseconds=500))

    report = harness.calibration_report("model-a", (r1 - timedelta(seconds=1), r2 + timedelta(seconds=1)))

    assert report.number_registered == 2
    assert report.number_realized == 2
    assert report.brier_score == pytest.approx(((0.25 - 0) ** 2 + (0.75 - 1) ** 2) / 2)
    assert report.calibration_curve[2].count == 1
    assert report.calibration_curve[7].count == 1


def test_calibration_report_counts_unrealized_predictions_without_metric_values(tmp_path):
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    harness = make_harness(tmp_path, clock=SequenceClock(registered_at))
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.25})

    report = harness.calibration_report("model-a", (registered_at - timedelta(seconds=1), registered_at + timedelta(seconds=1)))

    assert report.number_registered == 1
    assert report.number_realized == 0
    assert report.brier_score is None
    assert report.ece is None


def test_module_level_functions_use_default_harness(tmp_path, monkeypatch):
    import prospective_harness.api as api

    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "_default_harness", ProspectiveHarness(sqlite_path=tmp_path / "default.sqlite3", clock=SequenceClock(t0, t0 + timedelta(seconds=2))))
    pid = ph.register_prediction("model-a", "dataset-1", {"probability": 0.9})
    ph.record_outcome(pid, {"label": 1}, t0 + timedelta(seconds=1))
    report = ph.calibration_report("model-a", (t0 - timedelta(seconds=1), t0 + timedelta(seconds=1)))

    assert report.number_registered == 1
    assert report.number_realized == 1
