"""Contract tests for the pure serial frame codec."""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from airgap.protocol import (
    Ack,
    ArmCommand,
    BootEvent,
    ButtonEvent,
    DisarmCommand,
    FlagCommand,
    FrameTooLong,
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
from airgap.vocab import AckErrorCode, LedState, TonePattern


def _wire(command: object) -> dict[str, object]:
    frame = encode(command)  # type: ignore[arg-type]
    assert frame.endswith(b"\n")
    assert frame.isascii()
    return json.loads(frame[:-1].decode("ascii"))


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (PingCommand(1), {"id": 1, "cmd": "ping"}),
        (LedCommand(2, LedState.AMBER), {"id": 2, "cmd": "led", "state": "amber"}),
        (
            ToneCommand(3, TonePattern.ALERT, 3),
            {"id": 3, "cmd": "tone", "pattern": "alert", "n": 3},
        ),
        (FlagCommand(4, True), {"id": 4, "cmd": "flag", "up": True}),
        (RelayCommand(5, False), {"id": 5, "cmd": "relay", "closed": False}),
        (RelayRenewCommand(6), {"id": 6, "cmd": "relay_renew"}),
        (
            LcdCommand(7, "DROP users_backup", "412 rows - irreversible"),
            {"id": 7, "cmd": "lcd", "l1": "DROP users_backu", "l2": "412 rows - irrev"},
        ),
        (ArmCommand(8, "a91f3c2e"), {"id": 8, "cmd": "arm", "req": "a91f3c2e"}),
        (DisarmCommand(9), {"id": 9, "cmd": "disarm"}),
    ],
)
def test_encode_emits_each_command_shape(
    command: object, expected: dict[str, object]
) -> None:
    assert _wire(command) == expected


@given(
    command_id=st.integers(min_value=1, max_value=65535),
    state=st.sampled_from(list(LedState)),
    pattern=st.sampled_from(list(TonePattern)),
    count=st.integers(min_value=1, max_value=5),
    up=st.booleans(),
    closed=st.booleans(),
    l1=st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=64
    ),
    l2=st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=64
    ),
    req=st.from_regex(r"[0-9a-f]{8}", fullmatch=True),
)
def test_encode_preserves_normalized_command_wire_fields(
    command_id: int,
    state: LedState,
    pattern: TonePattern,
    count: int,
    up: bool,
    closed: bool,
    l1: str,
    l2: str,
    req: str,
) -> None:
    commands_and_expected = [
        (PingCommand(command_id), {"id": command_id, "cmd": "ping"}),
        (
            LedCommand(command_id, state),
            {"id": command_id, "cmd": "led", "state": state.value},
        ),
        (
            ToneCommand(command_id, pattern, count),
            {"id": command_id, "cmd": "tone", "pattern": pattern.value, "n": count},
        ),
        (FlagCommand(command_id, up), {"id": command_id, "cmd": "flag", "up": up}),
        (
            RelayCommand(command_id, closed),
            {"id": command_id, "cmd": "relay", "closed": closed},
        ),
        (RelayRenewCommand(command_id), {"id": command_id, "cmd": "relay_renew"}),
        (
            LcdCommand(command_id, l1, l2),
            {"id": command_id, "cmd": "lcd", "l1": l1[:16], "l2": l2[:16]},
        ),
        (ArmCommand(command_id, req), {"id": command_id, "cmd": "arm", "req": req}),
        (DisarmCommand(command_id), {"id": command_id, "cmd": "disarm"}),
    ]

    for command, expected in commands_and_expected:
        assert _wire(command) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b'{"id":3,"ok":true}\n', Ack(3, True)),
        (
            b'{"id":9,"ok":false,"err":"unknown_cmd"}\n',
            Ack(9, False, AckErrorCode.UNKNOWN_CMD),
        ),
        (
            b'{"ev":"btn","which":"approve","req":"a91f3c2e","t":91043}\n',
            ButtonEvent("approve", "a91f3c2e", 91043),
        ),
        (b'{"ev":"boot","fw":"1.0.0","t":12}\n', BootEvent("1.0.0", 12)),
        (b'{"ev":"lease_expired","t":104220}\n', LeaseExpiredEvent(104220)),
        (
            b'{"ev":"tick","dial":7,"relay":false,"armed":true,"lease_ms":0,"btns":0,"t":92044}\n',
            TickEvent(7, False, True, 0, 0, 92044),
        ),
    ],
)
def test_decode_preserves_each_device_frame_shape(
    line: bytes, expected: object
) -> None:
    assert decode(line) == expected


def test_decode_tolerates_carriage_return() -> None:
    assert decode(b'{"id":3,"ok":true}\r\n') == Ack(3, True)


def test_decode_rejects_command_shaped_input() -> None:
    assert decode(b'{"id":1,"cmd":"ping"}\n') is None


@pytest.mark.parametrize(
    "line",
    [
        b'{"id":3,"ok":true',
        b'{"id":3,"ok":true}\x00',
        b"\xff\xfe\n",
        b'{"ev":"unknown","t":1}\n',
        b'{"id":3,"ok":false}\n',
    ],
)
def test_decode_returns_none_for_invalid_frames(line: bytes) -> None:
    assert decode(line) is None


@settings(max_examples=1000)
@given(st.binary(max_size=256))
def test_decode_never_raises_for_random_bytes(line: bytes) -> None:
    result = decode(line)
    assert result is None or isinstance(
        result, (Ack, ButtonEvent, BootEvent, LeaseExpiredEvent, TickEvent)
    )


def test_encode_rejects_frames_longer_than_physical_limit() -> None:
    with pytest.raises(FrameTooLong):
        encode(PingCommand(int("9" * 201)))


def test_encode_clamps_tone_count_to_device_range() -> None:
    assert _wire(ToneCommand(1, TonePattern.ALERT, 99))["n"] == 5


def test_id_allocator_wraps_after_65535() -> None:
    allocator = IdAllocator(start=65535)

    assert allocator.next() == 65535
    assert allocator.next() == 1
    assert allocator.next() == 2
