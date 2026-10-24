# FraudShield Swarm: specification v1.1 (amended)

Supersedes v1.0, which was pasted into this repo's planning session on Fri Oct 23, 2026. v1.1 describes what this repo builds and claims nothing it can't show. It's a hackathon entry: public, with simulated data only, and no classification marking.

## What changed from v1.0, and why

| # | v1.0 said | v1.1 says | Why |
|---|---|---|---|
| 1 | Hyphenate is the orchestration platform that runs the swarm | Hyphenate is the prize this entry targets. Orchestration is our own code: the case file, the resolution protocol and the policy. | Hyphenate is a sponsor whose product is finance agents for ERP work. It publishes no API or SDK (`CONCEPT.md`). Its judges would read their own company misdescribed. |
| 2 | "CRS" is a Customer Risk Scoring engine | No customer risk score is integrated. The sponsor CRS (a credit-data provider) is not targeted. | The name collides with a sponsor. `TRACKS.md` says the CRS prize needs credit data at the center, live against its sandbox, and nothing here does that. |
| 3 | Armadin sandboxes agents and red-teams them; Elastic stores everything | Both are named only as integration points: an evidence-store interface and an audit-log sink. Neither is built. | Both sponsors were "coming soon" with no published criteria (`TRACKS.md`). |
| 4 | "Deterministic recommendation generation", while an LLM synthesizer resolves conflicts by "weighted consensus" | A fixed policy in code (rules R1–R8) decides every disposition. The synthesizer only writes the report, from structured findings. | The two v1.0 claims contradict each other. If a model weighs the consensus, the decision isn't deterministic. |
| 5 | Sample case: indirect exposure rises from 28% to 41%; behavior plus graph override sanctions; file a SAR | The 50% rule is applied as OFAC states it, and the sample case is fixed (below). | An entity is blocked when blocked persons own 50% or more of it in aggregate. A blocked owner counts in full, and stakes are never multiplied down a chain. 41% doesn't block. v1.0 also overrode its own rule 3 without saying why. |
| 6 | 99.9% availability, 1,000+ concurrent alerts, under 5 minutes per alert | Replaced by what the demo shows: 210 simulated cases in about 4 seconds offline, every case traced to evidence. | A 34-hour build can't demonstrate production targets, and claiming them invites "how do you know?" |
| 7 | "Confidential — Internal Use Only" | Removed | The document lives in a public repo and goes on Devpost. |

## Scope

A transaction-monitoring alert comes in. Three specialists investigate it and the case is worked through a fixed protocol. The output is a disposition, proposed actions and an auditable report. Out of scope: moving money, freezing accounts, and filing anything. Every action is a proposal for a human analyst.

## Architecture

```
mock stream -> monitor rules -> case (one per subject)
   -> specialists, each on a scoped view -> append-only case file (citations checked)
   -> resolution protocol (conflicts and open items, one deeper check each, max 2 rounds)
   -> policy R1-R8 (code) -> proposed actions -> report (structured findings only)
```

| Specialist | Sees | Deeper check | Model use |
|---|---|---|---|
| Graph analyst | Edges near the subject: amounts, times, accounts. No names. | 400-day window | None (heuristics) |
| Sanctions checker | Names and countries on the alert, the registry chain three levels up, the watchlist | One RFI to the payee's bank | Claude is wired to read the free-text RFI reply; code verifies the reading and decides. Not yet exercised: every run so far used the regex fallback, and the call may still fail on its first real run for reasons other than credentials. |
| Behavioral profiler | The subject account's own history | 400-day baseline | None (heuristics) |
| Synthesizer | Structured findings only | — | Template today |

**How a model may touch a decision.** A model may only extract fields from untrusted text, and it must return the verbatim passage it used. Code checks that the passage appears in the reply and contains the date. Code then compares the result with the watchlist entry. A reading that fails a check can neither confirm nor discount a match, so the case goes to a human (R2). The defense against a reply that tries to instruct the system is that verification and the comparison happen in code. A small regex also flags instruction-like phrases so the report can show them. It catches only the phrases it lists, and nothing depends on it.

## Disposition policy

| Rule | Disposition |
|---|---|
| R1 | Confirmed watchlist match, or 50%+ aggregate blocked ownership: hold, whatever the other findings say |
| R2 | A potential match that is neither confirmed nor discounted: review |
| R3 | A conflict still open after resolution: review, with both positions attached |
| R4 | Two or more independent suspicious findings: act (proposals; a human approves) |
| R5 | One suspicious finding: review |
| R6 | An unknown blocks a close; it doesn't block action that other evidence supports |
| R7 | All benign, at least one explaining the activity: close |
| R8 | Otherwise: review |

## Sample case, corrected (scenario `s06_ownership_chain`)

A corporate customer wires $150k–$240k to Corvane Trading Ltd (CY), a newly onboarded payee. The registry shows Halvane Holdings Ltd (AE) holding a stake in Corvane. A listed person, whose date of birth in the registry matches the watchlist, holds 70% of Halvane.
- **Halvane is blocked** (70% ≥ 50%). Its stake in Corvane therefore counts in full.
- **Stake 60%:** Corvane is blocked → R1 hold, plus a draft sanctions report.
- **Stake 41%:** Corvane is not blocked. That 41% is exposure, not a match. Together with the profiler's finding (a new payee, 7–15x the largest payment in 90 days), R4 proposes enhanced due diligence on Corvane's owners and confirmation with the customer. No sanctions hold.

## Data model

Records carry typed IDs: `txn:`, `acct:`, `party:`, `wl:`, `own:` and `rfi:`. Findings cite these IDs. The case file rejects any citation that doesn't exist or that the citing agent was never shown. Evidence records:
- **Transaction:** time, accounts, amount, channel, device, location
- **Party:** name, country, kind, date of birth when KYC or a registry holds one
- **Ownership:** owner, owned, percentage, source
- **RFI reply:** untrusted free text
- **Watchlist entry:** fictional names and programs

## How we know (from a real run)

`python -m fraudshield.bench --seeds 30` scores 210 simulated cases against hidden labels. The swarm missed no suspicious case, took no action on a legitimate one, and matched the expected disposition in 207 of 210 cases. Majority vote missed all 30 true sanctions matches.

The same people wrote the scenarios and the specialists. This shows the protocol behaves as designed on planted patterns. It is not a detection rate. Judge-typed cases (`python -m fraudshield.inject`) are the only test outside our own scenarios.

## Integration points (not built)

- **Elastic:** an evidence-store and audit-log sink behind the case file
- **SpacetimeDB:** the case file as a module, with rules as reducers and agents as identities. Waiting on the CLI install.
- **Armadin:** agent sandboxing, if its criteria fit
- **A customer risk score:** an input to the profiler
