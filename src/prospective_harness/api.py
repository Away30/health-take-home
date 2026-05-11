"""Public API for registering predictions and reporting calibration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .calibration import build_calibration_report, validate_outcome_label, validate_prediction_probability
from .dao import PredictionDAO
from .exceptions import PredictionNotFoundError, TemporalOrderingError
from .hashing import content_hash
from .models import CalibrationReport, PredictionId
from .sqlite_dao import SQLitePredictionDAO


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ProspectiveHarness:
    """Service layer enforcing prospective registration invariants."""

    def __init__(self, dao: PredictionDAO | None = None, sqlite_path: str | Path | None = None) -> None:
        if dao is not None and sqlite_path is not None:
            raise ValueError("provide either dao or sqlite_path, not both")
        self.dao: PredictionDAO = dao if dao is not None else SQLitePredictionDAO(sqlite_path or "prospective_harness.sqlite3")

    def register_prediction(self, model_id: str, dataset_hash: str, prediction: dict[str, Any]) -> PredictionId:
        validate_prediction_probability(prediction)
        registered_at = datetime.now(timezone.utc)
        digest = content_hash({"model_id": model_id, "dataset_hash": dataset_hash, "prediction": prediction})
        return self.dao.add_prediction(model_id, dataset_hash, prediction, digest, registered_at)

    def record_outcome(self, prediction_id: PredictionId, outcome: dict[str, Any], observed_at: datetime) -> None:
        validate_outcome_label(outcome)
        prediction = self.dao.get_prediction(PredictionId(str(prediction_id)))
        if prediction is None:
            raise PredictionNotFoundError(f"prediction not found: {prediction_id}")
        observed_at = _to_utc(observed_at)
        if observed_at <= prediction.registered_at:
            raise TemporalOrderingError("outcome observed_at must be strictly after prediction registration time")
        self.dao.add_outcome(prediction.id, outcome, observed_at, datetime.now(timezone.utc))

    def calibration_report(self, model_id: str, time_window: tuple[datetime, datetime]) -> CalibrationReport:
        start, end = (_to_utc(time_window[0]), _to_utc(time_window[1]))
        rows = self.dao.list_predictions_with_outcomes(model_id, (start, end))
        probabilities: list[float] = []
        labels: list[int] = []
        for row in rows:
            if row.outcome is None:
                continue
            probabilities.append(validate_prediction_probability(row.prediction.prediction))
            labels.append(validate_outcome_label(row.outcome.outcome))
        return build_calibration_report(
            model_id=model_id,
            time_window=(start, end),
            probabilities=probabilities,
            labels=labels,
            number_registered=len(rows),
        )


_default_harness: ProspectiveHarness | None = None


def _get_default_harness() -> ProspectiveHarness:
    global _default_harness
    if _default_harness is None:
        _default_harness = ProspectiveHarness()
    return _default_harness


def register_prediction(model_id: str, dataset_hash: str, prediction: dict[str, Any]) -> PredictionId:
    return _get_default_harness().register_prediction(model_id, dataset_hash, prediction)


def record_outcome(prediction_id: PredictionId, outcome: dict[str, Any], observed_at: datetime) -> None:
    _get_default_harness().record_outcome(prediction_id, outcome, observed_at)


def calibration_report(model_id: str, time_window: tuple[datetime, datetime]) -> CalibrationReport:
    return _get_default_harness().calibration_report(model_id, time_window)
