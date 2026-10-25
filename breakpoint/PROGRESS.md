# Breakpoint: build progress (updated 2026-10-24)

> **2026-10-25: Breakpoint pivoted from brokerage reconciliation to CAM audits** (branch `claude/project-thread-ykopqi`).
> The world is now Golden Wok Restaurant Group: 40 stores, 12 landlords, annual CAM statements arriving Feb 2 to May 29,
> 2026. The novel case is an unauthorized gross-up (taught Mar 10, reused May 5). README.md is current; the brokerage
> details below are history. `make test`: 52 passed, 1 skipped. `make demo`: 33/33, $54,317.97 credited.


Branch `breakpoint`. Plan: "Breakpoint — Hyphenate Track Build Plan" (Desktop PDF). See README.md for the full picture.

## Done and verified
- Steps 0–15 and 17 of the plan. Step 16 (generality / BenchRec) is skipped as the plan's first scope cut.
- `make test`: 42 passed, 1 skipped (T35).
- `make demo`: 100% detection recall and precision, 33/33 planted breaks in their expected state with answer-key
  amounts, ledger equals truth, invariants green. Full replay with agents takes ~13 s.
- Dashboard (React/Vite/Tailwind/Recharts), served by `make api` on :8000. Every page was checked by hand in Chrome
  with no console errors, including the full teach → approve → auto-fix-in-October flow driven through the UI.

## 2026-10-24 evening (second pass)
- Claude path exercised end to end against a mocked API (agreeable, 500, refusal and malformed-output models): every
  run still ends 33/33 with the ledger equal to truth. Fixed: malformed/truncated model output and missing credentials
  crashed the replay; `make test-llm` used `--count` (pytest-repeat, not installed). Added BP_LLM_STRICT, BP_LLM_AGENTS,
  BP_LLM_TIMEOUT and tests/test_llm.py.
- Rehearsed 38 judge injections across the quarter (Replay and HTTP API). Fixed: injections on the last day or after
  the replay ended were silently never detected; the API now refuses them with a reason.
- Devpost draft in docs/DEVPOST.md, video script and judging-table notes in docs/DEMO-SCRIPT.md.
- `make test`: 51 passed, 1 skipped.

## Not done / open
- No ANTHROPIC_API_KEY on this machine, so the Claude path (`bp/agents/llm.py`) has not run against the live model.
  Set the key and run `make test-llm`, then a replay, before recording.
- Demo video, Devpost write-up, rehearsing a judge-injected break.
- Stretch: S1/S2 break types, BenchRec generality.
