from datetime import datetime, timedelta, timezone

import pytest

import prospective_harness as ph
from prospective_harness.api import ProspectiveHarness
from prospective_harness.exceptions import PredictionNotFoundError, TemporalOrderingError, ImmutablePredictionError
from prospective_harness.models import PredictionId


def make_harness(tmp_path):
    return ProspectiveHarness(sqlite_path=tmp_path / "api.sqlite3")


def test_register_prediction_returns_id_and_persists_hash(tmp_path):
    harness = make_harness(tmp_path)

    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})
    stored = harness.dao.get_prediction(pid)

    assert isinstance(pid, PredictionId)
    assert stored is not None
    assert stored.prediction_hash and len(stored.prediction_hash) == 64


def test_record_outcome_accepts_observed_after_registration(tmp_path):
    harness = make_harness(tmp_path)
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})
    registered_at = harness.dao.get_prediction(pid).registered_at

    harness.record_outcome(pid, {"label": 1}, registered_at + timedelta(seconds=1))

    assert harness.dao.get_outcome(pid).outcome == {"label": 1}


def test_record_outcome_rejects_equal_observation_time(tmp_path):
    harness = make_harness(tmp_path)
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})
    registered_at = harness.dao.get_prediction(pid).registered_at

    with pytest.raises(TemporalOrderingError):
        harness.record_outcome(pid, {"label": 1}, registered_at)


def test_record_outcome_rejects_observation_before_registration(tmp_path):
    harness = make_harness(tmp_path)
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})
    registered_at = harness.dao.get_prediction(pid).registered_at

    with pytest.raises(TemporalOrderingError):
        harness.record_outcome(pid, {"label": 1}, registered_at - timedelta(seconds=1))


def test_record_outcome_rejects_unknown_prediction_id(tmp_path):
    harness = make_harness(tmp_path)

    with pytest.raises(PredictionNotFoundError):
        harness.record_outcome(PredictionId("missing"), {"label": 1}, datetime.now(timezone.utc))


def test_record_outcome_rejects_duplicate_outcome(tmp_path):
    harness = make_harness(tmp_path)
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})
    registered_at = harness.dao.get_prediction(pid).registered_at
    harness.record_outcome(pid, {"label": 1}, registered_at + timedelta(seconds=1))

    with pytest.raises(ImmutablePredictionError):
        harness.record_outcome(pid, {"label": 0}, registered_at + timedelta(seconds=2))


def test_api_rejects_invalid_prediction_and_outcome_payloads(tmp_path):
    harness = make_harness(tmp_path)

    with pytest.raises(ValueError):
        harness.register_prediction("model-a", "dataset-1", {"score": 0.6})

    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.6})
    registered_at = harness.dao.get_prediction(pid).registered_at

    with pytest.raises(ValueError):
        harness.record_outcome(pid, {"value": 1}, registered_at + timedelta(seconds=1))


def test_calibration_report_filters_model_and_window(tmp_path):
    harness = make_harness(tmp_path)
    p1 = harness.register_prediction("model-a", "dataset-1", {"probability": 0.25})
    p2 = harness.register_prediction("model-a", "dataset-2", {"probability": 0.75})
    p3 = harness.register_prediction("model-b", "dataset-3", {"probability": 0.95})
    r1 = harness.dao.get_prediction(p1).registered_at
    r2 = harness.dao.get_prediction(p2).registered_at
    r3 = harness.dao.get_prediction(p3).registered_at
    harness.record_outcome(p1, {"label": 0}, r1 + timedelta(seconds=1))
    harness.record_outcome(p2, {"label": 1}, r2 + timedelta(seconds=1))
    harness.record_outcome(p3, {"label": 1}, r3 + timedelta(seconds=1))

    report = harness.calibration_report("model-a", (r1 - timedelta(seconds=1), r2 + timedelta(seconds=1)))

    assert report.number_registered == 2
    assert report.number_realized == 2
    assert report.brier_score == pytest.approx(((0.25 - 0) ** 2 + (0.75 - 1) ** 2) / 2)
    assert report.calibration_curve[2].count == 1
    assert report.calibration_curve[7].count == 1


def test_calibration_report_counts_unrealized_predictions_without_metric_values(tmp_path):
    harness = make_harness(tmp_path)
    pid = harness.register_prediction("model-a", "dataset-1", {"probability": 0.25})
    registered_at = harness.dao.get_prediction(pid).registered_at

    report = harness.calibration_report("model-a", (registered_at - timedelta(seconds=1), registered_at + timedelta(seconds=1)))

    assert report.number_registered == 1
    assert report.number_realized == 0
    assert report.brier_score is None
    assert report.ece is None


def test_module_level_functions_use_default_harness(tmp_path, monkeypatch):
    import prospective_harness.api as api

    monkeypatch.setattr(api, "_default_harness", ProspectiveHarness(sqlite_path=tmp_path / "default.sqlite3"))
    pid = ph.register_prediction("model-a", "dataset-1", {"probability": 0.9})
    registered_at = api._default_harness.dao.get_prediction(pid).registered_at
    ph.record_outcome(pid, {"label": 1}, registered_at + timedelta(seconds=1))
    report = ph.calibration_report("model-a", (registered_at - timedelta(seconds=1), registered_at + timedelta(seconds=1)))

    assert report.number_registered == 1
    assert report.number_realized == 1
