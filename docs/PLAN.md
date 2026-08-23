# Airgap — Implementation Plan

**Version:** 1.0
**Date:** 2026-08-23
**Status:** awaiting review
**Covers:** `DESIGN.md` v1.2 and `spec/00`–`05` as of commit `fcdcf1b`

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
| **M2 — MVP** | A blocking MCP approval gate, triaged, policy-bounded, audited | Physical hardware, high-risk readability |
| **M3** | All of that, physically enforced on a real device | A presentable demo |
| **M4** | The demo | — |

**M2 is the MVP** — the thesis proven end to end. One caveat, see §4.3: M2
supports **low and medium risk only**, because v1.2 made a full-fidelity reader a
prerequisite for high-risk approval.

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
| **AIR-18** | `airgap watch` terminal reader | `standard` | §4.3. A full-fidelity view of the pending request in a terminal, over `ui` scope. No browser |

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

Two consequences, and the second needs a human decision:

1. AIR-18 lands in M2; AIR-13 moves to M4 and becomes genuinely optional.
2. **`DESIGN.md` §9 currently names the dashboard specifically.** If AIR-18 is
   accepted as the high-risk reader, §9 needs to say "a full-fidelity reader
   (terminal or dashboard)". That is a design-intent change, so per §10 it is
   **not mine to make** — raised in §13 Q1.

Until Q1 is answered, treat high-risk as requiring AIR-13.

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
| AIR-18 | AIR-9 | Reads `ui`-scope routes |
| AIR-13 | AIR-9 | Same |
| AIR-14 | AIR-10, AIR-12, AIR-18 | Full chain, and the reader is how high-risk scenarios are asserted |
| AIR-15 | AIR-14, AIR-5 | Runbook needs both a working system and real hardware |

### Waves

The table below is **machine-verified**, not hand-maintained. `scripts/validate_plan.py`
recomputes it from `tickets.yaml` and also checks that the graph is acyclic, every
dependency resolves, every `reads` path exists, and every ticket has Context, Scope,
Acceptance criteria and Verification sections. The first run of it caught this plan
and the ticket file disagreeing about five dependency edges. Run it before dispatch
and in CI.

```
python scripts/validate_plan.py
```


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
**Exit:** every test obligation in `spec/02` passes, each failure mode
independently. Specifically: resolve-then-close succeeds (the v1.1 deadlock stays
fixed); `relay_renew` is rejected on an open contact; the full cycle asserts in
order; a mismatched `req` never closes; a replayed button closes at most once;
`lease_expired` while unarmed touches no queued request.

### M2 — MVP: the gate works end to end
**Tickets:** + AIR-9, 10, 11, 12, 18
**Exit:** an MCP client blocks on `request_approval`; a scripted approve returns
`APPROVED: <reason>`; a Warden `auto_approve` on a policy-blocked tool returns
`DENIED`; an empty policy table escalates everything while still honouring a
Warden `block`; **no broker route resolves a request, for any `decided_by`**; the
`agent` token cannot reach a `ui` route.

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
| **Layering** | Only `supervisor.py` imports `transport.py`; `protocol.py` and `policy.py` import nothing internal | AIR-1, CI |
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

| Tier | Tickets | Model / effort | Extra |
|---|---|---|---|
| `mechanical` | AIR-1, 15 | smallest capable, low | — |
| `standard` | AIR-2, 3, 5, 7, 10, 13, 18 | mid, medium | — |
| `design` | AIR-4, 9, 12, 14, 16 | strongest, high | PR explains the structural choice |
| `safety-critical` | AIR-6, 8, 11, 17 | strongest, maximum | **Second independent adversarial session** |

**Session budget:** 17 agent tickets + 4 adversarial = 21 base, + ~25% rework ≈
**26 sessions**. AIR-5 is human and consumes none. Peak concurrency is 3, set by
the width of waves 3, 4 and 8 rather than by any tooling limit. Confirmed by
`scripts/validate_plan.py`.

**Human gates:** 4 — one per milestone. The orchestrator reports and waits; it
does not advance a milestone on its own.

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

| Milestone | Agent sessions | Human gates | Blocked by anything external |
|---|---|---|---|
| M0 | 2 | 1 | — |
| M1 | 9 + 3 adversarial | 1 | — |
| M2 | 5 + 1 adversarial | 1 | — |
| M3 | 0 | 1 | **Parts delivery** |
| M4 | 3 | 1 | M3 |

M3 has zero agent sessions and is the most likely thing to slip, because it
depends on shipping and a soldering iron rather than on tokens.

---

## 13. Open planning questions

**Q1 — Does AIR-18 satisfy the high-risk reader requirement?**
`DESIGN.md` §9 names the dashboard. If a terminal reader qualifies — and §4.3
argues it is *better*, being a smaller attack surface — then §9 needs rewording
and AIR-13 leaves the MVP path. **This is a design-intent change and therefore
not mine to make (§10).** Until answered, high-risk requires AIR-13 and the
dashboard sits on the M2 path.

**Q2 — Is 26 sessions an acceptable envelope?** If not, the lever is the
adversarial passes on AIR-6 and AIR-8. I would not cut them on AIR-17 or AIR-11.

**Q3 — Should M1 include a hardware smoke test?** Currently all hardware is in
M3. An early `ping` against a real board would surface wiring problems weeks
sooner, at the cost of needing parts earlier. Leaning yes if parts arrive early.

---

## 14. Exit criteria for this plan

Planning is done when all of these are true:

- [ ] `python scripts/validate_plan.py` exits 0
- [ ] Every ticket has a verification command that could exit 0 on a real machine
- [ ] The dependency graph is acyclic and every edge has a stated reason (§5)
- [ ] No ticket depends on an unresolved contract question
- [ ] Q1 is answered, since it moves a ticket between milestones
- [ ] A consistency sweep over `DESIGN.md` + `spec/` finds no contradictions
- [ ] The human has signed off

Nothing in `DESIGN.md` §12's open items blocks this: Q5 (cut-short dwell), R7–R10
and F10 are all accepted risks with no ticket depending on their resolution.
