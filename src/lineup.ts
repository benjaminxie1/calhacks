// Seat kinds shared by run-sim and bench.
import { loadDict } from "./dict.js";
import { rng } from "./engine.js";
import { allyBot } from "./team.js";
import { brainBot, fieldModel, liveModel, proModel, scoutModel, sharkModel, simBot, starterBot, type Player } from "./sim.js";

export function makePlayers(kinds: string[], seed0: number): Player[] {
  const d = loadDict();
  const r = rng(seed0 * 7919);
  const extra = process.env.PARAMS ? JSON.parse(process.env.PARAMS) : {};
  const extraB = process.env.PARAMS_B ? JSON.parse(process.env.PARAMS_B) : {};
  const allies = new Set(kinds.flatMap((k, i) => (k === "ally" ? [i] : [])));
  const team = process.env.TEAM ? JSON.parse(process.env.TEAM) : undefined;
  return kinds.map((kind, i) => {
    // brainL0.1 = a brain with fixed λ0 = 0.1
    if (kind.startsWith("brainL")) return brainBot(kind + i, { ...extraB, lam0: Number(kind.slice(6)) }, seed0 * 3000 + i);
    // field12 = a field bot 1.2x as aggressive
    if (kind.startsWith("field") && kind.length > 5) return fieldModel(d, r, Number(kind.slice(5)) / 10);
    // scout:Doppio = a model of that live bot (data/scout-profiles.json)
    if (kind.startsWith("scout:")) return scoutModel(d, r, kind.slice(6));
    if (kind.startsWith("shark") && kind.length > 5) return sharkModel(d, r, Number(kind.slice(5)) / 10);
    switch (kind) {
      case "ally": return allyBot(`ally${i}`, extra, seed0 * 1000 + i, allies, team);
      case "brain": return brainBot(`brain${i}`, extra, seed0 * 1000 + i);
      case "brainB": return brainBot(`brainB${i}`, extraB, seed0 * 2000 + i);
      case "shark": return sharkModel(d, r, 1);
      case "field": return fieldModel(d, r);
      case "pro": return proModel(d, r);
      case "live": return liveModel(d, r, process.env.LIVE_STAGE ? JSON.parse(process.env.LIVE_STAGE) : undefined, Number(process.env.LIVE_BOOST ?? 1.3), Number(process.env.LIVE_BONUS ?? 80));
      case "starter": return starterBot(d);
      case "cheap": return simBot("Cheapskate", r);
      case "value": return simBot("ValueBidder", r);
      case "aggr": return simBot("Aggressive", r);
      default: throw new Error(kind);
    }
  });
}
