// Pulls the public match history from maincloud's HTTP SQL endpoint and
// normalises it into plain objects. Every table the flywheel reads is
// public; an anonymous identity is enough, so no bot token is involved.

import * as fs from "node:fs";
import * as path from "node:path";

const HOST = process.env.STDB_HOST ?? "https://maincloud.spacetimedb.com";
const DB = process.env.STDB_DB ?? "scrabblebot";

export interface MatchRow {
  id: number;
  ended: boolean;
  firstPrice: boolean;
  startedAt: number; // ms since epoch, 0 if never started
  auctionMs: number;
  rackSize: number;
}
export interface Participant { matchId: number; botId: number; balance: number; score: number }
export interface AuctionRow { auctionId: number; matchId: number; letter: number; winner: number | null; paid: number; closedAt: number }
export interface WordRow { matchId: number; botId: number; word: string; reward: number; playedAt: number }
export interface BotRow { id: number; name: string; isSim: boolean }

export interface History {
  fetchedAt: string;
  matches: MatchRow[];
  participants: Participant[];
  auctions: AuctionRow[];
  topBid: Map<number, number>; // auctionId -> winning (highest) bid
  words: WordRow[];
  bots: BotRow[];
}

async function token(): Promise<string> {
  const r = await fetch(`${HOST}/v1/identity`, { method: "POST" });
  if (!r.ok) throw new Error(`identity: HTTP ${r.status}`);
  return ((await r.json()) as { token: string }).token;
}

async function sql(tok: string, q: string): Promise<unknown[][]> {
  for (let attempt = 0; ; attempt++) {
    try {
      const r = await fetch(`${HOST}/v1/database/${DB}/sql`, {
        method: "POST",
        headers: { Authorization: `Bearer ${tok}` },
        body: q,
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
      const j = (await r.json()) as { rows: unknown[][] }[];
      return j[0]!.rows;
    } catch (e) {
      if (attempt >= 3) throw new Error(`sql "${q}": ${e instanceof Error ? e.message : e}`);
      await new Promise((res) => setTimeout(res, 2000 * 2 ** attempt));
    }
  }
}

// SATS JSON: Option is [0, value] (some) or [1, []] (none); a Timestamp is
// [micros]; a plain enum is [tag, []].
const opt = <T>(v: unknown): T | null => ((v as [number, T])[0] === 0 ? (v as [number, T])[1] : null);
const ts = (v: unknown) => Number((v as [number])[0]) / 1000;
const L = (s: unknown) => (s as string).charCodeAt(0) - 65;

export async function fetchHistory(): Promise<History> {
  const tok = await token();
  const [ms, ps, ar, tb, wp, bs] = await Promise.all([
    sql(tok, "SELECT * FROM match_state"),
    sql(tok, "SELECT * FROM match_participant"),
    sql(tok, "SELECT * FROM auction_result"),
    sql(tok, "SELECT * FROM auction_top_bid_archive"),
    sql(tok, "SELECT * FROM word_play"),
    sql(tok, "SELECT * FROM bot"),
  ]);
  // match_state: id, status, current_round, current_auction_id, bag_total,
  // auction_type (0 = firstPrice, 1 = vickrey), created_at, started_at,
  // ended_at, auction_duration_ms, starting_rack_size
  const matches = ms.map((r) => {
    const started = opt<unknown>(r[7]);
    return {
      id: Number(r[0]),
      ended: (r[1] as [number])[0] === 2,
      firstPrice: (r[5] as [number])[0] === 0,
      startedAt: started ? ts(started) : 0,
      auctionMs: Number(r[9]),
      rackSize: Number(r[10]),
    };
  });
  const participants = ps.map((r) => ({ matchId: Number(r[1]), botId: Number(r[2]), balance: Number(r[3]), score: Number(r[4]) }));
  const auctions = ar.map((r) => {
    const w = opt<number>(r[3]);
    return { auctionId: Number(r[0]), matchId: Number(r[1]), letter: L(r[2]), winner: w === null ? null : Number(w), paid: Number(r[4]), closedAt: ts(r[5]) };
  });
  const topBid = new Map(tb.map((r) => [Number(r[0]), Number(r[2])] as [number, number]));
  const words = wp.map((r) => ({ matchId: Number(r[1]), botId: Number(r[2]), word: r[3] as string, reward: Number(r[6]), playedAt: ts(r[7]) }));
  const bots = bs.map((r) => ({ id: Number(r[0]), name: r[1] as string, isSim: r[3] as boolean }));
  return { fetchedAt: new Date().toISOString(), matches, participants, auctions, topBid, words, bots };
}

// Raw cache (gitignored) so a fit can be rerun offline.
export function saveHistory(h: History, dir: string) {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "history.json"), JSON.stringify({ ...h, topBid: [...h.topBid] }));
}
export function loadHistory(dir: string): History {
  const j = JSON.parse(fs.readFileSync(path.join(dir, "history.json"), "utf8"));
  return { ...j, topBid: new Map(j.topBid) };
}
