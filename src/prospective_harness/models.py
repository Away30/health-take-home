"""Value objects and report models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PredictionId(str):
    """Opaque id returned when a prediction is registered."""


class CalibrationBin(BaseModel):
    """One bin in a 10-bin calibration curve."""

    model_config = ConfigDict(frozen=True)

    bin_index: int
    lower: float
    upper: float
    count: int
    mean_predicted_probability: float | None = None
    observed_frequency: float | None = None


class CalibrationReport(BaseModel):
    """Calibration summary for a model over a registration time window."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    time_window: tuple[datetime, datetime]
    number_registered: int = Field(ge=0)
    number_realized: int = Field(ge=0)
    calibration_curve: list[CalibrationBin]
    brier_score: float | None = None
    ece: float | None = None


@dataclass(frozen=True)
class StoredPrediction:
    id: PredictionId
    model_id: str
    dataset_hash: str
    prediction: dict[str, Any]
    prediction_hash: str
    registered_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class StoredOutcome:
    prediction_id: PredictionId
    outcome: dict[str, Any]
    observed_at: datetime
    recorded_at: datetime


@dataclass(frozen=True)
class PredictionWithOutcome:
    prediction: StoredPrediction
    outcome: StoredOutcome | None
