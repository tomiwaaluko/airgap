"""Validated device command path: allowlist, clamps, rate limits, fail-closed."""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Callable

from airgap.protocol import (
    Ack,
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
from airgap.vocab import AuditEvent, DecidedBy, LedState, Verdict

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
_INTERLOCK_WINDOW_S = 30.0
_INTERLOCK_WINDOW_MS = 30_000
_RENEW_INTERVAL_S = 3.0
_DEFAULT_DWELL_S = 60
_ARM_DEAD_TIME_S = 2.0

_log = logging.getLogger(__name__)


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
        on_resolve: Callable[[str, str, str], None] | None = None,
        on_audit: Callable[[str, str | None, object], None] | None = None,
    ) -> None:
        self._transport = transport
        self._clock = clock or time.monotonic
        self._on_resolve_pending = on_resolve_pending
        self._on_resolve = on_resolve
        self._on_audit = on_audit
        self._last_tick = self._clock()
        self._last_tick_device_t: int | None = None
        self._unparseable = 0
        self._pending: list[str] = []
        self._armed: str | None = None
        self._relay_gated = False
        self._dwell_s = _DEFAULT_DWELL_S
        self._seq = 0
        self._arm_acked = False
        self._arm_ack_seq = 0
        self._arm_acked_device_t: int | None = None
        self._approve: ButtonEvent | None = None
        self._approve_seq = 0
        self._approve_host_at: float | None = None
        self._approve_consumed = False
        self._human_resolved = False
        self._renewing = False
        self._last_renew_at: float | None = None
        self._dwell_deadline: float | None = None
        self._cycle_incomplete = False
        self._dead_until: float | None = None
        self._buttons_released = True
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

    def arm(
        self,
        request_id: str,
        *,
        relay_gated: bool = False,
        dwell_s: int = _DEFAULT_DWELL_S,
    ) -> None:
        """ARMED is Supervisor state so Rule 4 still holds after resolve (C1)."""
        if self._armed is not None:
            raise SupervisorRejection("already armed")
        if self._dead_until is not None and self._clock() < self._dead_until:
            raise SupervisorRejection("arming dead time")
        if not self._buttons_released:
            raise SupervisorRejection("buttons not released")
        self._armed = request_id
        self._relay_gated = relay_gated
        self._dwell_s = dwell_s
        self._arm_acked = False
        self._arm_ack_seq = 0
        self._arm_acked_device_t = None
        self._approve = None
        self._approve_seq = 0
        self._approve_host_at = None
        self._approve_consumed = False
        self._human_resolved = False
        self._renewing = False
        self._last_renew_at = None
        self._dwell_deadline = None
        self._cycle_incomplete = False

    def disarm(self) -> None:
        """Stop holding; ARMED is released only after the contact reopens."""
        self._stop_renewing()
        self._clear_arm_state()

    def track_pending(self, request_id: str) -> None:
        """Seam for AIR-9: pending ids resolved `link_lost` on safe state."""
        self._pending.append(request_id)

    async def on_event(self, ev: Event) -> None:
        if isinstance(ev, TickEvent):
            self._last_tick = self._clock()
            self._last_tick_device_t = ev.t
            if ev.btns == 0:
                self._buttons_released = True
            await self.tick_cycle()
            return
        if isinstance(ev, ButtonEvent):
            await self._on_button(ev)
            return
        if isinstance(ev, LeaseExpiredEvent):
            await self._on_lease_expired()

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
            await self.on_event(decoded)

    async def check_watchdog(self) -> None:
        if self._in_safe_state:
            return
        if not self._transport.connected:
            await self.enter_safe_state("disconnect")
            return
        if self._clock() - self._last_tick >= _TICK_TIMEOUT_S:
            await self.enter_safe_state("tick_starvation")
            return
        await self.tick_cycle()

    async def tick_cycle(self) -> None:
        """Drive renewals and dwell from the injected clock; never sleep."""
        if self._in_safe_state or not self._renewing:
            return
        now = self._clock()
        if self._dwell_deadline is not None and now >= self._dwell_deadline:
            await self._end_dwell()
            return
        if (
            self._last_renew_at is not None
            and now - self._last_renew_at >= _RENEW_INTERVAL_S
        ):
            await self._renew()

    async def enter_safe_state(self, reason: str) -> None:
        del reason
        if self._entering_safe:
            return
        already = self._in_safe_state
        self._entering_safe = True
        self._in_safe_state = True
        self._healthy = False
        self._stop_renewing()
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

    async def _dispatch(self, cmd: Command, *, force: bool) -> Ack | None:
        if self._in_safe_state and not force:
            if not (isinstance(cmd, RelayCommand) and cmd.closed is False):
                raise SupervisorRejection("device is in the safe state")
        if not force:
            if self._drop_tone(cmd) or self._drop_lcd(cmd) or self._drop_global(cmd):
                return None
            if self._reject_relay(cmd):
                raise SupervisorRejection("relay rate limit")
            if isinstance(cmd, RelayCommand) and cmd.closed is True:
                failed = self._interlock_failure()
                if failed is not None:
                    _log.warning(
                        "interlock rejected relay close: condition %s failed",
                        failed,
                    )
                    raise SupervisorRejection(f"interlock condition {failed} failed")
                self._ensure_human_resolved()
        return await self._write(cmd)

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

    async def _write(self, cmd: Command) -> Ack:
        try:
            ack = await self._transport.write(encode(cmd))
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
        elif isinstance(cmd, RelayCommand) and cmd.closed is True:
            self._sent_relay.append(now)
        await self._after_successful_write(cmd, ack)
        return ack

    async def _after_successful_write(self, cmd: Command, ack: Ack) -> None:
        if isinstance(cmd, ArmCommand) and ack.ok and cmd.req == self._armed:
            self._seq += 1
            self._arm_ack_seq = self._seq
            self._arm_acked = True
            self._arm_acked_device_t = self._last_tick_device_t
            return
        if isinstance(cmd, RelayCommand) and cmd.closed is True and ack.ok:
            self._approve_consumed = True
            if self._relay_gated:
                self._start_cycle()
            self._audit(AuditEvent.RELAY_CLOSED, self._armed, {})
            return
        if isinstance(cmd, RelayCommand) and cmd.closed is False:
            await self._after_relay_open()
            return
        if isinstance(cmd, RelayRenewCommand):
            return
        if isinstance(cmd, DisarmCommand):
            self._stop_renewing()
            self._clear_arm_state()

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

    async def _on_button(self, ev: ButtonEvent) -> None:
        if ev.which in {"deny", "never"}:
            await self._on_deny_or_never(ev)
            return
        if ev.which != "approve":
            return
        self._seq += 1
        self._approve = ev
        self._approve_seq = self._seq
        self._approve_host_at = self._clock()
        failed = self._interlock_failure()
        if failed is not None:
            _log.warning("interlock rejected button: condition %s failed", failed)
            return
        await self._rule_4a()

    async def _on_deny_or_never(self, ev: ButtonEvent) -> None:
        """Deny is not Rule-4-gated; matching armed id after arm ack is enough."""
        self._seq += 1
        if (
            self._armed is None
            or ev.req != self._armed
            or not self._arm_acked
            or self._seq <= self._arm_ack_seq
            or self._human_resolved
        ):
            return
        request_id = self._armed
        self._audit(AuditEvent.BUTTON, request_id, {"which": ev.which})
        self._audit(
            AuditEvent.RESOLVED,
            request_id,
            {"verdict": Verdict.DENIED, "decided_by": DecidedBy.HUMAN},
        )
        if self._on_resolve is not None:
            self._on_resolve(request_id, Verdict.DENIED, DecidedBy.HUMAN)
        self._human_resolved = True
        if request_id in self._pending:
            self._pending.remove(request_id)
        await self._send_disarm()

    async def _rule_4a(self) -> None:
        if self._relay_gated:
            await self.send(RelayCommand(id=self._internal_ids.next(), closed=True))
            return
        self._ensure_human_resolved()
        self._approve_consumed = True
        await self._finish_without_relay()

    def _interlock_failure(self) -> int | None:
        if self._armed is None:
            return 1
        if self._approve is None or self._approve_consumed:
            return 2
        if self._approve.which != "approve":
            return 2
        if self._approve.req != self._armed:
            return 3
        if not self._arm_acked or self._approve_seq <= self._arm_ack_seq:
            return 4
        if self._arm_acked_device_t is None:
            return 4
        if self._approve.t <= self._arm_acked_device_t:
            return 4
        if (
            self._approve_host_at is not None
            and self._clock() - self._approve_host_at >= _INTERLOCK_WINDOW_S
        ):
            return 5
        if (
            self._last_tick_device_t is not None
            and self._last_tick_device_t - self._approve.t >= _INTERLOCK_WINDOW_MS
        ):
            return 5
        return None

    def _ensure_human_resolved(self) -> None:
        if self._human_resolved or self._armed is None:
            return
        request_id = self._armed
        self._audit(AuditEvent.BUTTON, request_id, {"which": "approve"})
        self._audit(
            AuditEvent.RESOLVED,
            request_id,
            {"verdict": Verdict.APPROVED, "decided_by": DecidedBy.HUMAN},
        )
        if self._on_resolve is not None:
            self._on_resolve(request_id, Verdict.APPROVED, DecidedBy.HUMAN)
        self._human_resolved = True
        if request_id in self._pending:
            self._pending.remove(request_id)

    async def _finish_without_relay(self) -> None:
        await self._send_disarm()

    def _start_cycle(self) -> None:
        now = self._clock()
        self._renewing = True
        self._last_renew_at = now
        self._dwell_deadline = now + float(self._dwell_s)
        self._cycle_incomplete = False

    def _stop_renewing(self) -> None:
        self._renewing = False
        self._last_renew_at = None
        self._dwell_deadline = None

    async def _renew(self) -> None:
        await self.send(RelayRenewCommand(id=self._internal_ids.next()))
        self._last_renew_at = self._clock()

    async def _end_dwell(self) -> None:
        self._stop_renewing()
        await self.send(RelayCommand(id=self._internal_ids.next(), closed=False))

    async def _after_relay_open(self) -> None:
        was_holding = self._armed is not None and self._human_resolved
        self._stop_renewing()
        if was_holding:
            self._audit(AuditEvent.RELAY_OPENED, self._armed, {})
            await self._send_disarm()

    async def _on_lease_expired(self) -> None:
        mid_dwell = (
            self._armed is not None
            and self._renewing
            and self._dwell_deadline is not None
            and self._clock() < self._dwell_deadline
        )
        if mid_dwell:
            request_id = self._armed
            self._cycle_incomplete = True
            self._stop_renewing()
            self._audit(
                AuditEvent.LEASE_EXPIRED,
                request_id,
                {"cycle_incomplete": True},
            )
            await self._send_disarm()
            return
        self._audit(AuditEvent.LEASE_EXPIRED, None, {})
        await self.send(RelayCommand(id=self._internal_ids.next(), closed=False))

    async def _send_disarm(self) -> None:
        if self._armed is not None and not self._in_safe_state:
            try:
                await self._dispatch(
                    DisarmCommand(id=self._internal_ids.next()),
                    force=False,
                )
            except SupervisorRejection:
                pass
        self._begin_dead_time()
        self.disarm()

    def _begin_dead_time(self) -> None:
        self._dead_until = self._clock() + _ARM_DEAD_TIME_S
        self._buttons_released = False

    def _clear_arm_state(self) -> None:
        self._armed = None
        self._arm_acked = False
        self._arm_ack_seq = 0
        self._arm_acked_device_t = None
        self._relay_gated = False

    def _audit(
        self, event: AuditEvent, request_id: str | None, payload: object
    ) -> None:
        if self._on_audit is not None:
            self._on_audit(event, request_id, payload)


def _lcd_field(value: str) -> str:
    stripped = "".join(char for char in value if ord(char) < 128)
    return stripped[:_LCD_WIDTH]
