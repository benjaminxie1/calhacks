# Cal Hacks 13.0 build plan — "Paid Means Proven" (working title)

GridTwin-derived. Targets the Tier 1 stack: Hyphenate (primary), Microsoft, Factory, Anthropic.
Written Fri Oct 23, ~8 PM. Submission hard deadline: **Sun Oct 25, 9:00 AM** (Devpost). Be at the venue by 8 AM.
Format: 3-minute pitch + 2-minute Q&A, pre-recorded or live demo, project image + GitHub repo required.

## 0. The rule that shapes everything

Devpost: **"All work must be done during the hackathon."**
So this is a from-scratch rewrite in ~36 hours with GridTwin as the design spec, not a fork.
New repo, no copied files. GridTwin is cited as prior art in the README (same pattern as GridTwin's own "Built during the hackathon" section).
Confirm the official hacking start time on the Hive site before the first commit; judges can read commit timestamps.
Teammates need to hear this tonight.

## 1. The seat (recommendation; pick one)

**Primary: procure-to-pay for O&M repair invoices.**
A plant owner's AP analyst does a three-way match: work order ↔ invoice ↔ receipt of service.
Today the "receipt" is the contractor's completion note and a photo. Here it is an **independently verified fix**: fresh telemetry, a camera viewpoint the contractor did not choose, a 30-minute hold, re-verification.

How it reads against Hyphenate's four questions:
- *Real enough:* work orders carry minutes, dollars and a de-energize flag (GridTwin's rate card: fan/filter $1,800, comms card $900, module $2,200, transformer oil $6,000). Invoices are synthetic against that rate card. Real SPP LMPs price every unverified hour of lost output.
- *Would you hire it, for which seats:* "AP analyst for repair invoices and the field-verification sign-off. Approves under a threshold only after verified completion, holds with a stated release condition, never releases payment. The verifier never reads contractor or matcher prose."
- *Something new:* the receipt in a three-way match is a machine-verified physical outcome, not a signature.
- *The moment:* an invoice for "replace fan/filter, INV-1, $1,800" lands with a completion photo and "temps normal." Work order exists, rate matches, photo looks fine. The verifier re-runs the checks: fan_rpm 0, heatsink climbing again 12 minutes after the hold. **Hold the invoice**, state what releases it (fan_rpm > 0 and heatsink within model for 30 min from viewpoint INV-1/rear), request a return visit. F3 (fan relapse) and F6 (stale SCADA: telemetry frozen at perfect values, only the timestamp is stale) were built as exactly these traps.

**Alternative: revenue assurance in the close.** Attribute lost output at real LMPs to verified incidents; accrue liquidated damages. More real data, weaker moment, more new domain code. Recommend AP primary with the LMP cost line as a one-afternoon enrichment ("this unverified repair has cost $X so far").

## 2. What gets built (one thing done well)

```
invoice lands (file in ap_inbox/)  ──trigger──►  Droid matcher (Factory SDK)
                                                   │ tools: get_work_order, get_rate_card,
                                                   │        get_verification, request_reverify
                                                   ▼
                        decision: APPROVE | HOLD | DISPUTE | ESCALATE  (+ release condition)
                                                   │
          ┌────────────────────────────────────────┤
          ▼                                        ▼
  Verifier (Claude)                       Finance view (plain HTML/JS)
  runs the DoD template for the           invoice · work order · evidence · Droid steps
  work-order type against the sim:        and diffs · decision · audit trail
  telemetry fresh/threshold, visual
  from an unused viewpoint, hold 30 min,
  re-verify. Never reads prose.
```

- **Plant sim** (Python, deterministic, hidden truth read only by the judge): 5 MW solar, 4 inverters, cooling, SCADA with `last_update_ts`. Faults: control (nothing wrong), F3 fan relapse, F6 stale SCADA. Work orders with the rate card. Camera = photo bank keyed to hidden state.
- **Definition-of-done templates per work-order type** (no planner, no worker loop, no splat, no recursion). Claude fills the template from the work order and a cited OEM/standards reference; the verifier executes it.
- **Ledger as a git repo**: `work_orders/`, `ap_inbox/`, `evidence/`, `decisions/`, `journal.csv`. Droid's output is a diff in `decisions/` and `journal.csv`; a human merges = approves. Payment is never a tool.
- **Judge-injectable anomalies** (CLI switch from day one; Hyphenate says they may try their own scenario): duplicate invoice, rate-card mismatch, invoice for an asset with no work order, amount above threshold, invoice dated before the fix completed, correct fix (must approve fast, to show it is not a naysayer).
- **Bench**: arms `trusting` (approves on contractor note), `self_verify`, `full` (independent verifier + hold). Metrics: false-approval rate, dollars correctly held, cycle time on clean invoices, escalation precision. This is the Microsoft "measurable usefulness" and the Hyphenate "how you know."

## 3. Per-track checklist

| Track | What they score | What we ship |
|---|---|---|
| Hyphenate | real enough; would you hire it; something new; the moment | Demo video **opens** with the hold. Live demo with the anomaly injector ready. One line on seats. Real-vs-simulated table (section 4). |
| Factory | Droid does real work on a trigger; control over what it touches; visible plans/steps/diffs | Python `droid-sdk`: `Session(cwd=ledger_repo)`, `restrict_tools` allowlist, `autonomy` LOW, `on_permission` denies anything outside `decisions/` and `journal.csv`, in-process MCP tools (`droid-sdk[mcp]`). Stream `ToolCall`/`ToolResult` as steps and `EditAction` as the diff (there is no plan event; visible work = tool sequence + diffs). README section "Where Droid runs." **If Droid is not doing the real matching by Sat noon, drop the Factory submission rather than fake it.** Plain-Python matcher is the fallback. |
| Anthropic | named community; lived problem; one thing usable Monday; Claude load-bearing, failure mode known | Community: asset-management analysts approving O&M invoices at small solar IPPs (or sharpen this). **To-do: one conversation this weekend with someone who has lived it, named in the pitch.** Claude = verifier and hold-reason writer. Failure mode: approving on thin evidence; mitigations: fail closed, threshold, human release, verifier blind to prose. |
| Microsoft | customer understanding; measurable usefulness; path to production; responsible design; end-to-end | Customer: IPP finance ops. Metrics from the bench. Production path: CMMS (work orders), AP inbox/ERP (invoices), SCADA historian (telemetry), segregation of duties, approval thresholds, audit log. Responsible: no auto-pay, vendor dispute path, every decision explains itself. |

## 4. Real vs simulated (goes in README and the video)

| Piece | Status | How we know it is honest |
|---|---|---|
| Plant telemetry, faults | Simulated, hidden truth read only by the judge | Bench scores against hidden truth |
| Contractor invoices | Synthetic, from the rate card, with realistic noise | Generator is in the repo |
| Photos | Third-party licensed (re-fetch during the event, LICENSES.csv) | Listed with source and license |
| Prices (LMP) | Real, SPP Integrated Marketplace public data, re-downloaded during the event | URL + fetch timestamp in the repo |
| OEM / standards references | Real, fetched and cited | URL cached with the check |
| Models and SDKs | Real: Claude (verifier), Factory Droid (matcher) | Named in README |

## 5. Timeline with gates

- **Tonight (Fri):** install Droid CLI, get a Factory key (booth) and an Anthropic key (presenting sponsor); smoke-test `droid_sdk.run()` and one Claude call. Repo skeleton, ledger layout, minimal sim (plant + control + F3 + F6), work orders, invoice generator. Confirm hacking start time.
- **Sat morning:** DoD templates, verifier with hold + re-verify, Droid matcher with MCP tools. **Gate, Sat noon: Droid does the real match or Factory is dropped.**
- **Sat afternoon:** finance view streaming Droid steps and diffs, bench (3 arms), anomaly injector, LMP cost line if time.
- **Sat evening:** demo video that opens with the moment; README (built-during section, Droid section, real/sim table); pitch at 3:00; Devpost draft. **Feature freeze Sat 8 PM.**
- **Sun 7:00–8:30 AM:** final run, submit before 9:00.

## 6. Blockers right now

- `droid` CLI missing, `droid-sdk` not installed, `FACTORY_API_KEY` unset.
- `ANTHROPIC_API_KEY` unset (anthropic SDK is installed).
- Node 18 is present (Droid TypeScript SDK minimum). Python 3.12 is present.
- Nobody interviewed yet for the Anthropic "lived problem" bar.
