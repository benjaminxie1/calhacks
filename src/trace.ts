import { loadDict, chr } from "./dict.js";
import { rng } from "./engine.js";
import { brainBot, playMatch, sharkModel, type Player } from "./sim.js";
const d = loadDict(); const r = rng(5);
const wrap = (p: Player): Player => ({
  ...p,
  start(rack, n) { console.log(`${p.name} start`, [...rack].map((k, c) => chr(c).repeat(k)).join("")); p.start(rack, n); },
  bid(v) { const b = p.bid(v); if (b > 0) console.log(`  ${p.name} bag${v.bagLeft} bal${v.balance} bid ${b} on ${chr(v.letter)} rack=${[...v.rack].map((k, c) => chr(c).repeat(k)).join("")}`); return b; },
  plays(v, f) { const w = p.plays(v, f); if (w.length) console.log(`  ${p.name} PLAYS ${w.join(" ")} ${f ? "(final)" : ""}`); return w; },
  observe(l, paid, won) { if (won) console.log(`  ${p.name} WON ${chr(l)} paid ${paid}`); p.observe(l, paid, won); },
});
const ps = [wrap(brainBot("brain", JSON.parse(process.env.PARAMS ?? "{}"), 1)), wrap(sharkModel(d, r)), sharkModel(d, r), sharkModel(d, r), sharkModel(d, r), sharkModel(d, r)];
console.log(playMatch(ps, Number(process.argv[2] ?? 3)));
