"""Validate the ticket graph and emit the wave schedule.

Run before dispatching anything, and in CI. This exists because the first time it
was run it caught PLAN.md and tickets.yaml disagreeing about five dependency
edges -- the plan had been revised and the ticket file had not.

    python scripts/validate_plan.py

Exit 0 if the graph is sound. Exit 1 with a specific complaint otherwise.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TICKETS = ROOT / "docs" / "tickets" / "tickets.yaml"
TIERS = {"mechanical", "standard", "design", "safety-critical"}
REQUIRED_SECTIONS = ("## Context", "## Scope", "## Acceptance criteria", "## Verification")

problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


def main() -> int:
    data = yaml.safe_load(TICKETS.read_text(encoding="utf-8"))
    tickets = data["tickets"]
    by_id = {t["id"]: t for t in tickets}

    if len(by_id) != len(tickets):
        fail("duplicate ticket ids")

    for t in tickets:
        tid = t["id"]
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

    # ---- waves ----
    deps = {t["id"]: list(t.get("depends_on", [])) for t in tickets}
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

    @functools.lru_cache(maxsize=None)
    def depth(node: str) -> int:
        return 1 + max((depth(d) for d in deps[node]), default=0)

    tail = max(deps, key=depth)
    path = [tail]
    while deps[path[-1]]:
        path.append(max(deps[path[-1]], key=depth))
    path.reverse()

    human = {t["id"] for t in tickets if t.get("human")}
    critical_tier = {t["id"] for t in tickets if t["tier"] == "safety-critical"}

    print(f"{len(tickets)} tickets, graph is acyclic\n")
    print("WAVES")
    agent_peak = 0
    for i, wave in enumerate(waves, 1):
        agents = [w for w in wave if w not in human]
        agent_peak = max(agent_peak, len(agents))
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
