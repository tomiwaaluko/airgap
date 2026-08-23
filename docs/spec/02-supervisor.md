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

## Rule 5 — fail-safe

Any of the following triggers an immediate transition to the **safe state**:

- No `tick` event for 3000 ms
- Serial port error, disconnect, or unexpected EOF
- Three consecutive unparseable lines
- An ack timeout on a `relay` command
- Unhandled exception anywhere in the broker process

**Safe state** is: relay open, LED red, flag up, all pending requests resolved as
`denied` with reason `link_lost`. The relay is opened by de-energising, so a
dead host or unplugged USB cable produces the safe state passively — verify this
on real hardware during bring-up (`AIR-5`).

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
