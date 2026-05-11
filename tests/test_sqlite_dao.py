from datetime import datetime, timedelta, timezone

import pytest

from prospective_harness.exceptions import ImmutablePredictionError
from prospective_harness.hashing import content_hash
from prospective_harness.models import PredictionId
from prospective_harness.sqlite_dao import SQLitePredictionDAO


def make_dao(tmp_path):
    return SQLitePredictionDAO(tmp_path / "harness.sqlite3")


def test_dao_adds_and_loads_prediction_with_hash(tmp_path):
    dao = make_dao(tmp_path)
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    payload = {"probability": 0.7}
    digest = content_hash({"model_id": "m1", "dataset_hash": "abc", "prediction": payload})

    pid = dao.add_prediction("m1", "abc", payload, digest, registered_at)
    loaded = dao.get_prediction(pid)

    assert isinstance(pid, PredictionId)
    assert loaded is not None
    assert loaded.id == pid
    assert loaded.model_id == "m1"
    assert loaded.dataset_hash == "abc"
    assert loaded.prediction == payload
    assert loaded.prediction_hash == digest
    assert loaded.registered_at == registered_at


def test_dao_update_prediction_always_raises_immutability_error(tmp_path):
    dao = make_dao(tmp_path)

    with pytest.raises(ImmutablePredictionError):
        dao.update_prediction(PredictionId("missing"), {"probability": 0.2})


def test_dao_adds_and_loads_outcome(tmp_path):
    dao = make_dao(tmp_path)
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    pid = dao.add_prediction("m1", "abc", {"probability": 0.7}, "x" * 64, registered_at)
    observed_at = registered_at + timedelta(hours=1)

    dao.add_outcome(pid, {"label": 1}, observed_at, observed_at + timedelta(minutes=1))
    outcome = dao.get_outcome(pid)

    assert outcome is not None
    assert outcome.prediction_id == pid
    assert outcome.outcome == {"label": 1}
    assert outcome.observed_at == observed_at


def test_dao_rejects_duplicate_outcome_as_immutable(tmp_path):
    dao = make_dao(tmp_path)
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    pid = dao.add_prediction("m1", "abc", {"probability": 0.7}, "x" * 64, registered_at)
    observed_at = registered_at + timedelta(hours=1)

    dao.add_outcome(pid, {"label": 1}, observed_at, observed_at)

    with pytest.raises(ImmutablePredictionError):
        dao.add_outcome(pid, {"label": 0}, observed_at + timedelta(hours=1), observed_at + timedelta(hours=1))


def test_dao_filters_predictions_by_model_and_time_window(tmp_path):
    dao = make_dao(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    inside = start + timedelta(hours=1)
    outside = start - timedelta(hours=1)
    end = start + timedelta(days=1)

    kept = dao.add_prediction("m1", "a", {"probability": 0.1}, "a" * 64, inside)
    dao.add_prediction("m1", "b", {"probability": 0.2}, "b" * 64, outside)
    dao.add_prediction("m2", "c", {"probability": 0.3}, "c" * 64, inside)

    rows = dao.list_predictions_with_outcomes("m1", (start, end))

    assert [row.prediction.id for row in rows] == [kept]
