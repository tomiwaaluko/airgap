"""Audit-chain behavior and tamper-localization tests."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from freezegun import freeze_time
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import airgap.audit as audit
from airgap.models import AuditLog, database_url
from airgap.models import engine as application_engine
from airgap.vocab import AuditEvent

ROOT = Path(__file__).parents[1]
GENESIS_HASH = "0" * 64
PINNED_AT = datetime(2026, 8, 23, 14, 15, 16, 123456, tzinfo=UTC)
PINNED_HASH = "be31845f2233c00efaa089a7db809d38b1025655e2b7e8d74868b2585e91292c"
UNLINKED_SECOND_HASH = (
    "bf5459987a22c7e04b4ce814f32e596e8d8114fe0856c4f0bc3c5618dd1cad0b"
)
FIELD_MUTATIONS = {
    "seq": "seq = seq + 100",
    "at": "at = at + interval '1 second'",
    "event": "event = 'safe_state'",
    "request_id": "request_id = 'deadbeef'",
    "payload": "payload = '{\"changed\": true}'::jsonb",
    "prev_hash": "prev_hash = repeat('a', 64)",
    "row_hash": "row_hash = repeat('b', 64)",
}


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url())
    return config


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    """Give the public API the same real PostgreSQL schema production uses."""
    config = _alembic_config()
    application_engine.cache_clear()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    database = application_engine()
    try:
        yield database
    finally:
        database.dispose()
        application_engine.cache_clear()
        command.downgrade(config, "base")


@pytest.fixture(autouse=True)
def empty_audit_log(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY"))


def _rows(database: Engine) -> list[AuditLog]:
    with Session(database) as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.seq)))


def _append_three_rows() -> None:
    audit.append(AuditEvent.REQUEST_CREATED, "a0000001", {"step": 1})
    audit.append(AuditEvent.ARMED, "a0000001", {"step": 2})
    audit.append(AuditEvent.RESOLVED, "a0000001", {"step": 3})


def _bypass_trigger_update(
    database: Engine,
    assignment: str,
    target_seq: int,
) -> None:
    with database.begin() as connection:
        connection.execute(
            text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only")
        )
    try:
        with database.begin() as connection:
            connection.execute(
                text(f"UPDATE audit_log SET {assignment} WHERE seq = :target_seq"),
                {"target_seq": target_seq},
            )
    finally:
        with database.begin() as connection:
            connection.execute(
                text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only")
            )


@freeze_time(PINNED_AT)
def test_append_matches_pinned_known_good_hash(migrated_engine: Engine) -> None:
    """Catch any byte-level drift in canonical JSON or row serialization."""
    audit.append(
        AuditEvent.REQUEST_CREATED,
        "a1b2c3d4",
        {
            "targets": ["alpha", "βeta"],
            "details": {"safe": True, "attempts": 2},
            "action": "déployer",
        },
    )

    [row] = _rows(migrated_engine)
    assert row.seq == 1
    assert row.at == PINNED_AT
    assert row.prev_hash == GENESIS_HASH
    assert row.row_hash == PINNED_HASH


@freeze_time(PINNED_AT)
def test_payload_key_order_does_not_change_hash(migrated_engine: Engine) -> None:
    first_payload = {
        "z": 3,
        "nested": {"z": False, "a": True},
        "a": 1,
    }
    second_payload = {
        "a": 1,
        "nested": {"a": True, "z": False},
        "z": 3,
    }

    audit.append(AuditEvent.REQUEST_CREATED, "a1b2c3d4", first_payload)
    first_hash = _rows(migrated_engine)[0].row_hash

    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY"))

    audit.append(AuditEvent.REQUEST_CREATED, "a1b2c3d4", second_payload)
    second_hash = _rows(migrated_engine)[0].row_hash

    assert first_hash == second_hash


@pytest.mark.parametrize(
    "payload",
    [
        {"value": -0.0},
        {"value": 1e-7},
        {"value": 1e20},
        {"value": 1e100},
    ],
)
def test_append_hashes_postgres_normalized_jsonb(
    migrated_engine: Engine,
    payload: object,
) -> None:
    """Hash the representation verification will read back from JSONB."""
    audit.append(AuditEvent.SAFE_STATE, None, payload)

    assert audit.verify_chain() == (True, None)


@pytest.mark.parametrize("request_id", ["", "abc", "A1B2C3D4", "a1b2c3g4"])
def test_append_rejects_request_ids_that_char_would_normalize(
    migrated_engine: Engine,
    request_id: str,
) -> None:
    with pytest.raises(ValueError, match="eight lowercase hexadecimal"):
        audit.append(AuditEvent.SAFE_STATE, request_id, {})

    assert _rows(migrated_engine) == []


def test_verify_chain_accepts_empty_chain() -> None:
    assert audit.verify_chain() == (True, None)


def test_verify_chain_accepts_healthy_chain() -> None:
    _append_three_rows()

    assert audit.verify_chain() == (True, None)


def test_verify_chain_detects_fractional_jsonb_precision_mutation(
    migrated_engine: Engine,
) -> None:
    """Catch JSONB verification that hydrates numerics through lossy floats."""
    audit.append(AuditEvent.SAFE_STATE, None, {"value": 0.1})
    _bypass_trigger_update(
        migrated_engine,
        "payload = jsonb_build_object("
        "'value', CAST('0.1000000000000000000000000000001' AS numeric))",
        1,
    )

    assert audit.verify_chain() == (False, 1)


@pytest.mark.parametrize("field", FIELD_MUTATIONS)
@pytest.mark.parametrize("target_seq", [1, 2, 3], ids=["first", "middle", "tail"])
def test_verify_chain_localizes_every_mutated_field(
    migrated_engine: Engine,
    field: str,
    target_seq: int,
) -> None:
    """Catch any stored field being omitted from verification."""
    _append_three_rows()
    _bypass_trigger_update(migrated_engine, FIELD_MUTATIONS[field], target_seq)

    mutated_seq = target_seq + 100 if field == "seq" else target_seq
    assert audit.verify_chain() == (False, mutated_seq)


@freeze_time(PINNED_AT)
def test_verify_chain_rejects_a_self_consistent_unlinked_row(
    migrated_engine: Engine,
) -> None:
    """Catch verification that checks row hashes but never checks links."""
    audit.append(AuditEvent.REQUEST_CREATED, "a0000001", {"step": 1})
    audit.append(AuditEvent.ARMED, "a0000001", {"step": 2})
    _bypass_trigger_update(
        migrated_engine,
        f"prev_hash = repeat('0', 64), row_hash = '{UNLINKED_SECOND_HASH}'",
        2,
    )

    assert audit.verify_chain() == (False, 2)


def test_verify_chain_starts_at_requested_sequence(migrated_engine: Engine) -> None:
    _append_three_rows()
    _bypass_trigger_update(migrated_engine, "event = 'safe_state'", 1)

    assert audit.verify_chain() == (False, 1)
    assert audit.verify_chain(from_seq=2) == (True, None)
