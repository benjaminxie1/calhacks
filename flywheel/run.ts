// One turn of the data flywheel:
//   1. pull the public match history from maincloud,
//   2. summarise the live market and refit the seed prices,
//   3. recalibrate the field model so sim prices match live prices,
//   4. search parameters in the simulator against that field,
//   5. write params.json (read by the live bot at each new match) only if
//      the best candidate beats the current params.json on paired seeds.
//
// Usage: npm run flywheel [-- --quick] [-- --dry]
//   --quick  small sample sizes, for testing the pipeline
//   --dry    report only; don't write params.json or flywheel/state.json

import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_PARAMS, SEED_PRICE, rng } from "../src/engine.js";
import { fetchHistory, loadHistory, saveHistory } from "./fetch.js";
import { calibrate, DEFAULT_FIELD, type FieldModel } from "./field.js";
import { fitSeedPrice, observe, type Observed } from "./fit.js";
import { evaluate, paired, perturb, seedsFrom, type ParamSet, type Score } from "./tune.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PARAMS_FILE = path.join(ROOT, "params.json");
const STATE_FILE = path.join(ROOT, "flywheel", "state.json");
const DATA_DIR = path.join(ROOT, "flywheel", "data");
const args = new Set(process.argv.slice(2));
const QUICK = args.has("--quick");
const DRY = args.has("--dry");
const OFFLINE = args.has("--offline"); // reuse flywheel/data/history.json

interface State {
  updatedAt: string;
  observed: Omit<Observed, "bots"> & { bots: Observed["bots"] };
  field: FieldModel;
  lastAccepted?: { at: string; gain: number; se: number; meanRank: number; meanScore: number; was: { meanRank: number; meanScore: number } };
}

const readJson = <T>(f: string): T | undefined => {
  try {
    return JSON.parse(fs.readFileSync(f, "utf8")) as T;
  } catch {
    return undefined;
  }
};

// Keep only what the engine understands, like the live loader does.
function clean(p: Record<string, unknown>): ParamSet {
  const out: ParamSet = {};
  for (const [k, v] of Object.entries(p)) {
    if (k in DEFAULT_PARAMS && typeof v === "number" && Number.isFinite(v)) out[k] = v;
    if (k === "seedPrice" && Array.isArray(v) && v.length === 26) out[k] = v as number[];
  }
  return out;
}

const fmt = (s: Score) => `rank ${s.meanRank.toFixed(3)} score ${s.meanScore.toFixed(1)}`;
const knobs = (p: ParamSet) => JSON.stringify(Object.fromEntries(Object.entries(p).filter(([k]) => k !== "seedPrice")));

async function main() {
  const t0 = Date.now();
  const log = (m: string) => console.log(`[flywheel ${((Date.now() - t0) / 1000).toFixed(0)}s] ${m}`);

  const h = OFFLINE ? loadHistory(DATA_DIR) : await fetchHistory();
  if (!OFFLINE) saveHistory(h, DATA_DIR);
  const o = observe(h);
  log(`history: ${h.matches.length} matches, window ${o.matches} since ${o.since}; mean score ${o.meanScore}, winner ${o.winScore}`);
  const us = o.bots.find((b) => b.name === (process.env.BOT_NAME ?? "benx"));
  if (us) log(`benx in window: ${us.n} matches, score ${us.score}, rank ${us.rank}`);
  if (o.matches < 20) throw new Error("too few matches to fit");

  const state = readJson<State>(STATE_FILE);
  const incumbent = clean(readJson<Record<string, unknown>>(PARAMS_FILE) ?? {});
  const prevSeed = (incumbent.seedPrice as number[] | undefined) ?? SEED_PRICE;
  const seedPrice = fitSeedPrice(o, prevSeed, h);

  log("calibrating field model");
  const field = calibrate(o, state?.field ?? DEFAULT_FIELD, QUICK ? 2 : 5, QUICK ? 20 : 60, (m) => log(m));
  log(`field: playMinLen ${field.playMinLen}, stage ${field.stage.join("/")}`);

  // Candidates. [0] is params.json exactly as the live bot runs it now; the
  // rest use the refreshed seed prices.
  const r = rng(Date.now() & 0xffffffff);
  const base = { ...incumbent, seedPrice };
  const cands: ParamSet[] = [incumbent, base, { seedPrice }];
  const nPerturb = QUICK ? 2 : 7;
  for (let i = 0; i < nPerturb; i++) cands.push(perturb(base, r, i < nPerturb / 2 ? 1 : 0.5));
  const seen = new Set<string>();
  for (let i = cands.length - 1; i >= 0; i--) {
    const k = JSON.stringify(Object.entries(cands[i]!).sort());
    if (seen.has(k) && i > 0) cands.splice(i, 1);
    seen.add(k);
  }

  // One seat runs the live bot's current settings: the top live bots
  // out-score the fitted field, and a candidate has to beat them too.
  const rival = process.env.FLYWHEEL_NO_RIVAL ? null : incumbent;
  const n1 = QUICK ? 8 : 40;
  log(`stage 1: ${cands.length} candidates x ${n1} matches`);
  const s1 = await evaluate(cands, field, seedsFrom(r() * 1e9, n1), rival);
  cands.forEach((c, i) => log(`  [${i}] ${fmt(s1[i]!)}  ${i === 0 ? "(current params.json) " : ""}${knobs(c)}`));

  // Stage 2: the incumbent and the two best challengers on fresh seeds.
  const order = s1.map((s, i) => ({ i, s })).filter((x) => x.i > 0).sort((a, b) => a.s.meanRank - b.s.meanRank || b.s.meanScore - a.s.meanScore);
  const finalists = [0, ...order.slice(0, 2).map((x) => x.i)];
  const n2 = QUICK ? 12 : 200;
  log(`stage 2: candidates ${finalists.join(",")} x ${n2} matches`);
  const s2 = await evaluate(finalists.map((i) => cands[i]!), field, seedsFrom(r() * 1e9, n2), rival);
  let bestK = 0;
  let best = { gain: 0, se: 0 };
  for (let k = 1; k < finalists.length; k++) {
    const pr = paired(s2[0]!, s2[k]!);
    log(`  [${finalists[k]}] ${fmt(s2[k]!)} vs current ${fmt(s2[0]!)}: gain ${pr.gain.toFixed(3)} ± ${pr.se.toFixed(3)}`);
    if (pr.gain - 2 * pr.se > 0 && pr.gain > best.gain) {
      bestK = k;
      best = pr;
    }
  }

  const nextState: State = {
    updatedAt: new Date().toISOString(),
    observed: o,
    field,
    lastAccepted: state?.lastAccepted,
  };
  if (bestK === 0) {
    log("no candidate beat the current params by 2 standard errors; params.json unchanged");
    console.log("FLYWHEEL_RESULT unchanged");
    return;
  }
  const winner = cands[finalists[bestK]!]!;
  nextState.lastAccepted = {
    at: nextState.updatedAt,
    gain: best.gain,
    se: best.se,
    meanRank: s2[bestK]!.meanRank,
    meanScore: s2[bestK]!.meanScore,
    was: { meanRank: s2[0]!.meanRank, meanScore: s2[0]!.meanScore },
  };
  log(`accepting [${finalists[bestK]}] ${knobs(winner)}`);
  if (DRY) {
    console.log("FLYWHEEL_RESULT improved (dry run, nothing written)");
    return;
  }
  fs.writeFileSync(PARAMS_FILE, JSON.stringify(winner, null, 2) + "\n");
  fs.writeFileSync(STATE_FILE, JSON.stringify(nextState, null, 2) + "\n");
  console.log(`FLYWHEEL_RESULT improved rank ${s2[0]!.meanRank.toFixed(3)} -> ${s2[bestK]!.meanRank.toFixed(3)}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
