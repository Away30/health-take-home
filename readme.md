# Prospective Simulation Harness

A small Python package for prospective model evaluation. Researchers can register a model prediction before an outcome is observed, record the outcome in a bounded prospective window, and generate calibration reports.

The core design goal is to make backfilling materially harder than a simple `observed_at > registered_at` check. The package therefore enforces:

- strict `registered_at < observed_at <= recorded_at` ordering
- a configurable maximum delay between `observed_at` and `recorded_at` that defaults to 5 minutes
- SQLite triggers that reject direct `UPDATE` and `DELETE` attempts on prediction and outcome rows
- SHA-256 content hashes that are recomputed and verified when prediction rows are read

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
# In real use, record the outcome when it is actually observed. This example
# uses the test-friendly clock pattern only to show the API shape.
harness.record_outcome(
    prediction_id=prediction_id,
    outcome={"label": 1},
    observed_at=registered_at + timedelta(seconds=1),
)

report = harness.calibration_report(
    model_id="mortality-risk-v1",
    time_window=(registered_at - timedelta(seconds=1), registered_at + timedelta(days=1)),
)
print(report.model_dump())
```

The package also exposes the required module-level functions:

```python
from prospective_harness import register_prediction, record_outcome, calibration_report
```

Those functions lazily create a default SQLite database named `prospective_harness.sqlite3` in the current working directory. For tests or production code that need an explicit database path, prefer `ProspectiveHarness(sqlite_path=...)`.

## Data Contract

The assignment specifies `prediction: dict` and `outcome: dict`. Calibration metrics require a concrete binary probability contract, so this package uses:

```python
prediction = {"probability": 0.73}  # float in [0, 1]
outcome = {"label": 1}             # integer 0 or 1
```

Invalid prediction or outcome payloads raise `ValueError`.

## Prospective Registration Invariants

`register_prediction(model_id, dataset_hash, prediction)` stores a new row with a SHA-256 content hash over:

```python
{"model_id": model_id, "dataset_hash": dataset_hash, "prediction": prediction}
```

`record_outcome(prediction_id, outcome, observed_at)` stores an outcome only when all temporal checks pass:

```python
registered_at < observed_at <= recorded_at
recorded_at - observed_at <= max_recording_delay
```

The default `max_recording_delay` is 5 minutes. This prevents the common backfill pattern of registering at `T0`, waiting until the real outcome is known, then claiming `observed_at = T0 + 1 second`. Callers that operate under a different trusted recording workflow can configure the delay explicitly:

```python
from datetime import timedelta
from prospective_harness import ProspectiveHarness

harness = ProspectiveHarness(
    sqlite_path="research.sqlite3",
    max_recording_delay=timedelta(hours=3),
)
```

Temporal violations raise `TemporalOrderingError`.

## Append-Only Storage And Tamper Detection

SQLite storage is append-only at both the DAO and database-trigger level:

- The DAO exposes no prediction mutation method.
- A unique outcome constraint prevents recording a second outcome for the same prediction.
- SQLite triggers reject direct `UPDATE` and `DELETE` statements on `predictions` and `outcomes`.
- Prediction hashes are recomputed on read; mismatches raise `DataIntegrityError`.

This does not claim to provide cryptographic external timestamping. A stronger production system could add external notary timestamps or an append-only log service. Within the requested SQLite package, the implementation prevents normal API mutation, direct SQL rewrites, future-dated outcomes, and delayed backfilled outcome recording.

## Calibration Report

`calibration_report(model_id, time_window)` filters predictions in SQL by `model_id` and registration time, then returns a `CalibrationReport` with:

- `number_registered`: predictions registered for the model inside the time window
- `number_realized`: registered predictions that have outcomes
- `calibration_curve`: exactly 10 bins: `[0.0,0.1)`, `[0.1,0.2)`, ..., `[0.9,1.0]`
- `brier_score`: mean squared error over realized predictions, or `None` if there are no realized outcomes
- `ece`: expected calibration error over realized predictions, or `None` if there are no realized outcomes

## Storage Boundary

`SQLitePredictionDAO` implements the DAO used by `ProspectiveHarness`. Business logic depends on the DAO protocol rather than SQLite-specific details, so another backend such as Postgres can be added by implementing the same methods.
