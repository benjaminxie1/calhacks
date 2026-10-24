// A league of strategy variants playing each other. Every match seats 6 of
// the variants, drawn at random, so each variant meets the whole population
// rather than one fixed lineup. Prints each variant's mean rank and score
// with standard errors, and its edge over the baseline seat.
//
//   npx tsx src/league.ts [matches] [seed]      WORKERS (default: cores - 1)
//
// Variants start from params.json (what the live bot runs) and change one
// idea at a time.

import { fork } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import { emptyCounts, rng, type Params } from "./engine.js";
import { potentialBot } from "./potential/seat.js";
import { brainBot, playMatch, type Player } from "./sim.js";

const LIVE: Partial<Params> = JSON.parse(fs.readFileSync(new URL("../params.json", import.meta.url), "utf8"));
const { rareCap: _cap, ...LIVE_NO_CAP } = LIVE as Record<string, unknown>;

interface Variant { name: string; group: string; make: (seed: number) => Player }
const brain = (name: string, group: string, p: Partial<Params>, opts: { noRevealed?: boolean } = {}): Variant => ({
  name,
  group,
  make: (seed) => {
    const b = brainBot(name, p, seed);
    if (!opts.noRevealed) return b;
    // As the live client ran until now: opponents' revealed starting tiles unused.
    return { ...b, bid: (v) => b.bid({ ...v, revealed: emptyCounts() }), plays: (v, f) => b.plays({ ...v, revealed: emptyCounts() }, f) };
  },
});
const live = (o: Partial<Params>) => ({ ...LIVE, ...o });

export const VARIANTS: Variant[] = [
  brain("base", "baseline", LIVE),
  brain("noCap", "baseline", LIVE_NO_CAP as Partial<Params>),
  brain("codeDefaults", "baseline", {}),
  brain("noRevealed", "2 bag/opponent inference", LIVE, { noRevealed: true }),
  brain("deny0", "2 bag/opponent inference", live({ denyW: 0 })),
  brain("deny0.2", "2 bag/opponent inference", live({ denyW: 0.2 })),
  brain("deny0.4", "2 bag/opponent inference", live({ denyW: 0.4 })),
  brain("vcEq0.5", "1/3 learned values", live({ valueCorr: 0.5, valueCorrSplit: 1 })),
  brain("vcEq1", "1/3 learned values", live({ valueCorr: 1, valueCorrSplit: 1 })),
  brain("vcEq1.5", "1/3 learned values", live({ valueCorr: 1.5, valueCorrSplit: 1 })),
  brain("vcEq1noCap", "1/3 learned values", { ...(LIVE_NO_CAP as Partial<Params>), valueCorr: 1, valueCorrSplit: 1 }),
  brain("vcFace0.5", "1/3 learned values", live({ valueCorr: 0.5, valueCorrSplit: 0 })),
  brain("vcFace1", "1/3 learned values", live({ valueCorr: 1, valueCorrSplit: 0 })),
  brain("samples16", "5 rollouts", live({ samples: 16 })),
  brain("samples96", "5 rollouts", live({ samples: 96 })),
  brain("maxMiss4", "5 rollouts", live({ maxMissing: 4 })),
  brain("maxMiss6", "5 rollouts", live({ maxMissing: 6 })),
  brain("horizon40", "5 rollouts", live({ horizonCap: 40 })),
  brain("lam0.3", "4 refinement/pacing", live({ lam0: 0.3 })),
  brain("lam0.5", "4 refinement/pacing", live({ lam0: 0.5 })),
  brain("lamGamma0.6", "4 refinement/pacing", live({ lamGamma: 0.6 })),
  brain("playEps0.5", "4 refinement/pacing", live({ playEps: 0.5 })),
  brain("avail0.15", "4 refinement/pacing", live({ availProb: 0.15 })),
  brain("endgame20", "4 refinement/pacing", live({ endgameN: 20 })),
  { name: "pot", group: "other strategy", make: () => potentialBot("pot", {}) },
];

interface Out { name: string; rank: number; score: number; rel: number }

function runSlice(from: number, to: number, seed0: number): Out[] {
  const out: Out[] = [];
  for (let m = from; m < to; m++) {
    const r = rng(seed0 * 100003 + m * 7);
    // Six distinct variants, uniformly.
    const idx = VARIANTS.map((_, i) => i);
    for (let i = idx.length - 1; i > 0; i--) {
      const j = Math.floor(r() * (i + 1));
      [idx[i], idx[j]] = [idx[j]!, idx[i]!];
    }
    const seat = idx.slice(0, 6);
    const players = seat.map((i, k) => VARIANTS[i]!.make(seed0 * 1000 + m * 6 + k));
    const { scores } = playMatch(players, seed0 * 100003 + m);
    const mean = scores.reduce((a, b) => a + b, 0) / 6;
    seat.forEach((i, k) => {
      const s = scores[k]!;
      out.push({ name: VARIANTS[i]!.name, rank: 1 + scores.filter((x) => x > s).length, score: s, rel: s - (mean * 6 - s) / 5 });
    });
  }
  return out;
}

if (process.env.LEAGUE_CHILD) {
  const [from, to, seed] = process.env.LEAGUE_CHILD.split(",").map(Number);
  process.send!(runSlice(from!, to!, seed!));
} else if (import.meta.url === `file://${process.argv[1]}`) {
  const N = Number(process.argv[2] ?? 1000);
  const seed0 = Number(process.argv[3] ?? 1);
  const W = Math.min(N, Number(process.env.WORKERS ?? Math.max(1, os.cpus().length - 1)));
  const t0 = Date.now();
  const parts = await Promise.all(
    Array.from({ length: W }, (_, w) => {
      const from = Math.floor((w * N) / W);
      const to = Math.floor(((w + 1) * N) / W);
      return new Promise<Out[]>((res, rej) => {
        const c = fork(process.argv[1]!, process.argv.slice(2), { env: { ...process.env, LEAGUE_CHILD: `${from},${to},${seed0}` }, execArgv: process.execArgv });
        c.on("message", (m) => res(m as Out[]));
        c.on("error", rej);
        c.on("exit", (code) => code && rej(new Error(`worker exit ${code}`)));
      });
    }),
  );
  const all = parts.flat();
  const by = new Map<string, Out[]>();
  for (const o of all) (by.get(o.name) ?? by.set(o.name, []).get(o.name)!).push(o);
  const mean = (a: number[]) => a.reduce((x, y) => x + y, 0) / a.length;
  const se = (a: number[]) => Math.sqrt(a.reduce((x, y) => x + (y - mean(a)) ** 2, 0) / Math.max(1, a.length - 1) / a.length);
  const base = by.get("base")!;
  const rows = VARIANTS.map((v) => ({ v, o: by.get(v.name) ?? [] })).sort((a, b) => mean(a.o.map((x) => x.rank)) - mean(b.o.map((x) => x.rank)));
  console.log(`${N} matches, ${all.length / 6 | 0} seats of 6, ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  console.log("variant".padEnd(13), "group".padEnd(26), "  n", " rank", "  s.e.", " score", " vs table", " edge vs base (rank, s.e.)");
  for (const { v, o } of rows) {
    const rk = o.map((x) => x.rank);
    const d = mean(base.map((x) => x.rank)) - mean(rk);
    const dse = Math.sqrt(se(base.map((x) => x.rank)) ** 2 + se(rk) ** 2);
    console.log(
      v.name.padEnd(13), v.group.padEnd(26), String(o.length).padStart(4), mean(rk).toFixed(2).padStart(5), se(rk).toFixed(2).padStart(6),
      mean(o.map((x) => x.score)).toFixed(1).padStart(6), mean(o.map((x) => x.rel)).toFixed(1).padStart(9),
      v.name === "base" ? "" : `   ${d >= 0 ? "+" : ""}${d.toFixed(2)} (${(d / dse).toFixed(1)})`,
    );
  }
}
