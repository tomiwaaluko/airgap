"""Seed demo policies and a decision history Postgres can actually serve."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TextIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from airgap.audit import append
from airgap.models import (
    Policy,
    Request,
    WardenAssessment,
    database_url,
    session_factory,
)
from airgap.vocab import AuditEvent, DecidedBy, PolicyAction, Verdict
from airgap.warden import DecisionHistoryEntry

# Product default: an empty auto-approve envelope. Unmatched tools escalate
# (and still honour a Warden block). Seed escalate/block examples only.
HISTORY_TOOL_NAME = "db.drop_table"
UPDATED_BY = "seed_demo"
_FIRST_REQUEST_ID = "d15a0001"
_MODEL = "claude-sonnet-4-20250514"
_BASE = datetime(2026, 8, 20, 15, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicySeed:
    tool_pattern: str
    min_dial: int
    action: PolicyAction
    relay_gated: bool
    dwell_s: int = 60


DEMO_POLICIES: tuple[PolicySeed, ...] = (
    PolicySeed(
        tool_pattern="db.drop_*",
        min_dial=0,
        action=PolicyAction.ESCALATE,
        relay_gated=False,
    ),
    PolicySeed(
        tool_pattern="shell.exec",
        min_dial=0,
        action=PolicyAction.BLOCK,
        relay_gated=False,
    ),
    PolicySeed(
        tool_pattern="pump.start",
        min_dial=0,
        action=PolicyAction.ESCALATE,
        relay_gated=True,
        dwell_s=60,
    ),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestSeed:
    id: str
    offset_s: int
    actor: str
    tool_name: str
    tool_args: dict[str, object]
    justification: str
    risk_class: str
    verdict: Verdict
    decided_by: DecidedBy
    reason: str
    dial_at_decision: int
    latency_ms: int
    blast_radius: str
    reversible: bool
    warden_reasoning: str
    audit: tuple[AuditEvent, ...]


DEMO_REQUESTS: tuple[RequestSeed, ...] = (
    RequestSeed(
        id=_FIRST_REQUEST_ID,
        offset_s=0,
        actor="claude-code/session-4f2a",
        tool_name=HISTORY_TOOL_NAME,
        tool_args={"table": "users_backup"},
        justification="cleaning up staging",
        risk_class="high",
        verdict=Verdict.APPROVED,
        decided_by=DecidedBy.HUMAN,
        reason="operator approved destructive DDL on staging",
        dial_at_decision=8,
        latency_ms=41904,
        blast_radius="412 rows",
        reversible=False,
        warden_reasoning="DROP TABLE is irreversible; escalate to a human.",
        audit=(
            AuditEvent.REQUEST_CREATED,
            AuditEvent.WARDEN_VERDICT,
            AuditEvent.ARMED,
            AuditEvent.BUTTON,
            AuditEvent.RESOLVED,
        ),
    ),
    RequestSeed(
        id="d15a0002",
        offset_s=3600,
        actor="claude-code/session-4f2a",
        tool_name=HISTORY_TOOL_NAME,
        tool_args={"table": "audit_archive"},
        justification="disk was full",
        risk_class="high",
        verdict=Verdict.DENIED,
        decided_by=DecidedBy.HUMAN,
        reason="user declined destructive DDL",
        dial_at_decision=7,
        latency_ms=22110,
        blast_radius="unknown row count",
        reversible=False,
        warden_reasoning="Irreversible DDL against a named archive table.",
        audit=(
            AuditEvent.REQUEST_CREATED,
            AuditEvent.WARDEN_VERDICT,
            AuditEvent.ARMED,
            AuditEvent.BUTTON,
            AuditEvent.RESOLVED,
        ),
    ),
    RequestSeed(
        id="d15a0003",
        offset_s=7200,
        actor="claude-code/session-91bc",
        tool_name=HISTORY_TOOL_NAME,
        tool_args={"table": "tmp_import"},
        justification="finished a one-off import",
        risk_class="high",
        verdict=Verdict.EXPIRED,
        decided_by=DecidedBy.SYSTEM,
        reason="request expired",
        dial_at_decision=6,
        latency_ms=1_800_000,
        blast_radius="staging import",
        reversible=False,
        warden_reasoning="Same DROP family as prior decisions; still escalate.",
        audit=(
            AuditEvent.REQUEST_CREATED,
            AuditEvent.WARDEN_VERDICT,
            AuditEvent.ARMED,
            AuditEvent.RESOLVED,
        ),
    ),
    RequestSeed(
        id="d15a0004",
        offset_s=10800,
        actor="claude-code/session-4f2a",
        tool_name="shell.exec",
        tool_args={"command": "rm -rf /"},
        justification="cleanup",
        risk_class="blocked",
        verdict=Verdict.DENIED,
        decided_by=DecidedBy.POLICY,
        reason="policy action is block",
        dial_at_decision=3,
        latency_ms=840,
        blast_radius="host filesystem",
        reversible=False,
        warden_reasoning="Unconstrained shell is blocked regardless of dial.",
        audit=(
            AuditEvent.REQUEST_CREATED,
            AuditEvent.WARDEN_VERDICT,
            AuditEvent.POLICY_OVERRIDE,
            AuditEvent.RESOLVED,
        ),
    ),
    RequestSeed(
        id="d15a0005",
        offset_s=14400,
        actor="claude-code/session-4f2a",
        tool_name="pump.start",
        tool_args={"circuit": "bench-lamp"},
        justification="live enforcement-mode check after bring-up",
        risk_class="medium",
        verdict=Verdict.APPROVED,
        decided_by=DecidedBy.HUMAN,
        reason="operator approved the dwell window",
        dial_at_decision=4,
        latency_ms=15320,
        blast_radius="bench lamp only",
        reversible=True,
        warden_reasoning="Relay-gated row cannot auto-approve; escalate.",
        audit=(
            AuditEvent.REQUEST_CREATED,
            AuditEvent.WARDEN_VERDICT,
            AuditEvent.ARMED,
            AuditEvent.BUTTON,
            AuditEvent.RESOLVED,
            AuditEvent.RELAY_CLOSED,
            AuditEvent.RELAY_OPENED,
        ),
    ),
)


def missing_database_url_message(exc: BaseException | None = None) -> str:
    detail = str(exc) if exc is not None else "DATABASE_URL is missing"
    return f"{detail}; seed_demo refuses to write SQLite"


def history_for_tool(session: Session, tool_name: str) -> list[DecisionHistoryEntry]:
    """The same shape `search_decision_history` returns for a known tool_name."""
    rows = session.scalars(
        select(Request)
        .where(Request.tool_name == tool_name)
        .where(Request.verdict.is_not(None))
        .order_by(Request.created_at)
    )
    entries: list[DecisionHistoryEntry] = []
    for row in rows:
        if row.verdict is None or row.decided_by is None:
            continue
        entries.append(
            DecisionHistoryEntry(
                tool_name=row.tool_name,
                verdict=row.verdict,
                decided_by=row.decided_by,
            )
        )
    return entries


def seed_policies(session: Session, *, now: datetime) -> None:
    for row in DEMO_POLICIES:
        session.merge(
            Policy(
                tool_pattern=row.tool_pattern,
                min_dial=row.min_dial,
                action=row.action.value,
                relay_gated=row.relay_gated,
                dwell_s=row.dwell_s,
                updated_at=now,
                updated_by=UPDATED_BY,
            )
        )


def _insert_request(session: Session, spec: RequestSeed) -> None:
    created = _BASE + timedelta(seconds=spec.offset_s)
    resolved = created + timedelta(milliseconds=spec.latency_ms)
    session.add(
        Request(
            id=spec.id,
            created_at=created,
            resolved_at=resolved,
            actor=spec.actor,
            tool_name=spec.tool_name,
            tool_args=spec.tool_args,
            justification=spec.justification,
            risk_class=spec.risk_class,
            verdict=spec.verdict.value,
            decided_by=spec.decided_by.value,
            reason=spec.reason,
            dial_at_decision=spec.dial_at_decision,
            latency_ms=spec.latency_ms,
        )
    )
    session.add(
        WardenAssessment(
            request_id=spec.id,
            model=_MODEL,
            risk_class=spec.risk_class,
            reversible=spec.reversible,
            blast_radius=spec.blast_radius,
            injection_suspected=False,
            reasoning=spec.warden_reasoning,
            tool_calls=[
                {
                    "name": "classify_risk",
                    "input": {"tool_name": spec.tool_name},
                    "output": {
                        "risk_class": spec.risk_class,
                        "reversible": spec.reversible,
                    },
                }
            ],
            latency_ms=min(spec.latency_ms, 2400),
            created_at=created + timedelta(milliseconds=80),
        )
    )


def _audit_request(spec: RequestSeed) -> None:
    payloads: dict[AuditEvent, object] = {
        AuditEvent.REQUEST_CREATED: {
            "actor": spec.actor,
            "tool_name": spec.tool_name,
            "tool_args": spec.tool_args,
        },
        AuditEvent.WARDEN_VERDICT: {
            "proposal": PolicyAction.ESCALATE.value,
            "risk_class": spec.risk_class,
        },
        AuditEvent.POLICY_OVERRIDE: {"action": PolicyAction.BLOCK.value},
        AuditEvent.ARMED: {"req": spec.id},
        AuditEvent.BUTTON: {
            "which": "approve" if spec.verdict is Verdict.APPROVED else "deny"
        },
        AuditEvent.RESOLVED: {
            "verdict": spec.verdict.value,
            "decided_by": spec.decided_by.value,
        },
        AuditEvent.RELAY_CLOSED: {"req": spec.id},
        AuditEvent.RELAY_OPENED: {"req": spec.id},
    }
    for event in spec.audit:
        append(event, spec.id, payloads[event])


def seed(session: Session, *, now: datetime | None = None) -> bool:
    """Insert demo rows. Returns False when this database was already seeded."""
    stamp = datetime.now(UTC) if now is None else now
    seed_policies(session, now=stamp)
    existing = session.get(Request, _FIRST_REQUEST_ID)
    if existing is not None:
        session.commit()
        return False
    for spec in DEMO_REQUESTS:
        _insert_request(session, spec)
    session.commit()
    for spec in DEMO_REQUESTS:
        _audit_request(spec)
    return True


def run_seed(*, stdout: TextIO | None = None) -> int:
    out = sys.stdout if stdout is None else stdout
    if not os.environ.get("DATABASE_URL"):
        out.write(f"RED    {missing_database_url_message()}\n")
        return 1
    try:
        database_url()
    except RuntimeError as exc:
        out.write(f"RED    {missing_database_url_message(exc)}\n")
        return 1
    try:
        factory = session_factory()
        with factory() as session:
            inserted = seed(session)
            history = history_for_tool(session, HISTORY_TOOL_NAME)
    except Exception as exc:
        out.write(f"RED    Postgres unreachable: {exc}\n")
        return 1
    verb = "seeded" if inserted else "already present"
    out.write(
        f"GREEN  policies {verb} (no auto_approve rows; relay_gated is escalate)\n"
    )
    out.write(
        f"GREEN  search_decision_history({HISTORY_TOOL_NAME!r}) "
        f"-> {len(history)} prior decisions\n"
    )
    for entry in history:
        out.write(f"       {entry.verdict} by {entry.decided_by}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    return run_seed()


if __name__ == "__main__":
    raise SystemExit(main())
