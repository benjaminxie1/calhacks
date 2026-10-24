// The potential strategy (src/potential/strategy.ts) as a seat in the sim,
// so it can be benchmarked against the brain on the same seeded matches.
import { emptyCounts, unseenPool, type Counts } from "../engine.js";
import type { Player, View } from "../sim.js";
import { Strategy, type Params } from "./strategy.js";
import { letterOf } from "./words.js";

export function potentialBot(name: string, p: Partial<Params> = {}): Player {
  const s = new Strategy(p);
  let myStart: Counts = emptyCounts();
  const decide = (v: View) =>
    s.decideFrom(
      Uint8Array.from(v.rack),
      v.balance,
      letterOf(v.letter),
      v.bagLeft,
      unseenPool(v.auctioned, myStart, v.revealed),
    );
  return {
    name,
    start(rack) {
      myStart = Int16Array.from(rack);
    },
    bid: (v) => decide(v).bid,
    plays: (v) => decide(v).words,
    observe() {},
  };
}
