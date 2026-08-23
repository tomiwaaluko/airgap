# Plan Review 01 — disposition

**Reviewed:** `PLAN.md` v1.0 against `DESIGN.md` v1.2, `spec/00`–`05`,
`tickets.yaml`, `AGENTS.md`, and `scripts/validate_plan.py`.
**Verdict given:** do not start wave 1 on this text. 3 Critical, 7 Important,
4 Minor.
**Outcome:** all 14 accepted, one with a scoping change and one with a partial
disagreement recorded below. `PLAN.md` is now v1.1; `DESIGN.md` v1.3; `spec/02`
and `spec/03` v1.1.

## The load-bearing finding

> M1's exit criterion is "every test obligation in `spec/02` passes."
> `spec/02` still requires renewal to stop on resolution and `lease_expired` to
> resolve pending as denied. AIR-17 forbids both.

This is review 02's C1 — the v1.1 relay-lease deadlock — **reappearing as a
milestone gate**, and it is worth being precise about how.

The v1.2 sweep rewrote Rules 4, 4a, 4b and 4c correctly. It did not touch the
test-obligation list at the foot of the same file. So `spec/02` shipped
contradicting itself, seventy lines apart: Rule 4b said renewal "does **not**
stop on verdict — that was the v1.1 contradiction", and the test list below it
asked for renewal that "**stops** immediately on resolution". Rule 4c said a
stray `lease_expired` "must not resolve, deny, or disturb any queued request",
and the test list asked for one that "resolves any pending request as `denied`".

Then `PLAN.md` made that document a milestone gate. A session following §10 —
"the contract is truth" — writes the deadlocked tests and passes M1. A session
following AIR-17 writes the correct tests and fails it. Nothing said which wins.

Two things make this the review's most useful finding. First, it is P2 — "a fix
to one document leaves its siblings stale" — happening **inside the document that
names P2**, in the same commit that named it. Second, the sibling that went stale
was not another file, it was the bottom of the same file, which is the version of
the failure a sibling sweep is least likely to catch because the file was
obviously already open.

Fixes: `spec/02`'s test list is now split by ticket and reconciled against its own
rules, with both stale obligations replaced by their inverses and labelled as
such. M1's gate names two commands instead of a document. AIR-16's lint now
covers `spec/02`, which finding I4 pointed out it did not — the lint's coverage
map had excluded the one document with live drift in it.

## Disposition

| ID | Accepted | Fix |
|---|---|---|
| **C1** | yes | `spec/02` → v1.1. Test obligations split into a Rules 1/2/3/5/6 list (AIR-6) and a Rules 4/4a/4b/4c list (AIR-17), both reconciled with the rules above them. `PLAN.md` M1 exits on two `pytest` commands, not on a document. AIR-6 and AIR-17 each point at their own list and state they are not scored against the other's. Also fixed: "the eight commands" → nine |
| **C2** | yes | Q1 answered by the human: **either reader qualifies.** `DESIGN.md` v1.3 makes "full-fidelity reader" a role with two implementations and prefers the terminal on attack-surface grounds. `PLAN.md` §4.3 and §13 no longer argue both sides; AIR-14's existing dependency on AIR-18 is now correct rather than accidentally correct |
| **C3** | yes | `validate_plan.py` rewritten standard-library-only with a strict YAML-subset parser that refuses constructs it does not understand. It cross-checks against PyYAML when PyYAML happens to be importable, so the hand parser cannot drift silently. Separately, `hypothesis`, `freezegun` and `rich` added to the AGENTS.md stack and to AIR-1's scope |
| **I1** | yes, rescoped | See below |
| **I2** | yes | Pure core may import `airgap.vocab` and nothing else; `vocab.py` imports nothing internal. Stated in AGENTS.md §5 with the rationale, in `PLAN.md` P1, in §8's layering row, in AIR-1's layering test, and in AIR-2's acceptance criteria. AIR-1 must demonstrate the test fails on `transport` and passes on `vocab` |
| **I3** | yes | New `ui_ro` token scope in `spec/03` v1.1: reads only, no write route, rejects `Origin`, and `403` from `PUT /policies`. AIR-18 holds it. Reflected in `DESIGN.md` §11 and N-T8 |
| **I4** | yes | AIR-16 lints `spec/02` as well, and must catch the real historical defect (nine commands → eight) as an acceptance criterion |
| **I5** | yes | Folded into C3. AIR-1 now declares the complete set and says explicitly that a later ticket needing one of these has found a defect in AIR-1, not a reason to edit `pyproject.toml` quietly |
| **I6** | yes | `validate_plan.py` now parses both §5 tables out of `PLAN.md` and compares them to `tickets.yaml` edge by edge and wave by wave, printing the correct wave table on mismatch. The third copy in `ORCHESTRATOR_PROMPT.md` is deleted, and the orchestrator is told to dispatch from the validator's output rather than transcribe it |
| **I7** | yes | AIR-14's first scenario is now the **consent-channel** happy path, which sends no relay command and must assert its absence. The ticket says why order matters: the first scenario is the one that gets copied |
| **M1** | yes | Five gates, M0–M4, in `PLAN.md` §9, §12 and the orchestrator prompt |
| **M2** | yes | §12 rebuilt with a per-milestone ticket column and a total row: 17 + 4 adversarial = 21, matching what the validator prints. M1 was double-counting M0 |
| **M3** | partial | See below |
| **M4** | yes | `check_injection_signature` removed from AIR-12, with a note that `DESIGN.md` T2 demoted LLM-side injection screening from a control to not-a-control, and that shipping the tool would advertise a withdrawn defence |

## One rescoping, on I1

Accepted — AIR-16's stale-reference lint as written would have failed CI on wave
2 and been unfixable from inside the ticket, since §10 forbids an agent editing
`docs/spec/`. The frozen contracts, `AGENTS.md` and the orchestrator prompt all
contain the string `/decide` in order to say the endpoint is gone.

The fix is not to loosen the lint but to point it at the right thing. Prose
explaining a removal is the documentation working correctly. A route in `src/` is
the regression. So the lint now covers `src/` and `tests/`, plus one structural
assertion that `spec/03` declares no route-table row for the term, and the ticket
carries both a positive and a negative acceptance criterion: adding
`@app.post("/decide")` to the broker must fail, and a sentence saying `/decide`
was removed must not.

## One partial disagreement, on M3

> AIR-6 is safety-critical and gets an adversarial session after the interlock
> was moved to AIR-17. Remaining AIR-6 is clamps and rate limits.

The description undersells what stayed. AIR-6 also owns **Rule 5, the safe-state
transition** — the fail-closed path that every other guarantee degrades onto when
the serial link dies, and the thing that decides whether a pending request
resolves `link_lost` or hangs. A defect there is silent by construction: it waits
for the failure it was supposed to catch. That is a `safety-critical` module by
any definition that is not purely about line count, so **the tier stays**.

Where the finding is right, and where I have taken it: AIR-6 is now the thinnest
safety-critical ticket, so if Q2's budget question forces a cut, its adversarial
pass is the correct first one to lose. §6 says so explicitly and orders the rest —
cut AIR-6's before AIR-8's, and neither before AIR-17's or AIR-11's. The reviewer
identified the right lever; I disagree only that the tier should move with it.

## What changed, by file

| File | Change |
|---|---|
| `docs/DESIGN.md` | → v1.3. Full-fidelity reader as a role; terminal preferred, with the ANSI-injection caveat stated; `ui_ro` in §11; N-T8 and R4 reworded; §13 token-scope test obligation covers all three scopes |
| `docs/spec/02-supervisor.md` | → v1.1. Test obligations split by ticket and reconciled with Rules 4b/4c; nine commands |
| `docs/spec/03-broker-api.md` | → v1.1. `ui_ro` scope |
| `docs/PLAN.md` | → v1.1. M1 gate, Q1 closed, five gates, §12 arithmetic, §5 verification claim made true, P1 vocab carve-out, §6 AIR-6 note |
| `docs/tickets/tickets.yaml` | AIR-1, 2, 6, 12, 14, 16, 17, 18 |
| `scripts/validate_plan.py` | Rewritten: stdlib-only, PLAN.md cross-check |
| `docs/ORCHESTRATOR_PROMPT.md` | Step 0 bare `python`, Step 1 reconcile rather than create, wave list deleted, five gates |
| `AGENTS.md` | Pure-core rule, dependency stack |

## Still open

- **Q2** — 26-session envelope. Unanswered; §6 now records the cut order if it
  needs one.
- **Q3** — early hardware smoke test in M1. Unanswered, and cheap to decide once
  parts arrive.
- Everything `DESIGN.md` §12 already lists as accepted: F10 welded relay, R9
  audit anchoring, R10 lease window, Q5 cut-short dwell. None blocks dispatch.
