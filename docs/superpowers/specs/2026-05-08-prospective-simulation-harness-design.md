# Prospective Simulation Harness Design

## Goal

Build a small Python package that lets researchers pre-register binary probabilistic model predictions, later record observed outcomes, and produce calibration reports while making backfilling and prediction mutation impossible through API and DAO invariants.

## Scope

The package implements the README requirements: a Python library with SQLite storage, a swappable DAO boundary, append-only prediction registration, strict prospective outcome recording, calibration metrics, documentation, and pytest coverage. A post-review hardening pass adds bounded outcome recording, SQLite append-only triggers, and hash re-verification on read. It intentionally does not include a web service, CLI, Docker setup, authentication, or a Postgres implementation because those are not required and would add evaluation risk without improving the core take-home objective.

## Public Interface

The top-level package exposes these required functions:

```python
register_prediction(model_id: str, dataset_hash: str, prediction: dict) -> PredictionId
record_outcome(prediction_id: PredictionId, outcome: dict, observed_at: datetime) -> None
calibration_report(model_id: str, time_window: tuple[datetime, datetime]) -> CalibrationReport
```

A `ProspectiveHarness` class provides the same operations for callers that need to inject a DAO or point at a specific SQLite database. The module-level functions use a default SQLite database path and remain convenient for the required interface.

## Data Contract

The README leaves `prediction: dict` and `outcome: dict` intentionally broad, but Brier score and ECE require a concrete probability/label interpretation. This implementation uses the minimal binary calibration contract:

```python
prediction = {"probability": 0.73}
outcome = {"label": 1}
```

`probability` must be numeric and within `[0, 1]`. `label` must be `0` or `1`. Invalid shapes raise `ValueError` at the API boundary.

## Architecture

Files are organized by responsibility:

- `src/prospective_harness/__init__.py`: public exports.
- `src/prospective_harness/api.py`: `ProspectiveHarness` service and module-level required functions.
- `src/prospective_harness/models.py`: Pydantic value objects and report models.
- `src/prospective_harness/exceptions.py`: domain exceptions.
- `src/prospective_harness/dao.py`: DAO protocol that storage implementations must satisfy.
- `src/prospective_harness/sqlite_dao.py`: SQLAlchemy-backed SQLite DAO.
- `src/prospective_harness/calibration.py`: pure calibration math.
- `src/prospective_harness/hashing.py`: canonical JSON serialization and SHA-256 content hashing.

Business invariants live in the service and DAO boundary, not in README prose. Calibration math is pure and testable without a database.

## Storage Design

SQLite uses two append-oriented tables:

### `predictions`

- `id`: string UUID primary key.
- `model_id`: model identifier.
- `dataset_hash`: caller-provided dataset hash.
- `prediction_json`: canonical JSON payload.
- `prediction_hash`: SHA-256 hash of the registered content.
- `registered_at`: UTC timestamp assigned at registration.
- `created_at`: UTC insertion timestamp.

### `outcomes`

- `id`: integer primary key.
- `prediction_id`: unique foreign key to `predictions.id`.
- `outcome_json`: canonical JSON payload.
- `observed_at`: caller-provided observation timestamp.
- `recorded_at`: UTC insertion timestamp.

A unique outcome per prediction preserves the append-only interpretation: predictions cannot be overwritten, and realized outcomes cannot be revised through the public API. SQLite triggers also reject direct UPDATE and DELETE statements on predictions and outcomes.

## Backfilling Prevention

`record_outcome` loads the registered prediction and compares timestamps. It accepts an outcome only when:

```python
registered_at < observed_at <= recorded_at
recorded_at - observed_at <= max_recording_delay
```

The default `max_recording_delay` is five minutes. If `observed_at == registered_at`, `observed_at < registered_at`, `observed_at > recorded_at`, or the recording delay exceeds the configured bound, the service raises `TemporalOrderingError`. This explicitly distinguishes valid prospective outcomes from contemporaneous or retrospective records and avoids silently accepting backfilled data.

## Immutability

Predictions are append-only:

- Registration inserts a new row with canonical JSON and SHA-256 hash.
- Public API exposes no update operation.
- The DAO exposes no prediction mutation method.
- SQLite triggers reject direct database UPDATE and DELETE attempts.
- Prediction hashes are recomputed on read and mismatches raise `DataIntegrityError`.
- Duplicate outcome recording raises `ImmutablePredictionError` because revising an outcome would alter the realized record.

## Calibration Report

`calibration_report(model_id, time_window)` selects predictions for the model whose `registered_at` timestamp falls inside the inclusive time window. It returns:

- `number_registered`: count of registered predictions in the window.
- `number_realized`: count of those predictions with outcomes.
- `calibration_curve`: exactly 10 bins: `[0.0,0.1)`, `[0.1,0.2)`, ..., `[0.9,1.0]`.
- `brier_score`: mean squared error over realized predictions, or `None` if no outcomes are realized.
- `ece`: expected calibration error over realized predictions, or `None` if no outcomes are realized.

Each bin includes count, average predicted probability, and observed frequency. Empty bins have count `0` and `None` for means/frequencies.

## Error Handling

Domain errors are explicit:

- `PredictionNotFoundError`: no registered prediction exists for an id.
- `TemporalOrderingError`: outcome observation time is not strictly after registration time.
- `ImmutablePredictionError`: caller attempts to mutate a registered prediction or overwrite an outcome.

Validation errors for malformed prediction/outcome dictionaries use `ValueError` because they are caller input contract violations.

## Testing Strategy

The pytest suite covers at least 15 tests across four categories:

1. Happy path: package imports, prediction registration, outcome recording, and report generation.
2. Invariants: immutable predictions, duplicate outcome rejection, and strict temporal ordering.
3. Storage/hash behavior: canonical SHA-256 content hash, model/time filtering, and DAO swap boundary behavior.
4. Calibration math: known-truth Brier score, ECE, 10-bin curve, empty realized outcomes, and boundary bin placement.

Clean checkout verification is `python -m pytest` after installing the package with test dependencies.

## Acceptance Criteria

The project is complete when:

- A Python package is installable from the repository root.
- The required README interface exists and works.
- SQLite storage persists registered predictions and outcomes.
- Registered predictions store SHA-256 content hashes.
- Mutation attempts raise `ImmutablePredictionError`.
- Outcomes observed at or before registration raise `TemporalOrderingError`.
- Calibration reports include registration count, realized count, 10 bins, Brier score, and ECE.
- At least 15 pytest tests pass from a clean checkout.
- README contains accurate run instructions.
