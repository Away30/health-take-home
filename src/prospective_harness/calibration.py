"""Calibration validation and metric calculations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

import numpy as np

from .models import CalibrationBin, CalibrationReport


def validate_prediction_probability(prediction: dict[str, Any]) -> float:
    """Extract and validate the binary-event probability from a prediction."""

    if "probability" not in prediction:
        raise ValueError("prediction must contain a 'probability' field")
    probability = prediction["probability"]
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        raise ValueError("prediction probability must be numeric")
    probability = float(probability)
    if probability < 0.0 or probability > 1.0:
        raise ValueError("prediction probability must be between 0 and 1")
    return probability


def validate_outcome_label(outcome: dict[str, Any]) -> int:
    """Extract and validate the binary observed label from an outcome."""

    if "label" not in outcome:
        raise ValueError("outcome must contain a 'label' field")
    label = outcome["label"]
    if isinstance(label, bool) or label not in (0, 1):
        raise ValueError("outcome label must be 0 or 1")
    return int(label)


def _bin_index(probability: float) -> int:
    if probability == 1.0:
        return 9
    return min(9, max(0, int(probability * 10)))


def build_calibration_report(
    *,
    model_id: str,
    time_window: tuple[datetime, datetime],
    probabilities: Sequence[float],
    labels: Sequence[int],
    number_registered: int,
) -> CalibrationReport:
    """Build a 10-bin calibration report from realized binary predictions."""

    if len(probabilities) != len(labels):
        raise ValueError("probabilities and labels must have the same length")

    bins: list[CalibrationBin] = []
    realized = len(probabilities)
    for index in range(10):
        lower = index / 10
        upper = (index + 1) / 10
        member_indexes = [i for i, p in enumerate(probabilities) if _bin_index(float(p)) == index]
        if member_indexes:
            bin_probs = np.array([probabilities[i] for i in member_indexes], dtype=float)
            bin_labels = np.array([labels[i] for i in member_indexes], dtype=float)
            mean_probability = float(np.mean(bin_probs))
            observed_frequency = float(np.mean(bin_labels))
        else:
            mean_probability = None
            observed_frequency = None
        bins.append(
            CalibrationBin(
                bin_index=index,
                lower=lower,
                upper=upper,
                count=len(member_indexes),
                mean_predicted_probability=mean_probability,
                observed_frequency=observed_frequency,
            )
        )

    if realized == 0:
        brier_score = None
        ece = None
    else:
        probs = np.array(probabilities, dtype=float)
        ys = np.array(labels, dtype=float)
        brier_score = float(np.mean((probs - ys) ** 2))
        ece = float(
            sum(
                (bin_.count / realized) * abs(bin_.mean_predicted_probability - bin_.observed_frequency)
                for bin_ in bins
                if bin_.count > 0
                and bin_.mean_predicted_probability is not None
                and bin_.observed_frequency is not None
            )
        )

    return CalibrationReport(
        model_id=model_id,
        time_window=time_window,
        number_registered=number_registered,
        number_realized=realized,
        calibration_curve=bins,
        brier_score=brier_score,
        ece=ece,
    )
