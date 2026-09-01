# Airgap demo runbook

Follow this file from a cold machine. Do not improvise around it.

This is the **software half** of AIR-15. It does **not** claim the Arduino is
wired, and it does **not** claim M3 / M4 hardware exit. Physical bring-up —
including [`spec/04`](spec/04-firmware.md) checklist items **6** (unplug USB
while the relay is closed) and **7** (kill the host with USB still powered; the
contact must open within 10 s) — is **AIR-5**, a human task. Until AIR-5 is
done, `uv run python scripts/preflight.py` is supposed to exit non-zero.

A stage-fallback recording is produced **after AIR-5**, not now. There is no
video in this repository.

The 60-second narrative is a **consent-channel** `DROP TABLE`. The agent *asked*
and a human had a real out-of-band veto. The action is not made impossible. Do
not send a relay command for it. Do not approve from the dashboard — there is
no approve control, no approve token scope, and no `/decide` endpoint.

## 0. What you need

- Git, [uv](https://docs.astral.sh/uv/), Python 3.14
- PostgreSQL 17 listening locally
- Node 24 only if you want the optional dashboard
- After AIR-5: an Arduino UNO on USB, firmware from `firmware/`, wired per
  [`spec/04`](spec/04-firmware.md) (status LED is **two digital pins**, `D5`
  green and `D6` red — not a PWM RGB LED)

## 1. Clone and install

```text
git clone https://github.com/tomiwaaluko/airgap.git
cd airgap
uv sync
```

## 2. Postgres

Create the role and database, then export an explicit pg8000 URL. Bare
`postgresql://` selects a driver this project does not ship. The connecting
`psql` user must be a superuser (often `postgres`). If the role or database
already exists, skip that statement.

POSIX:

```text
psql -U postgres -h 127.0.0.1 -c "CREATE ROLE airgap WITH LOGIN PASSWORD 'airgap';"
psql -U postgres -h 127.0.0.1 -c "CREATE DATABASE airgap OWNER airgap;"
export DATABASE_URL=postgresql+pg8000://airgap:airgap@127.0.0.1:5432/airgap
uv run alembic upgrade head
```

PowerShell:

```text
psql -U postgres -h 127.0.0.1 -c "CREATE ROLE airgap WITH LOGIN PASSWORD 'airgap';"
psql -U postgres -h 127.0.0.1 -c "CREATE DATABASE airgap OWNER airgap;"
$env:DATABASE_URL = "postgresql+pg8000://airgap:airgap@127.0.0.1:5432/airgap"
uv run alembic upgrade head
```

## 3. Seed

Writes escalate / block policy rows (empty auto-approve envelope — the product
default), one `relay_gated` row that is **not** `auto_approve`, and enough
`requests` / Warden / audit history that `search_decision_history` for
`db.drop_table` returns prior decisions.

```text
uv run python scripts/seed_demo.py
```

If `DATABASE_URL` is missing or not `postgresql+pg8000://`, this exits non-zero
and does not fall back to SQLite.

## 4. Tokens

Mint three secrets and keep them in this shell. Scopes are not interchangeable:

| Variable | Scope | May call |
|---|---|---|
| `AIRGAP_AGENT_TOKEN` | `agent` | `POST /request_approval` only |
| `AIRGAP_UI_TOKEN` | `ui` | reads + `PUT /policies/{pattern}` — **never approve** |
| `AIRGAP_UI_RO_TOKEN` | `ui_ro` | `GET /pending`, `/audit`, `/policies` only |

POSIX:

```text
export AIRGAP_AGENT_TOKEN="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AIRGAP_UI_TOKEN="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AIRGAP_UI_RO_TOKEN="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export BROKER_URL=http://127.0.0.1:8741
export ANTHROPIC_API_KEY="<your key>"
```

PowerShell:

```text
$env:AIRGAP_AGENT_TOKEN = $(uv run python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:AIRGAP_UI_TOKEN = $(uv run python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:AIRGAP_UI_RO_TOKEN = $(uv run python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:BROKER_URL = "http://127.0.0.1:8741"
$env:ANTHROPIC_API_KEY = "<your key>"
```

`scripts/run_broker.py` reads the three tokens from the environment. The
dashboard uses `AIRGAP_UI_TOKEN`. `airgap watch` uses `AIRGAP_UI_RO_TOKEN`.
The MCP process uses `AIRGAP_AGENT_TOKEN`. None of them can resolve a request.

`ANTHROPIC_API_KEY` is for a live Warden. If it is missing, `run_broker.py`
prints a red warning and the human path still works — triage fails closed
to escalate.

## 5. Preflight

```text
uv run python scripts/preflight.py
```

Checks Postgres, the serial port, the relay (open / safe-state command only),
the two-pin LED, and the flag servo. Prints a GREEN / RED summary.

- **No Arduino:** exits non-zero with a red `Arduino serial device is absent`.
  That is the correct result on this software-half ticket.
- **After AIR-5:** set `AIRGAP_SERIAL_PORT` (for example `COM3` or
  `/dev/ttyACM0`). Opening the port resets a typical UNO; preflight waits up
  to 3 s for a `boot` frame before ping. Then `Supervisor.send` — ping, LED
  amber then off, flag up, `relay(closed=false)`. It never closes the contact.
  Passing preflight is not passing bring-up items 6 and 7.

## 6. Hardware (AIR-5 — skip until the board exists)

Do not run the live narrative against a missing device. When the board is in
hand, flash from [`firmware/README.md`](../firmware/README.md) and complete
every item in [`spec/04` bring-up](spec/04-firmware.md), especially:

6. Unplug USB while the relay is closed — the contact must open.
7. Kill the host process with USB still connected — the contact must open
   within 10 s via lease expiry.

Those two are different mechanisms. Passing one says nothing about the other.
They are AIR-5, not this file.

## 7. Broker — bind `127.0.0.1` only

Never `0.0.0.0`. Default port `8741`. Requires AIR-5 for a real
`SerialTransport`; `MockTransport` is for tests, not the stage.

```text
uv run python -c "from airgap.broker import BIND_HOST; print(BIND_HOST)"
```

must print `127.0.0.1`.

Start the broker with the public constructors (Supervisor owns the port; the
LLM never writes to serial). `scripts/run_broker.py` loads seeded policy rows
and resolved `db.drop_table` history into `RequestStore` so the Warden's
`search_decision_history` tool has something to find without pasting a
launcher. It fails closed if `AIRGAP_SERIAL_PORT` is missing. After opening
the port it waits up to 3 s for a `boot` frame to sit in the transport
queue **without consuming it**, so `startup()` can still ack and `serve_link`
still sees the boot.

```text
# POSIX
export AIRGAP_SERIAL_PORT=/dev/ttyACM0
uv run python scripts/run_broker.py
```

```text
# PowerShell
$env:AIRGAP_SERIAL_PORT = "COM3"
uv run python scripts/run_broker.py
```

Bind stays `127.0.0.1` inside `run()`. Requires `DATABASE_URL`, the three
tokens from §4, `AIRGAP_SERIAL_PORT`, and `ANTHROPIC_API_KEY` for a live
Warden. If the Warden is unavailable it fails closed to escalate — the human
path still works.

## 8. MCP `request_approval`

Second terminal. `create_server()` requires `AIRGAP_AGENT_TOKEN`; set the
same values as §4.

POSIX:

```text
export BROKER_URL=http://127.0.0.1:8741
export AIRGAP_AGENT_TOKEN="<same value as §4>"
uv run python -m airgap.mcp_server
```

PowerShell:

```text
$env:BROKER_URL = "http://127.0.0.1:8741"
$env:AIRGAP_AGENT_TOKEN = "<same value as §4>"
uv run python -m airgap.mcp_server
```

Point an MCP client at that process. One-liner for Claude Code / Cursor
(`mcp.json`; same clone directory; token from §4):

```json
{
  "mcpServers": {
    "airgap": {
      "command": "uv",
      "args": ["run", "python", "-m", "airgap.mcp_server"],
      "env": {
        "BROKER_URL": "http://127.0.0.1:8741",
        "AIRGAP_AGENT_TOKEN": "<AIRGAP_AGENT_TOKEN from §4>"
      }
    }
  }
}
```

The tool description is the one in [`spec/03`](spec/03-broker-api.md). The
call **blocks** until a verdict exists. That is the product, not a hang.

Consent-channel demo arguments (no relay):

```json
{
  "tool_name": "db.drop_table",
  "tool_args": {"table": "users_backup"},
  "justification": "cleaning up staging"
}
```

## 9. `airgap watch`

Third terminal. `run_watch()` requires `AIRGAP_UI_RO_TOKEN`; set the same
values as §4. Read the full armed action here — the high-risk LCD is only
the short code plus `SEE READER`.

POSIX:

```text
export BROKER_URL=http://127.0.0.1:8741
export AIRGAP_UI_RO_TOKEN="<same value as §4>"
uv run airgap watch
```

PowerShell:

```text
$env:BROKER_URL = "http://127.0.0.1:8741"
$env:AIRGAP_UI_RO_TOKEN = "<same value as §4>"
uv run airgap watch
```

Read-only. Quit is the only keybinding. There is no approve / deny / never
binding — those stay on the device.

## 10. Optional dashboard (`web/`)

```text
cd web
npm install
```

POSIX: `export AIRGAP_BROKER_URL=http://127.0.0.1:8741` and
`export AIRGAP_UI_TOKEN=...` then `npm run dev`.

PowerShell: `$env:AIRGAP_BROKER_URL = "http://127.0.0.1:8741"` and
`$env:AIRGAP_UI_TOKEN = "..."` then `npm run dev`.

Binds `127.0.0.1:3000`. Holds the `ui` token on the server. **No approve
control.** Do not look for one. Policy edits are the only write, and they
cannot mint `decided_by=human`.

## 11. Sixty-second narrative

Speak this once, in order, while a cold observer watches the desk and
`airgap watch`. Do not narrate the relay. This request is not relay-gated.
`db.drop_table` is high-risk: the LCD does **not** show the DROP. Read the
action on `airgap watch`.

| t | What happens |
|---|---|
| 0 s | The actor calls `request_approval` for `db.drop_table` / `users_backup`. The MCP tool call **blocks**. |
| 2 s | Flag up, LED red (D6), alert tone. LCD line 1 is the per-arm short code; line 2 is `SEE READER`. Read the full action on `airgap watch`. **Relay stays open.** No `relay` command is sent. |
| 8 s | `airgap watch` shows tool, args, justification. The optional dashboard shows the same queue. Neither surface can approve. |
| 20 s | Human presses **APPROVE on the device** (D2). Not the dashboard. Not a token. |
| 22 s | LED green (D5). The blocking call returns `APPROVED: ...`. |
| 60 s | A cold observer can answer: the agent wanted to drop a table, the device held the call, the physical button released it. |

A later, optional beat — **only after AIR-5 items 6 and 7** — is an
enforcement-mode `pump.start` against the seeded `relay_gated` row. That path
does send `relay` / `relay_renew`. Do not substitute it for the DROP TABLE
opener, and do not tell anyone the DROP was prevented by geometry.

## Fallback video

Record a 60-second capture of this narrative **after AIR-5** so a dead board on
stage is not a dead demo. Do not add a placeholder file now.
