// Usage: tsx src/run-sim.ts [lineup] [matches] [seed]
// Prints average score, rank and win rate per seat.
import { makePlayers } from "./lineup.js";
import { playMatch } from "./sim.js";

const lineup = process.argv[2] ?? "brain,shark,shark,shark,shark,shark";
const N = Number(process.argv[3] ?? 20);
const seed0 = Number(process.argv[4] ?? 1);
const firstPrice = process.env.FIRST_PRICE === "1";
const kinds = lineup.split(",");
const players = makePlayers(kinds, seed0);
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
