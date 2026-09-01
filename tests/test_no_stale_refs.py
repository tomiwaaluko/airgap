"""Reject removed implementation surfaces without linting explanatory prose."""

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC_ROOT = ROOT / "src"
TESTS_ROOT = ROOT / "tests"
BROKER_SPEC = ROOT / "docs" / "spec" / "03-broker-api.md"
REMOVED_ROUTES = ("/decide",)
# A probe POST to a missing path is AIR-14. A decorator that serves it is AIR-16.
_ROUTE_REGISTRATION = re.compile(
    r"@(?:app|router)\.(?:get|post|put|patch|delete)\(|"
    r"add_api_route\(|"
    r"APIRouter\("
)


def _python_source_hits(term: str) -> list[str]:
    hits: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        hits.extend(_line_hits(path, term, require_registration=False))
    for path in TESTS_ROOT.rglob("*.py"):
        hits.extend(_line_hits(path, term, require_registration=True))
    return hits


def _line_hits(path: Path, term: str, *, require_registration: bool) -> list[str]:
    hits: list[str] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if term not in line:
            continue
        if require_registration and _ROUTE_REGISTRATION.search(line) is None:
            continue
        location = f"{path.relative_to(ROOT)}:{line_number}"
        hits.append(f"{location}: {line.strip()}")
    return hits


def _markdown_table_hits(path: Path, term: str) -> list[str]:
    hits: list[str] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if line.startswith("|") and line.endswith("|") and term in line:
            hits.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
    return hits


def test_removed_routes_do_not_reappear_in_implementation() -> None:
    failures: list[str] = []
    for route in REMOVED_ROUTES:
        failures.extend(
            f"removed route {route!r} appears in {hit}"
            for hit in _python_source_hits(route)
        )

    assert not failures, "\n".join(failures)


def test_broker_spec_has_no_route_table_row_for_removed_route() -> None:
    failures: list[str] = []
    for route in REMOVED_ROUTES:
        failures.extend(
            f"removed route {route!r} is declared by {hit}"
            for hit in _markdown_table_hits(BROKER_SPEC, route)
        )

    assert not failures, "\n".join(failures)


def test_markdown_sentence_about_removal_is_not_a_route_declaration() -> None:
    route = REMOVED_ROUTES[0]

    assert route in BROKER_SPEC.read_text(encoding="utf-8")
    assert not _markdown_table_hits(BROKER_SPEC, route)
