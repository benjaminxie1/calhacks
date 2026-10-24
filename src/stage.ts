// Tournament stage awareness. The server's tournament (spacetimedb/src/
// tournament.ts) asks for something different at each stage:
//   * Swiss: tables of `matchSize` bots (admin default 4); Swiss points are
//     matchSize - place, ties share the higher place, total score breaks
//     ties. So: minimise mean finishing place.
//   * Bracket: every surviving bot (top cut, default 8) in one match; last
//     place is knocked out, until two remain. Only "not last" matters
//     (third place = surviving down to the 3-bot match).
//   * Final: two bots, three games, highest total score wins: maximise the
//     score margin over one opponent.
// The match's stage comes from the public tournament_match table; the
// number of bots from match_participant. Both are read per match.

import type { Params } from "./engine.js";

export type Stage = "casual" | "swiss" | "bracket" | "final";

// Param overrides per stage, on top of params.json. Only changes that won
// in sim at that stage's table size belong here (see the commit log).
export const STAGE_PARAMS: Record<Stage, Partial<Params>> = {
  casual: {},
  // seedFloor (Vickrey only): bid at least ~market on every letter, so
  // opponents can't collect letters we skip for 1 coin. Sim, 64 matches
  // vs scout models of the top live bots at 4 bots, wins / mean place:
  // floor 0: 56% / 1.55, 0.7: 70% / 1.41, 1.0: 78% / 1.33.
  swiss: { seedFloor: 0.7 },
  // Same idea; at 5 bots 0.7 lifts wins 65% -> 77%, at 8 bots last place
  // stays 0% either way. 3-bot tables get 1.0 (see sizeParams).
  bracket: { seedFloor: 0.7 },
  // Heads-up, our value-only bidding let the opponent take most letters
  // for 1 coin (sim vs scout models of Doppio/Nijello/postturing/Karthik:
  // 2-6% wins). A floor bid at the pre-match price on every letter: 60-69%.
  final: { seedFloor: 1 },
};

// Table-size rules, under the stage's own params: the fewer the bots, the
// more letters go cheap to whoever bids (3 bots, sim: last place 17% with
// no floor, 13% at 0.7, 9% at 1.0).
function sizeParams(stage: Stage, players: number): Partial<Params> {
  if (stage !== "casual" && players > 0 && players <= 3) return { seedFloor: 1 };
  return {};
}

export function stageOf(phaseTag: string | undefined): Stage {
  switch (phaseTag) {
    case "Swiss":
      return "swiss";
    case "Bracket":
      return "bracket";
    case "Final":
      return "final";
    default:
      return "casual";
  }
}

// params.json may carry {"stages": {"final": {...}}}: per-stage overrides
// that win over STAGE_PARAMS, so a stage can be retuned without a restart.
export function stageParams(stage: Stage, players: number, fromFile?: Partial<Record<Stage, Partial<Params>>>): Partial<Params> {
  return { ...STAGE_PARAMS[stage], ...sizeParams(stage, players), ...(fromFile?.[stage] ?? {}) };
}
