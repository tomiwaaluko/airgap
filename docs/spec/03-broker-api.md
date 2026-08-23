# Broker API — FROZEN CONTRACT v1

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

Blocks. No server-side timeout by default; see *Timeouts* below.

### `POST /decide/{request_id}` — system and policy verdicts **only**

```jsonc
{ "approved": false, "decided_by": "policy", "reason": "blocked by policy db.drop_*" }
```

`decided_by` accepts exactly `policy`, `warden_auto`, or `system`. It **must
reject `human` with `403` from every caller**, without exception and regardless
of origin, source address, or credential.

**This is a security boundary, not a validation nicety.** The human verdict is
produced in-process by the Supervisor after the Rule 4 interlock (`spec/02` Rule
4a) and never travels over HTTP. If `/decide` could mint `decided_by=human`, then
for consent-channel actions — which is most of them — the five-condition
interlock would gate nothing that matters, and any co-resident process, or a page
issuing a simple cross-origin POST to localhost, could produce `APPROVED`. See
`DESIGN.md` D11 and C1 in `docs/reviews/2026-08-23-design-review.md`.

Returns `404` if the request is not pending, `409` if already resolved, `403` for
`decided_by=human`.

Additional hardening, all required:

- Bind `127.0.0.1` only. Never `0.0.0.0`.
- Require a bearer token generated at broker startup, held in memory, never
  written to disk or logs.
- Require `Content-Type: application/json` and reject requests carrying
  `Origin`, which no legitimate local caller sends. Together these defeat the
  simple-request cross-origin path.
- The **dashboard has no approve capability**: no route, no button, no token
  scope that can resolve a request.

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
| Relay lease renewal | 3 s while closed, device expires at 10 s | bounds an unsupervised closed contact after a broker kill |
| Inbound request rate | 6/min per `tool_name` per actor | an agent retry loop must not be able to flood the queue |
| Ack timeout, device | 100 ms | see [`01-serial-protocol.md`](01-serial-protocol.md) |
| Link loss | 3000 ms without a tick | safe state, all pending → `link_lost` |

Expiry is the only time bound, and it fails closed.

## Concurrency

**Exactly one request may be armed at a time.** The device has one flag, one
relay, and three buttons; there is no way for a human to indicate *which* of two
pending requests they approved.

A second `request_approval` arriving while one is armed is queued FIFO and its
caller keeps blocking. The dashboard shows the queue depth. Do not attempt to
arm two requests concurrently — the interlock in
[`02-supervisor.md`](02-supervisor.md) will reject it, correctly.

## Startup and recovery

On broker start: open the serial link, `disarm`, drive the relay open, mark any
`requests` rows still `pending` in the database as `link_lost`. A broker restart
never inherits a pending approval.
