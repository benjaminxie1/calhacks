// Dictionary index and word search. Racks are 26-slot letter counts
// (index 0 = 'A'), so the hot loops never touch strings.

import * as fs from "node:fs";

const A = 65;

// Standard Scrabble values, A..Z (the module's `letterValue`).
export const VALUES = [
  1, 3, 3, 2, 1, 4, 2, 4, 1, 8, 5, 1, 3, 1, 1, 3, 10, 1, 1, 1, 1, 4, 4, 8, 4, 10,
];

// The module's starting bag: standard distribution, no blanks, 98 tiles.
export const BAG = [
  9, 2, 2, 4, 12, 2, 3, 2, 9, 1, 1, 4, 2, 6, 8, 2, 1, 6, 4, 6, 4, 2, 2, 1, 2, 1,
];

export type Rack = Uint8Array; // length 26

// totalReward = base * num / denom, integer division (letters.ts).
export function reward(base: number, len: number): number {
  if (len <= 3) return base;
  if (len === 4) return Math.floor((base * 3) / 2);
  if (len === 5) return base * 2;
  if (len === 6) return Math.floor((base * 5) / 2);
  return base * 3;
}

export function slot(c: string): number {
  return c.charCodeAt(0) - A;
}

export function letterOf(i: number): string {
  return String.fromCharCode(A + i);
}

export function rackFromMap(m: Map<string, number>): Rack {
  const r = new Uint8Array(26);
  for (const [c, n] of m) {
    const i = slot(c);
    if (i >= 0 && i < 26) r[i] = Math.min(255, r[i]! + n);
  }
  return r;
}

export function rackFromString(s: string): Rack {
  const r = new Uint8Array(26);
  for (const c of s) r[slot(c)]!++;
  return r;
}

export function rackTotal(r: Rack): number {
  let t = 0;
  for (let i = 0; i < 26; i++) t += r[i]!;
  return t;
}

export function rackMask(r: Rack): number {
  let m = 0;
  for (let i = 0; i < 26; i++) if (r[i]! > 0) m |= 1 << i;
  return m;
}

export function rackString(r: Rack): string {
  let s = "";
  for (let i = 0; i < 26; i++) s += letterOf(i).repeat(r[i]!);
  return s;
}

export function popcount(x: number): number {
  x -= (x >>> 1) & 0x55555555;
  x = (x & 0x33333333) + ((x >>> 2) & 0x33333333);
  return (Math.imul((x + (x >>> 4)) & 0x0f0f0f0f, 0x01010101) >>> 24) & 0xff;
}

let SHARED: Lexicon | undefined;

export class Lexicon {
  readonly n: number;
  // Longest first, then alphabetical: the order the module's
  // `findBestPlayable` searches in.
  readonly words: string[];
  readonly counts: Uint8Array; // n * 26
  readonly masks: Int32Array;
  readonly lens: Uint8Array;
  readonly rewards: Int32Array;
  // startLe[L] = first index whose word has length <= L.
  private readonly startLe: Int32Array;
  readonly maxLen: number;
  private readonly set: Set<string>;

  constructor(raw: string[]) {
    const words = [
      ...new Set(
        raw
          .map((w) => w.trim().toUpperCase())
          .filter((w) => w.length >= 2 && /^[A-Z]+$/.test(w)),
      ),
    ].sort((a, b) => b.length - a.length || (a < b ? -1 : a > b ? 1 : 0));
    this.words = words;
    this.set = new Set(words);
    const n = words.length;
    this.n = n;
    this.counts = new Uint8Array(n * 26);
    this.masks = new Int32Array(n);
    this.lens = new Uint8Array(n);
    this.rewards = new Int32Array(n);
    this.maxLen = n > 0 ? words[0]!.length : 0;
    for (let i = 0; i < n; i++) {
      const w = words[i]!;
      let base = 0;
      let mask = 0;
      for (let j = 0; j < w.length; j++) {
        const s = w.charCodeAt(j) - A;
        this.counts[i * 26 + s]!++;
        mask |= 1 << s;
        base += VALUES[s]!;
      }
      this.masks[i] = mask;
      this.lens[i] = w.length;
      this.rewards[i] = reward(base, w.length);
    }
    this.startLe = new Int32Array(this.maxLen + 2);
    for (let L = 0; L <= this.maxLen + 1; L++) {
      let lo = 0;
      let hi = n;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (this.lens[mid]! > L) lo = mid + 1;
        else hi = mid;
      }
      this.startLe[L] = lo;
    }
  }

  static fromFile(path: string | URL): Lexicon {
    return new Lexicon(fs.readFileSync(path, "utf8").split("\n"));
  }

  // The module's own wordlist, so the bot never plays a word it rejects.
  static shared(): Lexicon {
    SHARED ??= Lexicon.fromFile(
      new URL("../../wordlist.txt", import.meta.url),
    );
    return SHARED;
  }

  has(word: string): boolean {
    return this.set.has(word);
  }

  // Index range [from, to) of words with length in [minLen, maxLen].
  range(minLen: number, maxLen: number): [number, number] {
    const hi = Math.min(maxLen, this.maxLen);
    const lo = Math.max(minLen, 1);
    if (hi < lo) return [0, 0];
    return [this.startLe[hi]!, this.startLe[lo - 1]!];
  }

  fits(i: number, r: Rack): boolean {
    const base = i * 26;
    for (let s = 0; s < 26; s++) if (this.counts[base + s]! > r[s]!) return false;
    return true;
  }

  // Every word `r` can spell with at least `minLen` letters, in index order
  // (longest first).
  playable(r: Rack, minLen = 2): number[] {
    const total = rackTotal(r);
    const mask = rackMask(r);
    const [from, to] = this.range(minLen, total);
    const out: number[] = [];
    for (let i = from; i < to; i++) {
      if ((this.masks[i]! & ~mask) !== 0) continue;
      if (this.fits(i, r)) out.push(i);
    }
    return out;
  }

  // The module's sim word choice: the first (longest, then alphabetical)
  // word of at least `minLen` letters.
  firstPlayable(r: Rack, minLen: number): number | undefined {
    const total = rackTotal(r);
    const mask = rackMask(r);
    const [from, to] = this.range(minLen, total);
    for (let i = from; i < to; i++) {
      if ((this.masks[i]! & ~mask) !== 0) continue;
      if (this.fits(i, r)) return i;
    }
    return undefined;
  }

  take(i: number, r: Rack) {
    const base = i * 26;
    for (let s = 0; s < 26; s++) r[s] = r[s]! - this.counts[base + s]!;
  }

  give(i: number, r: Rack) {
    const base = i * 26;
    for (let s = 0; s < 26; s++) r[s] = r[s]! + this.counts[base + s]!;
  }
}

// The set of disjoint words from `r` with the largest total reward. A
// bounded search: at each step only the `breadth` best-paying words that
// still fit are tried, and words are taken in candidate order so each set
// is visited once.
export function bestPartition(
  lex: Lexicon,
  r: Rack,
  breadth = 10,
  minLen = 2,
): { total: number; words: number[] } {
  const cand = lex
    .playable(r, minLen)
    .sort((a, b) => lex.rewards[b]! - lex.rewards[a]!);
  const work = Uint8Array.from(r);
  let best = { total: 0, words: [] as number[] };
  const chosen: number[] = [];
  let nodes = 0;

  const upper = (rack: Rack) => {
    let base = 0;
    for (let s = 0; s < 26; s++) base += rack[s]! * VALUES[s]!;
    return base * 3;
  };

  const rec = (start: number, total: number) => {
    nodes++;
    if (total > best.total) best = { total, words: [...chosen] };
    if (nodes > 20000) return;
    if (total + upper(work) <= best.total) return;
    let tried = 0;
    for (let k = start; k < cand.length && tried < breadth; k++) {
      const w = cand[k]!;
      if (!lex.fits(w, work)) continue;
      tried++;
      lex.take(w, work);
      chosen.push(w);
      rec(k, total + lex.rewards[w]!);
      chosen.pop();
      lex.give(w, work);
    }
  };
  rec(0, 0);
  return best;
}
