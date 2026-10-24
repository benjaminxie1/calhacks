// Live bot. Based on clockworklabs/scrabblebot bot-starter, with the
// strategy from ./engine.ts.
//
// First run:  BOT_NAME=<name> BOT_TOKEN=<token from /team> npm start
// Later runs: BOT_NAME=<name> npm start   (token saved to .token-<name>)
// Tournament: add TOURNAMENT_ID=<id>
// Tuning:     PARAMS='{"lam0":0.3}' overrides engine DEFAULT_PARAMS

import { DbConnection, type ErrorContext } from "./module_bindings/index.js";
import { Identity } from "spacetimedb";
import * as fs from "node:fs";
import * as path from "node:path";
import { Brain, countsOf, emptyCounts, unseenPool, type Counts } from "./engine.js";

const HOST = process.env.STDB_HOST ?? "https://maincloud.spacetimedb.com";
const DB_NAME = process.env.STDB_DB ?? "scrabblebot";
const BOT_NAME = process.env.BOT_NAME ?? "bot";
const BOT_TOKEN = process.env.BOT_TOKEN;
const TOURNAMENT_ID = process.env.TOURNAMENT_ID;
const PARAMS = process.env.PARAMS ? JSON.parse(process.env.PARAMS) : {};
const TOKEN_PATH = path.join(process.cwd(), `.token-${BOT_NAME}`);

const loadToken = () => {
  try {
    return fs.readFileSync(TOKEN_PATH, "utf8").trim() || undefined;
  } catch {
    return undefined;
  }
};

let myIdentity: Identity | null = null;
let myBotId: bigint | null = null;
const brains = new Map<string, Brain>(); // per match
const bidOn = new Set<string>();
const finalDone = new Set<string>();
const lastRack = new Map<string, string>();

const warn = (what: string) => (e: unknown) =>
  console.warn(`[${BOT_NAME}] ${what} failed: ${e instanceof Error ? e.message : e}`);

function brainFor(matchId: bigint): Brain {
  const k = String(matchId);
  let b = brains.get(k);
  if (!b) {
    b = new Brain(PARAMS);
    brains.set(k, b);
  }
  return b;
}

function rackFor(conn: DbConnection, matchId: bigint): Counts {
  const r = emptyCounts();
  for (const h of conn.db.myRack.iter())
    if (h.matchId === matchId) r[h.letter.charCodeAt(0) - 65] += h.count;
  return r;
}

function balanceFor(conn: DbConnection, matchId: bigint): number | null {
  for (const p of conn.db.matchParticipant.iter())
    if (p.matchId === matchId && p.botId === myBotId) return Number(p.balance);
  return null;
}

// Every letter revealed in this match (closed auctions + the open one).
function auctionedFor(conn: DbConnection, matchId: bigint): Counts {
  const a = emptyCounts();
  for (const x of conn.db.auction.iter()) if (x.matchId === matchId) a[x.letter.charCodeAt(0) - 65]++;
  return a;
}

// Our starting rack = rack now + letters we've played - letters we've won.
function startRackFor(conn: DbConnection, matchId: bigint): Counts {
  const s = rackFor(conn, matchId);
  for (const w of conn.db.wordPlay.iter())
    if (w.matchId === matchId && w.botId === myBotId) {
      const c = countsOf(w.word);
      for (let i = 0; i < 26; i++) s[i] += c[i]!;
    }
  for (const r of conn.db.auctionResult.iter())
    if (r.matchId === matchId && r.winnerBotId === myBotId) s[r.letter.charCodeAt(0) - 65]--;
  for (let i = 0; i < 26; i++) if (s[i]! < 0) s[i] = 0;
  return s;
}

function runningMatches(conn: DbConnection): bigint[] {
  const out: bigint[] = [];
  for (const p of conn.db.matchParticipant.iter()) {
    if (p.botId !== myBotId) continue;
    if (conn.db.matchState.id.find(p.matchId)?.status.tag === "Running") out.push(p.matchId);
  }
  return out;
}

function tryBid(conn: DbConnection, auctionId: bigint, matchId: bigint, letter: string) {
  if (myBotId === null) return;
  const key = String(auctionId);
  if (bidOn.has(key)) return;
  const m = conn.db.matchState.id.find(matchId);
  const balance = balanceFor(conn, matchId);
  if (!m || m.status.tag !== "Running" || balance === null) return;
  bidOn.add(key);
  // Last auction: dump everything first, while the match is still running.
  if (m.bagTotal === 0) playWords(conn, matchId, true);
  const t0 = Date.now();
  const amount = brainFor(matchId).decideBid({
    letter: letter.charCodeAt(0) - 65,
    rack: rackFor(conn, matchId),
    balance,
    pool: unseenPool(auctionedFor(conn, matchId), startRackFor(conn, matchId)),
    bagLeft: m.bagTotal,
    firstPrice: m.auctionType.tag === "FirstPrice",
  });
  console.log(`[${BOT_NAME}] m${matchId} bag${m.bagTotal} '${letter}' bid ${amount} (bal ${balance}, ${Date.now() - t0}ms)`);
  if (amount > 0)
    conn.reducers.submitBid({ auctionId, amount: BigInt(amount) }).catch(warn(`bid on ${auctionId}`));
}

function playWords(conn: DbConnection, matchId: bigint, final: boolean) {
  const k = String(matchId);
  if (final) {
    if (finalDone.has(k)) return;
    finalDone.add(k);
  }
  const rack = rackFor(conn, matchId);
  const rk = rack.join(",");
  if (!final && lastRack.get(k) === rk) return;
  lastRack.set(k, rk);
  const m = conn.db.matchState.id.find(matchId);
  const balance = balanceFor(conn, matchId);
  if (!m || balance === null) return;
  const words = brainFor(matchId).choosePlays(
    rack,
    unseenPool(auctionedFor(conn, matchId), startRackFor(conn, matchId)),
    m.bagTotal,
    balance,
    final || m.bagTotal === 0,
  );
  for (const word of words) {
    console.log(`[${BOT_NAME}] m${matchId} plays ${word}${final ? " (final)" : ""}`);
    conn.reducers.submitWord({ matchId, word }).catch(warn(`playing ${word}`));
  }
}

function joinLobby(conn: DbConnection) {
  if (myBotId === null || runningMatches(conn).length) return;
  const open = Array.from(conn.db.lobby.iter()).find((l) => l.status.tag === "Open");
  if (open && Array.from(conn.db.lobbyMember.iter()).some((x) => x.lobbyId === open.id && x.botId === myBotId)) return;
  console.log(`[${BOT_NAME}] joining lobby`);
  conn.reducers.joinLobby({}).catch(warn("joining the lobby"));
}

function bootstrap(conn: DbConnection) {
  if (TOURNAMENT_ID)
    conn.reducers
      .registerForTournament({ tournamentId: BigInt(TOURNAMENT_ID) })
      .then(() => console.log(`[${BOT_NAME}] registered for tournament ${TOURNAMENT_ID}`))
      .catch(warn("tournament registration"));
  joinLobby(conn);
  for (const a of conn.db.auction.iter()) if (a.status.tag === "Open") tryBid(conn, a.id, a.matchId, a.letter);
}

function onConnect(conn: DbConnection, identity: Identity, token: string) {
  myIdentity = identity;
  fs.writeFileSync(TOKEN_PATH, token);
  console.log(`[${BOT_NAME}] connected as ${identity.toHexString()}`);
  conn
    .subscriptionBuilder()
    .onApplied(() => {
      myBotId = conn.db.botCredential.identity.find(myIdentity!)?.botId ?? null;
      if (myBotId === null) {
        console.error(`[${BOT_NAME}] no bot for this token; get a fresh one from /team`);
        process.exit(1);
      }
      console.log(`[${BOT_NAME}] acting as '${conn.db.bot.id.find(myBotId)?.name}' (id ${myBotId})`);
      bootstrap(conn);
    })
    .subscribeToAllTables();

  conn.db.auction.onInsert((_c, a) => {
    if (a.status.tag === "Open") tryBid(conn, a.id, a.matchId, a.letter);
  });
  conn.db.myRack.onInsert((_c, h) => playWords(conn, h.matchId, false));
  conn.db.myRack.onUpdate((_c, _o, h) => playWords(conn, h.matchId, false));
  conn.db.auctionResult.onInsert((_c, r) => {
    const b = brains.get(String(r.matchId));
    if (!b) return;
    b.observePrice(r.letter.charCodeAt(0) - 65, Number(r.paid));
    b.observeSpend(r.winnerBotId === myBotId ? Number(r.paid) : 0);
  });
  conn.db.matchState.onUpdate((_c, old, neu) => {
    if (old.status.tag !== "Ended" && neu.status.tag === "Ended") {
      brains.delete(String(neu.id));
      const p = Array.from(conn.db.matchParticipant.iter()).find((x) => x.matchId === neu.id && x.botId === myBotId);
      if (p) {
        console.log(`[${BOT_NAME}] match ${neu.id} ended, score ${p.score}`);
        joinLobby(conn);
      }
    }
  });
}

DbConnection.builder()
  .withUri(HOST)
  .withDatabaseName(DB_NAME)
  .withToken(BOT_TOKEN ?? loadToken())
  .onConnect(onConnect)
  .onConnectError((_c: ErrorContext, e: Error) => console.error("connect error:", e.message))
  .onDisconnect(() => console.log("disconnected"))
  .build();
