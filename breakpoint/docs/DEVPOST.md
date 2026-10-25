# Breakpoint — Devpost write-up (draft)

Paste each section into the matching Devpost field. Numbers are from `make demo` on seed 42; re-run before submitting
and update them if anything changed (especially after the live-model run).

**Tagline (≤ 200 chars):** An autonomous CAM audit team: it reads every landlord's year-end bill against the lease,
credits the overcharges it has learned, and asks a human only when something is new.

**Track:** The Hyphenated Enterprise: Autonomous Systems for Finance (Hyphenate).

**Seats it fills:** lease administration analyst, and the accounts payable reviewer who checks landlord statements
before they're paid.

---

## Inspiration

Every company that leases retail space pays its landlords common-area-maintenance (CAM) charges: a monthly estimate,
then a year-end reconciliation statement that bills the difference. Those statements are long, built by the landlord,
and easy to get wrong in the landlord's favor: the tenant's share computed on occupied area instead of the whole
center, a management fee charged on taxes and insurance, a capped expense left uncapped, a new roof billed as a
repair. The lease says what's allowed, but checking 40 statements against 40 leases line by line is work nobody
in-house has time for, so most get paid as billed.

We wanted a team of agents that does that review end to end, under real finance controls, and that *learns* a new kind
of overcharge from one human explanation instead of a retraining run.

## What it does

Breakpoint replays the 2026 statement season (Feb 2 to May 29) for Golden Wok Restaurant Group, a mock chain with 40
stores and 12 landlords. Each landlord's statement arrives on its own day; AP books it as billed; Breakpoint reviews
it against the lease abstract and lease text, the county's tax bill for the parcel, and the estimate payments the bank
cleared. 33 errors are planted across 31 statements, with an answer key.

- **Known overcharges resolve themselves.** Share on occupied area, management fee over the lease limit, the
  controllable cap left off, capital items passed through, taxes above the county bill, and missed estimate payments
  are each matched to a playbook branch. Each credit is computed by deterministic lease-math calculators, approved
  under a coded policy and posted by the only component that can write to the ledger. Every resolution cites the
  playbook, the branch and the person who taught it, and the landlord gets a dispute notice citing the lease section.
- **One statement, five problems.** Mission Plaza bills our Hayward store $36,965.60. The lease allows $24,127.50.
  Breakpoint finds all five problems and attributes every dollar of the $12,838.10 difference to exactly one of them.
- **New overcharges go to a human, once.** On Mar 10 a landlord grosses up variable expenses to 95% occupancy, which
  our lease doesn't allow. No playbook fits, so the agent stops and asks the lease admin lead in Teach mode. She picks
  the trusted source, the actions and the fact that makes this case different. The learner turns that into a readable
  branch, `gross_up_not_in_lease`, and the controller approves it.
- **The lesson sticks.** On May 5 another landlord grosses up three of our stores. All three are credited in one case
  automatically, citing the March lesson.
- **Landlords get chased.** When taxes are billed above the county's figure, outreach asks the landlord for the tax
  bills and proof of payment, follows up, and escalates to the controller when the landlord goes silent.
- **Judges can try to break it.** The Inject page plants an overcharge of your choice in any statement that hasn't
  arrived yet. It lands on the next replay day and gets handled in front of you.
- **Ask why.** The auditor answers "why was this resolved this way?" from the event log, citing event numbers.

## Proof

| Metric (seed 42) | Result |
|---|---|
| Detection recall / precision vs the answer key | 100% / 100% (no unplanted findings) |
| Planted errors in their expected final state, with answer-key amounts | 33 / 33 |
| Overcharges found and credited | $54,317.97 |
| Ledger after replay equals the correct books built from the true world | yes, every lease and cash balance |
| Invariants: trial balance zero daily, entries balanced, no closed-period postings, unique idempotency keys, no floats | all green |
| Cases closed by an active playbook with no human | 25 of 27 (1 taught, 1 escalated to the controller by design) |
| Judge-style injections rehearsed across the season | 48 of 48 detected and resolved with exact amounts |
| Tests | 52 (T1–T37 from our build plan, plus model-fallback tests) |

### Real vs simulated

| Part | Status |
|---|---|
| Lease math | Real CAM clauses: pro-rata share on GLA, controllable cap, management fee base, capital exclusion, gross-up |
| Leases and statements | Simulated by a seeded generator: 40 stores, 12 landlords, lease text per store |
| County tax records | Simulated per parcel, standing in for the county assessor's lookup |
| Ledger engine | Real double-entry ledger on Postgres (append-only, balance trigger, period lock); simulated entries |
| AP payments and bank | Simulated estimate payments with bank clearing status |
| People | Personas that answer from the true world (played by Claude with a key, templated without) |
| Overcharges | Planted, with an answer key |

## How we built it

- **Python 3.12, FastAPI, Pydantic, SQLAlchemy, Postgres 16.** `Decimal` everywhere; a test fails the build if a float
  touches money.
- **Lease math as code.** `bp/cam.py` recomputes each statement line from the lease: share on GLA rounded per line,
  the cap over last year's controllables, the fee on its allowed base, exclusions, and gross-up only where the lease
  allows it. Each overcharged dollar is attributed to exactly one finding, so findings sum to billed minus correct to
  the cent.
- **Claude (`claude-opus-5-5`) through one thin wrapper** (`bp/agents/llm.py`, `messages.parse` with Pydantic schemas).
  Each agent asks for a typed decision. If the call is refused, malformed or unavailable, it falls back to a deterministic
  policy and logs that it did.
- **Agents with separated duties:** matcher (code), investigator, preparer, approver, outreach, learner and auditor
  (LLM), plus executor (code). Each agent has its own tool scope. The preparer can never approve, and the approver can
  never post. Only the executor holds ledger credentials, and it refuses any proposal whose SHA-256 changed after
  approval. Credits over $2,500 need the controller.
- **The model never sets an amount.** Every number comes from a calculator, and a proposal that doesn't reproduce exactly
  is rejected.
- **Playbooks, not weights.** Induction drafts the starting library from 30 disputes closed last season and the SOP. The
  model proposes branch conditions in a small, safe condition language. Code accepts a condition only if it matches
  every case it was learned from and none from another branch. Learning adds a branch a controller approves, so every
  learned behavior can be read and revoked.
- **React + Vite + Tailwind + Recharts dashboard:** Overview, Findings, Case detail (landlord billed vs lease allows,
  agent timeline, proposals with postings and approval hashes, landlord messages, ask-why), Approvals, Teach mode,
  Playbooks, Proof, Inject overcharge and Audit.

## Challenges we ran into

- **Attributing every dollar once.** One statement can be wrong five ways at once, and the errors interact: a wrong
  share inflates every line, including the capital item and the fee. We price the share finding on the lines as
  billed and every other finding at the lease share, so the five credits add up to exactly the overcharge with nothing
  counted twice. The planter asserts that identity for every statement it touches.
- **The management fee base.** The allowed fee depends on the cap, the county tax figure and the exclusions, so it has
  to be computed after them, from the corrected lines, not from what the landlord printed.
- **Making "learning" auditable.** We kept the model in the loop for judgment and explanation and kept the learned
  artifact as code-checkable conditions.

## Accomplishments that we're proud of

Every planted error ends in its expected state with the answer key's exact amounts. The books match the true world to
the cent, and the novel-overcharge-to-automatic loop works live in the UI.

## What we learned

The controls (segregation of duties, approval hashes, calculator-only amounts, append-only ledger) made the agents
*easier* to build, because a wrong model answer can at worst send a case to a human.

## What's next for Breakpoint

Reading real lease PDFs into the abstract (with a human confirming each clause), real county assessor lookups, and
more clause types: base-year leases, expense stops and anchor-tenant exclusions.

## Built with

python, fastapi, pydantic, sqlalchemy, postgresql, claude, anthropic, react, vite, tailwindcss, recharts, docker
