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

1. There is exactly one pending request, and the Supervisor is armed with its id.
2. A `btn` event with `which == "approve"` has been received.
3. That event's `req` field **equals** the armed request id.
4. That event arrived **after** the `arm` command was acked.
5. Fewer than 30 seconds have elapsed since that button event.

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

`decided_by="human"` has exactly one producer: this code path. The broker's
`POST /decide` **must reject** that value from every caller regardless of origin,
and no dashboard route may resolve a request. Without this, the five conditions
gate only the relay — and for a consent-channel action the relay is irrelevant, so
any local caller could produce `APPROVED`. See `DESIGN.md` D11.

### Rule 4b — lease renewal

While a relay is closed, re-send `relay(closed=True)` every **3000 ms** to renew
the device-side 10 000 ms lease (spec/01). Stop renewing the moment the request
resolves or the safe state is entered. A received `lease_expired` event means the
host failed to renew: treat it as a fault, resolve any pending request as `denied`
with reason `lease_expired`, and audit it.

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
