from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from prospective_harness.exceptions import DataIntegrityError, ImmutablePredictionError
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


def test_dao_recomputes_hash_on_read_and_detects_inserted_tampered_row(tmp_path):
    dao = make_dao(tmp_path)
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    with dao.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            INSERT INTO predictions (
                id, model_id, dataset_hash, prediction_json, prediction_hash,
                registered_at_us, created_at_us
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tampered",
                "m1",
                "abc",
                '{"probability":0.99}',
                "0" * 64,
                int(registered_at.timestamp() * 1_000_000),
                int(registered_at.timestamp() * 1_000_000),
            ),
        )

    with pytest.raises(DataIntegrityError):
        dao.get_prediction(PredictionId("tampered"))


def test_sqlite_triggers_reject_direct_prediction_update_and_delete(tmp_path):
    dao = make_dao(tmp_path)
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    payload = {"probability": 0.7}
    pid = dao.add_prediction("m1", "abc", payload, content_hash({"model_id": "m1", "dataset_hash": "abc", "prediction": payload}), registered_at)

    with dao.engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("UPDATE predictions SET prediction_json = ? WHERE id = ?", ('{"probability":0.2}', str(pid)))
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("DELETE FROM predictions WHERE id = ?", (str(pid),))


def test_dao_adds_and_loads_outcome(tmp_path):
    dao = make_dao(tmp_path)
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    payload = {"probability": 0.7}
    pid = dao.add_prediction("m1", "abc", payload, content_hash({"model_id": "m1", "dataset_hash": "abc", "prediction": payload}), registered_at)
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
    payload = {"probability": 0.7}
    pid = dao.add_prediction("m1", "abc", payload, content_hash({"model_id": "m1", "dataset_hash": "abc", "prediction": payload}), registered_at)
    observed_at = registered_at + timedelta(hours=1)

    dao.add_outcome(pid, {"label": 1}, observed_at, observed_at)

    with pytest.raises(ImmutablePredictionError):
        dao.add_outcome(pid, {"label": 0}, observed_at + timedelta(hours=1), observed_at + timedelta(hours=1))


def test_sqlite_triggers_reject_direct_outcome_update_and_delete(tmp_path):
    dao = make_dao(tmp_path)
    registered_at = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    payload = {"probability": 0.7}
    pid = dao.add_prediction("m1", "abc", payload, content_hash({"model_id": "m1", "dataset_hash": "abc", "prediction": payload}), registered_at)
    observed_at = registered_at + timedelta(hours=1)
    dao.add_outcome(pid, {"label": 1}, observed_at, observed_at)

    with dao.engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("UPDATE outcomes SET outcome_json = ? WHERE prediction_id = ?", ('{"label":0}', str(pid)))
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql("DELETE FROM outcomes WHERE prediction_id = ?", (str(pid),))


def test_dao_filters_predictions_by_model_and_time_window_in_sql(tmp_path):
    dao = make_dao(tmp_path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    inside = start + timedelta(hours=1)
    outside = start - timedelta(hours=1)
    end = start + timedelta(days=1)

    p1 = {"probability": 0.1}
    p2 = {"probability": 0.2}
    p3 = {"probability": 0.3}
    kept = dao.add_prediction("m1", "a", p1, content_hash({"model_id": "m1", "dataset_hash": "a", "prediction": p1}), inside)
    dao.add_prediction("m1", "b", p2, content_hash({"model_id": "m1", "dataset_hash": "b", "prediction": p2}), outside)
    dao.add_prediction("m2", "c", p3, content_hash({"model_id": "m2", "dataset_hash": "c", "prediction": p3}), inside)

    rows = dao.list_predictions_with_outcomes("m1", (start, end))

    assert [row.prediction.id for row in rows] == [kept]
