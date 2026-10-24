# FraudShield Swarm — agent instructions

Read `PLAN.md` first, then `fraudshield/SPEC.md` and `fraudshield/FRAMEWORK.md`. This is a Cal Hacks 13.0 entry (Oct 23–25 2026). Hard deadline Sun Oct 25 9:00 AM PT.

## Rules that bind every session
- **All work is done during the hackathon.** Never copy files from `~/recursiveagents` (GridTwin) or `~/Downloads/headroom`. Reuse the ideas, re-derive the code.
- One thing done well: investigate a transaction-monitoring alert with three scoped specialists (graph analyst, sanctions checker, behavioral profiler), resolve their disagreements in a fixed protocol, and propose a disposition a human approves.
- Scope cuts are final unless PLAN.md changes. Targets are Hyphenate, SpacetimeDB, Anthropic (only once the Claude path has run) and Microsoft. Don't build for CRS, Armadin, Elastic or Factory.
- **No model decides.** Policy R1–R8 in `fraudshield/policy.py` computes every disposition. A model may only read untrusted text, and must return the verbatim passage it used. Code verifies the passage and does every comparison. An unverified reading neither confirms nor discounts.
- **Nothing executes.** No agent or tool moves money, freezes an account, files a SAR or contacts a customer. Actions are `Recommendation` rows with `status: proposed`, for a human analyst.
- Each specialist reads only its `View`. A finding must cite evidence IDs that exist and that the agent was shown. Findings are append-only. A revision supersedes the agent's current position, only in a resolution round, and must cite new evidence.
- The synthesizer and other agents read only `Finding.public()`, never an agent's `note`.
- `unknown` blocks a close. A confirmed watchlist match, or 50%+ aggregate blocked ownership, is held whatever the other findings say (R1).
- **The 50% rule:** blocked ownership is summed, never multiplied. An owner that is itself 50%+ blocked counts in full.
- A broken rule records a `Rejection` and returns. In the SpacetimeDB module, insert a `rejection` row and return; don't throw `SenderError` when the row has to survive, because rollback behaviour is unconfirmed.
- Hidden truth (`Truth`) is read only by `bench.py`. Agents never see it.
- All data is simulated: invented people, banks, registries and a fictional watchlist. Never put a real sanctions list or real customer data in this public repo.
- **Claims match runs.** The bench scores scenarios we wrote ourselves, so it is not a detection rate; say so. Don't call Claude load-bearing until a run with `--extractor claude` has succeeded; reports label which reader ran.
- No API keys or SpacetimeDB identity tokens in the repo. `.env` is gitignored.

## Layout
- `fraudshield/`: the build
  - `stream.py` (scenarios, RFI replies, registry, hidden truth)
  - `monitor.py` (alert rules)
  - `case.py` (case file, views, rules)
  - `specialists.py`
  - `extract.py` (verified reading of untrusted text)
  - `protocol.py`, `policy.py`, `report.py`, `swarm.py`, `run.py`, `bench.py`, `inject.py`
  - `tests/`
- To be built:
  - `module/`: the SpacetimeDB module (TypeScript), with tables and reducers holding the case-file rules
  - `fraudshield/stdb.py`: thin HTTP client (identity, call, SQL); there is no Python SDK
  - `dashboard/`: React + SpacetimeDB TypeScript SDK (case view, analyst phone view)
- Stale, so replace and don't extend: `proven/`, `ledger/`, `viewer/`, `docs/DEMO.md`, `docs/JUDGE_QA.md`, `tests/test_skeleton.py`, `scripts/inject.py`, `scripts/smoke_droid.py`. `scripts/smoke_claude.py` still works as a credential check.

## Commands
```bash
.venv/bin/python -m fraudshield.run                       # all scenarios -> runs/fraudshield/*.md
.venv/bin/python -m fraudshield.run --extractor claude    # Claude reads RFI replies (needs credentials)
.venv/bin/python -m fraudshield.bench --seeds 30
.venv/bin/python -m fraudshield.inject --help             # judge-typed case
.venv/bin/pytest -q
.venv/bin/python scripts/smoke_claude.py                  # one Claude call
spacetime publish -p module <db>                          # once module/ exists; -s <server> for a local `spacetime start`
spacetime sql <db> "SELECT * FROM finding"
```
