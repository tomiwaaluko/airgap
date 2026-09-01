"""End-to-end chain: MCP client through broker to a scripted MockTransport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Sequence
from hashlib import sha256
from typing import Any

from test_broker import (
    UI_ORIGIN,
    Harness,
    _auth,
    _client,
    _human_approve,
    _human_deny,
    _post_approval,
    _rule,
    _startup,
    _wait_armed,
    _warden,
)
from test_mcp_server import TOOL_ARGS, _mcp_against_http, _text
from test_supervisor import Clock
from test_watch import COLLIDING_ARGS, _watcher

from airgap.broker import create_app
from airgap.policy import PolicyRule
from airgap.protocol import BootEvent, ButtonEvent, LeaseExpiredEvent, TickEvent
from airgap.supervisor import Supervisor
from airgap.transport import MockTransport
from airgap.vocab import AuditEvent, DecidedBy, PolicyAction, Verdict

GENESIS_HASH = "0" * 64
MISMATCHED_REQ = "deadbeef"
SPEC_CONSENT_APPROVE: tuple[str, ...] = (
    AuditEvent.REQUEST_CREATED,
    AuditEvent.WARDEN_VERDICT,
    AuditEvent.ARMED,
    AuditEvent.BUTTON,
    AuditEvent.RESOLVED,
)
SPEC_GATED_APPROVE: tuple[str, ...] = (
    *SPEC_CONSENT_APPROVE,
    AuditEvent.RELAY_CLOSED,
    AuditEvent.RELAY_OPENED,
)
DEFAULT_AUDIT_PREFIX: tuple[str, ...] = (
    AuditEvent.REQUEST_CREATED,
    AuditEvent.WARDEN_VERDICT,
)
T1_ARGS = {
    "tool_name": "db.drop_table",
    "tool_args": COLLIDING_ARGS,
    "justification": "drop production lookalike",
}


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)


def _frames(transport: MockTransport) -> list[dict[str, object]]:
    return [json.loads(frame.decode("ascii")) for frame in transport.writes]


def _cmds(frames: list[dict[str, object]]) -> list[str]:
    return [str(frame["cmd"]) for frame in frames]


def _relay_closes(frames: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        frame
        for frame in frames
        if frame.get("cmd") == "relay" and frame.get("closed") is True
    ]


def _queue_ack(transport: MockTransport, frame: bytes) -> None:
    """Script the matching ack MockTransport.write requires, or it AckTimeouts."""
    payload = json.loads(frame.decode("ascii"))
    command_id = int(payload["id"])
    cmd = payload.get("cmd")
    closed = bool(getattr(transport, "contact_closed", False))
    if cmd == "relay_renew" and not closed:
        ack: dict[str, object] = {
            "id": command_id,
            "ok": False,
            "err": "not_closed",
        }
    else:
        ack = {"id": command_id, "ok": True}
    transport._script.appendleft((json.dumps(ack) + "\n").encode("ascii"))


def _e2e_harness(
    *,
    action: str = "escalate",
    risk_class: str = "medium",
    policies: tuple[PolicyRule, ...] = (),
    expiry_s: float = 30 * 60,
    dial: int = 0,
    host_loop: bool = False,
) -> Harness:
    clock = Clock()
    transport = MockTransport()
    transport.contact_closed = False
    order: list[str] = []
    audits: list[tuple[str, str | None, object]] = []
    chain_rows: list[tuple[int, str, str | None, str, str, str]] = []
    previous_hash = GENESIS_HASH
    original_write = transport.write

    async def tracked_write(frame: bytes) -> Any:
        payload = json.loads(frame.decode("ascii"))
        cmd = str(payload["cmd"])
        if cmd == "relay":
            order.append(f"write:relay:{str(payload['closed']).lower()}")
        else:
            order.append(f"write:{cmd}")
        _queue_ack(transport, frame)
        ack = await original_write(frame)
        if cmd == "relay":
            transport.contact_closed = bool(payload["closed"])
        return ack

    transport.write = tracked_write  # type: ignore[method-assign]

    def on_audit(event: str, request_id: str | None, payload: object) -> None:
        nonlocal previous_hash
        name = str(event)
        seq = len(chain_rows) + 1
        canonical = _canonical(payload)
        digest = _row_hash(previous_hash, seq, name, request_id, canonical)
        chain_rows.append((seq, name, request_id, canonical, previous_hash, digest))
        previous_hash = digest
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
        csrf_secret="csrf-test-secret",
        host_loop=host_loop,
    )
    harness = Harness(
        app=app,
        broker=app.state.broker,
        transport=transport,  # type: ignore[arg-type]
        clock=clock,
        supervisor=supervisor,
        store=app.state.broker.store,
        audits=audits,
        order=order,
        tokens=dict(app.state.tokens),
        csrf_secret=str(app.state.csrf_secret),
    )
    harness.chain_rows = chain_rows  # type: ignore[attr-defined]
    return harness


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _row_hash(
    previous: str,
    seq: int,
    event: str,
    request_id: str | None,
    canonical: str,
) -> str:
    material = previous + str(seq) + event + (request_id or "") + canonical
    return sha256(material.encode("utf-8")).hexdigest()


def _verify_sealed_chain(
    rows: list[tuple[int, str, str | None, str, str, str]],
) -> None:
    """Check hashes sealed at append time; do not rematerialize from the live list."""
    previous = GENESIS_HASH
    for seq, event, request_id, canonical, stored_prev, stored_hash in rows:
        assert stored_prev == previous
        assert stored_hash == _row_hash(
            stored_prev, seq, event, request_id, canonical
        )
        previous = stored_hash


def _assert_expected_events(
    names: list[str],
    expected: Sequence[str],
    *,
    exact: bool,
) -> None:
    if exact:
        assert names == list(expected)
        return
    cursor = iter(names)
    for want in expected:
        for got in cursor:
            if got == want:
                break
        else:
            raise AssertionError(f"audit sequence missing {want!r} in {names}")


def _require_precedes(order: list[str], audit_name: str, write_name: str) -> None:
    assert write_name in order
    assert audit_name in order, f"{audit_name} missing but {write_name} is present"
    assert order.index(audit_name) < order.index(write_name)


def _assert_log_before_act(order: list[str]) -> None:
    if "write:arm" in order:
        _require_precedes(order, "audit:armed", "write:arm")
    if "write:relay:true" in order:
        _require_precedes(order, "audit:resolved", "write:relay:true")
    if "audit:relay_opened" in order:
        later_disarm = next(
            (
                index
                for index, item in enumerate(order)
                if item == "write:disarm"
                and index > order.index("audit:relay_opened")
            ),
            None,
        )
        if later_disarm is not None:
            assert order.index("audit:relay_opened") < later_disarm
    _require_precedes(order, "audit:request_created", "audit:warden_verdict")


def _assert_audit(
    harness: Harness,
    *,
    expected: Sequence[str] = DEFAULT_AUDIT_PREFIX,
    exact: bool = False,
) -> None:
    """Empty chain fails; a device write requires the audit that precedes it."""
    chain_rows = getattr(harness, "chain_rows", [])
    assert harness.audits, "audit list is empty"
    assert chain_rows, "sealed audit chain is empty"
    live_names = [event for event, _, _ in harness.audits]
    sealed_names = [row[1] for row in chain_rows]
    assert live_names == sealed_names
    _verify_sealed_chain(chain_rows)
    _assert_expected_events(live_names, expected, exact=exact)
    _assert_log_before_act(harness.order)


def _lcd_text(frames: list[dict[str, object]]) -> str:
    return " ".join(
        f"{frame.get('l1', '')} {frame.get('l2', '')}"
        for frame in frames
        if frame.get("cmd") == "lcd"
    )


async def _advance(
    harness: Harness,
    seconds: float,
    *,
    relay: bool = False,
    armed: bool = True,
) -> None:
    harness.clock.advance(seconds)
    await harness.broker.on_event(
        TickEvent(
            dial=5,
            relay=relay,
            armed=armed,
            lease_ms=10_000 if relay else 0,
            btns=0,
            t=int(harness.clock.t * 1000),
        )
    )
    await harness.broker.pump()


def test_consent_channel_happy_path() -> None:
    """DROP TABLE is a veto, not a contact; the first scenario is the common case."""
    _run(_consent_channel_happy_path())


async def _consent_channel_happy_path() -> None:
    harness = _e2e_harness()
    assert type(harness.transport) is MockTransport
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        before = len(harness.transport.writes)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        assert not task.done()
        await _human_approve(harness, str(armed["request_id"]))
        result = await task
    text = _text(result)
    assert text.startswith("APPROVED: ")
    assert "DENIED:" not in text
    frames = _frames(harness.transport)[before:]
    cmds = _cmds(frames)
    assert "relay" not in cmds
    assert "relay_renew" not in cmds
    assert _relay_closes(_frames(harness.transport)) == []
    row = harness.store.get(str(armed["request_id"]))
    assert row is not None
    assert row.verdict == Verdict.APPROVED
    assert row.decided_by == DecidedBy.HUMAN
    _assert_audit(harness, expected=SPEC_CONSENT_APPROVE, exact=True)


def test_deny_path_never_closes_relay() -> None:
    _run(_deny_path_never_closes_relay())


async def _deny_path_never_closes_relay() -> None:
    harness = _e2e_harness()
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        await _human_deny(harness, str(armed["request_id"]))
        result = await task
    text = _text(result)
    assert text.startswith("DENIED: ")
    assert not text.startswith("APPROVED:")
    assert _relay_closes(_frames(harness.transport)) == []
    assert harness.transport.contact_closed is False
    row = harness.store.get(str(armed["request_id"]))
    assert row is not None
    assert row.verdict == Verdict.DENIED
    assert row.decided_by == DecidedBy.HUMAN
    _assert_audit(harness, expected=SPEC_CONSENT_APPROVE, exact=True)


def test_policy_block_overrides_warden_auto_approve() -> None:
    _run(_policy_block_overrides_warden_auto_approve())


async def _policy_block_overrides_warden_auto_approve() -> None:
    harness = _e2e_harness(
        action="auto_approve",
        risk_class="low",
        policies=(_rule(PolicyAction.BLOCK),),
    )
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        before = len(harness.transport.writes)
        result = await client.call_tool("request_approval", TOOL_ARGS)
    text = _text(result)
    assert text.startswith("DENIED: ")
    cmds = _cmds(_frames(harness.transport)[before:])
    assert "arm" not in cmds
    assert "relay" not in cmds
    override = [
        payload
        for event, _, payload in harness.audits
        if event == AuditEvent.POLICY_OVERRIDE and isinstance(payload, dict)
    ]
    assert override
    assert override[0]["from"] == PolicyAction.AUTO_APPROVE
    assert override[0]["to"] == PolicyAction.BLOCK
    rows = list(harness.store.resolved())
    assert rows[0].verdict == Verdict.DENIED
    assert rows[0].decided_by == DecidedBy.POLICY
    _assert_audit(harness)


def test_empty_policy_table_escalates_everything() -> None:
    _run(_empty_policy_table_escalates_everything())


async def _empty_policy_table_escalates_everything() -> None:
    harness = _e2e_harness(action="auto_approve", risk_class="low", policies=())
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        assert not task.done()
        warden = next(
            payload
            for event, _, payload in harness.audits
            if event == AuditEvent.WARDEN_VERDICT and isinstance(payload, dict)
        )
        assert warden["proposal"] == PolicyAction.AUTO_APPROVE
        assert warden["resolved"] == PolicyAction.ESCALATE
        await _human_approve(harness, str(armed["request_id"]))
        result = await task
    assert _text(result).startswith("APPROVED: ")
    row = harness.store.get(str(armed["request_id"]))
    assert row is not None
    assert row.decided_by == DecidedBy.HUMAN
    assert row.decided_by != DecidedBy.WARDEN_AUTO
    _assert_audit(harness)


def test_relay_gated_policy_auto_approve_escalates_without_press() -> None:
    _run(_relay_gated_policy_auto_approve_escalates_without_press())


async def _relay_gated_policy_auto_approve_escalates_without_press() -> None:
    harness = _e2e_harness(
        action="auto_approve",
        risk_class="low",
        policies=(_rule(PolicyAction.AUTO_APPROVE, relay_gated=True),),
    )
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        assert not task.done()
        assert _relay_closes(_frames(harness.transport)) == []
        assert harness.transport.contact_closed is False
        await _human_deny(harness, str(armed["request_id"]))
        result = await task
    assert _text(result).startswith("DENIED: ")
    assert _relay_closes(_frames(harness.transport)) == []
    _assert_audit(harness)


def test_no_http_route_resolves_a_request() -> None:
    _run(_no_http_route_resolves_a_request())


async def _no_http_route_resolves_a_request() -> None:
    harness = _e2e_harness()
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
    assert not any("decide" in path for _, path in mutating)
    assert not any("approve" in path.lower() for _, path in mutating)

    async with _client(harness) as http:
        await _startup(harness)
        task = asyncio.create_task(_post_approval(http, harness.agent))
        armed = await _wait_armed(harness, http)
        request_id = str(armed["request_id"])
        spoof = {
            "verdict": "approved",
            "decided_by": "human",
            "approved": True,
            "request_id": request_id,
        }
        for decided_by in ("human", "policy", "warden_auto", "system"):
            spoof["decided_by"] = decided_by
            for path in (
                "/decide",
                "/approve",
                f"/request_approval/{request_id}/decide",
                f"/requests/{request_id}/decide",
            ):
                response = await http.post(
                    path, json=spoof, headers=_auth(harness.agent)
                )
                assert response.status_code in {404, 405, 403, 401}
        assert not task.done()
        assert harness.store.pending_count() == 1
        pending = await http.get("/pending", headers=_auth(harness.ui_ro))
        assert pending.json()["armed"]["request_id"] == request_id
        assert pending.json()["queue"] == []
        assert (
            await http.get("/pending", headers=_auth(harness.agent))
        ).status_code == 403
        assert (await _post_approval(http, harness.ui)).status_code == 403
        assert (await _post_approval(http, harness.ui_ro)).status_code == 403
        assert harness.store.pending_count() == 1
        assert (
            await http.put(
                "/policies/db.drop_table",
                json={"action": "block", "min_dial": 10, "relay_gated": False},
                headers=_auth(harness.ui_ro, **{"X-CSRF-Token": harness.csrf_secret}),
            )
        ).status_code == 403
        await _human_deny(harness, request_id)
        response = await task
        body = response.json()
        assert body["verdict"] == Verdict.DENIED
        assert body["decided_by"] == DecidedBy.HUMAN
    _assert_audit(harness, expected=SPEC_CONSENT_APPROVE, exact=True)


def test_broker_killed_link_up_lease_expires() -> None:
    _run(_broker_killed_link_up_lease_expires())


async def _broker_killed_link_up_lease_expires() -> None:
    harness = _e2e_harness(
        policies=(_rule(PolicyAction.ESCALATE, relay_gated=True),),
    )
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        await _human_approve(harness, str(armed["request_id"]))
        result = await task
        assert _text(result).startswith("APPROVED: ")
        assert harness.transport.contact_closed is True
        writes_at_kill = len(harness.transport.writes)
        assert harness.transport.connected
        harness.clock.advance(10.0)
        await harness.broker.on_event(
            LeaseExpiredEvent(t=int(harness.clock.t * 1000))
        )
    assert harness.transport.connected
    after_kill = _frames(harness.transport)[writes_at_kill:]
    assert not any(
        frame.get("cmd") == "relay" and frame.get("closed") is False
        for frame in after_kill
    )
    assert not any(frame.get("cmd") == "relay_renew" for frame in after_kill)
    lease_audits = [
        payload
        for event, _, payload in harness.audits
        if event == AuditEvent.LEASE_EXPIRED
    ]
    assert lease_audits
    row = harness.store.get(str(armed["request_id"]))
    assert row is not None
    assert row.verdict == Verdict.APPROVED
    _assert_audit(
        harness,
        expected=(
            *SPEC_CONSENT_APPROVE,
            AuditEvent.RELAY_CLOSED,
            AuditEvent.LEASE_EXPIRED,
        ),
        exact=True,
    )


def test_relay_gated_happy_path_cycle() -> None:
    _run(_relay_gated_happy_path_cycle())


async def _relay_gated_happy_path_cycle() -> None:
    harness = _e2e_harness(
        policies=(_rule(PolicyAction.ESCALATE, relay_gated=True),),
    )
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        await _human_approve(harness, str(armed["request_id"]))
        result = await asyncio.wait_for(task, timeout=3)
        assert _text(result).startswith("APPROVED: ")
        assert harness.transport.contact_closed is True
        assert _relay_closes(_frames(harness.transport))
        await _advance(harness, 3.0, relay=True)
        await _advance(harness, 3.0, relay=True)
        await _advance(harness, 4.0, relay=True)
        cmds = _cmds(_frames(harness.transport))
        assert cmds.count("relay_renew") >= 2
        assert harness.transport.contact_closed is True
        await _advance(harness, 50.0, relay=True)
    assert harness.transport.contact_closed is False
    frames = _frames(harness.transport)
    assert any(
        frame.get("cmd") == "relay" and frame.get("closed") is False
        for frame in frames
    )
    assert "disarm" in _cmds(frames)
    opened = [
        event for event, _, _ in harness.audits if event == AuditEvent.RELAY_OPENED
    ]
    assert opened
    row = harness.store.get(str(armed["request_id"]))
    assert row is not None
    assert row.verdict == Verdict.APPROVED
    assert row.decided_by == DecidedBy.HUMAN
    _assert_audit(harness, expected=SPEC_GATED_APPROVE, exact=True)


def test_empty_policy_table_warden_block_still_blocks() -> None:
    _run(_empty_policy_table_warden_block_still_blocks())


async def _empty_policy_table_warden_block_still_blocks() -> None:
    harness = _e2e_harness(action="block", policies=())
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        result = await client.call_tool("request_approval", TOOL_ARGS)
    assert _text(result).startswith("DENIED: ")
    warden = next(
        payload
        for event, _, payload in harness.audits
        if event == AuditEvent.WARDEN_VERDICT and isinstance(payload, dict)
    )
    assert warden["proposal"] == PolicyAction.BLOCK
    assert warden["resolved"] == PolicyAction.BLOCK
    rows = list(harness.store.resolved())
    assert rows[0].verdict == Verdict.DENIED
    assert rows[0].decided_by == DecidedBy.POLICY
    assert _relay_closes(_frames(harness.transport)) == []
    _assert_audit(harness)


def test_relay_gated_row_auto_approve_resolves_escalate() -> None:
    _run(_relay_gated_row_auto_approve_resolves_escalate())


async def _relay_gated_row_auto_approve_resolves_escalate() -> None:
    harness = _e2e_harness(
        action="auto_approve",
        policies=(_rule(PolicyAction.AUTO_APPROVE, relay_gated=True),),
    )
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        warden = next(
            payload
            for event, _, payload in harness.audits
            if event == AuditEvent.WARDEN_VERDICT and isinstance(payload, dict)
        )
        assert warden["proposal"] == PolicyAction.AUTO_APPROVE
        assert warden["resolved"] == PolicyAction.ESCALATE
        override = [
            payload
            for event, _, payload in harness.audits
            if event == AuditEvent.POLICY_OVERRIDE and isinstance(payload, dict)
        ]
        assert override
        assert override[0]["to"] == PolicyAction.ESCALATE
        await _human_deny(harness, str(armed["request_id"]))
        await task
    _assert_audit(harness)


def test_device_reset_mid_request_is_denied() -> None:
    _run(_device_reset_mid_request_is_denied())


async def _device_reset_mid_request_is_denied() -> None:
    harness = _e2e_harness()
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        await harness.broker.on_event(BootEvent(fw="1.0.0", t=999))
        await harness.broker.pump()
        result = await task
    text = _text(result)
    assert text.startswith("DENIED: ")
    assert "APPROVED" not in text
    row = harness.store.get(str(armed["request_id"]))
    assert row is not None
    assert row.verdict == Verdict.DENIED
    assert row.reason == "device_reset"
    assert row.decided_by == DecidedBy.SYSTEM
    assert row.verdict != Verdict.APPROVED
    assert _relay_closes(_frames(harness.transport)) == []
    _assert_audit(harness)


def test_link_loss_mid_request_is_link_lost() -> None:
    _run(_link_loss_mid_request_is_link_lost())


async def _link_loss_mid_request_is_link_lost() -> None:
    harness = _e2e_harness()
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        harness.clock.advance(3.001)
        await harness.broker.pump()
        result = await task
    text = _text(result)
    assert "link_lost" in text
    assert text.startswith("DENIED:")
    row = harness.store.get(str(armed["request_id"]))
    assert row is not None
    assert row.verdict == Verdict.LINK_LOST
    assert row.verdict != Verdict.DENIED
    assert row.decided_by == DecidedBy.SYSTEM
    assert harness.transport.contact_closed is False
    assert _relay_closes(_frames(harness.transport)) == []
    _assert_audit(harness)


def test_high_risk_readability_t1() -> None:
    _run(_high_risk_readability_t1())


async def _high_risk_readability_t1() -> None:
    harness = _e2e_harness(action="escalate", risk_class="high")
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        before = len(harness.transport.writes)
        task = asyncio.create_task(client.call_tool("request_approval", T1_ARGS))
        armed = await _wait_armed(harness, client)
        short_code = str(armed["short_code"])
        lcd = _lcd_text(_frames(harness.transport)[before:])
        assert short_code in lcd
        assert "SEE READER" in lcd
        for ident in ("users_production", "users_prod_bak"):
            assert ident not in lcd
            assert ident[:16] not in lcd
            assert ident[:8] not in lcd
        watcher, inner = _watcher(harness)
        try:
            snapshot = await watcher.tick()
            assert snapshot.short_code == short_code
            assert "users_production" in snapshot.tool_args_text
            assert "users_prod_bak" in snapshot.tool_args_text
            assert snapshot.tool_args == COLLIDING_ARGS
        finally:
            await inner.aclose()
        await _human_deny(harness, str(armed["request_id"]))
        await task
    _assert_audit(harness, expected=SPEC_CONSENT_APPROVE, exact=True)


def test_rearming_mints_fresh_short_code() -> None:
    _run(_rearming_mints_fresh_short_code())


async def _rearming_mints_fresh_short_code() -> None:
    harness = _e2e_harness(risk_class="high")
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", T1_ARGS))
        armed = await _wait_armed(harness, client)
        first = str(armed["short_code"])
        request_id = str(armed["request_id"])
        watcher, inner = _watcher(harness)
        try:
            stale = await watcher.tick()
            assert stale.short_code == first
            await harness.broker.drive_arm(request_id)
            live = await watcher.tick()
            second = str(live.short_code)
            assert first != second
            assert live.short_code != stale.short_code
            assert live.request_id == request_id
            async with _client(harness) as http:
                pending = await http.get(
                    "/pending", headers=_auth(harness.ui_ro)
                )
                assert pending.json()["armed"]["short_code"] == second
        finally:
            await inner.aclose()
        await _human_deny(harness, request_id)
        await task
    _assert_audit(harness)


def test_mismatched_req_button_does_not_close_relay() -> None:
    _run(_mismatched_req_button_does_not_close_relay())


async def _mismatched_req_button_does_not_close_relay() -> None:
    harness = _e2e_harness(
        policies=(_rule(PolicyAction.ESCALATE, relay_gated=True),),
    )
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        armed = await _wait_armed(harness, client)
        request_id = str(armed["request_id"])
        device_t = int(harness.clock.t * 1000) + 10_000
        await harness.broker.on_event(
            TickEvent(
                dial=5,
                relay=False,
                armed=True,
                lease_ms=0,
                btns=0,
                t=device_t,
            )
        )
        await harness.broker.on_event(
            ButtonEvent(which="approve", req=MISMATCHED_REQ, t=device_t + 50)
        )
        await asyncio.sleep(0)
        await harness.broker.pump()
        assert not task.done()
        assert _relay_closes(_frames(harness.transport)) == []
        assert harness.transport.contact_closed is False
        row = harness.store.get(request_id)
        assert row is not None
        assert row.verdict is None
        await _human_deny(harness, request_id)
        result = await task
    assert _text(result).startswith("DENIED: ")
    _assert_audit(harness, expected=SPEC_CONSENT_APPROVE, exact=True)


def test_second_press_in_dead_time_binds_to_nothing() -> None:
    _run(_second_press_in_dead_time_binds_to_nothing())


async def _second_press_in_dead_time_binds_to_nothing() -> None:
    harness = _e2e_harness()
    second_args = {
        "tool_name": "db.truncate_table",
        "tool_args": {"table": "users_backup"},
        "justification": "second in line",
    }
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        await _startup(harness)
        first_task = asyncio.create_task(
            client.call_tool("request_approval", TOOL_ARGS)
        )
        first = await _wait_armed(harness, client)
        second_task = asyncio.create_task(
            client.call_tool("request_approval", second_args)
        )
        await asyncio.sleep(0)
        await harness.broker.pump()
        assert harness.store.pending_count() == 2
        await _human_approve(harness, str(first["request_id"]))
        first_result = await first_task
        assert _text(first_result).startswith("APPROVED: ")
        assert not second_task.done()
        pending = [row for row in harness.store.pending()]
        assert len(pending) == 1
        second_id = pending[0].id
        device_t = int(harness.clock.t * 1000) + 50
        await harness.broker.on_event(
            ButtonEvent(which="approve", req=second_id, t=device_t)
        )
        await asyncio.sleep(0)
        await harness.broker.pump()
        assert not second_task.done()
        second_row = harness.store.get(second_id)
        assert second_row is not None
        assert second_row.verdict is None
        assert harness.broker.armed_id is None
        await _advance(harness, 2.0, armed=False)
        second_armed = await _wait_armed(harness, client)
        assert second_armed["request_id"] == second_id
        await _human_deny(harness, second_id)
        second_result = await second_task
    assert _text(second_result).startswith("DENIED: ")
    _assert_audit(harness)
