"""Pure, directional codec for the Airgap serial wire protocol."""

import json
from dataclasses import dataclass, field
from typing import ClassVar, Literal, TypeGuard

from airgap.vocab import AckErrorCode, CommandName, EventName, LedState, TonePattern

_MAX_FRAME_LENGTH = 200
_LCD_WIDTH = 16

type ButtonWhich = Literal["approve", "deny", "never"]


class FrameTooLong(ValueError):
    """Raised before a frame could violate the device's fixed buffer limit."""


@dataclass(frozen=True, slots=True)
class PingCommand:
    id: int
    cmd: ClassVar[CommandName] = CommandName.PING


@dataclass(frozen=True, slots=True)
class LedCommand:
    id: int
    state: LedState
    cmd: ClassVar[CommandName] = CommandName.LED


@dataclass(frozen=True, slots=True)
class ToneCommand:
    id: int
    pattern: TonePattern
    n: int
    cmd: ClassVar[CommandName] = CommandName.TONE


@dataclass(frozen=True, slots=True)
class FlagCommand:
    id: int
    up: bool
    cmd: ClassVar[CommandName] = CommandName.FLAG


@dataclass(frozen=True, slots=True)
class RelayCommand:
    id: int
    closed: bool
    cmd: ClassVar[CommandName] = CommandName.RELAY


@dataclass(frozen=True, slots=True)
class RelayRenewCommand:
    id: int
    cmd: ClassVar[CommandName] = CommandName.RELAY_RENEW


@dataclass(frozen=True, slots=True)
class LcdCommand:
    id: int
    l1: str
    l2: str
    cmd: ClassVar[CommandName] = CommandName.LCD


@dataclass(frozen=True, slots=True)
class ArmCommand:
    id: int
    req: str
    cmd: ClassVar[CommandName] = CommandName.ARM


@dataclass(frozen=True, slots=True)
class DisarmCommand:
    id: int
    cmd: ClassVar[CommandName] = CommandName.DISARM


type Command = (
    PingCommand
    | LedCommand
    | ToneCommand
    | FlagCommand
    | RelayCommand
    | RelayRenewCommand
    | LcdCommand
    | ArmCommand
    | DisarmCommand
)


@dataclass(frozen=True, slots=True)
class Ack:
    id: int
    ok: bool
    err: AckErrorCode | None = None


@dataclass(frozen=True, slots=True)
class ButtonEvent:
    which: ButtonWhich
    req: str | None
    t: int
    ev: EventName = field(default=EventName.BUTTON, init=False)


@dataclass(frozen=True, slots=True)
class BootEvent:
    fw: str
    t: int
    ev: EventName = field(default=EventName.BOOT, init=False)


@dataclass(frozen=True, slots=True)
class LeaseExpiredEvent:
    t: int
    ev: EventName = field(default=EventName.LEASE_EXPIRED, init=False)


@dataclass(frozen=True, slots=True)
class TickEvent:
    dial: int
    relay: bool
    armed: bool
    lease_ms: int
    btns: int
    t: int
    ev: EventName = field(default=EventName.TICK, init=False)


type Event = ButtonEvent | BootEvent | LeaseExpiredEvent | TickEvent


class IdAllocator:
    """Keeps command ids in the device's nonzero 16-bit acknowledgement space."""

    def __init__(self, start: int = 1) -> None:
        if not 1 <= start <= 65535:
            raise ValueError("start must be between 1 and 65535")
        self._next = start

    def next(self) -> int:
        """Return an id before advancing so the first id is the configured start."""
        value = self._next
        self._next = 1 if value == 65535 else value + 1
        return value


def encode(cmd: Command) -> bytes:
    """Create the sole host-to-device representation, including host normalization."""
    wire: dict[str, object] = {"id": cmd.id, "cmd": cmd.cmd}
    match cmd:
        case LedCommand(state=state):
            wire["state"] = state
        case ToneCommand(pattern=pattern, n=count):
            wire["pattern"] = pattern
            wire["n"] = min(max(count, 1), 5)
        case FlagCommand(up=up):
            wire["up"] = up
        case RelayCommand(closed=closed):
            wire["closed"] = closed
        case LcdCommand(l1=l1, l2=l2):
            wire["l1"] = l1[:_LCD_WIDTH]
            wire["l2"] = l2[:_LCD_WIDTH]
        case ArmCommand(req=req):
            wire["req"] = req
        case PingCommand() | RelayRenewCommand() | DisarmCommand():
            pass

    frame = (
        json.dumps(wire, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        + b"\n"
    )
    if len(frame) > _MAX_FRAME_LENGTH:
        raise FrameTooLong(
            f"frame is {len(frame)} bytes; maximum is {_MAX_FRAME_LENGTH}"
        )
    return frame


def decode(line: bytes) -> Ack | Event | None:
    """Discard malformed or host-originated frames without exposing parser errors."""
    try:
        if len(line) > _MAX_FRAME_LENGTH:
            return None
        raw = line.removesuffix(b"\n").removesuffix(b"\r")
        decoded = json.loads(raw.decode("ascii"), parse_constant=_reject_constant)
        if not isinstance(decoded, dict):
            return None
        if "cmd" in decoded:
            return None
        return _decode_ack(decoded) or _decode_event(decoded)
    except TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError:
        return None


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _decode_ack(frame: dict[str, object]) -> Ack | None:
    if set(frame) == {"id", "ok"} and _is_int(frame["id"]) and frame["ok"] is True:
        return Ack(frame["id"], frame["ok"])
    if (
        set(frame) == {"id", "ok", "err"}
        and _is_int(frame["id"])
        and frame["ok"] is False
        and isinstance(frame["err"], str)
    ):
        try:
            return Ack(frame["id"], False, AckErrorCode(frame["err"]))
        except ValueError:
            return None
    return None


def _decode_event(frame: dict[str, object]) -> Event | None:
    event_name = frame.get("ev")
    if not isinstance(event_name, str):
        return None
    try:
        event = EventName(event_name)
    except ValueError:
        return None

    if event is EventName.BUTTON:
        return _decode_button(frame)
    if event is EventName.BOOT:
        return _decode_boot(frame)
    if event is EventName.LEASE_EXPIRED:
        return _decode_lease_expired(frame)
    if event is EventName.TICK:
        return _decode_tick(frame)
    return None


def _decode_button(frame: dict[str, object]) -> ButtonEvent | None:
    which = frame.get("which")
    req = frame.get("req")
    timestamp = frame.get("t")
    if (
        set(frame) != {"ev", "which", "req", "t"}
        or not _is_button_which(which)
        or not (isinstance(req, str) or req is None)
        or not _is_nonnegative_int(timestamp)
    ):
        return None
    return ButtonEvent(which, req, timestamp)


def _decode_boot(frame: dict[str, object]) -> BootEvent | None:
    firmware = frame.get("fw")
    timestamp = frame.get("t")
    if (
        set(frame) != {"ev", "fw", "t"}
        or not isinstance(firmware, str)
        or not _is_nonnegative_int(timestamp)
    ):
        return None
    return BootEvent(firmware, timestamp)


def _decode_lease_expired(frame: dict[str, object]) -> LeaseExpiredEvent | None:
    timestamp = frame.get("t")
    if set(frame) != {"ev", "t"} or not _is_nonnegative_int(timestamp):
        return None
    return LeaseExpiredEvent(timestamp)


def _decode_tick(frame: dict[str, object]) -> TickEvent | None:
    dial = frame.get("dial")
    relay = frame.get("relay")
    armed = frame.get("armed")
    lease_ms = frame.get("lease_ms")
    buttons = frame.get("btns")
    timestamp = frame.get("t")
    if (
        set(frame) != {"ev", "dial", "relay", "armed", "lease_ms", "btns", "t"}
        or not _is_int(dial)
        or not 0 <= dial <= 10
        or not isinstance(relay, bool)
        or not isinstance(armed, bool)
        or not _is_nonnegative_int(lease_ms)
        or not _is_int(buttons)
        or not 0 <= buttons <= 7
        or not _is_nonnegative_int(timestamp)
    ):
        return None
    return TickEvent(dial, relay, armed, lease_ms, buttons, timestamp)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonnegative_int(value: object) -> TypeGuard[int]:
    return _is_int(value) and value >= 0


def _is_button_which(value: object) -> TypeGuard[ButtonWhich]:
    return value in {"approve", "deny", "never"}
