// Live bot. Based on clockworklabs/scrabblebot bot-starter, with the
// strategy from ./engine.ts.
//
// First run:  BOT_NAME=<name> BOT_TOKEN=<token from /team> npm start
// Later runs: BOT_NAME=<name> npm start   (token saved to .token-<name>)
// Unattended: BOT_NAME=<name> scripts/live.sh  (restarts, pulls updates)
// Tournament: add TOURNAMENT_ID=<id>
// Tuning:     params.json (re-read at each new match), then PARAMS='{"lam0":0.3}'
//             on top. Both are partial engine Params; params.json may also
//             carry "seedPrice": [26 numbers].
// Updates:    LIVE_AUTOPULL=1 pulls origin/scrabblebot after each match; if
//             code changed the process exits 75 so the wrapper restarts it.
//
// Exit codes: 1 = bad token (don't restart), 75 = restart me.

import { DbConnection, type ErrorContext } from "./module_bindings/index.js";
import { Identity } from "spacetimedb";
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { promisify } from "node:util";
import { Brain, DEFAULT_PARAMS, countsOf, emptyCounts, unseenPool, type Counts, type Params } from "./engine.js";

const HOST = process.env.STDB_HOST ?? "https://maincloud.spacetimedb.com";
const DB_NAME = process.env.STDB_DB ?? "scrabblebot";
const BOT_NAME = process.env.BOT_NAME ?? "bot";
const BOT_TOKEN = process.env.BOT_TOKEN;
const TOURNAMENT_ID = process.env.TOURNAMENT_ID;
// Test mode: DRY_RUN=1 BOT_ID=<any bot id> shadows that bot's matches
// without a token and without sending anything (our rack stays empty).
const DRY_RUN = process.env.DRY_RUN === "1";
const ENV_PARAMS = process.env.PARAMS ? JSON.parse(process.env.PARAMS) : {};
const PARAMS_FILE = process.env.PARAMS_FILE ?? path.join(process.cwd(), "params.json");
const AUTOPULL = process.env.LIVE_AUTOPULL === "1";
const PULL_BRANCH = process.env.PULL_BRANCH ?? "scrabblebot";
const TOKEN_PATH = path.join(process.cwd(), `.token-${BOT_NAME}`);
const RUNS_DIR = path.join(process.cwd(), "runs");

// Bid timing. A bid must reach the server before the auction closes. Our
// clock and the server's differ by tens of ms, so the deadline is counted
// from when the auction reached us: duration - time since then - one
// round trip (measured from bid acks) - a safety margin.
const BID_SAFETY_MS = 80;
const FULL_SAMPLES_BUDGET_MS = 450; // below this, Monte Carlo samples shrink
const MIN_SAMPLES = 6;
const STALL_MS = 20_000; // no updates during a running match = dead socket
const OFFLINE_MS = 90_000;

const exec = promisify(execFile);

const loadToken = () => {
  try {
    return fs.readFileSync(TOKEN_PATH, "utf8").trim() || undefined;
  } catch {
    return undefined;
  }
};

let liveConn: DbConnection | null = null;
let myIdentity: Identity | null = null;
let myBotId: bigint | null = null;
let connected = false;
let lastConnectedAt = Date.now();
let lastEventAt = Date.now();
let lastJoinAt = 0;
let rttEst = 150; // reducer round trip, ms
let lastParams = "";
const brains = new Map<string, Brain>(); // per match
const matchSubs = new Map<string, { unsubscribe(): void }>();
const bidOn = new Set<string>();
const finalDone = new Set<string>();
const lastRack = new Map<string, string>();
const playsQueued = new Set<string>();
const loggedEnd = new Set<string>();

const warn = (what: string) => (e: unknown) =>
  console.warn(`[${BOT_NAME}] ${what} failed: ${e instanceof Error ? e.message : e}`);

// Append-only JSONL log, one file per UTC day, for the data flywheel.
function log(ev: Record<string, unknown>) {
  try {
    fs.mkdirSync(RUNS_DIR, { recursive: true });
    const t = new Date();
    const line = JSON.stringify({ t: t.toISOString(), bot: BOT_NAME, ...ev }, (_k, v) =>
      typeof v === "bigint" ? Number(v) : v,
    );
    fs.appendFileSync(path.join(RUNS_DIR, `live-${t.toISOString().slice(0, 10)}.jsonl`), line + "\n");
  } catch (e) {
    warn("logging")(e);
  }
}

// params.json (if present and valid) + PARAMS env. Unknown keys are
// dropped; a bad file keeps the last good params.
let fileParams: Partial<Params> & { seedPrice?: number[] } = {};
function loadParams(): Partial<Params> & { seedPrice?: number[] } {
  try {
    if (fs.existsSync(PARAMS_FILE)) {
      const raw = JSON.parse(fs.readFileSync(PARAMS_FILE, "utf8"));
      const next: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(raw)) {
        if (k in DEFAULT_PARAMS && typeof v === "number" && Number.isFinite(v)) next[k] = v;
        if (k === "seedPrice" && Array.isArray(v) && v.length === 26 && v.every((x) => typeof x === "number" && x > 0))
          next[k] = v;
      }
      fileParams = next;
    }
  } catch (e) {
    warn(`reading ${PARAMS_FILE}`)(e);
  }
  return { ...fileParams, ...ENV_PARAMS };
}

function brainFor(matchId: bigint): Brain {
  const k = String(matchId);
  let b = brains.get(k);
  if (!b) {
    const params = loadParams();
    const s = JSON.stringify(params);
    if (s !== lastParams) {
      console.log(`[${BOT_NAME}] params ${s}`);
      lastParams = s;
    }
    log({ type: "params", match: matchId, params });
    b = new Brain(params);
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

// Only the rows of matches we play in: the auction tables hold every
// match ever played (25k+ rows), which made each bid scan them all.
function subscribeMatch(conn: DbConnection, matchId: bigint) {
  const k = String(matchId);
  if (matchSubs.has(k)) return;
  const m = conn.db.matchState.id.find(matchId);
  if (m?.status.tag === "Ended") return;
  console.log(`[${BOT_NAME}] subscribing to match ${matchId}`);
  const h = conn
    .subscriptionBuilder()
    .onApplied(() => {
      for (const a of conn.db.auction.iter())
        if (a.matchId === matchId && a.status.tag === "Open") tryBid(conn, a.id, a.matchId, a.letter, a.opensAt, a.closesAt);
      queuePlays(conn, matchId);
    })
    .onError((_c: ErrorContext) => console.warn(`[${BOT_NAME}] match ${matchId} subscription error`))
    .subscribe([
      `SELECT * FROM auction WHERE match_id = ${matchId}`,
      `SELECT * FROM auction_result WHERE match_id = ${matchId}`,
      `SELECT * FROM word_play WHERE match_id = ${matchId}`,
      `SELECT * FROM match_participant WHERE match_id = ${matchId}`,
    ]);
  matchSubs.set(k, h);
}

function tryBid(
  conn: DbConnection,
  auctionId: bigint,
  matchId: bigint,
  letter: string,
  opensAt: { toMillis(): bigint },
  closesAt: { toMillis(): bigint },
) {
  if (myBotId === null) return;
  const key = String(auctionId);
  if (bidOn.has(key)) return;
  const m = conn.db.matchState.id.find(matchId);
  const balance = balanceFor(conn, matchId);
  if (!m || m.status.tag !== "Running" || balance === null) return;
  bidOn.add(key);
  const t0 = Date.now();
  const lag = t0 - Number(opensAt.toMillis()); // logged only: includes clock skew
  // Local-clock estimate of the last moment to send. The second term covers
  // auctions that reach us late (e.g. right after a reconnect).
  const close = Math.min(t0 + Number(closesAt.toMillis() - opensAt.toMillis()) - rttEst, Number(closesAt.toMillis()) - rttEst / 2);
  // Last auction: dump everything first, while the match is still running.
  if (m.bagTotal === 0) playWords(conn, matchId, true);
  const brain = brainFor(matchId);
  const budget = close - Date.now() - BID_SAFETY_MS;
  const full = brain.p.samples;
  const samples = budget >= FULL_SAMPLES_BUDGET_MS ? full : Math.max(MIN_SAMPLES, Math.floor((full * Math.max(0, budget)) / FULL_SAMPLES_BUDGET_MS));
  brain.p.samples = samples;
  let amount = 0;
  try {
    amount = brain.decideBid({
      letter: letter.charCodeAt(0) - 65,
      rack: rackFor(conn, matchId),
      balance,
      pool: unseenPool(auctionedFor(conn, matchId), startRackFor(conn, matchId)),
      bagLeft: m.bagTotal,
      firstPrice: m.auctionType.tag === "FirstPrice",
    });
  } finally {
    brain.p.samples = full;
  }
  const ms = Date.now() - t0;
  const left = close - Date.now();
  console.log(
    `[${BOT_NAME}] m${matchId} bag${m.bagTotal} '${letter}' bid ${amount} (bal ${balance}, ${ms}ms, lag ${lag}ms, ~${left}ms left${samples < full ? `, ${samples} samples` : ""})`,
  );
  const ev = { type: "bid", match: matchId, auction: auctionId, letter, bag: m.bagTotal, balance, amount, ms, lag, left, samples };
  const sent = Date.now();
  if (DRY_RUN) log({ ...ev, dry: true });
  else if (amount > 0)
    conn.reducers
      .submitBid({ auctionId, amount: BigInt(amount) })
      .then(() => {
        const rtt = Date.now() - sent;
        rttEst = 0.8 * rttEst + 0.2 * Math.min(800, rtt);
        log({ ...ev, rtt });
      })
      .catch((e) => {
        warn(`bid on ${auctionId}`)(e);
        log({ ...ev, error: e instanceof Error ? e.message : String(e) });
      });
  else log(ev);
}

// Plays wait until this batch of updates is handled, so a new auction in
// the same transaction gets its bid out first.
function queuePlays(conn: DbConnection, matchId: bigint) {
  const k = String(matchId);
  if (playsQueued.has(k)) return;
  playsQueued.add(k);
  setTimeout(() => {
    playsQueued.delete(k);
    playWords(conn, matchId, false);
  }, 0);
}

function playWords(conn: DbConnection, matchId: bigint, final: boolean) {
  const k = String(matchId);
  if (final) {
    if (finalDone.has(k)) return;
    finalDone.add(k);
  }
  const m = conn.db.matchState.id.find(matchId);
  const balance = balanceFor(conn, matchId);
  if (!m || m.status.tag !== "Running" || balance === null) return;
  const rack = rackFor(conn, matchId);
  const rk = rack.join(",");
  if (!final && lastRack.get(k) === rk) return;
  lastRack.set(k, rk);
  const words = brainFor(matchId).choosePlays(
    rack,
    unseenPool(auctionedFor(conn, matchId), startRackFor(conn, matchId)),
    m.bagTotal,
    balance,
    final || m.bagTotal === 0,
  );
  for (const word of words) {
    if (DRY_RUN) continue;
    console.log(`[${BOT_NAME}] m${matchId} plays ${word}${final ? " (final)" : ""}`);
    conn.reducers
      .submitWord({ matchId, word })
      .then(() => log({ type: "play", match: matchId, word, bag: m.bagTotal, final }))
      .catch(warn(`playing ${word}`));
  }
}

function inOpenLobby(conn: DbConnection): boolean {
  for (const x of conn.db.lobbyMember.iter()) {
    if (x.botId !== myBotId) continue;
    if (conn.db.lobby.id.find(x.lobbyId)?.status.tag === "Open") return true;
  }
  return false;
}

function joinLobby(conn: DbConnection) {
  if (myBotId === null || runningMatches(conn).length || inOpenLobby(conn)) return;
  lastJoinAt = Date.now();
  if (DRY_RUN) return;
  console.log(`[${BOT_NAME}] joining lobby`);
  conn.reducers.joinLobby({}).catch(warn("joining the lobby"));
}

function bootstrap(conn: DbConnection) {
  if (TOURNAMENT_ID && !DRY_RUN)
    conn.reducers
      .registerForTournament({ tournamentId: BigInt(TOURNAMENT_ID) })
      .then(() => console.log(`[${BOT_NAME}] registered for tournament ${TOURNAMENT_ID}`))
      .catch(warn("tournament registration"));
  for (const p of conn.db.matchParticipant.iter()) if (p.botId === myBotId) subscribeMatch(conn, p.matchId);
  joinLobby(conn);
}

function onMatchEnded(conn: DbConnection, matchId: bigint) {
  const k = String(matchId);
  if (loggedEnd.has(k)) return;
  loggedEnd.add(k);
  brains.delete(k);
  const ps = Array.from(conn.db.matchParticipant.iter())
    .filter((x) => x.matchId === matchId)
    .sort((a, b) => Number(b.score - a.score));
  const rank = ps.findIndex((x) => x.botId === myBotId) + 1;
  const me = ps[rank - 1];
  if (me) {
    console.log(`[${BOT_NAME}] match ${matchId} ended, score ${me.score}, rank ${rank}/${ps.length}`);
    log({
      type: "end",
      match: matchId,
      score: me.score,
      balance: me.balance,
      rank,
      field: ps.map((x) => ({ bot: x.botId, name: conn.db.bot.id.find(x.botId)?.name, score: x.score, balance: x.balance })),
    });
  }
  // Keep the match rows a moment for late results, then drop them.
  setTimeout(() => {
    matchSubs.get(k)?.unsubscribe();
    matchSubs.delete(k);
  }, 5000);
  void afterMatch(conn);
}

// Between matches: pull strategy updates; restart if code changed.
async function afterMatch(conn: DbConnection) {
  if (AUTOPULL) {
    try {
      const before = (await exec("git", ["rev-parse", "HEAD"])).stdout.trim();
      await exec("git", ["pull", "--ff-only", "-q", "origin", PULL_BRANCH], { timeout: 20_000 });
      const after = (await exec("git", ["rev-parse", "HEAD"])).stdout.trim();
      if (before !== after) {
        console.log(`[${BOT_NAME}] pulled ${before.slice(0, 7)}..${after.slice(0, 7)}`);
        const changed = await exec("git", ["diff", "--quiet", before, after, "--", "src", "package.json", "wordlist.txt"]).then(
          () => false,
          () => true,
        );
        if (changed && !runningMatches(conn).length) {
          console.log(`[${BOT_NAME}] code changed, restarting`);
          log({ type: "restart", from: before, to: after });
          process.exit(75);
        }
      }
    } catch (e) {
      warn("git pull")(e);
    }
  }
  joinLobby(conn);
}

function onConnect(conn: DbConnection, identity: Identity, token: string) {
  liveConn = conn;
  myIdentity = identity;
  connected = true;
  lastConnectedAt = Date.now();
  if (!DRY_RUN) fs.writeFileSync(TOKEN_PATH, token, { mode: 0o600 });
  console.log(`[${BOT_NAME}] connected as ${identity.toHexString()}`);
  // Step 1: find our bot id. Step 2: subscribe to what we need.
  conn
    .subscriptionBuilder()
    .onApplied(() => {
      if (myBotId !== null) return;
      myBotId = DRY_RUN && process.env.BOT_ID ? BigInt(process.env.BOT_ID) : (conn.db.botCredential.identity.find(myIdentity!)?.botId ?? null);
      if (myBotId === null) {
        console.error(`[${BOT_NAME}] no bot for this token; get a fresh one from /team`);
        process.exit(1);
      }
      console.log(`[${BOT_NAME}] acting as '${conn.db.bot.id.find(myBotId)?.name}' (id ${myBotId})`);
      conn
        .subscriptionBuilder()
        .onApplied(() => bootstrap(conn))
        .subscribe([
          `SELECT * FROM match_participant WHERE bot_id = ${myBotId}`,
          `SELECT * FROM lobby_member WHERE bot_id = ${myBotId}`,
          "SELECT * FROM lobby",
          "SELECT * FROM match_state",
          "SELECT * FROM my_rack",
        ]);
    })
    .subscribe(["SELECT * FROM bot_credential", "SELECT * FROM bot"]);

  const touch = () => (lastEventAt = Date.now());
  conn.db.auction.onInsert((_c, a) => {
    touch();
    if (a.status.tag === "Open") tryBid(conn, a.id, a.matchId, a.letter, a.opensAt, a.closesAt);
  });
  conn.db.auction.onUpdate(touch);
  conn.db.myRack.onInsert((_c, h) => (touch(), queuePlays(conn, h.matchId)));
  conn.db.myRack.onUpdate((_c, _o, h) => (touch(), queuePlays(conn, h.matchId)));
  conn.db.matchParticipant.onInsert((_c, p) => {
    if (p.botId === myBotId) subscribeMatch(conn, p.matchId);
  });
  conn.db.auctionResult.onInsert((_c, r) => {
    touch();
    log({ type: "result", match: r.matchId, auction: r.auctionId, letter: r.letter, winner: r.winnerBotId ?? null, paid: r.paid, mine: r.winnerBotId === myBotId });
    const b = brains.get(String(r.matchId));
    if (!b) return;
    b.observePrice(r.letter.charCodeAt(0) - 65, Number(r.paid));
    b.observeSpend(r.winnerBotId === myBotId ? Number(r.paid) : 0);
  });
  conn.db.matchState.onUpdate((_c, old, neu) => {
    if (old.status.tag !== "Ended" && neu.status.tag === "Ended" && matchSubs.has(String(neu.id))) onMatchEnded(conn, neu.id);
  });
}

DbConnection.builder()
  .withUri(HOST)
  .withDatabaseName(DB_NAME)
  .withToken(DRY_RUN ? undefined : (BOT_TOKEN ?? loadToken()))
  .withAutomaticReconnect({ minDelayMs: 500, maxDelayMs: 5000 })
  .onConnect(onConnect)
  .onAutomaticReconnect((conn) => {
    liveConn = conn;
    connected = true;
    lastConnectedAt = Date.now();
    lastEventAt = Date.now();
    console.log(`[${BOT_NAME}] reconnected`);
    log({ type: "reconnect" });
    setTimeout(() => bootstrap(conn), 1000);
  })
  .onConnectError((_c: ErrorContext, e: Error, next?: number, delay?: number) => {
    console.error(`[${BOT_NAME}] connect error: ${e.message}${next ? ` (retry ${next} in ${delay}ms)` : ""}`);
    if (next === undefined) process.exit(75);
  })
  .onDisconnect((_c: ErrorContext, e?: Error, next?: number) => {
    connected = false;
    console.log(`[${BOT_NAME}] disconnected${e ? `: ${e.message}` : ""}`);
    log({ type: "disconnect", error: e?.message });
    if (next === undefined) process.exit(75);
  })
  .build();

// Watchdog: the wrapper (scripts/live.sh) restarts us on exit 75.
setInterval(() => {
  const now = Date.now();
  if (!connected && now - lastConnectedAt > OFFLINE_MS) {
    console.error(`[${BOT_NAME}] offline for ${OFFLINE_MS / 1000}s, restarting`);
    process.exit(75);
  }
  if (!connected || !liveConn || myBotId === null) return;
  lastConnectedAt = now;
  if (runningMatches(liveConn).length) {
    if (now - lastEventAt > STALL_MS) {
      console.error(`[${BOT_NAME}] no updates for ${STALL_MS / 1000}s during a match, restarting`);
      log({ type: "stall" });
      process.exit(75);
    }
  } else if (now - lastJoinAt > 20_000) joinLobby(liveConn);
}, 5000);
