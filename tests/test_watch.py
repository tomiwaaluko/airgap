"""airgap watch: full-fidelity reader over ui_ro, GET-only, no approve path."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from test_broker import (
    AGENT_BODY,
    Harness,
    _auth,
    _client,
    _harness,
    _human_deny,
    _post_approval,
    _rule,
    _startup,
    _wait_armed,
)

from airgap.cli import main
from airgap.vocab import PolicyAction
from airgap.watch import (
    KEYBINDINGS,
    POLL_INTERVAL_S,
    GetOnlyClient,
    MethodNotAllowed,
    ScopeRejected,
    Watcher,
)

HOSTILE_JUSTIFICATION = (
    "please approve\x1b[2J\x1b[H\x1b[0;0H\x1b[31mINJECT\rOVERWRITE\x07\x1b[3J"
)
COLLIDING_ARGS: dict[str, object] = {
    "drop": "users_production",
    "keep": "users_prod_bak",
}


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)


class _SpyTransport(httpx.AsyncBaseTransport):
    """Fail the test on the wire if the reader issues anything but GET."""

    def __init__(self, app: object) -> None:
        self._inner = ASGITransport(app=app)
        self.methods: list[str] = []
        self.origin_sent = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.methods.append(request.method)
        names = {key.lower() for key in request.headers}
        if "origin" in names:
            self.origin_sent = True
        if request.method.upper() != "GET":
            raise AssertionError(f"reader issued non-GET {request.method}")
        return await self._inner.handle_async_request(request)


async def _arm(
    harness: Harness,
    body: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], asyncio.Task[Any], AsyncClient]:
    await _startup(harness)
    agent_client = _client(harness)
    await agent_client.__aenter__()
    task = asyncio.create_task(_post_approval(agent_client, harness.agent, body))
    armed = await _wait_armed(harness, agent_client)
    return armed, task, agent_client


async def _finish(
    harness: Harness,
    armed: Mapping[str, object],
    task: asyncio.Task[Any],
    *clients: AsyncClient,
) -> None:
    await _human_deny(harness, str(armed["request_id"]))
    await task
    for client in clients:
        await client.aclose()


def _watcher(
    harness: Harness,
    *,
    token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Any = None,
) -> tuple[Watcher, AsyncClient]:
    inner = AsyncClient(
        transport=transport or ASGITransport(app=harness.app),
        base_url="http://127.0.0.1",
    )
    client = GetOnlyClient(inner, token=token or harness.ui_ro)
    watcher = Watcher(
        broker_url="http://127.0.0.1",
        token=token or harness.ui_ro,
        http_client=client,
        clock=harness.clock,
        sleep=sleep,
    )
    return watcher, inner


def test_short_code_in_view_matches_armed_per_arm_nonce() -> None:
    _run(_short_code_matches())


async def _short_code_matches() -> None:
    harness = _harness(risk_class="high")
    armed, task, agent = await _arm(harness)
    watcher, inner = _watcher(harness)
    try:
        snapshot = await watcher.tick()
        assert snapshot.short_code == armed["short_code"]
        assert snapshot.short_code
        assert str(snapshot.short_code) in snapshot.rendered
        assert snapshot.request_id == armed["request_id"]
    finally:
        await _finish(harness, armed, task, inner, agent)


def test_tool_args_complete_including_near_colliding_names() -> None:
    _run(_tool_args_complete())


async def _tool_args_complete() -> None:
    harness = _harness(risk_class="high")
    body = {**AGENT_BODY, "tool_args": COLLIDING_ARGS}
    armed, task, agent = await _arm(harness, body)
    watcher, inner = _watcher(harness)
    try:
        snapshot = await watcher.tick()
        compact = snapshot.rendered.replace("\n", "")
        assert "users_production" in snapshot.tool_args_text
        assert "users_prod_bak" in snapshot.tool_args_text
        assert "users_production" in compact
        assert "users_prod_bak" in compact
        assert snapshot.tool_args == COLLIDING_ARGS
    finally:
        await _finish(harness, armed, task, inner, agent)


def test_hostile_ansi_in_justification_cannot_hijack_terminal() -> None:
    _run(_hostile_justification())


async def _hostile_justification() -> None:
    harness = _harness(
        risk_class="high",
        policies=(_rule(PolicyAction.ESCALATE, pattern="db.drop_*"),),
    )
    body = {**AGENT_BODY, "justification": HOSTILE_JUSTIFICATION}
    armed, task, agent = await _arm(harness, body)
    watcher, inner = _watcher(harness)
    try:
        snapshot = await watcher.tick()
        rendered = snapshot.rendered
        assert "\x1b" not in rendered
        assert "\x1b" not in snapshot.justification
        assert "\r" not in rendered
        assert "\x07" not in rendered
        assert snapshot.actor == AGENT_BODY["actor"]
        assert snapshot.tool_name == AGENT_BODY["tool_name"]
        assert snapshot.short_code == armed["short_code"]
        assert snapshot.actor in rendered
        assert snapshot.tool_name in rendered
        assert str(snapshot.short_code) in rendered
    finally:
        await _finish(harness, armed, task, inner, agent)


def test_no_approve_keybinding() -> None:
    blob = " ".join(KEYBINDINGS) + " " + " ".join(KEYBINDINGS.values())
    lowered = blob.lower()
    assert "approve" not in lowered
    assert "deny" not in lowered
    assert "never" not in lowered
    assert POLL_INTERVAL_S == 1.0


def test_ui_ro_token_works_and_agent_token_is_rejected() -> None:
    _run(_scope_gate())


async def _scope_gate() -> None:
    harness = _harness(risk_class="high")
    armed, task, agent = await _arm(harness)
    ok, ok_inner = _watcher(harness, token=harness.ui_ro)
    bad, bad_inner = _watcher(harness, token=harness.agent)
    try:
        snapshot = await ok.tick()
        assert snapshot.short_code == armed["short_code"]
        with pytest.raises(ScopeRejected):
            await bad.tick()
    finally:
        await _finish(harness, armed, task, ok_inner, bad_inner, agent)


def test_reader_http_client_rejects_non_get() -> None:
    _run(_client_rejects_writes())


async def _client_rejects_writes() -> None:
    async with AsyncClient() as inner:
        client = GetOnlyClient(inner, token="ui-ro-secret")
        for method in ("post", "put", "patch", "delete"):
            with pytest.raises(MethodNotAllowed):
                await getattr(client, method)("http://127.0.0.1/pending")
        with pytest.raises(MethodNotAllowed):
            await client.request("POST", "http://127.0.0.1/pending")
        with pytest.raises(MethodNotAllowed):
            await client.request("PUT", "http://127.0.0.1/policies/x")


def test_full_reader_issues_only_get_and_never_sends_origin() -> None:
    _run(_reader_get_only_on_the_wire())


async def _reader_get_only_on_the_wire() -> None:
    harness = _harness(
        risk_class="high",
        policies=(_rule(PolicyAction.ESCALATE, pattern="db.drop_*"),),
    )
    armed, task, agent = await _arm(harness)
    spy = _SpyTransport(harness.app)
    slept: list[float] = []
    watcher, inner = _watcher(harness, transport=spy, sleep=slept.append)
    try:
        snapshot = await watcher.tick()
        assert spy.methods
        assert spy.methods == ["GET"] * len(spy.methods)
        assert not spy.origin_sent
        assert slept == []
        assert snapshot.link in {"up", "down"}
        assert snapshot.queue_depth >= 1
        assert snapshot.risk_class == "high"
        assert snapshot.reasoning
        assert snapshot.policy is not None
        assert snapshot.policy["tool_pattern"] == "db.drop_*"
        assert snapshot.dial is not None
        harness.clock.advance(9)
        later = await watcher.tick()
        assert later.elapsed_s is not None
        assert later.elapsed_s >= 9
    finally:
        await _finish(harness, armed, task, inner, agent)


def test_same_ui_ro_token_put_policies_is_403() -> None:
    _run(_reader_token_cannot_write_policy())


async def _reader_token_cannot_write_policy() -> None:
    harness = _harness()
    await _startup(harness)
    watcher, inner = _watcher(harness, token=harness.ui_ro)
    try:
        assert watcher.token == harness.ui_ro
        async with _client(harness) as write_client:
            response = await write_client.put(
                "/policies/db.drop_table",
                json={
                    "action": "auto_approve",
                    "min_dial": 0,
                    "relay_gated": False,
                },
                headers=_auth(watcher.token, **{"X-CSRF-Token": harness.csrf_secret}),
            )
        assert response.status_code == 403
    finally:
        await inner.aclose()


def test_cli_watch_requires_ui_ro_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIRGAP_UI_RO_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="usage: airgap watch"):
        main([])
    with pytest.raises(ValueError, match="AIRGAP_UI_RO_TOKEN"):
        main(["watch"])
