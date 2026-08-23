# Serial Protocol — FROZEN CONTRACT v1

Do not extend, rename, or "improve" anything on this page. Every component on
both sides of the cable is written against it. If you believe it is wrong, stop
and raise it; do not implement a variant.

## Physical layer

| Setting | Value |
|---|---|
| Baud | `115200` |
| Framing | 8N1 |
| Encoding | UTF-8, ASCII subset only |
| Frame delimiter | `\n` (LF). `\r` is tolerated on read, never sent. |
| Max frame length | 200 bytes including the newline |
| Flow control | none |

One JSON object per line. No pretty-printing, no embedded newlines.

## Direction: host → device (commands)

Every command carries a monotonically increasing integer `id` starting at 1.
The device **must** ack every command within 100 ms.

```json
{"id":1,"cmd":"ping"}
{"id":2,"cmd":"led","state":"off"}          // off | green | amber | red
{"id":3,"cmd":"tone","pattern":"alert","n":3}  // ok | deny | alert, n = 1..5
{"id":4,"cmd":"flag","up":true}
{"id":5,"cmd":"relay","closed":false}
{"id":6,"cmd":"lcd","l1":"DROP users_backup","l2":"412 rows - irreversible"}
{"id":7,"cmd":"arm","req":"a91f3c2e"}       // begin pending state for request id
{"id":8,"cmd":"disarm"}
```

Field rules:

- `cmd` — one of exactly: `ping`, `led`, `tone`, `flag`, `relay`, `relay_renew`,
  `lcd`, `arm`, `disarm`. Unknown → error ack.

### The relay is closed by one command and held by a different one

This separation is load-bearing. `relay` is gated by the Supervisor's Rule 4
interlock; `relay_renew` deliberately is not. Reusing `relay` as the heartbeat
made the two rules contradictory — see review 02 finding C1.

| Command | Gated? | Effect |
|---|---|---|
| `{"id":n,"cmd":"relay","closed":true}` | **Yes**, Rule 4 | `OPEN → CLOSED`. Starts a 10 000 ms lease. Rejected with `not_armed` if the device is disarmed |
| `{"id":n,"cmd":"relay_renew"}` | **No** | Extends the lease **only if the contact is already closed**. If open, acks `{"ok":false,"err":"not_closed"}` and changes nothing |
| `{"id":n,"cmd":"relay","closed":false}` | **No** | `CLOSED → OPEN` immediately, cancels the lease. Always accepted, in every state |

`relay_renew` is safe to leave ungated because it cannot create a closure — it can
only extend one that a gated close already authorised. It is a keepalive, not a
command.

If the lease expires the device opens the contact itself and emits
`{"ev":"lease_expired","t":...}`. The host renews every 3000 ms for as long as it
intends the contact to stay closed. This is the only thing the device decides
autonomously, and it is a deadline rather than a judgment — see `DESIGN.md` D8.
- `led.state` — one of `off`, `green`, `amber`, `red`. Amber is red+green both on.
- `tone.pattern` — one of `ok`, `deny`, `alert`. `n` clamps to 1..5.
- `lcd.l1` / `lcd.l2` — truncated to 16 chars by the **host**, not the device.
- `arm.req` — 8 lowercase hex chars. The device stores it and echoes it on every
  subsequent button event. This is what makes a button press unforgeable.

## Direction: device → host

### Acks — one per command, same `id`

```json
{"id":3,"ok":true}
{"id":9,"ok":false,"err":"unknown_cmd"}
```

`err` is one of: `unknown_cmd`, `bad_field`, `out_of_range`, `not_armed`, `busy`.

### Events — unsolicited, no `id`

```json
{"ev":"btn","which":"approve","req":"a91f3c2e","t":91043}
{"ev":"boot","fw":"1.0.0","t":12}
{"ev":"lease_expired","t":104220}
```

- `ev.lease_expired` — the device opened the relay itself because the host stopped
  renewing. The host must treat this as a link/health fault, resolve any pending
  request as `denied` with reason `lease_expired`, and audit it.

- `ev.btn.which` — `approve` | `deny` | `never`.
- `ev.btn.req` — the request id from the last `arm`. If the device is not armed
  it emits `{"ev":"btn","which":"...","req":null,...}` and the host **must
  discard it**.
- `t` — device `millis()`. Host-side wall-clock is the host's problem.

### Telemetry — every 1000 ms, unconditionally

```json
{"ev":"tick","dial":7,"relay":false,"armed":true,"lease_ms":0,"btns":0,"t":92044}
```

- `lease_ms` — milliseconds remaining on the relay lease, `0` when the contact is
  open. Lets the host detect a lease it is failing to renew before it expires.
- `btns` — 3-bit field of buttons **currently held**: bit 0 `approve`, bit 1
  `deny`, bit 2 `never`. `0` means all released. Without this the host cannot
  observe release, and `DESIGN.md` D5's "all buttons released before arming"
  requirement is unimplementable (review 02 finding I2).

### Held-button suppression

On receiving `arm`, the device records which buttons are held at that instant and
**suppresses their events until they are released and pressed again**. A finger
resting on APPROVE when a request arms cannot approve it.

This is enforced on the device because only the device can see the transition.
The host-side dead time in `DESIGN.md` D5 handles the timing gap; this handles the
held-through case, which dead time alone does not close.

- `dial` — autonomy level, integer 0..10, mapped from `A0` by the **firmware**.
- Absence of ticks for **3000 ms** is a link failure. See fail-safe in
  [`02-supervisor.md`](02-supervisor.md).

## Rules that bind both sides

1. The device **never** initiates a command. It only acks and emits events.
2. The host **never** sends a new command before the previous one is acked or
   100 ms has elapsed, whichever is first.
3. Any line that fails to parse as JSON is **dropped silently** by both sides and
   counted. It is never partially interpreted.
4. `id` wraps at 65535 back to 1. Acks are matched on `id` alone.
5. On `boot`, the device comes up disarmed, relay **open**, flag **up**. The host
   **must not re-`arm`**: a device reset mid-request resolves that request as
   `denied` with reason `device_reset`. A power cycle is a denial, never a
   resumption (`spec/04` boot sequence, `DESIGN.md` F3).

## Reference codec behaviour

`src/airgap/protocol.py` exposes pure functions with no I/O:

```python
encode(cmd: Command) -> bytes          # ends in b"\n", <= 200 bytes, raises FrameTooLong
decode(line: bytes) -> Ack | Event | None   # None on unparseable, never raises
```

Round-trip property that must hold in tests:
`decode(encode(c))` preserves every field of `c` for all valid `c`.
