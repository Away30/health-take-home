# Prospective Simulation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an installable Python package that implements prospective prediction registration, immutable SQLite storage, outcome recording, and calibration reporting.

**Architecture:** The package separates domain API, DAO protocol, SQLite persistence, hashing, and calibration math. The public API exposes the README-required functions and a configurable `ProspectiveHarness` class for tests and custom database paths.

**Tech Stack:** Python 3.11+, pydantic, sqlalchemy, numpy, pytest.

---

## File Structure

- Create `pyproject.toml`: package metadata and dependencies.
- Replace `readme.md` with accurate setup, usage, and test instructions.
- Create `src/prospective_harness/__init__.py`: public exports.
- Create `src/prospective_harness/api.py`: required functions and service class.
- Create `src/prospective_harness/models.py`: Pydantic value/report models.
- Create `src/prospective_harness/exceptions.py`: domain exceptions.
- Create `src/prospective_harness/dao.py`: DAO protocol.
- Create `src/prospective_harness/sqlite_dao.py`: SQLAlchemy SQLite DAO.
- Create `src/prospective_harness/calibration.py`: pure metric functions.
- Create `src/prospective_harness/hashing.py`: canonical JSON and SHA-256 helpers.
- Create `tests/`: pytest coverage for API, DAO invariants, hashing, and calibration math.

### Task 1: Packaging Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/prospective_harness/__init__.py`
- Create: `src/prospective_harness/exceptions.py`
- Test: `tests/test_package.py`

- [ ] **Step 1: Write import smoke test**

```python
def test_package_exports_required_api():
    import prospective_harness as ph

    assert callable(ph.register_prediction)
    assert callable(ph.record_outcome)
    assert callable(ph.calibration_report)
    assert ph.ImmutablePredictionError.__name__ == "ImmutablePredictionError"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_package.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create minimal package files**

Create `pyproject.toml` with project metadata, dependencies, pytest config, and src layout. Create package exports and exception classes.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_package.py -v`
Expected: PASS.

### Task 2: Models, Hashing, and Calibration Math

**Files:**
- Create: `src/prospective_harness/models.py`
- Create: `src/prospective_harness/hashing.py`
- Create: `src/prospective_harness/calibration.py`
- Test: `tests/test_hashing.py`
- Test: `tests/test_calibration.py`

- [ ] **Step 1: Write tests for canonical hashing and known-truth metrics**

Test canonical JSON order-insensitivity, SHA-256 length, 10 bins, Brier score, ECE, boundary placement, and empty realized behavior.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hashing.py tests/test_calibration.py -v`
Expected: FAIL with missing modules/functions.

- [ ] **Step 3: Implement models and pure functions**

Implement Pydantic `PredictionId`, `CalibrationBin`, and `CalibrationReport`; implement `canonical_json`, `content_hash`, `validate_prediction_probability`, `validate_outcome_label`, and `build_calibration_report`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hashing.py tests/test_calibration.py -v`
Expected: PASS.

### Task 3: SQLite DAO

**Files:**
- Create: `src/prospective_harness/dao.py`
- Create: `src/prospective_harness/sqlite_dao.py`
- Test: `tests/test_sqlite_dao.py`

- [ ] **Step 1: Write DAO tests**

Test insert/load prediction, hash persistence, append-only update rejection, outcome insertion, duplicate outcome rejection, model/time filtering, and unknown prediction behavior.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sqlite_dao.py -v`
Expected: FAIL with missing DAO implementation.

- [ ] **Step 3: Implement DAO protocol and SQLite DAO**

Use SQLAlchemy declarative mappings, create tables on initialization, store datetimes timezone-normalized to UTC, and expose typed DAO methods.

- [ ] **Step 4: Run DAO tests**

Run: `python -m pytest tests/test_sqlite_dao.py -v`
Expected: PASS.

### Task 4: Public API and Prospective Invariants

**Files:**
- Create: `src/prospective_harness/api.py`
- Modify: `src/prospective_harness/__init__.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write API tests**

Test required signatures, happy path, strict `observed_at > registered_at`, invalid prediction/outcome shape, reports by model/time window, default module functions, and configurable harness database paths.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py -v`
Expected: FAIL with missing API implementation.

- [ ] **Step 3: Implement `ProspectiveHarness` and module functions**

Inject DAO into the service, validate dictionaries, assign registration timestamps, compare outcome timestamps, delegate report math to pure calibration code, and expose default SQLite-backed functions.

- [ ] **Step 4: Run API tests**

Run: `python -m pytest tests/test_api.py -v`
Expected: PASS.

### Task 5: README and Full Verification

**Files:**
- Modify: `readme.md`
- Run: full pytest suite

- [ ] **Step 1: Rewrite README with actual package instructions**

Document install, run tests, API usage, data contract, invariants, and clean checkout verification.

- [ ] **Step 2: Run complete verification**

Run: `python -m pytest -v`
Expected: at least 15 tests PASS.

- [ ] **Step 3: Audit README requirements against artifacts**

Map every README requirement to code/tests and fix any uncovered item before completion.

## Self-Review

Spec coverage: all required functions, append-only storage, SHA-256 hashing, strict temporal ordering, SQLite DAO, calibration report fields, dependency limits, README, and 15+ tests are assigned to tasks.

Placeholder scan: no TBD/TODO/fill-later placeholders are present.

Type consistency: the plan uses `PredictionId`, `CalibrationReport`, `ImmutablePredictionError`, and `TemporalOrderingError` consistently with the design spec.
