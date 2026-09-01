"""Supervisor core: allowlist, clamps, rate limits, and fail-closed safe state."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable
from typing import cast

import pytest

from airgap.protocol import (
    Ack,
    ArmCommand,
    ButtonEvent,
    Command,
    LcdCommand,
    LedCommand,
    PingCommand,
    RelayCommand,
    ToneCommand,
    encode,
)
from airgap.supervisor import Supervisor, SupervisorRejection
from airgap.transport import AckTimeout
from airgap.vocab import LedState, TonePattern, Verdict


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class AutoAckTransport:
    """Records writes and acks immediately so rate-limit tests stay readable."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self._closed = False
        self.timeout_on_relay = False

    @property
    def connected(self) -> bool:
        return not self._closed

    async def write(self, frame: bytes) -> Ack:
        payload = json.loads(frame.decode("ascii"))
        self.writes.append(frame)
        if self.timeout_on_relay and payload.get("cmd") == "relay":
            raise AckTimeout(f"command {payload['id']} was not acknowledged")
        return Ack(int(payload["id"]), True)

    async def read_lines(self) -> AsyncIterator[bytes]:
        if False:
            yield b""

    def close(self) -> None:
        self._closed = True


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)


def _frames(transport: AutoAckTransport) -> list[dict[str, object]]:
    return [json.loads(frame.decode("ascii")) for frame in transport.writes]


def _supervisor(
    *,
    clock: Clock | None = None,
    transport: AutoAckTransport | None = None,
    resolved: list[tuple[str, str]] | None = None,
) -> tuple[Supervisor, AutoAckTransport, Clock, list[tuple[str, str]]]:
    clock = clock or Clock()
    transport = transport or AutoAckTransport()
    resolved = resolved if resolved is not None else []
    supervisor = Supervisor(
        transport,
        clock=clock,
        on_resolve_pending=lambda request_id, verdict: resolved.append(
            (request_id, verdict)
        ),
    )
    return supervisor, transport, clock, resolved


def test_rejects_dict_string_and_raw_passthrough() -> None:
    supervisor, _, _, _ = _supervisor()

    with pytest.raises(SupervisorRejection):
        _run(supervisor.send(cast(Command, {"cmd": "ping"})))
    with pytest.raises(SupervisorRejection):
        _run(supervisor.send(cast(Command, "ping")))
    with pytest.raises(SupervisorRejection):
        _run(supervisor.send(cast(Command, encode(PingCommand(id=1)))))


def test_tone_clamp_boundaries() -> None:
    supervisor, transport, _, _ = _supervisor()

    _run(supervisor.send(ToneCommand(id=1, pattern=TonePattern.ALERT, n=0)))
    frames = _frames(transport)
    assert frames[0]["n"] == 1

    supervisor_n1, transport_n1, _, _ = _supervisor()
    _run(supervisor_n1.send(ToneCommand(id=2, pattern=TonePattern.ALERT, n=1)))
    assert _frames(transport_n1)[0]["n"] == 1

    supervisor2, transport2, clock, _ = _supervisor()
    _run(supervisor2.send(ToneCommand(id=1, pattern=TonePattern.ALERT, n=5)))
    clock.advance(2.0)
    _run(supervisor2.send(ToneCommand(id=2, pattern=TonePattern.ALERT, n=6)))
    frames2 = _frames(transport2)
    assert frames2[0]["n"] == 5
    assert frames2[1]["n"] == 5


def test_led_state_outside_enum_is_rejected_not_coerced() -> None:
    supervisor, transport, _, _ = _supervisor()

    with pytest.raises(SupervisorRejection):
        _run(supervisor.send(LedCommand(id=1, state=cast(LedState, "blue"))))
    assert transport.writes == []


def test_lcd_truncation_and_non_ascii_stripping() -> None:
    supervisor, transport, _, _ = _supervisor()

    _run(
        supervisor.send(
            LcdCommand(
                id=1,
                l1="DROP users_production",
                l2="hello™café",
            )
        )
    )
    frame = _frames(transport)[0]
    assert frame["l1"] == "DROP users_produ"
    assert frame["l2"] == "hellocaf"
    assert len(cast(str, frame["l1"])) == 16


def test_arm_req_must_be_eight_lowercase_hex() -> None:
    supervisor, transport, _, _ = _supervisor()

    with pytest.raises(SupervisorRejection):
        _run(supervisor.send(ArmCommand(id=1, req="ABCDEF01")))
    with pytest.raises(SupervisorRejection):
        _run(supervisor.send(ArmCommand(id=2, req="abc")))
    with pytest.raises(SupervisorRejection):
        _run(supervisor.send(ArmCommand(id=3, req="zzzzzzzz")))
    _run(supervisor.send(ArmCommand(id=4, req="a91f3c2e")))
    assert _frames(transport)[0]["req"] == "a91f3c2e"


def test_global_rate_limit_drops_eleventh_non_relay_in_one_second() -> None:
    supervisor, transport, clock, _ = _supervisor()

    for index in range(10):
        _run(supervisor.send(PingCommand(id=index + 1)))
    _run(supervisor.send(PingCommand(id=11)))
    assert len(transport.writes) == 10

    clock.advance(1.0)
    _run(supervisor.send(PingCommand(id=12)))
    assert len(transport.writes) == 11


def test_tone_rate_limit_drops_second_tone_inside_two_seconds() -> None:
    supervisor, transport, clock, _ = _supervisor()

    _run(supervisor.send(ToneCommand(id=1, pattern=TonePattern.ALERT, n=1)))
    _run(supervisor.send(ToneCommand(id=2, pattern=TonePattern.OK, n=1)))
    assert len(transport.writes) == 1
    clock.advance(2.0)
    _run(supervisor.send(ToneCommand(id=3, pattern=TonePattern.OK, n=1)))
    assert len(transport.writes) == 2


def test_lcd_rate_limit_drops_third_lcd_in_one_second() -> None:
    supervisor, transport, clock, _ = _supervisor()

    _run(supervisor.send(LcdCommand(id=1, l1="a", l2="b")))
    _run(supervisor.send(LcdCommand(id=2, l1="c", l2="d")))
    _run(supervisor.send(LcdCommand(id=3, l1="e", l2="f")))
    assert len(transport.writes) == 2
    clock.advance(1.0)
    _run(supervisor.send(LcdCommand(id=4, l1="g", l2="h")))
    assert len(transport.writes) == 3


def test_relay_rate_limit_rejects_never_drops() -> None:
    supervisor, transport, clock, _ = _supervisor()

    supervisor.arm("a91f3c2e", relay_gated=True)
    _run(supervisor.send(ArmCommand(id=10, req="a91f3c2e")))
    _run(supervisor.on_event(ButtonEvent(which="approve", req="a91f3c2e", t=1)))
    closes = [
        frame
        for frame in _frames(transport)
        if frame.get("cmd") == "relay" and frame.get("closed") is True
    ]
    assert len(closes) == 1
    with pytest.raises(SupervisorRejection):
        _run(supervisor.send(RelayCommand(id=2, closed=True)))
    closes = [
        frame
        for frame in _frames(transport)
        if frame.get("cmd") == "relay" and frame.get("closed") is True
    ]
    assert len(closes) == 1
    clock.advance(1.0)
    _run(supervisor.send(RelayCommand(id=3, closed=False)))
    assert any(
        frame.get("cmd") == "relay" and frame.get("closed") is False
        for frame in _frames(transport)
    )


def test_tick_starvation_enters_safe_state() -> None:
    supervisor, transport, clock, resolved = _supervisor()
    supervisor.track_pending("a91f3c2e")

    _run(supervisor.check_watchdog())
    assert supervisor.healthy is True

    clock.advance(3.001)
    _run(supervisor.check_watchdog())

    assert supervisor.healthy is False
    assert resolved == [("a91f3c2e", Verdict.LINK_LOST)]
    commands = [frame["cmd"] for frame in _frames(transport)]
    assert "relay" in commands
    assert "led" in commands
    assert "flag" in commands
    assert {"cmd": "relay", "closed": False}.items() <= _frames(transport)[
        0
    ].items() or any(
        frame.get("cmd") == "relay" and frame.get("closed") is False
        for frame in _frames(transport)
    )
    assert any(
        frame.get("cmd") == "led" and frame.get("state") == "red"
        for frame in _frames(transport)
    )
    assert any(
        frame.get("cmd") == "flag" and frame.get("up") is True
        for frame in _frames(transport)
    )


def test_three_unparseable_lines_enter_safe_state() -> None:
    supervisor, _, _, resolved = _supervisor()
    supervisor.track_pending("deadbeef")

    _run(supervisor.feed_line(b"not-json\n"))
    _run(supervisor.feed_line(b"{}\n"))
    assert supervisor.healthy is True
    _run(supervisor.feed_line(b"\xff\n"))
    assert supervisor.healthy is False
    assert resolved == [("deadbeef", Verdict.LINK_LOST)]


def test_safe_state_uses_link_lost_not_denied() -> None:
    supervisor, _, clock, resolved = _supervisor()
    supervisor.track_pending("aaaaaaaa")
    clock.advance(3.001)
    _run(supervisor.check_watchdog())
    assert resolved[0][1] == Verdict.LINK_LOST
    assert resolved[0][1] != "denied"


def test_relay_open_succeeds_in_safe_state() -> None:
    supervisor, transport, clock, _ = _supervisor()
    clock.advance(3.001)
    _run(supervisor.check_watchdog())
    before = len(transport.writes)
    _run(supervisor.send(RelayCommand(id=99, closed=False)))
    assert len(transport.writes) == before + 1
    assert _frames(transport)[-1]["closed"] is False


def test_relay_ack_timeout_enters_safe_state() -> None:
    transport = AutoAckTransport()
    transport.timeout_on_relay = True
    clock = Clock()
    resolved: list[tuple[str, str]] = []
    supervisor = Supervisor(
        transport,
        clock=clock,
        on_resolve_pending=lambda request_id, verdict: resolved.append(
            (request_id, verdict)
        ),
    )
    supervisor.track_pending("cccccccc")
    supervisor.arm("bbbbbbbb", relay_gated=True)
    _run(supervisor.send(ArmCommand(id=10, req="bbbbbbbb")))
    # Closing after a matching approve must not recurse on a second timeout.
    with pytest.raises(AckTimeout):
        _run(supervisor.on_event(ButtonEvent(which="approve", req="bbbbbbbb", t=1)))
    assert supervisor.healthy is False
    assert resolved == [("cccccccc", Verdict.LINK_LOST)]
