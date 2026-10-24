# Breakpoint: build progress (updated 2026-10-24)

Branch `breakpoint`. Plan: "Breakpoint — Hyphenate Track Build Plan" (Desktop PDF). See README.md for the full picture.

## Done and verified
- Steps 0–15 and 17 of the plan. Step 16 (generality / BenchRec) is skipped as the plan's first scope cut.
- `make test`: 42 passed, 1 skipped (T35).
- `make demo`: 100% detection recall and precision, 33/33 planted breaks in their expected state with answer-key
  amounts, ledger equals truth, invariants green. Full replay with agents takes ~13 s.
- Dashboard (React/Vite/Tailwind/Recharts), served by `make api` on :8000. Every page was checked by hand in Chrome
  with no console errors, including the full teach → approve → auto-fix-in-October flow driven through the UI.

## Not done / open
- No ANTHROPIC_API_KEY on this machine, so the Claude path (`bp/agents/llm.py`) has not run against the live model.
  Set the key and run `make test-llm`, then a replay, before recording.
- Demo video, Devpost write-up, rehearsing a judge-injected break.
- Stretch: S1/S2 break types, BenchRec generality.
