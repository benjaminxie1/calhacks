# Breakpoint

**An autonomous CAM audit team for a multi-location restaurant chain.** Every spring each landlord mails an annual
common-area-maintenance (CAM) reconciliation statement. Breakpoint reads each one against the lease, the county tax
bill and the estimate payments we made, credits every overcharge it has learned to fix, and asks a human only when it
meets something new.

**Seats it fills:** lease administration analyst and the accounts payable reviewer who checks landlord statements
before they are paid.

Built for the Cal Hacks 13.0 Hyphenate track, "The Hyphenated Enterprise: Autonomous Systems for Finance".

## The moment

1. Replay the 2026 statement season (Feb 2 to May 29) in fast-forward for Golden Wok Restaurant Group (mock), 40 stores
   with 12 landlords. Known overcharges resolve themselves: share computed on occupied area instead of the center's
   GLA, a management fee charged on taxes and insurance, a controllable cap left off, a roof replacement passed through
   as a repair, taxes above the county's bill, an estimate payment never credited. Each fix cites the playbook, the
   branch and the person who taught it.
2. On Mar 3 the Mission Plaza statement for the Hayward store arrives billing **$36,965.60**. The lease allows
   **$24,127.50**. Breakpoint finds all five problems and credits the **$12,838.10** difference, each dollar traced to a
   lease clause.
3. On Mar 10 Bayshore Retail Partners grosses up variable expenses to 95% occupancy. The lease has no gross-up
   clause and no playbook fits, so the agent stops and asks the lease admin lead. She picks the trusted source (the
   lease), the actions (credit the gross-up, send a dispute notice) and the fact that makes this case different. The
   learner turns that into a branch anyone can read: `gross_up_not_in_lease`. The controller approves it.
4. On May 5 Coastline Property Group grosses up three of our stores the same way. All three are fixed in one case,
   automatically, citing the March lesson.
5. A judge plants an overcharge of their choice in a statement that hasn't arrived yet and watches it get handled.

## Proof (seed 42, `make demo`)

| Metric | Result |
|---|---|
| Detection recall / precision vs the answer key | 100% / 100% (0 unplanted findings) |
| Planted errors in their expected final state, with answer-key amounts | 33 / 33 |
| Overcharges found and credited | $54,317.97 across 31 statements |
| Ledger after replay equals the correct books built from the true world | yes, every lease and cash balance |
| Invariants: trial balance zero daily, entries balanced, no closed-period postings, unique idempotency keys, no floats | all green |
| Cases closed by an active playbook with no human | 25 of 27 (1 taught, 1 escalated to the controller by design) |
| Judge-style injections rehearsed (6 types x 4 dates x 2 stores) | 48 of 48 detected and resolved with answer-key amounts |
| Full 83-business-day replay with agents (offline policies) | ~3 s |

Sierra Vista Realty never sends the tax bills we ask for. That case is escalated to the controller after the landlord's
reply window, as specified; it is the expected outcome, not a miss.

## Run it

```sh
make setup        # Python 3.12 venv (uv)
make up           # Postgres in Docker on :5433
make world        # seeded world + answer key in data/world/seed42
make demo         # replay the season with scripted people and score it against the answer key
make test         # 52 tests (T1-T37 plus model-fallback tests; T35 generality skipped as the plan's first scope cut)
make api          # builds the dashboard and serves it with the API on http://localhost:8000
```

In the dashboard header, set the controller and lease admin lead to "you (UI)" to approve and teach by hand. Set them
to the scripted Sam Lee and Dana Ortiz to let the replay run unattended.

With `ANTHROPIC_API_KEY` set in `.env`, the agents decide with Claude (`claude-opus-5-5`, structured outputs). Without
a key, every agent runs a deterministic offline policy, so tests and the demo are reproducible either way.
`make test-llm` runs the LLM-dependent tests (T26, T27, T32) three times each against the live model, with
`BP_LLM_STRICT=1` so a silent fallback to the offline policy fails the test instead of passing it. If a model call is
refused, malformed, times out (`BP_LLM_TIMEOUT`, default 60 s) or has no credentials, the agent uses its offline policy and
the event records why. `BP_LLM_AGENTS=investigator,outreach,auditor` limits the model to those agents.

Live injection (Inject overcharge page) supports six types: share on occupied area, management fee at 15% of
everything, a capital item passed through, taxes above the county bill, a missed estimate payment, and a gross-up the
lease doesn't allow. The target is any store whose statement hasn't arrived yet; it lands on the next replay day. The
nine clean stores' statements arrive between Apr 13 and May 28, so there is always one to pick until the last day.

## Architecture

```
 sources (8, each behind its own API key)          ┌──────────── Playbook library ◄─────────────┐
 leases · landlord statements · AP · bank          │   (versioned, per-branch provenance+stats) │
 county tax records · centers · booking · people   ▼                                            │
        │                                     playbook fits? ──no──► Teach mode (human)          │
        ▼                                          │yes                    │                     │
  Matcher (code: lease math) ─► Investigator (LLM)─┘                 Learner (LLM) drafts        │
        ▲             asks via Outreach (LLM)      ▼                       │                     │
        │                                    Preparer (LLM + calculators)  ▼                     │
        │                                          ▼                 Controller approves ────────┘
        │                                    Approval: agent ≤ $2,500 from an active playbook, else controller
        │                                          ▼
        │                                    Executor (code) — the only writer to the ledger
        └────────────── Verify (code): recompute the statement; nothing of the overcharge may be left uncredited
```

The matcher recomputes each statement line by line from the lease abstract: pro-rata share on the center's GLA,
the controllable-expense cap over last year, the management fee on its allowed base (after the cap, without taxes,
insurance, capital or gross-up), capital exclusions, gross-up only where the lease allows it, taxes at the county's
figure, and estimates at what the bank cleared. Every planted dollar is attributed to exactly one finding, so the
findings on a statement sum to billed minus correct to the cent. A printed total within $1 of the recomputation is
rounding: accepted and auto-cleared, never a dispute.

### Where each agent acts

| Agent | Type | Can read | Can do | Cannot |
|---|---|---|---|---|
| Matcher (`bp/recon/matcher.py`) | code | all sources | create findings (breaks) | anything else |
| Investigator (`bp/agents/core.py`) | LLM | sources, breaks, playbooks, events | notes, root cause, playbook match | propose, message, post |
| Preparer (`bp/agents/core.py`) | LLM + calculators | case evidence, playbooks | proposals (amounts only from `bp/calculators.py`) | approve, execute |
| Outreach (`bp/agents/outreach.py`) | LLM | directory, case summary | send, follow up (1 bd), escalate (3 bd) | propose, post, move money |
| Approver (`bp/agents/core.py`) | LLM bound by coded policy | proposal, evidence, playbook | approve ≤ $2,500 from active playbooks | anything larger, money movement, playbooks |
| Controller | human (UI) | everything | approve anything; approve/flag/retire playbooks | — |
| Executor (`bp/agents/executor.py`) | code | approved proposals | post credits | anything unapproved or changed after approval |
| Learner (`bp/agents/learner.py`) | LLM + checks | teach sessions, history, SOP | draft playbooks | activate playbooks |
| Auditor (`bp/agents/auditor.py`) | LLM | event log only | answer "why" with citations | anything else |

### Rules enforced in code (each has a test)

- Each agent gets only its own tools. An out-of-scope call is denied and logged (T16).
- A proposal's preparer can never be its approver (T17).
- An approval stores a SHA-256 of the proposal; the executor refuses if the content changed (T18).
- Only the executor is ever issued ledger and bank credentials (T19).
- Amounts come only from deterministic calculators; a proposal that doesn't reproduce is rejected (T11).
- Every entry balances per asset, and a deferred Postgres trigger enforces it (T6, T7). Posted entries are immutable (T8).
  Closed months reject postings (T9). Idempotency keys mean no double posts (T10).
- Every state change writes an event: actor, time, case, inputs, evidence, reason (T31).

### Playbooks and the learning loop

One playbook per break key (which sources disagree on which field, e.g. `statement-lease:admin_fee`), so choosing
one is a lookup with no inference. Branches read only facts from the deterministic context step
(`bp/recon/context.py`), so the same case always takes the same branch. Induction (`bp/agents/learner.py`) drafts the
starting library from 30 disputes the team closed last season (`data/history/cases_2025.json`) and `data/SOP.md`.
The model proposes conditions, and code checks them: a condition must hold for every case it was learned from and for
none that belong to another branch. If the check fails, a deterministic predicate search is used instead. Learning adds
branches a controller approves, never model weights, so every learned behavior stays readable and revocable. A failed
verify or a human override flags the playbook.

## Real vs simulated

| Part | Status |
|---|---|
| Lease math | Real CAM clauses: pro-rata share on GLA, controllable cap, management fee base, capital exclusion, gross-up |
| Leases and statements | Simulated by the seeded generator (`bp/world/generate.py`): 40 stores, 12 landlords, lease text per store |
| County tax records | Simulated per parcel (APN), standing in for the county assessor's lookup |
| Ledger engine | Real double-entry ledger on Postgres; simulated entries |
| AP payments and bank | Simulated monthly estimate payments with bank clearing status |
| People | Personas answering from the true world (LLM-played with a key, templated without) |
| Overcharges | Planted by `bp/world/inject.py`, with an answer key |

## Scope cuts taken

Generality demo (BenchRec) not built; T35 is skipped. The Sources and Inbox pages were folded into the case detail page
(the source values and messages appear there); the Audit page and ask-why are built.
