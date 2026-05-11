"""SQLite storage implementation using SQLAlchemy."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .exceptions import ImmutablePredictionError
from .hashing import canonical_json
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
    registered_at: Mapped[str] = mapped_column(String, index=True, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    outcome: Mapped["OutcomeRow | None"] = relationship(back_populates="prediction", uselist=False)


class OutcomeRow(Base):
    __tablename__ = "outcomes"
    __table_args__ = (UniqueConstraint("prediction_id", name="uq_outcomes_prediction_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    prediction_id: Mapped[str] = mapped_column(ForeignKey("predictions.id"), nullable=False, index=True)
    outcome_json: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[str] = mapped_column(String, nullable=False)
    recorded_at: Mapped[str] = mapped_column(String, nullable=False)

    prediction: Mapped[PredictionRow] = relationship(back_populates="outcome")


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _encode_dt(value: datetime) -> str:
    return _to_utc(value).isoformat().replace("+00:00", "Z")


def _decode_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _stored_prediction(row: PredictionRow) -> StoredPrediction:
    return StoredPrediction(
        id=PredictionId(row.id),
        model_id=row.model_id,
        dataset_hash=row.dataset_hash,
        prediction=json.loads(row.prediction_json),
        prediction_hash=row.prediction_hash,
        registered_at=_decode_dt(row.registered_at),
        created_at=_decode_dt(row.created_at),
    )


def _stored_outcome(row: OutcomeRow) -> StoredOutcome:
    return StoredOutcome(
        prediction_id=PredictionId(row.prediction_id),
        outcome=json.loads(row.outcome_json),
        observed_at=_decode_dt(row.observed_at),
        recorded_at=_decode_dt(row.recorded_at),
    )


class SQLitePredictionDAO:
    """SQLite-backed append-only prediction DAO."""

    def __init__(self, path: str | Path = "prospective_harness.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.path}", future=True)
        Base.metadata.create_all(self.engine)
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
            registered_at=_encode_dt(registered_at),
            created_at=_encode_dt(now),
        )
        with self._sessionmaker() as session:
            session.add(row)
            session.commit()
        return prediction_id

    def get_prediction(self, prediction_id: PredictionId) -> StoredPrediction | None:
        with self._sessionmaker() as session:
            row = session.get(PredictionRow, str(prediction_id))
            return _stored_prediction(row) if row else None

    def update_prediction(self, prediction_id: PredictionId, prediction: dict[str, Any]) -> None:
        raise ImmutablePredictionError("registered predictions are append-only and cannot be updated")

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
            observed_at=_encode_dt(observed_at),
            recorded_at=_encode_dt(recorded_at),
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
        start, end = (_to_utc(time_window[0]), _to_utc(time_window[1]))
        with self._sessionmaker() as session:
            rows = list(
                session.scalars(
                    select(PredictionRow)
                    .where(PredictionRow.model_id == model_id)
                    .order_by(PredictionRow.registered_at, PredictionRow.id)
                )
            )
            result: list[PredictionWithOutcome] = []
            for row in rows:
                prediction = _stored_prediction(row)
                if start <= prediction.registered_at <= end:
                    outcome = _stored_outcome(row.outcome) if row.outcome else None
                    result.append(PredictionWithOutcome(prediction=prediction, outcome=outcome))
            return result
