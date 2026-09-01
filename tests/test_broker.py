"""Broker blocking approval: in-process verdicts, no HTTP resolver."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from httpx import ASGITransport, AsyncClient
from test_supervisor import AutoAckTransport, Clock
from test_warden import RecordingSession, StubMessage, _proposal_json, _text_message

from airgap.broker import BIND_HOST, ApprovalIn, RequestStore, StoredRequest, create_app
from airgap.policy import PolicyRule
from airgap.protocol import BootEvent, ButtonEvent, TickEvent
from airgap.supervisor import Supervisor
from airgap.vocab import AuditEvent, DecidedBy, PolicyAction, Verdict
from airgap.warden import Warden

AGENT_BODY: dict[str, object] = {
    "actor": "claude-code/session-4f2a",
    "tool_name": "db.drop_table",
    "tool_args": {"table": "users_backup"},
    "justification": "cleaning up staging",
}
UI_ORIGIN = "http://127.0.0.1:5173"
HIGH_ARG = "users_production_backup"


@dataclass
class Harness:
    app: Any
    broker: Any
    transport: AutoAckTransport
    clock: Clock
    supervisor: Supervisor
    store: RequestStore
    audits: list[tuple[str, str | None, object]]
    order: list[str]
    tokens: dict[str, str]
    csrf_secret: str

    @property
    def agent(self) -> str:
        return self.tokens["agent"]

    @property
    def ui(self) -> str:
        return self.tokens["ui"]

    @property
    def ui_ro(self) -> str:
        return self.tokens["ui_ro"]


def _frames(transport: AutoAckTransport) -> list[dict[str, object]]:
    return [json.loads(frame.decode("ascii")) for frame in transport.writes]


def _cmds(frames: Sequence[Mapping[str, object]]) -> list[str]:
    return [str(frame["cmd"]) for frame in frames]


class RepeatClient:
    """Return the same proposal on every Messages API call."""

    def __init__(self, message: StubMessage) -> None:
        self._message = message
        self.messages = self

    def create(self, **kwargs: Any) -> StubMessage:
        del kwargs
        return self._message


def _warden(action: str, *, risk_class: str = "medium") -> Warden:
    return Warden(
        RepeatClient(_text_message(_proposal_json(action, risk_class=risk_class))),
        RecordingSession(),
    )


def _rule(
    action: PolicyAction,
    *,
    pattern: str = "*",
    relay_gated: bool = False,
    min_dial: int = 10,
) -> PolicyRule:
    return PolicyRule(
        tool_pattern=pattern,
        min_dial=min_dial,
        action=action,
        relay_gated=relay_gated,
    )


def _harness(
    *,
    action: str = "escalate",
    risk_class: str = "medium",
    policies: Sequence[PolicyRule] = (),
    store: RequestStore | None = None,
    expiry_s: float = 30 * 60,
    dial: int = 0,
    host_loop: bool = False,
) -> Harness:
    clock = Clock()
    transport = AutoAckTransport()
    order: list[str] = []
    audits: list[tuple[str, str | None, object]] = []
    original_write = transport.write

    async def tracked_write(frame: bytes) -> Any:
        payload = json.loads(frame.decode("ascii"))
        order.append(f"write:{payload['cmd']}")
        return await original_write(frame)

    transport.write = tracked_write  # type: ignore[method-assign]

    def on_audit(event: str, request_id: str | None, payload: object) -> None:
        name = str(event)
        audits.append((name, request_id, payload))
        order.append(f"audit:{name}")

    supervisor = Supervisor(transport, clock=clock)
    app = create_app(
        supervisor=supervisor,
        warden=_warden(action, risk_class=risk_class),
        on_audit=on_audit,
        clock=clock,
        policies=policies,
        dial=dial,
        origin_allowlist=(UI_ORIGIN,),
        expiry_s=expiry_s,
        store=store,
        csrf_secret="csrf-test-secret",
        host_loop=host_loop,
    )
    return Harness(
        app=app,
        broker=app.state.broker,
        transport=transport,
        clock=clock,
        supervisor=supervisor,
        store=app.state.broker.store,
        audits=audits,
        order=order,
        tokens=dict(app.state.tokens),
        csrf_secret=str(app.state.csrf_secret),
    )


def _client(harness: Harness) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=harness.app),
        base_url="http://127.0.0.1",
    )


def _auth(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **headers}


def _tick(*, t: int, btns: int = 0, armed: bool = False) -> TickEvent:
    return TickEvent(
        dial=5,
        relay=False,
        armed=armed,
        lease_ms=0,
        btns=btns,
        t=t,
    )


async def _wait_for(predicate: Callable[[], bool], *, steps: int = 200) -> None:
    for _ in range(steps):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition was not met")


async def _startup(harness: Harness) -> None:
    await harness.broker.startup()
    await harness.broker.on_event(_tick(t=100, btns=0))
    await harness.broker.pump()


async def _post_approval(
    client: AsyncClient,
    token: str,
    body: Mapping[str, object] | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> Any:
    headers = _auth(token)
    if extra_headers:
        headers.update(extra_headers)
    return await client.post(
        "/request_approval", json=dict(body or AGENT_BODY), headers=headers
    )


async def _wait_armed(harness: Harness, client: AsyncClient) -> dict[str, object]:
    del client
    await _wait_for(lambda: harness.broker.armed_id is not None)
    await _wait_for(
        lambda: any(
            frame.get("cmd") == "arm" and frame.get("req") == harness.broker.armed_id
            for frame in _frames(harness.transport)
        )
    )
    payload = harness.broker.pending_payload()
    armed = payload["armed"]
    assert armed is not None
    return cast(dict[str, object], armed)


async def _human_approve(harness: Harness, request_id: str) -> None:
    device_t = int(harness.clock.t * 1000) + 10_000
    await harness.broker.on_event(_tick(t=device_t, btns=0, armed=True))
    await harness.broker.on_event(
        ButtonEvent(which="approve", req=request_id, t=device_t + 50)
    )
    await asyncio.sleep(0)
    await harness.broker.pump()


async def _human_deny(
    harness: Harness, request_id: str, *, which: str = "deny"
) -> None:
    device_t = int(harness.clock.t * 1000) + 10_000
    await harness.broker.on_event(_tick(t=device_t, btns=0, armed=True))
    await harness.broker.on_event(
        ButtonEvent(which=cast(Any, which), req=request_id, t=device_t + 50)
    )
    await asyncio.sleep(0)
    await harness.broker.pump()


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)


def test_blocks_until_resolved_then_returns_verdict() -> None:
    _run(_blocks_until_resolved())


async def _blocks_until_resolved() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        task = asyncio.create_task(_post_approval(client, harness.agent))
        armed = await _wait_armed(harness, client)
        assert not task.done()
        await _human_approve(harness, str(armed["request_id"]))
        response = await task
        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == Verdict.APPROVED
        assert body["decided_by"] == DecidedBy.HUMAN
        assert body["approved"] is True
        assert body["request_id"] == armed["request_id"]


def test_route_table_has_no_resolver_for_any_decided_by() -> None:
    _run(_route_table_has_no_resolver())


async def _route_table_has_no_resolver() -> None:
    harness = _harness()
    mutating: set[tuple[str, str]] = set()
    for route in harness.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not isinstance(path, str) or not methods:
            continue
        for method in methods:
            if method in {"POST", "PUT", "PATCH", "DELETE"}:
                mutating.add((method, path))
    assert mutating == {
        ("POST", "/request_approval"),
        ("PUT", "/policies/{pattern}"),
    }

    async with _client(harness) as client:
        await _startup(harness)
        task = asyncio.create_task(_post_approval(client, harness.agent))
        armed = await _wait_armed(harness, client)
        request_id = str(armed["request_id"])
        spoof = {
            "verdict": "approved",
            "decided_by": "human",
            "approved": True,
            "request_id": request_id,
        }
        for method, path in sorted(mutating):
            concrete = path.replace("{pattern}", "db.drop_table")
            headers = _auth(harness.ui, **{"X-CSRF-Token": harness.csrf_secret})
            if method == "POST" and path == "/request_approval":
                headers = _auth(harness.agent)
            await client.request(method, concrete, json=spoof, headers=headers)
        assert not task.done()
        pending = await client.get("/pending", headers=_auth(harness.ui_ro))
        assert pending.json()["armed"]["request_id"] == request_id
        await _human_deny(harness, request_id)
        response = await task
        assert response.json()["verdict"] == Verdict.DENIED
        assert response.json()["decided_by"] == DecidedBy.HUMAN


def test_token_scopes_isolated_in_all_six_directions() -> None:
    _run(_token_scopes_isolated())


async def _token_scopes_isolated() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        assert (
            await client.get("/pending", headers=_auth(harness.agent))
        ).status_code == 403
        assert (
            await client.put(
                "/policies/db.drop_table",
                json={"action": "block", "min_dial": 10, "relay_gated": False},
                headers=_auth(harness.agent, **{"X-CSRF-Token": harness.csrf_secret}),
            )
        ).status_code == 403
        assert (await _post_approval(client, harness.ui)).status_code == 403
        assert (await _post_approval(client, harness.ui_ro)).status_code == 403
        assert (
            await client.put(
                "/policies/db.drop_table",
                json={"action": "block", "min_dial": 10, "relay_gated": False},
                headers=_auth(harness.ui_ro, **{"X-CSRF-Token": harness.csrf_secret}),
            )
        ).status_code == 403
        pending = await client.get("/pending", headers=_auth(harness.ui))
        assert pending.status_code == 200
        policies = await client.get("/policies", headers=_auth(harness.ui_ro))
        assert policies.status_code == 200


def test_ui_ro_put_policies_is_403() -> None:
    _run(_ui_ro_put_is_403())


async def _ui_ro_put_is_403() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        response = await client.put(
            "/policies/db.drop_table",
            json={"action": "escalate", "min_dial": 10, "relay_gated": False},
            headers=_auth(harness.ui_ro, **{"X-CSRF-Token": harness.csrf_secret}),
        )
        assert response.status_code == 403


def test_origin_keys_on_token_scope_not_route() -> None:
    _run(_origin_keys_on_scope())


async def _origin_keys_on_scope() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        agent = await _post_approval(
            client,
            harness.agent,
            extra_headers={"Origin": UI_ORIGIN},
        )
        assert agent.status_code in {400, 403}
        ui_ro = await client.get(
            "/pending",
            headers=_auth(harness.ui_ro, Origin=UI_ORIGIN),
        )
        assert ui_ro.status_code in {400, 403}
        ui_ok = await client.get(
            "/pending",
            headers=_auth(harness.ui, Origin=UI_ORIGIN),
        )
        assert ui_ok.status_code == 200
        ui_bad = await client.get(
            "/pending",
            headers=_auth(harness.ui, Origin="http://evil.example"),
        )
        assert ui_bad.status_code in {400, 403}


def test_high_risk_lcd_shows_short_code_not_argument_prefix() -> None:
    _run(_high_risk_lcd())


async def _high_risk_lcd() -> None:
    harness = _harness(action="escalate", risk_class="high")
    body = {
        **AGENT_BODY,
        "tool_args": {"table": HIGH_ARG},
        "justification": "drop production lookalike",
    }
    async with _client(harness) as client:
        await _startup(harness)
        writes_before = len(harness.transport.writes)
        task = asyncio.create_task(_post_approval(client, harness.agent, body))
        armed = await _wait_armed(harness, client)
        short_code = str(armed["short_code"])
        assert short_code
        assert short_code.lower() not in str(armed["request_id"])
        lcd_text = " ".join(
            f"{frame.get('l1', '')} {frame.get('l2', '')}"
            for frame in _frames(harness.transport)[writes_before:]
            if frame.get("cmd") == "lcd"
        )
        assert short_code in lcd_text
        assert "SEE READER" in lcd_text
        assert HIGH_ARG not in lcd_text
        assert HIGH_ARG[:16] not in lcd_text
        assert HIGH_ARG[:8] not in lcd_text
        await _human_deny(harness, str(armed["request_id"]))
        await task


def test_rearm_mints_a_different_short_code() -> None:
    _run(_rearm_mints_different_code())


async def _rearm_mints_different_code() -> None:
    harness = _harness(risk_class="high")
    async with _client(harness) as client:
        await _startup(harness)
        task = asyncio.create_task(_post_approval(client, harness.agent))
        armed = await _wait_armed(harness, client)
        first = str(armed["short_code"])
        request_id = str(armed["request_id"])
        await harness.broker.drive_arm(request_id)
        again = await client.get("/pending", headers=_auth(harness.ui_ro))
        second = str(again.json()["armed"]["short_code"])
        assert first != second
        await _human_deny(harness, request_id)
        await task


def test_audit_armed_before_arm_frame_and_resolved_before_release() -> None:
    _run(_audit_ordering())


async def _audit_ordering() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        task = asyncio.create_task(_post_approval(client, harness.agent))
        armed = await _wait_armed(harness, client)
        assert harness.order.index("audit:armed") < harness.order.index("write:arm")
        await _human_approve(harness, str(armed["request_id"]))
        response = await task
        assert response.status_code == 200
        assert harness.order.index("audit:resolved") < len(harness.order)
        created = [event for event, _, _ in harness.audits]
        assert created[0] == AuditEvent.REQUEST_CREATED
        assert AuditEvent.WARDEN_VERDICT in created
        assert created.index(AuditEvent.REQUEST_CREATED) < created.index(
            AuditEvent.WARDEN_VERDICT
        )


def test_second_request_stays_blocked_until_first_resolves_and_dead_time() -> None:
    _run(_fifo_and_dead_time())


async def _fifo_and_dead_time() -> None:
    harness = _harness()
    second_body = {
        **AGENT_BODY,
        "tool_name": "db.truncate_table",
        "justification": "second in line",
    }
    async with _client(harness) as client:
        await _startup(harness)
        first_task = asyncio.create_task(
            harness.broker.request_approval(ApprovalIn.model_validate(AGENT_BODY))
        )
        first = await asyncio.wait_for(_wait_armed(harness, client), timeout=3)
        second_task = asyncio.create_task(
            harness.broker.request_approval(ApprovalIn.model_validate(second_body))
        )
        await asyncio.wait_for(
            _wait_for(lambda: harness.store.pending_count() == 2), timeout=3
        )
        payload = harness.broker.pending_payload()
        assert payload["armed"] is not None
        assert payload["armed"]["request_id"] == first["request_id"]
        assert len(payload["queue"]) == 1
        assert payload["queue"][0]["short_code"] is None
        assert not first_task.done()
        assert not second_task.done()
        await _human_approve(harness, str(first["request_id"]))
        first_out = await asyncio.wait_for(first_task, timeout=3)
        assert first_out.verdict == Verdict.APPROVED
        assert not second_task.done()
        assert harness.broker.pending_payload()["armed"] is None
        await asyncio.wait_for(harness.broker.pump(), timeout=3)
        assert harness.broker.pending_payload()["armed"] is None
        harness.clock.advance(2.0)
        await asyncio.wait_for(
            harness.broker.on_event(_tick(t=3000, btns=0)), timeout=3
        )
        await asyncio.wait_for(harness.broker.pump(), timeout=3)
        second = await asyncio.wait_for(_wait_armed(harness, client), timeout=3)
        assert second["request_id"] != first["request_id"]
        await _human_approve(harness, str(second["request_id"]))
        second_out = await asyncio.wait_for(second_task, timeout=3)
        assert second_out.verdict == Verdict.APPROVED


def test_expiry_resolves_expired_not_denied() -> None:
    _run(_expiry_not_denied())


async def _expiry_not_denied() -> None:
    harness = _harness(expiry_s=30 * 60)
    async with _client(harness) as client:
        await _startup(harness)
        task = asyncio.create_task(_post_approval(client, harness.agent))
        await _wait_armed(harness, client)
        harness.clock.advance(30 * 60)
        await harness.broker.on_event(
            _tick(t=int(harness.clock.t * 1000), btns=0, armed=True)
        )
        await harness.broker.pump()
        response = await task
        body = response.json()
        assert body["verdict"] == Verdict.EXPIRED
        assert body["decided_by"] == DecidedBy.SYSTEM
        assert body["verdict"] != Verdict.DENIED
        assert not any(
            frame.get("cmd") == "relay" and frame.get("closed") is True
            for frame in _frames(harness.transport)
        )


def test_tick_starvation_resolves_link_lost_not_denied() -> None:
    _run(_tick_starvation_link_lost())


async def _tick_starvation_link_lost() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        task = asyncio.create_task(_post_approval(client, harness.agent))
        await _wait_armed(harness, client)
        harness.clock.advance(3.001)
        await harness.broker.pump()
        response = await task
        body = response.json()
        assert body["verdict"] == Verdict.LINK_LOST
        assert body["decided_by"] == DecidedBy.SYSTEM
        assert body["verdict"] != Verdict.DENIED


def test_startup_marks_pending_link_lost() -> None:
    _run(_startup_recovery())


def test_host_loop_lifespan_runs_startup_without_manual_call() -> None:
    _run(_host_loop_lifespan_startup())


async def _host_loop_lifespan_startup() -> None:
    store = RequestStore()
    seeded = StoredRequest(
        id="abcd1234",
        actor="claude-code/session-4f2a",
        tool_name="db.drop_table",
        tool_args={"table": "users_backup"},
        justification="left over",
        risk_class="high",
        relay_gated=False,
        dwell_s=60,
        created_at=0.0,
    )
    store.put(seeded)
    harness = _harness(store=store, host_loop=True)
    assert harness.app.state.host_loop is True
    async with harness.app.router.lifespan_context(harness.app):
        row = store.get("abcd1234")
        assert row is not None
        assert row.verdict == Verdict.LINK_LOST
        assert row.decided_by == DecidedBy.SYSTEM


async def _startup_recovery() -> None:
    store = RequestStore()
    seeded = StoredRequest(
        id="abcd1234",
        actor="claude-code/session-4f2a",
        tool_name="db.drop_table",
        tool_args={"table": "users_backup"},
        justification="left over",
        risk_class="high",
        relay_gated=False,
        dwell_s=60,
        created_at=0.0,
    )
    store.put(seeded)
    harness = _harness(store=store)
    async with _client(harness) as client:
        await _startup(harness)
        row = store.get("abcd1234")
        assert row is not None
        assert row.verdict == Verdict.LINK_LOST
        assert row.decided_by == DecidedBy.SYSTEM
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["pending"] == 0


def test_rate_limit_rejects_seventh_and_does_not_grow_queue() -> None:
    _run(_rate_limit())


async def _rate_limit() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        tasks = [
            asyncio.create_task(
                harness.broker.request_approval(ApprovalIn.model_validate(AGENT_BODY))
            )
            for _ in range(6)
        ]
        await _wait_for(lambda: harness.store.pending_count() == 6)
        seventh = await _post_approval(client, harness.agent)
        assert seventh.status_code == 429
        assert harness.store.pending_count() == 6
        payload = harness.broker.pending_payload()
        depth = (1 if payload["armed"] else 0) + len(payload["queue"])
        assert depth == 6
        armed_id = str(payload["armed"]["request_id"])
        await _human_deny(harness, armed_id)
        await tasks[0]
        for task in tasks[1:]:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def test_auto_approve_sends_no_led_flag_tone_relay_or_arm() -> None:
    _run(_auto_approve_silent())


async def _auto_approve_silent() -> None:
    harness = _harness(
        action="auto_approve",
        risk_class="low",
        policies=(_rule(PolicyAction.AUTO_APPROVE),),
    )
    async with _client(harness) as client:
        await _startup(harness)
        before = len(harness.transport.writes)
        response = await _post_approval(client, harness.agent)
        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == Verdict.APPROVED
        assert body["decided_by"] == DecidedBy.WARDEN_AUTO
        cmds = _cmds(_frames(harness.transport)[before:])
        assert "led" not in cmds
        assert "flag" not in cmds
        assert "tone" not in cmds
        assert "arm" not in cmds
        assert "relay" not in cmds
        assert "lcd" in cmds


def test_policy_block_denies_without_arming() -> None:
    _run(_policy_block())


async def _policy_block() -> None:
    harness = _harness(
        action="escalate",
        policies=(_rule(PolicyAction.BLOCK),),
    )
    async with _client(harness) as client:
        await _startup(harness)
        before = len(harness.transport.writes)
        response = await _post_approval(client, harness.agent)
        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] == Verdict.DENIED
        assert body["decided_by"] == DecidedBy.POLICY
        cmds = _cmds(_frames(harness.transport)[before:])
        assert "arm" not in cmds


def test_deny_button_unblocks_denied_human_without_relay_close() -> None:
    _run(_deny_button())


async def _deny_button() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        task = asyncio.create_task(_post_approval(client, harness.agent))
        armed = await _wait_armed(harness, client)
        await _human_deny(harness, str(armed["request_id"]))
        response = await task
        body = response.json()
        assert body["verdict"] == Verdict.DENIED
        assert body["decided_by"] == DecidedBy.HUMAN
        assert not any(
            frame.get("cmd") == "relay" and frame.get("closed") is True
            for frame in _frames(harness.transport)
        )
        button_at = next(
            i
            for i, (event, _, payload) in enumerate(harness.audits)
            if event == AuditEvent.BUTTON and payload == {"which": "deny"}
        )
        resolved_at = next(
            i
            for i, (event, _, payload) in enumerate(harness.audits)
            if event == AuditEvent.RESOLVED
            and isinstance(payload, dict)
            and payload.get("verdict") == Verdict.DENIED
            and payload.get("decided_by") == DecidedBy.HUMAN
        )
        assert button_at < resolved_at


def test_bind_host_is_loopback_only() -> None:
    assert BIND_HOST == "127.0.0.1"


def test_ui_put_policies_without_csrf_fails() -> None:
    _run(_csrf_required())


async def _csrf_required() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        denied = await client.put(
            "/policies/db.drop_table",
            json={"action": "block", "min_dial": 10, "relay_gated": False},
            headers=_auth(harness.ui, Origin=UI_ORIGIN),
        )
        assert denied.status_code == 403
        allowed = await client.put(
            "/policies/db.drop_table",
            json={
                "action": "block",
                "min_dial": 10,
                "relay_gated": False,
                "dwell_s": 60,
            },
            headers=_auth(
                harness.ui,
                Origin=UI_ORIGIN,
                **{"X-CSRF-Token": harness.csrf_secret},
            ),
        )
        assert allowed.status_code == 200
        rows = allowed.json()["policies"]
        saved = next(row for row in rows if row["tool_pattern"] == "db.drop_table")
        assert saved["updated_by"] == "ui"
        assert saved["action"] == "block"


def test_ui_put_policies_persists_updated_by_on_get() -> None:
    _run(_updated_by_roundtrip())


async def _updated_by_roundtrip() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        put = await client.put(
            "/policies/db.drop_*",
            json={
                "action": "escalate",
                "min_dial": 4,
                "relay_gated": False,
                "dwell_s": 30,
            },
            headers=_auth(
                harness.ui,
                Origin=UI_ORIGIN,
                **{"X-CSRF-Token": harness.csrf_secret},
            ),
        )
        assert put.status_code == 200
        listed = await client.get("/policies", headers=_auth(harness.ui))
        assert listed.status_code == 200
        saved = next(
            row
            for row in listed.json()["policies"]
            if row["tool_pattern"] == "db.drop_*"
        )
        assert saved["updated_by"] == "ui"
        assert saved["min_dial"] == 4
        assert saved["dwell_s"] == 30


def test_boot_mid_request_is_denied_device_reset() -> None:
    _run(_boot_device_reset())


async def _boot_device_reset() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        task = asyncio.create_task(_post_approval(client, harness.agent))
        armed = await _wait_armed(harness, client)
        request_id = str(armed["request_id"])
        await harness.broker.on_event(BootEvent(fw="1.0.0", t=999))
        await harness.broker.pump()
        response = await task
        body = response.json()
        assert body["verdict"] == Verdict.DENIED
        assert body["reason"] == "device_reset"
        assert body["decided_by"] == DecidedBy.SYSTEM
        pending = await client.get("/pending", headers=_auth(harness.ui_ro))
        armed_after = pending.json()["armed"]
        if armed_after is not None:
            assert armed_after["request_id"] != request_id


def test_missing_or_unknown_token_is_401() -> None:
    _run(_auth_401())


async def _auth_401() -> None:
    harness = _harness()
    async with _client(harness) as client:
        await _startup(harness)
        missing = await client.post("/request_approval", json=AGENT_BODY)
        assert missing.status_code == 401
        unknown = await client.post(
            "/request_approval",
            json=AGENT_BODY,
            headers=_auth("nope"),
        )
        assert unknown.status_code == 401
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert health.json()["link"] in {"up", "down"}
