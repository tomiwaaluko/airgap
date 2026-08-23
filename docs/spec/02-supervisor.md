# Supervisor — FROZEN CONTRACT v1

The Supervisor is the only code permitted to hand a frame to the transport. It
is deterministic: no LLM call, no network call, no randomness, no clock-dependent
branching other than rate limiting. It is the layer that makes "the model cannot
write to the device" a structural fact rather than a promise.

## Position in the call graph

```
broker / warden  ->  Supervisor.send(cmd)  ->  Transport.write(bytes)  ->  device
                          |
                          +-- rejects, clamps, or rate-limits. Never asks anyone.
```

Nothing else may import `transport` directly. This is enforced by a test
(`tests/test_layering.py`) that greps the source tree.

## Rule 1 — command allowlist

Only the eight commands in [`01-serial-protocol.md`](01-serial-protocol.md) may
be constructed. The Supervisor takes typed `Command` objects, never dicts and
never strings. There is no passthrough, no `raw()`, no escape hatch. A request to
send anything else raises `SupervisorRejection`.

## Rule 2 — value clamps

| Field | Clamp |
|---|---|
| `tone.n` | clamp to `1..5` |
| `led.state` | must be in the enum, else reject |
| `lcd.l1` / `lcd.l2` | truncate to 16 chars, strip non-ASCII |
| `arm.req` | must match `^[0-9a-f]{8}$`, else reject |

Clamping is silent and logged. Rejection raises.

## Rule 3 — rate limits

| Limit | Value | On breach |
|---|---|---|
| Global command rate | 10 / second | queue, then drop oldest non-`relay` |
| `tone` | 1 per 2 seconds | drop |
| `relay` | 1 per second | **reject** (never silently drop a relay command) |
| `lcd` | 2 / second | drop |

Relay commands are never dropped. If one cannot be sent, that is an error the
caller must see.

## Rule 4 — the relay interlock

This is the safety-critical rule. Read it twice.

`relay(closed=True)` is accepted **only** when all of the following hold:

1. **The Supervisor is ARMED with request id R.** This is Supervisor state, not a
   database query. The Supervisor stays ARMED across the request's resolution and
   through the entire relay cycle, disarming only after the contact reopens. v1.1
   worded this as "exactly one *pending* request", which deadlocked: Rule 4a
   resolves the request before sending the close, so by the time the close is
   sent nothing is pending and the condition could never pass (review 02 C1).
2. A `btn` event with `which == "approve"` has been received.
3. That event's `req` field **equals** R.
4. That event arrived **after** the `arm` command was acked.
5. Fewer than 30 seconds have elapsed since that button event.

Condition 5 gates only this initial `OPEN → CLOSED` transition, which the
Supervisor issues immediately. Holding the contact closed afterwards uses
`relay_renew`, which is **not** gated by Rule 4 — see Rule 4b. Renewal cannot
create a closure, only extend one this rule already authorised.

If any condition fails, the command is rejected and the failure is logged with
which condition failed. `relay(closed=False)` is **always** accepted — opening is
never gated.

There is deliberately **no auto-approve branch**. An auto-approved request never
produces a button event, so it can never satisfy condition 2 and can never close
the relay. That is the mechanism behind `DESIGN.md` D12, and it is why the rule is
written in terms of button events rather than verdicts.

### Rule 4a — the interlock is what mints `decided_by=human`

A passing interlock does two things, in this order:

1. Resolves the pending request **in-process** with `decided_by="human"`, writing
   the audit row **before** anything moves (invariant 5 / NF8).
2. Then sends `relay(closed=True)` if the request is relay-gated.

`decided_by="human"` has exactly one producer: this code path. **There is no HTTP
endpoint that resolves a request at all** — `/decide` was removed entirely in
v1.2, because the Warden, the resolver and the Supervisor all run in the broker's
own process and no legitimate caller is remote (review 02 finding I4). Without
this, the five conditions gate only the relay — and for a consent-channel action
the relay is irrelevant, so any local caller could produce `APPROVED`. See
`DESIGN.md` D11 and `spec/03`.

### Rule 4b — the relay cycle, start to finish

v1.1 left "when does a healthy approved relay reopen?" unspecified, which is
review 02 finding C2. It is specified here.

**Only requests whose policy row sets `relay_gated = true` have a relay cycle at
all.** Consent-channel requests — the majority — resolve and return with no relay
command ever sent. Do not send relay commands for them.

For a relay-gated request, after Rule 4a has written the audit row:

| Step | Action |
|---|---|
| 1 | Send `relay(closed=True)` — the one Rule-4-gated command |
| 2 | Return `approved` to the blocked MCP caller **immediately**; do not wait for the cycle to finish |
| 3 | Start renewing with `relay_renew` every **3000 ms** |
| 4 | Start the **dwell timer**: `policies.dwell_s`, default **60 s** |
| 5 | On dwell expiry, send `relay(closed=False)` and stop renewing |
| 6 | Audit `relay_opened`, then **disarm** |
| 7 | Observe `btns == 0` in a tick, then wait the 2 s dead time, then the next queued request may arm |

Renewal **stops** on any of: dwell expiry, explicit open, safe state, or disarm.
It does **not** stop on verdict — the verdict happens at step 2, well before the
contact reopens. That was the v1.1 contradiction.

The dwell is the actuation window: "you have 60 seconds to run the pump." There
is no completion signal from the actor, deliberately — `DESIGN.md` D10 keeps the
tool surface to one call — so a bounded window is the honest mechanism.

### Rule 4c — `lease_expired` is scoped to the armed request

A `lease_expired` event means the host failed to renew a contact it intended to
hold. Handling depends on state, and **it may never touch a request other than
the armed one**:

- **ARMED with R, mid-dwell:** fault. Audit `lease_expired`, mark R's cycle
  incomplete, stop renewing, disarm. R's verdict is already `approved` and does
  not change — the human did approve; the actuation window was cut short. The
  actor is not re-notified; `DESIGN.md` D10 gives it no channel.
- **Not armed, or dwell already complete:** a stray. Audit it, confirm the
  contact is open, and change nothing. It must not resolve, deny, or disturb any
  queued request.

## Rule 5 — fail-safe

Any of the following triggers an immediate transition to the **safe state**:

- No `tick` event for 3000 ms
- Serial port error, disconnect, or unexpected EOF
- Three consecutive unparseable lines
- An ack timeout on a `relay` command
- Unhandled exception anywhere in the broker process

**Safe state** is: relay open, LED red, flag up, lease renewal stopped, and all
pending requests resolved with **`verdict="link_lost"`** — a first-class verdict
value in `spec/05`, *not* `denied` with a reason string. The audit row is written
before any caller is released.

The relay opens passively when the device loses power (unplugged cable, host
powered down), and via the device-side lease within 10 s when the host process
dies with USB still powered. Those are two different mechanisms covering two
different failures; see `DESIGN.md` D8. Verify both on real hardware during
bring-up (`AIR-5`).

## Rule 6 — no judgment

The Supervisor never decides whether an action *should* be approved. It only
decides whether a frame is *well-formed and permitted*. If you find yourself
writing risk logic here, it belongs in `policy.py`.

## Interface

```python
class Supervisor:
    def arm(self, request_id: str) -> None: ...
    def disarm(self) -> None: ...
    def send(self, cmd: Command) -> None: ...        # raises SupervisorRejection
    def on_event(self, ev: Event) -> None: ...       # feeds the interlock state machine
    def enter_safe_state(self, reason: str) -> None: ...
    @property
    def healthy(self) -> bool: ...
```

## Test obligations

`tests/test_supervisor.py` must cover, at minimum:

- every clamp boundary (`n=0`, `n=1`, `n=5`, `n=6`)
- every one of the five relay-interlock conditions failing **independently**
- a `btn` event with a **mismatched** `req` never closes the relay
- a `btn` event while disarmed never closes the relay
- a replayed `btn` event (same one delivered twice) closes the relay at most once
- tick starvation for 3001 ms enters the safe state
- `relay(closed=False)` succeeds in every state including the safe state
- an auto-approved request never closes the relay, however policy is configured
- a passing interlock writes the audit row **before** sending `relay(closed=True)`
  — assert the ordering, not just that both happened
- the lease is renewed at 3 s intervals while closed, and renewal **stops**
  immediately on resolution or safe state
- a `lease_expired` event resolves any pending request as `denied` /
  `lease_expired` and is audited
- the 2 s arming dead time holds: a second button press within it binds to
  nothing, and the next request does not arm until all buttons are observed
  released (`DESIGN.md` D5)
