"""Full-fidelity terminal reader. Holds ui_ro; never approves, never writes."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from airgap.policy import matches_tool

DEFAULT_BROKER_URL = "http://127.0.0.1:8741"
POLL_INTERVAL_S = 1.0
# Quit is the only binding. Approve/deny/never stay on the device.
KEYBINDINGS: dict[str, str] = {"ctrl+c": "quit"}

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))"
)


class MethodNotAllowed(Exception):
    """The watch client has no write methods; a verb here would be a new capability."""


class ScopeRejected(Exception):
    """This process is ui_ro-only; an agent token cannot read the queue."""


@dataclass(frozen=True, slots=True)
class WatchSnapshot:
    """One poll's worth of queue state, already safe to print."""

    link: str
    queue_depth: int
    request_id: str | None
    actor: str
    tool_name: str
    tool_args: dict[str, object]
    tool_args_text: str
    justification: str
    risk_class: str
    reasoning: str
    policy: dict[str, object] | None
    dial: int | None
    elapsed_s: int | None
    short_code: str | None
    rendered: str


class GetOnlyClient:
    """httpx wrapper whose non-GET methods fail closed at the client layer."""

    def __init__(self, inner: httpx.AsyncClient, *, token: str) -> None:
        self._inner = inner
        self._token = token

    def _headers(self, extra: Mapping[str, str] | None) -> dict[str, str]:
        headers = {
            key: value
            for key, value in dict(extra or {}).items()
            if key.lower() != "origin"
        }
        headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        headers = self._headers(
            cast(Mapping[str, str] | None, kwargs.pop("headers", None))
        )
        return await self._inner.get(url, headers=headers, **kwargs)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if method.upper() != "GET":
            raise MethodNotAllowed(f"{method} is not allowed from airgap watch")
        return await self.get(url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        del url, kwargs
        raise MethodNotAllowed("POST is not allowed from airgap watch")

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        del url, kwargs
        raise MethodNotAllowed("PUT is not allowed from airgap watch")

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        del url, kwargs
        raise MethodNotAllowed("PATCH is not allowed from airgap watch")

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        del url, kwargs
        raise MethodNotAllowed("DELETE is not allowed from airgap watch")


def sanitize(value: str) -> str:
    """Strip ANSI and escape leftover control characters."""
    stripped = _ANSI_RE.sub("", value)
    return "".join(
        f"\\x{ord(char):02x}" if ord(char) < 32 or ord(char) == 127 else char
        for char in stripped
    )


class Watcher:
    """Poll GET /pending (and sibling reads) and render the armed request in full."""

    def __init__(
        self,
        *,
        broker_url: str,
        token: str,
        http_client: GetOnlyClient,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None] | None] | None = None,
        poll_interval: float = POLL_INTERVAL_S,
        console: Console | None = None,
    ) -> None:
        self._url = broker_url.rstrip("/")
        self.token = token
        self._client = http_client
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._poll_interval = poll_interval
        self._console = console

    async def tick(self) -> WatchSnapshot:
        pending = await self._get_json("/pending", required=True)
        audit = await self._get_json("/audit", required=False)
        policies = await self._get_json("/policies", required=False)
        health = await self._get_json("/health", required=False)
        snapshot = self._snapshot(pending, audit, policies, health)
        return snapshot

    async def run(self) -> None:
        console = self._console or Console(
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
        while True:
            snapshot = await self.tick()
            console.clear()
            _print_snapshot(console, snapshot)
            await _maybe_await(self._sleep(self._poll_interval))

    async def _get_json(self, path: str, *, required: bool) -> dict[str, object]:
        response = await self._client.get(f"{self._url}{path}")
        if path == "/pending" and response.status_code in {401, 403}:
            raise ScopeRejected("AIRGAP_UI_RO_TOKEN is not accepted as ui_ro")
        if response.status_code >= 400:
            if required:
                response.raise_for_status()
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            return {}
        return cast(dict[str, object], payload)

    def _snapshot(
        self,
        pending: Mapping[str, object],
        audit: Mapping[str, object],
        policies: Mapping[str, object],
        health: Mapping[str, object],
    ) -> WatchSnapshot:
        armed_raw = pending.get("armed")
        queue_raw = pending.get("queue")
        queue = queue_raw if isinstance(queue_raw, list) else []
        armed = armed_raw if isinstance(armed_raw, dict) else None
        queue_depth = (1 if armed is not None else 0) + len(queue)
        health_pending = health.get("pending")
        if isinstance(health_pending, int) and not isinstance(health_pending, bool):
            queue_depth = health_pending
        link = _as_str(pending.get("link")) or _as_str(health.get("link")) or "down"
        tool_args = _as_dict(armed.get("tool_args") if armed else None)
        tool_name = sanitize(_as_str(armed.get("tool_name") if armed else None))
        policy = _as_optional_dict(armed.get("policy") if armed else None)
        if policy is None and armed is not None:
            policy = _match_policy(tool_name, policies)
        reasoning = sanitize(_as_str(armed.get("reasoning") if armed else None))
        if not reasoning:
            reasoning = sanitize(
                _reasoning_from_audit(
                    audit, _as_str(armed.get("request_id") if armed else None)
                )
            )
        justification = sanitize(_as_str(armed.get("justification") if armed else None))
        actor = sanitize(_as_str(armed.get("actor") if armed else None))
        risk_class = sanitize(_as_str(armed.get("risk_class") if armed else None))
        short_raw = armed.get("short_code") if armed is not None else None
        short_code = sanitize(short_raw) if isinstance(short_raw, str) else None
        request_id = _as_str(armed.get("request_id") if armed else None) or None
        tool_args_text = sanitize(
            json.dumps(tool_args, indent=2, ensure_ascii=False, default=str)
        )
        snapshot = WatchSnapshot(
            link=sanitize(link),
            queue_depth=queue_depth,
            request_id=request_id,
            actor=actor,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_args_text=tool_args_text,
            justification=justification,
            risk_class=risk_class,
            reasoning=reasoning,
            policy=policy,
            dial=_as_int(armed.get("dial") if armed else None),
            elapsed_s=_as_int(armed.get("elapsed_s") if armed else None),
            short_code=short_code,
            rendered="",
        )
        return replace(snapshot, rendered=_render(snapshot))


def _plain(value: str) -> Text:
    """Untrusted copy is text, never Rich markup, and folds instead of cropping."""
    return Text(value, overflow="fold", no_wrap=False)


def _print_snapshot(console: Console, snapshot: WatchSnapshot) -> None:
    meta = Table.grid(padding=(0, 2), expand=True)
    meta.add_column(style="bold", no_wrap=True)
    meta.add_column(overflow="fold", ratio=1)
    meta.add_row("link", _plain(snapshot.link))
    meta.add_row("queue", _plain(str(snapshot.queue_depth)))
    meta.add_row("dial", _plain("—" if snapshot.dial is None else str(snapshot.dial)))
    meta.add_row(
        "elapsed_s",
        _plain("—" if snapshot.elapsed_s is None else str(snapshot.elapsed_s)),
    )
    meta.add_row("short_code", _plain(snapshot.short_code or "—"))
    meta.add_row("request_id", _plain(snapshot.request_id or "—"))
    meta.add_row("actor", _plain(snapshot.actor or "—"))
    meta.add_row("tool_name", _plain(snapshot.tool_name or "—"))
    meta.add_row("risk_class", _plain(snapshot.risk_class or "—"))
    meta.add_row("policy", _plain(_policy_line(snapshot.policy)))
    meta.add_row("justification", _plain(snapshot.justification or "—"))
    meta.add_row("reasoning", _plain(snapshot.reasoning or "—"))
    console.print(
        Panel(meta, title="airgap watch", subtitle="ui_ro · no approve"),
        markup=False,
        highlight=False,
        crop=False,
        overflow="fold",
        soft_wrap=True,
    )
    console.print(
        Panel(_plain(snapshot.tool_args_text), title="tool_args"),
        markup=False,
        highlight=False,
        crop=False,
        overflow="fold",
        soft_wrap=True,
    )


def _render(snapshot: WatchSnapshot) -> str:
    console = Console(
        record=True,
        width=120,
        color_system=None,
        highlight=False,
        markup=False,
        force_terminal=False,
        soft_wrap=True,
    )
    _print_snapshot(console, snapshot)
    return console.export_text()


def _policy_line(policy: dict[str, object] | None) -> str:
    if policy is None:
        return "—"
    pattern = sanitize(str(policy.get("tool_pattern") or "—"))
    action = sanitize(str(policy.get("action") or "—"))
    min_dial = policy.get("min_dial")
    gated = policy.get("relay_gated")
    return f"{pattern} {action} min_dial={min_dial} relay_gated={gated}"


def _match_policy(
    tool_name: str, payload: Mapping[str, object]
) -> dict[str, object] | None:
    rows = payload.get("policies")
    if not isinstance(rows, list):
        return None
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        pattern = raw.get("tool_pattern")
        if isinstance(pattern, str) and matches_tool(pattern, tool_name):
            return cast(dict[str, object], raw)
    return None


def _reasoning_from_audit(payload: Mapping[str, object], request_id: str) -> str:
    if not request_id:
        return ""
    events = payload.get("events")
    if not isinstance(events, list):
        return ""
    for raw in reversed(events):
        if not isinstance(raw, dict):
            continue
        if raw.get("request_id") != request_id:
            continue
        body = raw.get("payload")
        if isinstance(body, dict) and body.get("reasoning"):
            return str(body["reasoning"])
    return ""


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _as_optional_dict(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return None


async def _maybe_await(result: Awaitable[None] | None) -> None:
    if result is None:
        return
    await result


async def run_watch(
    *,
    broker_url: str | None = None,
    token: str | None = None,
    argv: Sequence[str] | None = None,
) -> None:
    del argv
    resolved_url = broker_url or os.environ.get("BROKER_URL", DEFAULT_BROKER_URL)
    resolved_token = token or os.environ.get("AIRGAP_UI_RO_TOKEN", "")
    if not resolved_token:
        raise ValueError("AIRGAP_UI_RO_TOKEN is required")
    async with httpx.AsyncClient() as inner:
        client = GetOnlyClient(inner, token=resolved_token)
        watcher = Watcher(
            broker_url=resolved_url,
            token=resolved_token,
            http_client=client,
        )
        await watcher.run()
