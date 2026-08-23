# Broker API — FROZEN CONTRACT v1.1

> **v1.1 (2026-08-23), plan review I3.** Added the `ui_ro` scope. The terminal
> reader would otherwise have held a token that can rewrite the policy table.


The Broker holds an agent's tool call open until a human decides. This document
is the contract for both its HTTP surface and the MCP tool built on top of it.

## The blocking design, and the evidence for it

The central bet is that an MCP tool call can hang for minutes without anything
upstream giving up. That was verified before this spec was written, not assumed.

**Spike:** [`spikes/01-blocking-tool-call/`](../../spikes/01-blocking-tool-call/)
— see `spike-result.txt` for the recorded run and `FINDINGS.md` for the reading.

Because that held, `request_approval` is a **blocking call**, not a handle-and-poll
pair. Keep it that way: the blocking shape is what makes the demo legible (the
agent visibly stalls) and what makes the guarantee simple to reason about.

**If a future client turns out to time out**, the fallback is already designed:
add `check_approval(request_id)` and have `request_approval` return immediately
with `{"status":"pending","request_id":...}` when called with `blocking=false`.
Do not build that until something actually needs it.

## HTTP surface

Internal only. Not exposed to the network. The MCP server is the public face.

### `POST /request_approval`

```jsonc
// request
{ "actor": "claude-code/session-4f2a",
  "tool_name": "db.drop_table",
  "tool_args": {"table": "users_backup"},
  "justification": "cleaning up staging" }

// response — sent only once a verdict exists
{ "request_id": "a91f3c2e",
  "approved": false,
  "verdict": "denied",
  "decided_by": "human",
  "reason": "user declined destructive DDL",
  "latency_ms": 41904 }
```

Blocks. No server-side timeout by default; see *Timeouts* below. Returns as soon
as the verdict exists -- for a relay-gated request that is before the dwell
window closes, not after (`spec/02` Rule 4b step 2).

### There is no `/decide` endpoint

v1.1 kept `POST /decide` for policy and system verdicts after removing the human
one. Review 02 finding I4 showed that is still a hole: the Warden, the policy
engine and the Supervisor all run **in the broker's own process**, so no
legitimate caller is remote. Anything reaching `/decide` over HTTP is by
definition not one of them, and a co-resident agent holding the MCP token could
have posted `decided_by=policy` to unblock a consent-channel call with no button
press and no policy evaluation.

**Every verdict is produced in-process.** There is no HTTP surface that resolves
a request, for any `decided_by` value. Removing the endpoint removes the class.

| Verdict | Produced by | Path |
|---|---|---|
| `human` | Supervisor, after Rule 4a | in-process |
| `policy` / `warden_auto` | Policy engine, after resolution | in-process |
| `system` (`expired`, `link_lost`, `device_reset`) | Broker timers and the Supervisor's safe state | in-process |

### Token scopes

One unscoped token was wrong: the MCP server and the dashboard are separate
processes with different needs (I4).

| Scope | Held by | May call |
|---|---|---|
| `agent` | MCP server | `POST /request_approval` only |
| `ui` | Dashboard backend | `GET /pending`, `/audit`, `/policies`, and `PUT /policies/{pattern}` |
| `ui_ro` | `airgap watch` terminal reader | `GET /pending`, `/audit`, `/policies`. **No write route, ever** |

All three are generated at broker startup and held in memory. The `agent` token
grants nothing that can resolve a request; no `ui*` token grants anything that
can approve one. No scope can reach another's routes.

`ui_ro` exists because read access and policy-write access have no reason to
share a credential. The terminal reader is a long-running local process whose
environment a co-resident agent can often read; giving it a token that can widen
the policy table would hand N-T8 to exactly the party the system exists to
constrain. A reader needs to read. `PUT /policies/{pattern}` is therefore
reachable **only** with `ui`, and a `ui_ro` token presented to it is rejected with
`403` — not silently ignored, and not downgraded to a no-op.

Hardening:

- Bind `127.0.0.1` only. Never `0.0.0.0`.
- `POST /request_approval` requires `Content-Type: application/json` and rejects
  any request carrying an `Origin` header — no legitimate agent sends one, and it
  closes the simple-cross-origin path.
- The dashboard is a browser and **does** send `Origin`, so `ui`-scope routes
  check it against an allowlist instead of rejecting it, and `PUT /policies`
  additionally requires a double-submit CSRF token. v1.1's blanket `Origin`
  rejection would have broken the dashboard (I4).
- `ui_ro` is not a browser. Like `agent`, it rejects any request carrying an
  `Origin` header, which keeps the read-only token off the browser-reachable
  path entirely.
- **No reader has an approve capability**: no route, no button, no token scope,
  and nothing in `ui` or `ui_ro` scope that resolves a request.

### `GET /pending`

Returns the queue. Used by the dashboard and by the bridge to reconcile after a
device reboot.

### `GET /health`

`{"ok": true, "link": "up"|"down", "pending": 2}`

## MCP tool contract

Exactly one tool is exposed. Resist adding more.

```jsonc
{
  "name": "request_approval",
  "description": "Request human approval for an irreversible action. Blocks until a human physically approves or denies. May take minutes. Call this BEFORE taking any action that cannot be undone.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "tool_name":     {"type": "string", "description": "The action you intend to take, e.g. 'db.drop_table'"},
      "tool_args":     {"type": "object", "description": "The exact arguments you intend to use"},
      "justification": {"type": "string", "description": "Why you believe this action is warranted"}
    },
    "required": ["tool_name", "justification"]
  }
}
```

Returns a single text block: `"APPROVED: <reason>"` or `"DENIED: <reason>"`.

The description matters more than usual — it is the only thing telling the actor
agent to call this *before* acting rather than after. Do not shorten it.

## Timeouts

| Layer | Setting | Rationale |
|---|---|---|
| MCP client | none (SDK default) | verified to survive the spike duration |
| Broker `/request_approval` | none | the physical gate has no timeout either |
| Request expiry | **30 minutes**, configurable | resolves to verdict `expired` — not `denied` — relay stays open |
| Relay lease renewal | `relay_renew` every 3 s, device expires at 10 s | bounds an unsupervised closed contact after a broker kill |
| Relay dwell window | `policies.dwell_s`, default 60 s | the actuation window; there is no completion signal from the actor |
| Arming dead time | 2 s, plus `btns == 0` observed | stops a press binding to a request the human never read |
| Inbound request rate | 6/min per `tool_name` per actor | an agent retry loop must not be able to flood the queue |
| Ack timeout, device | 100 ms | see [`01-serial-protocol.md`](01-serial-protocol.md) |
| Link loss | 3000 ms without a tick | safe state, all pending → `link_lost` |

Every one of these bounds fails toward open. Expiry is the only one that resolves
a *request*; the lease, dwell and dead time bound the relay cycle instead.

## Concurrency

**Exactly one request may be armed at a time.** The device has one flag, one
relay, and three buttons; there is no way for a human to indicate *which* of two
pending requests they approved.

A second `request_approval` arriving while one is armed is queued FIFO and its
caller keeps blocking. The dashboard shows the queue depth. Do not attempt to
arm two requests concurrently — the interlock in
[`02-supervisor.md`](02-supervisor.md) will reject it, correctly.

## Startup and recovery

On broker start: open the serial link, `disarm`, drive the relay open
(`relay(closed=false)` is ungated and always accepted), mark any `requests` rows
still `pending` in the database as `link_lost`. A broker restart
never inherits a pending approval.
