// Parameter search in the simulator. Candidates are judged on the same
// seeds as the incumbent (paired), and one only replaces the incumbent if
// its mean rank is better by a margin larger than the paired noise.

import { fork, type ChildProcess } from "node:child_process";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_PARAMS, rng } from "../src/engine.js";
import type { FieldModel } from "./field.js";
import type { Job, Outcome } from "./worker.js";

export type ParamSet = Record<string, number | number[]>;

// Search space. Keys that the current engine doesn't have are skipped, so
// this can list knobs from strategy work that hasn't landed yet.
export const SPACE: Record<string, { lo: number; hi: number; int?: boolean; log?: boolean }> = {
  lam0: { lo: 0.05, hi: 1.2, log: true },
  lamBeta: { lo: 0.3, hi: 2.0 },
  lamGamma: { lo: 0, hi: 1.2 },
  availProb: { lo: 0.2, hi: 1.0 },
  horizonCap: { lo: 20, hi: 68, int: true },
  playEps: { lo: 0, hi: 3 },
  lamMarket: { lo: 0, hi: 1.5 },
  denyW: { lo: 0, hi: 0.6 },
  endgameN: { lo: 6, hi: 20, int: true },
  bidMult: { lo: 1, hi: 1.8 },
  bidFloor: { lo: 0, hi: 1.5 },
  floorMinV: { lo: 0.3, hi: 3 },
};

export function tunableKeys(): string[] {
  return Object.keys(SPACE).filter((k) => k in DEFAULT_PARAMS);
}

// A random step around `base` on 1-3 knobs.
export function perturb(base: ParamSet, r: () => number, scale = 1): ParamSet {
  const keys = tunableKeys();
  const out: ParamSet = { ...base };
  const nk = 1 + Math.floor(r() * 3);
  for (let i = 0; i < nk; i++) {
    const k = keys[Math.floor(r() * keys.length)]!;
    const s = SPACE[k]!;
    const cur = (out[k] as number | undefined) ?? (DEFAULT_PARAMS as unknown as Record<string, number>)[k]!;
    const z = (r() * 2 - 1) * 0.35 * scale;
    let v = s.log ? cur * Math.exp(z * Math.log(s.hi / s.lo)) : cur + z * (s.hi - s.lo);
    v = Math.max(s.lo, Math.min(s.hi, v));
    out[k] = s.int ? Math.round(v) : Math.round(v * 1000) / 1000;
  }
  return out;
}

export interface Score { meanRank: number; meanScore: number; ranks: number[]; scores: number[] }

// Runs every (candidate, seeds) pair across a pool of child processes.
export async function evaluate(cands: ParamSet[], field: FieldModel, seeds: number[], rival: ParamSet, rivals: number, workers = os.cpus().length): Promise<Score[]> {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const chunk = Math.max(2, Math.ceil(seeds.length / Math.max(1, Math.ceil((workers * 2) / cands.length))));
  const jobs: Job[] = [];
  cands.forEach((c, ci) => {
    for (let i = 0; i < seeds.length; i += chunk)
      jobs.push({ id: jobs.length, params: { ...c, _cand: ci } as never, rival, rivals, field, seeds: seeds.slice(i, i + chunk) });
  });
  const results = new Map<number, Outcome[]>();
  const pool: ChildProcess[] = [];
  let next = 0;
  await new Promise<void>((resolve, reject) => {
    const feed = (w: ChildProcess) => {
      if (next < jobs.length) w.send(jobs[next++]!);
      else if (results.size === jobs.length) resolve();
    };
    for (let i = 0; i < Math.min(workers, jobs.length); i++) {
      const w = fork(path.join(here, "worker.ts"), [], { execArgv: ["--import", "tsx"] });
      w.on("message", (m: { id: number; out: Outcome[] }) => {
        results.set(m.id, m.out);
        feed(w);
      });
      w.on("error", reject);
      w.on("exit", (code) => {
        if (code && results.size < jobs.length) reject(new Error(`worker exited ${code}`));
      });
      pool.push(w);
      feed(w);
    }
  }).finally(() => pool.forEach((w) => w.kill()));
  return cands.map((_, ci) => {
    const outs = jobs.filter((j) => (j.params as { _cand: number })._cand === ci).flatMap((j) => results.get(j.id)!);
    const ranks = outs.map((o) => o.rank);
    const scores = outs.map((o) => o.score);
    return {
      meanRank: ranks.reduce((a, b) => a + b, 0) / ranks.length,
      meanScore: scores.reduce((a, b) => a + b, 0) / scores.length,
      ranks,
      scores,
    };
  });
}

// Paired comparison of b against a on the same seeds: mean rank gain
// (positive = b better) and its standard error.
export function paired(a: Score, b: Score): { gain: number; se: number } {
  const d = a.ranks.map((r, i) => r - b.ranks[i]! + (b.scores[i]! - a.scores[i]!) * 0.001);
  const n = d.length;
  const m = d.reduce((x, y) => x + y, 0) / n;
  const v = d.reduce((x, y) => x + (y - m) ** 2, 0) / Math.max(1, n - 1);
  return { gain: m, se: Math.sqrt(v / n) };
}

export function seedsFrom(base: number, n: number): number[] {
  const r = rng(base);
  return Array.from({ length: n }, () => Math.floor(r() * 2 ** 31));
}
