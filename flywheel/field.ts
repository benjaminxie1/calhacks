// A parametric model of the live field, fitted to the public match history.
//
// Each opponent seat bids  level[letter] * stage[third of the bag] *
// seatScale * U(0.6, 1.3), capped by its balance. seatScale is drawn per
// match (lognormal), so a lineup mixes cheap and aggressive bots the way
// real lobbies do. Opponents play their best word of >= playMinLen letters
// whenever they can and dump everything in the last auction, which matches
// the 7-letter-heavy word mix seen live.
//
// `calibrate` adjusts level/stage until simulated sale prices match the
// observed ones, and picks playMinLen to match the observed average score.

import { loadDict, type Dict } from "../src/dict.js";
import { bestPartition, rng, spellable } from "../src/engine.js";
import { playMatch, type Player } from "../src/sim.js";
import type { Observed } from "./fit.js";

export interface FieldModel {
  level: number[]; // 26 per-letter bid levels
  stage: number[]; // multipliers for the first/middle/last third of the bag
  seatSd: number; // sd of log seat scale
  playMinLen: number;
}

export const DEFAULT_FIELD: FieldModel = {
  // The old hand-made shark levels, as a starting point for calibration.
  level: [14, 15, 19, 13, 14, 17, 12, 19, 14, 30, 21, 11, 16, 11, 14, 17, 33, 11, 12, 11, 15, 15, 17, 32, 21, 38],
  stage: [1, 1, 1],
  seatSd: 0.3,
  playMinLen: 7,
};

const TOTAL_AUCTIONS = 68;

export function fieldBot(d: Dict, f: FieldModel, r: () => number, name = "field"): Player {
  let scale = 1;
  let lastKey = "";
  return {
    name,
    start() {
      // Box-Muller
      const z = Math.sqrt(-2 * Math.log(1 - r())) * Math.cos(2 * Math.PI * r());
      scale = Math.exp(f.seatSd * z);
      lastKey = "";
    },
    bid(v) {
      if (v.bagLeft <= 0) return 0;
      const s = Math.min(2, Math.floor((3 * (TOTAL_AUCTIONS - 1 - v.bagLeft)) / TOTAL_AUCTIONS));
      const b = f.level[v.letter]! * f.stage[Math.max(0, s)]! * scale * (0.6 + 0.7 * r());
      return Math.max(0, Math.min(Math.floor(b), v.balance));
    },
    plays(v, final) {
      if (final) return bestPartition(d, v.rack, 40).words;
      const key = v.rack.join(",");
      if (key === lastKey) return [];
      lastKey = key;
      const ws = spellable(d, v.rack, f.playMinLen).sort((a, b) => d.reward[b]! - d.reward[a]!);
      return ws.length ? [d.words[ws[0]!]!] : [];
    },
    observe() {},
  };
}

export interface SimStats {
  paidByLetter: number[];
  paidByStage: number[];
  noSaleLate: number; // share of last-third auctions with no winner
  meanScore: number;
}

// Run `n` all-field matches and record what letters sold for.
export function fieldStats(f: FieldModel, n: number, seed: number): SimStats {
  const d = loadDict();
  const r = rng(seed);
  const sumL = new Array(26).fill(0), nL = new Array(26).fill(0);
  const sumS = [0, 0, 0], nS = [0, 0, 0];
  let none = 0, late = 0, score = 0;
  for (let m = 0; m < n; m++) {
    let k = 0;
    const players = Array.from({ length: 6 }, (_, i) => {
      const p = fieldBot(d, f, r, `f${i}`);
      const obs = p.observe;
      // Seat 0 records every auction's outcome (all seats see the same).
      if (i === 0)
        p.observe = (letter, paid, won) => {
          const s = Math.min(2, Math.floor((3 * k++) / TOTAL_AUCTIONS));
          // `paid` is 0 only when nobody bid.
          if (paid > 0) {
            sumL[letter] += paid; nL[letter]++;
            sumS[s] += paid; nS[s]++;
          } else if (s === 2) none++;
          if (s === 2) late++;
          obs(letter, paid, won);
        };
      return p;
    });
    const res = playMatch(players, seed * 7777 + m);
    score += res.scores.reduce((a, b) => a + b, 0) / 6;
  }
  return {
    paidByLetter: sumL.map((s, i) => (nL[i] ? s / nL[i] : 0)),
    paidByStage: sumS.map((s, i) => (nS[i] ? s / nS[i] : 0)),
    noSaleLate: late ? none / late : 0,
    meanScore: score / n,
  };
}

export function calibrate(obs: Observed, start: FieldModel = DEFAULT_FIELD, rounds = 6, n = 60, log = console.log): FieldModel {
  let f: FieldModel = { ...start, level: start.level.slice(), stage: start.stage.slice() };
  for (let it = 0; it < rounds; it++) {
    const s = fieldStats(f, n, 101 + it);
    const errL = obs.paidByLetter.map((o, c) => (s.paidByLetter[c]! > 0 ? o / s.paidByLetter[c]! : 1));
    // Letter levels absorb the overall scale; stages are relative to it.
    f.level = f.level.map((l, c) => Math.max(1, l * Math.pow(errL[c]!, 0.8)));
    const meanObs = obs.paidByStage.reduce((a, b) => a + b, 0) / 3;
    const meanSim = s.paidByStage.reduce((a, b) => a + b, 0) / 3;
    f.stage = f.stage.map((m, i) => {
      const ratio = obs.paidByStage[i]! / meanObs / (s.paidByStage[i]! / meanSim || 1);
      return Math.max(0.3, Math.min(3, m * Math.pow(ratio, 0.8)));
    });
    const err = Math.sqrt(errL.reduce((a, e) => a + Math.log(e) ** 2, 0) / 26);
    log(`  calibrate ${it}: price rms log-err ${err.toFixed(3)}, stage sim ${s.paidByStage.map((x) => x.toFixed(1)).join("/")} obs ${obs.paidByStage.map((x) => x.toFixed(1)).join("/")}, score sim ${s.meanScore.toFixed(0)} obs ${obs.meanScore.toFixed(0)}`);
  }
  // Word policy: whichever minimum length lands closest to the observed
  // average score.
  let best = f.playMinLen, bestErr = Infinity;
  for (const L of [5, 6, 7, 8]) {
    const s = fieldStats({ ...f, playMinLen: L }, n, 999);
    const e = Math.abs(s.meanScore - obs.meanScore);
    log(`  playMinLen ${L}: score ${s.meanScore.toFixed(1)} (obs ${obs.meanScore.toFixed(1)})`);
    if (e < bestErr) { bestErr = e; best = L; }
  }
  f.playMinLen = best;
  f.level = f.level.map((x) => Math.round(x * 10) / 10);
  f.stage = f.stage.map((x) => Math.round(x * 1000) / 1000);
  return f;
}
