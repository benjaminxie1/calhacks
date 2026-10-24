// Bidding and word-play strategy.
//
// The points are in long words: the length multiplier reaches 3x at seven
// letters and applies to the whole word, so a letter is worth what it adds
// to the 7+ letter words the rack is close to spelling. Coins and unplayed
// letters are worth nothing when the match ends, so the bot spends its
// balance down as the bag empties and plays out its rack while it still can.
//
// Everything here is a pure function of a `MatchView`, so the same code runs
// in the live bot (index.ts) and in the offline arena (arena.ts).

import {
  BAG,
  bestPartition,
  Lexicon,
  popcount,
  rackFromMap,
  rackMask,
  rackTotal,
  slot,
  type Rack,
} from "./words.js";

export interface PlayerView {
  botId: string;
  balance: number;
  score: number;
}

export interface MatchView {
  me: string; // my bot id
  letter: string | null; // letter on the open auction
  bagTotal: number; // tiles left in the bag, not counting the open auction's
  rack: Map<string, number>;
  balance: number;
  players: PlayerView[];
  auctions: { letter: string; winner: string | null }[]; // closed, this match
  words: { botId: string; word: string }[]; // played, this match
}

export interface Decision {
  words: string[]; // play these first, in order
  bid: number; // then bid this on the open auction (0 = don't)
}

export interface Params {
  winRate: number; // share of a wanted letter's future copies we expect to win
  maxMissing: number; // target words at most this many letters short
  minTargetLen: number;
  topN: number; // rack potential sums the best few targets...
  decay: number; // ...weighting the k-th by decay^k
  bidScale: number; // coins bid per point of potential gained
  spendHorizon: number; // larger = spend the balance more slowly
  flushAt: number; // play out the rack once bagTotal <= flushAt
  playLen: number; // play words this long as soon as they're spellable
  lowCoins: number; // below this balance, cash in a shorter word...
  lowCoinLen: number; // ...of at least this many letters
  leaveWeight: number; // how much the leftover rack counts when choosing a word
}

// Tuned in the arena (tune.ts) against a field running from passive to
// hot bidders. flushAt 1 plays out a tile early: in the arena it costs
// under a point a match, and live it leaves a whole auction of slack for
// latency instead of racing the final close.
export const DEFAULT_PARAMS: Params = {
  winRate: 0.45,
  maxMissing: 3,
  minTargetLen: 4,
  topN: 4,
  decay: 0.6,
  bidScale: 0.8,
  spendHorizon: 2,
  flushAt: 1,
  playLen: 7,
  lowCoins: 8,
  lowCoinLen: 5,
  leaveWeight: 0.5,
};

const MAX_EXTRA = 3; // copies of one letter a target may still need

export class Strategy {
  readonly params: Params;
  private readonly lex: Lexicon;
  // Per-decision scratch: chance of winning the k-th more copy of a letter,
  // at [slot * (MAX_EXTRA + 1) + k].
  private readonly p = new Float64Array(26 * (MAX_EXTRA + 1));
  private readonly topA: Float64Array;
  private readonly topB: Float64Array;

  constructor(params: Partial<Params> = {}, lex: Lexicon = Lexicon.shared()) {
    this.params = { ...DEFAULT_PARAMS, ...params };
    this.lex = lex;
    this.topA = new Float64Array(this.params.topN);
    this.topB = new Float64Array(this.params.topN);
  }

  decide(view: MatchView): Decision {
    const rack = rackFromMap(view.rack);
    return this.decideFrom(rack, view.balance, view.letter, view.bagTotal, unseenPool(view, rack));
  }

  // The same decision from raw counts, for hosts that track the game
  // themselves. `pool`: tiles not yet seen anywhere (not auctioned, not
  // ours, not shown by an opponent's play), the open auction's excluded.
  decideFrom(
    rack: Rack,
    balance: number,
    letter: string | null,
    bagTotal: number,
    pool: ArrayLike<number>,
  ): Decision {
    this.setOdds(pool, bagTotal);
    const words: string[] = [];
    for (const w of this.chooseWords(bagTotal, rack, balance)) {
      words.push(this.lex.words[w]!);
      this.lex.take(w, rack);
      balance += this.lex.rewards[w]!;
    }
    return { words, bid: this.chooseBid(letter, bagTotal, rack, balance) };
  }

  // ---------- Words ----------

  private chooseWords(bagTotal: number, rack: Rack, balance: number): number[] {
    const prm = this.params;
    // The last chance: the match ends when the final auction closes.
    if (bagTotal <= prm.flushAt) return bestPartition(this.lex, rack).words;

    const out: number[] = [];
    for (;;) {
      const w = this.bestWord(rack, prm.playLen);
      if (w === undefined) break;
      out.push(w);
      this.lex.take(w, rack);
      balance += this.lex.rewards[w]!;
    }
    if (balance < prm.lowCoins) {
      const w = this.bestWord(rack, prm.lowCoinLen);
      if (w !== undefined) {
        out.push(w);
        this.lex.take(w, rack);
      }
    }
    // The search took the letters out of `rack`; put them back for
    // decide() to take again.
    for (const w of out) this.lex.give(w, rack);
    return out;
  }

  // The spellable word of `minLen`+ letters that pays best, counting what it
  // leaves behind.
  private bestWord(rack: Rack, minLen: number): number | undefined {
    const cands = this.lex
      .playable(rack, minLen)
      .sort((a, b) => this.lex.rewards[b]! - this.lex.rewards[a]!)
      .slice(0, 6);
    let best: number | undefined;
    let bestValue = -Infinity;
    for (const w of cands) {
      this.lex.take(w, rack);
      const leave = this.potential(rack, -1)[0];
      this.lex.give(w, rack);
      const value = this.lex.rewards[w]! + this.params.leaveWeight * leave;
      if (value > bestValue) {
        bestValue = value;
        best = w;
      }
    }
    return best;
  }

  // ---------- Bids ----------

  private chooseBid(letter: string | null, bagTotal: number, rack: Rack, balance: number): number {
    if (letter === null || balance <= 0) return 0;
    // The final tile lands as the match ends, too late to play.
    if (bagTotal === 0) return 0;
    const L = slot(letter);
    let gain: number;
    if (bagTotal === 1) {
      // Last playable tile: worth what it adds to the final play-out.
      const before = bestPartition(this.lex, rack).total;
      rack[L]!++;
      const after = bestPartition(this.lex, rack).total;
      rack[L]!--;
      gain = after - before;
    } else {
      const [a, b] = this.potential(rack, L);
      gain = b - a;
    }
    if (gain <= 0) return 0;
    const prm = this.params;
    const pace = 1 + balance / (prm.spendHorizon * bagTotal);
    return Math.max(0, Math.min(balance, Math.floor(gain * prm.bidScale * pace)));
  }

  // ---------- Rack potential ----------

  // Chance of winning more copies of each letter before the match ends,
  // from the letters not yet seen and the auctions left.
  private setOdds(pool: ArrayLike<number>, bagTotal: number) {
    let unseen = 0;
    for (let s = 0; s < 26; s++) unseen += Math.max(0, pool[s]!);
    // Tiles still to be auctioned after this one that arrive in time to play.
    const future = Math.max(0, bagTotal - 1);
    const q = this.params.winRate;
    for (let s = 0; s < 26; s++) {
      const expected = unseen > 0 ? (Math.max(0, pool[s]!) * future) / unseen : 0;
      for (let k = 1; k <= MAX_EXTRA; k++) {
        this.p[s * (MAX_EXTRA + 1) + k] = 1 - Math.exp(-q * Math.max(0, expected - (k - 1)));
      }
    }
  }

  // [potential of rack, potential of rack plus one `extra`] (extra = -1: none).
  // Potential sums the best few target words, each its reward times the
  // chance of collecting its missing letters.
  potential(rack: Rack, extra: number): [number, number] {
    const prm = this.params;
    const lex = this.lex;
    const M = prm.maxMissing;
    const total = rackTotal(rack) + (extra >= 0 ? 1 : 0);
    const [from, to] = lex.range(prm.minTargetLen, total + M);
    const mask = rackMask(rack);
    const maskB = extra >= 0 ? mask | (1 << extra) : mask;
    const topA = this.topA.fill(0);
    const topB = this.topB.fill(0);
    const N = prm.topN;
    const masks = lex.masks;
    const counts = lex.counts;
    const p = this.p;
    const stride = MAX_EXTRA + 1;

    for (let i = from; i < to; i++) {
      const wm = masks[i]!;
      if (popcount(wm & ~maskB) > M) continue;
      const base = i * 26;
      let missA = 0;
      let missB = 0;
      let piA = 1;
      let piB = 1;
      let bits = wm;
      while (bits !== 0) {
        const low = bits & -bits;
        bits ^= low;
        const s = 31 - Math.clz32(low);
        const need = counts[base + s]!;
        const have = rack[s]!;
        if (need > have) {
          const short = need - have;
          missA += short;
          for (let k = 1; k <= short; k++) piA *= p[s * stride + Math.min(k, MAX_EXTRA)]!;
        }
        const haveB = s === extra ? have + 1 : have;
        if (need > haveB) {
          const short = need - haveB;
          missB += short;
          for (let k = 1; k <= short; k++) piB *= p[s * stride + Math.min(k, MAX_EXTRA)]!;
        }
        if (missB > M) break;
      }
      if (missB > M) continue;
      const r = lex.rewards[i]!;
      if (missA <= M) insertTop(topA, N, r * piA);
      insertTop(topB, N, r * piB);
    }
    let a = 0;
    let b = 0;
    let wgt = 1;
    for (let k = 0; k < N; k++) {
      a += wgt * topA[k]!;
      b += wgt * topB[k]!;
      wgt *= prm.decay;
    }
    return [a, b];
  }
}

// Keep `top` as the N largest values seen, descending.
function insertTop(top: Float64Array, N: number, v: number) {
  if (v <= top[N - 1]!) return;
  let k = N - 1;
  while (k > 0 && top[k - 1]! < v) {
    top[k] = top[k - 1]!;
    k--;
  }
  top[k] = v;
}

// Tiles not yet seen anywhere, from the public history: the bag, less every
// auctioned tile (the open one too), my starting tiles, and the starting
// tiles opponents have shown by playing more of a letter than they won.
export function unseenPool(view: MatchView, rack: Rack): number[] {
  const pool = BAG.slice();
  const won = new Array<number>(26).fill(0);
  const playedBy = new Map<string, number[]>();
  const wonBy = new Map<string, number[]>();
  const bump = (m: Map<string, number[]>, who: string, s: number) => {
    let v = m.get(who);
    if (!v) m.set(who, (v = new Array<number>(26).fill(0)));
    v[s]!++;
  };
  for (const a of view.auctions) {
    const s = slot(a.letter);
    pool[s]!--;
    if (a.winner !== null) bump(wonBy, a.winner, s);
    if (a.winner === view.me) won[s]!++;
  }
  if (view.letter !== null) pool[slot(view.letter)]!--;
  for (const w of view.words) for (const c of w.word) bump(playedBy, w.botId, slot(c));
  // A bot's starting tiles = what it holds or played, less what it won.
  const myPlayed = playedBy.get(view.me);
  for (let s = 0; s < 26; s++) {
    pool[s]! -= Math.max(0, rack[s]! + (myPlayed?.[s] ?? 0) - won[s]!);
  }
  for (const [who, played] of playedBy) {
    if (who === view.me) continue;
    const w = wonBy.get(who);
    for (let s = 0; s < 26; s++) pool[s]! -= Math.max(0, played[s]! - (w?.[s] ?? 0));
  }
  return pool.map((n) => Math.max(0, n));
}
