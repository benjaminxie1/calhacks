# Breakpoint — rules for working in this repo

Autonomous reconciliation team for a mock retail brokerage (Meridian Securities).
Build plan: the "Breakpoint — Hyphenate Track Build Plan" (requirements R1–R28, tests T1–T37).

## Non-negotiable rules
- Decimal only. No floats for money or quantities, anywhere (Python `Decimal`, Postgres `NUMERIC`).
- Tests before code. Each build step names the tests that define "done".
- Only the executor writes to the ledger or the bank. Agents never hold a credential.
- Never edit a closed period. Fixes post in the open period with a reference to the original entry.
- Every state change emits an event (`bp.agents.events.emit`).
- LLM output never sets an amount. Amounts come from `bp.calculators` only.

## Layout
- `bp/world/` seeded true-world generator + break injector + answer key
- `bp/sources/` the eight source systems (store, FastAPI services with per-source keys)
- `bp/ledger/` double-entry ledger on Postgres + mock bank
- `bp/recon/` deterministic matcher (creates breaks, nothing else)
- `bp/agents/` llm wrapper, tool registry, investigator, preparer, approver, executor, outreach, learner, auditor
- `bp/playbooks/` schema, condition language, action library, induction
- `bp/engine/` replay clock and case workflow
- `bp/api/` dashboard API; `web/` React dashboard

## Commands
make up | make world SEED=42 | make replay | make test | make demo | make api | make web
