"""Storage interface for prospective predictions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .models import PredictionId, PredictionWithOutcome, StoredOutcome, StoredPrediction


class PredictionDAO(Protocol):
    def add_prediction(
        self,
        model_id: str,
        dataset_hash: str,
        prediction: dict[str, Any],
        prediction_hash: str,
        registered_at: datetime,
    ) -> PredictionId: ...

    def get_prediction(self, prediction_id: PredictionId) -> StoredPrediction | None: ...

    def add_outcome(
        self,
        prediction_id: PredictionId,
        outcome: dict[str, Any],
        observed_at: datetime,
        recorded_at: datetime,
    ) -> StoredOutcome: ...

    def get_outcome(self, prediction_id: PredictionId) -> StoredOutcome | None: ...

    def list_predictions_with_outcomes(
        self,
        model_id: str,
        time_window: tuple[datetime, datetime],
    ) -> list[PredictionWithOutcome]: ...
