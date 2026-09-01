"""Fail-closed demo preflight: Postgres, serial, relay, LED, and servo."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TextIO

from sqlalchemy import create_engine, text

from airgap.protocol import (
    BootEvent,
    Command,
    FlagCommand,
    LedCommand,
    PingCommand,
    RelayCommand,
    decode,
)
from airgap.supervisor import Supervisor, SupervisorRejection
from airgap.transport import AckTimeout, SerialTransport, Transport
from airgap.vocab import LedState

DEVICE_ABSENT = "Arduino serial device is absent"
NO_BOOT = "no boot frame after serial open (UNO typically resets on open)"
BOOT_WAIT_S = 3.0
_POSTGRES_PREFIX = "postgresql+pg8000://"


class DeviceAbsent(OSError):
    """Raised when the Arduino is not on a serial port we can open."""


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    message: str


def check_postgres(url: str | None) -> Check:
    """Fail closed when the URL is missing, mis-prefixed, or unreachable."""
    if url is None or url == "":
        return Check("Postgres", False, "DATABASE_URL is missing")
    if not url.startswith(_POSTGRES_PREFIX):
        return Check(
            "Postgres",
            False,
            "DATABASE_URL must use postgresql+pg8000://",
        )
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        return Check("Postgres", False, f"unreachable: {exc}")
    finally:
        engine.dispose()
    return Check("Postgres", True, "reachable")


def default_serial_port() -> str | None:
    env = os.environ.get("AIRGAP_SERIAL_PORT")
    if env:
        return env
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    ports = list(list_ports.comports())
    for port in ports:
        blob = f"{port.device} {port.description} {port.manufacturer or ''}"
        if "arduino" in blob.lower():
            return port.device
    if len(ports) == 1:
        return ports[0].device
    return None


def open_serial_transport() -> Transport:
    """Open the configured (or detected) port. Absence is a hard failure."""
    port = default_serial_port()
    if port is None:
        raise DeviceAbsent(DEVICE_ABSENT)
    try:
        return SerialTransport(port)
    except Exception as exc:
        raise DeviceAbsent(f"{DEVICE_ABSENT}: {exc}") from exc


async def _send(supervisor: Supervisor, command: Command) -> str | None:
    try:
        await supervisor.send(command)
    except (AckTimeout, SupervisorRejection, OSError) as exc:
        return str(exc)
    return None


async def wait_for_boot(
    transport: Transport, *, timeout_s: float = BOOT_WAIT_S
) -> str | None:
    """Drain until a boot event, or fail closed. No time.sleep()."""

    async def _drain() -> bool:
        async for line in transport.read_lines():
            decoded = decode(line)
            if isinstance(decoded, BootEvent):
                return True
        return False

    try:
        found = await asyncio.wait_for(_drain(), timeout=timeout_s)
    except TimeoutError:
        return NO_BOOT
    except Exception as exc:
        return f"{NO_BOOT}: {exc}"
    if not found:
        return NO_BOOT
    return None


def _no_boot_checks(message: str) -> list[Check]:
    return [
        Check("Serial", False, message),
        Check("Relay", False, "cannot verify: no boot"),
        Check("LED", False, "cannot verify: no boot"),
        Check("Servo", False, "cannot verify: no boot"),
    ]


async def probe_hardware(
    transport: Transport, *, boot_timeout_s: float = BOOT_WAIT_S
) -> list[Check]:
    """Command the device through the Supervisor. No check may skip to green."""
    boot_err = await wait_for_boot(transport, timeout_s=boot_timeout_s)
    if boot_err is not None:
        return _no_boot_checks(boot_err)
    supervisor = Supervisor(transport)
    ping_err = await _send(supervisor, PingCommand(id=1))
    led_err = await _send(supervisor, LedCommand(id=2, state=LedState.AMBER))
    if led_err is None:
        off_err = await _send(supervisor, LedCommand(id=3, state=LedState.OFF))
        led_err = off_err
    servo_err = await _send(supervisor, FlagCommand(id=4, up=True))
    # closed=false is ungated and is the safe-state command. Never close here.
    relay_err = await _send(supervisor, RelayCommand(id=5, closed=False))
    return [
        Check(
            "Serial",
            ping_err is None,
            "ping acked" if ping_err is None else ping_err,
        ),
        Check(
            "Relay",
            relay_err is None,
            "open command acked" if relay_err is None else relay_err,
        ),
        Check(
            "LED",
            led_err is None,
            "amber/off acked" if led_err is None else led_err,
        ),
        Check(
            "Servo",
            servo_err is None,
            "flag-up acked" if servo_err is None else servo_err,
        ),
    ]


def hardware_absent_checks(message: str = DEVICE_ABSENT) -> list[Check]:
    """Relay/LED/servo fail red when the board is missing; they never skip green."""
    return [
        Check("Serial", False, message),
        Check("Relay", False, "cannot verify: device absent"),
        Check("LED", False, "cannot verify: device absent"),
        Check("Servo", False, "cannot verify: device absent"),
    ]


def collect_checks(
    *,
    database_url: str | None | object = ...,
    postgres: Check | None = None,
    open_transport: Callable[[], Transport] | None = None,
) -> list[Check]:
    if postgres is None:
        url = (
            os.environ.get("DATABASE_URL")
            if database_url is ...
            else database_url
        )
        if url is not None and not isinstance(url, str):
            raise TypeError("database_url must be str or None")
        postgres = check_postgres(url)
    opener = open_transport if open_transport is not None else open_serial_transport
    transport: Transport | None = None
    try:
        transport = opener()
        hardware = asyncio.run(probe_hardware(transport))
    except DeviceAbsent as exc:
        hardware = hardware_absent_checks(str(exc) or DEVICE_ABSENT)
    except Exception as exc:
        hardware = hardware_absent_checks(f"{DEVICE_ABSENT}: {exc}")
    finally:
        if transport is not None:
            transport.close()
    return [postgres, *hardware]


def render_summary(checks: Sequence[Check], out: TextIO) -> None:
    out.write("Airgap preflight\n")
    for check in checks:
        mark = "GREEN" if check.ok else "RED"
        out.write(f"{mark:5}  {check.name:<8}  {check.message}\n")
    out.write("\n")
    if all(check.ok for check in checks):
        out.write("OK\n")
    else:
        out.write("FAILED\n")


def run_preflight(
    *,
    database_url: str | None | object = ...,
    postgres: Check | None = None,
    open_transport: Callable[[], Transport] | None = None,
    stdout: TextIO | None = None,
) -> int:
    out = sys.stdout if stdout is None else stdout
    checks = collect_checks(
        database_url=database_url,
        postgres=postgres,
        open_transport=open_transport,
    )
    render_summary(checks, out)
    return 0 if all(check.ok for check in checks) else 1


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    return run_preflight()


if __name__ == "__main__":
    raise SystemExit(main())
