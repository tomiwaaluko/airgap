"""Keep the frozen contract vocabularies synchronized with ``airgap.vocab``."""

import re
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

ROOT = Path(__file__).parents[1]
VOCAB_PATH = Path("src/airgap/vocab.py")
SPEC_01 = Path("docs/spec/01-serial-protocol.md")
SPEC_02 = Path("docs/spec/02-supervisor.md")
SPEC_05 = Path("docs/spec/05-data-model.md")

INLINE_VALUE = re.compile(r"`([a-z][a-z0-9_]*)`")
CODE_SPAN = re.compile(r"`([^`]+)`")
JSON_FIELD = r'"{field}"\s*:\s*"([a-z][a-z0-9_]*)"'
NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _read(path: Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _fenced_json(text: str) -> list[str]:
    return re.findall(r"```(?:json|jsonc)\s*\n(.*?)```", text, flags=re.DOTALL)


def _table_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def _json_values(chunks: list[str], field: str) -> set[str]:
    pattern = re.compile(JSON_FIELD.format(field=re.escape(field)))
    return {match for chunk in chunks for match in pattern.findall(chunk)}


def _paragraph(text: str, marker: str) -> str:
    start = text.index(marker)
    end = text.find("\n\n", start)
    value_start = start + len(marker)
    return text[value_start:] if end == -1 else text[value_start:end]


def _line_after(text: str, marker: str) -> str:
    line = next(line for line in text.splitlines() if marker in line)
    return line.split(marker, 1)[1]


def _inline_values(text: str) -> set[str]:
    return set(INLINE_VALUE.findall(text))


def _table_notes(text: str, column: str) -> str:
    for cells in _table_rows(text):
        if cells and cells[0] == column:
            return cells[-1]
    raise AssertionError(f"table row {column!r} not found")


def _serial_vocabularies() -> dict[type[StrEnum], set[str]]:
    text = _read(SPEC_01)
    table_text = "\n".join(" | ".join(row) for row in _table_rows(text))
    json_and_tables = [*_fenced_json(text), table_text]
    return {
        CommandName: _json_values(json_and_tables, "cmd"),
        AckErrorCode: _inline_values(_paragraph(text, "`err` is one of:")),
        EventName: _json_values(_fenced_json(text), "ev"),
        LedState: _inline_values(_line_after(text, "`led.state` — one of")),
        TonePattern: _inline_values(
            _line_after(text, "`tone.pattern` — one of").split(". ", 1)[0]
        ),
    }


def _data_model_vocabularies() -> dict[type[StrEnum], set[str]]:
    text = _read(SPEC_05)
    return {
        Verdict: _inline_values(_table_notes(text, "`verdict`")),
        DecidedBy: _inline_values(_table_notes(text, "`decided_by`")),
        AuditEvent: _inline_values(_table_notes(text, "`event`")),
        PolicyAction: _inline_values(_table_notes(text, "`action`")),
    }


def _assert_same_vocabulary(
    enum_type: type[StrEnum], spec_values: set[str], spec_path: Path
) -> None:
    code_values = {member.value for member in enum_type}
    differences = [
        *(
            f"{term!r} appears in {spec_path.as_posix()} but is absent from "
            f"{VOCAB_PATH.as_posix()}"
            for term in sorted(spec_values - code_values)
        ),
        *(
            f"{term!r} appears in {VOCAB_PATH.as_posix()} but is absent from "
            f"{spec_path.as_posix()}"
            for term in sorted(code_values - spec_values)
        ),
    ]
    assert not differences, "\n".join(differences)


@pytest.mark.parametrize(
    ("enum_type", "spec_values"),
    _serial_vocabularies().items(),
    ids=lambda item: item.__name__ if isinstance(item, type) else None,
)
def test_serial_vocabulary_matches_code(
    enum_type: type[StrEnum], spec_values: set[str]
) -> None:
    _assert_same_vocabulary(enum_type, spec_values, SPEC_01)


@pytest.mark.parametrize(
    ("enum_type", "spec_values"),
    _data_model_vocabularies().items(),
    ids=lambda item: item.__name__ if isinstance(item, type) else None,
)
def test_data_model_vocabulary_matches_code(
    enum_type: type[StrEnum], spec_values: set[str]
) -> None:
    _assert_same_vocabulary(enum_type, spec_values, SPEC_05)


def _supervisor_command_references(text: str) -> set[str]:
    references: set[str] = set()
    table_kind: str | None = None
    for cells in _table_rows(text):
        if cells[0] in {"Field", "Limit", "Step"}:
            table_kind = cells[0]
            continue
        if table_kind in {"Field", "Limit"}:
            references.update(
                value.split(".", 1)[0].split("(", 1)[0]
                for value in CODE_SPAN.findall(cells[0])
            )
        elif table_kind == "Step" and len(cells) > 1:
            references.update(
                re.findall(r"(?:Send|with) `([a-z][a-z0-9_]*)", cells[1])
            )
    return references


def _assert_known_references(
    values: set[str], enum_type: type[StrEnum], description: str
) -> None:
    known = {member.value for member in enum_type}
    unknown = values - known
    messages = [
        f"{term!r} is used as {description} in {SPEC_02.as_posix()} but is absent "
        f"from {VOCAB_PATH.as_posix()}"
        for term in sorted(unknown)
    ]
    assert not messages, "\n".join(messages)


def test_supervisor_command_count_matches_names() -> None:
    text = _read(SPEC_02)
    match = re.search(r"Only the ([a-z]+) commands", text)
    assert match is not None, f"command count missing from {SPEC_02.as_posix()}"
    declared = NUMBER_WORDS.get(match.group(1))
    names = {member.value for member in CommandName}

    assert declared == len(names), (
        f"{SPEC_02.as_posix()} declares {match.group(1)!r} commands, but "
        f"{VOCAB_PATH.as_posix()} defines {len(names)} names: {sorted(names)}"
    )


def test_supervisor_command_references_are_known() -> None:
    references = _supervisor_command_references(_read(SPEC_02))
    assert references, f"no command references parsed from {SPEC_02.as_posix()}"
    _assert_known_references(references, CommandName, "a command name")


def test_supervisor_verdict_references_are_known() -> None:
    text = _read(SPEC_02)
    spec_verdicts = _data_model_vocabularies()[Verdict]
    references = _inline_values(text) & spec_verdicts
    references.update(re.findall(r'verdict=["`]([a-z][a-z0-9_]*)', text))
    references.update(
        re.findall(r"verdict is already `([a-z][a-z0-9_]*)`", text)
    )
    assert references, f"no verdict references parsed from {SPEC_02.as_posix()}"
    _assert_known_references(references, Verdict, "a verdict")


def test_supervisor_decided_by_references_are_known() -> None:
    text = _read(SPEC_02)
    references = set(
        re.findall(r'decided_by=["`]*([a-z][a-z0-9_]*)', text)
    )
    assert references, f"no decided_by references parsed from {SPEC_02.as_posix()}"
    _assert_known_references(references, DecidedBy, "a decided_by value")
