# AIR-2 and AIR-7 dispatch blockers — disposition

**Accepted by:** the human project owner in the orchestrator session on
2026-08-23.

**Outcome:** both dispatch blockers are accepted as project defects. The serial
contract moves to v1.2, and pg8000 becomes part of the approved dependency
baseline.

## AIR-2 — directional codec property

The frozen serial contract declared:

```python
decode(line: bytes) -> Ack | Event | None
```

but required `decode(encode(c))` for a `Command c`. The command travels
host-to-device while `decode()` handles device-to-host frames. No implementation
could satisfy both statements without accepting the wrong frame direction.

The accepted correction keeps the directional API intact:

- generated commands are checked by parsing the ASCII JSON emitted by
  `encode()` and comparing normalized wire fields;
- valid Ack/Event fixtures are checked through `decode()`;
- command-shaped input to `decode()` must return `None`.

The sibling sweep updates `docs/spec/01-serial-protocol.md`, AIR-2's canonical
body in `docs/tickets/tickets.yaml`, and `docs/PLAN.md`'s verification table in
the same commit. No merged ticket is stale: AIR-3 already depends on the
unchanged `Ack | Event | None` API, while AIR-4 and AIR-16 do not rely on the
removed impossible property. AIR-2 was blocked and is resumed after this change
merges.

## AIR-7 — missing PostgreSQL DBAPI

AIR-7 must apply an Alembic migration to real PostgreSQL and verify a
database-enforced append-only trigger. SQLAlchemy and Alembic do not include a
PostgreSQL DBAPI, and the frozen dependency baseline omitted one.

The first candidate, `psycopg[binary]`, installed but could not import because
Windows Application Control rejected its bundled native DLL. The accepted
correction therefore adds the pure-Python `pg8000` driver to `AGENTS.md`,
`pyproject.toml`, and the lockfile. It uses the explicit
`postgresql+pg8000://` SQLAlchemy dialect so a bare `postgresql://` URL cannot
silently select the absent legacy psycopg2 driver. CI receives an ephemeral
PostgreSQL 17 service and a matching `DATABASE_URL`; no external database or
secret is required.

AIR-7 was blocked before writing code and is resumed in its original dedicated
Codex CLI thread after this baseline correction merges.
