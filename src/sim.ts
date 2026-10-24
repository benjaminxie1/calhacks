// Offline replica of the server's match loop (spacetimedb/src/game.ts):
// 98-tile bag, 5-tile starting racks, one auction per letter, Vickrey or
// first-price, ties broken at random, the module's sim bots exactly as the
// server plays them, and words playable between auctions. Used to tune
// the strategy against the bots actually on the leaderboard.

import { BAG, LETTER_VALUE, loadDict, type Dict } from "./dict.js";
import {
  Brain,
  bestPartition,
  countsOf,
  emptyCounts,
  rng,
  spellable,
  total,
  unseenPool,
  type Counts,
  type Params,
} from "./engine.js";

export interface View {
  rack: Counts;
  balance: number;
  letter: number;
  bagLeft: number; // after drawing the current letter
  firstPrice: boolean;
  auctioned: Counts; // every letter revealed so far, current included
}

export interface Player {
  name: string;
  isSim?: boolean;
  start(rack: Counts, nPlayers: number): void;
  bid(v: View): number;
  // Called after every auction (and at the final auction's open with
  // final = true). Returns words to play.
  plays(v: View, final: boolean): string[];
  observe(letter: number, paid: number, iWon: boolean): void;
}

// ---------- Opponent models ----------

// The module's own sim bots (game.ts decideBid / simulateWordPlay).
export function simBot(kind: "Cheapskate" | "ValueBidder" | "Aggressive", r: () => number): Player {
  return {
    name: kind,
    isSim: true,
    start() {},
    bid(v) {
      const val = LETTER_VALUE[v.letter]!;
      const vowel = [0, 4, 8, 14, 20].includes(v.letter);
      const base = kind === "Cheapskate" ? val - 1 : kind === "ValueBidder" ? val : val + (vowel ? 4 : 2);
      const jitter = Math.floor(r() * 5) - 2;
      return Math.max(Math.min(Math.max(base + jitter, 1), v.balance), 0);
    },
    plays() {
      return []; // handled by the sim loop right after a win, like the server
    },
    observe() {},
  };
}

// bot-starter's default strategy: face value (+2 if new), plays the
// longest word it can spell whenever its rack changes.
export function starterBot(d: Dict): Player {
  return {
    name: "Starter",
    start() {},
    bid(v) {
      const val = LETTER_VALUE[v.letter]!;
      const want = v.rack[v.letter] === 0 ? val + 2 : Math.max(1, val);
      return Math.min(want, Math.max(0, v.balance));
    },
    plays(v) {
      const w = spellable(d, v.rack, 2).sort((a, b) => d.len[b]! - d.len[a]!)[0];
      return w === undefined ? [] : [d.words[w]!];
    },
    observe() {},
  };
}

// A model of the strong live bots (LetterShark/code/Yezi): bids a per-letter
// level scaled by noise, plays any 7+ letter word, dumps at the end.
// Levels are ~85% of what those bots bid when they won, from the live data.
const SHARK_BID = [
  14, 15, 19, 13, 14, 17, 12, 19, 14, 30, 21, 11, 16, 11, 14, 17, 33, 11, 12, 11,
  15, 15, 17, 32, 21, 38,
];
export function sharkModel(d: Dict, r: () => number, scale = 1): Player {
  return {
    name: `Shark${scale}`,
    start() {},
    bid(v) {
      if (v.bagLeft <= 0) return 0;
      const b = SHARK_BID[v.letter]! * scale * (0.6 + 0.7 * r());
      return Math.max(0, Math.min(Math.floor(b), v.balance));
    },
    plays(v, final) {
      if (final) return bestPartition(d, v.rack, 60).words;
      const ws = spellable(d, v.rack, 7).sort((a, b) => d.reward[b]! - d.reward[a]!);
      return ws.length ? [d.words[ws[0]!]!] : [];
    },
    observe() {},
  };
}

// A model of the live field as of 2026-10-24 (~20 real bots, 6 per match).
// Recent live matches (last 150) clear at about these prices; the top bid
// runs ~1.4x the price paid:
export const LIVE_PAID = [
  10, 15, 15, 13, 11, 18, 12, 19, 10, 25, 21, 10, 16, 10, 10, 16, 23, 11, 12, 10,
  9, 17, 17, 27, 19, 32,
];
// Each field bot has its own aggression, bids a noisy per-letter level,
// bids up letters that complete a 5+ letter word, paces its budget, and
// plays its best 5+ letter word as soon as it can (dumping at the end).
const FIELD_BID = [
  10, 15, 15, 12, 11, 17, 11, 18, 10, 25, 21, 10, 15, 10, 10, 15, 26, 10, 11, 9,
  10, 16, 16, 27, 18, 33,
];
export function fieldModel(d: Dict, r: () => number, level = 1): Player {
  let aggr = 1;
  return {
    name: "Field",
    start() {
      aggr = level * (0.75 + 0.5 * r());
    },
    bid(v) {
      if (v.bagLeft <= 0) return 0;
      let b = FIELD_BID[v.letter]! * aggr * (0.6 + 0.7 * r());
      const w = Int16Array.from(v.rack);
      w[v.letter]++;
      if (spellable(d, w, 5).length > spellable(d, v.rack, 5).length) b *= 1.4;
      // Spend roughly evenly: a coin is worth more when money runs short.
      const perAuction = v.balance / Math.max(1, v.bagLeft + 1);
      b *= Math.min(1.3, Math.max(0.5, perAuction / 1.6));
      return Math.max(0, Math.min(Math.floor(b), v.balance));
    },
    plays(v, final) {
      if (final) return bestPartition(d, v.rack, 60).words;
      const minLen = v.bagLeft > 20 ? 6 : 5;
      const ws = spellable(d, v.rack, minLen).sort((a, b) => d.reward[b]! - d.reward[a]!);
      return ws.length ? [d.words[ws[0]!]!] : [];
    },
    observe() {},
  };
}

// Our bot.
export function brainBot(name: string, p: Partial<Params>, seed: number): Player {
  let brain: Brain;
  let myStart: Counts = emptyCounts();
  let lastRackKey = "";
  return {
    name,
    start(rack) {
      brain = new Brain(p, seed++);
      myStart = Int16Array.from(rack);
      lastRackKey = "";
    },
    bid(v) {
      return brain.decideBid({
        letter: v.letter,
        rack: v.rack,
        balance: v.balance,
        pool: unseenPool(v.auctioned, myStart),
        bagLeft: v.bagLeft,
        firstPrice: v.firstPrice,
      });
    },
    plays(v, final) {
      const key = v.rack.join(",");
      if (!final && key === lastRackKey) return [];
      lastRackKey = key;
      return brain.choosePlays(v.rack, unseenPool(v.auctioned, myStart), v.bagLeft, v.balance, final);
    },
    observe(letter, paid, iWon) {
      brain.observePrice(letter, paid);
      brain.observeSpend(iWon ? paid : 0);
    },
  };
}

// ---------- The match loop ----------

export interface Result {
  scores: number[];
  balances: number[];
  // Per auction: letter, price paid, top bid, winner seat (-1 = none).
  log: { letter: number; paid: number; top: number; winner: number }[];
}

export function playMatch(players: Player[], seed: number, firstPrice = false, rackSize = 5): Result {
  const d = loadDict();
  const r = rng(seed);
  const bag = Int16Array.from(BAG);
  let bagTotal = total(bag);
  const draw = (): number | undefined => {
    if (bagTotal === 0) return undefined;
    let k = Math.floor(r() * bagTotal);
    for (let c = 0; c < 26; c++) {
      if (k < bag[c]!) {
        bag[c]--;
        bagTotal--;
        return c;
      }
      k -= bag[c]!;
    }
    return undefined;
  };
  const n = players.length;
  const racks = players.map(() => emptyCounts());
  const bal = players.map(() => 100);
  const score = players.map(() => 0);
  for (let i = 0; i < n; i++) for (let k = 0; k < rackSize; k++) racks[i]![draw()!]++;
  players.forEach((p, i) => p.start(Int16Array.from(racks[i]!), n));
  const auctioned = emptyCounts();
  const log: Result["log"] = [];

  const play = (i: number, word: string) => {
    if (!d.lexSet.has(word)) return;
    const need = countsOf(word);
    for (let c = 0; c < 26; c++) if (need[c]! > racks[i]![c]!) return;
    for (let c = 0; c < 26; c++) racks[i]![c] -= need[c]!;
    let base = 0;
    for (const ch of word) base += LETTER_VALUE[ch.charCodeAt(0) - 65]!;
    const L = word.length;
    const rw = L <= 3 ? base : L === 4 ? Math.floor((base * 3) / 2) : L === 5 ? base * 2 : L === 6 ? Math.floor((base * 5) / 2) : base * 3;
    bal[i] += rw;
    score[i] += rw;
  };
  const view = (i: number, letter: number): View => ({
    rack: Int16Array.from(racks[i]!),
    balance: bal[i]!,
    letter,
    bagLeft: bagTotal,
    firstPrice,
    auctioned,
  });
  const realPlays = (letter: number, final: boolean) => {
    players.forEach((p, i) => {
      if (p.isSim) return;
      for (const w of p.plays(view(i, letter), final)) play(i, w);
    });
  };

  let letter = draw();
  while (letter !== undefined) {
    auctioned[letter]++;
    if (bagTotal === 0) realPlays(letter, true);
    const bids = players.map((p, i) => ({ i, a: Math.max(0, Math.min(Math.floor(p.bid(view(i, letter!))), bal[i]!)), t: r() }));
    const live = bids.filter((b) => b.a > 0).sort((x, y) => y.a - x.a || x.t - y.t);
    let winner = -1;
    let paid = 0;
    if (live.length) {
      winner = live[0]!.i;
      paid = firstPrice ? live[0]!.a : live[1] ? live[1].a : Math.min(1, live[0]!.a);
      if (bal[winner]! >= paid) {
        bal[winner] -= paid;
        racks[winner]![letter]++;
      } else winner = -1;
    }
    log.push({ letter: letter!, paid: winner >= 0 ? paid : 0, top: live[0]?.a ?? 0, winner });
    players.forEach((p, i) => p.observe(letter!, paid, i === winner));
    if (winner >= 0 && players[winner]!.isSim) {
      // findBestPlayable(rack, 3): the longest word of 3+ letters.
      const w = spellable(d, racks[winner]!, 3).sort((a, b) => d.len[b]! - d.len[a]! || (d.words[a]! < d.words[b]! ? -1 : 1))[0];
      if (w !== undefined) play(winner, d.words[w]!);
    }
    letter = draw();
    if (letter !== undefined && bagTotal > 0) realPlays(letter, false);
  }
  return { scores: score, balances: bal, log };
}
