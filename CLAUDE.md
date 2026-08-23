# CLAUDE.md

## Read `AGENTS.md` first

All project context for this repository lives in **[`AGENTS.md`](AGENTS.md)** —
what Airgap is, the invariants that outrank any individual ticket, the frozen
contract documents, environment commands, conventions, testing obligations, the
definition of done, and the Linear workflow.

`AGENTS.md` is the single source of truth and is kept current. This file exists
only to point at it, so that Claude Code, Codex, and any other agent read the
same context rather than drifting apart.

**Do not duplicate project context here.** If something needs to be added, add it
to `AGENTS.md`.

## Quick reference

```bash
uv sync                   # install
uv run pytest             # test
uv run ruff check . --fix # lint
uv run mypy src/          # types
```

Design intent (why the system is shaped this way):

- `docs/DESIGN.md` — problem, threat model, design decisions, flows, failure analysis

Contracts (frozen — read before coding, do not edit casually):

- `docs/spec/00-overview.md` — system, boundaries, invariants
- `docs/spec/01-serial-protocol.md` — the wire format
- `docs/spec/02-supervisor.md` — safety rules and the relay interlock
- `docs/spec/03-broker-api.md` — HTTP and MCP contract
- `docs/spec/04-firmware.md` — pin map and timers
- `docs/spec/05-data-model.md` — schema and audit chain

Tickets: `docs/tickets/` is the source of truth; Linear mirrors it.
