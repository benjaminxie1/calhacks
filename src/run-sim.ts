// Usage: tsx src/run-sim.ts [lineup] [matches] [seed]
// Prints average score, rank and win rate per seat.
import { loadDict } from "./dict.js";
import { rng } from "./engine.js";
import { brainBot, playMatch, sharkModel, simBot, starterBot, type Player } from "./sim.js";

const lineup = process.argv[2] ?? "brain,shark,shark,shark,shark,shark";
const N = Number(process.argv[3] ?? 20);
const seed0 = Number(process.argv[4] ?? 1);
const firstPrice = process.env.FIRST_PRICE === "1";
const extra = process.env.PARAMS ? JSON.parse(process.env.PARAMS) : {};
const extraB = process.env.PARAMS_B ? JSON.parse(process.env.PARAMS_B) : {};
const d = loadDict();
const r = rng(seed0 * 7919);

function make(kind: string, i: number): Player {
  // brainL0.1 = a brain with fixed λ0 = 0.1 (the aggressive self-play optimum)
  if (kind.startsWith("brainL")) return brainBot(kind + i, { lam0: Number(kind.slice(6)) }, seed0 * 3000 + i);
  switch (kind) {
    case "brain": return brainBot(`brain${i}`, extra, seed0 * 1000 + i);
    case "brainB": return brainBot(`brainB${i}`, extraB, seed0 * 2000 + i);
    case "shark": return sharkModel(d, r, 1);
    case "shark13": return sharkModel(d, r, 1.3);
    case "starter": return starterBot(d);
    case "cheap": return simBot("Cheapskate", r);
    case "value": return simBot("ValueBidder", r);
    case "aggr": return simBot("Aggressive", r);
    default: throw new Error(kind);
  }
}
const kinds = lineup.split(",");
const players = kinds.map(make);
const sums = kinds.map(() => ({ score: 0, rank: 0, win: 0, bal: 0 }));
const t0 = Date.now();
for (let m = 0; m < N; m++) {
  // rotate seats so seat order never matters
  const res = playMatch(players, seed0 * 100003 + m, firstPrice);
  res.scores.forEach((s, i) => {
    const rank = 1 + res.scores.filter((x) => x > s).length;
    sums[i]!.score += s;
    sums[i]!.rank += rank;
    sums[i]!.win += rank === 1 ? 1 : 0;
    sums[i]!.bal += res.balances[i]!;
  });
}
kinds.forEach((k, i) => {
  const s = sums[i]!;
  console.log(`${(k + i).padEnd(10)} score ${(s.score / N).toFixed(1).padStart(6)}  rank ${(s.rank / N).toFixed(2)}  win ${((100 * s.win) / N).toFixed(0).padStart(3)}%  endbal ${(s.bal / N).toFixed(0)}`);
});
console.log(`${N} matches in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
