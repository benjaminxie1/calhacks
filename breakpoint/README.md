# Breakpoint

**An autonomous reconciliation team for a retail brokerage.** It finds trade and cash breaks across five systems,
resolves the ones it has learned, and asks a human only when it meets something new.

**Seats it fills:** trade reconciliation analyst and settlements / cash ops analyst at a retail brokerage.

Built for the Cal Hacks 13.0 Hyphenate track, "The Hyphenated Enterprise: Autonomous Systems for Finance".

## The moment

1. Replay Q3 in fast-forward. Known breaks (partial fills booked as full, stale commission schedules, duplicate
   execution reports, missed dividends) resolve themselves. Each one cites the playbook and branch it used and the person who taught it.
2. On Aug 13 a 2:1 NFLX split hits. No playbook fits, so the agent stops and asks the ops lead. She picks the trusted
   source, the action and the fact that makes this case different. The learner turns that into a draft playbook branch:
   `split_on_symbol and custodian_qty == ledger_qty * split_ratio`.
3. The controller approves it. On Oct 15 a COST split is fixed automatically for all ten holders, citing the August lesson.
4. A judge injects a break of their choice from the UI and watches it get handled.

## Proof (seed 42, `make demo`)

| Metric | Result |
|---|---|
| Detection recall / precision vs the answer key | 100% / 100% (0 unplanted breaks) |
| Planted breaks in their expected final state, with answer-key amounts | 33 / 33 |
| Ledger after replay equals the correct books built from the true world | yes, every client and firm balance |
| Invariants: trial balance zero daily, entries balanced, no closed-period postings, unique idempotency keys, no floats | all green |
| Cases closed by an active playbook with no human | 20 of 23 (2 taught, 1 escalated to the controller by design) |
| Judge-style injections across the quarter (rehearsal) | 38; all landing inside the replay detected and resolved with answer-key amounts |
| Full 81-day replay with agents | ~13 s |

The client behind the second ACH return never answers the cash call. That case is escalated to the controller after 3
business days, as specified; it is the expected outcome, not a miss.

## Run it

```sh
make setup        # Python 3.12 venv (uv)
make up           # Postgres in Docker on :5433
make world        # seeded world + answer key in data/world/seed42 (~6 s)
make demo         # replay the quarter with scripted people and score it against the answer key
make test         # 52 tests (T1-T37 plus model-fallback tests; T35 generality skipped as the plan's first scope cut)
make api          # builds the dashboard and serves it with the API on http://localhost:8000
```

In the dashboard header, set the controller and ops lead to "you (UI)" to approve and teach by hand. Set them to the scripted
Sam Lee and Dana Ortiz to let the replay run unattended.

With `ANTHROPIC_API_KEY` set in `.env`, the agents decide with Claude (`claude-opus-5-5`, structured outputs). Without
a key, every agent runs a deterministic offline policy, so tests and the demo are reproducible either way.
`make test-llm` runs the LLM-dependent tests (T26, T27, T32) three times each against the live model, with
`BP_LLM_STRICT=1` so a silent fallback to the offline policy fails the test instead of passing it. If a model call is
refused, malformed, times out (`BP_LLM_TIMEOUT`, default 60 s) or has no credentials, the agent uses its offline policy and
the event records why. `BP_LLM_AGENTS=investigator,outreach,auditor` limits the model to those agents (a full replay makes
about 90 model calls).

Live injection (Inject break page) supports B1, B2, B3 and H2. B2 needs a date after Aug 17 and a standard or active
account. B1, B3 and H2 surface at T+1 settlement, so they are refused on the replay's last day.

## Architecture

```
 sources (8, each behind its own API key)          ┌──────────── Playbook library ◄─────────────┐
 OMS · executions · booking feed · custodian       │   (versioned, per-branch provenance+stats) │
 bank · reference/corp actions · fees · directory  ▼                                            │
        │                                     playbook fits? ──no──► Teach mode (human)          │
        ▼                                          │yes                    │                     │
  Matcher (code) ──► Investigator (LLM) ───────────┘                 Learner (LLM) drafts        │
        ▲             asks via Outreach (LLM)      ▼                       │                     │
        │                                    Preparer (LLM + calculators)  ▼                     │
        │                                          ▼                 Controller approves ────────┘
        │                                    Approval: agent ≤ $500 from an active playbook, else controller
        │                                          ▼
        │                                    Executor (code) — the only writer to ledger and bank
        └────────────── Verify (code): rerun recon on the affected keys; flag the playbook if it fails
```

### Where each agent acts

| Agent | Type | Can read | Can do | Cannot |
|---|---|---|---|---|
| Matcher (`bp/recon/matcher.py`) | code | all sources | create breaks | anything else |
| Investigator (`bp/agents/core.py`) | LLM | sources, breaks, playbooks, events | notes, root cause, playbook match | propose, message, post |
| Preparer (`bp/agents/core.py`) | LLM + calculators | case evidence, playbooks | proposals (amounts only from `bp/calculators.py`) | approve, execute |
| Outreach (`bp/agents/outreach.py`) | LLM | directory, case summary | send, follow up (1 bd), escalate (3 bd) | propose, post, move money |
| Approver (`bp/agents/core.py`) | LLM bound by coded policy | proposal, evidence, playbook | approve ≤ $500 from active playbooks | anything larger, money movement, playbooks |
| Controller | human (UI) | everything | approve anything; approve/flag/retire playbooks | — |
| Executor (`bp/agents/executor.py`) | code | approved proposals | post, transfer | anything unapproved or changed after approval |
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

One playbook per break key (which sources disagree on which field), so choosing one is a lookup with no inference.
Branches read only facts from the deterministic context step (`bp/recon/context.py`), so the same case always takes
the same branch. Induction (`bp/agents/learner.py`) drafts the starting library from 30 resolved Q2 tickets
(`data/history/q2_cases.json`) and `data/SOP.md`. The model proposes conditions, and code checks them: a condition must hold
for every case it was learned from and for none that belong to another branch. If the check fails, a deterministic predicate
search is used instead. Learning adds branches a controller approves, never model weights, so every learned behavior stays
readable and revocable. A failed verify or a human override flags the playbook.

## Real vs simulated

| Part | Status |
|---|---|
| Symbols and CIKs | Real, from SEC EDGAR (`data/symbols.json`) |
| Prices | Simulated: seeded random walk (labeled) |
| Ledger engine | Real double-entry ledger on Postgres (the plan's Formance fallback); simulated entries |
| Bank | In-app mock bank with real ACH return codes (the plan's Increase fallback) |
| Orders, executions, custodian | Simulated by the seeded generator (`bp/world/generate.py`) |
| People | Personas answering from the true world (LLM-played with a key, templated without) |
| Breaks | Planted by `bp/world/inject.py`, with an answer key |

## Scope cuts taken (from the plan's list)

Generality demo (BenchRec) not built; T35 is skipped. Formance was replaced by the Postgres ledger and Increase by the mock bank.
The Sources and Inbox pages were folded into the case detail page (the source values and messages appear there); the Audit page and ask-why are built.
Stretch break types S1 and S2 are not planted.
