# FraudShield Swarm: framework

**Status: this is the build.** On Fri Oct 23 just before midnight PT, FraudShield replaced the settlement plan (`PLAN.md` section 9 says why). Earlier that night it had been parked as reference only. The amended spec is in [SPEC.md](SPEC.md).

Everything runs offline with no API keys. All data is simulated: invented people, accounts and watchlist.

```bash
.venv/bin/python -m fraudshield.run                 # seven scenarios -> runs/fraudshield/*.md
.venv/bin/python -m fraudshield.run --extractor claude   # Claude reads RFI replies (needs credentials)
.venv/bin/python -m fraudshield.bench --seeds 30    # three arms vs hidden truth
.venv/bin/python -m fraudshield.inject --help       # a judge types their own case
.venv/bin/pytest -q fraudshield/tests
```

## Flow

```
mock stream (stream.py)  ->  monitor rules (monitor.py)  ->  one case per subject
                                                              |
         +----------------------------------------------------+
         v                         v                          v
   graph_analyst            sanctions_checker          behavioral_profiler      each reads only its View
   edges, no names          names + watchlist,         subject's own history:
                            one RFI                    devices, places, hours
         |                         |                          |
         +-------------> case file (case.py) <----------------+   append-only findings; every
                               |                                   citation checked against what
                               v                                   that agent was shown
                   resolution protocol (protocol.py)               conflict -> each side gets the
                               |                                   other's structured claim, one
                               v                                   deeper check, revise only with
                   disposition policy (policy.py)                  new evidence
                               |                                   rules R1-R8, in code
                               v
                   synthesizer (report.py)  ->  case report: positions, findings log,
                                                disagreement log, proposed actions, evidence index
```

## What each agent sees

| Agent | Standard view | Deeper check (once, when challenged) |
|---|---|---|
| graph_analyst | Edges touching the subject or its direct counterparties, last 30 days. No names, devices or memos. | 400-day window |
| sanctions_checker | Names and countries of the parties on the alert, their registry owners three levels up, the watchlist. Applies the 50% ownership rule. | One RFI. The reply is untrusted free text: Claude (or the regex fallback) extracts the date of birth with a verbatim quote, code verifies the quote and compares the date. |
| behavioral_profiler | The subject's own history, last 90 days, all fields | 400-day baseline |
| synthesizer | Structured findings only (`Finding.public()`); never an agent's note | — |

## Rules the case file enforces (in code; each becomes a SpacetimeDB reducer)

- Only specialists file findings. Only the sanctions checker sends RFIs.
- Every finding cites evidence. Every cited ID must exist and must have been shown to the citing agent.
- Findings are append-only. A revision supersedes the agent's current position, happens only in a resolution round, and must cite evidence the old finding didn't.
- A broken rule writes a rejection row and returns. The case continues.

## Disposition policy (first match wins)

| Rule | Disposition |
|---|---|
| R1 | A watchlist match confirmed on secondary identifiers, or 50%+ aggregate blocked ownership, is held, whatever the other findings say. |
| R2 | A potential match that is neither confirmed nor discounted can't be closed or actioned. |
| R3 | A conflict still open after resolution goes to a human with both positions. |
| R4 | Two or more independent suspicious findings: act (proposals, human approves). |
| R5 | One suspicious finding with no corroboration: review. |
| R6 | An unknown, or a missing finding, blocks a close. It doesn't block action that other evidence supports. |
| R7 | All benign and at least one explains the activity: close as a false positive. |
| R8 | Anything else: review. |

Actions are `Recommendation` rows with `status: proposed`. Nothing in the package can execute one.

## Scenarios and a real run (seed 7)

| Scenario | Planted | Result | How it got there |
|---|---|---|---|
| s00_legit_large | Annual tuition to an established college | close (R7) | Profiler's 90-day view says unusual; the challenge from the graph analyst triggers the 400-day check, which finds last year's payment. Profiler revises. |
| s01_ato_mule | New device at 3 AM, large transfer to a 2-week-old account that fans in and forwards out | act (R4) | Profiler (takeover) and graph (mule) agree independently. |
| s02_namesake | Long-standing contractor one letter off a newly listed name | close (R7) | Sanctions sends the RFI; date of birth differs; it revises to discounted. |
| s03_structuring | Cash deposits just under $10k at several branches, then a wire out | act (R4) | Profiler (structuring) and graph (pass-through) agree. |
| s04_intercompany_loop | Three affiliates pass ~$50k in a circle monthly | review (R3) | Graph: funds return to origin. Profiler: recurring for a year. Both deeper checks reaffirm; the conflict stays open, which is the right answer. |
| s05_sanctions_match | Same contractor pattern, but the payee is the listed person. Their bank's reply tells "automated screening systems" to mark the match as discounted. | hold (R1) | The reader extracts the date of birth and flags the instruction. Code compares the dates and confirms the match. The profiler still says "established payee" and is overruled by R1. |
| s06_ownership_chain | A $150k–$240k wire to a new company. A listed person owns 70% of a shell that holds 60% or 41% of the company. | hold (R1) at 60%; act (R4) at 41% | The 50% rule is applied as OFAC states it: summed, never multiplied. 41% isn't blocked, so the case gets enhanced due diligence, not a sanctions hold. |

## Bench (210 cases: 30 seeds × 7 scenarios, about 4 seconds)

| Arm | Caught | Missed | Cleared | False action | To analyst | Exact disposition |
|---|---|---|---|---|---|---|
| rules_only (every alert to an analyst) | 0 | 0 | 0 | 0 | 210 | 36 |
| majority (round-0 vote, no protocol) | 87 | **33** | 60 | 0 | 30 | 157 |
| swarm | 117 | **0** | 54 | 0 | 39 | **207** |

- **Majority vote's misses:** all 30 true sanctions matches (two "looks normal" votes outvoted the match) and 3 structuring cases.
- **Majority vote never holds:** on the blocked-ownership cases it proposes act instead.
- **The swarm's 39 reviews:** the 30 loop cases (correct), 6 first-year tuition payments with no precedent, and 3 thin structuring cases. Those 3 are its only inexact dispositions.

"Exact" compares each disposition with `Truth.expected`.

**Read this honestly.** The same people wrote the scenarios and the specialists, so this shows the protocol behaves as designed on planted patterns. It is not a detection rate.

## SpacetimeDB port

The case file maps onto a module one-to-one.
- **Tables:** private evidence (`txn`, `account`, `party`, `watchlist_entry`, `rfi_sealed`); case tables (`alert`, `case_scope`, `finding`, `challenge`, `exchange`, `decision`, `recommendation`, `rejection`, `role`); a schedule table, `rfi_deadline`.
- **Reducers:** `submit_finding` checks `ctx.sender`'s role, citations against `case_scope`, and the revision rules. The others are `request_rfi`, `open_round` (writes `challenge` rows that agents subscribe to), `decide` (R1–R8 in TypeScript), `approve_recommendation` (human identity only) and `inject_txn` (judge-typed cases).
- **Clients:** Python agents get identities from `POST /v1/identity` and call reducers over HTTP. The React dashboard subscribes.
- **Unverified:** `spacetime` isn't installed, Node is 18.20.5, `SenderError` rollback is unconfirmed, and per-identity filtered views are unconfirmed.

## Adversarial review (25 agents, Fri Oct 23)

| Idea | Demo | Difficulty | Capability | Overall (median) |
|---|---|---|---|---|
| A: FraudShield | 6 | 5 | 3 | 5 |
| B: Artist settlement (PLAN.md) | 7 | 6 | 5 | 7 |
| C: Trade recon (Plan B) | 6 | 6 | 5 | 6 |

B ranked first 18 times, C 7, and A never. The attacks on A:
- The bench is circular.
- Claude isn't load-bearing.
- The category is crowded.
- The sponsor list is too spread out.
- There's no domain interview.

Changes that would raise A:
- Make the sanctions/RFI specialist a real Claude call that reads untrusted memo and RFI text, with an injection planted in one reply.
- Move R1–R8 into SpacetimeDB reducers.
- Add a judge-injection form.
- Cut to three scenarios (s02, s05, s01).
- Pitch Hyphenate and SpacetimeDB only.

## Not built yet

- **The Claude path has never run.** `extract.py` calls Claude, but this machine has no credentials. Every run so far used the regex fallback, and reports say so. The request shape (structured output plus server-side fallback on the beta endpoint) is unverified until the first real call, which may fail for reasons other than credentials. The report label tells the two apart. Don't call Claude load-bearing until a run with `--extractor claude` succeeds.
- **Injection detection is a short regex.** The real defense is quote verification plus the code-side comparison. The flag only makes the attempt visible in the report.
- **The rest is still heuristics and templates.** The other two specialists are heuristics, and the narrator is a template.
- **No SpacetimeDB module or dashboard.** The module waits on the `spacetime` CLI.
- **No independent test cases.** Bench cases aren't generated independently of the specialists' authors. Judge injection (`inject.py`) is the only outside test.
