# Real-module check

`real-module.test.ts` plays the brain through whole matches in the ScrabbleBot
module's own test harness: real reducers, the five seeded sims, one tick per
auction. It fails if the module refuses any word the brain sends, including the
last auction's play-out. It also prints the brain's average score, to compare
with `npx tsx src/bench.ts brain,cheap,value,aggr,value,aggr`, which checks
that `src/sim.ts` plays by the module's rules.

```sh
git clone https://github.com/clockworklabs/scrabblebot ../scrabblebot-module
cp verify/real-module.test.ts ../scrabblebot-module/spacetimedb/src/tests/
cd ../scrabblebot-module/spacetimedb && npm install
CALHACKS_DIR=<path to this checkout> npx vitest run src/tests/real-module.test.ts
```

Result on 2026-10-23 (engine at ec24c1d, 12 matches): avg score 278.7, 12/12
wins, all words accepted, 14 plays in the last auction. The sim gives 271.9 for
the same lineup (48 matches).
