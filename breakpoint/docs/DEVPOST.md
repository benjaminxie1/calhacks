# Breakpoint — Devpost write-up (draft)

Paste each section into the matching Devpost field. Numbers are from `make demo` on seed 42; re-run before submitting
and update them if anything changed (especially after the live-model run).

**Tagline (≤ 200 chars):** An autonomous reconciliation team for a retail brokerage: it finds breaks across five systems,
fixes the ones it has learned, and asks a human only when something is new.

**Track:** The Hyphenated Enterprise: Autonomous Systems for Finance (Hyphenate).

**Seats it fills:** trade reconciliation analyst and settlements / cash ops analyst at a retail brokerage.

---

## Inspiration

Every broker-dealer runs a reconciliation desk. Each morning, analysts compare the order system, the executing broker's
fills, the booking ledger, the custodian's statement and the bank. Where they disagree there is a "break", and someone has
to work out which system is right, fix the books and tell the client. Most breaks are the same dozen patterns, and the
fix lives in an SOP and in the heads of a few senior people. When a new pattern turns up, such as a stock split the
booking job missed, it waits in a queue until one of them looks at it.

We wanted a team of agents that does the routine work end to end, under real controls, and that *learns* the new
pattern from one human explanation instead of a retraining run.

## What it does

Breakpoint runs a simulated quarter (Jul 1 to Oct 23, 2026) at Meridian Securities, a mock retail broker with 200
accounts trading 40 real SEC EDGAR symbols. That's about 22,000 orders across eight source systems, with 33 breaks
planted against an answer key.

- **Known breaks resolve themselves.** Partial fills booked as full, commissions charged at the old fee schedule,
  duplicate execution reports and missed dividends are each matched to a playbook branch. Each fix is prepared by
  deterministic calculators, approved under a coded policy and posted by the only component that can write to the
  ledger. Every resolution cites the playbook, the branch and the person who taught it.
- **New breaks go to a human, once.** On Aug 13 a 2:1 NFLX split hits and no playbook fits, so the agent stops and asks
  the ops lead in Teach mode. She picks the trusted source, the action and the fact that makes this case different. The
  learner turns that into a readable branch: `split_on_symbol and custodian_qty == ledger_qty * split_ratio`. The
  controller approves it.
- **The lesson sticks.** On Oct 15 a COST split hits ten holders. It is fixed automatically, citing the August lesson.
- **Money problems get chased.** When an ACH deposit is returned after the client already bought shares, outreach asks
  the client for funds, follows up after one business day and escalates to the controller after three.
- **Judges can try to break it.** The Inject page plants a break of your choice (partial fill, stale fee, duplicate
  report or price rounding) on any account and symbol. It lands on the next replay day and gets handled in front of you.
- **Ask why.** The auditor answers "why was this resolved this way?" from the event log, citing event numbers.

## Proof

| Metric (seed 42) | Result |
|---|---|
| Detection recall / precision vs the answer key | 100% / 100% (no unplanted breaks) |
| Planted breaks in their expected final state, with answer-key amounts | 33 / 33 |
| Ledger after replay equals the correct books built from the true world | yes, every client and firm balance |
| Invariants: trial balance zero daily, entries balanced, no closed-period postings, unique idempotency keys, no floats | all green |
| Cases closed by an active playbook with no human | 20 of 23 (2 taught, 1 escalated to the controller by design) |
| Judge-style injections rehearsed across the quarter | 38; every one landing inside the replay was detected and resolved with exact amounts |
| Tests | 52 (T1–T37 from our build plan, plus model-fallback tests) |

### Real vs simulated

| Part | Status |
|---|---|
| Symbols and CIKs | Real, from SEC EDGAR |
| Prices | Simulated: seeded random walk (labeled) |
| Ledger engine | Real double-entry ledger on Postgres (append-only, per-asset balance trigger, period lock); simulated entries |
| Bank | In-app mock bank with real ACH return codes |
| Orders, executions, custodian | Simulated by a seeded generator |
| People | Personas that answer from the true world (played by Claude with a key, templated without) |
| Breaks | Planted, with an answer key |

## How we built it

- **Python 3.12, FastAPI, Pydantic, SQLAlchemy, Postgres 16.** `Decimal` everywhere; a test fails the build if a float
  touches money.
- **Claude (`claude-opus-5-5`) through one thin wrapper** (`bp/agents/llm.py`, `messages.parse` with Pydantic schemas).
  Each agent asks for a typed decision. If the call is refused, malformed or unavailable, it falls back to a deterministic
  policy and logs that it did.
- **Agents with separated duties:** matcher (code), investigator, preparer, approver, outreach, learner and auditor (LLM), plus
  executor (code). Each agent has its own tool scope. The preparer can never approve, and the approver can never post.
  Only the executor holds ledger and bank credentials, and it refuses any proposal whose SHA-256 changed after approval.
- **The model never sets an amount.** Every number comes from a calculator, and a proposal that doesn't reproduce exactly is
  rejected.
- **Playbooks, not weights.** Induction drafts the starting library from 30 resolved Q2 tickets and the SOP. The model
  proposes branch conditions in a small, safe condition language. Code accepts a condition only if it matches every case
  it was learned from and none from another branch. Learning adds a branch a controller approves, so every learned
  behavior can be read and revoked.
- **React + Vite + Tailwind + Recharts dashboard:** Overview, Breaks, Case detail (source compare, agent timeline,
  proposals with postings and approval hashes, messages, ask-why), Approvals, Teach mode, Playbooks, Proof,
  Inject break and Audit.

## Challenges we ran into

- **Phantom breaks.** A posted correction and the break it fixes must cancel in the same recon pass. Otherwise end-of-day
  cash recon reports a new break for the fix itself. The matcher subtracts the deltas of open breaks and of
  corrections in flight. The answer key's zero-false-positive check is what caught it.
- **Getting the split right.** A split changes quantity and cost per share but not cash, for every holder at once, and
  the October split has to be fixed by a branch learned in August from a different symbol.
- **Making "learning" auditable.** We kept the model in the loop for judgment and explanation and kept the
  learned artifact as code-checkable conditions.

## Accomplishments that we're proud of

Every planted break ends in its expected state with the answer key's exact amounts. The books match the true world to
the cent, and the novel-break-to-automatic loop works live in the UI.

## What we learned

The controls (segregation of duties, approval hashes, calculator-only amounts, append-only ledger) made the agents
*easier* to build, because a wrong model answer can at worst send a case to a human.

## What's next for Breakpoint

Two more break types (a trade booked to the wrong account; a broker correction not reflected), a second domain loaded
by config only (payments vs bank), real prices, and a real sandbox bank.

## Built with

python, fastapi, pydantic, sqlalchemy, postgresql, claude, anthropic, react, vite, tailwindcss, recharts, docker, sec-edgar
