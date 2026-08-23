"""Reject removed implementation surfaces without linting explanatory prose."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
IMPLEMENTATION_ROOTS = (ROOT / "src", ROOT / "tests")
BROKER_SPEC = ROOT / "docs" / "spec" / "03-broker-api.md"
REMOVED_ROUTES = ("/" "decide",)


def _python_source_hits(roots: tuple[Path, ...], term: str) -> list[str]:
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if term in line:
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
            for hit in _python_source_hits(IMPLEMENTATION_ROOTS, route)
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
