"""Public API for registering predictions and reporting calibration.

The module-level convenience functions lazily create a SQLite database named
``prospective_harness.sqlite3`` in the caller's current working directory. Use
``ProspectiveHarness(sqlite_path=...)`` when the database location matters.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .calibration import build_calibration_report, validate_outcome_label, validate_prediction_probability
from .dao import PredictionDAO
from .exceptions import PredictionNotFoundError, TemporalOrderingError
from .hashing import content_hash
from .models import CalibrationReport, PredictionId
from .sqlite_dao import SQLitePredictionDAO

DEFAULT_MAX_RECORDING_DELAY = timedelta(minutes=5)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProspectiveHarness:
    """Service layer enforcing prospective registration invariants."""

    def __init__(
        self,
        dao: PredictionDAO | None = None,
        sqlite_path: str | Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        max_recording_delay: timedelta = DEFAULT_MAX_RECORDING_DELAY,
    ) -> None:
        if dao is not None and sqlite_path is not None:
            raise ValueError("provide either dao or sqlite_path, not both")
        if max_recording_delay < timedelta(0):
            raise ValueError("max_recording_delay must be non-negative")
        self.dao: PredictionDAO = dao if dao is not None else SQLitePredictionDAO(sqlite_path or "prospective_harness.sqlite3")
        self._clock = clock or _utc_now
        self.max_recording_delay = max_recording_delay

    def _now(self) -> datetime:
        return _to_utc(self._clock())

    def register_prediction(self, model_id: str, dataset_hash: str, prediction: dict[str, Any]) -> PredictionId:
        validate_prediction_probability(prediction)
        registered_at = self._now()
        digest = content_hash({"model_id": model_id, "dataset_hash": dataset_hash, "prediction": prediction})
        return self.dao.add_prediction(model_id, dataset_hash, prediction, digest, registered_at)

    def record_outcome(self, prediction_id: PredictionId, outcome: dict[str, Any], observed_at: datetime) -> None:
        validate_outcome_label(outcome)
        prediction = self.dao.get_prediction(PredictionId(str(prediction_id)))
        if prediction is None:
            raise PredictionNotFoundError(f"prediction not found: {prediction_id}")

        observed_at = _to_utc(observed_at)
        recorded_at = self._now()
        if observed_at <= prediction.registered_at:
            raise TemporalOrderingError("outcome observed_at must be strictly after prediction registration time")
        if observed_at > recorded_at:
            raise TemporalOrderingError("outcome observed_at cannot be in the future relative to recorded_at")
        if recorded_at - observed_at > self.max_recording_delay:
            raise TemporalOrderingError("outcome recorded too late after observed_at to preserve prospective evidence")

        self.dao.add_outcome(prediction.id, outcome, observed_at, recorded_at)

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
