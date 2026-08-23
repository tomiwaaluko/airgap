"""Database schema and append-only audit-log tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    CHAR,
    Boolean,
    DateTime,
    Integer,
    SmallInteger,
    Text,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.exc import DBAPIError

from airgap.models import (
    AuditLog,
    Base,
    Policy,
    Request,
    WardenAssessment,
    database_url,
)

ROOT = Path(__file__).parents[1]


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url())
    return config


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    """Start and finish each module against a genuinely empty database."""
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    database = create_engine(database_url())
    try:
        yield database
    finally:
        database.dispose()
        command.downgrade(config, "base")


def test_models_define_the_frozen_schema() -> None:
    """Catch a model whose table, column, type, default, or index drifts."""
    assert set(Base.metadata.tables) == {
        "requests",
        "warden_assessments",
        "audit_log",
        "policies",
    }

    requests = Request.__table__
    assert list(requests.columns.keys()) == [
        "id",
        "created_at",
        "resolved_at",
        "actor",
        "tool_name",
        "tool_args",
        "justification",
        "risk_class",
        "verdict",
        "decided_by",
        "reason",
        "dial_at_decision",
        "latency_ms",
    ]
    assert isinstance(requests.c.id.type, CHAR)
    assert requests.c.id.type.length == 8
    assert isinstance(requests.c.created_at.type, DateTime)
    assert requests.c.created_at.type.timezone is True
    assert isinstance(requests.c.tool_args.type, JSONB)
    assert isinstance(requests.c.dial_at_decision.type, SmallInteger)
    assert isinstance(requests.c.latency_ms.type, Integer)
    assert {index.name for index in requests.indexes} == {
        "ix_requests_created_at_desc",
        "ix_requests_tool_name_verdict",
    }

    assessments = WardenAssessment.__table__
    assert list(assessments.columns.keys()) == [
        "id",
        "request_id",
        "model",
        "risk_class",
        "reversible",
        "blast_radius",
        "injection_suspected",
        "reasoning",
        "tool_calls",
        "latency_ms",
        "created_at",
    ]
    assert isinstance(assessments.c.request_id.type, CHAR)
    assert assessments.c.request_id.type.length == 8
    assert isinstance(assessments.c.reversible.type, Boolean)
    assert isinstance(assessments.c.tool_calls.type, JSONB)

    audit_log = AuditLog.__table__
    assert list(audit_log.columns.keys()) == [
        "seq",
        "at",
        "event",
        "request_id",
        "payload",
        "prev_hash",
        "row_hash",
    ]
    assert isinstance(audit_log.c.request_id.type, CHAR)
    assert audit_log.c.request_id.type.length == 8
    assert isinstance(audit_log.c.payload.type, JSONB)
    assert isinstance(audit_log.c.prev_hash.type, CHAR)
    assert audit_log.c.prev_hash.type.length == 64
    assert isinstance(audit_log.c.row_hash.type, CHAR)
    assert audit_log.c.row_hash.type.length == 64

    policies = Policy.__table__
    assert list(policies.columns.keys()) == [
        "tool_pattern",
        "min_dial",
        "action",
        "relay_gated",
        "dwell_s",
        "updated_at",
        "updated_by",
    ]
    assert isinstance(policies.c.tool_pattern.type, Text)
    assert isinstance(policies.c.min_dial.type, SmallInteger)
    assert isinstance(policies.c.relay_gated.type, Boolean)
    assert policies.c.relay_gated.server_default is not None
    assert policies.c.dwell_s.server_default is not None


def test_migration_creates_the_frozen_postgres_schema(migrated_engine: Engine) -> None:
    """Catch migrations that do not materialize the declarative contract."""
    inspector = inspect(migrated_engine)
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "requests",
        "warden_assessments",
        "audit_log",
        "policies",
    }
    assert {index["name"] for index in inspector.get_indexes("requests")} == {
        "ix_requests_created_at_desc",
        "ix_requests_tool_name_verdict",
    }

    config = _alembic_config()
    command.downgrade(config, "base")
    assert set(inspect(migrated_engine).get_table_names()) == {"alembic_version"}
    command.upgrade(config, "head")


def test_audit_log_rejects_updates(migrated_engine: Engine) -> None:
    """Catch removal or weakening of the database append-only protection."""
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_log "
                "(at, event, payload, prev_hash, row_hash) VALUES "
                "(now(), 'request_created', '{}', :previous, :row)"
            ),
            {"previous": "0" * 64, "row": "1" * 64},
        )

    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        with migrated_engine.begin() as connection:
            connection.execute(text("UPDATE audit_log SET event = 'armed'"))


def test_audit_log_rejects_deletes(migrated_engine: Engine) -> None:
    """Catch removal or weakening of the database append-only protection."""
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_log "
                "(at, event, payload, prev_hash, row_hash) VALUES "
                "(now(), 'request_created', '{}', :previous, :row)"
            ),
            {"previous": "0" * 64, "row": "2" * 64},
        )

    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        with migrated_engine.begin() as connection:
            connection.execute(text("DELETE FROM audit_log"))
