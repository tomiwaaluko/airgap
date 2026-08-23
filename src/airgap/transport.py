"""Serial transports keep the wire boundary testable without an Arduino."""

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Protocol, cast, runtime_checkable

import serial  # type: ignore[import-untyped]

from airgap.protocol import Ack, decode

_MAX_FRAME_LENGTH = 200
_ACK_TIMEOUT_SECONDS = 0.1


class AckTimeout(TimeoutError):
    """Raised when the device does not acknowledge a command in time."""


@runtime_checkable
class Transport(Protocol):
    """Lets the Supervisor use hardware and scripted devices identically."""

    @property
    def connected(self) -> bool: ...

    async def write(self, frame: bytes) -> Ack: ...

    async def read_lines(self) -> AsyncIterator[bytes]: ...

    def close(self) -> None: ...


class _SerialPort(Protocol):
    @property
    def in_waiting(self) -> int: ...

    @property
    def is_open(self) -> bool: ...

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


class _LineAssembler:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._discarding = False
        self.overflow_count = 0

    def feed(self, chunk: bytes) -> list[bytes]:
        lines: list[bytes] = []
        for byte in chunk:
            if self._discarding:
                if byte == ord("\n"):
                    self._discarding = False
                    self.overflow_count += 1
                continue
            self._buffer.append(byte)
            if byte == ord("\n"):
                lines.append(bytes(self._buffer))
                self._buffer.clear()
            elif len(self._buffer) == _MAX_FRAME_LENGTH:
                self._buffer.clear()
                self._discarding = True
        return lines


class SerialTransport:
    """Uses non-blocking reads so a missing device frame cannot stall the host."""

    def __init__(
        self,
        port: str,
        *,
        serial_factory: Callable[..., _SerialPort] | None = None,
    ) -> None:
        factory = serial_factory or cast(Callable[..., _SerialPort], serial.Serial)
        self._serial = factory(
            port=port,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
            write_timeout=0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self._assembler = _LineAssembler()
        self._event_lines: deque[bytes] = deque()
        self._acks: dict[int, Ack] = {}
        self._closed = False

    @property
    def connected(self) -> bool:
        return not self._closed and self._serial.is_open

    @property
    def overflow_count(self) -> int:
        return self._assembler.overflow_count

    async def write(self, frame: bytes) -> Ack:
        command_id = _command_id(frame)
        self._serial.write(frame)
        try:
            return await asyncio.wait_for(
                self._wait_for_ack(command_id), timeout=_ACK_TIMEOUT_SECONDS
            )
        except TimeoutError as error:
            raise AckTimeout(f"command {command_id} was not acknowledged") from error

    async def read_lines(self) -> AsyncIterator[bytes]:
        while self.connected:
            if self._event_lines:
                yield self._event_lines.popleft()
                continue
            self._ingest_available()
            await asyncio.sleep(0)

    def close(self) -> None:
        self._closed = True
        self._serial.close()

    async def _wait_for_ack(self, command_id: int) -> Ack:
        while self.connected:
            existing = self._acks.pop(command_id, None)
            if existing is not None:
                return existing
            self._ingest_available()
            await asyncio.sleep(0)
        raise AckTimeout(f"command {command_id} lost its serial link")

    def _ingest_available(self) -> None:
        waiting = self._serial.in_waiting
        if waiting == 0:
            return
        lines = self._assembler.feed(self._serial.read(waiting))
        for line in lines:
            decoded = decode(line)
            if isinstance(decoded, Ack):
                self._acks[decoded.id] = decoded
            else:
                self._event_lines.append(line)


class MockTransport:
    """Replays device lines deterministically, including malformed device output."""

    def __init__(self, script: Iterable[bytes | None] = ()) -> None:
        self._script = deque(script)
        self._queued_lines: deque[bytes] = deque()
        self._closed = False
        self.writes: list[bytes] = []

    @property
    def connected(self) -> bool:
        return not self._closed

    async def write(self, frame: bytes) -> Ack:
        command_id = _command_id(frame)
        self.writes.append(frame)
        while self.connected:
            line = self._next_script_line()
            if line is None:
                break
            decoded = decode(line)
            if isinstance(decoded, Ack) and decoded.id == command_id:
                return decoded
            self._queued_lines.append(line)
        raise AckTimeout(f"command {command_id} was not acknowledged")

    async def read_lines(self) -> AsyncIterator[bytes]:
        while self.connected:
            if self._queued_lines:
                yield self._queued_lines.popleft()
                continue
            line = self._next_script_line()
            if line is None:
                return
            yield line

    def close(self) -> None:
        self._closed = True

    def _next_script_line(self) -> bytes | None:
        if not self._script:
            return None
        line = self._script.popleft()
        if line is None:
            self._closed = True
        return line


def _command_id(frame: bytes) -> int:
    try:
        decoded = json.loads(frame.removesuffix(b"\n").decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("frame must be an ASCII JSON command") from error
    if not isinstance(decoded, dict):
        raise ValueError("frame must contain an integer command id")
    command_id: object = decoded.get("id")
    if not isinstance(command_id, int) or isinstance(command_id, bool):
        raise ValueError("frame must contain an integer command id")
    return command_id
