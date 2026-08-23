"""Host-native behavioral verification for the Arduino sketch."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from airgap.vocab import AckErrorCode, CommandName, EventName, LedState, TonePattern

ROOT = Path(__file__).parents[1]
SKETCH = ROOT / "firmware" / "airgap" / "airgap.ino"
SIMULATOR = ROOT / "tests" / "firmware_sim" / "main.cpp"


@pytest.fixture(scope="session")
def firmware_simulator(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = shutil.which("g++")
    assert compiler is not None, "g++ is required for hardware-free firmware tests"

    output = tmp_path_factory.mktemp("firmware") / "airgap-sim.exe"
    result = subprocess.run(
        [
            compiler,
            "-std=c++11",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{SIMULATOR.parent}",
            str(SIMULATOR),
            "-o",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


@pytest.mark.parametrize(
    "scenario",
    [
        "boot",
        "commands",
        "buttons_disarmed",
        "held_button",
        "held_button_short_press",
        "lease_expiry",
        "late_renew",
        "renew_open",
        "tick",
        "malformed_recovery",
    ],
)
def test_firmware_behavior(firmware_simulator: Path, scenario: str) -> None:
    result = subprocess.run(
        [str(firmware_simulator), scenario],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("CMD", {item.value for item in CommandName}),
        ("ERR", {item.value for item in AckErrorCode}),
        ("EVENT", {item.value for item in EventName}),
        ("LED", {item.value for item in LedState}),
        ("TONE", {item.value for item in TonePattern}),
    ],
)
def test_firmware_vocabulary_matches_shared_contract(
    prefix: str, expected: set[str]
) -> None:
    source = SKETCH.read_text(encoding="utf-8")
    declared = set(
        re.findall(
            rf'constexpr char {prefix}_[A-Z_]+\[\] = "([a-z][a-z0-9_]*)";',
            source,
        )
    )
    assert declared == expected
