# Airgap — Implementation Plan

**Version:** 1.2
**Date:** 2026-08-23
**Status:** revised after plan reviews
[01](reviews/2026-08-23-plan-review.md) and
[02](reviews/2026-08-23-plan-review-02.md). Both dispositions are written in.
**Covers:** `DESIGN.md` v1.4, `spec/00` v1.1, `spec/01` v1.1, `spec/02` v1.1,
`spec/03` v1.1, and `spec/04`, `05` at v1

---

## 1. What this document is

`DESIGN.md` says what to build and why. `spec/` pins the interfaces.
`tickets/tickets.yaml` defines units of work. **This document says in what order,
by whom, verified how, and what happens when something goes wrong.**

| Layer | Document | Who changes it |
|---|---|---|
| Intent | `DESIGN.md` | Human only |
| Contracts | `spec/00`–`05` | Human only, via §10 |
| **Plan** | **this file** | **Human, freely — it is not frozen** |
| Work items | `tickets/tickets.yaml` | Orchestrator may refine acceptance criteria; may not add or merge tickets |

The plan is deliberately not frozen. If reality disagrees with the schedule, the
schedule is wrong.

---

## 2. Planning principles, derived from two design reviews

Both reviews found defects that no single-document read would catch, and review
02 found a defect *introduced by review 01's fix*. Three principles follow, and
they shape the whole plan:

**P1 — Vocabulary drift is the dominant failure mode.** Across both reviews the
recurring bug was the same word meaning different things in different files:
`denied` vs `link_lost`, `ALWAYS-DENY` vs `never`, three vs four LED states,
`expired` as a verdict vs a reason. Every one was a documentation-only bug that
would have become a code bug. **Mitigation:** AIR-16 single-sources every
vocabulary into one module and lints the spec tables against it in CI. It is the
second ticket, before any behaviour is written.

P1 costs one carve-out in the pure-core rule: `protocol.py` and `policy.py` may
import `airgap.vocab`, and nothing else. Without it, the two modules forbidden
from importing anything internal would have to redeclare the command and verdict
enums inline — creating the third and fourth copies of exactly the vocabulary
this principle exists to single-source. `vocab.py` has no I/O and no internal
imports of its own, so the core stays a leaf plus one leaf, and testability, the
thing purity was protecting, is untouched. `tests/test_layering.py` enforces the
rule in this precise shape (AGENTS.md §5).

**P2 — A fix to one document leaves its siblings stale.** The v1.1 lease was
correct in `DESIGN.md` and incoherent against `spec/02`. **Mitigation:** the
contract change protocol (§10) requires a sibling sweep, not just a patch.

**P3 — The interlock deserves its own ticket and its own adversary.** It now
carries Rules 4, 4a, 4b and 4c — the arming state machine, verdict minting, the
relay cycle, the dwell, lease renewal, and `lease_expired` scoping. That is too
much to bury inside "the Supervisor." **Mitigation:** split out as AIR-17,
safety-critical, with a dedicated adversarial session.

---

## 3. Scope and cut lines

### In scope for v1

Everything in `DESIGN.md` §3 Goals, single-host, one device, one operator.

### Explicitly deferred, with the risk already accepted

| Deferred | Tracked as | Why it is safe to defer |
|---|---|---|
| Audit anchoring to device EEPROM | R9 | Chain is already tamper-evident below superuser; anchoring only defends against a Z2 superuser, and N-T2 already concedes that zone |
| Arg-level policy matching | R8 | v1 forbids broad tools in the auto-approve envelope, and the envelope ships empty |
| Dial-gated policy widening | R7 | Policy edits are audited; widening is visible |
| Monitored-contact relay | F10 | Documented as unmitigated; affects enforcement mode only |
| Multi-party approval | N-T3/N-T4 | Stretch |
| Remote / hosted broker | §11 | Widens Z2 for no benefit at this scale |

### The cut lines

| Milestone | If you stop here, you have | You do not have |
|---|---|---|
| **M1** | The interlock provably correct on mock hardware | Any agent integration |
| **M2 — MVP** | A blocking MCP approval gate, triaged, policy-bounded, audited, **at every risk class including high** | Physical hardware, a browser UI |
| **M3** | All of that, physically enforced on a real device | A presentable demo |
| **M4** | The demo, and the dashboard | — |

**M2 is the MVP** — the thesis proven end to end, at all three risk classes.
AIR-18 ships the full-fidelity reader §9 requires for high-risk, so nothing is
cut by risk class.

> This table used to say M2 lacked "high-risk readability" and supported "low and
> medium risk only". That was the leftover of the pre-Q1 draft, when the reader
> was assumed to be the dashboard and the dashboard was M4. **Q1 moved the reader
> into M2, and this table did not follow** — leaving §3 and §4.3 asserting
> opposite MVP scopes, with §3 being the one an orchestrator reads first.

---

## 4. Work breakdown

18 tickets. Three are new since the design reviews; ids are appended rather than
renumbered so existing references in `docs/reviews/` stay valid. Dependency order
and numeric order are not the same thing — read §5, not the numbers.

### 4.1 The three new tickets

| ID | Title | Tier | Why it exists |
|---|---|---|---|
| **AIR-16** | Vocabulary single-source and spec lint | `design` | P1. Every enum — commands, errors, verdicts, `decided_by`, audit events, LED states, tone patterns — defined once in `src/airgap/vocab.py`, with a CI test that parses the spec tables and fails on any mismatch. Nothing else may define these inline |
| **AIR-17** | Relay interlock and cycle | `safety-critical` | P3. Rules 4, 4a, 4b, 4c split out of AIR-6: arming state machine, verdict minting, gated close, ungated renew, dwell, `lease_expired` scoping, dead time |
| **AIR-18** | `airgap watch` terminal reader | `standard` | §4.3. A full-fidelity view of the pending request in a terminal, over **`ui_ro`** scope — reads only, no policy-write capability. No browser |

AIR-6 narrows correspondingly to Supervisor **core**: allowlist, typed commands,
clamps, the four rate limits, tick watchdog, and the safe-state transition.

### 4.2 Full set, grouped by subsystem

| Subsystem | Tickets |
|---|---|
| Foundations | AIR-1 scaffold · AIR-16 vocabulary |
| Wire | AIR-2 codec · AIR-3 transport |
| Device | AIR-4 firmware · AIR-5 bring-up *(human)* |
| Persistence | AIR-7 data model · AIR-8 audit chain |
| Safety | AIR-6 supervisor core · AIR-17 interlock and cycle · AIR-11 policy resolver |
| Service | AIR-9 broker · AIR-10 MCP server · AIR-12 warden |
| Surfaces | AIR-18 terminal reader · AIR-13 dashboard |
| Integration | AIR-14 end-to-end · AIR-15 demo runbook |

### 4.3 A consequence of v1.2 that changes the plan

The review-02 I7 fix requires a `high`-risk request to be read somewhere
full-fidelity, because a 16×2 LCD cannot distinguish `users_production` from
`users_prod_bak`. In v1.1 the dashboard was a presentation nicety. **In v1.2 it
is a prerequisite for approving anything high-risk**, which would drag a Next.js
app onto the MVP critical path.

AIR-18 exists to avoid that. A terminal reader gives the same full-fidelity view
with **a materially smaller attack surface than a browser** — no template
rendering, no XSS, no stale tab. Review 02's I7 named "a browser and a template
are more attackable than a 32-character serial write"; a CLI reader is closer to
the serial write than to the browser, so it does not merely relocate the exposure,
it reduces it.

**Q1 was raised here and has now been answered** (2026-08-23): either reader
satisfies §9. `DESIGN.md` v1.3 introduces **full-fidelity reader** as a *role*
with two implementations, notes that the terminal is the safer default, and no
longer names the dashboard as the only one. Consequences, now settled:

1. AIR-18 lands in M2. AIR-13 moves to M4 and is genuinely optional.
2. AIR-14 depends on AIR-18, not AIR-13 — which is what the graph already said.

Nothing in this plan is now conditional on Q1. The earlier draft asserted the
graph one way and the prose the other ("until Q1 is answered, high-risk requires
AIR-13") while §14 forbade any ticket depending on an unresolved contract
question. That contradiction is closed.

---

## 5. Dependency graph and schedule

Every edge below has a reason. An edge with no reason is a scheduling accident and
should be removed.

| Ticket | Depends on | Because |
|---|---|---|
| AIR-1 | — | — |
| AIR-16 | AIR-1 | Needs the package to exist |
| AIR-2 | AIR-16 | Codec imports the command and error enums |
| AIR-4 | AIR-16 | Firmware constants are linted against the same source |
| AIR-7 | AIR-16 | Schema enums come from the same source |
| AIR-3 | AIR-2 | Transport frames what the codec produces |
| AIR-5 | AIR-4 | Cannot bring up firmware that does not compile |
| AIR-8 | AIR-7 | Chain writes to `audit_log` |
| AIR-11 | AIR-7 | Resolver reads `policies`, including `relay_gated` |
| AIR-6 | AIR-3 | `Supervisor.send()` writes through the transport |
| AIR-12 | AIR-11 | Warden's proposal is bounded by the resolver |
| **AIR-17** | AIR-6, AIR-8 | Needs `send()` to command the relay **and** the audit chain, because Rule 4a's defining property is *audit-before-act ordering*, which cannot be asserted without a real chain |
| AIR-9 | AIR-17, AIR-11 | Broker orchestrates the interlock and the resolver |
| AIR-10 | AIR-9 | MCP wraps the broker |
| AIR-18 | AIR-9 | Reads `ui_ro`-scope routes |
| AIR-13 | AIR-9 | Same |
| AIR-14 | AIR-10, AIR-12, AIR-18 | Full chain, and the reader is how high-risk scenarios are asserted |
| AIR-15 | AIR-14, AIR-5 | Runbook needs both a working system and real hardware |

### Waves

**Both tables in this section are machine-verified.** `scripts/validate_plan.py`
parses them out of this file and compares them against `tickets.yaml` edge by
edge and wave by wave, so drift here fails CI rather than surviving to dispatch.
It also checks the graph is acyclic, every dependency resolves, every `reads` path
exists, and every ticket has Context, Scope, Acceptance criteria and Verification
sections. On a mismatch it prints the correct wave table to paste back.

```
python scripts/validate_plan.py
```

Its first run caught this plan and the ticket file disagreeing about five
dependency edges. **At that point it did not yet read this file** — it validated
`tickets.yaml` alone while the sentence above claimed the table was verified, so
the table could have drifted with CI green. It reads both now, which is what
makes the claim true rather than aspirational.


| Wave | Tickets | Parallel | Notes |
|---|---|---|---|
| 1 | AIR-1 | 1 | Everything blocks on it |
| 2 | AIR-16 | 1 | Second gate. Nothing behavioural starts before the vocabulary is single-sourced |
| 3 | AIR-2, AIR-4, AIR-7 | 3 | |
| 4 | AIR-3, AIR-8, AIR-11, **AIR-5** | 3 + human | AIR-5 is human and unblocks nothing until AIR-15; it may run any time from here |
| 5 | AIR-6, AIR-12 | 2 | |
| 6 | **AIR-17** | 1 | Highest-risk ticket. Adversarial session mandatory |
| 7 | AIR-9 | 1 | |
| 8 | AIR-10, AIR-18, AIR-13 | 3 | |
| 9 | AIR-14 | 1 | |
| 10 | AIR-15 | 1 | |

**Critical path:** AIR-1 → 16 → 2 → 3 → 6 → 17 → 9 → 10 → 14 → 15. Ten waves, so
ten sequential agent sessions minimum. **Peak concurrent agent sessions is 3** —
wave 4 holds four tickets but AIR-5 is human, so it does not consume a session.

**Waves are dependency order, not milestone order.** AIR-13 becomes *eligible* in
wave 8 because AIR-9 has merged, but it belongs to M4 and nothing in M2 needs it.
If §13 Q2 lands on "cut", AIR-13 is deferred past the M2 gate rather than run
alongside AIR-10 and AIR-18 — it would otherwise consume one of only three peak
slots to build something the MVP does not require. Eligible is not the same as
scheduled; the orchestrator may hold an M4 ticket until its milestone.

### The real-world dependency nobody schedules

**AIR-5 needs parts that are not in the Student Kit** — a 5V relay module (~$4), a
16×2 I²C LCD (~$6), and something to switch (~$8). Shipping is days.

AIR-5 gates M3 and M4. **Order the parts before wave 1**, not at wave 4. This is
the single most likely cause of a schedule slip and it costs $18 to eliminate
today.

---

## 6. Risk-ordered sequencing

Foundational order and risk order disagree in two places, and where they do, risk
wins.

| Ticket | Risk | Handling |
|---|---|---|
| **AIR-17** interlock | Highest. Two design reviews both found their Critical defects here | Own ticket, `safety-critical`, adversarial session, and its full test matrix is M1's exit criterion |
| **AIR-9** broker | High. Blocking semantics; the spike proved the transport, not this implementation | `design` tier. First thing it must prove is that a call blocks and no route resolves it |
| **AIR-5** hardware | High but *external* — the risk is parts and wiring, not code | Decoupled from the software path entirely; parts ordered up front |
| AIR-8 audit | Medium. A pinned known-good vector protects against silent drift | `safety-critical` + adversary |
| AIR-11 policy | Medium. The never-widen property is the one guarantee surviving a compromised Warden | `safety-critical` + adversary; asserted as a property, not cases |
| AIR-12 warden | Low. Fails safe by construction — anything unparseable becomes `escalate` | `design` |
| AIR-13 dashboard | Low functionally; **it is new attack surface** (I7) | Standard, but no approve route and CSP required |

**On AIR-6 keeping `safety-critical` after the interlock left it.** What remains
is the allowlist, the clamps, the rate limits, and the safe-state transition —
and the last of those is Rule 5, the fail-closed path that every other guarantee
degrades onto when the link dies. A silent bug there does not announce itself;
it waits for the failure it was supposed to catch. The tier stays.

It is still the **right first cut** if §13 Q2 forces one, because it is now the
thinnest safety-critical ticket and its adversary has the least surface to attack.
Cut its adversarial pass before AIR-8's, and neither before AIR-17's or AIR-11's.

Two things are deliberately built earlier than their dependencies demand:

- **AIR-16** could technically come after the codec. It comes before because P1
  says drift is the dominant failure mode and drift is cheapest to prevent at
  time zero.
- **AIR-8** could come after the Supervisor. It comes before because AIR-17's
  audit-before-act ordering is untestable against a stub.

---

## 7. Milestones and exit criteria

Exit criteria are commands that exit 0, not judgments.

### M0 — Foundations green
**Tickets:** AIR-1, AIR-16
**Exit:** `uv sync && uv run ruff check . && uv run mypy src/ && uv run pytest` exits 0.
The layering test fails when `import airgap.transport` is added to `broker.py`.
The spec lint fails when a command is added to `spec/01` without adding it to
`vocab.py`. **Both negative tests must be demonstrated, not assumed.**

### M1 — The interlock is provably correct
**Tickets:** + AIR-2, 3, 4, 6, 7, 8, 17
**Exit:** these two commands exit 0, with their full test lists pasted:

```
uv run pytest tests/test_supervisor.py -v      # AIR-6
uv run pytest tests/test_interlock.py -v       # AIR-17
```

Between them they must cover both lists at the foot of `spec/02`, which that
document now splits by ticket. Specifically: resolve-then-close succeeds (the
v1.1 deadlock stays fixed); `relay_renew` acks `not_closed` on an open contact;
the full cycle asserts in order; a mismatched `req` never closes; a replayed
button closes at most once; `lease_expired` while unarmed touches no queued
request; renewal **continues** past the verdict.

> **This gate used to read "every test obligation in `spec/02` passes", which was
> unscoreable.** `spec/02`'s test list still carried two v1.1 obligations that
> Rules 4b and 4c had already replaced — renewal stopping on resolution, and
> `lease_expired` denying pending requests. A session treating `spec/02` as truth
> (as §10 instructs) would have written the deadlocked tests; a session treating
> AIR-17 as truth would have failed the milestone. Nothing in this plan said
> which won. `spec/02` is now v1.1 and its list is reconciled with its own rules;
> the gate names commands instead of a document.

### M2 — MVP: the gate works end to end
**Tickets:** + AIR-9, 10, 11, 12, 18
**Exit:** an MCP client blocks on `request_approval`; a scripted approve returns
`APPROVED: <reason>`; a Warden `auto_approve` on a policy-blocked tool returns
`DENIED`; an empty policy table escalates everything while still honouring a
Warden `block`; **no broker route resolves a request, for any `decided_by`**; and
each of the three token scopes is confined to its own routes — `agent` cannot
reach a `ui*` route, no `ui*` token can reach `POST /request_approval`, and
`ui_ro` is refused by `PUT /policies/{pattern}`.

### M3 — Physically enforced
**Tickets:** + AIR-5
**Exit:** all eight bring-up items, including **both** fail-safe mechanisms —
item 6 (cable pull) and item 7 (broker killed, USB powered, contact opens within
10 s). Passing one says nothing about the other.

### M4 — Demo ready
**Tickets:** + AIR-13, 14, 15
**Exit:** `uv run python scripts/preflight.py` exits 0 with hardware attached;
a cold machine reaches a working demo following `DEMO.md` alone; the 60-second
narrative runs without intervention.

---

## 8. Verification strategy

| Layer | Scope | Where |
|---|---|---|
| **Spec lint** | Vocabulary in docs matches `vocab.py`, both directions | AIR-16, CI on every push |
| **Layering** | Only `supervisor.py` imports `transport.py`; `protocol.py` and `policy.py` import nothing internal **except `airgap.vocab`**, which imports nothing internal at all | AIR-1, CI |
| **Plan/ticket agreement** | §5's two tables match `tickets.yaml` edge for edge and wave for wave | `validate_plan.py`, CI |
| **Unit** | Every module has a peer test file | Each ticket |
| **Property** | Codec round-trip (`hypothesis`); policy never widens, asserted as an invariant over generated inputs | AIR-2, AIR-11 |
| **Adversarial** | A second, independent session tries to defeat the protection | AIR-6, 8, 11, 17 |
| **Integration** | Full chain on `MockTransport` + stubbed LLM | AIR-14 |
| **Hardware** | Bring-up checklist | AIR-5, human, once |

**No live API calls and no physical hardware in the automated suite.** A ticket
needing either says so in its acceptance criteria; AIR-5 is the only one.

**Negative tests are first-class.** A test that only proves the happy path is not
coverage of a safety interlock. Both design reviews found Critical defects that a
happy-path test would have sailed past.

---

## 9. Dispatch model

**Implementation runs on Codex 5.6.** The tier determines the model and the
effort mode; nothing is chosen per ticket. This table is the only place the
mapping is written down — a ticket's Linear description renders it from here, so
the two cannot disagree.

| Tier | Tickets | Codex model | Effort | Extra |
|---|---|---|---|---|
| `mechanical` | AIR-1, 15 | **5.6 Luna** | `low` | — |
| `standard` | AIR-2, 3, 5, 7, 10, 13, 18 | **5.6 Terra** | `medium` | — |
| `design` | AIR-4, 9, 12, 14, 16 | **5.6 Sol** | `high` | PR explains the structural choice |
| `safety-critical` | AIR-6, 8, 11, 17 | **5.6 Sol** | `xhigh` | **Second independent adversarial session, also Sol / `xhigh`** |

Two notes on the mapping. **The adversarial pass is never cheaper than the
implementation it attacks** — an attacker weaker than the builder finds nothing
and produces false assurance, which is worse than skipping the pass honestly.
And **AIR-5 carries `standard` but is `human: true`**; the tier is vestigial
there and no model is ever assigned. Never dispatch it.

**Session budget:** 17 agent tickets + 4 adversarial = 21 base, + ~25% rework ≈
**26 sessions**. AIR-5 is human and consumes none. Peak concurrency is 3, set by
the width of waves 3, 4 and 8 rather than by any tooling limit. The 21 is printed
by `scripts/validate_plan.py`, not maintained by hand; §12 breaks it down by
milestone and must sum to the same number.

**Before dispatching anything**, `python scripts/validate_plan.py` must exit 0.
Note the bare `python` — it is standard-library-only precisely so that it runs at
wave 0, before AIR-1 has written `pyproject.toml` and before `uv sync` can
install anything. Keep it that way.

**Human gates:** 5 — one each at **M0, M1, M2, M3 and M4**. The orchestrator
reports and waits; it does not advance a milestone on its own. (An earlier draft
said "4 — one per milestone" while §7 listed five, which invites an orchestrator
to silently skip one. There are five milestones and five gates.)

---

## 10. Contract change protocol

`docs/spec/` is frozen. This is how a legitimate change happens, and it exists
because both design reviews changed contracts and review 02's defect *was* a
contract change applied without a sibling sweep (P2).

1. **No agent edits `docs/spec/` or `DESIGN.md`. Ever.** A PR touching either is
   rejected on sight.
2. A session that believes a contract is wrong **stops**, comments on the ticket
   with the specific contradiction — quote both sides — and does not code around
   it. It does not pick an interpretation.
3. The orchestrator escalates to the human. It does not adjudicate.
4. If the human accepts the change:
   a. Human edits the contract and bumps its version.
   b. **Sibling sweep.** Every other document referencing the changed concept is
      checked in the same commit. This is not optional: the v1.1 lease was
      correct in `DESIGN.md` and incoherent against `spec/02`, and shipped that
      way for a full review cycle.
   c. Record it in `docs/reviews/` with the disposition.
   d. **Identify every ticket whose acceptance criteria are now stale, including
      merged ones**, and reopen them.
5. Contract changes after M1 are expensive by design. That is the whole reason
   two review rounds happened before a line of code.

**A session finding a genuine contract defect is a success, not a failure.** It is
the most valuable thing a session can produce on this project, and it should be
reported as such rather than worked around.

---

## 11. Failure handling

| Situation | Response |
|---|---|
| Ticket fails CI | Back to the same session with the specific failing item. Orchestrator does not fix it |
| Session blocked by a dependency not in `depends_on` | Stop, comment, escalate. The graph is wrong and must be corrected in this file |
| Adversarial session finds a real hole | Back to the implementer as a review comment. If the hole is in the *contract*, §10 |
| Two sessions read one contract differently | The contract is ambiguous. §10. **Never resolve by picking a side** |
| Ticket exceeds its context | Split it and record the split here. Do not let a session compress a safety-critical ticket to fit |
| Parts have not arrived | M3/M4 slip. M0–M2 are unaffected — that decoupling is deliberate |
| Blocking call fails against a real client | R5. The handle-and-poll fallback is designed in `spec/03` but not built. Build it only then |

---

## 12. Estimates

Wall-clock is dominated by human review gates, not agent time, so estimates are
given in sessions and gates rather than hours.

| Milestone | New tickets | Agent sessions | Human gates | Blocked by anything external |
|---|---|---|---|---|
| M0 | AIR-1, AIR-16 | 2 | 1 | — |
| M1 | AIR-2, AIR-3, AIR-4, AIR-6, AIR-7, AIR-8, AIR-17 | 7 + 3 adversarial | 1 | — |
| M2 | AIR-9, AIR-10, AIR-11, AIR-12, AIR-18 | 5 + 1 adversarial | 1 | — |
| M3 | AIR-5 *(human)* | 0 | 1 | **Parts delivery** |
| M4 | AIR-13, AIR-14, AIR-15 | 3 | 1 | M3 |

**This table is machine-checked too.** `scripts/validate_plan.py` asserts that
every ticket appears in exactly one milestone row, that the agent-session numbers
sum to the budget it computes from the graph (21), and that the gates sum to 5.
Ticket ids are written out in full rather than as "AIR-2, 3, 4" precisely so a
machine can read them.

A previous draft put M1 at "9 + 3", double-counting M0's two tickets, and the
claim that these numbers "must stay equal" was checked by eye. It is not any
more.

M3 has zero agent sessions and is the most likely thing to slip, because it
depends on shipping and a soldering iron rather than on tokens.

---

## 13. Open planning questions

**Q1 — Does AIR-18 satisfy the high-risk reader requirement? — ANSWERED, yes.**
Either reader qualifies. `DESIGN.md` v1.3 §4.2 and §9 now describe a
full-fidelity **reader** as a role, with the terminal preferred where the
deployment allows it, and `spec/03` v1.1 adds the `ui_ro` scope so the terminal
reader cannot hold a policy-write token. AIR-18 is in M2; AIR-13 is in M4 and
optional. Closed 2026-08-23.

**Q2 — Is 26 sessions an acceptable envelope?** If not, the lever is the
adversarial pass on AIR-6. I would not cut it on AIR-17, AIR-11 or AIR-8.
See §6 for why AIR-6 keeps its tier but is the right thing to cut first.

**Q3 — Should M1 include a hardware smoke test?** Currently all hardware is in
M3. An early `ping` against a real board would surface wiring problems weeks
sooner, at the cost of needing parts earlier. Leaning yes if parts arrive early.

---

## 14. Exit criteria for this plan

Planning is done when all of these are true:

- [x] `python scripts/validate_plan.py` exits 0 — **on a bare checkout**, with no
      `pyproject.toml` and nothing installed
- [x] Every ticket has a verification command that could exit 0 on a real machine
- [x] The dependency graph is acyclic and every edge has a stated reason (§5)
- [x] §5's tables agree with `tickets.yaml`, checked by the validator, not by eye
- [x] No ticket depends on an unresolved contract question
- [x] Q1 is answered, since it moves a ticket between milestones
- [x] Every milestone gate is a command, not a document reference. M1's used to
      name `spec/02`, whose own test list contradicted its own rules
- [x] A consistency sweep over `DESIGN.md` + `spec/` finds no contradictions,
      **and the recurring ones are now machine-checked** — `validate_plan.py`
      carries a stale-phrase list seeded with every contradiction the three
      review rounds actually found, so the same sentence cannot survive the next
      sweep. This box was ticked once while it was false (plan review 02 C1);
      it is no longer a matter of anyone's memory
- [ ] The human has signed off

Nothing in `DESIGN.md` §12's open items blocks this: Q5 (cut-short dwell), R7–R10
and F10 are all accepted risks with no ticket depending on their resolution.
