# ScrabbleBot handoff

The bot for the Spacetime ScrabbleBot tournament (https://scrabblebot.spacetimedb.com,
Saturday 11:59 PM). It's built from the server source at
https://github.com/clockworklabs/scrabblebot and the live match history.

## Status

- **Working:** strategy engine, an offline simulator that copies the server's rules, and
  a live client. The client connects to maincloud and subscribes; I tested that without
  a token.
- **Not done:** registration. It needs a browser login, and this cloud session had no
  Chrome access.
- **Not done:** a live match with a real token.
- **Not done:** finished tuning. See "Next steps".

## Register and run (owner, on a laptop)

1. Sign in at https://scrabblebot.spacetimedb.com. Link your spacetimedb.com identity at `/account`.
2. Create a team at `/team/new`. On `/team`, click **Generate token**. The token is shown once.
3. Run the bot:
   ```sh
   git clone -b scrabblebot --single-branch https://github.com/benjaminxie1/calhacks scrabblebot && cd scrabblebot && npm install
   BOT_NAME=<name> BOT_TOKEN=<token> npm start            # casual lobby matches (builds ELO)
   BOT_NAME=<name> TOURNAMENT_ID=<id> npm start            # tournament; id shown on /tournament
   ```
   The bot saves the token to `.token-<name>`, so later runs don't need `BOT_TOKEN`.
   For unattended runs use `BOT_NAME=<name> scripts/live.sh`: it restarts the bot after a
   crash, disconnect or code update and pulls `origin/scrabblebot` between matches.
   Params come from `params.json` (re-read at each new match, written by the flywheel),
   then `PARAMS` env on top. Every bid, result, play and match end is appended to
   `runs/live-<date>.jsonl`. `DRY_RUN=1 BOT_ID=<id>` shadows another bot's matches
   without a token, to check timing. Only the
   bot process can register for a tournament, so it must be running while registration is open.

## Game facts that drive the strategy (read from `spacetimedb/src/game.ts`)

- The bag holds 98 tiles. Each bot is dealt 5 hidden tiles. Each remaining letter gets its
  own 1 s sealed-bid auction: 68 auctions in a 6-bot match. Matches are Vickrey (the winner
  pays the second-highest bid) by default. A tournament can be set to first-price.
- **Spending money does not lower your score.** Score is only the sum of word rewards, and
  money left at the end is worth nothing. So this is a budget-pacing problem: bid v/λ.
- Word reward is the sum of letter values times a length multiplier: 1x for 3 letters or
  fewer, 1.5x at 4, 2x at 5, 2.5x at 6, 3x at 7+.
- The match ends the moment the last auction closes. The last letter can never be played.
  Play everything during the last auction (when `bagTotal == 0`).
- Tournament: Swiss rounds award points by finishing place. Then elimination knocks out
  last place each round. The final is 3 games, decided by total score.
- Sim bots bid face value −1 (Cheapskate), face value (ValueBidder), or face value +2/+4
  (Aggressive), ±2 jitter. They play their longest word of 3+ letters right after each win.

## Live field (queried anonymously from the public tables)

`curl -X POST https://maincloud.spacetimedb.com/v1/identity` returns a token. Then POST SQL to
`/v1/database/scrabblebot/sql`. `auction_result`, `auction_top_bid_archive`, `word_play` and
`match_participant` are all readable. That gives every bot's winning bids.

| bot | avg score/match | avg rank |
|---|---|---|
| LetterShark | 169 | 2.09 |
| code | 115 | 2.64 |
| bot | 108 | 2.39 |
| Doppio | 97 | 2.17 |

LetterShark bids about 12–20 on common letters and 35–45 on J/X/Q/Z. It plays almost only
7–8 letter words (CAZIQUE 81, EPIZOOTY 66), then dumps short words at the end.

## Code

- `src/engine.ts`: the strategy.
  - Monte Carlo letter value: sample future bag draws and find target words of 4+ letters
    that become completable. Each target is worth its reward minus λ × the market price of
    its missing letters. A letter's value is the score gain from adding it, compared on the
    same samples.
  - Bid = value / λ. λ is set by `lam0`, decays as the bag empties, and scales with balance.
    There is optional adaptive pacing (`paceEta`, from Balseiro–Gur).
  - Plays a word only when its reward plus the value of the leftover rack beats holding.
    In the last auction it plays the best set of words it can make (branch and bound).
  - Prices are seeded from the live data and updated each auction from what letters
    actually sold for.
- `src/sim.ts` + `src/run-sim.ts`: the offline match loop and opponent models (exact sim
  bots, the starter bot, a "shark" model of the live top bots, and copies of our bot).
  - `npx tsx src/run-sim.ts brain,shark13,shark,shark13,shark,shark13 30 6`
  - Set `PARAMS` / `PARAMS_B` (JSON) to give a seat different settings.
- `src/trace.ts`: prints every bid, win and play in one match.
- `src/index.ts`: the live client (adapted from bot-starter). `src/module_bindings` was
  copied from the repo's frontend, so the `spacetime` CLI isn't needed.

## Tuning results so far (6-bot sims)

- Against a field of shark models, `lam0=0.4` scores about 83/match, rank 2.4. That's the
  current default. More aggressive λ does worse here (0.05 scores 60).
- In self-play, lower λ (more aggressive) wins: 0.1–0.2 beats 0.4. Bots that bid all-in
  outbid each other and the moderate shark model wins. **The best λ depends on the field.**
- Adaptive pacing with `paceEta` set to 0.05 or 0.15 did not beat fixed λ yet.

## Checks against a second bot and the real module (Jonathan, Oct 23)

Jonathan built a separate bot in parallel. Its strategy is now the `pot` seat in the
bench (`src/potential/`). Its approach: a letter is worth what it adds to the summed
reward × completion chance of the best few target words, with no sampling. Everything
below is 64–96 paired matches in `src/bench.ts` unless it says otherwise.

- **The sim matches the module.** `verify/real-module.test.ts` plays the brain through
  the module's own test harness (real reducers, the seeded sims). At ec24c1d it scored
  278.7 avg over 12 matches, against 271.9 in the sim for the same lineup. After a8115f2
  plus `flushAt` 1 it scored 289.3. Every word was accepted, including last-auction plays.
  See `verify/README.md`.
- **Node 18 couldn't connect.** The SDK needs a WebSocket, which Node has built in only
  from 22. Added `undici@6`.
- **The brain beats `pot`:**

  | lineup | brain | pot |
  |---|---|---|
  | `pot,brain,field,field,field,field` | 81.9, rank 2.02 | 78.1, rank 2.48 |
  | `pot,brain,shark,shark13,shark,shark13` | 98.0, rank 2.04 | 80.9, rank 2.69 |
  | `pot,brain,shark,field,shark13,field` | 84.7, rank 2.19 | 76.7, rank 2.70 |
  | same, first-price | 84.6, rank 1.80 | 72.0, rank 2.91 |
  | `brain,pot,shark,field` (4 players) | 127.8, rank 1.98 | 111.3, rank 2.27 |

  Bidding `pot` harder made it worse (66–74). It's 10–25x faster, though (next bullet).
- **Speed.** With a 19-tile rack near the end, on a loaded machine, `choosePlays` took
  300–500 ms and `decideBid` up to ~200 ms; `pot` took 2–28 ms. Bids go out before plays
  and samples shrink with the time left, so the bid itself is covered. The risk is a long
  play search pushing back the next auction's bid. A cheap fallback for a short budget
  would close that.
- **Brain settings tried** (`brain,shark,field,shark13,field,brainB`; only brain0 changes):

  | change | brain0 | brainB5 |
  |---|---|---|
  | none | 81.0 | 81.3 |
  | `flushAt` 1 (now the default) | 80.9 | 81.2 |
  | `lamBeta` 1.5 / 2 | 72.1 / 69.5 | 81.9 / 83.2 |
  | `endgameN` 20 | 79.3 | 81.8 |
  | `denyW` 0.3 / 0.7 | 75.3 / 61.7 | 81.1 / 86.3 |
  | first-price, `firstPriceShade` 0.6 / 0.75 / 0.9 | 82.5 / 82.3 / 82.3 | 82.4 / 81.4 / 81.0 |
  | `lam0` 0.3 (default 0.4) | 77.6 | 84.8 |

  Denial hurts at both weights. First-price is fine at the default shade.
- **Open: small tables.** At 4 players (`brain,shark,field,brainB`), the field model
  roughly ties the brain: field 120.5 vs brain 113.4 / 118.0. The brain ends with 38–45
  coins unspent. Lower λ (`lam0` 0.2–0.3) and higher `availProb` (0.35, 0.45) made it worse
  (99–108). If tournament tables are smaller than 6, this is the gap to look at. One
  lead: `totalAuctions` is fixed at 68, but a 4-player match has 77 auctions.

## What polarbear's live matches show (Oct 24, 36 matches)

Polarbear runs this brain live. In the 25 matches it lost, compared with each match's winner
(from `auction_result`, `auction_top_bid_archive` and `word_play`):

| per match | polarbear | winner |
|---|---|---|
| tiles bought | 7.4 | 15.2 |
| tiles in the first third of the bag | 1.3 | 4.3 |
| vowels and 1-2 point consonants | 5.1 at ~12 | 11.7 at ~10 |
| J/Q/X/Z | 0.7 at 40 (bid 55) | 0.4 at 21 (bid 38) |
| words | 1.7 × 6.3 letters | 3.4 × 5.7 letters |
| coins left at the end | 42 | 24 |

So the brain under-buys cheap common letters early, overpays for J/Q/X/Z, waits for long
words and ends with unspent coins. The sim doesn't show this yet: against the fitted
field the brain ranks ~1.4-1.9, while live it ranks ~3.2. The field seats only cash in 8+
letter words, so they score too little. A crude "top bot" seat (field bidding with 5-6
letter cash-ins) scored only 42-46, so the strong live bots need a better model.

`rareCap` caps the bid on J/Q/X/Z at a multiple of their market price. At 1.3 it scored
94.8 / rank 1.78 against 93.8 / 1.88 without it (`brain,brainB,fit,fit,fit,fit`, 64 paired
matches, brainB = params.json), and it is in params.json now. Buying common letters
earlier through `availProb` 0.15 scored worse in the sim (88.4 / 2.16), which conflicts
with the live data. That one needs a live A/B, not the sim.

## Next steps, in order

1. **Fix the known valuation flaw.** The trace shows the bot bidding 1–9 on common letters
   early and losing them. The cause: the Monte Carlo assumes we can buy *any* of the next
   30 revealed letters, so having E now looks worthless. Fix: thin each sampled future
   letter with an `availProb` of about 0.3–0.5 (the share of letters we can really win),
   and raise `horizonCap` to about 60. Tune `availProb`. This change was drafted but not
   applied.
2. Make λ track the field. Set it from the prices seen in the current match (`price[]`),
   or retune `paceEta`.
3. Denial: work out opponents' racks from public wins minus their played words. Bid up
   letters that would complete a high-value word for them. Only rank matters in the
   tournament. (Tried at `denyW` 0.3 and 0.7: both worse. See the checks above.)
4. For first-price tournaments, check `firstPriceShade` with `FIRST_PRICE=1`. (Done:
   0.6–0.9 all score about the same, so the default 0.75 is fine.)
5. Run it live in casual lobbies before Saturday. Watch the bid timing in the logs: the
   bid has to land inside the 1 s window.
