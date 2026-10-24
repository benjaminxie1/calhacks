# Data flywheel

Every run pulls the public match history, refits the market, and retunes the
bot in the simulator. It writes `params.json` (repo root) only when a new
setting beats the current one on the same simulated matches, by more than
two standard errors of mean rank. The live client re-reads `params.json` at the
start of every match, and `scripts/live.sh` with `LIVE_AUTOPULL=1` pulls the
branch between matches. So a committed improvement reaches the running bot
within one match.

```sh
npm run flywheel                 # full run, about 12 min on 4 cores
npm run flywheel -- --quick      # small samples, to check the pipeline (~1 min)
npm run flywheel -- --dry        # report only, write nothing
npm run flywheel -- --offline    # reuse flywheel/data/history.json
```

Steps:

1. `fetch.ts` reads `match_state`, `match_participant`, `auction_result`,
   `auction_top_bid_archive`, `word_play` and `bot` over maincloud's HTTP SQL
   with an anonymous identity. No bot token is used. The raw cache goes to
   `flywheel/data/` (gitignored).
2. `fit.ts` uses the last 150 standard 6-seat matches. It computes the mean price paid
   per letter and per third of the match, the share of late auctions with no sale,
   the average and winning scores, and a per-bot leaderboard. The new
   `seedPrice` is the observed mean price, shrunk towards the old one for letters
   with little data.
3. `field.ts` fits the opponent model. Each opponent seat bids
   `level[letter] × stage × seatScale × U(0.6, 1.3)`, with `seatScale` lognormal
   per match. The fit iterates `level` and `stage` until simulated prices match the live
   ones, then picks the opponents' minimum word length that best matches the live average score.
4. `tune.ts` and `worker.ts` run the search. The candidates are the current `params.json`,
   that plus the new seed prices, the code defaults, and random 1–3 knob steps from
   the current params. The search space covers every key of `SPACE` that exists in
   `DEFAULT_PARAMS`, so new strategy knobs are picked up automatically once they
   are listed there. Stage 1 runs 48 matches each. Stage 2 runs the current params plus
   the top two on 300 fresh paired seeds.
5. If a candidate wins, `run.ts` writes `params.json` and `flywheel/state.json`, which holds the fitted field,
   the observed market and the accepted gain. The scheduled job commits both and pushes them to
   `scrabblebot`.
