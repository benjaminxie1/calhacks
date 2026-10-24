// Seat kinds shared by run-sim and bench.
import { loadDict } from "./dict.js";
import { rng } from "./engine.js";
import { brainBot, fieldModel, sharkModel, simBot, starterBot, type Player } from "./sim.js";

export function makePlayers(kinds: string[], seed0: number): Player[] {
  const d = loadDict();
  const r = rng(seed0 * 7919);
  const extra = process.env.PARAMS ? JSON.parse(process.env.PARAMS) : {};
  const extraB = process.env.PARAMS_B ? JSON.parse(process.env.PARAMS_B) : {};
  return kinds.map((kind, i) => {
    // brainL0.1 = a brain with fixed λ0 = 0.1
    if (kind.startsWith("brainL")) return brainBot(kind + i, { ...extraB, lam0: Number(kind.slice(6)) }, seed0 * 3000 + i);
    if (kind.startsWith("shark") && kind.length > 5) return sharkModel(d, r, Number(kind.slice(5)) / 10);
    switch (kind) {
      case "brain": return brainBot(`brain${i}`, extra, seed0 * 1000 + i);
      case "brainB": return brainBot(`brainB${i}`, extraB, seed0 * 2000 + i);
      case "shark": return sharkModel(d, r, 1);
      case "field": return fieldModel(d, r);
      case "starter": return starterBot(d);
      case "cheap": return simBot("Cheapskate", r);
      case "value": return simBot("ValueBidder", r);
      case "aggr": return simBot("Aggressive", r);
      default: throw new Error(kind);
    }
  });
}
