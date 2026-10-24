// The strategy: Monte Carlo valuation of letters against target words.
//
// The game in strategy terms:
//   * Score is the sum of word rewards; money spent on bids is NOT deducted
//     from score. Money only buys letters, and leftover money at the end is
//     worth nothing. So this is a budget-pacing problem for a "value
//     maximiser": bid v/λ where v is a letter's marginal score and λ is the
//     score a coin can still buy (Gummadi-Key-Proutiere / Balseiro-Gur
//     pacing). λ falls to ~0 as the bag empties.
//   * Rewards are superlinear in length (3x at 7+ letters), so a letter's
//     worth is how much it raises the chance of completing a long, valuable
//     word before the bag runs out. We estimate that like Maven/Quackle's
//     simulation: sample the letters the bag will still reveal, and see
//     which target words become completable, net of what the missing
//     letters will cost at market prices.
//   * Under Vickrey (the default) truthful bidding of that value is
//     optimal; under first-price we shade towards the expected runner-up.

import { BAG, LETTER_VALUE, loadDict, type Dict } from "./dict.js";

export type Counts = Int16Array; // 26 slots

export function emptyCounts(): Counts {
  return new Int16Array(26);
}
export function countsOf(s: string): Counts {
  const c = emptyCounts();
  for (const ch of s) c[ch.charCodeAt(0) - 65]++;
  return c;
}
export function total(c: Counts): number {
  let t = 0;
  for (let i = 0; i < 26; i++) t += c[i]!;
  return t;
}

// Market price (coins paid) per letter, seeded from the live server's
// history of real-bot matches (mean paid over the last 150 matches, as of
// 2026-10-24), then tracked online during a match. params.json can
// override it with a fresher "seedPrice".
export const SEED_PRICE = [
  10, 15, 15, 13, 11, 18, 12, 19, 10, 25, 21, 10, 16, 10, 10, 16, 23, 11, 12, 10,
  9, 17, 17, 27, 19, 32,
];
// The price level lam0 was tuned at. When the market runs hotter than
// this, a coin buys less score, so λ falls (see lamMarket).
export const REF_PRICE = SEED_PRICE.slice();

// Bag-weighted mean price: the price of an "average" letter.
export function priceLevel(price: ArrayLike<number>): number {
  let s = 0;
  let n = 0;
  for (let c = 0; c < 26; c++) {
    s += BAG[c]! * price[c]!;
    n += BAG[c]!;
  }
  return s / n;
}

export interface Params {
  samples: number; // Monte Carlo futures per valuation
  maxMissing: number; // ignore targets missing more letters than this
  horizonCap: number; // how many future reveals a plan may rely on
  minTargetLen: number;
  lam0: number; // score per coin at the start of a match
  lamBeta: number; // how λ decays with the share of the bag left
  lamGamma: number; // how λ scales with how rich we are
  lamMin: number;
  playEps: number; // a play must beat holding by this many points
  firstPriceShade: number; // fraction of value bid under first-price
  bidCapFrac: number; // never bid more than this share of balance (mid-game)
  // Adaptive pacing (dual descent on the budget constraint, after
  // Balseiro & Gur): raise λ when we spend faster than balance/auctions
  // left, lower it when we underspend. 0 disables.
  paceEta: number;
  lamMax: number;
  // Share of future letters we can realistically win. A plan can only
  // count on that fraction of the bag, which is what makes a common letter
  // worth buying now rather than "later".
  availProb: number;
  // λ scales with (reference price level / current price level)^lamMarket,
  // so it tracks how hot the field in this match is. 0 disables.
  lamMarket: number;
  // Denial: add denyW × the most points one opponent would gain from this
  // letter right now (from what they publicly hold). 0 disables.
  denyW: number;
  // Endgame: with this many auctions left, value letters by what they add
  // to the best set of words we can play now.
  endgameN: number;
  // Scale every Vickrey bid (live data: benx loses ~16 auctions/match by
  // 2 coins or less). 1 = off.
  bidMult: number;
  // A letter worth at least floorMinV points gets a bid of at least
  // bidFloor × its market price. 0 = off.
  bidFloor: number;
  floorMinV: number;
  // Common letters (face value <= cheapMaxFace) worth cheapMinV+ points
  // get a bid of at least cheapFloor × pre-match price (seedPrice, not the
  // in-match price, which our own lost bids would ratchet up). Live winners bid
  // ~1.2× the clearing price on E/R/S/T/..., so bidFloor 0.9 loses them
  // by ~2 coins; this raises the floor on those letters only. 0 = off.
  cheapFloor: number;
  cheapMaxFace: number;
  cheapMinV: number;
  // Before the last stretch of the bag, cap bids on letters of face value
  // >= rareMinFace at rareCap × market price (live: benx spends its early
  // budget on J/Q/X/Z while top bots buy them late). 0 = off.
  rareCap: number;
  rareMinFace: number;
  rareUntil: number; // the cap applies while bagLeft > rareUntil × auctions
  // Cap every Vickrey bid at mktCap × market price (with bidFloor this
  // imitates the top live bots: win bid ≈ 1.15-1.3× market). 0 = off.
  mktCap: number;
  // Play policy: credit for playing a word now, as a share of λ × reward
  // (the reward is also coins that buy letters in later auctions).
  liqW: number;
  // Rack letters a plan doesn't use still score in the final dump: count
  // them at resW × face value.
  resW: number;
  // Scale resW by (share of the bag already drawn)^resLate: leftovers are
  // only sure to end up in the dump late in the match. 0 = flat.
  resLate: number;
  // Cap on a leftover letter's face value in that credit (Q, Z, J, X
  // rarely fit a dump word).
  resCap: number;
  // When we lose as the runner-up, the price paid is just our own bid (at
  // a 2-bot table always), a lower bound on the letter's price: record it
  // as paid × this instead. 1 = off.
  lostPriceMult: number;
  // Bid at least seedFloor × the letter's pre-match price on every letter
  // (paced by coins per auction left), valued or not. Heads-up, a letter we
  // don't bid on goes to the opponent for 1 coin. 0 = off.
  seedFloor: number;
  // Two-word plans: a rack's value is its best target plus pairW × the
  // best second target that fits beside it in the same future. 0 = off.
  pairW: number;
}

export const DEFAULT_PARAMS: Params = {
  samples: 48,
  maxMissing: 5,
  horizonCap: 60,
  minTargetLen: 4,
  lam0: 0.4,
  lamBeta: 1.0,
  lamGamma: 1.0,
  lamMin: 0.02,
  playEps: 0.5,
  firstPriceShade: 0.75,
  bidCapFrac: 1.0,
  paceEta: 0,
  lamMax: 3,
  availProb: 0.25,
  lamMarket: 0,
  denyW: 0,
  endgameN: 12,
  bidMult: 1,
  bidFloor: 0.9,
  floorMinV: 1,
  cheapFloor: 0,
  cheapMaxFace: 2,
  cheapMinV: 1,
  rareCap: 0,
  rareMinFace: 8,
  rareUntil: 0.33,
  mktCap: 0,
  liqW: 0.1,
  resW: 0,
  resLate: 0,
  resCap: 99,
  lostPriceMult: 1,
  seedFloor: 0,
  pairW: 0,
};

// mulberry32
export function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------- Word search ----------

// Indices of words spellable from `rack` with length >= minLen.
export function spellable(d: Dict, rack: Counts, minLen = 2): number[] {
  const t = total(rack);
  const out: number[] = [];
  const n = d.words.length;
  for (let i = 0; i < n; i++) {
    const L = d.len[i]!;
    if (L > t || L < minLen) continue;
    const s = d.start[i]!;
    const e = s + d.nDistinct[i]!;
    let ok = true;
    for (let k = s; k < e; k++) {
      if (d.letters[2 * k + 1]! > rack[d.letters[2 * k]!]!) {
        ok = false;
        break;
      }
    }
    if (ok) out.push(i);
  }
  return out;
}

// Best set of disjoint words from `rack` (max total reward). Branch and
// bound over the spellable words, capped by time.
export function bestPartition(
  d: Dict,
  rack: Counts,
  budgetMs = 60,
): { total: number; words: string[] } {
  const cand = spellable(d, rack, 2).sort((a, b) => d.reward[b]! - d.reward[a]!);
  const r = Int16Array.from(rack);
  let best = 0;
  let bestSet: number[] = [];
  const cur: number[] = [];
  const deadline = Date.now() + budgetMs;
  let nodes = 0;
  const valueLeft = () => {
    let v = 0;
    for (let c = 0; c < 26; c++) v += r[c]! * LETTER_VALUE[c]!;
    return v;
  };
  const fits = (i: number) => {
    const s = d.start[i]!;
    const e = s + d.nDistinct[i]!;
    for (let k = s; k < e; k++)
      if (d.letters[2 * k + 1]! > r[d.letters[2 * k]!]!) return false;
    return true;
  };
  const apply = (i: number, sign: number) => {
    const s = d.start[i]!;
    const e = s + d.nDistinct[i]!;
    for (let k = s; k < e; k++) r[d.letters[2 * k]!] -= sign * d.letters[2 * k + 1]!;
  };
  const dfs = (from: number, score: number) => {
    if (score > best) {
      best = score;
      bestSet = cur.slice();
    }
    if ((++nodes & 255) === 0 && Date.now() > deadline) return;
    if (score + 3 * valueLeft() <= best) return;
    for (let j = from; j < cand.length; j++) {
      const i = cand[j]!;
      if (!fits(i)) continue;
      apply(i, 1);
      cur.push(i);
      dfs(j, score + d.reward[i]!);
      cur.pop();
      apply(i, -1);
      if (Date.now() > deadline) return;
    }
  };
  dfs(0, 0);
  return { total: best, words: bestSet.map((i) => d.words[i]!) };
}

// Reward of the best single word spellable from `rack` (3+ letters).
export function bestWord(d: Dict, rack: Counts): number {
  let best = 0;
  for (const i of spellable(d, rack, 3)) if (d.reward[i]! > best) best = d.reward[i]!;
  return best;
}

// ---------- Monte Carlo valuation ----------

export interface Market {
  price: number[]; // expected coins to win each letter
  lambda: number; // score per coin
  resW?: number; // weight on rack letters a plan leaves over
}

// Draw `h` letters without replacement from `pool`, `k` times.
export function sampleFutures(
  pool: Counts,
  h: number,
  k: number,
  rand: () => number,
  avail = 1,
): Int16Array[] {
  const bag: number[] = [];
  for (let c = 0; c < 26; c++) for (let n = 0; n < pool[c]!; n++) bag.push(c);
  const take = Math.min(h, bag.length);
  const out: Int16Array[] = [];
  for (let s = 0; s < k; s++) {
    const f = new Int16Array(26);
    for (let i = 0; i < take; i++) {
      const j = i + Math.floor(rand() * (bag.length - i));
      const t = bag[i]!;
      bag[i] = bag[j]!;
      bag[j] = t;
      if (avail >= 1 || rand() < avail) f[bag[i]!]++;
    }
    out.push(f);
  }
  return out;
}

interface Cand {
  i: number;
  cost: number; // λ * price of letters missing from the rack
  used: number; // face value of the rack letters the word uses
}

const resFace = (c: number, cap: number) => Math.min(LETTER_VALUE[c]!, cap);

function candidates(
  d: Dict,
  rack: Counts,
  m: Market,
  maxMissing: number,
  minLen: number,
  resCap = 99,
): Cand[] {
  const out: Cand[] = [];
  const n = d.words.length;
  const t = total(rack);
  for (let i = 0; i < n; i++) {
    const L = d.len[i]!;
    if (L < minLen || L > t + maxMissing) continue;
    const s = d.start[i]!;
    const e = s + d.nDistinct[i]!;
    let miss = 0;
    let cost = 0;
    let used = 0;
    for (let k = s; k < e; k++) {
      const c = d.letters[2 * k]!;
      const want = d.letters[2 * k + 1]!;
      const need = want - rack[c]!;
      used += Math.min(want, rack[c]!) * resFace(c, resCap);
      if (need > 0) {
        miss += need;
        if (miss > maxMissing) break;
        cost += need * m.price[c]!;
      }
    }
    if (miss <= maxMissing) out.push({ i, cost: cost * m.lambda, used });
  }
  return out;
}

// Expected best single-target value of `rack` over the sampled futures,
// plus the same with one extra `extra` letter (if given). Common random
// numbers make the difference low-variance.
export function valueRack(
  d: Dict,
  rack: Counts,
  futures: Int16Array[],
  m: Market,
  p: Params,
  extra = -1,
): { v0: number; v1: number } {
  if (p.pairW > 0) return valueRackPairs(d, rack, futures, m, p, extra);
  const cands = candidates(d, rack, m, p.maxMissing, p.minTargetLen, p.resCap);
  let sum0 = 0;
  let sum1 = 0;
  const lp = extra >= 0 ? m.lambda * m.price[extra]! : 0;
  const rw0 = m.resW ?? p.resW;
  let face = 0;
  for (let c = 0; c < 26; c++) face += rack[c]! * resFace(c, p.resCap);
  const fx = extra >= 0 ? resFace(extra, p.resCap) * rw0 : 0;
  const res0 = rw0 * face;
  for (const f of futures) {
    let b0 = res0;
    let b1 = res0 + fx;
    for (const { i, cost, used } of cands) {
      const rw = d.reward[i]! + rw0 * (face - used);
      if (rw - cost + lp + fx <= b1 && rw - cost <= b0) continue;
      const s = d.start[i]!;
      const e = s + d.nDistinct[i]!;
      let deficit = 0;
      let defLetter = -1;
      let needsExtra = false;
      for (let k = s; k < e; k++) {
        const c = d.letters[2 * k]!;
        const need = d.letters[2 * k + 1]! - rack[c]!;
        if (need <= 0) continue;
        if (c === extra) needsExtra = true;
        const short = need - f[c]!;
        if (short > 0) {
          deficit += short;
          defLetter = c;
          if (deficit > 1) break;
        }
      }
      const v = rw - cost;
      if (deficit === 0) {
        if (v > b0) b0 = v;
        // With the extra letter: it fills a gap (no residual), or sits in
        // the residual.
        const v1 = needsExtra ? v + lp : v + fx;
        if (v1 > b1) b1 = v1;
      } else if (deficit === 1 && defLetter === extra) {
        const v1 = v + lp;
        if (v1 > b1) b1 = v1;
      }
    }
    sum0 += b0;
    sum1 += Math.max(b0, b1);
  }
  const k = futures.length || 1;
  return { v0: sum0 / k, v1: sum1 / k };
}

// valueRack with two-word plans: per future, keep the best few completable
// targets and score the best single word or disjoint pair (the second one
// at pairW).
const PAIR_TOP = 8;
function valueRackPairs(
  d: Dict,
  rack: Counts,
  futures: Int16Array[],
  m: Market,
  p: Params,
  extra: number,
): { v0: number; v1: number } {
  const cands = candidates(d, rack, m, p.maxMissing, p.minTargetLen, p.resCap);
  const lp = extra >= 0 ? m.lambda * m.price[extra]! : 0;
  const top0: { i: number; v: number }[] = [];
  const top1: { i: number; v: number }[] = [];
  const push = (top: { i: number; v: number }[], i: number, v: number) => {
    if (top.length < PAIR_TOP) top.push({ i, v });
    else if (v > top[PAIR_TOP - 1]!.v) top[PAIR_TOP - 1] = { i, v };
    else return;
    top.sort((a, b) => b.v - a.v);
  };
  const floor = (top: { v: number }[]) => (top.length < PAIR_TOP ? 0 : top[PAIR_TOP - 1]!.v);
  const avail = new Int16Array(26);
  const fits = (i: number, sign: number) => {
    const s = d.start[i]!;
    const e = s + d.nDistinct[i]!;
    let ok = true;
    for (let k = s; k < e; k++) {
      const c = d.letters[2 * k]!;
      avail[c] -= sign * d.letters[2 * k + 1]!;
      if (avail[c]! < 0) ok = false;
    }
    return ok;
  };
  const best = (top: { i: number; v: number }[], f: Int16Array, withExtra: boolean) => {
    if (top.length === 0) return 0;
    let b = top[0]!.v;
    for (let a = 0; a < top.length; a++) {
      const ta = top[a]!;
      if (ta.v + p.pairW * ta.v <= b) break;
      for (let c = 0; c < 26; c++) avail[c] = rack[c]! + f[c]!;
      if (withExtra) avail[extra]++;
      fits(ta.i, 1);
      for (let k = a + 1; k < top.length; k++) {
        const tb = top[k]!;
        const v = ta.v + p.pairW * tb.v;
        if (v <= b) break;
        if (fits(tb.i, 1)) b = v;
        fits(tb.i, -1);
      }
    }
    return b;
  };
  let sum0 = 0;
  let sum1 = 0;
  for (const f of futures) {
    top0.length = 0;
    top1.length = 0;
    for (const { i, cost } of cands) {
      const v = d.reward[i]! - cost;
      if (v <= 0 || (v + lp <= floor(top1) && v <= floor(top0))) continue;
      const s = d.start[i]!;
      const e = s + d.nDistinct[i]!;
      let deficit = 0;
      let defLetter = -1;
      let needsExtra = false;
      for (let k = s; k < e; k++) {
        const c = d.letters[2 * k]!;
        const need = d.letters[2 * k + 1]! - rack[c]!;
        if (need <= 0) continue;
        if (c === extra) needsExtra = true;
        const short = need - f[c]!;
        if (short > 0) {
          deficit += short;
          defLetter = c;
          if (deficit > 1) break;
        }
      }
      if (deficit === 0) {
        push(top0, i, v);
        if (extra >= 0) push(top1, i, needsExtra ? v + lp : v);
      } else if (deficit === 1 && defLetter === extra) push(top1, i, v + lp);
    }
    const b0 = best(top0, f, false);
    sum0 += b0;
    sum1 += extra >= 0 ? Math.max(b0, best(top1, f, true)) : b0;
  }
  const k = futures.length || 1;
  return { v0: sum0 / k, v1: sum1 / k };
}

// ---------- The per-match brain ----------

export interface BidInput {
  letter: number;
  rack: Counts;
  balance: number;
  pool: Counts; // letters not yet revealed (current one excluded) and not ours
  bagLeft: number; // letters still in the bag after the current one
  firstPrice: boolean;
  opps?: Opp[]; // public view of each opponent
}

// What everyone can see of an opponent: letters won minus letters played
// (their 5 hidden starting tiles stay unknown), balance and score.
export interface Opp {
  known: Counts;
  balance: number;
  score: number;
}

export class Brain {
  d: Dict;
  p: Params;
  price: number[];
  seen: number[]; // observations per letter for the price EMA
  rand: () => number;
  totalAuctions: number;
  lam: number; // paced λ (when paceEta > 0)
  spentSinceUpdate = 0;
  lastBid = -1; // our last bid, to spot results where we were runner-up
  seedPrice: number[];
  auctionsSinceUpdate = 0;

  // `seedPrice` (26 numbers) replaces SEED_PRICE, e.g. from params.json.
  constructor(p: Partial<Params> & { seedPrice?: number[] } = {}, seed = Date.now() & 0xffffffff) {
    const { seedPrice, ...rest } = p;
    this.d = loadDict();
    this.p = { ...DEFAULT_PARAMS, ...rest };
    this.price = seedPrice?.length === 26 ? seedPrice.slice() : SEED_PRICE.slice();
    this.seedPrice = this.price.slice();
    this.seen = new Array(26).fill(0);
    this.rand = rng(seed);
    this.totalAuctions = 68;
    this.lam = this.p.lam0;
  }

  // Auctions in a match of n bots: the bag less every starting rack
  // (68 at 6 bots, 78 at 4, 88 heads-up). λ paces against this.
  setPlayers(n: number, rackSize = 5) {
    this.totalAuctions = Math.max(1, BAG.reduce((a, b) => a + b, 0) - n * rackSize);
  }

  // Our own auction outcome: what we paid (0 if we lost).
  observeSpend(paid: number) {
    this.spentSinceUpdate += paid;
    this.auctionsSinceUpdate++;
  }

  private pace(balance: number, bagLeft: number) {
    if (this.auctionsSinceUpdate === 0) return;
    const rho = balance / Math.max(1, bagLeft + 1);
    const spent = this.spentSinceUpdate / this.auctionsSinceUpdate;
    const g = (spent - rho) / Math.max(rho, 2);
    this.lam *= Math.exp(this.p.paceEta * Math.max(-2, Math.min(2, g)) * this.auctionsSinceUpdate);
    this.lam = Math.max(this.p.lamMin, Math.min(this.p.lamMax, this.lam));
    this.spentSinceUpdate = 0;
    this.auctionsSinceUpdate = 0;
  }

  // Record what a letter actually sold for (Vickrey `paid` = runner-up).
  observePrice(letter: number, paid: number, won?: boolean) {
    if (won === false && paid > 0 && paid === this.lastBid) paid *= this.p.lostPriceMult;
    const n = ++this.seen[letter]!;
    const w = Math.max(0.15, 1 / (n + 2));
    this.price[letter] = (1 - w) * this.price[letter]! + w * Math.max(paid, 1);
  }

  lambda(balance: number, bagLeft: number): number {
    const p = this.p;
    if (p.paceEta > 0) return this.lam;
    const frac = Math.max(0, bagLeft) / Math.max(1, this.totalAuctions);
    const rich = 100 / Math.max(balance, 5);
    const hot = p.lamMarket ? Math.pow(priceLevel(REF_PRICE) / priceLevel(this.price), p.lamMarket) : 1;
    return Math.max(
      p.lamMin,
      p.lam0 * hot * Math.pow(frac, p.lamBeta) * Math.pow(rich, p.lamGamma),
    );
  }

  market(balance: number, bagLeft: number): Market {
    const drawn = 1 - Math.max(0, bagLeft) / Math.max(1, this.totalAuctions);
    return {
      price: this.price,
      lambda: this.lambda(balance, bagLeft),
      resW: this.p.resW * Math.pow(Math.min(1, Math.max(0, drawn)), this.p.resLate),
    };
  }

  // Letters the remaining bag can still reveal *and* we could still use:
  // the very last letter arrives as the match ends, so it is never usable.
  horizon(bagLeft: number) {
    return Math.min(this.p.horizonCap, Math.max(0, bagLeft - 1));
  }

  letterValue(inp: BidInput): number {
    const { rack, letter } = inp;
    // bagLeft = 0: this is the last auction; its letter arrives as the
    // match ends and can never be played.
    if (inp.bagLeft <= 0) return 0;
    const m = this.market(inp.balance, inp.bagLeft);
    const futures = sampleFutures(
      inp.pool,
      this.horizon(inp.bagLeft),
      this.p.samples,
      this.rand,
      this.p.availProb,
    );
    // Price of the letter itself doesn't enter: we're deciding what to pay.
    const { v0, v1 } = valueRack(this.d, rack, futures, m, this.p, letter);
    let v = v1 - v0;
    // Near the end, multi-word dumps matter: count what the letter adds to
    // the best partition we could play right now.
    if (inp.bagLeft <= this.p.endgameN) {
      const with1 = Int16Array.from(rack);
      with1[letter]++;
      const a = bestPartition(this.d, rack, 25).total;
      const b = bestPartition(this.d, with1, 25).total;
      v = Math.max(v, b - a);
    }
    return Math.max(0, v);
  }

  // The most points one opponent would score from `letter` now, from the
  // tiles they publicly hold: best word with it minus best word without.
  denyValue(letter: number, opps: Opp[]): number {
    let best = 0;
    for (const o of opps) {
      if (total(o.known) < 2) continue;
      const w = Int16Array.from(o.known);
      w[letter]++;
      const a = bestWord(this.d, o.known);
      const b = bestWord(this.d, w);
      if (b - a > best) best = b - a;
    }
    return best;
  }

  decideBid(inp: BidInput): number {
    this.lastBid = 0;
    if (this.p.paceEta > 0) this.pace(inp.balance, inp.bagLeft);
    let v = this.letterValue(inp);
    if (this.p.denyW > 0 && inp.opps && inp.bagLeft > 0) v += this.p.denyW * this.denyValue(inp.letter, inp.opps);
    const floor = this.floorBid(inp);
    if (v <= 0) return (this.lastBid = floor);
    const lam = this.lambda(inp.balance, inp.bagLeft);
    let bid = (v / lam) * (inp.firstPrice ? 1 : this.p.bidMult);
    if (!inp.firstPrice && this.p.bidFloor > 0 && v >= this.p.floorMinV && inp.bagLeft > 0)
      bid = Math.max(bid, this.p.bidFloor * this.price[inp.letter]!);
    if (!inp.firstPrice && this.p.cheapFloor > 0 && v >= this.p.cheapMinV && inp.bagLeft > 0 && LETTER_VALUE[inp.letter]! <= this.p.cheapMaxFace)
      bid = Math.max(bid, this.p.cheapFloor * this.seedPrice[inp.letter]!);
    if (inp.firstPrice) {
      // Pay-your-bid: shade towards what the runner-up is likely to bid,
      // but never above value.
      const market = this.price[inp.letter]! * 1.25 + 1;
      bid = Math.min(bid * this.p.firstPriceShade, Math.max(market, bid * 0.4));
    }
    if (!inp.firstPrice && this.p.mktCap > 0 && inp.bagLeft > 0) bid = Math.min(bid, this.p.mktCap * this.price[inp.letter]!);
    if (this.p.rareCap > 0 && LETTER_VALUE[inp.letter]! >= this.p.rareMinFace && inp.bagLeft > this.p.rareUntil * this.totalAuctions)
      bid = Math.min(bid, this.p.rareCap * this.price[inp.letter]!);
    const cap = inp.bagLeft > 8 ? inp.balance * this.p.bidCapFrac : inp.balance;
    return (this.lastBid = Math.max(floor, Math.min(Math.floor(bid), Math.floor(cap))));
  }

  // See seedFloor. Paced like the live field: scaled by coins per auction
  // left over 1.6, within 0.5-1.3.
  floorBid(inp: BidInput): number {
    // Vickrey only: under first-price the winner pays its own bid, so there
    // is no 1-coin gift to deny (sim heads-up: the floor cost 46 pts).
    if (this.p.seedFloor <= 0 || inp.bagLeft <= 0 || inp.firstPrice) return 0;
    const perAuction = inp.balance / Math.max(1, inp.bagLeft + 1);
    const pace = Math.min(1.3, Math.max(0.5, perAuction / 1.6));
    return Math.max(0, Math.min(Math.floor(this.p.seedFloor * this.seedPrice[inp.letter]! * pace), Math.floor(inp.balance)));
  }

  // Words to play now, in order. `final` = the bag is empty and this is
  // the last chance: play the best partition of everything.
  choosePlays(
    rack: Counts,
    pool: Counts,
    bagLeft: number,
    balance: number,
    final: boolean,
  ): string[] {
    const d = this.d;
    if (final) return bestPartition(d, rack, 150).words;
    const r = Int16Array.from(rack);
    const plays: string[] = [];
    const m = this.market(balance, bagLeft);
    // Horizon for plans: letters still to come from the bag after now.
    const h = this.horizon(bagLeft + 1);
    const futures = sampleFutures(pool, h, this.p.samples, this.rand, this.p.availProb);
    for (let iter = 0; iter < 4; iter++) {
      const words = spellable(d, r, 2)
        .sort((a, b) => d.reward[b]! - d.reward[a]!)
        .slice(0, 10);
      if (words.length === 0) break;
      const hold = valueRack(d, r, futures, m, this.p).v0;
      let bestGain = this.p.playEps;
      let bestW = -1;
      for (const i of words) {
        const rest = Int16Array.from(r);
        const s = d.start[i]!;
        const e = s + d.nDistinct[i]!;
        for (let k = s; k < e; k++) rest[d.letters[2 * k]!] -= d.letters[2 * k + 1]!;
        // Money now also buys letters sooner: a little credit for liquidity.
        const after = d.reward[i]! * (1 + this.p.liqW * m.lambda) + valueRack(d, rest, futures, m, this.p).v0;
        const gain = after - hold;
        if (gain > bestGain) {
          bestGain = gain;
          bestW = i;
        }
      }
      if (bestW < 0) break;
      const s = d.start[bestW]!;
      const e = s + d.nDistinct[bestW]!;
      for (let k = s; k < e; k++) r[d.letters[2 * k]!] -= d.letters[2 * k + 1]!;
      plays.push(d.words[bestW]!);
    }
    return plays;
  }
}

// Unseen letters from our point of view: the full bag minus every letter
// auctioned so far (public) minus our own starting rack.
// `revealed` (optional): opponents' hidden starting tiles that their word
// plays have exposed (letters played beyond letters won), which will
// never come up for auction either.
export function unseenPool(auctioned: Counts, myStart: Counts, revealed?: Counts): Counts {
  const p = emptyCounts();
  for (let c = 0; c < 26; c++)
    p[c] = Math.max(0, BAG[c]! - auctioned[c]! - myStart[c]! - (revealed ? revealed[c]! : 0));
  return p;
}

// Public bookkeeping for one opponent, from auction wins and word plays.
export class OppTracker {
  won = emptyCounts();
  played = emptyCounts();
  balance = 100;
  score = 0;
  addWin(letter: number) {
    this.won[letter]++;
  }
  addPlay(word: string) {
    for (const ch of word) this.played[ch.charCodeAt(0) - 65]++;
  }
  view(): Opp {
    const k = emptyCounts();
    for (let c = 0; c < 26; c++) k[c] = Math.max(0, this.won[c]! - this.played[c]!);
    return { known: k, balance: this.balance, score: this.score };
  }
  // Hidden starting tiles their plays have shown.
  revealed(): Counts {
    const r = emptyCounts();
    for (let c = 0; c < 26; c++) r[c] = Math.max(0, this.played[c]! - this.won[c]!);
    return r;
  }
}
