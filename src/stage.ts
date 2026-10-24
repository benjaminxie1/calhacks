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
  swiss: {},
  bracket: {},
  final: {},
};

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
export function stageParams(stage: Stage, fromFile?: Partial<Record<Stage, Partial<Params>>>): Partial<Params> {
  return { ...STAGE_PARAMS[stage], ...(fromFile?.[stage] ?? {}) };
}
