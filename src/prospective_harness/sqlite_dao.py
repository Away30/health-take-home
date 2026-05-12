"""SQLite storage implementation using SQLAlchemy."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from .exceptions import DataIntegrityError, ImmutablePredictionError
from .hashing import canonical_json, content_hash
from .models import PredictionId, PredictionWithOutcome, StoredOutcome, StoredPrediction


class Base(DeclarativeBase):
    pass


class PredictionRow(Base):
    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    model_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String, nullable=False)
    prediction_json: Mapped[str] = mapped_column(Text, nullable=False)
    prediction_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    registered_at_us: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    created_at_us: Mapped[int] = mapped_column(Integer, nullable=False)

    outcome: Mapped["OutcomeRow | None"] = relationship(back_populates="prediction", uselist=False)


class OutcomeRow(Base):
    __tablename__ = "outcomes"
    __table_args__ = (UniqueConstraint("prediction_id", name="uq_outcomes_prediction_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    prediction_id: Mapped[str] = mapped_column(ForeignKey("predictions.id"), nullable=False, index=True)
    outcome_json: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at_us: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at_us: Mapped[int] = mapped_column(Integer, nullable=False)

    prediction: Mapped[PredictionRow] = relationship(back_populates="outcome")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _encode_dt(value: datetime) -> int:
    return int(_to_utc(value).timestamp() * 1_000_000)


def _decode_dt(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000, timezone.utc)


def _prediction_payload(row: PredictionRow) -> dict[str, Any]:
    return json.loads(row.prediction_json)


def _verify_prediction_hash(row: PredictionRow) -> dict[str, Any]:
    prediction = _prediction_payload(row)
    expected = content_hash({"model_id": row.model_id, "dataset_hash": row.dataset_hash, "prediction": prediction})
    if row.prediction_hash != expected:
        raise DataIntegrityError(f"prediction hash mismatch for {row.id}")
    return prediction


def _stored_prediction(row: PredictionRow) -> StoredPrediction:
    prediction = _verify_prediction_hash(row)
    return StoredPrediction(
        id=PredictionId(row.id),
        model_id=row.model_id,
        dataset_hash=row.dataset_hash,
        prediction=prediction,
        prediction_hash=row.prediction_hash,
        registered_at=_decode_dt(row.registered_at_us),
        created_at=_decode_dt(row.created_at_us),
    )


def _stored_outcome(row: OutcomeRow) -> StoredOutcome:
    return StoredOutcome(
        prediction_id=PredictionId(row.prediction_id),
        outcome=json.loads(row.outcome_json),
        observed_at=_decode_dt(row.observed_at_us),
        recorded_at=_decode_dt(row.recorded_at_us),
    )


def _install_append_only_triggers(connection) -> None:
    statements = [
        """
        CREATE TRIGGER IF NOT EXISTS predictions_no_update
        BEFORE UPDATE ON predictions
        BEGIN
            SELECT RAISE(ABORT, 'predictions are append-only');
        END;
        """,
        """
        CREATE TRIGGER IF NOT EXISTS predictions_no_delete
        BEFORE DELETE ON predictions
        BEGIN
            SELECT RAISE(ABORT, 'predictions are append-only');
        END;
        """,
        """
        CREATE TRIGGER IF NOT EXISTS outcomes_no_update
        BEFORE UPDATE ON outcomes
        BEGIN
            SELECT RAISE(ABORT, 'outcomes are append-only');
        END;
        """,
        """
        CREATE TRIGGER IF NOT EXISTS outcomes_no_delete
        BEFORE DELETE ON outcomes
        BEGIN
            SELECT RAISE(ABORT, 'outcomes are append-only');
        END;
        """,
    ]
    for statement in statements:
        connection.execute(text(statement))


class SQLitePredictionDAO:
    """SQLite-backed append-only prediction DAO."""

    def __init__(self, path: str | Path = "prospective_harness.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.path}", future=True)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            _install_append_only_triggers(connection)
        self._sessionmaker = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def add_prediction(
        self,
        model_id: str,
        dataset_hash: str,
        prediction: dict[str, Any],
        prediction_hash: str,
        registered_at: datetime,
    ) -> PredictionId:
        prediction_id = PredictionId(str(uuid.uuid4()))
        now = datetime.now(timezone.utc)
        row = PredictionRow(
            id=str(prediction_id),
            model_id=model_id,
            dataset_hash=dataset_hash,
            prediction_json=canonical_json(prediction),
            prediction_hash=prediction_hash,
            registered_at_us=_encode_dt(registered_at),
            created_at_us=_encode_dt(now),
        )
        with self._sessionmaker() as session:
            session.add(row)
            session.commit()
        return prediction_id

    def get_prediction(self, prediction_id: PredictionId) -> StoredPrediction | None:
        with self._sessionmaker() as session:
            row = session.get(PredictionRow, str(prediction_id))
            return _stored_prediction(row) if row else None

    def add_outcome(
        self,
        prediction_id: PredictionId,
        outcome: dict[str, Any],
        observed_at: datetime,
        recorded_at: datetime,
    ) -> StoredOutcome:
        row = OutcomeRow(
            prediction_id=str(prediction_id),
            outcome_json=canonical_json(outcome),
            observed_at_us=_encode_dt(observed_at),
            recorded_at_us=_encode_dt(recorded_at),
        )
        with self._sessionmaker() as session:
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ImmutablePredictionError("outcomes are append-only and cannot be overwritten") from exc
            session.refresh(row)
            return _stored_outcome(row)

    def get_outcome(self, prediction_id: PredictionId) -> StoredOutcome | None:
        with self._sessionmaker() as session:
            row = session.scalar(select(OutcomeRow).where(OutcomeRow.prediction_id == str(prediction_id)))
            return _stored_outcome(row) if row else None

    def list_predictions_with_outcomes(
        self,
        model_id: str,
        time_window: tuple[datetime, datetime],
    ) -> list[PredictionWithOutcome]:
        start_us, end_us = (_encode_dt(time_window[0]), _encode_dt(time_window[1]))
        with self._sessionmaker() as session:
            rows = list(
                session.scalars(
                    select(PredictionRow)
                    .where(PredictionRow.model_id == model_id)
                    .where(PredictionRow.registered_at_us >= start_us)
                    .where(PredictionRow.registered_at_us <= end_us)
                    .order_by(PredictionRow.registered_at_us, PredictionRow.id)
                )
            )
            result: list[PredictionWithOutcome] = []
            for row in rows:
                prediction = _stored_prediction(row)
                outcome = _stored_outcome(row.outcome) if row.outcome else None
                result.append(PredictionWithOutcome(prediction=prediction, outcome=outcome))
            return result
