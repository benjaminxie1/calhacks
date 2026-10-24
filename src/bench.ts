// Parallel, paired benchmark: every config plays the same seeded matches.
// Usage: tsx src/bench.ts <lineup> [matches] [seed]
//   lineup like "brain,field,field,field,field,field"; PARAMS / PARAMS_B as in run-sim.
//   WORKERS (default: cores). Prints per-seat mean ± s.e. and the field's
//   average price per letter, to compare with the live market.
import { fork } from "node:child_process";
import * as os from "node:os";
import { chr } from "./dict.js";
import { makePlayers } from "./lineup.js";
import { playMatch } from "./sim.js";

const lineup = process.argv[2] ?? "brain,field,field,field,field,field";
const N = Number(process.argv[3] ?? 40);
const seed0 = Number(process.argv[4] ?? 1);
const firstPrice = process.env.FIRST_PRICE === "1";

interface Out {
  scores: number[][];
  ranks: number[][];
  bals: number[][];
  paid: number[];
  top: number[];
  n: number[];
}

function runSlice(from: number, to: number): Out {
  const kinds = lineup.split(",");
  const players = makePlayers(kinds, seed0 + from);
  const o: Out = { scores: [], ranks: [], bals: [], paid: new Array(26).fill(0), top: new Array(26).fill(0), n: new Array(26).fill(0) };
  for (let m = from; m < to; m++) {
    const res = playMatch(players, seed0 * 100003 + m, firstPrice);
    o.scores.push(res.scores);
    o.ranks.push(res.scores.map((s) => 1 + res.scores.filter((x) => x > s).length));
    o.bals.push(res.balances);
    for (const l of res.log) {
      if (l.winner < 0) continue;
      o.paid[l.letter] += l.paid;
      o.top[l.letter] += l.top;
      o.n[l.letter]++;
    }
  }
  return o;
}

if (process.env.BENCH_CHILD) {
  const [from, to] = process.env.BENCH_CHILD.split(",").map(Number);
  process.send!(runSlice(from!, to!));
} else {
  const W = Math.min(N, Number(process.env.WORKERS ?? os.cpus().length));
  const t0 = Date.now();
  const parts = await Promise.all(
    Array.from({ length: W }, (_, w) => {
      const from = Math.floor((w * N) / W);
      const to = Math.floor(((w + 1) * N) / W);
      return new Promise<Out>((res, rej) => {
        const c = fork(process.argv[1]!, process.argv.slice(2), {
          env: { ...process.env, BENCH_CHILD: `${from},${to}` },
          execArgv: process.execArgv,
        });
        c.on("message", (m) => res(m as Out));
        c.on("error", rej);
        c.on("exit", (code) => code && rej(new Error(`worker exit ${code}`)));
      });
    }),
  );
  const all: Out = { scores: [], ranks: [], bals: [], paid: new Array(26).fill(0), top: new Array(26).fill(0), n: new Array(26).fill(0) };
  for (const p of parts) {
    all.scores.push(...p.scores);
    all.ranks.push(...p.ranks);
    all.bals.push(...p.bals);
    for (let c = 0; c < 26; c++) {
      all.paid[c] += p.paid[c]!;
      all.top[c] += p.top[c]!;
      all.n[c] += p.n[c]!;
    }
  }
  const kinds = lineup.split(",");
  const mean = (a: number[]) => a.reduce((x, y) => x + y, 0) / a.length;
  const se = (a: number[]) => {
    const m = mean(a);
    return Math.sqrt(a.reduce((x, y) => x + (y - m) ** 2, 0) / Math.max(1, a.length - 1) / a.length);
  };
  kinds.forEach((k, i) => {
    const sc = all.scores.map((r) => r[i]!);
    const rk = all.ranks.map((r) => r[i]!);
    const win = rk.filter((x) => x === 1).length / rk.length;
    console.log(
      `${(k + i).padEnd(10)} score ${mean(sc).toFixed(1).padStart(6)} ±${se(sc).toFixed(1)}  rank ${mean(rk).toFixed(2)} ±${se(rk).toFixed(2)}  win ${(100 * win).toFixed(0).padStart(3)}%  endbal ${mean(all.bals.map((r) => r[i]!)).toFixed(0)}`,
    );
  });
  if (process.env.PRICES)
    console.log(
      "paid/top: " +
        all.paid.map((p, c) => `${chr(c)}${(p / Math.max(1, all.n[c]!)).toFixed(0)}/${(all.top[c]! / Math.max(1, all.n[c]!)).toFixed(0)}`).join(" "),
    );
  console.log(`${N} matches in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
}
