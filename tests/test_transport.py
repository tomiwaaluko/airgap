"""Behavioural tests for serial and scripted device transports."""

import asyncio
from collections.abc import AsyncIterator

import pytest

from airgap.protocol import Ack, BootEvent, TickEvent
from airgap.transport import AckTimeout, MockTransport, SerialTransport


async def _collect(lines: AsyncIterator[bytes]) -> list[bytes]:
    return [line async for line in lines]


def test_mock_records_writes_and_replays_normal_tick_stream() -> None:
    transport = MockTransport(
        [
            b'{"ev":"tick","dial":7,"relay":false,"armed":false,"lease_ms":0,"btns":0,"t":1000}\n',
            b'{"id":1,"ok":true}\n',
        ]
    )

    ack = asyncio.run(transport.write(b'{"id":1,"cmd":"ping"}\n'))

    assert ack == Ack(1, True)
    assert transport.writes == [b'{"id":1,"cmd":"ping"}\n']
    assert _collect_lines(asyncio.run(_collect(transport.read_lines()))) == [
        TickEvent(7, False, False, 0, 0, 1000)
    ]


def test_mock_can_model_starvation_garbage_boot_and_disconnect() -> None:
    transport = MockTransport(
        [
            b"garbage\n",
            b'{"ev":"boot","fw":"1.0.0","t":12}\n',
            None,
        ]
    )

    lines = asyncio.run(_collect(transport.read_lines()))

    assert lines == [b"garbage\n", b'{"ev":"boot","fw":"1.0.0","t":12}\n']
    assert not transport.connected
    assert [BootEvent("1.0.0", 12)] == [
        event for event in _collect_lines(lines) if isinstance(event, BootEvent)
    ]


def test_write_times_out_when_no_matching_ack_arrives() -> None:
    transport = MockTransport([b'{"id":2,"ok":true}\n'])

    with pytest.raises(AckTimeout, match="1"):
        asyncio.run(transport.write(b'{"id":1,"cmd":"ping"}\n'))


def test_serial_preserves_events_received_before_the_matching_ack() -> None:
    serial = _FakeSerial(
        [
            b'{"ev":"boot","fw":"1.0.0","t":12}\n{"id":1,"ok":true}\n',
        ]
    )
    transport = SerialTransport("COM_TEST", serial_factory=lambda **_: serial)

    assert asyncio.run(transport.write(b'{"id":1,"cmd":"ping"}\n')) == Ack(1, True)
    assert asyncio.run(_collect(transport.read_lines())) == [
        b'{"ev":"boot","fw":"1.0.0","t":12}\n'
    ]


def test_serial_preserves_events_received_after_the_matching_ack() -> None:
    serial = _FakeSerial(
        [
            b'{"id":1,"ok":true}\n{"ev":"boot","fw":"1.0.0","t":12}\n',
        ]
    )
    transport = SerialTransport("COM_TEST", serial_factory=lambda **_: serial)

    assert asyncio.run(transport.write(b'{"id":1,"cmd":"ping"}\n')) == Ack(1, True)
    assert asyncio.run(_collect(transport.read_lines())) == [
        b'{"ev":"boot","fw":"1.0.0","t":12}\n'
    ]


def test_serial_assembles_three_chunks_into_one_frame() -> None:
    serial = _FakeSerial(
        [
            b'{"ev":"tick","dial":7,',
            b'"relay":false,"armed":false,',
            b'"lease_ms":0,"btns":0,"t":1000}\n',
        ]
    )
    transport = SerialTransport("COM_TEST", serial_factory=lambda **_: serial)

    lines = asyncio.run(_collect(transport.read_lines()))

    assert lines == [
        b'{"ev":"tick","dial":7,"relay":false,"armed":false,"lease_ms":0,"btns":0,"t":1000}\n'
    ]
    assert _collect_lines(lines) == [TickEvent(7, False, False, 0, 0, 1000)]


def test_serial_discards_only_an_overflowed_line_and_recovers() -> None:
    serial = _FakeSerial([b"x" * 200 + b"\nvalid\n"])
    transport = SerialTransport("COM_TEST", serial_factory=lambda **_: serial)

    assert asyncio.run(_collect(transport.read_lines())) == [b"valid\n"]
    assert transport.overflow_count == 1


def _collect_lines(lines: list[bytes]) -> list[Ack | BootEvent | TickEvent | None]:
    from airgap.protocol import decode

    return [decode(line) for line in lines]


class _FakeSerial:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.is_open = True
        self.written: list[bytes] = []

    @property
    def in_waiting(self) -> int:
        if self._chunks:
            return len(self._chunks[0])
        self.is_open = False
        return 0

    def read(self, _: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def write(self, frame: bytes) -> int:
        self.written.append(frame)
        return len(frame)

    def close(self) -> None:
        self.is_open = False
