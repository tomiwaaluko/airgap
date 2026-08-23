# Airgap — System Overview

**Status:** frozen for implementation
**Owner:** Tomiwa Aluko

## What this is

A physical consent terminal for autonomous AI agents. Any agent that wants to
take an irreversible action calls an MCP tool. That tool call **blocks** until a
human presses a physical button on an Arduino-driven device.

**Read [`../DESIGN.md` §4.4](../DESIGN.md) before you form a mental model of what
this guarantees.** Airgap runs in two modes and they are not equivalent:

- **Consent channel** (most actions — `DROP TABLE`, `send_email`): the guarantee
  is that the agent *asked* and a human had a genuine out-of-band veto. The
  action is **not** made impossible; an agent with the credentials can reach the
  resource by another path.
- **Enforcement boundary** (the relay is physically in the controlled thing's
  power path): the action *is* physically impossible while the contact is open.

Neither mode survives compromise of the host (see `DESIGN.md` §4.3 N-T2). The
"prevented by geometry" framing applies to the enforcement mode only, and only
while the host is intact. Do not carry the stronger claim into code comments,
the README, or a demo script.

## The one-sentence architecture

```
Actor Agent --tool call (BLOCKS)--> Broker --> Warden (triage) --> Supervisor
  --> Serial Bridge --USB--> Arduino --> [human presses APPROVE] --> unblocks
```

## Components and their boundaries

| Component | Responsibility | Never does |
|---|---|---|
| **Actor agent** | Proposes actions. Any MCP client. | Not part of this repo. |
| **Broker** | Holds the call open, orchestrates the verdict. | Never talks to serial directly. Never produces `decided_by=human`. |
| **Warden** | LLM triage: *proposes* auto-approve / escalate / block. | Never executes the action; never closes the relay; never produces a final verdict. |
| **Policy engine** | Deterministic resolution that may only narrow the Warden's proposal. | Never calls an LLM. Never widens. |
| **Supervisor** | Validates every device command. Runs the Rule 4 interlock. Fail-safe. **The only producer of `decided_by=human`.** | Never makes a *risk* judgment. |
| **Serial bridge** | Owns the serial port. Encodes/decodes frames. | Never interprets meaning. Never resolves a request. |
| **Firmware** | Fast local loop: buttons, LEDs, servo, relay, dial. | Never initiates a command. Never waits on the network. |

## Non-negotiable invariants

These hold no matter what any ticket says. If a ticket appears to contradict one,
stop and flag it rather than implementing it.

1. **The LLM never writes to the serial port.** Every device command passes
   through the Supervisor, which validates against a static allowlist.
2. **Fail closed, with the right verdict.** Loss of serial, broker crash,
   timeout, device reset, and unparseable frames all leave the relay **open** and
   release every blocked caller with a refusal. The *verdict* recorded differs and
   the distinction matters for the audit trail: `link_lost`, `expired`, and
   `denied`/`device_reset` are not interchangeable, and none of them is "denied
   with a reason string" (`spec/05`).
3. **The autonomy dial is read-only to software.** The firmware reports `A0`;
   nothing in the system can set it. This is the point of the dial.
4. **The Warden cannot approve what the policy engine blocks.** Deterministic
   rules run *after* the LLM and can only narrow, never widen.
5. **Every decision is logged before it is acted on**, not after.
6. **The relay closes only on a verified button event whose request id matches
   the id the Supervisor is ARMED with.** Armed state, not a database query — the
   Supervisor stays armed through resolution and the whole relay cycle.
7. **No HTTP surface resolves a request.** There is no `/decide` endpoint. Every
   verdict is produced in-process; `decided_by=human` only by the Supervisor
   after Rule 4a. A software path to `APPROVED` would void the entire premise
   (`DESIGN.md` D11).
8. **`relay` closes, `relay_renew` holds.** The close is Rule-4-gated; the
   keepalive is not, because it cannot create a closure. The device opens the
   contact itself if not renewed within 10 s, so killing the broker cannot leave
   it closed.
9. **A `relay_gated` policy row never resolves to `auto_approve`** — the resolver
   forces `escalate`. Enforcement-mode actions always get a human.

## Repo layout

```
firmware/airgap/airgap.ino     Arduino command interpreter (one sketch, never reflashed mid-demo)
src/airgap/
  protocol.py                  Frame encode/decode. Pure functions, no I/O.
  transport.py                 SerialTransport + MockTransport behind one interface.
  supervisor.py                Validation, clamps, rate limits, fail-safe.
  broker.py                    FastAPI app. Blocking request_approval; verdicts are in-process.
  warden.py                    LLM triage agent.
  policy.py                    Deterministic rules.
  models.py                    SQLAlchemy models.
  mcp_server.py                MCP server exposing request_approval.
tests/                         pytest. Every module above has a peer test file.
web/                           Next.js dashboard.
docs/spec/                     These documents. The contracts.
docs/tickets/                  Ticket definitions, source of truth for Linear.
spikes/                        De-risking experiments and their recorded results.
```

## Read next

- [`../DESIGN.md`](../DESIGN.md) — **why** the system is shaped this way: threat
  model, design decisions and the alternatives rejected, end-to-end flows,
  failure analysis. Read once before your first ticket.
- [`01-serial-protocol.md`](01-serial-protocol.md) — the wire contract. Frozen.
- [`02-supervisor.md`](02-supervisor.md) — safety rules. Frozen.
- [`03-broker-api.md`](03-broker-api.md) — HTTP + MCP contract. Frozen.
- [`04-firmware.md`](04-firmware.md) — pin map and state machine. Frozen.
- [`05-data-model.md`](05-data-model.md) — schema and audit chain. Frozen.
