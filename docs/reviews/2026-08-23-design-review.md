# Design Review 01 — disposition

**Reviewed:** `docs/DESIGN.md` v1.0, against `spec/00`–`05`, `AGENTS.md`, and
tickets AIR-9 / AIR-13.
**Verdict given:** not ready to implement from. 3 Critical, 12 Important, 4 Minor.
**Outcome:** all 19 findings accepted. `DESIGN.md` is now v1.1 and six contract
documents changed with it.

## The load-bearing finding

The review's framing was the most useful part of it:

> §4.3–§4.4 are honest. G1, T3, T1, and the frozen overview still sell the
> stronger thesis — geometry stops the agent. Those cannot both be implemented.
> The first faithful build will grow a clickable localhost approve path and call
> it an air gap.

That was correct, and it was the root of C1, I10 and half the Important findings.
v1.0 had an honest §4.4 bolted onto goals and a threat table written before it.
v1.1 propagates §4.4 upward into G1, T1, T3, and outward into `spec/00` and
`AGENTS.md` §1.

## Disposition

| ID | Accepted | Fix |
|---|---|---|
| **C1** | yes | `decided_by=human` is minted only by the Supervisor after Rule 4, in-process. `/decide` rejects it with `403` from every caller. Broker binds `127.0.0.1`, requires a startup token, rejects requests carrying `Origin`. Dashboard has no approve route at all. G1 restated as a claim about the buttons. → D11, spec/02 4a, spec/03, invariant 8 |
| **C2** | yes | §7.1 and §7.3 reordered: audit row written before the relay moves and before callers are released. Ordering is now an explicit test obligation, not just a diagram | 
| **C3** | yes | One call graph everywhere: bridge decodes → Supervisor interlock → Supervisor resolves in-process. `/decide` is system/policy only. Propagated to §7, §11, spec/00, spec/02, spec/03 |
| **I1** | yes | D8 split. Cable pull → passive coil de-energise. Broker killed with USB powered → **device-side relay lease**, renew 3 s, expire 10 s. New protocol field `lease_ms`, new event `lease_expired`, new bring-up item 7 |
| **I2** | yes | U4 now resolves `expired`, not "denied". `expired` and `link_lost` documented as peer verdicts, not reason strings |
| **I3** | yes | spec/02 Rule 5 changed from `denied`/reason `link_lost` to `verdict="link_lost"`, matching spec/03 and spec/05 |
| **I4** | yes | G6 rewritten: the one-way valve is the **policy layer, not the LLM**. Z3 restated. The Warden proposes; auto-approve is a widening relative to deny-by-default and is named as such |
| **I5** | yes | Auto-approve is now visually silent — LCD only, no LED, no flag, no tone. Green means "a human just approved" and nothing else. U2 and §9 agree |
| **I6** | yes | Button named `never` throughout (ALWAYS-DENY retired). Q1 resolved: persists, stored as a `policies` block row. T4 now cites a **broker-side** rate limit (NF12, 6/min per tool per actor); the serial rate limits bound a different thing |
| **I7** | yes | T1 marked "holds when Z2 intact". High-risk requests no longer put a truncatable identifier on the LCD — they show a short code and `SEE DASHBOARD`. §13's T1 criterion rewritten to something honestly testable |
| **I8** | yes | Unmatched policy → `escalate`, stated in spec/05. New **N-T8**: the policy table is software-writable and inherits Z2's trust. R7 records that gating widening edits behind the dial is the real fix |
| **I9** | yes | D9 downgraded. Chain is unkeyed and unanchored; a Postgres superuser can rewrite and recompute it. Now claimed as tamper-evident **below superuser**. EEPROM anchoring named as the intended fix (R9) |
| **I10** | yes | §4.4 "Partially" → **"No"**. `spec/00` and `AGENTS.md` §1 rewritten so agents reading them first do not inherit the stronger claim |
| **I11** | yes | Warden injection screening demoted to weak secondary. U3's real defence restated as envelope membership. D6's bound restated precisely: a compromised Warden can fully exploit the existing envelope but cannot widen it. Arg-vs-name matching gap recorded as R8 |
| **I12** | yes | F3 now matches spec/04: device reset mid-request resolves `denied` / `device_reset`. Flag direction corrected — boot drives it **up** |
| **M1** | yes | Principle reworded: the device alerts once per request and never nags; a pattern may contain several beeps |
| **M2** | yes | D3 corrected to four LED states (`off`/`green`/`amber`/`red`) |
| **M3** | yes | §9 states the LCD is optional and never carries the only copy of a distinction; LCD-less builds distinguish high-risk by tone and colour |
| **M4** | yes | D5 gains a mandatory 2 s arming dead time plus an all-buttons-released requirement, closing the shifted-in-time ambiguity |

## Disagreements

None. Every finding was reproducible against the documents as written.

## Extension beyond the review

C3 noted that policy auto-approve calling `/decide` is "a third closer Rule 4
never sees." That understates the consequence. Rule 4 requires a verified button
press before the relay may close, and an auto-approved request never produces
one — so **auto-approval and enforcement-boundary mode are structurally
incompatible**. A relay-gated action can never be auto-approved, whatever the
policy table says.

v1.0 left this to emerge as a surprise during AIR-11. It is now **D12**, enforced
by the absence of an auto-approve branch in the interlock rather than by
configuration discipline.

## Tickets changed

AIR-4 (lease), AIR-5 (bring-up item 7), AIR-6 (Rule 4a/4b, dead time, audit
ordering), AIR-9 (`/decide` rejection, rate limit, `expired`), AIR-11 (unmatched
default, relay-gated exclusion), AIR-13 (no approve control), AIR-14 (four new
end-to-end scenarios).

## Still open after v1.1

- R10 — the 10 s lease window is the worst unsupervised interval in the system.
  Accepted, tunable.
- R9 — audit anchoring is designed, not built.
- Q4 — fixed vs per-deployment lease interval. Leaning fixed.
- F10 — welded relay remains real and unmitigated.
