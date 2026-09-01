"""Validated device command path: allowlist, clamps, rate limits, fail-closed."""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable

from airgap.protocol import (
    ArmCommand,
    BootEvent,
    ButtonEvent,
    Command,
    DisarmCommand,
    Event,
    FlagCommand,
    IdAllocator,
    LcdCommand,
    LeaseExpiredEvent,
    LedCommand,
    PingCommand,
    RelayCommand,
    RelayRenewCommand,
    TickEvent,
    ToneCommand,
    decode,
    encode,
)
from airgap.transport import AckTimeout, Transport
from airgap.vocab import LedState, Verdict

_ARM_REQ = re.compile(r"^[0-9a-f]{8}$")
_COMMAND_TYPES = (
    PingCommand,
    LedCommand,
    ToneCommand,
    FlagCommand,
    RelayCommand,
    RelayRenewCommand,
    LcdCommand,
    ArmCommand,
    DisarmCommand,
)
_TICK_TIMEOUT_S = 3.0
_GLOBAL_RATE = 10
_GLOBAL_WINDOW_S = 1.0
_TONE_WINDOW_S = 2.0
_LCD_RATE = 2
_LCD_WINDOW_S = 1.0
_RELAY_WINDOW_S = 1.0
_LCD_WIDTH = 16


class SupervisorRejection(ValueError):
    """Raised when a caller asks the Supervisor to send a forbidden command."""


class Supervisor:
    """The only module allowed to hand a frame to the transport."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Callable[[], float] | None = None,
        on_resolve_pending: Callable[[str, str], None] | None = None,
    ) -> None:
        self._transport = transport
        self._clock = clock or time.monotonic
        self._on_resolve_pending = on_resolve_pending
        self._last_tick = self._clock()
        self._unparseable = 0
        self._pending: list[str] = []
        self._armed: str | None = None
        self._healthy = True
        self._in_safe_state = False
        self._entering_safe = False
        self._internal_ids = IdAllocator(start=60000)
        self._sent_global: deque[float] = deque()
        self._sent_tone: deque[float] = deque()
        self._sent_lcd: deque[float] = deque()
        self._sent_relay: deque[float] = deque()

    @property
    def healthy(self) -> bool:
        return self._healthy

    def arm(self, request_id: str) -> None:
        """Record Supervisor ARMED state. Interlock conditions are AIR-17."""
        self._armed = request_id

    def disarm(self) -> None:
        self._armed = None

    def track_pending(self, request_id: str) -> None:
        """Seam for AIR-9: pending ids resolved `link_lost` on safe state."""
        self._pending.append(request_id)

    def on_event(self, ev: Event) -> None:
        if isinstance(ev, TickEvent):
            self._last_tick = self._clock()

    async def send(self, cmd: Command) -> None:
        if not isinstance(cmd, _COMMAND_TYPES):
            raise SupervisorRejection("only typed Command objects are accepted")
        prepared = self._prepare(cmd)
        await self._dispatch(prepared, force=False)

    async def feed_line(self, line: bytes) -> None:
        decoded = decode(line)
        if decoded is None:
            self._unparseable += 1
            if self._unparseable >= 3:
                await self.enter_safe_state("unparseable")
            return
        self._unparseable = 0
        if isinstance(decoded, (ButtonEvent, BootEvent, LeaseExpiredEvent, TickEvent)):
            self.on_event(decoded)

    async def check_watchdog(self) -> None:
        if self._in_safe_state:
            return
        if not self._transport.connected:
            await self.enter_safe_state("disconnect")
            return
        if self._clock() - self._last_tick >= _TICK_TIMEOUT_S:
            await self.enter_safe_state("tick_starvation")

    async def enter_safe_state(self, reason: str) -> None:
        del reason
        if self._entering_safe:
            return
        already = self._in_safe_state
        self._entering_safe = True
        self._in_safe_state = True
        self._healthy = False
        pending = list(self._pending)
        self._pending.clear()
        if self._on_resolve_pending is not None:
            for request_id in pending:
                self._on_resolve_pending(request_id, Verdict.LINK_LOST)
        if not already:
            await self._best_effort_safe_actuators()
        self._entering_safe = False

    def _prepare(self, cmd: Command) -> Command:
        if isinstance(cmd, LedCommand):
            if cmd.state not in LedState:
                raise SupervisorRejection("led.state is not a valid LED state")
            return cmd
        if isinstance(cmd, ToneCommand):
            return ToneCommand(
                id=cmd.id,
                pattern=cmd.pattern,
                n=min(max(cmd.n, 1), 5),
            )
        if isinstance(cmd, LcdCommand):
            return LcdCommand(
                id=cmd.id,
                l1=_lcd_field(cmd.l1),
                l2=_lcd_field(cmd.l2),
            )
        if isinstance(cmd, ArmCommand):
            if _ARM_REQ.fullmatch(cmd.req) is None:
                raise SupervisorRejection("arm.req must be 8 lowercase hex chars")
            return cmd
        return cmd

    async def _dispatch(self, cmd: Command, *, force: bool) -> None:
        if self._in_safe_state and not force:
            if not (isinstance(cmd, RelayCommand) and cmd.closed is False):
                raise SupervisorRejection("device is in the safe state")
        if not force:
            if self._drop_tone(cmd) or self._drop_lcd(cmd) or self._drop_global(cmd):
                return
            if self._reject_relay(cmd):
                raise SupervisorRejection("relay rate limit")
        await self._write(cmd)

    def _drop_tone(self, cmd: Command) -> bool:
        if not isinstance(cmd, ToneCommand):
            return False
        self._prune(self._sent_tone, _TONE_WINDOW_S)
        return len(self._sent_tone) >= 1

    def _drop_lcd(self, cmd: Command) -> bool:
        if not isinstance(cmd, LcdCommand):
            return False
        self._prune(self._sent_lcd, _LCD_WINDOW_S)
        return len(self._sent_lcd) >= _LCD_RATE

    def _drop_global(self, cmd: Command) -> bool:
        if isinstance(cmd, RelayCommand):
            return False
        self._prune(self._sent_global, _GLOBAL_WINDOW_S)
        return len(self._sent_global) >= _GLOBAL_RATE

    def _reject_relay(self, cmd: Command) -> bool:
        if not isinstance(cmd, RelayCommand) or cmd.closed is False:
            return False
        self._prune(self._sent_relay, _RELAY_WINDOW_S)
        return len(self._sent_relay) >= 1

    async def _write(self, cmd: Command) -> None:
        try:
            await self._transport.write(encode(cmd))
        except AckTimeout:
            if isinstance(cmd, RelayCommand) and not self._entering_safe:
                await self.enter_safe_state("ack_timeout")
            raise
        now = self._clock()
        self._sent_global.append(now)
        if isinstance(cmd, ToneCommand):
            self._sent_tone.append(now)
        elif isinstance(cmd, LcdCommand):
            self._sent_lcd.append(now)
        elif isinstance(cmd, RelayCommand):
            self._sent_relay.append(now)

    async def _best_effort_safe_actuators(self) -> None:
        commands: tuple[Command, ...] = (
            RelayCommand(id=self._internal_ids.next(), closed=False),
            LedCommand(id=self._internal_ids.next(), state=LedState.RED),
            FlagCommand(id=self._internal_ids.next(), up=True),
        )
        for command in commands:
            try:
                await self._dispatch(command, force=True)
            except AckTimeout:
                continue

    def _prune(self, times: deque[float], window: float) -> None:
        now = self._clock()
        while times and now - times[0] >= window:
            times.popleft()


def _lcd_field(value: str) -> str:
    stripped = "".join(char for char in value if ord(char) < 128)
    return stripped[:_LCD_WIDTH]
