// Dictionary index shared by the strategy, the simulator and the live bot.
// Words are stored as 26-slot letter counts in one flat Uint8Array so the
// per-auction scans stay allocation-free.

import * as fs from "node:fs";
import * as path from "node:path";

export const LETTER_VALUE = [
  1, 3, 3, 2, 1, 4, 2, 4, 1, 8, 5, 1, 3, 1, 1, 3, 10, 1, 1, 1, 1, 4, 4, 8, 4, 10,
];

// Standard Scrabble bag, no blanks: 98 tiles.
export const BAG = [
  9, 2, 2, 4, 12, 2, 3, 2, 9, 1, 1, 4, 2, 6, 8, 2, 1, 6, 4, 6, 4, 2, 2, 1, 2, 1,
];

export const A = 65;
export const idx = (c: string) => c.charCodeAt(0) - A;
export const chr = (i: number) => String.fromCharCode(A + i);

export function reward(base: number, len: number): number {
  if (len <= 3) return base;
  if (len === 4) return Math.floor((base * 3) / 2);
  if (len === 5) return base * 2;
  if (len === 6) return Math.floor((base * 5) / 2);
  return base * 3;
}

export interface Dict {
  words: string[];
  counts: Uint8Array; // words.length * 26
  len: Uint8Array;
  reward: Uint16Array;
  // Distinct letters of each word, as (letter, count) pairs packed into
  // `letters`, starting at `start[i]` and running for `nDistinct[i]` pairs.
  start: Uint32Array;
  nDistinct: Uint8Array;
  letters: Uint8Array;
  lexSet: Set<string>;
}

let cached: Dict | undefined;

export function wordlistPath(): string {
  const here = path.dirname(new URL(import.meta.url).pathname);
  return path.join(here, "..", "wordlist.txt");
}

export function loadDict(file = wordlistPath()): Dict {
  if (cached) return cached;
  const raw = fs
    .readFileSync(file, "utf8")
    .split("\n")
    .map((l) => l.trim().toUpperCase())
    .filter((l) => l.length >= 2 && /^[A-Z]+$/.test(l));
  const n = raw.length;
  const counts = new Uint8Array(n * 26);
  const len = new Uint8Array(n);
  const rew = new Uint16Array(n);
  const start = new Uint32Array(n);
  const nDistinct = new Uint8Array(n);
  const letters: number[] = [];
  for (let i = 0; i < n; i++) {
    const w = raw[i]!;
    let base = 0;
    for (let k = 0; k < w.length; k++) {
      const c = w.charCodeAt(k) - A;
      counts[i * 26 + c]++;
      base += LETTER_VALUE[c]!;
    }
    len[i] = Math.min(w.length, 255);
    rew[i] = reward(base, w.length);
    start[i] = letters.length / 2;
    let d = 0;
    for (let c = 0; c < 26; c++) {
      const k = counts[i * 26 + c]!;
      if (k > 0) {
        letters.push(c, k);
        d++;
      }
    }
    nDistinct[i] = d;
  }
  cached = {
    words: raw,
    counts,
    len,
    reward: rew,
    start,
    nDistinct,
    letters: Uint8Array.from(letters),
    lexSet: new Set(raw),
  };
  return cached;
}

export function wordReward(w: string): number {
  let base = 0;
  for (const ch of w) base += LETTER_VALUE[idx(ch)]!;
  return reward(base, w.length);
}
