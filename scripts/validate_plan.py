"""Validate the ticket graph, and check PLAN.md still agrees with it.

    python scripts/validate_plan.py

Exit 0 if the plan is sound. Exit 1 with a specific complaint otherwise.

Two things to know before editing this file.

**Standard library only, deliberately.** This is the pre-dispatch gate named in
`PLAN.md` section 9 and `ORCHESTRATOR_PROMPT.md` step 0, which means it runs on a
bare checkout -- before AIR-1 has created `pyproject.toml`, and therefore before
`uv sync` can install anything. An earlier version imported PyYAML and could not
run at the moment it was most needed. Do not reintroduce that. The YAML subset
parser below exists for exactly this reason; when PyYAML *is* importable it is
used as a cross-check, never as the primary path.

**It reads PLAN.md, not just tickets.yaml.** The first version validated the
ticket file alone while the plan claimed its tables were "machine-verified" --
so the tables could drift and CI would stay green. That is the P2 failure mode
the plan is named after. Both tables in section 5 are now checked against the
graph.
"""

from __future__ import annotations

import functools
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TICKETS = ROOT / "docs" / "tickets" / "tickets.yaml"
PLAN = ROOT / "docs" / "PLAN.md"

TIERS = {"mechanical", "standard", "design", "safety-critical"}

# Tier -> (Codex 5.6 model, effort). PLAN.md section 9 is the human-readable
# copy and is checked against this dict, so the two cannot drift apart.
TIER_DISPATCH = {
    "mechanical": ("Luna", "low"),
    "standard": ("Terra", "medium"),
    "design": ("Sol", "high"),
    "safety-critical": ("Sol", "xhigh"),
}
REQUIRED_SECTIONS = ("## Context", "## Scope", "## Acceptance criteria", "## Verification")

problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


# --------------------------------------------------------------------------
# YAML subset parser
# --------------------------------------------------------------------------
# Handles exactly what tickets.yaml uses and rejects everything else loudly:
#   tickets:
#     - id: AIR-1
#       tier: mechanical
#       depends_on: [AIR-2, AIR-3]
#       human: true
#       reads: ["a/b.md"]
#       body: |
#         free text
#
# A parser that silently mishandles a construct is worse than one that refuses
# it, because the graph it produces would look plausible.


class ParseError(Exception):
    pass


def _scalar(raw: str) -> object:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part) for part in inner.split(",")]
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    if raw == "null" or raw == "~":
        return None
    return raw


def parse_tickets(text: str) -> list[dict]:
    lines = text.splitlines()
    tickets: list[dict] = []
    current: dict | None = None
    i = 0
    seen_root = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        indent = len(line) - len(line.lstrip())

        if indent == 0:
            if stripped == "tickets:":
                seen_root = True
                i += 1
                continue
            # Top-level scalar metadata (version, project, team_key). Recorded
            # nowhere; it exists for the orchestrator, not for the graph.
            if ":" in stripped and not seen_root:
                i += 1
                continue
            raise ParseError(f"line {i + 1}: unexpected content at column 0: {stripped!r}")

        if not seen_root:
            raise ParseError(f"line {i + 1}: content before `tickets:`")

        if stripped.startswith("- "):
            if indent != 2:
                raise ParseError(f"line {i + 1}: list item at indent {indent}, expected 2")
            current = {}
            tickets.append(current)
            stripped = stripped[2:]
            indent = 4
        elif indent != 4:
            raise ParseError(f"line {i + 1}: key at indent {indent}, expected 4")

        if current is None:
            raise ParseError(f"line {i + 1}: key outside any list item")

        if ":" not in stripped:
            raise ParseError(f"line {i + 1}: not a `key: value` pair -- {stripped!r}")

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if value in ("|", "|-", ">"):
            if value == ">":
                raise ParseError(f"line {i + 1}: folded scalars (`>`) are not supported; use `|`")
            body: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) < 6:
                    break
                body.append(nxt[6:] if len(nxt) >= 6 else "")
                i += 1
            while body and not body[-1].strip():
                body.pop()
            current[key] = "\n".join(body)
            continue

        current[key] = _scalar(value)
        i += 1

    if not seen_root:
        raise ParseError("no `tickets:` key found")
    return tickets


def cross_check_with_pyyaml(text: str, parsed: list[dict]) -> None:
    """If PyYAML happens to be installed, confirm the hand parser agrees with it.

    Never required. This is what stops the parser above from quietly drifting
    away from real YAML as tickets.yaml grows.
    """
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return
    reference = yaml.safe_load(text)["tickets"]
    if len(reference) != len(parsed):
        fail(f"parser disagrees with PyYAML: {len(parsed)} tickets vs {len(reference)}")
        return
    for ref, got in zip(reference, parsed):
        for key in set(ref) | set(got):
            a, b = ref.get(key), got.get(key)
            if isinstance(a, str) and isinstance(b, str):
                a, b = a.strip(), b.strip()
            if a != b:
                fail(f"parser disagrees with PyYAML on {ref.get('id')}.{key}")


# --------------------------------------------------------------------------
# PLAN.md tables
# --------------------------------------------------------------------------

TICKET_RE = re.compile(r"AIR-\d+")


def _row_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def plan_edges(plan: str) -> dict[str, set[str]] | None:
    """Parse the section 5 'Ticket | Depends on | Because' table."""
    edges: dict[str, set[str]] = {}
    in_table = False
    for line in plan.splitlines():
        cells = _row_cells(line) if line.strip().startswith("|") else None
        if cells and len(cells) == 3 and cells[0] == "Ticket" and cells[1] == "Depends on":
            in_table = True
            continue
        if in_table:
            if not cells or len(cells) != 3:
                break
            if set(cells[0]) <= set("-: "):
                continue
            ids = TICKET_RE.findall(cells[0])
            if len(ids) != 1:
                fail(f"PLAN.md section 5 dependency row names {len(ids)} tickets: {cells[0]!r}")
                continue
            edges[ids[0]] = set(TICKET_RE.findall(cells[1]))
    return edges or None


def check_dispatch_table(plan: str) -> None:
    """Section 9's tier -> model/effort table must match TIER_DISPATCH."""
    seen: dict[str, tuple[str, str]] = {}
    in_table = False
    for line in plan.splitlines():
        cells = _row_cells(line) if line.strip().startswith("|") else None
        if cells and len(cells) >= 4 and cells[0] == "Tier" and cells[2] == "Codex model":
            in_table = True
            continue
        if in_table:
            if not cells or len(cells) < 4:
                break
            if set(cells[0]) <= set("-: "):
                continue
            tier = cells[0].strip("`")
            model = cells[2].replace("*", "").replace("5.6", "").strip()
            effort = cells[3].strip("`* ")
            seen[tier] = (model, effort)
    if not seen:
        fail("PLAN.md: could not find the section 9 dispatch table")
        return
    for tier, expected in TIER_DISPATCH.items():
        got = seen.get(tier)
        if got is None:
            fail(f"PLAN.md section 9 has no row for tier {tier!r}")
        elif got != expected:
            fail(f"PLAN.md section 9 dispatches {tier!r} to {got}, script says {expected}")


def plan_milestones(plan: str) -> list[tuple[str, list[str], int, int]] | None:
    """Parse the section 12 estimates table.

    Returns (milestone, ticket ids, agent sessions, human gates) per row. The
    session cell reads like "7 + 3 adversarial", so every integer in it counts.
    """
    rows: list[tuple[str, list[str], int, int]] = []
    in_table = False
    for line in plan.splitlines():
        cells = _row_cells(line) if line.strip().startswith("|") else None
        if cells and len(cells) >= 4 and cells[0] == "Milestone" and cells[1] == "New tickets":
            in_table = True
            continue
        if in_table:
            if not cells or len(cells) < 4:
                break
            if set(cells[0]) <= set("-: "):
                continue
            if not re.fullmatch(r"M\d+", cells[0]):
                continue
            sessions = sum(int(n) for n in re.findall(r"\d+", cells[2]))
            gates = sum(int(n) for n in re.findall(r"\d+", cells[3]))
            rows.append((cells[0], TICKET_RE.findall(cells[1]), sessions, gates))
    return rows or None


def plan_waves(plan: str) -> list[list[str]] | None:
    """Parse the section 5 'Wave | Tickets | Parallel' table."""
    waves: list[list[str]] = []
    in_table = False
    for line in plan.splitlines():
        cells = _row_cells(line) if line.strip().startswith("|") else None
        if cells and len(cells) >= 3 and cells[0] == "Wave" and cells[1] == "Tickets":
            in_table = True
            continue
        if in_table:
            if not cells or len(cells) < 3:
                break
            if set(cells[0]) <= set("-: "):
                continue
            waves.append(sorted(TICKET_RE.findall(cells[1])))
    return waves or None


# --------------------------------------------------------------------------
# Stale-phrase sweep
# --------------------------------------------------------------------------
# Every entry here is a defect that actually shipped and was actually fixed.
# Three review rounds found the same failure twice each: a contract is corrected
# in one file and its sibling keeps the old sentence. The reviews caught those.
# This catches them the next time, before dispatch, for free.
#
# `docs/reviews/` is excluded because a disposition has to quote the defect it
# closed. Add an entry whenever a sweep removes a phrase; the cost is one line.

STALE_PHRASES: list[tuple[str, str]] = [
    ("as `denied` with reason `lease_expired`",
     "spec/02 Rule 4c: lease_expired never denies a request. Plan review 02 C1"),
    ("resolve any pending request as `denied`",
     "spec/02 Rule 4c: a stray device event must not resolve a queued request"),
    ("SEE DASHBOARD",
     "the reader is a role; the dashboard is M4 and does not exist at M2. Plan review 02 I1"),
    ("the eight commands",
     "spec/01 lists nine. Plan review 01 C1"),
    ("low and medium risk only",
     "AIR-18 puts the reader in M2, so high risk is in the MVP. Plan review 02 I1"),
    ("stops** immediately on resolution",
     "spec/02 Rule 4b: renewal never stops on verdict. Plan review 01 C1"),
]

SWEEP_SKIP = ("docs/reviews", ".git", ".venv", "node_modules")

# A document explaining that a phrase was removed has to quote the phrase. That
# is the documentation working, not a regression -- the same distinction that
# rescoped the `/decide` lint in plan review 01 I1. Put `stale-ok` anywhere in
# the paragraph and the whole paragraph is exempt, because a retraction runs to
# several sentences and often quotes the phrase more than once. A paragraph is a
# run of non-blank lines. The marker is greppable, so "what are we deliberately
# still quoting?" stays a one-command question.
SWEEP_EXEMPT = "stale-ok"


def _exempt_lines(lines: list[str]) -> set[int]:
    """1-indexed line numbers inside a paragraph containing the marker."""
    exempt: set[int] = set()
    start = 0
    for i in range(len(lines) + 1):
        if i == len(lines) or not lines[i].strip():
            block = lines[start:i]
            if any(SWEEP_EXEMPT in line for line in block):
                exempt.update(range(start + 1, i + 1))
            start = i + 1
    return exempt


def stale_phrase_sweep() -> None:
    for path in sorted(ROOT.rglob("*.md")) + sorted(ROOT.rglob("*.yaml")):
        rel = path.relative_to(ROOT).as_posix()
        if any(part in rel for part in SWEEP_SKIP):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        exempt = _exempt_lines(lines)
        for phrase, why in STALE_PHRASES:
            if phrase not in text:
                continue
            for n, line in enumerate(lines, 1):
                if phrase in line and n not in exempt:
                    fail(f"{rel}:{n} contains removed phrase {phrase!r} -- {why}")


# --------------------------------------------------------------------------


def main() -> int:
    try:
        tickets = parse_tickets(TICKETS.read_text(encoding="utf-8"))
    except ParseError as exc:
        print(f"PLAN VALIDATION FAILED\n\n  - tickets.yaml: {exc}", file=sys.stderr)
        return 1

    cross_check_with_pyyaml(TICKETS.read_text(encoding="utf-8"), tickets)
    stale_phrase_sweep()

    by_id = {t["id"]: t for t in tickets}
    if len(by_id) != len(tickets):
        fail("duplicate ticket ids")

    for t in tickets:
        tid = t.get("id", "<no id>")
        if t.get("tier") not in TIERS:
            fail(f"{tid}: tier {t.get('tier')!r} not in {sorted(TIERS)}")
        if not t.get("reads"):
            fail(f"{tid}: no `reads` list -- a cold session needs to be told what to read")
        for path in t.get("reads", []):
            if not (ROOT / path).exists():
                fail(f"{tid}: reads {path!r} which does not exist")
        body = t.get("body", "")
        for section in REQUIRED_SECTIONS:
            if section not in body:
                fail(f"{tid}: body has no {section!r} section")
        for dep in t.get("depends_on", []):
            if dep not in by_id:
                fail(f"{tid}: depends on unknown ticket {dep!r}")

    if problems:
        return report()

    deps = {t["id"]: list(t.get("depends_on", [])) for t in tickets}

    # ---- waves ----
    waves: list[list[str]] = []
    done: set[str] = set()
    remaining = set(deps)
    while remaining:
        ready = sorted(i for i in remaining if all(d in done for d in deps[i]))
        if not ready:
            fail(f"dependency cycle among: {sorted(remaining)}")
            return report()
        waves.append(ready)
        done |= set(ready)
        remaining -= set(ready)

    # ---- PLAN.md must agree ----
    plan = PLAN.read_text(encoding="utf-8")

    stated_edges = plan_edges(plan)
    if stated_edges is None:
        fail("PLAN.md: could not find the section 5 dependency table")
    else:
        for tid in sorted(set(stated_edges) | set(deps)):
            want, got = set(deps.get(tid, [])), stated_edges.get(tid)
            if got is None:
                fail(f"PLAN.md section 5 has no dependency row for {tid}")
            elif want != got:
                fail(
                    f"{tid}: tickets.yaml depends on {sorted(want) or 'nothing'}, "
                    f"PLAN.md section 5 says {sorted(got) or 'nothing'}"
                )
        for tid in sorted(set(stated_edges) - set(deps)):
            fail(f"PLAN.md section 5 has a row for {tid}, which is not in tickets.yaml")

    stated_waves = plan_waves(plan)
    if stated_waves is None:
        fail("PLAN.md: could not find the section 5 wave table")
    elif stated_waves != waves:
        fail("PLAN.md wave table does not match the computed schedule (correct table printed below)")

    human = {t["id"] for t in tickets if t.get("human")}
    critical_tier = {t["id"] for t in tickets if t["tier"] == "safety-critical"}
    budget = len(tickets) - len(human) + len(critical_tier)

    check_dispatch_table(plan)

    milestones = plan_milestones(plan)
    if milestones is None:
        fail("PLAN.md: could not find the section 12 estimates table")
    else:
        placed: dict[str, str] = {}
        for name, ids, _, _ in milestones:
            for tid in ids:
                if tid in placed:
                    fail(f"PLAN.md section 12: {tid} appears in both {placed[tid]} and {name}")
                placed[tid] = name
        for tid in sorted(set(deps) - set(placed)):
            fail(f"PLAN.md section 12: {tid} is in no milestone row")
        for tid in sorted(set(placed) - set(deps)):
            fail(f"PLAN.md section 12: milestone row names {tid}, not in tickets.yaml")

        stated_sessions = sum(m[2] for m in milestones)
        if stated_sessions != budget:
            fail(
                f"PLAN.md section 12 sessions sum to {stated_sessions}, "
                f"but the graph gives a budget of {budget}"
            )
        stated_gates = sum(m[3] for m in milestones)
        if stated_gates != len(milestones):
            fail(
                f"PLAN.md section 12 has {len(milestones)} milestones "
                f"but {stated_gates} human gates; expected one each"
            )

    @functools.lru_cache(maxsize=None)
    def depth(node: str) -> int:
        return 1 + max((depth(d) for d in deps[node]), default=0)

    tail = max(deps, key=depth)
    path = [tail]
    while deps[path[-1]]:
        path.append(max(deps[path[-1]], key=depth))
    path.reverse()

    agent_peak = max(len([w for w in wave if w not in human]) for wave in waves)

    if problems:
        print("\ncomputed wave table:\n", file=sys.stderr)
        for i, wave in enumerate(waves, 1):
            marks = ", ".join(w + (" (human)" if w in human else "") for w in wave)
            agents = len([w for w in wave if w not in human])
            print(f"| {i} | {marks} | {agents} | |", file=sys.stderr)
        return report()

    print(f"{len(tickets)} tickets, graph is acyclic")
    print("PLAN.md section 5 agrees with tickets.yaml on every edge and every wave")
    print("PLAN.md section 12 places every ticket once and its arithmetic checks out")
    print(f"stale-phrase sweep clean ({len(STALE_PHRASES)} known-removed phrases)")
    print("PLAN.md section 9 dispatch table matches the tier mapping\n")
    print("WAVES")
    for i, wave in enumerate(waves, 1):
        marks = ", ".join(w + (" (human)" if w in human else "") for w in wave)
        print(f"  {i:>2}: {marks}")
    print(f"\ndepth: {len(waves)} waves")
    print(f"peak concurrent agent sessions: {agent_peak}")
    print(f"critical path: {' -> '.join(path)}")
    print(f"safety-critical (need an adversarial session): {sorted(critical_tier)}")
    print(f"human-only (never dispatch): {sorted(human)}")

    budget = len(tickets) - len(human) + len(critical_tier)
    print(f"\nsession budget: {budget} base + rework")
    return 0


def report() -> int:
    print("PLAN VALIDATION FAILED\n", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
