# Airgap

**A physical consent terminal for autonomous AI agents.**

An agent that wants to take an irreversible action calls an MCP tool. That tool
call **blocks**. On the desk, an Arduino raises a servo-driven flag, turns an LED
red, beeps, and holds a relay contact open. The call does not return until a
human presses a physical button.

The approval **input** is out-of-band: a physical button with no software path by
which an agent can press it. What that buys depends on the mode — see below.

> **Status:** design complete, implementation not started.
> Contracts are frozen; work is tracked in [`docs/tickets/tickets.yaml`](docs/tickets/tickets.yaml).

---

## What it actually guarantees

Read this before assuming anything. Airgap operates in two modes, and conflating
them would be dishonest:

| Mode | When | Guarantee | Survives host compromise? |
|---|---|---|---|
| **Enforcement boundary** | The relay is physically in the power path of the controlled thing — a lamp, pump, motor, machine mains | **Strong.** Physically impossible while the relay is open | **No** |
| **Consent channel** | The action is purely digital and reachable another way — `DROP TABLE`, `send_email`, an API call | **Moderate.** Guarantees the agent *asked*, and gives a human a real out-of-band veto. Does **not** make the action impossible | No |

Most software actions are consent channel.

### Airgap is not a sandbox

It does not confine an agent. Specifically, it does **not** defend against:

- **An agent that never calls `request_approval`.** Airgap gates what is routed
  through it. Pair it with least-privilege credentials.
- **A compromised host.** The broker, Supervisor, database and dashboard share one
  trust zone and fail as a unit. A compromised host can render a false action on
  the LCD, synthesise button frames, rewrite the policy table, and recompute the
  audit chain. Every guarantee above is void. This is the dominant residual risk.
- **A malicious or coerced human**, or anyone else within reach of the button.
- **An auto-approved action**, which by definition no human ever sees. Ships
  disabled, with an empty policy envelope.
- **Malicious firmware.** Flash it yourself from `firmware/`.
- **A relay that fails welded shut.** Not detectable in software.
- **Audit tampering by a database superuser.** The hash chain is unkeyed and
  unanchored, so a superuser can rewrite history and recompute it. Tamper-evident
  below superuser, not tamper-proof.

The full analysis, including the threats it *does* defend against, is in
[`docs/DESIGN.md` §4](docs/DESIGN.md).

---

## How it works

```
Actor Agent ──tool call (BLOCKS)──▶ Broker ──▶ Warden ──▶ Policy ──▶ Supervisor
                                                                        │
                                                                    USB serial
                                                                        ▼
                                                                  Arduino UNO
                                                          LED · piezo · servo flag
                                                             relay held OPEN
                                                                        │
                                                          [ human presses APPROVE ]
                                                                        │
        ◀──────── call unblocks, returns {"approved": true} ────────────┘
```

Two properties do the real work:

1. **A deterministic Supervisor sits between every decision and the serial port.**
   The LLM proposes; the Supervisor validates against a static allowlist. There is
   no raw passthrough.
2. **The policy engine runs *after* the LLM and can only narrow its verdict.**
   A jailbroken Warden cannot widen its own authority.

---

## Documentation

| Document | What it covers |
|---|---|
| [`docs/DESIGN.md`](docs/DESIGN.md) | **Start here.** Problem, threat model, design decisions with rejected alternatives, flows, failure analysis, NFRs |
| [`docs/spec/`](docs/spec/) | Frozen contracts: serial protocol, supervisor interlock, broker/MCP API, firmware pin map, data model |
| [`docs/tickets/tickets.yaml`](docs/tickets/tickets.yaml) | 15-ticket implementation DAG |
| [`AGENTS.md`](AGENTS.md) | Cold-start context for implementing agents |
| [`spikes/`](spikes/) | De-risking experiments and their recorded results |

## Verified before it was specced

An MCP tool call that blocks for **150 seconds survives on both stdio and
streamable-http transports** with default client settings — nothing in the SDK
defaults to a finite timeout. That is the assumption the whole architecture rests
on, so it was tested before the spec was written rather than after.

Recorded run and caveats: [`spikes/01-blocking-tool-call/`](spikes/01-blocking-tool-call/)

## Hardware

Built on an Arduino UNO Rev3 from the Arduino Student Kit. Beyond the kit: a 5V
relay module (~$4), a 16×2 I²C LCD (~$6), and something to switch (~$8).

Pin map and the AVR timer reasoning behind it: [`docs/spec/04-firmware.md`](docs/spec/04-firmware.md).

## License

MIT
