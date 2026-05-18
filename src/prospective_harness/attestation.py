"""Tamper-evident append-only attestation library.

This module implements two structurally separate SQLite-backed hash-chain
substrates. Consequential writes anchor to both substrates in one transaction;
reads and explicit verification recompute the chains before returning data.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Integer, String, Text, UniqueConstraint, create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .hashing import canonical_json

SubstrateName = Literal["A", "B"]
GENESIS_A = "0" * 64
GENESIS_B = "0" * 128


class AttestationError(Exception):
    """Base class for attestation errors."""


class ChainVerificationError(AttestationError):
    """Raised when an attestation chain does not verify."""


class WriteLatencyExceededError(AttestationError):
    """Raised when a write cannot complete within the configured latency bound."""


class AnchorRef(BaseModel):
    """Reference to one substrate's anchor row."""

    model_config = ConfigDict(frozen=True)

    substrate: SubstrateName
    position: int = Field(ge=1)
    digest: str


class AttestationEnvelopeModel(BaseModel):
    """Application payload plus provenance anchors."""

    model_config = ConfigDict(frozen=True)

    record_id: str
    payload: dict[str, Any]
    anchor_a: AnchorRef
    anchor_b: AnchorRef
    chain_position_a: int = Field(ge=1)
    chain_position_b: int = Field(ge=1)
    server_recorded_at: datetime


class AttestationEnvelope(Protocol):
    record_id: str
    payload: dict[str, Any]
    anchor_a: AnchorRef
    anchor_b: AnchorRef
    chain_position_a: int
    chain_position_b: int
    server_recorded_at: datetime


class ChainVerificationReport(BaseModel):
    """Auditor-facing result for chain verification."""

    model_config = ConfigDict(frozen=True)

    substrate: SubstrateName
    valid: bool
    total_records_verified: int
    break_position: int | None = None
    break_record_id: str | None = None
    recomputed_digest: str | None = None
    stored_digest: str | None = None
    reason: str | None = None
    last_verified_record_id: str | None = None


class Base(DeclarativeBase):
    pass


class EnvelopeRow(Base):
    __tablename__ = "attestation_envelopes"

    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    server_recorded_at_us: Mapped[int] = mapped_column(Integer, nullable=False)


class AnchorARow(Base):
    __tablename__ = "attestation_anchor_a"
    __table_args__ = (UniqueConstraint("record_id", name="uq_attestation_anchor_a_record_id"),)

    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    previous_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    anchor_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    server_recorded_at_us: Mapped[int] = mapped_column(Integer, nullable=False)


class AnchorBRow(Base):
    __tablename__ = "attestation_anchor_b"
    __table_args__ = (UniqueConstraint("record_id", name="uq_attestation_anchor_b_record_id"),)

    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    previous_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    anchor_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    server_recorded_at_us: Mapped[int] = mapped_column(Integer, nullable=False)


@dataclass(frozen=True)
class _AnchorData:
    position: int
    record_id: str
    previous_digest: str
    payload_digest: str
    anchor_digest: str
    server_recorded_at_us: int


class AttestationStore:
    """SQLite-backed two-substrate attestation store."""

    def __init__(
        self,
        sqlite_path: str | Path = "attestations.sqlite3",
        *,
        max_write_latency_seconds: float = 1.0,
        _clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_write_latency_seconds <= 0:
            raise ValueError("max_write_latency_seconds must be positive")
        self.path = Path(sqlite_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self._clock = _clock or (lambda: datetime.now(timezone.utc))
        self.max_write_latency_seconds = max_write_latency_seconds
        # SQLite serializes writers at the database level; this lock makes the
        # in-process order deterministic for the concurrency test and callers.
        self._write_lock = threading.Lock()

    def write(self, payload: dict[str, Any]) -> AttestationEnvelopeModel:
        with self._write_lock:
            with self.engine.begin() as connection:
                connection.execute(text("BEGIN IMMEDIATE"))
                record_id = str(uuid.uuid4())
                server_recorded_at = _to_utc(self._clock())
                server_recorded_at_us = _encode_dt(server_recorded_at)
                payload_json = canonical_json(payload)

                prev_a = self._last_anchor(connection, "A")
                prev_b = self._last_anchor(connection, "B")
                position_a = 1 if prev_a is None else prev_a.position + 1
                position_b = 1 if prev_b is None else prev_b.position + 1
                previous_a = GENESIS_A if prev_a is None else prev_a.anchor_digest
                previous_b = GENESIS_B if prev_b is None else prev_b.anchor_digest

                payload_digest_a = _payload_digest_a(payload_json)
                payload_digest_b = _payload_digest_b(payload_json)
                anchor_digest_a = _anchor_digest_a(
                    position=position_a,
                    record_id=record_id,
                    previous_digest=previous_a,
                    payload_digest=payload_digest_a,
                    server_recorded_at_us=server_recorded_at_us,
                )
                anchor_digest_b = _anchor_digest_b(
                    position=position_b,
                    record_id=record_id,
                    previous_digest=previous_b,
                    payload_digest=payload_digest_b,
                    server_recorded_at_us=server_recorded_at_us,
                )

                self._insert_envelope(connection, record_id, payload_json, server_recorded_at_us)
                self._insert_anchor_a(
                    connection,
                    position_a,
                    record_id,
                    previous_a,
                    payload_digest_a,
                    anchor_digest_a,
                    server_recorded_at_us,
                )
                self._insert_anchor_b(
                    connection,
                    position_b,
                    record_id,
                    previous_b,
                    payload_digest_b,
                    anchor_digest_b,
                    server_recorded_at_us,
                )

                latency_end = _to_utc(self._clock())
                if (latency_end - server_recorded_at).total_seconds() > self.max_write_latency_seconds:
                    raise WriteLatencyExceededError("attestation write exceeded configured latency bound")

                return AttestationEnvelopeModel(
                    record_id=record_id,
                    payload=payload,
                    anchor_a=AnchorRef(substrate="A", position=position_a, digest=anchor_digest_a),
                    anchor_b=AnchorRef(substrate="B", position=position_b, digest=anchor_digest_b),
                    chain_position_a=position_a,
                    chain_position_b=position_b,
                    server_recorded_at=server_recorded_at,
                )

    def read(self, record_id: str) -> AttestationEnvelopeModel:
        with self.engine.begin() as connection:
            envelope_row = self._get_envelope_row(connection, record_id)
            if envelope_row is None:
                raise KeyError(record_id)
            anchor_a = self._get_anchor_a(connection, record_id)
            anchor_b = self._get_anchor_b(connection, record_id)
            if anchor_a is None or anchor_b is None:
                raise ChainVerificationError("record is not anchored to both substrates")

        report_a = self.verify_chain("A", up_to_record_id=record_id)
        if not report_a.valid:
            raise ChainVerificationError(f"substrate A verification failed at position {report_a.break_position}")
        report_b = self.verify_chain("B", up_to_record_id=record_id)
        if not report_b.valid:
            raise ChainVerificationError(f"substrate B verification failed at position {report_b.break_position}")

        with self.engine.begin() as connection:
            envelope_row = self._get_envelope_row(connection, record_id)
            anchor_a = self._get_anchor_a(connection, record_id)
            anchor_b = self._get_anchor_b(connection, record_id)
            if envelope_row is None or anchor_a is None or anchor_b is None:
                raise ChainVerificationError("record changed during verification")
            if anchor_a.position != anchor_b.position or anchor_a.server_recorded_at_us != anchor_b.server_recorded_at_us:
                raise ChainVerificationError("cross-substrate anchor mismatch")
            if anchor_a.record_id != anchor_b.record_id:
                raise ChainVerificationError("cross-substrate record mismatch")
            payload = json.loads(envelope_row.payload_json)
            return AttestationEnvelopeModel(
                record_id=record_id,
                payload=payload,
                anchor_a=AnchorRef(substrate="A", position=anchor_a.position, digest=anchor_a.anchor_digest),
                anchor_b=AnchorRef(substrate="B", position=anchor_b.position, digest=anchor_b.anchor_digest),
                chain_position_a=anchor_a.position,
                chain_position_b=anchor_b.position,
                server_recorded_at=_decode_dt(envelope_row.server_recorded_at_us),
            )

    def verify_chain(self, substrate: SubstrateName, up_to_record_id: str | None = None) -> ChainVerificationReport:
        with self.engine.begin() as connection:
            rows = self._anchor_rows(connection, substrate)
            previous = GENESIS_A if substrate == "A" else GENESIS_B
            for expected_position, row in enumerate(rows, start=1):
                if row.position != expected_position:
                    return _break_report(
                        substrate,
                        total=expected_position - 1,
                        position=expected_position,
                        record_id=row.record_id,
                        recomputed=previous,
                        stored=row.anchor_digest,
                        reason="non-contiguous chain position",
                    )
                if row.previous_digest != previous:
                    return _break_report(
                        substrate,
                        total=expected_position - 1,
                        position=row.position,
                        record_id=row.record_id,
                        recomputed=previous,
                        stored=row.previous_digest,
                        reason="previous digest does not match prior anchor",
                    )
                envelope = self._get_envelope_row(connection, row.record_id)
                if envelope is None:
                    return _break_report(
                        substrate,
                        total=expected_position - 1,
                        position=row.position,
                        record_id=row.record_id,
                        recomputed=None,
                        stored=row.anchor_digest,
                        reason="anchor points to missing envelope",
                    )
                if envelope.server_recorded_at_us != row.server_recorded_at_us:
                    return _break_report(
                        substrate,
                        total=expected_position - 1,
                        position=row.position,
                        record_id=row.record_id,
                        recomputed=str(envelope.server_recorded_at_us),
                        stored=str(row.server_recorded_at_us),
                        reason="anchor timestamp does not match envelope timestamp",
                    )
                payload_digest = _payload_digest_a(envelope.payload_json) if substrate == "A" else _payload_digest_b(envelope.payload_json)
                if payload_digest != row.payload_digest:
                    recomputed_anchor = _recompute_anchor(substrate, row, payload_digest)
                    return _break_report(
                        substrate,
                        total=expected_position - 1,
                        position=row.position,
                        record_id=row.record_id,
                        recomputed=recomputed_anchor,
                        stored=row.anchor_digest,
                        reason="payload digest mismatch",
                    )
                recomputed = _recompute_anchor(substrate, row, payload_digest)
                if recomputed != row.anchor_digest:
                    return _break_report(
                        substrate,
                        total=expected_position - 1,
                        position=row.position,
                        record_id=row.record_id,
                        recomputed=recomputed,
                        stored=row.anchor_digest,
                        reason="anchor digest mismatch",
                    )
                previous = recomputed
                if up_to_record_id == row.record_id:
                    return ChainVerificationReport(
                        substrate=substrate,
                        valid=True,
                        total_records_verified=expected_position,
                        last_verified_record_id=row.record_id,
                    )
            if up_to_record_id is not None:
                return ChainVerificationReport(
                    substrate=substrate,
                    valid=False,
                    total_records_verified=len(rows),
                    break_position=None,
                    break_record_id=up_to_record_id,
                    recomputed_digest=None,
                    stored_digest=None,
                    reason="requested record id was not found in chain",
                    last_verified_record_id=rows[-1].record_id if rows else None,
                )
            return ChainVerificationReport(
                substrate=substrate,
                valid=True,
                total_records_verified=len(rows),
                last_verified_record_id=rows[-1].record_id if rows else None,
            )

    def _insert_envelope(self, connection: Connection, record_id: str, payload_json: str, server_recorded_at_us: int) -> None:
        connection.execute(
            text(
                "INSERT INTO attestation_envelopes (record_id, payload_json, server_recorded_at_us) "
                "VALUES (:record_id, :payload_json, :server_recorded_at_us)"
            ),
            {
                "record_id": record_id,
                "payload_json": payload_json,
                "server_recorded_at_us": server_recorded_at_us,
            },
        )

    def _insert_anchor_a(
        self,
        connection: Connection,
        position: int,
        record_id: str,
        previous_digest: str,
        payload_digest: str,
        anchor_digest: str,
        server_recorded_at_us: int,
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO attestation_anchor_a "
                "(position, record_id, previous_digest, payload_digest, anchor_digest, server_recorded_at_us) "
                "VALUES (:position, :record_id, :previous_digest, :payload_digest, :anchor_digest, :server_recorded_at_us)"
            ),
            locals_without_self(locals()),
        )

    def _insert_anchor_b(
        self,
        connection: Connection,
        position: int,
        record_id: str,
        previous_digest: str,
        payload_digest: str,
        anchor_digest: str,
        server_recorded_at_us: int,
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO attestation_anchor_b "
                "(position, record_id, previous_digest, payload_digest, anchor_digest, server_recorded_at_us) "
                "VALUES (:position, :record_id, :previous_digest, :payload_digest, :anchor_digest, :server_recorded_at_us)"
            ),
            locals_without_self(locals()),
        )

    def _last_anchor(self, connection: Connection, substrate: SubstrateName) -> _AnchorData | None:
        table = "attestation_anchor_a" if substrate == "A" else "attestation_anchor_b"
        row = connection.execute(text(f"SELECT * FROM {table} ORDER BY position DESC LIMIT 1")).mappings().first()
        return _anchor_from_mapping(row) if row else None

    def _anchor_rows(self, connection: Connection, substrate: SubstrateName) -> list[_AnchorData]:
        table = "attestation_anchor_a" if substrate == "A" else "attestation_anchor_b"
        rows = connection.execute(text(f"SELECT * FROM {table} ORDER BY position ASC")).mappings().all()
        return [_anchor_from_mapping(row) for row in rows]

    def _get_anchor_a(self, connection: Connection, record_id: str) -> _AnchorData | None:
        row = connection.execute(
            text("SELECT * FROM attestation_anchor_a WHERE record_id = :record_id"), {"record_id": record_id}
        ).mappings().first()
        return _anchor_from_mapping(row) if row else None

    def _get_anchor_b(self, connection: Connection, record_id: str) -> _AnchorData | None:
        row = connection.execute(
            text("SELECT * FROM attestation_anchor_b WHERE record_id = :record_id"), {"record_id": record_id}
        ).mappings().first()
        return _anchor_from_mapping(row) if row else None

    def _get_envelope_row(self, connection: Connection, record_id: str) -> EnvelopeRow | None:
        row = connection.execute(
            text("SELECT record_id, payload_json, server_recorded_at_us FROM attestation_envelopes WHERE record_id = :record_id"),
            {"record_id": record_id},
        ).mappings().first()
        if row is None:
            return None
        envelope = EnvelopeRow()
        envelope.record_id = row["record_id"]
        envelope.payload_json = row["payload_json"]
        envelope.server_recorded_at_us = row["server_recorded_at_us"]
        return envelope



def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _encode_dt(value: datetime) -> int:
    return int(_to_utc(value).timestamp() * 1_000_000)


def _decode_dt(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000, timezone.utc)


def _payload_digest_a(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _payload_digest_b(payload_json: str) -> str:
    return hashlib.blake2b(_length_prefixed(payload_json.encode("utf-8"))).hexdigest()


def _anchor_digest_a(
    *,
    position: int,
    record_id: str,
    previous_digest: str,
    payload_digest: str,
    server_recorded_at_us: int,
) -> str:
    material = canonical_json(
        {
            "domain": "substrate-a-v1",
            "position": position,
            "record_id": record_id,
            "previous_digest": previous_digest,
            "payload_digest": payload_digest,
            "server_recorded_at_us": server_recorded_at_us,
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _anchor_digest_b(
    *,
    position: int,
    record_id: str,
    previous_digest: str,
    payload_digest: str,
    server_recorded_at_us: int,
) -> str:
    h = hashlib.blake2b()
    for part in (
        b"substrate-b-v1",
        str(position).encode("ascii"),
        record_id.encode("utf-8"),
        previous_digest.encode("ascii"),
        payload_digest.encode("ascii"),
        str(server_recorded_at_us).encode("ascii"),
    ):
        h.update(_length_prefixed(part))
    return h.hexdigest()


def _length_prefixed(data: bytes) -> bytes:
    return len(data).to_bytes(8, "big") + data


def _anchor_from_mapping(row: Any) -> _AnchorData:
    return _AnchorData(
        position=int(row["position"]),
        record_id=str(row["record_id"]),
        previous_digest=str(row["previous_digest"]),
        payload_digest=str(row["payload_digest"]),
        anchor_digest=str(row["anchor_digest"]),
        server_recorded_at_us=int(row["server_recorded_at_us"]),
    )


def _recompute_anchor(substrate: SubstrateName, row: _AnchorData, payload_digest: str) -> str:
    if substrate == "A":
        return _anchor_digest_a(
            position=row.position,
            record_id=row.record_id,
            previous_digest=row.previous_digest,
            payload_digest=payload_digest,
            server_recorded_at_us=row.server_recorded_at_us,
        )
    return _anchor_digest_b(
        position=row.position,
        record_id=row.record_id,
        previous_digest=row.previous_digest,
        payload_digest=payload_digest,
        server_recorded_at_us=row.server_recorded_at_us,
    )


def _break_report(
    substrate: SubstrateName,
    *,
    total: int,
    position: int | None,
    record_id: str | None,
    recomputed: str | None,
    stored: str | None,
    reason: str,
) -> ChainVerificationReport:
    return ChainVerificationReport(
        substrate=substrate,
        valid=False,
        total_records_verified=total,
        break_position=position,
        break_record_id=record_id,
        recomputed_digest=recomputed,
        stored_digest=stored,
        reason=reason,
    )


def locals_without_self(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key not in {"self", "connection"}}
