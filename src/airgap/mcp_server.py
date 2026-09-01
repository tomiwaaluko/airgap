"""MCP face of Airgap: one blocking tool, talking to the broker over HTTP."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, cast

import httpx
from mcp.server.mcpserver import MCPServer
from pydantic import Field

# Verbatim from spec/03 — this is what tells an actor to call before acting.
TOOL_DESCRIPTION = (
    "Request human approval for an irreversible action. Blocks until a human "
    "physically approves or denies. May take minutes. Call this BEFORE taking "
    "any action that cannot be undone."
)
BIND_HOST = "127.0.0.1"
DEFAULT_BROKER_URL = "http://127.0.0.1:8741"
_TOOL_NAME_DESC = "The action you intend to take, e.g. 'db.drop_table'"
_TOOL_ARGS_DESC = "The exact arguments you intend to use"
_JUSTIFICATION_DESC = "Why you believe this action is warranted"
_EMPTY_TOOL_ARGS: dict[str, object] = {}


def new_broker_client(**kwargs: Any) -> httpx.AsyncClient:
    """The gate has no client timeout; a finite one here would drop a waiting human."""
    kwargs.pop("timeout", None)
    return httpx.AsyncClient(**kwargs, timeout=None)


def transport_from_argv(
    argv: Sequence[str],
) -> tuple[Literal["stdio", "streamable-http"], dict[str, object]]:
    """Reuse the spike's --http PORT switch; stdio stays the default."""
    args = list(argv)
    if "--http" not in args:
        return "stdio", {}
    index = args.index("--http")
    if index + 1 >= len(args):
        raise SystemExit("usage: mcp_server.py [--http PORT]")
    return "streamable-http", {"host": BIND_HOST, "port": int(args[index + 1])}


def create_server(
    *,
    broker_url: str | None = None,
    agent_token: str | None = None,
    actor: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> MCPServer:
    """Build the public MCP process. It never imports the serial transport."""
    resolved_url = broker_url or os.environ.get("BROKER_URL", DEFAULT_BROKER_URL)
    resolved_token = agent_token or os.environ.get("AIRGAP_AGENT_TOKEN", "")
    if not resolved_token:
        raise ValueError("AIRGAP_AGENT_TOKEN is required")
    resolved_actor = actor or f"mcp/{uuid.uuid4().hex[:8]}"
    server = MCPServer("airgap")

    @server.tool(description=TOOL_DESCRIPTION, structured_output=False)
    async def request_approval(
        tool_name: Annotated[str, Field(description=_TOOL_NAME_DESC)],
        justification: Annotated[str, Field(description=_JUSTIFICATION_DESC)],
        tool_args: Annotated[
            dict[str, object], Field(description=_TOOL_ARGS_DESC)
        ] = _EMPTY_TOOL_ARGS,
    ) -> str:
        return await _call_broker(
            broker_url=resolved_url,
            agent_token=resolved_token,
            actor=resolved_actor,
            tool_name=tool_name,
            tool_args=dict(tool_args),
            justification=justification,
            http_client=http_client,
        )

    return server


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    kind, kwargs = transport_from_argv(args)
    server = create_server()
    if kind == "stdio":
        server.run("stdio")
        return
    server.run(
        "streamable-http",
        host=str(kwargs["host"]),
        port=int(cast(int, kwargs["port"])),
    )


def _format_verdict(data: Mapping[str, object]) -> str:
    reason = str(data.get("reason") or "(no reason given)")
    if data.get("approved") is True:
        return f"APPROVED: {reason}"
    verdict = str(data.get("verdict") or "denied")
    if verdict not in {"denied", "approved"}:
        return f"DENIED: {verdict}: {reason}"
    return f"DENIED: {reason}"


async def _call_broker(
    *,
    broker_url: str,
    agent_token: str,
    actor: str,
    tool_name: str,
    tool_args: dict[str, object],
    justification: str,
    http_client: httpx.AsyncClient | None,
) -> str:
    owns_client = http_client is None
    client = http_client or new_broker_client()
    try:
        response = await client.post(
            f"{broker_url.rstrip('/')}/request_approval",
            json={
                "actor": actor,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "justification": justification,
            },
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            await client.aclose()
    if not isinstance(payload, dict):
        return "DENIED: (no reason given)"
    return _format_verdict(cast(Mapping[str, object], payload))


if __name__ == "__main__":
    main()
