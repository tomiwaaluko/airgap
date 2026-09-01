"""Preflight fails closed when the Arduino is missing."""

from __future__ import annotations

import asyncio
import io
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from airgap.protocol import Ack
from airgap.transport import AckTimeout, MockTransport
from airgap.vocab import CommandName

_BOOT = b'{"ev":"boot","fw":"1.0.0","t":12}\n'

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import preflight  # noqa: E402


def _absent() -> MockTransport:
    raise preflight.DeviceAbsent(preflight.DEVICE_ABSENT)


def _stdout() -> io.StringIO:
    return io.StringIO()


def _run(**kwargs: object) -> tuple[int, str]:
    buf = _stdout()
    code = preflight.run_preflight(stdout=buf, **kwargs)  # type: ignore[arg-type]
    return code, buf.getvalue()


def test_preflight_exits_nonzero_when_device_absent() -> None:
    code, text = _run(
        postgres=preflight.Check("Postgres", True, "reachable"),
        open_transport=_absent,
    )

    assert code != 0
    assert preflight.DEVICE_ABSENT in text
    assert "FAILED" in text
    for name in ("Serial", "Relay", "LED", "Servo"):
        line = next(line for line in text.splitlines() if name in line)
        assert line.startswith("RED"), line
        assert "GREEN" not in line.split()[0]


def test_preflight_does_not_skip_actuators_as_green_when_open_fails() -> None:
    checks = preflight.collect_checks(
        postgres=preflight.Check("Postgres", True, "reachable"),
        open_transport=_absent,
    )
    by_name = {check.name: check for check in checks}
    assert by_name["Postgres"].ok is True
    for name in ("Serial", "Relay", "LED", "Servo"):
        assert by_name[name].ok is False
        assert "absent" in by_name[name].message


def test_missing_database_url_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    check = preflight.check_postgres(None)
    assert check.ok is False
    assert "DATABASE_URL" in check.message

    code, text = _run(database_url=None, open_transport=_absent)
    assert code != 0
    assert "DATABASE_URL is missing" in text
    assert "RED" in text


def test_wrong_database_url_prefix_is_red() -> None:
    check = preflight.check_postgres("postgresql://airgap@localhost/airgap")
    assert check.ok is False
    assert "postgresql+pg8000://" in check.message


def test_mock_device_that_acks_is_green_for_hardware() -> None:
    transport = MockTransport(
        [
            _BOOT,
            b'{"id":1,"ok":true}\n',
            b'{"id":2,"ok":true}\n',
            b'{"id":3,"ok":true}\n',
            b'{"id":4,"ok":true}\n',
            b'{"id":5,"ok":true}\n',
        ]
    )
    code, text = _run(
        postgres=preflight.Check("Postgres", True, "reachable"),
        open_transport=lambda: transport,
    )
    assert code == 0
    assert "OK" in text
    assert "ping acked" in text
    frames = [json.loads(frame.decode("ascii")) for frame in transport.writes]
    cmds = [str(frame["cmd"]) for frame in frames]
    assert CommandName.PING.value in cmds
    assert CommandName.LED.value in cmds
    assert CommandName.FLAG.value in cmds
    assert CommandName.RELAY.value in cmds
    relay = [frame for frame in frames if frame.get("cmd") == "relay"]
    assert relay == [{"id": 5, "cmd": "relay", "closed": False}]


def test_mock_device_ping_timeout_fails_serial() -> None:
    class TimeoutTransport(MockTransport):
        async def write(self, frame: bytes) -> Ack:
            raise AckTimeout("command 1 was not acknowledged")

    code, text = _run(
        postgres=preflight.Check("Postgres", True, "reachable"),
        open_transport=lambda: TimeoutTransport([_BOOT]),
    )
    assert code != 0
    assert "Serial" in text
    serial_line = next(line for line in text.splitlines() if "Serial" in line)
    assert serial_line.startswith("RED")


def test_mock_device_that_never_boots_fails_closed() -> None:
    transport = MockTransport(
        [
            b'{"id":1,"ok":true}\n',
            b'{"id":2,"ok":true}\n',
        ]
    )
    code, text = _run(
        postgres=preflight.Check("Postgres", True, "reachable"),
        open_transport=lambda: transport,
    )
    assert code != 0
    assert preflight.NO_BOOT in text
    serial_line = next(line for line in text.splitlines() if "Serial" in line)
    assert serial_line.startswith("RED")
    assert transport.writes == []


def test_silent_transport_boot_wait_times_out() -> None:
    class SilentTransport:
        def __init__(self) -> None:
            self._closed = False

        @property
        def connected(self) -> bool:
            return not self._closed

        async def write(self, frame: bytes) -> Ack:
            raise AckTimeout("silent")

        async def read_lines(self) -> AsyncIterator[bytes]:
            while self.connected:
                await asyncio.sleep(0)
            yield b""

        def close(self) -> None:
            self._closed = True

    err = asyncio.run(preflight.wait_for_boot(SilentTransport(), timeout_s=0.05))
    assert err is not None
    assert "boot" in err.lower()
