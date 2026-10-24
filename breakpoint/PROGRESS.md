# Breakpoint: build progress (stopped 2026-10-23, late)

Branch `breakpoint`. Plan: "Breakpoint — Hyphenate Track Build Plan" (Desktop PDF).

## Done and verified
- Steps 0–7: repo, docker-compose (Postgres :5433), Makefile, CLAUDE.md; Pydantic contracts; seeded world
  generator (22k orders, 40 real EDGAR symbols/CIKs); injector + answer key (33 planted breaks); keyed source
  services; Postgres ledger (immutability, per-asset balance, period-lock triggers; idempotency); matcher.
- A matcher-only replay of all 81 days (`python -m bp.engine.replay --no-agents`, ~20s) found every planted break
  with 0 false positives.
- Induction from the Q2 history + SOP rebuilds the 4 known playbooks (`bp.agents.learner.induct`).

## Written, not yet run or tested
- `bp/calculators.py`, `bp/agents/{llm,tools,approvals,executor,core,outreach,learner}.py`, `bp/engine/workflow.py`,
  `bp/recon/context.py`, `bp/playbooks/store.py`.

## Next, in order
1. Run the full replay with agents: `python -m bp.engine.replay --teach scripted`, then debug the workflow.
2. Write tests T1–T36 in `tests/` (none exist yet).
3. Auditor (ask-why), eval script / Proof metrics, live inject-break endpoint.
4. Dashboard API (`bp/api/app.py`) + React dashboard (`web/`), P0 pages.
5. `make demo`, README with the real-vs-simulated table.

## Resume
`open -a Docker && make up && make world` (world files are gitignored), then step 1.
No ANTHROPIC_API_KEY on this machine yet: agents run their deterministic offline path until one is set.
