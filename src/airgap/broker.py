"""Blocking broker: park callers until an in-process verdict exists."""

from __future__ import annotations

import asyncio
import secrets
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from airgap.policy import PolicyRule, matches_tool
from airgap.protocol import (
    ArmCommand,
    BootEvent,
    ButtonEvent,
    DisarmCommand,
    Event,
    FlagCommand,
    IdAllocator,
    LcdCommand,
    LedCommand,
    RelayCommand,
    TickEvent,
    ToneCommand,
    decode,
)
from airgap.supervisor import Supervisor, SupervisorRejection
from airgap.vocab import (
    AuditEvent,
    DecidedBy,
    LedState,
    PolicyAction,
    TonePattern,
    Verdict,
)
from airgap.warden import DecisionHistoryEntry, TriageRequest, Warden

BIND_HOST = "127.0.0.1"
DEFAULT_EXPIRY_S = 30 * 60
RATE_LIMIT = 6
RATE_WINDOW_S = 60.0
ARM_DEAD_TIME_S = 2.0
SHORT_CODE_LEN = 4
_SHORT_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CSRF_COOKIE = "airgap_csrf"
_CSRF_HEADER = "x-csrf-token"
_AGENT_ONLY = frozenset({("POST", "/request_approval")})
_UI_READS = frozenset(
    {
        ("GET", "/pending"),
        ("GET", "/audit"),
        ("GET", "/policies"),
    }
)
_UI_WRITES = frozenset({("PUT", "/policies/{pattern}")})


@dataclass
class StoredRequest:
    """In-memory request row; production may persist the same fields."""

    id: str
    actor: str
    tool_name: str
    tool_args: dict[str, object]
    justification: str
    risk_class: str
    relay_gated: bool
    dwell_s: int
    created_at: float
    verdict: str | None = None
    decided_by: str | None = None
    reason: str | None = None
    short_code: str | None = None
    latency_ms: int | None = None
    event: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class RequestStore:
    """Seedable request collection so startup recovery is testable without Postgres."""

    _rows: dict[str, StoredRequest] = field(default_factory=dict)

    def put(self, row: StoredRequest) -> None:
        self._rows[row.id] = row

    def get(self, request_id: str) -> StoredRequest | None:
        return self._rows.get(request_id)

    def pending(self) -> list[StoredRequest]:
        return [row for row in self._rows.values() if row.verdict is None]

    def pending_count(self) -> int:
        return len(self.pending())

    def resolved(self) -> list[StoredRequest]:
        return [row for row in self._rows.values() if row.verdict is not None]


@dataclass
class _PolicyRow:
    tool_pattern: str
    min_dial: int
    action: PolicyAction
    relay_gated: bool
    dwell_s: int = 60


class ApprovalIn(BaseModel):
    actor: str
    tool_name: str
    tool_args: dict[str, object] = Field(default_factory=dict)
    justification: str


class ApprovalOut(BaseModel):
    request_id: str
    approved: bool
    verdict: str
    decided_by: str
    reason: str
    latency_ms: int


class PolicyUpdate(BaseModel):
    action: PolicyAction
    min_dial: int
    relay_gated: bool = False
    dwell_s: int = 60


class Broker:
    """Holds MCP/HTTP callers on an Event until a non-HTTP path decides."""

    def __init__(
        self,
        supervisor: Supervisor,
        warden: Warden,
        *,
        on_audit: Callable[[str, str | None, object], None],
        clock: Callable[[], float],
        policies: Sequence[PolicyRule] = (),
        dial: int = 0,
        origin_allowlist: Sequence[str] = (),
        expiry_s: float = DEFAULT_EXPIRY_S,
        store: RequestStore | None = None,
        csrf_secret: str | None = None,
        tokens: Mapping[str, str] | None = None,
    ) -> None:
        self.supervisor = supervisor
        self._warden = warden
        self._on_audit = on_audit
        self._clock = clock
        self._dial = dial
        self._origin_allowlist = tuple(origin_allowlist)
        self._expiry_s = expiry_s
        self.store = store or RequestStore()
        self.csrf_secret = csrf_secret or secrets.token_urlsafe(32)
        self.tokens_by_scope = (
            dict(tokens) if tokens is not None else _generate_tokens()
        )
        self._token_scopes = {
            token: scope for scope, token in self.tokens_by_scope.items()
        }
        self._policies = [
            _PolicyRow(
                tool_pattern=rule.tool_pattern,
                min_dial=rule.min_dial,
                action=rule.action,
                relay_gated=rule.relay_gated,
                dwell_s=60,
            )
            for rule in policies
        ]
        self._queue: deque[str] = deque()
        self._armed_id: str | None = None
        self._rates: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._ids = IdAllocator()
        self._lock = asyncio.Lock()
        self._arm_lock = asyncio.Lock()
        self._started = False
        self._hold_arm_until = 0.0
        self.audit_events: list[tuple[str, str | None, object]] = []
        self._install_supervisor_hooks()

    @property
    def armed_id(self) -> str | None:
        return self._armed_id

    def _install_supervisor_hooks(self) -> None:
        previous_resolve = self.supervisor._on_resolve
        previous_pending = self.supervisor._on_resolve_pending
        previous_audit = self.supervisor._on_audit

        def on_resolve(request_id: str, verdict: str, decided_by: str) -> None:
            self._apply_supervisor_resolve(request_id, verdict, decided_by)
            if previous_resolve is not None:
                previous_resolve(request_id, verdict, decided_by)

        def on_pending(request_id: str, verdict: str) -> None:
            self._apply_link_lost(request_id, verdict)
            if previous_pending is not None:
                previous_pending(request_id, verdict)

        def on_audit(event: str, request_id: str | None, payload: object) -> None:
            self._audit(event, request_id, payload)
            if previous_audit is not None:
                previous_audit(event, request_id, payload)

        self.supervisor._on_resolve = on_resolve
        self.supervisor._on_resolve_pending = on_pending
        self.supervisor._on_audit = on_audit

    def _audit(self, event: str, request_id: str | None, payload: object) -> None:
        name = str(event)
        self.audit_events.append((name, request_id, payload))
        self._on_audit(name, request_id, payload)

    async def startup(self) -> None:
        """Disarm, open the relay, and refuse any inherited pending rows."""
        if self._started:
            return
        self._started = True
        try:
            await self.supervisor.send(DisarmCommand(id=self._ids.next()))
        except SupervisorRejection:
            pass
        await self.supervisor.send(RelayCommand(id=self._ids.next(), closed=False))
        for row in list(self.store.pending()):
            self._finalize(
                row,
                Verdict.LINK_LOST,
                DecidedBy.SYSTEM,
                "broker restart",
                audit=True,
            )

    async def pump(self) -> None:
        """Advance watchdog, expiry, and FIFO arming from the injected clock."""
        await self.supervisor.check_watchdog()
        await self._expire_due()
        await self._try_arm_next()

    async def on_event(self, ev: Event) -> None:
        if isinstance(ev, BootEvent):
            await self._on_boot()
            return
        if isinstance(ev, ButtonEvent) and ev.which in {"deny", "never"}:
            await self._on_human_deny(ev)
            return
        if isinstance(ev, TickEvent):
            self._dial = ev.dial
        await self.supervisor.on_event(ev)

    async def feed_line(self, line: bytes) -> None:
        decoded = decode(line)
        if isinstance(decoded, (BootEvent, ButtonEvent, TickEvent)):
            await self.on_event(decoded)
            return
        await self.supervisor.feed_line(line)

    async def request_approval(self, body: ApprovalIn) -> ApprovalOut:
        async with self._lock:
            if self._rate_limited(body.tool_name, body.actor):
                raise HTTPException(status_code=429, detail="rate limited")
            self._note_rate(body.tool_name, body.actor)
            request_id = self._new_request_id()
            row = StoredRequest(
                id=request_id,
                actor=body.actor,
                tool_name=body.tool_name,
                tool_args=dict(body.tool_args),
                justification=body.justification,
                risk_class="medium",
                relay_gated=False,
                dwell_s=60,
                created_at=self._clock(),
            )
            self.store.put(row)

        self._audit(
            AuditEvent.REQUEST_CREATED,
            row.id,
            {
                "actor": row.actor,
                "tool_name": row.tool_name,
                "tool_args": row.tool_args,
            },
        )
        matched = self._match_policy(row.tool_name)
        triage = self._warden.triage(
            TriageRequest(
                request_id=row.id,
                actor=row.actor,
                tool_name=row.tool_name,
                tool_args=row.tool_args,
                justification=row.justification,
            ),
            dial=self._dial,
            policies=self._policy_rules(),
            history=self._history(),
        )
        row.risk_class = triage.assessment.risk_class
        if matched is not None:
            row.relay_gated = matched.relay_gated
            row.dwell_s = matched.dwell_s
        self._audit(
            AuditEvent.WARDEN_VERDICT,
            row.id,
            {
                "proposal": triage.proposal.value,
                "resolved": triage.resolved.value,
                "risk_class": triage.assessment.risk_class,
                "reasoning": triage.assessment.reasoning,
            },
        )
        if triage.proposal is not triage.resolved:
            self._audit(
                AuditEvent.POLICY_OVERRIDE,
                row.id,
                {"from": triage.proposal.value, "to": triage.resolved.value},
            )

        if triage.resolved is PolicyAction.BLOCK:
            self._finalize(
                row,
                Verdict.DENIED,
                DecidedBy.POLICY,
                triage.assessment.reasoning,
                audit=True,
            )
            return _approval_out(row)

        if triage.resolved is PolicyAction.AUTO_APPROVE:
            self._finalize(
                row,
                Verdict.APPROVED,
                DecidedBy.WARDEN_AUTO,
                triage.assessment.reasoning,
                audit=True,
            )
            await self.supervisor.send(
                LcdCommand(
                    id=self._ids.next(),
                    l1=row.tool_name,
                    l2="",
                )
            )
            return _approval_out(row)

        self.supervisor.track_pending(row.id)
        async with self._lock:
            self._queue.append(row.id)
        await self._try_arm_next()
        await row.event.wait()
        return _approval_out(row)

    async def drive_arm(self, request_id: str) -> None:
        """Mint a fresh short code and drive LCD/LED/flag/tone/arm for this id."""
        async with self._arm_lock:
            await self._drive_arm_locked(request_id)

    async def _drive_arm_locked(self, request_id: str) -> None:
        row = self.store.get(request_id)
        if row is None or row.verdict is not None:
            return
        first = self._armed_id != request_id
        if first:
            self.supervisor.arm(
                request_id,
                relay_gated=row.relay_gated,
                dwell_s=row.dwell_s,
            )
            self._armed_id = request_id
            if request_id in self._queue:
                self._queue.remove(request_id)
        row.short_code = _mint_short_code(row)
        self._audit(AuditEvent.ARMED, row.id, {"short_code": row.short_code})
        await self._send_armed_actuators(row)
        await self.supervisor.send(ArmCommand(id=self._ids.next(), req=request_id))

    async def _try_arm_next(self) -> None:
        async with self._arm_lock:
            if self._armed_id is not None:
                return
            if self._clock() < self._hold_arm_until:
                return
            while self._queue:
                request_id = self._queue[0]
                row = self.store.get(request_id)
                if row is None or row.verdict is not None:
                    self._queue.popleft()
                    continue
                try:
                    await self._drive_arm_locked(request_id)
                except SupervisorRejection:
                    return
                return

    async def _send_armed_actuators(self, row: StoredRequest) -> None:
        if row.risk_class == "high":
            lcd = LcdCommand(
                id=self._ids.next(),
                l1=row.short_code or "",
                l2="SEE READER",
            )
            led = LedState.RED
            tone_n = 3
        else:
            lcd = LcdCommand(
                id=self._ids.next(),
                l1=row.tool_name,
                l2=row.justification,
            )
            led = LedState.AMBER
            tone_n = 1
        await self.supervisor.send(lcd)
        await self.supervisor.send(LedCommand(id=self._ids.next(), state=led))
        await self.supervisor.send(FlagCommand(id=self._ids.next(), up=True))
        await self.supervisor.send(
            ToneCommand(id=self._ids.next(), pattern=TonePattern.ALERT, n=tone_n)
        )

    async def _expire_due(self) -> None:
        now = self._clock()
        armed = self._armed_id
        for row in list(self.store.pending()):
            if now - row.created_at < self._expiry_s:
                continue
            self._finalize(
                row,
                Verdict.EXPIRED,
                DecidedBy.SYSTEM,
                "request expired",
                audit=True,
            )
            if armed == row.id:
                await self._disarm_device()

    async def _on_boot(self) -> None:
        if self._armed_id is None:
            return
        row = self.store.get(self._armed_id)
        if row is None or row.verdict is not None:
            return
        self._finalize(
            row,
            Verdict.DENIED,
            DecidedBy.SYSTEM,
            "device_reset",
            audit=True,
        )
        await self._disarm_device()

    async def _on_human_deny(self, ev: ButtonEvent) -> None:
        if ev.req is None or ev.req != self._armed_id:
            return
        row = self.store.get(ev.req)
        if row is None or row.verdict is not None:
            return
        self._audit(AuditEvent.BUTTON, row.id, {"which": ev.which})
        reason = "user declined" if ev.which == "deny" else "never allow this tool"
        self._audit(
            AuditEvent.RESOLVED,
            row.id,
            {
                "verdict": Verdict.DENIED,
                "decided_by": DecidedBy.HUMAN,
                "reason": reason,
            },
        )
        self._finalize(
            row,
            Verdict.DENIED,
            DecidedBy.HUMAN,
            reason,
            audit=False,
        )
        if ev.which == "never":
            self._block_tool(row.tool_name)
        await self._disarm_device()

    def _apply_supervisor_resolve(
        self, request_id: str, verdict: str, decided_by: str
    ) -> None:
        row = self.store.get(request_id)
        if row is None or row.verdict is not None:
            return
        reason = "approved" if verdict == Verdict.APPROVED else str(verdict)
        self._finalize(row, verdict, decided_by, reason, audit=False)

    def _apply_link_lost(self, request_id: str, verdict: str) -> None:
        row = self.store.get(request_id)
        if row is None or row.verdict is not None:
            return
        self._finalize(
            row,
            verdict,
            DecidedBy.SYSTEM,
            "link lost",
            audit=True,
        )
        if self._armed_id == request_id:
            self._armed_id = None

    def _finalize(
        self,
        row: StoredRequest,
        verdict: str,
        decided_by: str,
        reason: str,
        *,
        audit: bool,
    ) -> None:
        if row.verdict is not None:
            return
        if audit:
            self._audit(
                AuditEvent.RESOLVED,
                row.id,
                {
                    "verdict": verdict,
                    "decided_by": decided_by,
                    "reason": reason,
                },
            )
        now = self._clock()
        row.verdict = str(verdict)
        row.decided_by = str(decided_by)
        row.reason = reason
        row.latency_ms = max(0, int((now - row.created_at) * 1000))
        if self._armed_id == row.id:
            self._armed_id = None
        if row.id in self._queue:
            self._queue.remove(row.id)
        row.event.set()

    async def _disarm_device(self) -> None:
        try:
            await self.supervisor.send(DisarmCommand(id=self._ids.next()))
        except SupervisorRejection:
            pass
        self.supervisor.disarm()
        self._armed_id = None
        self._hold_arm_until = self._clock() + ARM_DEAD_TIME_S

    def _rate_limited(self, tool_name: str, actor: str) -> bool:
        times = self._rates[(tool_name, actor)]
        self._prune_rate(times)
        return len(times) >= RATE_LIMIT

    def _note_rate(self, tool_name: str, actor: str) -> None:
        self._rates[(tool_name, actor)].append(self._clock())

    def _prune_rate(self, times: deque[float]) -> None:
        now = self._clock()
        while times and now - times[0] >= RATE_WINDOW_S:
            times.popleft()

    def _new_request_id(self) -> str:
        for _ in range(32):
            candidate = secrets.token_hex(4)
            if self.store.get(candidate) is None:
                return candidate
        raise RuntimeError("unable to allocate request id")

    def _match_policy(self, tool_name: str) -> _PolicyRow | None:
        for row in self._policies:
            if matches_tool(row.tool_pattern, tool_name):
                return row
        return None

    def _policy_rules(self) -> list[PolicyRule]:
        return [
            PolicyRule(
                tool_pattern=row.tool_pattern,
                min_dial=row.min_dial,
                action=row.action,
                relay_gated=row.relay_gated,
            )
            for row in self._policies
        ]

    def _history(self) -> list[DecisionHistoryEntry]:
        entries: list[DecisionHistoryEntry] = []
        for row in self.store.resolved():
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

    def _block_tool(self, tool_name: str) -> None:
        self._policies = [
            row for row in self._policies if row.tool_pattern != tool_name
        ]
        self._policies.append(
            _PolicyRow(
                tool_pattern=tool_name,
                min_dial=0,
                action=PolicyAction.BLOCK,
                relay_gated=False,
            )
        )

    def pending_payload(self) -> dict[str, object]:
        armed_row = self.store.get(self._armed_id) if self._armed_id else None
        if armed_row is not None and armed_row.verdict is not None:
            armed_row = None
        queue_rows = [
            row
            for request_id in self._queue
            if (row := self.store.get(request_id)) is not None and row.verdict is None
        ]
        return {
            "link": self.link_status(),
            "armed": _pending_item(armed_row) if armed_row is not None else None,
            "queue": [_pending_item(row, short_code=None) for row in queue_rows],
        }

    def policies_payload(self) -> dict[str, object]:
        return {
            "policies": [
                {
                    "tool_pattern": row.tool_pattern,
                    "min_dial": row.min_dial,
                    "action": row.action.value,
                    "relay_gated": row.relay_gated,
                    "dwell_s": row.dwell_s,
                }
                for row in self._policies
            ]
        }

    def upsert_policy(
        self, pattern: str, update: PolicyUpdate, *, updated_by: str
    ) -> None:
        del updated_by
        self._policies = [row for row in self._policies if row.tool_pattern != pattern]
        self._policies.append(
            _PolicyRow(
                tool_pattern=pattern,
                min_dial=update.min_dial,
                action=update.action,
                relay_gated=update.relay_gated,
                dwell_s=update.dwell_s,
            )
        )

    def audit_payload(self) -> dict[str, object]:
        return {
            "events": [
                {
                    "event": event,
                    "request_id": request_id,
                    "payload": payload,
                }
                for event, request_id, payload in self.audit_events
            ]
        }

    def health_payload(self) -> dict[str, object]:
        return {
            "ok": True,
            "link": self.link_status(),
            "pending": self.store.pending_count(),
        }

    def link_status(self) -> str:
        return "up" if self.supervisor.healthy else "down"

    def authenticate(self, request: Request) -> str:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing token")
        token = header.split(" ", 1)[1].strip()
        scope = self._token_scopes.get(token)
        if scope is None:
            raise HTTPException(status_code=401, detail="unknown token")
        return scope

    def authorize(self, request: Request, scope: str, method: str, path: str) -> None:
        allowed = _allowed_routes(scope)
        if (method, path) not in allowed:
            raise HTTPException(status_code=403, detail="scope denied")
        origin = request.headers.get("origin")
        if scope in {"agent", "ui_ro"} and origin is not None:
            raise HTTPException(status_code=403, detail="origin not allowed")
        if (
            scope == "ui"
            and origin is not None
            and origin not in self._origin_allowlist
        ):
            raise HTTPException(status_code=403, detail="origin not allowed")

    def require_csrf(self, request: Request) -> None:
        header = request.headers.get(_CSRF_HEADER)
        cookie = request.cookies.get(_CSRF_COOKIE)
        if header is None:
            raise HTTPException(status_code=403, detail="csrf required")
        if secrets.compare_digest(header, self.csrf_secret):
            return
        if cookie is not None and secrets.compare_digest(header, cookie):
            return
        raise HTTPException(status_code=403, detail="csrf required")


def _allowed_routes(scope: str) -> set[tuple[str, str]]:
    if scope == "agent":
        return set(_AGENT_ONLY)
    if scope == "ui":
        return set(_UI_READS | _UI_WRITES)
    if scope == "ui_ro":
        return set(_UI_READS)
    return set()


def _generate_tokens() -> dict[str, str]:
    return {
        "agent": secrets.token_urlsafe(32),
        "ui": secrets.token_urlsafe(32),
        "ui_ro": secrets.token_urlsafe(32),
    }


def _mint_short_code(row: StoredRequest) -> str:
    forbidden = [row.id, *(str(value) for value in row.tool_args.values())]
    for _ in range(64):
        code = "".join(
            secrets.choice(_SHORT_CODE_ALPHABET) for _ in range(SHORT_CODE_LEN)
        )
        lowered = code.lower()
        if any(item.lower().startswith(lowered) for item in forbidden if item):
            continue
        if lowered in row.id:
            continue
        return code
    return "".join(secrets.choice(_SHORT_CODE_ALPHABET) for _ in range(SHORT_CODE_LEN))


def _pending_item(
    row: StoredRequest, *, short_code: str | None | object = ...
) -> dict[str, object]:
    code: str | None
    if short_code is ...:
        code = row.short_code
    else:
        code = short_code if isinstance(short_code, str) else None
    return {
        "request_id": row.id,
        "actor": row.actor,
        "tool_name": row.tool_name,
        "tool_args": row.tool_args,
        "justification": row.justification,
        "risk_class": row.risk_class,
        "short_code": code,
        "relay_gated": row.relay_gated,
    }


def _approval_out(row: StoredRequest) -> ApprovalOut:
    verdict = row.verdict or Verdict.DENIED
    return ApprovalOut(
        request_id=row.id,
        approved=verdict == Verdict.APPROVED,
        verdict=verdict,
        decided_by=row.decided_by or DecidedBy.SYSTEM,
        reason=row.reason or "",
        latency_ms=row.latency_ms or 0,
    )


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return request.url.path


def create_app(
    *,
    supervisor: Supervisor,
    warden: Warden,
    on_audit: Callable[[str, str | None, object], None],
    clock: Callable[[], float],
    policies: Sequence[PolicyRule] = (),
    dial: int = 0,
    origin_allowlist: Sequence[str] = (),
    expiry_s: float = DEFAULT_EXPIRY_S,
    store: RequestStore | None = None,
    csrf_secret: str | None = None,
    tokens: Mapping[str, str] | None = None,
) -> FastAPI:
    """Build a FastAPI app with injected doubles; tokens are minted if omitted."""

    broker = Broker(
        supervisor,
        warden,
        on_audit=on_audit,
        clock=clock,
        policies=policies,
        dial=dial,
        origin_allowlist=origin_allowlist,
        expiry_s=expiry_s,
        store=store,
        csrf_secret=csrf_secret,
        tokens=tokens,
    )

    app = FastAPI(
        title="Airgap Broker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.broker = broker
    app.state.tokens = dict(broker.tokens_by_scope)
    app.state.csrf_secret = broker.csrf_secret
    app.state.bind_host = BIND_HOST

    def _require(request: Request, *scopes: str) -> str:
        scope = broker.authenticate(request)
        if scope not in scopes:
            raise HTTPException(status_code=403, detail="scope denied")
        broker.authorize(request, scope, request.method, _route_path(request))
        return scope

    @app.post("/request_approval")
    async def request_approval(request: Request, body: ApprovalIn) -> ApprovalOut:
        content_type = request.headers.get("content-type", "")
        if "application/json" not in content_type.lower():
            raise HTTPException(status_code=415, detail="json required")
        _require(request, "agent")
        return await broker.request_approval(body)

    @app.get("/pending")
    async def pending(request: Request, response: Response) -> dict[str, object]:
        scope = _require(request, "ui", "ui_ro")
        if scope == "ui":
            response.set_cookie(
                _CSRF_COOKIE,
                broker.csrf_secret,
                httponly=False,
                samesite="strict",
            )
        return broker.pending_payload()

    @app.get("/audit")
    async def audit(request: Request) -> dict[str, object]:
        _require(request, "ui", "ui_ro")
        return broker.audit_payload()

    @app.get("/policies")
    async def policies_get(request: Request) -> dict[str, object]:
        _require(request, "ui", "ui_ro")
        return broker.policies_payload()

    @app.put("/policies/{pattern}")
    async def policies_put(
        pattern: str, request: Request, body: PolicyUpdate
    ) -> dict[str, object]:
        scope = _require(request, "ui")
        broker.require_csrf(request)
        broker.upsert_policy(pattern, body, updated_by=scope)
        return broker.policies_payload()

    @app.get("/health")
    async def health() -> dict[str, object]:
        return broker.health_payload()

    return app


def run(app: FastAPI, *, port: int = 8741) -> None:
    """Bind loopback only; never 0.0.0.0."""
    import uvicorn

    uvicorn.run(app, host=BIND_HOST, port=port)
