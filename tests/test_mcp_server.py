"""MCP request_approval: one blocking tool, no client timeout, both transports."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
import mcp
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from mcp import Client, ClientSession
from mcp.server.mcpserver import MCPServer
from test_broker import Harness, _harness, _rule, _startup

from airgap.mcp_server import (
    TOOL_DESCRIPTION,
    create_server,
    main,
    new_broker_client,
    transport_from_argv,
)
from airgap.vocab import PolicyAction

SPEC_DESCRIPTION = (
    "Request human approval for an irreversible action. Blocks until a human "
    "physically approves or denies. May take minutes. Call this BEFORE taking "
    "any action that cannot be undone."
)
TOOL_ARGS = {
    "tool_name": "db.drop_table",
    "tool_args": {"table": "users_backup"},
    "justification": "cleaning up staging",
}


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)


def _text(result: object) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    return " ".join(parts)


@asynccontextmanager
async def _mcp_against_http(
    app: FastAPI,
    *,
    agent_token: str,
    actor: str = "mcp/test-session",
) -> AsyncIterator[Client]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://127.0.0.1",
        timeout=None,
    ) as http:
        server = create_server(
            broker_url="http://127.0.0.1",
            agent_token=agent_token,
            actor=actor,
            http_client=http,
        )
        async with Client(server) as client:
            yield client


@asynccontextmanager
async def _mcp_against_broker(harness: Harness) -> AsyncIterator[Client]:
    await _startup(harness)
    async with _mcp_against_http(harness.app, agent_token=harness.agent) as client:
        yield client


def _stub_broker(
    *,
    approved: bool,
    verdict: str,
    reason: str,
    hold: asyncio.Event | None = None,
    seen: dict[str, object] | None = None,
    started: asyncio.Event | None = None,
) -> FastAPI:
    app = FastAPI()
    captured = seen if seen is not None else {}

    @app.post("/request_approval")
    async def request_approval(request: Request) -> dict[str, object]:
        captured["authorization"] = request.headers.get("authorization")
        captured["origin"] = request.headers.get("origin")
        captured["body"] = await request.json()
        if started is not None:
            started.set()
        if hold is not None:
            await hold.wait()
        return {
            "request_id": "a91f3c2e",
            "approved": approved,
            "verdict": verdict,
            "decided_by": "system",
            "reason": reason,
            "latency_ms": 1,
        }

    return app


def test_list_tools_matches_spec_exactly() -> None:
    _run(_list_tools_matches_spec())


async def _list_tools_matches_spec() -> None:
    assert TOOL_DESCRIPTION == SPEC_DESCRIPTION
    server = create_server(agent_token="agent-token", broker_url="http://127.0.0.1")
    async with Client(server) as client:
        listed = await client.list_tools()
    assert len(listed.tools) == 1
    tool = listed.tools[0]
    assert tool.name == "request_approval"
    assert tool.description == SPEC_DESCRIPTION
    schema = tool.input_schema
    assert schema["type"] == "object"
    assert schema["required"] == ["tool_name", "justification"]
    properties = cast(dict[str, dict[str, object]], schema["properties"])
    assert set(properties) == {"tool_name", "tool_args", "justification"}
    assert properties["tool_name"]["type"] == "string"
    assert properties["tool_name"]["description"] == (
        "The action you intend to take, e.g. 'db.drop_table'"
    )
    assert properties["tool_args"]["type"] == "object"
    assert properties["tool_args"]["description"] == (
        "The exact arguments you intend to use"
    )
    assert properties["justification"]["type"] == "string"
    assert properties["justification"]["description"] == (
        "Why you believe this action is warranted"
    )


def test_in_process_client_returns_approved_text() -> None:
    _run(_approved_text())


async def _approved_text() -> None:
    harness = _harness(
        action="auto_approve",
        risk_class="low",
        policies=(_rule(PolicyAction.AUTO_APPROVE),),
    )
    async with _mcp_against_broker(harness) as client:
        result = await client.call_tool("request_approval", TOOL_ARGS)
    text = _text(result)
    assert text.startswith("APPROVED: ")
    assert "DENIED:" not in text


def test_in_process_client_returns_denied_text() -> None:
    _run(_denied_text())


async def _denied_text() -> None:
    harness = _harness(
        action="escalate",
        policies=(_rule(PolicyAction.BLOCK),),
    )
    async with _mcp_against_broker(harness) as client:
        result = await client.call_tool("request_approval", TOOL_ARGS)
    text = _text(result)
    assert text.startswith("DENIED: ")
    assert not text.startswith("APPROVED:")


def test_expired_and_link_lost_map_to_denied_with_verdict() -> None:
    _run(_system_verdicts_are_denied())


async def _system_verdicts_are_denied() -> None:
    for verdict, reason in (
        ("expired", "request expired"),
        ("link_lost", "link lost"),
    ):
        app = _stub_broker(approved=False, verdict=verdict, reason=reason)
        async with _mcp_against_http(app, agent_token="agent-token") as client:
            result = await client.call_tool("request_approval", TOOL_ARGS)
        text = _text(result)
        assert text.startswith("DENIED:")
        assert verdict in text


def test_call_holds_until_broker_event_then_returns() -> None:
    _run(_hold_open_until_event())


async def _hold_open_until_event() -> None:
    hold = asyncio.Event()
    started = asyncio.Event()
    app = _stub_broker(
        approved=True,
        verdict="approved",
        reason="human pressed APPROVE",
        hold=hold,
        started=started,
    )
    async with _mcp_against_http(app, agent_token="agent-token") as client:
        task = asyncio.create_task(client.call_tool("request_approval", TOOL_ARGS))
        await started.wait()
        assert not task.done()
        hold.set()
        result = await task
    assert _text(result) == "APPROVED: human pressed APPROVE"


def test_posts_actor_and_agent_bearer_to_broker() -> None:
    _run(_actor_and_agent_token())


async def _actor_and_agent_token() -> None:
    seen: dict[str, object] = {}
    app = _stub_broker(
        approved=True,
        verdict="approved",
        reason="ok",
        seen=seen,
    )
    async with _mcp_against_http(
        app, agent_token="agent-secret", actor="mcp/session-4f2a"
    ) as client:
        await client.call_tool("request_approval", TOOL_ARGS)
    assert seen["authorization"] == "Bearer agent-secret"
    assert seen["origin"] is None
    body = cast(dict[str, object], seen["body"])
    assert body["actor"] == "mcp/session-4f2a"
    assert body["tool_name"] == "db.drop_table"
    assert body["tool_args"] == {"table": "users_backup"}
    assert body["justification"] == "cleaning up staging"


def test_new_broker_client_has_no_timeout() -> None:
    client = new_broker_client()
    try:
        assert client.timeout == httpx.Timeout(None)
        assert client.timeout.connect is None
        assert client.timeout.read is None
        assert client.timeout.write is None
        assert client.timeout.pool is None
    finally:
        asyncio.run(client.aclose())
    source = inspect.getsource(new_broker_client)
    assert "timeout=None" in source


def test_mcp_sdk_client_timeout_defaults_are_none() -> None:
    assert (
        inspect.signature(Client.call_tool).parameters["read_timeout_seconds"].default
        is None
    )
    assert (
        inspect.signature(ClientSession.call_tool)
        .parameters["read_timeout_seconds"]
        .default
        is None
    )
    assert (
        inspect.signature(mcp.Client.__init__).parameters.get("read_timeout_seconds")
        is None
        or inspect.signature(mcp.Client.__init__)
        .parameters["read_timeout_seconds"]
        .default
        is None
    )


def test_default_transport_is_stdio() -> None:
    kind, kwargs = transport_from_argv([])
    assert kind == "stdio"
    assert kwargs == {}


def test_http_flag_selects_streamable_http_on_loopback() -> None:
    kind, kwargs = transport_from_argv(["--http", "8792"])
    assert kind == "streamable-http"
    assert kwargs == {"host": "127.0.0.1", "port": 8792}


def test_both_transports_are_constructible() -> None:
    server = create_server(agent_token="agent-token", broker_url="http://127.0.0.1")
    assert isinstance(server, MCPServer)
    http_app = server.streamable_http_app(host="127.0.0.1")
    assert http_app is not None


def test_main_stdio_and_http_select_transports(monkeypatch: Any) -> None:
    seen: dict[str, object] = {}

    def fake_run(
        self: MCPServer,
        transport: str = "stdio",
        **kwargs: object,
    ) -> None:
        del self
        seen["transport"] = transport
        seen["kwargs"] = kwargs

    monkeypatch.setattr(MCPServer, "run", fake_run)
    monkeypatch.setenv("AIRGAP_AGENT_TOKEN", "agent-secret")
    monkeypatch.setenv("BROKER_URL", "http://127.0.0.1:8741")

    main([])
    assert seen == {"transport": "stdio", "kwargs": {}}

    main(["--http", "8792"])
    assert seen["transport"] == "streamable-http"
    kwargs = cast(Mapping[str, object], seen["kwargs"])
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8792
