# AGENTS.md — Airgap

You are implementing one ticket of a larger system. You start with **no memory of
previous sessions**. Everything you need is in this repository. Read this file
fully before writing code, then read the contract documents your ticket names.

---

## 1. What you are building

**Airgap** is a physical consent terminal for autonomous AI agents.

An AI agent that wants to take an irreversible action — drop a table, send money,
publish something — calls an MCP tool called `request_approval`. That tool call
**blocks**. Meanwhile an Arduino on the user's desk raises a servo-driven flag,
turns an LED red, and beeps. The call does not return until a human presses a
button on that device.

For the subset of actions whose policy row sets `relay_gated`, the device also
holds a relay contact **open**, physically preventing the action until approval.
**Most actions are not relay-gated** and never involve the relay at all — do not
write the relay cycle into the general path.

**Be precise about what that buys**, because an imprecise version of this
sentence will lead you to write code that overclaims. Airgap has two modes:

- **Consent channel** — most actions. The guarantee is that the agent *asked* and
  a human had a real out-of-band veto. The action is **not** impossible; an agent
  with credentials can reach the resource another way.
- **Enforcement boundary** — only when the relay is physically in the controlled
  thing's power path. Then it genuinely is impossible while the contact is open.

Neither survives compromise of the host. Read `docs/DESIGN.md` §4.3 and §4.4
before writing a comment, docstring, or README line that says "prevented by
geometry" — that phrase is true of the enforcement mode only.

A second LLM, the **Warden**, triages each request first and may auto-approve
low-risk ones. A deterministic **policy engine** runs after the Warden and can
only ever narrow its verdict, never widen it.

---

## 2. Invariants — these outrank your ticket

If a ticket appears to contradict any of these, **stop and say so in the ticket
comments instead of implementing it.**

1. **The LLM never writes to the serial port.** Every device command goes through
   `Supervisor.send()`, which validates against a static allowlist of typed
   command objects. There is no raw passthrough and you must not add one.
2. **Fail closed, with the right verdict.** Lost serial link, crashed broker,
   timeout, device reset, and unparseable frames all leave the relay **open** and
   release every blocked caller with a refusal. The *verdict* recorded differs and
   the distinction matters: `link_lost`, `expired`, and `denied`/`device_reset`
   are peers, and none of them is "denied with a reason string" (`spec/05`).
3. **The autonomy dial is read-only to software.** Firmware reports `A0`. Nothing
   in the system can set it. That is the entire point of having a physical dial.
4. **The policy engine can only narrow the Warden's verdict.** See the resolution
   table in `docs/spec/05-data-model.md`.
5. **Log the decision before acting on it**, never after.
6. **The relay closes only on a button event whose request id matches the armed
   request.** No match, no close. See the five-condition interlock in
   `docs/spec/02-supervisor.md`.
7. **Only `src/airgap/supervisor.py` may import `src/airgap/transport.py`.**
   There is a test that enforces this by scanning the source tree.
8. **No HTTP surface resolves a request — there is no `/decide` endpoint.**
   Every verdict is produced in-process: `human` by the Supervisor after Rule 4a,
   `policy`/`warden_auto` by the resolver, `system` by broker timers. The
   dashboard has no approve route, button, or token scope. If you find yourself
   adding a way for software to say a human approved something, you have removed
   the entire point of the project.
9. **The relay is closed by `relay`, held by `relay_renew`.** `relay` is
   Rule-4-gated; `relay_renew` is not, because it can only extend a closure that
   a gated close already authorised, never create one. Do not merge them — that
   was the v1.1 deadlock.
10. **A `relay_gated` policy row can never resolve to `auto_approve`.** The
    resolver forces it to `escalate`. Do not try to infer this from the interlock:
    a missing interlock branch stops the contact closing, it does not change the
    verdict, and the difference is a lamp that reports APPROVED while staying off.

---

## 3. Read the contracts before you write code

`docs/DESIGN.md` explains intent; the `docs/spec/` documents are the contracts you
write code against. Where they disagree, the contract is what compiles and the
disagreement is a bug a human must resolve — flag it, don't pick a side.

The contracts are **frozen**. They are not suggestions, and they are not yours to
improve. If you think one is wrong, comment on the Linear ticket and stop.

| Document | Read it when your ticket touches |
|---|---|
| `docs/DESIGN.md` | **why** the system is shaped this way — read §4 (threat model) and §6 (design decisions) once, before your first ticket |
| `docs/spec/00-overview.md` | anything — read this first, always |
| `docs/spec/01-serial-protocol.md` | the wire format, codec, bridge, or firmware |
| `docs/spec/02-supervisor.md` | validation, safety, the relay interlock |
| `docs/spec/03-broker-api.md` | HTTP endpoints, MCP tool, blocking semantics |
| `docs/spec/04-firmware.md` | the Arduino sketch, pin assignments, timers |
| `docs/spec/05-data-model.md` | database schema, audit chain, policy resolution |

Field names, JSON shapes, pin numbers, and enum values in those documents are
exact. Do not rename anything for style. Three tickets each inventing their own
JSON envelope is the specific failure mode this project is structured to avoid.

---

## 4. Environment and commands

Python 3.14, `uv` for dependency management, Node 24 for the dashboard.

```bash
uv sync                        # install everything, including dev deps
uv run pytest                  # full test suite
uv run pytest tests/test_supervisor.py -v    # one file
uv run ruff check . --fix      # lint
uv run ruff format .           # format
uv run mypy src/               # type check
```

Run these from the repo root. `uv run` handles the venv; do not create one by hand
and do not `pip install` into the system Python.

**Hardware is usually absent.** Everything must work against `MockTransport`,
which replays scripted device frames. A ticket that can only be verified with a
physical Arduino plugged in will say so explicitly in its acceptance criteria; if
yours doesn't, you must not need hardware.

---

## 5. Conventions

- **Typed.** Full annotations on every public function. `mypy` must pass.
- **Pure core.** `protocol.py` and `policy.py` do no I/O and import nothing from
  the rest of the package. Keep it that way; it is what makes them testable.
- **Errors are values at boundaries, exceptions inside.** `decode()` returns
  `None` on a bad frame rather than raising, because a garbage byte on a serial
  line is expected, not exceptional. `Supervisor.send()` raises, because being
  asked to send a forbidden command is a programming error.
- **No new dependencies** without saying so in the ticket comment. The stack is
  fastapi, uvicorn, pydantic, sqlalchemy, alembic, httpx, pyserial, mcp,
  anthropic, pytest, ruff, mypy. That should be enough.
- **Docstrings explain why, not what.** The signature says what.
- **No `TODO` comments.** If work is left over, it is a new Linear ticket.

---

## 6. Testing

- Every module in `src/airgap/` has a peer file in `tests/`.
- Test behaviour through the public interface, not private methods.
- The safety-critical modules — `supervisor.py`, `audit.py`, `policy.py` — need
  the failure cases enumerated in their spec documents, each failing
  **independently**. "It works when everything is fine" is not coverage of a
  safety interlock.
- Use `MockTransport` and `freezegun` for time. No `sleep()` in tests.
- A test that would pass against a stub implementation is not a test.

---

## 7. Definition of done

A ticket is done when **all** of these are true:

1. `uv run pytest` passes with no new skips.
2. `uv run ruff check .` and `uv run mypy src/` are clean.
3. The acceptance criteria in the ticket are each demonstrably met.
4. The verification command written in the ticket exits 0, pasted into the PR.
5. Nothing outside the ticket's stated scope changed.

**Do not mark a ticket done on the basis that the code looks right.** Run the
command. Paste the output.

---

## 8. Git and Linear workflow

- Branch per ticket: `air-7-broker-blocking-endpoint`.
- Conventional commits: `feat(broker): hold approval requests open until decided`.
- PR title carries the ticket id. PR body: what changed, the verification command
  and its output, and anything you had to decide that the spec did not cover.
- Move the Linear ticket to **In Review** when the PR opens, not before.
- If you were blocked, say what blocked you in a ticket comment and leave the
  ticket in progress. A silently abandoned ticket is worse than a blocked one.

---

## 9. When to stop and ask

Stop, comment on the ticket, and do not code around it if:

- A contract document is internally inconsistent or contradicts your ticket.
- You need a dependency that isn't in the list above.
- You'd have to weaken any invariant in section 2 to make a test pass.
- The ticket's acceptance criteria can't be verified without hardware and don't
  say they need it.
- You find a real safety hole in the design. Say so loudly. That is the most
  valuable thing you can produce on this project.

---

## 10. Effort tiers

Your ticket carries a `tier`. The orchestrator uses it to pick your model and
reasoning effort; it also tells you how much care to take.

| Tier | Means | Expectation |
|---|---|---|
| `mechanical` | scaffolding, config, boilerplate | Follow the pattern. Don't invent. |
| `standard` | normal implementation against a frozen contract | Match the spec exactly. Normal test coverage. |
| `design` | contract exists, implementation shape is open | Think about structure. Explain your choice in the PR. |
| `safety-critical` | the interlock, the audit chain, the policy resolver | Enumerate failure modes first, then implement. Adversarially review your own work before opening the PR: try to find the input that defeats it. |

---

## 11. Things that look like bugs but are not

- **The status LED is two digital pins, not a PWM RGB LED.** Every hardware PWM
  pin on the UNO is claimed by `millis()`, `Servo`, or `tone()`. This is
  deliberate and explained in `docs/spec/04-firmware.md`. Do not "fix" it.
- **`request_approval` blocks for minutes.** That is the product, not a hang. It
  was verified against a real MCP client in `spikes/01-blocking-tool-call/`.
- **The device never drives its own relay.** It reports button presses; the host
  commands the relay. This keeps the interlock in exactly one place.
- **Only one request can be armed at a time.** There is one flag and one set of
  buttons; a human cannot indicate *which* of two requests they approved.
- **`decode()` swallowing bad frames is correct.** Serial lines produce garbage.
- **The relay lease looks like the device making a decision.** It isn't — it is a
  deadline. Passive fail-safe only covers power loss; a killed broker with USB
  still powered would otherwise leave the contact closed forever.
- **Auto-approved requests light nothing up.** No LED, no flag, no tone — LCD
  only. Green means "a human just approved this" and nothing else, because with
  FIFO queuing a green flash could appear while a *different* request sits armed.
- **The absence of a `/decide` endpoint is not a missing feature.** See
  invariant 8. Every verdict is produced in-process, on purpose.
- **`relay_renew` returning `not_closed` on an open contact is correct.** A
  keepalive that could close a contact would be a close, and would need the
  interlock.
- **`expired` and `link_lost` are verdicts, not reasons.** A timeout is not a
  denial and the audit trail must be able to tell them apart.
