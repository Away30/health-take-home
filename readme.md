# Prospective Simulation Harness

A small Python package for prospective model evaluation. Researchers can register a model prediction before an outcome is observed, record the outcome later, and generate calibration reports. The storage layer is SQLite-backed and append-only at the application/DAO boundary.

## Requirements

- Python 3.11+
- Runtime dependencies: `pydantic`, `sqlalchemy`, `numpy`
- Test dependency: `pytest`

## Install From A Clean Checkout

```bash
python -m pip install -e '.[test]'
```

## Run Tests

```bash
python -m pytest -v
```

## Public API

```python
from datetime import timedelta

from prospective_harness import ProspectiveHarness

harness = ProspectiveHarness(sqlite_path="research.sqlite3")

prediction_id = harness.register_prediction(
    model_id="mortality-risk-v1",
    dataset_hash="sha256-of-dataset-snapshot",
    prediction={"probability": 0.73},
)

registered_at = harness.dao.get_prediction(prediction_id).registered_at
harness.record_outcome(
    prediction_id=prediction_id,
    outcome={"label": 1},
    observed_at=registered_at + timedelta(days=7),
)

report = harness.calibration_report(
    model_id="mortality-risk-v1",
    time_window=(registered_at - timedelta(seconds=1), registered_at + timedelta(days=8)),
)
print(report.model_dump())
```

The package also exposes the required module-level functions:

```python
from prospective_harness import register_prediction, record_outcome, calibration_report
```

Those functions use a default SQLite database named `prospective_harness.sqlite3` in the current working directory. For tests or production code that need an explicit database path, prefer `ProspectiveHarness(sqlite_path=...)`.

## Data Contract

The README task specifies `prediction: dict` and `outcome: dict`. Calibration metrics require a concrete binary probability contract, so this package uses:

```python
prediction = {"probability": 0.73}  # float in [0, 1]
outcome = {"label": 1}             # integer 0 or 1
```

Invalid prediction or outcome payloads raise `ValueError`.

## Prospective Registration Invariants

- `register_prediction(model_id, dataset_hash, prediction)` stores a new row with a SHA-256 content hash.
- Predictions are append-only. Any direct DAO update attempt raises `ImmutablePredictionError`.
- Outcomes are append-only. Recording a second outcome for the same prediction raises `ImmutablePredictionError`.
- `record_outcome(prediction_id, outcome, observed_at)` requires `observed_at > registered_at`.
- `observed_at == registered_at` and `observed_at < registered_at` both raise `TemporalOrderingError`.

## Calibration Report

`calibration_report(model_id, time_window)` returns a `CalibrationReport` with:

- `number_registered`: predictions registered for the model inside the time window.
- `number_realized`: registered predictions that have outcomes.
- `calibration_curve`: exactly 10 bins: `[0.0,0.1)`, `[0.1,0.2)`, ..., `[0.9,1.0]`.
- `brier_score`: mean squared error over realized predictions, or `None` if there are no realized outcomes.
- `ece`: expected calibration error over realized predictions, or `None` if there are no realized outcomes.

## Storage Boundary

`SQLitePredictionDAO` implements the DAO used by `ProspectiveHarness`. Business logic depends on the DAO protocol rather than SQLite-specific details, so another backend such as Postgres can be added by implementing the same methods.
