# Plan Review 02 — disposition

**Reviewed:** the review at
[`2026-08-23-plan-review-02.md`](2026-08-23-plan-review-02.md) — 1 Critical,
7 Important, 5 Minor against `PLAN.md` v1.1.
**Outcome:** all 13 accepted. `PLAN.md` → v1.2, `DESIGN.md` → v1.4, `spec/00` and
`spec/01` → v1.1. No disagreements.

## The load-bearing finding, and why it kept happening

C1: `spec/01` still told the host to resolve a pending request as `denied` on
`lease_expired` — the exact sentence `spec/02` v1.1 had removed from its own
footer and labelled as the defect.

Review 01's C1 was the same contradiction between `spec/02`'s rules and
`spec/02`'s test list. The disposition fixed the file it was pointed at and did
not open `spec/01`. **P2 recurred inside the sweep that claimed to close P2** —
which is now three consecutive rounds where the fix created the next finding:

| Round | Fix applied | Defect it created |
|---|---|---|
| Design review 01 | added the relay lease | renewal reused a gated command → deadlock (design 02 C1) |
| Design review 02 | split `relay` / `relay_renew`, rewrote Rule 4b/4c | `spec/02`'s test list kept the old behaviour (plan 01 C1) |
| Plan review 01 | fixed `spec/02`'s list and the M1 gate | `spec/01` kept the old behaviour (plan 02 C1) |

Three rounds, one shape: **a correction lands in the file being read and not in
its neighbour.** §10 already required a sibling sweep and it was performed each
time, by a reader who believed they were done.

So the fix this round is not another careful sweep. `scripts/validate_plan.py`
now carries a **stale-phrase list** seeded with every contradiction the four
rounds actually produced, and fails before dispatch on any recurrence. It found
five leftovers on its first run, two of them in `DESIGN.md` that this
disposition had not otherwise looked at.

It inherits review 01 I1's lesson: prose retracting a phrase must quote it, so a
paragraph marked `stale-ok` is exempt. Two such paragraphs exist and both are
annotated with why. `grep -rn stale-ok` answers "what are we still deliberately
quoting?" in one command.

## Disposition

| ID | Fix |
|---|---|
| **C1** | `spec/01` → v1.1. `ev.lease_expired` now defers to Rule 4c: armed and mid-dwell is a fault that leaves the verdict `approved`; unarmed is a stray that touches nothing. The retracted sentence is quoted in a marked block so a reader with a cached copy recognises it |
| **I1** | §3's cut-line table said M2 lacked "high-risk readability" and supported "low and medium risk only" — the pre-Q1 draft, contradicting §4.3, and §3 is what an orchestrator reads first. M2 now covers all three risk classes. `DESIGN.md` §9's LCD copy `SEE DASHBOARD` → `SEE READER`: at M2 the dashboard does not exist, so the device was pointing the operator at a surface that had not been built |
| **I2** | `PLAN.md` §4.1 says `ui_ro` |
| **I3** | AIR-9 now **mints** the per-arm short code and drives LCD copy from the risk class — nothing produced the nonce AIR-18 asserts against and T1 depends on. Re-arming mints a fresh code. AIR-9 and AIR-14 both carry `DESIGN.md` §13's T1 obligation: a high-risk request puts no truncatable identifier on the LCD, and the reader carries the untruncated action |
| **I4** | AIR-11's "no imports from elsewhere in `airgap`" → the vocab carve-out, with the reason. Review 01 I2 had been applied to the codec and not to the other pure-core module |
| **I5** | AIR-9's `Origin` handling keys on **token scope**, not route. `GET /pending` is reachable by both `ui` and `ui_ro`, so the route-keyed rule was not well-defined — implemented literally it rejects the dashboard's own read of the queue, which is design review 02's blanket-`Origin` break replayed on the shared path |
| **I6** | AIR-17 reproduces `spec/02`'s auto-approve obligation, with the D12 distinction spelled out: `relay_gated` is a policy bit, `auto_approve` is a verdict, and the dangerous configuration is both at once |
| **I7** | `spec/01`'s `err` list gains `not_closed` |
| **M1** | AIR-13 requires a tested CSP and a hostile-markup fixture. It is the browser reader, and I7 accepted it as new attack surface precisely because XSS there is a T1 bypass |
| **M2** | `PLAN.md` §5 states that waves are dependency order, not milestone order, and that AIR-13 may be held past M2 rather than spend one of three peak slots on an M4 ticket |
| **M3** | Orchestrator prompt's closing line asks for the reconciliation result, not "created issue ids" |
| **M4** | `spec/00` → v1.1, `vocab.py` in the module layout. Wave 1 had to invent a file the frozen overview did not name |
| **M5** | `validate_plan.py` parses §12 and asserts every ticket appears in exactly one milestone, the session numbers sum to the graph's budget, and gates equal milestones. §12's ticket ids are written out in full so a machine can read them |

## What the validator checks now

Four things, all of which were prose claims someone had to verify by eye:

1. the graph — acyclic, resolvable, `reads` paths exist, required sections
2. §5's two tables against `tickets.yaml`, edge by edge and wave by wave
3. §12's arithmetic against the budget the graph computes
4. the stale-phrase sweep

Each was added the round after a review found the corresponding claim false.
Both new checks were negative-tested rather than assumed:

```
- PLAN.md section 12 sessions sum to 22, but the graph gives a budget of 21
- docs/PLAN.md:494 contains removed phrase 'resolve any pending request as `denied`'
```

## Still open

Unchanged by this round, and none of it blocks dispatch: **Q2** (26-session
envelope; §6 records the cut order if it needs one), **Q3** (early hardware smoke
test), and `DESIGN.md` §12's accepted risks F10, R9, R10, Q5.
