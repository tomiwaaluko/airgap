"""Live demo broker: load seeded policies and history, then bind loopback."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Mapping, Sequence
from typing import TextIO, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from airgap.audit import append
from airgap.broker import RequestStore, StoredRequest, create_app, run
from airgap.models import Policy, Request, database_url, session_factory
from airgap.policy import PolicyRule, matches_tool
from airgap.protocol import BootEvent, decode
from airgap.supervisor import Supervisor
from airgap.transport import SerialTransport
from airgap.vocab import AuditEvent, DecidedBy, PolicyAction, Verdict
from airgap.warden import Warden

MISSING_SERIAL = (
    "AIRGAP_SERIAL_PORT is missing; live demo refuses MockTransport"
)
NO_BOOT = "no boot frame after serial open (UNO typically resets on open)"
WARDEN_UNAVAILABLE = (
    "ANTHROPIC_API_KEY is missing; Warden unavailable, human path still works"
)
BOOT_WAIT_S = 3.0
_TOKEN_NAMES = ("AIRGAP_AGENT_TOKEN", "AIRGAP_UI_TOKEN", "AIRGAP_UI_RO_TOKEN")
_ORIGIN = "http://127.0.0.1:3000"


class _UnavailableWardenClient:
    """messages.create raises so Warden.triage fails closed to escalate."""

    def __init__(self) -> None:
        self.messages = self

    def create(self, **kwargs: object) -> object:
        raise RuntimeError(WARDEN_UNAVAILABLE)


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(cast(Mapping[str, object], value))
    return {}


def _action(value: object) -> PolicyAction:
    if isinstance(value, PolicyAction):
        return value
    return PolicyAction(str(value))


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (Verdict, DecidedBy)):
        return value.value
    return str(value)


def policy_rules_from_rows(rows: Sequence[object]) -> list[PolicyRule]:
    """Map Policy ORM rows or seed dataclasses onto the broker's PolicyRule."""
    rules: list[PolicyRule] = []
    for row in rows:
        rules.append(
            PolicyRule(
                tool_pattern=str(row.tool_pattern),
                min_dial=int(row.min_dial),
                action=_action(row.action),
                relay_gated=bool(row.relay_gated),
            )
        )
    return rules


def _relay_gated_for(tool_name: str, policies: Sequence[PolicyRule]) -> bool:
    for rule in policies:
        if matches_tool(rule.tool_pattern, tool_name):
            return rule.relay_gated
    return False


def request_store_from_rows(
    rows: Sequence[object],
    *,
    policies: Sequence[PolicyRule] = (),
) -> RequestStore:
    """Load resolved requests so Broker._history() can serve the Warden tool."""
    store = RequestStore()
    for row in rows:
        verdict = _optional_str(getattr(row, "verdict", None))
        decided_by = _optional_str(getattr(row, "decided_by", None))
        if verdict is None or decided_by is None:
            continue
        tool_name = str(row.tool_name)
        reason = getattr(row, "reason", None)
        store.put(
            StoredRequest(
                id=str(row.id),
                actor=str(row.actor),
                tool_name=tool_name,
                tool_args=_as_dict(row.tool_args),
                justification=str(row.justification),
                risk_class=str(row.risk_class),
                relay_gated=_relay_gated_for(tool_name, policies),
                dwell_s=int(getattr(row, "dwell_s", 60) or 60),
                created_at=0.0,
                verdict=verdict,
                decided_by=decided_by,
                reason="" if reason is None else str(reason),
            )
        )
    return store


def load_demo_state(session: Session) -> tuple[list[PolicyRule], RequestStore]:
    """Postgres → in-memory policies + resolved history for create_app."""
    rules = policy_rules_from_rows(list(session.scalars(select(Policy))))
    store = request_store_from_rows(
        list(session.scalars(select(Request))),
        policies=rules,
    )
    return rules, store


def require_serial_port(*, env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    port = source.get("AIRGAP_SERIAL_PORT", "")
    if not port:
        raise SystemExit(f"RED    {MISSING_SERIAL}")
    return port


def tokens_from_env(*, env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    missing = [name for name in _TOKEN_NAMES if not source.get(name)]
    if missing:
        raise SystemExit(f"RED    missing {', '.join(missing)}")
    return {
        "agent": source["AIRGAP_AGENT_TOKEN"],
        "ui": source["AIRGAP_UI_TOKEN"],
        "ui_ro": source["AIRGAP_UI_RO_TOKEN"],
    }


def on_audit(event: str, request_id: str | None, payload: object) -> None:
    append(AuditEvent(event), request_id, payload)


def boot_queued(transport: object) -> bool:
    """True if a boot JSON line is already in `_event_lines` (no pop)."""
    lines = getattr(transport, "_event_lines", None)
    if lines is None:
        return False
    for line in lines:
        if isinstance(line, bytes) and isinstance(decode(line), BootEvent):
            return True
    return False


async def wait_for_boot_queued(
    transport: object, *, timeout_s: float = BOOT_WAIT_S
) -> str | None:
    """Ingest into the event queue until boot is visible. Do not consume it."""

    async def _poll() -> bool:
        while True:
            ingest = getattr(transport, "_ingest_available", None)
            if callable(ingest):
                ingest()
            if boot_queued(transport):
                return True
            if not getattr(transport, "connected", True):
                return False
            await asyncio.sleep(0)

    try:
        found = await asyncio.wait_for(_poll(), timeout=timeout_s)
    except TimeoutError:
        return NO_BOOT
    except Exception as exc:
        return f"{NO_BOOT}: {exc}"
    if not found:
        return NO_BOOT
    return None


def warden_from_env(
    session: object,
    *,
    env: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
) -> Warden:
    """Use Anthropic when keyed; otherwise a client whose create() raises."""
    source = os.environ if env is None else env
    key = source.get("ANTHROPIC_API_KEY", "")
    if not key:
        if stdout is not None:
            stdout.write(f"RED    {WARDEN_UNAVAILABLE}\n")
        return Warden(_UnavailableWardenClient(), session)
    from anthropic import Anthropic

    return Warden(Anthropic(api_key=key), session)


def run_broker(*, stdout: TextIO | None = None) -> int:
    out = sys.stdout if stdout is None else stdout
    try:
        port = require_serial_port()
        tokens = tokens_from_env()
        database_url()
    except SystemExit as exc:
        message = str(exc)
        if message:
            out.write(f"{message}\n")
        return 1
    except RuntimeError as exc:
        out.write(f"RED    {exc}\n")
        return 1

    try:
        transport = SerialTransport(port)
    except Exception as exc:
        out.write(f"RED    {MISSING_SERIAL}: {exc}\n")
        return 1
    boot_err = asyncio.run(wait_for_boot_queued(transport))
    if boot_err is not None:
        out.write(f"RED    {boot_err}\n")
        transport.close()
        return 1

    factory = session_factory()
    session = factory()
    rules, store = load_demo_state(session)
    app = create_app(
        supervisor=Supervisor(transport),
        warden=warden_from_env(session, stdout=out),
        on_audit=on_audit,
        clock=time.monotonic,
        policies=rules,
        store=store,
        tokens=tokens,
        origin_allowlist=(_ORIGIN,),
        host_loop=True,
    )
    run(app, port=8741)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    return run_broker()


if __name__ == "__main__":
    raise SystemExit(main())
