// Paired runoff: each candidate and the baseline (params.json) sit at the
// same table with four fitted-field seats, on the same seeds for every
// candidate. The edge is measured inside each match (candidate's rank minus
// the baseline's), which cancels most of the luck of the draw.
//
//   npx tsx src/runoff.ts <matches> '<json list of {"name":..,"params":{..}}>'

import { fork } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import { fieldBot } from "../flywheel/field.js";
import { loadDict } from "./dict.js";
import { rng, type Params } from "./engine.js";
import { brainBot, playMatch } from "./sim.js";

const LIVE: Partial<Params> = JSON.parse(fs.readFileSync(new URL("../params.json", import.meta.url), "utf8"));
const FIELD = JSON.parse(fs.readFileSync(new URL("../flywheel/state.json", import.meta.url), "utf8")).field;

// replace: use params as they are, instead of on top of params.json.
interface Cand { name: string; params: Partial<Params>; replace?: boolean }
interface Out { name: string; dRank: number; dScore: number; rank: number }

function runSlice(cands: Cand[], from: number, to: number): Out[] {
  const d = loadDict();
  const out: Out[] = [];
  for (const c of cands) {
    for (let m = from; m < to; m++) {
      const r = rng(m * 31 + 7);
      const players = [
        brainBot("cand", c.replace ? c.params : { ...LIVE, ...c.params }, m * 2),
        brainBot("base", LIVE, m * 2 + 1),
        ...[0, 1, 2, 3].map((i) => fieldBot(d, FIELD, r, `f${i}`)),
      ];
      const s = playMatch(players, 900000 + m).scores;
      const rank = (k: number) => 1 + s.filter((x) => x > s[k]!).length;
      out.push({ name: c.name, dRank: rank(0) - rank(1), dScore: s[0]! - s[1]!, rank: rank(0) });
    }
  }
  return out;
}

if (process.env.RUNOFF_CHILD) {
  const { cands, from, to } = JSON.parse(process.env.RUNOFF_CHILD);
  process.send!(runSlice(cands, from, to));
} else if (import.meta.url === `file://${process.argv[1]}`) {
  const N = Number(process.argv[2] ?? 200);
  const cands: Cand[] = JSON.parse(process.argv[3] ?? "[]");
  const W = Math.min(N, Number(process.env.WORKERS ?? Math.max(1, os.cpus().length - 1)));
  const t0 = Date.now();
  const parts = await Promise.all(
    Array.from({ length: W }, (_, w) => {
      const from = Math.floor((w * N) / W);
      const to = Math.floor(((w + 1) * N) / W);
      return new Promise<Out[]>((res, rej) => {
        const c = fork(process.argv[1]!, [], { env: { ...process.env, RUNOFF_CHILD: JSON.stringify({ cands, from, to }) }, execArgv: process.execArgv });
        c.on("message", (m) => res(m as Out[]));
        c.on("error", rej);
        c.on("exit", (code) => code && rej(new Error(`worker exit ${code}`)));
      });
    }),
  );
  const all = parts.flat();
  const mean = (a: number[]) => a.reduce((x, y) => x + y, 0) / a.length;
  const se = (a: number[]) => Math.sqrt(a.reduce((x, y) => x + (y - mean(a)) ** 2, 0) / Math.max(1, a.length - 1) / a.length);
  console.log(`${N} paired matches per candidate, ${((Date.now() - t0) / 1000).toFixed(0)}s (negative rank edge = better than the baseline)`);
  for (const c of cands) {
    const o = all.filter((x) => x.name === c.name);
    const dr = o.map((x) => x.dRank), ds = o.map((x) => x.dScore);
    console.log(
      `${c.name.padEnd(14)} rank vs base ${mean(dr) >= 0 ? "+" : ""}${mean(dr).toFixed(2)} ±${se(dr).toFixed(2)} (${(mean(dr) / se(dr)).toFixed(1)} s.e.)` +
        `  score vs base ${mean(ds) >= 0 ? "+" : ""}${mean(ds).toFixed(1)} ±${se(ds).toFixed(1)}  own rank ${mean(o.map((x) => x.rank)).toFixed(2)}`,
    );
  }
}
