"""Contract tests for the shared closed vocabularies."""

import ast
from enum import StrEnum
from pathlib import Path

import pytest

from airgap.vocab import (
    AckErrorCode,
    AuditEvent,
    CommandName,
    DecidedBy,
    EventName,
    LedState,
    PolicyAction,
    TonePattern,
    Verdict,
)

SRC = Path(__file__).parents[1] / "src" / "airgap"

VOCABULARY_TYPES = (
    CommandName,
    AckErrorCode,
    EventName,
    LedState,
    TonePattern,
    Verdict,
    DecidedBy,
    AuditEvent,
    PolicyAction,
)


@pytest.mark.parametrize(
    "enum_type",
    VOCABULARY_TYPES,
)
def test_closed_vocabulary_is_a_strenum(enum_type: type[StrEnum]) -> None:
    assert issubclass(enum_type, StrEnum)


def test_no_other_module_declares_an_enum() -> None:
    offenders: list[str] = []
    for path in SRC.glob("*.py"):
        if path.name == "vocab.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {
                base.id if isinstance(base, ast.Name) else base.attr
                for base in node.bases
                if isinstance(base, (ast.Name, ast.Attribute))
            }
            if base_names & {"Enum", "IntEnum", "StrEnum"}:
                offenders.append(f"{path.relative_to(SRC.parent)}:{node.name}")

    assert not offenders, f"closed vocabularies must live in vocab.py: {offenders}"
