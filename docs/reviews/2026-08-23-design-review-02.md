# Design Review 02 — disposition

**Reviewed:** `DESIGN.md` v1.1 and `spec/00`–`05` after review 01's 19 findings
were written in.
**Verdict given:** not ready to implement from. 2 Critical, 7 Important, 5 Minor.
**Outcome:** all 14 accepted. `DESIGN.md` is now v1.2; six contracts changed.

## The load-bearing finding

> v1.1 fixed the thesis. The lease fights the interlock.

Exactly right, and it was a defect introduced *by the v1.1 fix itself*. Review 01
found that a killed broker with USB still powered would leave the relay closed
forever, so v1.1 added a lease — and implemented renewal by re-sending
`relay(closed=true)`, which is Rule-4-gated. That made Rule 4, 4a, 4b and NF11
mutually unsatisfiable:

- Rule 4a resolves the request, *then* sends the close. After resolution nothing
  is pending, so Rule 4's condition 1 fails and **the contact can never close**.
- Rule 4b required a 3 s resend while also saying stop renewing on resolution —
  which under 4a is before the first close.
- Condition 5's 30 s bound would have killed renewals at 30 s regardless.

The spec/02 test list asked for both sides of the contradiction. A faithful
implementer would have had to pick a winner silently, which is the failure mode
this whole documentation structure exists to prevent.

The fix is structural: **close and hold are now different commands.**
`relay` is gated; `relay_renew` is not, because it acks `not_closed` on an open
contact and therefore cannot create a closure — only extend one a gated close
already authorised. Rule 4's condition 1 changed from "one pending request" to
"the Supervisor is ARMED with R", which is Supervisor state that survives
resolution.

## Disposition

| ID | Accepted | Fix |
|---|---|---|
| **C1** | yes | `relay_renew` added as a separate ungated keepalive. Rule 4 condition 1 rewritten to ARMED-state. Rule 4b rewritten: start renewing after the first accepted close, stop on open / dwell / safe state / disarm — never on verdict. → spec/01, spec/02, spec/04, D8 |
| **C2** | yes | The relay cycle now has a defined end: a **dwell window**, `policies.dwell_s`, default 60 s, after which the host opens the contact and disarms. MCP caller is released at the verdict (NF14), not at dwell end. New Rule 4c scopes `lease_expired` to the armed request so a stray event can never deny a queued one. → D8b, spec/02 |
| **I1** | yes | "Unmatched means escalate" was widening a Warden `block`. Restated: an unmatched row defaults the **policy action** to `escalate`, then the resolution table applies normally. Warden `block` + no row still resolves `block`. → spec/05 |
| **I2** | yes | The wire had no way to observe button release, making D5's requirement unimplementable. Added `btns` (3-bit held field) to `tick`, plus **device-side held-button suppression** on `arm` — dead time alone did not close the hold-through case. → spec/01, spec/04 |
| **I3** | yes | D12's inference was invalid: a missing interlock branch stops the *contact*, not the *verdict*, so an auto-approved lamp would return `APPROVED` and stay dark. `relay_gated` is now a **column** on `policies` and the **resolver** forces `auto_approve → escalate`. → D12, spec/05 |
| **I4** | yes | `/decide` **removed entirely.** v1.1 only stopped it minting `human`; a co-resident agent holding the MCP token could still post `decided_by=policy`. Every verdict is in-process, so the endpoint had no legitimate caller. Tokens are now scoped `agent` / `ui`. Blanket `Origin` rejection replaced with per-scope handling — it would have broken the browser dashboard. → D11, spec/03 |
| **I5** | yes | §7.1 rewritten as a **consent-channel** flow with no relay command; the relay cycle moved to a separate §7.1b for `relay_gated` actions. The v1.1 diagram trained implementers to close a relay on `DROP TABLE` |
| **I6** | yes | Consistency sweep: spec/04 no longer says "never drives the relay"; spec/01 boot no longer says re-arm; invariant 2 in spec/00 and AGENTS.md rewritten from "resolve to denied" to fail-closed-with-the-right-verdict |
| **I7** | yes, with a framing note | See below |
| **M1** | yes | spec/02's "no clock-dependent branching" would have forbidden the lease heartbeat. Reworded: no *judgment*, timers allowed, all fixed intervals failing toward open |
| **M2** | yes | Noted explicitly that the wire string is `APPROVED: <reason>` and the diagram's dash is a Mermaid parsing workaround |
| **M3** | yes | Z2 is "one host and one operator account", not "one process boundary" |
| **M4** | yes | Audit event enum extended (`relay_closed`, `relay_opened`, `lease_expired`) and the exact event sequence for each path pinned in spec/05 |
| **M5** | yes | G7 scoped to "verified against the Python SDK only". Q4 vs R10 resolved: the lease is **fixed at 10 s**, the dwell is per-policy |

## One framing note, on I7

Accepted and fixed, but the finding slightly overstates the change. It frames the
dashboard cross-reference as making T1 "depend on the in-band UI §2 rejected."

Two things worth separating:

- It is **not a new trust dependency.** The LCD was already Z2-rendered; N-T2 has
  always said the host can lie about what you are approving. Moving the
  authoritative text to the dashboard adds no zone.
- It **is a new attack surface** inside that zone, which is the real cost. A
  browser and a template are more attackable than a 32-character serial write.

And §2 rejected in-band *approval* — the press is still physical. What moved
in-band is the *description*, which was never out-of-band.

The fix reflects that split: the short code is generated per-arm with a nonce, so
a stale dashboard row cannot match a live request. That closes the stale-row case
the finding names. XSS remains bounded only by N-T2, and is now stated.

## Disagreements

None on substance. The I7 note above is a scoping refinement, not a rejection.

## Still open after v1.2

- **F13** — a cut-short dwell is not surfaced to the actor. D10's one-tool rule
  gives it no channel; leaning accept and let the audit trail carry it (Q5).
- **R10 / F2** — the 10 s lease window remains the worst unsupervised interval.
  Now fixed rather than tunable, so every deployment shares one envelope.
- **R9** — audit anchoring designed, not built.
- **F10** — welded relay, still real and unmitigated.
