"""Seed demo data without hardware; refuse SQLite when Postgres is absent."""

from __future__ import annotations

import io
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from airgap.models import Policy, Request, database_url, session_factory
from airgap.models import engine as application_engine
from airgap.vocab import PolicyAction, Verdict

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import seed_demo  # noqa: E402


def test_seed_exits_nonzero_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    buf = io.StringIO()
    assert seed_demo.run_seed(stdout=buf) != 0
    text_out = buf.getvalue()
    assert "DATABASE_URL" in text_out
    assert "SQLite" in text_out
    assert text_out.startswith("RED")


def test_seed_exits_nonzero_on_wrong_url_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://airgap@localhost/airgap")
    buf = io.StringIO()
    assert seed_demo.run_seed(stdout=buf) != 0
    assert "postgresql+pg8000://" in buf.getvalue()


def test_policies_keep_auto_approve_envelope_empty() -> None:
    assert all(
        row.action is not PolicyAction.AUTO_APPROVE for row in seed_demo.DEMO_POLICIES
    )
    actions = {row.action for row in seed_demo.DEMO_POLICIES}
    assert PolicyAction.ESCALATE in actions
    assert PolicyAction.BLOCK in actions
    gated = [row for row in seed_demo.DEMO_POLICIES if row.relay_gated]
    assert gated
    assert all(row.action is not PolicyAction.AUTO_APPROVE for row in gated)


def test_history_constants_include_known_tool() -> None:
    matching = [
        row
        for row in seed_demo.DEMO_REQUESTS
        if row.tool_name == seed_demo.HISTORY_TOOL_NAME
    ]
    assert len(matching) >= 2
    assert {row.verdict for row in matching} >= {Verdict.APPROVED, Verdict.DENIED}


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url())
    return config


def _postgres_configured() -> bool:
    return os.environ.get("DATABASE_URL", "").startswith("postgresql+pg8000://")


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    if not _postgres_configured():
        pytest.skip("needs DATABASE_URL postgresql+pg8000://")
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


@pytest.fixture
def empty_demo_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            text("TRUNCATE TABLE warden_assessments RESTART IDENTITY CASCADE")
        )
        connection.execute(text("TRUNCATE TABLE requests CASCADE"))
        connection.execute(text("TRUNCATE TABLE policies CASCADE"))
        connection.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY"))


def test_seed_makes_search_decision_history_return_rows(
    empty_demo_tables: None,
) -> None:
    buf = io.StringIO()
    assert seed_demo.run_seed(stdout=buf) == 0
    assert seed_demo.HISTORY_TOOL_NAME in buf.getvalue()

    with session_factory()() as session:
        history = seed_demo.history_for_tool(session, seed_demo.HISTORY_TOOL_NAME)
        assert history
        assert {entry.verdict for entry in history} >= {
            Verdict.APPROVED.value,
            Verdict.DENIED.value,
        }
        patterns = set(session.scalars(select(Policy.tool_pattern)))
        assert "db.drop_*" in patterns
        assert "pump.start" in patterns
        pump = session.get(Policy, "pump.start")
        assert pump is not None
        assert pump.relay_gated is True
        assert pump.action != PolicyAction.AUTO_APPROVE.value
        assert session.get(Request, "d15a0001") is not None

    buf2 = io.StringIO()
    assert seed_demo.run_seed(stdout=buf2) == 0
    assert "already present" in buf2.getvalue()
