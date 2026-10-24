// Learned letter values from the live history: what a won tile turned out
// to be worth, against what it cost.
//
// Each tile a bot won is credited with its share of the first later word of
// that bot that used it (the word's reward split by letter value), or 0 if
// no later word did. Starting tiles aren't known, so some uses that really
// came from a starting tile get credited to a won one; that inflates common
// letters a little, and the table is clipped to [0.5, 2].
//
// corr[letter][third of the bag] = (credited value / price paid), divided by
// the same ratio over all wins. The brain scales its letter value by
// corr^valueCorr (engine.ts), so 0 turns it off.
//
//   npx tsx flywheel/valuecorr.ts [history dir]   # writes flywheel/valuecorr.json

import * as fs from "node:fs";
import { LETTER_VALUE } from "../src/dict.js";
import { loadHistory, type History } from "./fetch.js";

// split: "face" credits each tile its share of the word by letter value;
// "equal" gives every tile the same share (each one is needed for the length).
export function fitValueCorr(h: History, split: "face" | "equal" = "face", window = 400): { corr: number[][]; n: number[][] } {
  const sim = new Set(h.bots.filter((b) => b.isSim).map((b) => b.id));
  const seats = new Map<number, number[]>();
  for (const p of h.participants) {
    if (!seats.has(p.matchId)) seats.set(p.matchId, []);
    seats.get(p.matchId)!.push(p.botId);
  }
  const ms = h.matches
    .filter((m) => m.ended && !m.firstPrice && seats.get(m.id)?.length === 6 && seats.get(m.id)!.every((b) => !sim.has(b)))
    .sort((a, b) => b.id - a.id)
    .slice(0, window);
  const ids = new Set(ms.map((m) => m.id));
  const auctions = new Map<number, History["auctions"]>();
  for (const a of h.auctions) if (ids.has(a.matchId)) (auctions.get(a.matchId) ?? auctions.set(a.matchId, []).get(a.matchId)!).push(a);
  const words = new Map<number, History["words"]>();
  for (const w of h.words) if (ids.has(w.matchId)) (words.get(w.matchId) ?? words.set(w.matchId, []).get(w.matchId)!).push(w);

  const val = Array.from({ length: 26 }, () => [0, 0, 0]);
  const paid = Array.from({ length: 26 }, () => [0, 0, 0]);
  const n = Array.from({ length: 26 }, () => [0, 0, 0]);
  for (const m of ms) {
    const as = (auctions.get(m.id) ?? []).sort((a, b) => a.auctionId - b.auctionId);
    const ws = (words.get(m.id) ?? []).sort((a, b) => a.playedAt - b.playedAt);
    // Uses still unclaimed, per bot: [playedAt, letter, credited value].
    const uses = new Map<number, [number, number, number][]>();
    for (const w of ws) {
      let base = 0;
      for (const ch of w.word) base += LETTER_VALUE[ch.charCodeAt(0) - 65]!;
      const list = uses.get(w.botId) ?? uses.set(w.botId, []).get(w.botId)!;
      for (const ch of w.word) {
        const c = ch.charCodeAt(0) - 65;
        list.push([w.playedAt, c, split === "equal" ? w.reward / w.word.length : base > 0 ? (w.reward * LETTER_VALUE[c]!) / base : 0]);
      }
    }
    as.forEach((a, i) => {
      if (a.winner === null) return;
      const s = Math.min(2, Math.floor((3 * i) / as.length));
      const list = uses.get(a.winner) ?? [];
      const k = list.findIndex((u) => u[1] === a.letter && u[0] >= a.closedAt);
      let v = 0;
      if (k >= 0) {
        v = list[k]![2];
        list.splice(k, 1);
      }
      val[a.letter]![s] += v;
      paid[a.letter]![s] += Math.max(1, a.paid);
      n[a.letter]![s]++;
    });
  }
  const totV = val.flat().reduce((x, y) => x + y, 0);
  const totP = paid.flat().reduce((x, y) => x + y, 0);
  const overall = totV / totP;
  // Shrink thin cells toward 1 so a handful of wins can't swing a letter.
  const corr = val.map((row, c) =>
    row.map((v, s) => {
      const k = n[c]![s]!;
      const raw = paid[c]![s]! > 0 ? v / paid[c]![s]! / overall : 1;
      const w = k / (k + 30);
      return Math.round(Math.max(0.5, Math.min(2, w * raw + (1 - w))) * 1000) / 1000;
    }),
  );
  return { corr, n };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const dir = process.argv[2] ?? new URL("./data", import.meta.url).pathname;
  const h = loadHistory(dir);
  const face = fitValueCorr(h, "face");
  const equal = fitValueCorr(h, "equal");
  fs.writeFileSync(new URL("./valuecorr.json", import.meta.url), JSON.stringify({ face: face.corr, equal: equal.corr }) + "\n");
  const L = (c: number) => String.fromCharCode(65 + c);
  console.log("letter: early/mid/late, face split | equal split");
  console.log(face.corr.map((r, c) => `${L(c)} ${r.map((x) => x.toFixed(2)).join("/")} | ${equal.corr[c]!.map((x) => x.toFixed(2)).join("/")}`).join("\n"));
}
