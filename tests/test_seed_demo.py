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

from airgap.broker import Broker
from airgap.models import Policy, Request, database_url, session_factory
from airgap.models import engine as application_engine
from airgap.supervisor import Supervisor
from airgap.transport import MockTransport
from airgap.vocab import PolicyAction, Verdict
from airgap.warden import TriageRequest, Warden

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_broker  # noqa: E402
import seed_demo  # noqa: E402


class _NoModel:
    def create(self, **kwargs: object) -> object:
        raise RuntimeError("loader test must not call the model")


class _StubClient:
    messages = _NoModel()


class _StubSession:
    def add(self, instance: object) -> None:
        del instance


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


def test_demo_dsn_is_not_used_as_test_database() -> None:
    demo = "postgresql+pg8000://airgap:airgap@127.0.0.1:5432/airgap"
    assert _looks_like_demo_dsn(demo)
    assert _looks_like_demo_dsn(demo + "/")
    assert not _looks_like_demo_dsn(
        "postgresql+pg8000://airgap:airgap@127.0.0.1:5432/airgap_test"
    )


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url())
    return config


def _looks_like_demo_dsn(url: str) -> bool:
    """Refuse the DEMO.md operator DSN so pytest cannot wipe seeded history."""
    normalized = url.rstrip("/")
    return "://airgap:airgap@" in normalized and normalized.endswith("/airgap")


def _seed_test_url() -> str | None:
    dedicated = os.environ.get("AIRGAP_TEST_DATABASE_URL", "")
    if dedicated.startswith("postgresql+pg8000://"):
        if _looks_like_demo_dsn(dedicated):
            return None
        return dedicated
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgresql+pg8000://") and not _looks_like_demo_dsn(url):
        return url
    return None


@pytest.fixture(scope="module")
def migrated_engine() -> Iterator[Engine]:
    url = _seed_test_url()
    if url is None:
        pytest.skip(
            "needs AIRGAP_TEST_DATABASE_URL (or a non-demo DATABASE_URL) "
            "postgresql+pg8000://; refuses the DEMO.md DSN"
        )
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    application_engine.cache_clear()
    config = _alembic_config()
    command.upgrade(config, "head")
    database = application_engine()
    try:
        yield database
    finally:
        database.dispose()
        application_engine.cache_clear()
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def test_loader_feeds_broker_history_and_warden_search() -> None:
    rules = run_broker.policy_rules_from_rows(seed_demo.DEMO_POLICIES)
    store = run_broker.request_store_from_rows(seed_demo.DEMO_REQUESTS, policies=rules)
    warden = Warden(_StubClient(), _StubSession())
    broker = Broker(
        Supervisor(MockTransport()),
        warden,
        on_audit=lambda *_a: None,
        clock=lambda: 0.0,
        policies=rules,
        store=store,
    )
    history = broker._history()
    drop = [
        entry
        for entry in history
        if entry.tool_name == seed_demo.HISTORY_TOOL_NAME
    ]
    assert drop
    assert {entry.verdict for entry in drop} >= {
        Verdict.APPROVED.value,
        Verdict.DENIED.value,
    }
    pump = store.get("d15a0005")
    assert pump is not None
    assert pump.relay_gated is True
    drop_row = store.get("d15a0001")
    assert drop_row is not None
    assert drop_row.relay_gated is False

    found = warden._run_tool(
        "search_decision_history",
        {"tool_name": seed_demo.HISTORY_TOOL_NAME},
        TriageRequest(
            request_id="d15a0001",
            actor="test",
            tool_name=seed_demo.HISTORY_TOOL_NAME,
            tool_args={},
            justification="loader",
        ),
        policies=rules,
        dial=0,
        history=history,
    )
    assert isinstance(found, list)
    assert any(row["tool_name"] == seed_demo.HISTORY_TOOL_NAME for row in found)


def test_live_broker_refuses_missing_serial_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AIRGAP_SERIAL_PORT", raising=False)
    with pytest.raises(SystemExit, match="AIRGAP_SERIAL_PORT"):
        run_broker.require_serial_port()
    buf = io.StringIO()
    assert run_broker.run_broker(stdout=buf) != 0
    assert "AIRGAP_SERIAL_PORT" in buf.getvalue()


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
