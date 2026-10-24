// Team play. Allied bots in the same match don't bid against each other:
// under Vickrey, an ally who loses to a teammate still sets the price the
// teammate pays. Before each auction every ally computes the same
// public-information value of the letter for every ally (tiles won minus
// tiles played, public balances, a seed taken from the auction id), so all
// of them agree on one designated bidder without talking. The others sit
// out unless their private rack (hidden starting tiles included) makes the
// letter worth far more to them than the shared estimate says it is worth
// to the designated ally. With no ally in the match, nothing changes.

import { loadDict, type Dict } from "./dict.js";
import {
  Brain,
  REF_PRICE,
  bestPartition,
  emptyCounts,
  rng,
  sampleFutures,
  unseenPool,
  valueRack,
  type Counts,
  type Params,
} from "./engine.js";
import type { Player, View } from "./sim.js";

// Frozen copy for the shared valuation: allies must compute identical
// numbers, so this must not follow DEFAULT_PARAMS tuning.
const SHARED_PARAMS: Params = {
  samples: 16,
  maxMissing: 4,
  horizonCap: 40,
  minTargetLen: 4,
  lam0: 0.4,
  lamBeta: 1.0,
  lamGamma: 0.6,
  lamMin: 0.02,
  playEps: 0.5,
  firstPriceShade: 0.75,
  bidCapFrac: 1.0,
  paceEta: 0,
  lamMax: 3,
  availProb: 0.3,
  lamMarket: 0,
  denyW: 0,
  endgameN: 12,
  flushAt: 0,
};
const SHARED_LAMBDA = 0.4;
const TIE_EPS = 0.5; // points: values this close count as a tie

export interface TeamConfig {
  // Sit out unless my private value > overrideK × the designated ally's
  // shared value + overrideMin (points).
  overrideK: number;
  overrideMin: number;
  // Sim only: allies see each other's true racks (as if they had shared
  // their starting tiles).
  oracle?: boolean;
  // Captain mode: allies play support for this mate id (bot id live,
  // seat in sim). They give up every letter the captain has a use for
  // (shared value >= captainMin points) and bid `drain` × market price on
  // the rest, so opponents pay more and run out of money sooner.
  captain?: number;
  captainMin?: number;
  drain?: number;
}
// drain 0.8: best captain result in sim (108/match vs 76 solo; 1.2 drained
// the field harder but the captain scored less).
export const DEFAULT_TEAM: TeamConfig = { overrideK: 2, overrideMin: 4, captainMin: 0.5, drain: 0.8 };

// What every ally can see of one ally.
export interface Mate {
  id: number; // bot id (live) or seat (sim): sort key shared by all allies
  known: Counts; // tiles won minus tiles played
  balance: number;
}

// Shared value (points) of `letter` to each mate. `pool` = letters not yet
// revealed, current excluded, from public information only.
export function sharedValues(d: Dict, letter: number, mates: Mate[], pool: Counts, bagLeft: number, key: number): number[] {
  if (bagLeft <= 0) return mates.map(() => 0);
  const m = { price: REF_PRICE, lambda: SHARED_LAMBDA };
  const h = Math.min(SHARED_PARAMS.horizonCap, Math.max(0, bagLeft - 1));
  const futures = sampleFutures(pool, h, SHARED_PARAMS.samples, rng(key * 2654435761), SHARED_PARAMS.availProb);
  return mates.map((x) => {
    const { v0, v1 } = valueRack(d, x.known, futures, m, SHARED_PARAMS, letter);
    let v = v1 - v0;
    if (bagLeft <= SHARED_PARAMS.endgameN) {
      const w = Int16Array.from(x.known);
      w[letter]++;
      v = Math.max(v, bestPartition(d, w, 25).total - bestPartition(d, x.known, 25).total);
    }
    return Math.max(0, v);
  });
}

// Index (into `mates`, which must be sorted by id) of the ally who bids.
export function pickBidder(letter: number, mates: Mate[], values: number[], key: number): number {
  // An ally too poor to win this letter shouldn't hold it for the team.
  const floor = REF_PRICE[letter]! * 0.5;
  let ok = mates.map((x, i) => i).filter((i) => mates[i]!.balance >= floor);
  if (!ok.length) ok = mates.map((_x, i) => i);
  const best = Math.max(...ok.map((i) => values[i]!));
  const tied = ok.filter((i) => values[i]! >= best - TIE_EPS);
  return tied[Math.floor(rng(key * 40503 + 17)() * tied.length)]!;
}

export interface TeamDecision {
  bidder: number; // mate id
  shared: number[]; // per mate, same order as the sorted mates
  sitOut: boolean;
}

// Should `me` bid, given my private bid? Returns the bid to send.
export function teamBid(
  d: Dict,
  me: number,
  myBid: number,
  myLambda: number,
  letter: number,
  mates: Mate[],
  pool: Counts,
  bagLeft: number,
  key: number,
  cfg: TeamConfig = DEFAULT_TEAM,
  price = REF_PRICE[letter]!, // our market estimate for this letter
  balance = Infinity,
): { bid: number; decision?: TeamDecision } {
  if (mates.length < 2) return { bid: myBid };
  const cap = cfg.captain !== undefined ? mates.find((x) => x.id === cfg.captain) : undefined;
  if (cap) {
    if (me === cap.id) return { bid: myBid };
    const [capV] = sharedValues(d, letter, [cap], pool, bagLeft, key);
    if (capV! >= (cfg.captainMin ?? 0.5)) return { bid: 0, decision: { bidder: cap.id, shared: [capV!], sitOut: true } };
    const drainBid = bagLeft > 0 ? Math.floor((cfg.drain ?? 0) * price) : 0;
    return { bid: Math.min(Math.floor(balance), Math.max(myBid, drainBid)), decision: { bidder: me, shared: [capV!], sitOut: false } };
  }
  if (myBid <= 0) return { bid: myBid };
  const sorted = mates.slice().sort((a, b) => a.id - b.id);
  const shared = sharedValues(d, letter, sorted, pool, bagLeft, key);
  const b = pickBidder(letter, sorted, shared, key);
  const bidder = sorted[b]!.id;
  if (bidder === me) return { bid: myBid, decision: { bidder, shared, sitOut: false } };
  const mine = myBid * myLambda; // back to points
  const sitOut = !(mine > cfg.overrideK * shared[b]! + cfg.overrideMin);
  return { bid: sitOut ? 0 : myBid, decision: { bidder, shared, sitOut } };
}

// Shared pool: the bag minus every letter revealed (current included) and
// every hidden starting tile that word plays have exposed.
export function publicPool(auctioned: Counts, revealed: Counts): Counts {
  return unseenPool(auctioned, emptyCounts(), revealed);
}

// ---------- Simulator seat ----------

// Our brain plus team play with the seats in `allies` (which include its
// own seat). Needs the sim's View.seat/seats/self/auction.
export function allyBot(name: string, p: Partial<Params>, seed: number, allies: Set<number>, team: Partial<TeamConfig> = {}): Player {
  const d = loadDict();
  const cfg = { ...DEFAULT_TEAM, ...team };
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
    bid(v: View) {
      const myBid = brain.decideBid({
        letter: v.letter,
        rack: v.rack,
        balance: v.balance,
        pool: unseenPool(v.auctioned, myStart, v.revealed),
        bagLeft: v.bagLeft,
        firstPrice: v.firstPrice,
        opps: v.opps,
      });
      const tiles = (seat: number, known: Counts) => (cfg.oracle ? Int16Array.from(v.racks![seat]!) : known);
      const mates: Mate[] = [{ id: v.seat!, known: tiles(v.seat!, v.self!.known), balance: v.self!.balance }];
      v.opps.forEach((o, j) => {
        if (allies.has(v.seats![j]!)) mates.push({ id: v.seats![j]!, known: tiles(v.seats![j]!, o.known), balance: o.balance });
      });
      // The pool must be common to allies: subtract every exposed tile,
      // including our own exposed ones, like the live client does.
      const rev = Int16Array.from(v.revealed);
      const own = v.selfRevealed!;
      for (let c = 0; c < 26; c++) rev[c] += own[c]!;
      const r = teamBid(d, v.seat!, myBid, brain.lambda(v.balance, v.bagLeft), v.letter, mates, publicPool(v.auctioned, rev), v.bagLeft, v.auction! + v.matchKey!, cfg, brain.price[v.letter]!, v.balance);
      return r.bid;
    },
    plays(v, final) {
      const key = v.rack.join(",");
      if (!final && key === lastRackKey) return [];
      lastRackKey = key;
      return brain.choosePlays(v.rack, unseenPool(v.auctioned, myStart, v.revealed), v.bagLeft, v.balance, final);
    },
    observe(letter, paid, iWon) {
      brain.observePrice(letter, paid);
      brain.observeSpend(iWon ? paid : 0);
    },
  };
}
