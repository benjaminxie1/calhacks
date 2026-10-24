// Summaries of the live market from the public history: what each letter
// sells for, how prices move through a match, how much bots score, and how
// our own bot is doing. These drive the seed prices and the field model.

import type { History } from "./fetch.js";

export interface Observed {
  matches: number; // matches in the window
  since: string;
  paidByLetter: number[]; // mean price paid (runner-up bid) per letter
  topByLetter: number[]; // mean winning bid per letter
  paidByStage: number[]; // thirds of the auction sequence
  noSaleLate: number;
  meanScore: number; // average score per seat
  winScore: number; // average winning score
  bots: { name: string; n: number; score: number; rank: number }[];
}

// The last `window` finished matches with a standard setup (6 seats,
// 5-tile racks, 1 s Vickrey auctions).
export function observe(h: History, window = 150): Observed {
  const name = new Map(h.bots.map((b) => [b.id, b.name]));
  const seats = new Map<number, typeof h.participants>();
  for (const p of h.participants) {
    if (!seats.has(p.matchId)) seats.set(p.matchId, []);
    seats.get(p.matchId)!.push(p);
  }
  const ms = h.matches
    .filter((m) => m.ended && !m.firstPrice && m.rackSize === 5 && seats.get(m.id)?.length === 6)
    .sort((a, b) => a.id - b.id)
    .slice(-window);
  const ids = new Set(ms.map((m) => m.id));
  const byMatch = new Map<number, typeof h.auctions>();
  for (const a of h.auctions) {
    if (!ids.has(a.matchId)) continue;
    if (!byMatch.has(a.matchId)) byMatch.set(a.matchId, []);
    byMatch.get(a.matchId)!.push(a);
  }
  const sumL = new Array(26).fill(0), topL = new Array(26).fill(0), nL = new Array(26).fill(0);
  const sumS = [0, 0, 0], nS = [0, 0, 0];
  let none = 0, late = 0;
  for (const arr of byMatch.values()) {
    arr.sort((a, b) => a.auctionId - b.auctionId);
    arr.forEach((a, i) => {
      const s = Math.min(2, Math.floor((3 * i) / arr.length));
      if (s === 2) late++;
      if (a.winner === null) {
        if (s === 2) none++;
        return;
      }
      sumL[a.letter] += a.paid; nL[a.letter]++;
      topL[a.letter] += h.topBid.get(a.auctionId) ?? a.paid;
      sumS[s] += a.paid; nS[s]++;
    });
  }
  let score = 0, nSeat = 0, win = 0;
  const agg = new Map<number, { s: number; r: number; n: number }>();
  for (const m of ms) {
    const ps = seats.get(m.id)!;
    win += Math.max(...ps.map((p) => p.score));
    for (const p of ps) {
      score += p.score; nSeat++;
      const rank = 1 + ps.filter((q) => q.score > p.score).length;
      const a = agg.get(p.botId) ?? { s: 0, r: 0, n: 0 };
      a.s += p.score; a.r += rank; a.n++;
      agg.set(p.botId, a);
    }
  }
  const r1 = (x: number) => Math.round(x * 10) / 10;
  return {
    matches: ms.length,
    since: ms.length ? new Date(ms[0]!.startedAt).toISOString() : "",
    paidByLetter: sumL.map((s, c) => r1(nL[c] ? s / nL[c] : 0)),
    topByLetter: topL.map((s, c) => r1(nL[c] ? s / nL[c] : 0)),
    paidByStage: sumS.map((s, i) => r1(nS[i] ? s / nS[i] : 0)),
    noSaleLate: r1((100 * none) / Math.max(1, late)) / 100,
    meanScore: r1(score / Math.max(1, nSeat)),
    winScore: r1(win / Math.max(1, ms.length)),
    bots: [...agg]
      .map(([id, a]) => ({ name: name.get(id) ?? `#${id}`, n: a.n, score: r1(a.s / a.n), rank: Math.round((100 * a.r) / a.n) / 100 }))
      .sort((a, b) => a.rank - b.rank),
  };
}

// Seed prices for the engine: observed mean price per letter, shrunk
// towards the previous seed when a letter has little data. Letters that
// never sold keep the previous value.
export function fitSeedPrice(o: Observed, prev: number[], h: History): number[] {
  const n = new Array(26).fill(0);
  const ids = new Set(h.matches.slice(-o.matches).map((m) => m.id));
  for (const a of h.auctions) if (a.winner !== null && ids.has(a.matchId)) n[a.letter]++;
  return o.paidByLetter.map((p, c) => {
    if (!p) return prev[c]!;
    const w = n[c] / (n[c] + 20);
    return Math.max(1, Math.round((w * p + (1 - w) * prev[c]!) * 10) / 10);
  });
}
