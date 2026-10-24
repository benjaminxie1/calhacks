// Child process for flywheel/tune.ts: plays our bot (seat 0) against five
// field-model opponents on the given seeds and reports score and rank.
import { loadDict } from "../src/dict.js";
import { rng } from "../src/engine.js";
import { brainBot, playMatch } from "../src/sim.js";
import { fieldBot, type FieldModel } from "./field.js";

// `rivals` seats run our own engine with `rival` params, standing in for
// the top live bots (which out-score the field model); the rest of the
// six seats are field-model bots.
export interface Job { id: number; params: Record<string, unknown>; rival: Record<string, unknown>; rivals: number; field: FieldModel; seeds: number[] }
export interface Outcome { score: number; rank: number }

process.on("message", (job: Job) => {
  const d = loadDict();
  const { _cand, ...params } = job.params;
  const out: Outcome[] = [];
  for (const seed of job.seeds) {
    // Opponents draw from their own stream, so every candidate faces the
    // same lineup and the same bag on a given seed (common random numbers).
    const r = rng(seed * 31 + 7);
    const players = [
      brainBot("us", params as never, seed),
      ...Array.from({ length: job.rivals }, (_, i) => brainBot(`rival${i}`, job.rival as never, seed + 1 + i)),
      ...Array.from({ length: 5 - job.rivals }, (_, i) => fieldBot(d, job.field, r, `f${i}`)),
    ];
    const s = playMatch(players, seed).scores;
    out.push({ score: s[0]!, rank: 1 + s.filter((x) => x > s[0]!).length });
  }
  process.send!({ id: job.id, out });
});
