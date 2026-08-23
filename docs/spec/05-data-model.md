# Data Model — FROZEN CONTRACT v1

Postgres. SQLAlchemy 2.x declarative models in `src/airgap/models.py`, Alembic
migrations in `migrations/`. SQLite is acceptable for local tests only; nothing
may depend on SQLite-specific behaviour.

## `requests`

The record of something an agent wanted to do.

| Column | Type | Notes |
|---|---|---|
| `id` | `char(8)` PK | lowercase hex, matches `arm.req` on the wire |
| `created_at` | `timestamptz` | server default `now()` |
| `resolved_at` | `timestamptz` NULL | |
| `actor` | `text` | agent identity string, e.g. `claude-code/session-4f2a` |
| `tool_name` | `text` | the tool the actor wanted to call |
| `tool_args` | `jsonb` | verbatim arguments |
| `justification` | `text` | the actor's stated reason |
| `risk_class` | `text` | `low` / `medium` / `high` / `blocked` |
| `verdict` | `text` NULL | `approved` / `denied` / `expired` / `link_lost`. These are peers: a timeout is `expired`, a dropped link is `link_lost`, neither is `denied` with a reason string |
| `decided_by` | `text` NULL | `human` / `policy` / `warden_auto` / `system`. **`human` is written only by the Supervisor after the Rule 4 interlock** (`spec/02` 4a); the HTTP layer rejects it |
| `reason` | `text` NULL | human-readable |
| `dial_at_decision` | `smallint` NULL | autonomy level when decided |
| `latency_ms` | `integer` NULL | created_at → resolved_at |

Index on `(created_at desc)` and on `(tool_name, verdict)`.

## `warden_assessments`

One per request that reached the Warden. Kept separate so the LLM's reasoning is
auditable independently of the outcome.

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `request_id` | `char(8)` FK → `requests.id` | |
| `model` | `text` | exact model id used |
| `risk_class` | `text` | what the Warden proposed |
| `reversible` | `boolean` | |
| `blast_radius` | `text` | free text, e.g. `412 rows` |
| `injection_suspected` | `boolean` | |
| `reasoning` | `text` | the Warden's own words |
| `tool_calls` | `jsonb` | what it looked up before deciding |
| `latency_ms` | `integer` | |
| `created_at` | `timestamptz` | |

## `audit_log`

Append-only, hash-chained. **Nothing may UPDATE or DELETE this table.** Enforced
with a Postgres rule/trigger in the migration, not just by convention.

| Column | Type | Notes |
|---|---|---|
| `seq` | `bigserial` PK | |
| `at` | `timestamptz` | |
| `event` | `text` | `request_created`, `warden_verdict`, `policy_override`, `armed`, `button`, `relay`, `resolved`, `safe_state` |
| `request_id` | `char(8)` NULL | |
| `payload` | `jsonb` | |
| `prev_hash` | `char(64)` | sha256 of the previous row's `row_hash`; genesis is 64 zeros |
| `row_hash` | `char(64)` | `sha256(prev_hash ‖ seq ‖ at_iso ‖ event ‖ request_id ‖ canonical_json(payload))` |

Canonical JSON is: sorted keys, no whitespace, UTF-8. The exact function lives in
`src/airgap/audit.py` and has its own test with a pinned known-good vector, so
that a future refactor cannot silently break the chain.

`verify_chain(from_seq=0) -> tuple[bool, int | None]` returns `(True, None)` or
`(False, first_bad_seq)`.

## `policies`

| Column | Type | Notes |
|---|---|---|
| `tool_pattern` | `text` PK | glob, e.g. `db.drop_*` |
| `min_dial` | `smallint` | escalate to human if dial ≥ this |
| `action` | `text` | `auto_approve` / `escalate` / `block` |
| `updated_at` | `timestamptz` | |
| `updated_by` | `text` | |

**No matching row means `escalate`.** An empty `policies` table therefore sends
every request to a human, which is the correct default and the shipped one
(`DESIGN.md` §12 Q2). There is no configuration in which an unmatched tool is
auto-approved.

**Relay-gated tools are never auto-approved**, whatever their policy row says.
The Supervisor's interlock has no auto-approve branch, so a row setting
`auto_approve` on a relay-gated tool resolves to `escalate` rather than silently
producing a request that can never actuate (`DESIGN.md` D12).

**Policy rows match `tool_name` only, never arguments.** A permissive row on a
broad tool such as `db.execute_sql` auto-approves every possible call to it. Broad
tools must not be given `auto_approve`; this is a convention in v1, not an
enforced constraint, and is tracked as `DESIGN.md` §12 R8.

Policy rows are evaluated **after** the Warden and can only narrow its proposal.
The resolution table:

| Warden says | Policy says | Result |
|---|---|---|
| auto_approve | auto_approve | auto_approve |
| auto_approve | escalate | escalate |
| auto_approve | block | block |
| escalate | auto_approve | **escalate** (never widened) |
| escalate | block | block |
| block | anything | block |

## Retention

Nothing is deleted. `requests` and `warden_assessments` may be archived to cold
storage after 180 days; `audit_log` never is, because deleting a row from a hash
chain invalidates every row after it.
