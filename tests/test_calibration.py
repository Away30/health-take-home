from datetime import datetime, timezone

import numpy as np
import pytest

from prospective_harness.calibration import build_calibration_report, validate_outcome_label, validate_prediction_probability


def test_validate_prediction_probability_accepts_binary_probability():
    assert validate_prediction_probability({"probability": 0.25}) == 0.25
    assert validate_prediction_probability({"probability": 1}) == 1.0


@pytest.mark.parametrize("payload", [{}, {"probability": -0.1}, {"probability": 1.1}, {"probability": "high"}])
def test_validate_prediction_probability_rejects_invalid_payloads(payload):
    with pytest.raises(ValueError):
        validate_prediction_probability(payload)


def test_validate_outcome_label_accepts_zero_or_one():
    assert validate_outcome_label({"label": 0}) == 0
    assert validate_outcome_label({"label": 1}) == 1


@pytest.mark.parametrize("payload", [{}, {"label": 2}, {"label": -1}, {"label": 0.5}, {"label": "yes"}])
def test_validate_outcome_label_rejects_invalid_payloads(payload):
    with pytest.raises(ValueError):
        validate_outcome_label(payload)


def test_calibration_report_matches_known_truth_fixture():
    probabilities = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]
    labels = [0, 0, 0, 0, 1, 1, 1, 1, 1, 1]

    report = build_calibration_report(
        model_id="model-a",
        time_window=(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)),
        probabilities=probabilities,
        labels=labels,
        number_registered=12,
    )

    assert report.number_registered == 12
    assert report.number_realized == 10
    assert len(report.calibration_curve) == 10
    assert report.brier_score == pytest.approx(float(np.mean((np.array(probabilities) - np.array(labels)) ** 2)))
    assert report.ece == pytest.approx(float(np.mean([abs(p - y) for p, y in zip(probabilities, labels)])))
    assert report.calibration_curve[4].count == 1
    assert report.calibration_curve[4].mean_predicted_probability == pytest.approx(0.45)
    assert report.calibration_curve[4].observed_frequency == pytest.approx(1.0)


def test_calibration_bins_place_boundaries_correctly():
    report = build_calibration_report(
        model_id="model-a",
        time_window=(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)),
        probabilities=[0.0, 0.1, 0.999, 1.0],
        labels=[0, 0, 1, 1],
        number_registered=4,
    )

    assert report.calibration_curve[0].count == 1
    assert report.calibration_curve[1].count == 1
    assert report.calibration_curve[9].count == 2


def test_calibration_report_handles_no_realized_outcomes():
    report = build_calibration_report(
        model_id="model-a",
        time_window=(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc)),
        probabilities=[],
        labels=[],
        number_registered=3,
    )

    assert report.number_registered == 3
    assert report.number_realized == 0
    assert report.brier_score is None
    assert report.ece is None
    assert len(report.calibration_curve) == 10
    assert all(bin.count == 0 for bin in report.calibration_curve)
