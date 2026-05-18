from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Thread

import pytest
from sqlalchemy import text

from prospective_harness.attestation import (
    GENESIS_A,
    GENESIS_B,
    AttestationStore,
    ChainVerificationError,
    WriteLatencyExceededError,
    _anchor_digest_a,
    _anchor_digest_b,
    _payload_digest_a,
    _payload_digest_b,
)
from prospective_harness.hashing import canonical_json


class SequenceClock:
    def __init__(self, *values: datetime):
        self.values = list(values)

    def __call__(self) -> datetime:
        if len(self.values) == 1:
            return self.values[0]
        return self.values.pop(0)


def make_store(tmp_path, *, clock=None, max_write_latency_seconds=1.0):
    return AttestationStore(
        sqlite_path=tmp_path / "attestation.sqlite3",
        max_write_latency_seconds=max_write_latency_seconds,
        _clock=clock,
    )


def test_write_returns_envelope_anchored_to_two_substrates(tmp_path):
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    store = make_store(tmp_path, clock=SequenceClock(t0, t0 + timedelta(milliseconds=10)))

    envelope = store.write({"kind": "hypothesis", "score": 0.82})

    assert envelope.record_id
    assert envelope.payload == {"kind": "hypothesis", "score": 0.82}
    assert envelope.anchor_a.substrate == "A"
    assert envelope.anchor_b.substrate == "B"
    assert envelope.anchor_a.digest != envelope.anchor_b.digest
    assert envelope.chain_position_a == 1
    assert envelope.chain_position_b == 1
    assert envelope.server_recorded_at == t0


def test_read_reverifies_both_chains_and_returns_envelope(tmp_path):
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    store = make_store(tmp_path, clock=SequenceClock(t0, t0 + timedelta(milliseconds=10)))
    envelope = store.write({"kind": "hypothesis"})

    loaded = store.read(envelope.record_id)

    assert loaded == envelope


def test_verify_chain_reports_total_records_for_each_substrate(tmp_path):
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    store = make_store(
        tmp_path,
        clock=SequenceClock(
            t0,
            t0 + timedelta(milliseconds=10),
            t0 + timedelta(seconds=1),
            t0 + timedelta(seconds=1, milliseconds=10),
        ),
    )
    first = store.write({"n": 1})
    second = store.write({"n": 2})

    report_a = store.verify_chain("A")
    report_b = store.verify_chain("B", up_to_record_id=second.record_id)

    assert report_a.valid is True
    assert report_b.valid is True
    assert report_a.total_records_verified == 2
    assert report_b.total_records_verified == 2
    assert report_a.last_verified_record_id == second.record_id
    assert store.verify_chain("A", up_to_record_id=first.record_id).total_records_verified == 1


def test_raw_payload_update_is_detected_by_chain_verification(tmp_path):
    store = make_store(tmp_path)
    envelope = store.write({"kind": "hypothesis", "value": 1})

    with store.engine.begin() as connection:
        connection.execute(
            text("UPDATE attestation_envelopes SET payload_json = :payload WHERE record_id = :record_id"),
            {"payload": '{"kind":"hypothesis","value":999}', "record_id": envelope.record_id},
        )

    report = store.verify_chain("A")
    assert report.valid is False
    assert report.break_position == 1
    assert report.recomputed_digest is not None
    assert report.stored_digest is not None
    with pytest.raises(ChainVerificationError):
        store.read(envelope.record_id)


def test_forged_insert_with_locally_correct_hash_is_detected(tmp_path):
    store = make_store(tmp_path)
    store.write({"n": 1})
    forged_payload = {"n": "forged"}

    with store.engine.begin() as connection:
        payload_json = canonical_json(forged_payload)
        recorded_at_us = int(datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp() * 1_000_000)
        payload_digest = _payload_digest_a(payload_json)
        anchor_digest = _anchor_digest_a(
            position=2,
            record_id="forged-record",
            previous_digest=GENESIS_A,
            payload_digest=payload_digest,
            server_recorded_at_us=recorded_at_us,
        )
        connection.execute(
            text("INSERT INTO attestation_envelopes (record_id, payload_json, server_recorded_at_us) VALUES (:record_id, :payload_json, :recorded_at)"),
            {"record_id": "forged-record", "payload_json": payload_json, "recorded_at": recorded_at_us},
        )
        connection.execute(
            text("INSERT INTO attestation_anchor_a (position, record_id, previous_digest, payload_digest, anchor_digest, server_recorded_at_us) VALUES (:position, :record_id, :previous_digest, :payload_digest, :anchor_digest, :recorded_at)"),
            {
                "position": 2,
                "record_id": "forged-record",
                "previous_digest": GENESIS_A,
                "payload_digest": payload_digest,
                "anchor_digest": anchor_digest,
                "recorded_at": recorded_at_us,
            },
        )

    report = store.verify_chain("A")
    assert report.valid is False
    assert report.break_position == 2
    assert report.recomputed_digest != report.stored_digest


def test_delete_middle_record_is_detected_by_chain_verification(tmp_path):
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    store = make_store(
        tmp_path,
        clock=SequenceClock(
            t0,
            t0 + timedelta(milliseconds=10),
            t0 + timedelta(seconds=1),
            t0 + timedelta(seconds=1, milliseconds=10),
            t0 + timedelta(seconds=2),
            t0 + timedelta(seconds=2, milliseconds=10),
        ),
    )
    store.write({"n": 1})
    middle = store.write({"n": 2})
    store.write({"n": 3})

    with store.engine.begin() as connection:
        connection.execute(text("DELETE FROM attestation_anchor_a WHERE record_id = :record_id"), {"record_id": middle.record_id})

    report = store.verify_chain("A")
    assert report.valid is False
    assert report.break_position == 2


def test_substrate_a_intact_b_tampered_makes_read_raise(tmp_path):
    store = make_store(tmp_path)
    envelope = store.write({"n": 1})

    with store.engine.begin() as connection:
        connection.execute(
            text("UPDATE attestation_anchor_b SET anchor_digest = :digest WHERE record_id = :record_id"),
            {"digest": "0" * 128, "record_id": envelope.record_id},
        )

    assert store.verify_chain("A").valid is True
    assert store.verify_chain("B").valid is False
    with pytest.raises(ChainVerificationError):
        store.read(envelope.record_id)


def test_substrate_b_intact_a_tampered_makes_read_raise(tmp_path):
    store = make_store(tmp_path)
    envelope = store.write({"n": 1})

    with store.engine.begin() as connection:
        connection.execute(
            text("UPDATE attestation_anchor_a SET anchor_digest = :digest WHERE record_id = :record_id"),
            {"digest": "0" * 64, "record_id": envelope.record_id},
        )

    assert store.verify_chain("A").valid is False
    assert store.verify_chain("B").valid is True
    with pytest.raises(ChainVerificationError):
        store.read(envelope.record_id)


def test_intact_but_cross_substrate_inconsistent_makes_read_raise(tmp_path):
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    store = make_store(
        tmp_path,
        clock=SequenceClock(
            t0,
            t0 + timedelta(milliseconds=10),
            t0 + timedelta(seconds=1),
            t0 + timedelta(seconds=1, milliseconds=10),
        ),
    )
    first = store.write({"n": 1})
    second = store.write({"n": 2})

    with store.engine.begin() as connection:
        connection.execute(
            text("UPDATE attestation_anchor_b SET record_id = :temp WHERE record_id = :second"),
            {"temp": "temporary-swap-id", "second": second.record_id},
        )
        connection.execute(
            text("UPDATE attestation_anchor_b SET record_id = :second WHERE record_id = :first"),
            {"first": first.record_id, "second": second.record_id},
        )
        connection.execute(
            text("UPDATE attestation_anchor_b SET record_id = :first WHERE record_id = :temp"),
            {"temp": "temporary-swap-id", "first": first.record_id},
        )
        previous = GENESIS_B
        rows = connection.execute(text("SELECT * FROM attestation_anchor_b ORDER BY position ASC")).mappings().all()
        for row in rows:
            envelope = connection.execute(
                text("SELECT payload_json, server_recorded_at_us FROM attestation_envelopes WHERE record_id = :record_id"),
                {"record_id": row["record_id"]},
            ).mappings().one()
            payload_digest = _payload_digest_b(envelope["payload_json"])
            digest = _anchor_digest_b(
                position=row["position"],
                record_id=row["record_id"],
                previous_digest=previous,
                payload_digest=payload_digest,
                server_recorded_at_us=envelope["server_recorded_at_us"],
            )
            connection.execute(
                text("UPDATE attestation_anchor_b SET previous_digest = :previous, payload_digest = :payload_digest, anchor_digest = :digest, server_recorded_at_us = :recorded_at WHERE position = :position"),
                {
                    "previous": previous,
                    "payload_digest": payload_digest,
                    "digest": digest,
                    "recorded_at": envelope["server_recorded_at_us"],
                    "position": row["position"],
                },
            )
            previous = digest

    assert store.verify_chain("A").valid is True
    assert store.verify_chain("B").valid is True
    with pytest.raises(ChainVerificationError, match="cross-substrate"):
        store.read(second.record_id)


def test_concurrent_writers_produce_well_defined_verifiable_order(tmp_path):
    store = make_store(tmp_path)
    results = []
    errors = []

    def write_payload(value):
        try:
            results.append(store.write({"writer": value}))
        except Exception as exc:  # pragma: no cover - failure detail for test
            errors.append(exc)

    threads = [Thread(target=write_payload, args=(value,)) for value in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(results) == 2
    assert sorted(envelope.chain_position_a for envelope in results) == [1, 2]
    assert sorted(envelope.chain_position_b for envelope in results) == [1, 2]
    assert store.verify_chain("A").valid is True
    assert store.verify_chain("B").valid is True


def test_latency_exceeding_bound_fails_closed_without_partial_rows(tmp_path):
    t0 = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    store = make_store(
        tmp_path,
        clock=SequenceClock(t0, t0 + timedelta(seconds=2)),
        max_write_latency_seconds=1.0,
    )

    with pytest.raises(WriteLatencyExceededError):
        store.write({"too": "slow"})

    with store.engine.begin() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM attestation_envelopes")) == 0
        assert connection.scalar(text("SELECT COUNT(*) FROM attestation_anchor_a")) == 0
        assert connection.scalar(text("SELECT COUNT(*) FROM attestation_anchor_b")) == 0


def test_caller_supplied_server_recorded_at_is_payload_only(tmp_path):
    authoritative = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    forged = "2099-01-01T00:00:00+00:00"
    store = make_store(tmp_path, clock=SequenceClock(authoritative, authoritative + timedelta(milliseconds=10)))

    envelope = store.write({"server_recorded_at": forged, "value": 1})

    assert envelope.payload["server_recorded_at"] == forged
    assert envelope.server_recorded_at == authoritative


def test_no_single_substrate_write_api_exists(tmp_path):
    store = make_store(tmp_path)

    assert not hasattr(store, "write_anchor_a")
    assert not hasattr(store, "write_anchor_b")
    assert not hasattr(store, "write_single_substrate")


def test_default_latency_bound_holds_under_synthetic_load(tmp_path):
    import time

    import numpy as np

    store = make_store(tmp_path)
    latencies = []
    for index in range(25):
        start = time.perf_counter()
        store.write({"synthetic_index": index})
        latencies.append(time.perf_counter() - start)

    assert np.percentile(latencies, 99) < store.max_write_latency_seconds
    assert store.verify_chain("A").total_records_verified == 25
    assert store.verify_chain("B").total_records_verified == 25
