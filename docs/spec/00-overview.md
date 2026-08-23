# Airgap — System Overview

**Status:** frozen for implementation
**Owner:** Tomiwa Aluko

## What this is

A physical consent terminal for autonomous AI agents. Any agent that wants to
take an irreversible action calls an MCP tool. That tool call **blocks** until a
human presses a physical button on an Arduino-driven device. Until then a servo
holds a relay contact open, so the action is prevented by geometry rather than
by a conditional in software that a prompt injection could talk its way past.

## The one-sentence architecture

```
Actor Agent --tool call (BLOCKS)--> Broker --> Warden (triage) --> Supervisor
  --> Serial Bridge --USB--> Arduino --> [human presses APPROVE] --> unblocks
```

## Components and their boundaries

| Component | Responsibility | Never does |
|---|---|---|
| **Actor agent** | Proposes actions. Any MCP client. | Not part of this repo. |
| **Broker** | Holds the call open, orchestrates the verdict. | Never talks to serial directly. |
| **Warden** | LLM triage: auto-approve / escalate / block. | Never executes the action; never closes the relay. |
| **Policy engine** | Deterministic rules that override the Warden. | Never calls an LLM. |
| **Supervisor** | Validates every device command. Fail-safe. | Never makes a judgment call. |
| **Serial bridge** | Owns the serial port. Encodes/decodes frames. | Never interprets meaning. |
| **Firmware** | Fast local loop: buttons, LEDs, servo, relay, dial. | Never initiates a command. Never waits on the network. |

## Non-negotiable invariants

These hold no matter what any ticket says. If a ticket appears to contradict one,
stop and flag it rather than implementing it.

1. **The LLM never writes to the serial port.** Every device command passes
   through the Supervisor, which validates against a static allowlist.
2. **Deny by default.** Loss of serial, broker crash, timeout, ambiguous state,
   or an unparseable frame all resolve to *denied* and an **open** relay.
3. **The autonomy dial is read-only to software.** The firmware reports `A0`;
   nothing in the system can set it. This is the point of the dial.
4. **The Warden cannot approve what the policy engine blocks.** Deterministic
   rules run *after* the LLM and can only narrow, never widen.
5. **Every decision is logged before it is acted on**, not after.
6. **The relay closes only on a verified button event whose request id matches
   the currently pending request.** No id match, no close.

## Repo layout

```
firmware/airgap/airgap.ino     Arduino command interpreter (one sketch, never reflashed mid-demo)
src/airgap/
  protocol.py                  Frame encode/decode. Pure functions, no I/O.
  transport.py                 SerialTransport + MockTransport behind one interface.
  supervisor.py                Validation, clamps, rate limits, fail-safe.
  broker.py                    FastAPI app. Blocking request/decide.
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
