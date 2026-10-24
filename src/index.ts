// Live bot. Based on clockworklabs/scrabblebot bot-starter, with the
// strategy from ./engine.ts.
//
// First run:  BOT_NAME=<name> BOT_TOKEN=<token from /team> npm start
// Later runs: BOT_NAME=<name> npm start   (token saved to .token-<name>)
// Unattended: BOT_NAME=<name> scripts/live.sh  (restarts, pulls updates)
// Tournaments: registers for any open one automatically (TOURNAMENT_ID=<id>
//             limits it to one, NO_TOURNAMENTS=1 disables)
// Tuning:     params.json (re-read at each new match), then PARAMS='{"lam0":0.3}'
//             on top. Both are partial engine Params; params.json may also
//             carry "seedPrice": [26 numbers].
// Updates:    LIVE_AUTOPULL=1 pulls origin/scrabblebot after each match; if
//             code changed the process exits 75 so the wrapper restarts it.
//
// Team play: ALLIES=name1,name2 (allied bots' names; may include our own)
//             makes allies join the casual lobby together.
//             TEAM_RELAY=https://ntfy.sh/<secret topic> (same for all allies)
//             shares starting tiles (src/relay.ts), so allies in a match
//             don't bid against each other (src/team.ts).
//             TEAM_CAPTAIN=<name>: allies play support for that bot.
//             With no ally in a match, play is unchanged.
//
// Exit codes: 1 = bad token (don't restart), 75 = restart me.

import { DbConnection, type ErrorContext } from "./module_bindings/index.js";
import { Identity } from "spacetimedb";
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { promisify } from "node:util";
import { Brain, DEFAULT_PARAMS, countsOf, emptyCounts, unseenPool, type Counts, type Params } from "./engine.js";
import { BAG } from "./dict.js";
import { Relay } from "./relay.js";
import { DEFAULT_TEAM, teamBid, type Mate, type TeamConfig } from "./team.js";

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
const ALLY_NAMES = new Set((process.env.ALLIES ?? "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean));
// Free while an ally is mid-match: wait this long for it before joining
// the lobby alone (a match runs ~75 s).
const ALLY_WAIT_MS = Number(process.env.ALLY_WAIT_MS ?? 90_000);
const START_BALANCE = 100;
const TEAM_RELAY = process.env.TEAM_RELAY;
const TEAM_CAPTAIN = process.env.TEAM_CAPTAIN?.trim().toLowerCase();
const TEAM_CFG: Partial<TeamConfig> = process.env.TEAM ? JSON.parse(process.env.TEAM) : {};

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
const allyIds = new Set<bigint>();
let freeSince = Date.now();
let relay: Relay | null = null;
const subApplied = new Set<string>(); // matches whose rows we hold

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
      subApplied.add(k);
      announceStart(conn, matchId);
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
  let team: Record<string, unknown> | undefined;
  const captain = allyIds.size ? captainId() : undefined;
  const mates = allyIds.size && (relay || captain !== undefined) ? matesFor(conn, matchId, opensAt.toMillis(), captain !== undefined) : undefined;
  if (mates) {
    if (mates.mates.length >= 2) {
      const solo = amount;
      const li = letter.charCodeAt(0) - 65;
      const cfg = { ...DEFAULT_TEAM, ...TEAM_CFG, captain };
      // Without the captain's starting tiles, a letter the captain wants can
      // look useless to it, so a supporter's drain bid would raise its price.
      if (!mates.shared) cfg.drain = 0;
      const r = teamBid(brain.d, Number(myBotId), amount, brain.lambda(balance, m.bagTotal), li, mates.mates, mates.pool, m.bagTotal, Number(auctionId), cfg, brain.price[li], balance);
      amount = r.bid;
      if (r.decision) team = { solo, bidder: r.decision.bidder, sitOut: r.decision.sitOut, tiles: mates.shared, shared: r.decision.shared.map((x) => +x.toFixed(1)) };
    }
  }
  const ms = Date.now() - t0;
  const left = close - Date.now();
  console.log(
    `[${BOT_NAME}] m${matchId} bag${m.bagTotal} '${letter}' bid ${amount} (bal ${balance}, ${ms}ms, lag ${lag}ms, ~${Math.round(left)}ms left${samples < full ? `, ${samples} samples` : ""}${team ? `, team: ${team.sitOut ? `ally ${team.bidder} bids` : "we bid"}` : ""})`,
  );
  const ev = { type: "bid", match: matchId, auction: auctionId, letter, bag: m.bagTotal, balance, amount, ms, lag, left, samples, team };
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

function captainId(): number | undefined {
  if (!TEAM_CAPTAIN || !liveConn) return undefined;
  for (const b of liveConn.db.bot.iter()) if (b.name.toLowerCase() === TEAM_CAPTAIN) return Number(b.id);
  return undefined;
}

// Us and our allies in this match, as every ally sees them when the
// auction opened: starting tiles (from the relay) + tiles won - tiles
// played, balance rebuilt from the results and the plays made before then
// (so a play racing the auction can't make allies disagree). Also the pool
// shared by all of them. No mates until every ally in the match holds
// everyone's starting tiles, except in captain mode, which then works
// from public tiles alone (supporters only need to know what the captain
// can use, and the sim shows that is enough).
function matesFor(conn: DbConnection, matchId: bigint, opensAt: bigint, captainMode = false): { mates: Mate[]; pool: Counts; shared: boolean } {
  const known = new Map<bigint, { won: Counts; played: Counts; balance: number }>();
  const side = (id: bigint) => {
    let x = known.get(id);
    if (!x) known.set(id, (x = { won: emptyCounts(), played: emptyCounts(), balance: START_BALANCE }));
    return x;
  };
  for (const p of conn.db.matchParticipant.iter()) if (p.matchId === matchId) side(p.botId);
  for (const r of conn.db.auctionResult.iter()) {
    if (r.matchId !== matchId || r.winnerBotId == null || r.closedAt.toMillis() > opensAt) continue;
    const x = side(r.winnerBotId);
    x.won[r.letter.charCodeAt(0) - 65]++;
    x.balance -= Number(r.paid);
  }
  for (const w of conn.db.wordPlay.iter()) {
    if (w.matchId !== matchId || w.playedAt.toMillis() >= opensAt) continue;
    const x = side(w.botId);
    const c = countsOf(w.word);
    for (let i = 0; i < 26; i++) x.played[i] += c[i]!;
    x.balance += Number(w.totalReward);
  }
  const team = [...known.keys()].filter((id) => id === myBotId || allyIds.has(id));
  const ready = team.length < 2 ? undefined : relay?.ready(String(matchId), team.map(String));
  const starts = ready ?? (captainMode && team.length >= 2 ? new Map<string, string>() : undefined);
  if (!starts) return { mates: [], pool: emptyCounts(), shared: false };
  // Hidden tiles out of the bag: allies' starting racks, plus whatever
  // the other bots' plays have exposed.
  const gone = emptyCounts();
  const mates: Mate[] = [];
  for (const [id, x] of known) {
    const s = team.includes(id) ? countsOf(starts.get(String(id)) ?? "") : undefined;
    const k = emptyCounts();
    for (let i = 0; i < 26; i++) {
      if (s) k[i] = Math.max(0, s[i]! + x.won[i]! - x.played[i]!);
      // Hidden tiles we know: shared starting tiles, else what plays exposed.
      gone[i] += s && starts.has(String(id)) ? s[i]! : Math.max(0, x.played[i]! - x.won[i]!);
    }
    if (s) mates.push({ id: Number(id), known: k, balance: x.balance });
  }
  // Minus letters revealed before this auction plus this one.
  for (const a of conn.db.auction.iter())
    if (a.matchId === matchId && a.opensAt.toMillis() <= opensAt) gone[a.letter.charCodeAt(0) - 65]++;
  const pool = emptyCounts();
  for (let i = 0; i < 26; i++) pool[i] = Math.max(0, BAG[i]! - gone[i]!);
  return { mates, pool, shared: ready !== undefined };
}

// Tell allies our starting tiles for this match.
function announceStart(conn: DbConnection, matchId: bigint) {
  // Our starting rack is rebuilt from the match's results and plays.
  if (!relay || !subApplied.has(String(matchId))) return;
  const s = startRackFor(conn, matchId);
  let tiles = "";
  for (let i = 0; i < 26; i++) tiles += String.fromCharCode(65 + i).repeat(s[i]!);
  if (tiles) relay.start(String(matchId), tiles);
}

// Ally names -> bot ids. Returns the newly found ids. An ally that
// registers after we start is picked up by bot.onInsert.
function resolveAllies(conn: DbConnection): bigint[] {
  if (!ALLY_NAMES.size || myBotId === null) return [];
  const found: bigint[] = [];
  for (const b of conn.db.bot.iter())
    if (b.id !== myBotId && !allyIds.has(b.id) && ALLY_NAMES.has(b.name.toLowerCase())) {
      allyIds.add(b.id);
      found.push(b.id);
    }
  if (found.length) {
    console.log(`[${BOT_NAME}] allies: ${[...allyIds].map((id) => `${conn.db.bot.id.find(id)?.name} (${id})`).join(", ")}`);
    log({ type: "allies", ids: [...allyIds] });
  }
  return found;
}

// Where an ally is: its lobby seat and match rows.
function watchBots(conn: DbConnection, ids: bigint[]) {
  if (!ids.length) return;
  conn.subscriptionBuilder().subscribe([
    ...ids.map((id) => `SELECT * FROM match_participant WHERE bot_id = ${id}`),
    ...ids.map((id) => `SELECT * FROM lobby_member WHERE bot_id = ${id}`),
  ]);
}

// Wait for allies before joining alone? Join at once when an ally already
// waits in the open lobby; wait (up to ALLY_WAIT_MS) while one is mid-match.
function waitForAllies(conn: DbConnection): boolean {
  if (!allyIds.size || TOURNAMENT_ID) return false;
  for (const x of conn.db.lobbyMember.iter())
    if (allyIds.has(x.botId) && conn.db.lobby.id.find(x.lobbyId)?.status.tag === "Open") return false;
  if (Date.now() - freeSince > ALLY_WAIT_MS) return false;
  for (const p of conn.db.matchParticipant.iter())
    if (allyIds.has(p.botId) && conn.db.matchState.id.find(p.matchId)?.status.tag === "Running") return true;
  return false;
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

// In a match that is starting or running: the server refuses joinLobby
// then, so don't send it. (runningMatches misses the Lobby phase.)
function inMatch(conn: DbConnection): boolean {
  for (const p of conn.db.matchParticipant.iter()) {
    if (p.botId !== myBotId) continue;
    const st = conn.db.matchState.id.find(p.matchId)?.status.tag;
    if (st === "Lobby" || st === "Running") return true;
  }
  return false;
}

function inOpenLobby(conn: DbConnection): boolean {
  for (const x of conn.db.lobbyMember.iter()) {
    if (x.botId !== myBotId) continue;
    if (conn.db.lobby.id.find(x.lobbyId)?.status.tag === "Open") return true;
  }
  return false;
}

function joinLobby(conn: DbConnection) {
  if (myBotId === null || restartPending || inMatch(conn) || inOpenLobby(conn)) return;
  // A join we just sent may have started a match we haven't heard of yet.
  if (Date.now() - lastJoinAt < 3000) return;
  if (waitForAllies(conn)) return;
  lastJoinAt = Date.now();
  if (DRY_RUN) return;
  console.log(`[${BOT_NAME}] joining lobby`);
  conn.reducers.joinLobby({}).catch(warn("joining the lobby"));
}

// Sign up for every tournament that opens registration (or only
// TOURNAMENT_ID if set). Set NO_TOURNAMENTS=1 to opt out.
const registering = new Set<string>();
function registerTournaments(conn: DbConnection) {
  if (DRY_RUN || myBotId === null || process.env.NO_TOURNAMENTS === "1") return;
  for (const t of conn.db.tournament.iter()) {
    const k = String(t.id);
    if (t.status.tag !== "Registration" || registering.has(k)) continue;
    if (TOURNAMENT_ID && k !== TOURNAMENT_ID) continue;
    if (Array.from(conn.db.tournamentEntry.iter()).some((e) => e.tournamentId === t.id && e.botId === myBotId)) continue;
    registering.add(k);
    conn.reducers
      .registerForTournament({ tournamentId: t.id })
      .then(() => {
        console.log(`[${BOT_NAME}] registered for tournament ${k} (${t.auctionType.tag})`);
        log({ type: "tournament_registered", tournament: t.id, auctionType: t.auctionType.tag });
      })
      .catch((e) => {
        registering.delete(k);
        warn(`registering for tournament ${k}`)(e);
      });
  }
}

function bootstrap(conn: DbConnection) {
  registerTournaments(conn);
  for (const p of conn.db.matchParticipant.iter()) if (p.botId === myBotId) subscribeMatch(conn, p.matchId);
  joinLobby(conn);
}

function onMatchEnded(conn: DbConnection, matchId: bigint) {
  const k = String(matchId);
  if (loggedEnd.has(k)) return;
  loggedEnd.add(k);
  brains.delete(k);
  relay?.forget(k);
  freeSince = Date.now();
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
        if (changed) {
          // Restart only while free: the watchdog exits once we're in no
          // match or open lobby, and we stop joining lobbies until then.
          console.log(`[${BOT_NAME}] code changed, restarting when free`);
          log({ type: "restart", from: before, to: after });
          restartPending = true;
          restartWhenFree(conn);
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
      resolveAllies(conn);
      watchBots(conn, [...allyIds]);
      if (TEAM_RELAY && !DRY_RUN) relay = new Relay(TEAM_RELAY, String(myBotId), (s) => console.warn(`[${BOT_NAME}] ${s}`));
      conn
        .subscriptionBuilder()
        .onApplied(() => bootstrap(conn))
        .subscribe([
          `SELECT * FROM match_participant WHERE bot_id = ${myBotId}`,
          `SELECT * FROM lobby_member WHERE bot_id = ${myBotId}`,
          "SELECT * FROM lobby",
          "SELECT * FROM match_state",
          "SELECT * FROM my_rack",
          "SELECT * FROM tournament",
          `SELECT * FROM tournament_entry WHERE bot_id = ${myBotId}`,
        ]);
    })
    .subscribe(["SELECT * FROM bot_credential", "SELECT * FROM bot"]);

  const touch = () => (lastEventAt = Date.now());
  conn.db.auction.onInsert((_c, a) => {
    touch();
    if (a.status.tag === "Open") tryBid(conn, a.id, a.matchId, a.letter, a.opensAt, a.closesAt);
  });
  conn.db.auction.onUpdate(touch);
  conn.db.myRack.onInsert((_c, h) => (touch(), setTimeout(() => announceStart(conn, h.matchId), 0), queuePlays(conn, h.matchId)));
  conn.db.myRack.onUpdate((_c, _o, h) => (touch(), queuePlays(conn, h.matchId)));
  conn.db.tournament.onInsert(() => registerTournaments(conn));
  conn.db.tournament.onUpdate(() => registerTournaments(conn));
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
    // An ally just came free: maybe join the lobby with it.
    else if (old.status.tag === "Running" && neu.status.tag !== "Running" && allyIds.size) setTimeout(() => joinLobby(conn), 300);
  });
  conn.db.bot.onInsert(() => watchBots(conn, resolveAllies(conn)));
  conn.db.bot.onUpdate(() => watchBots(conn, resolveAllies(conn)));
  conn.db.lobbyMember.onInsert((_c, x) => {
    if (allyIds.has(x.botId)) joinLobby(conn);
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

let restartPending = false;
function restartWhenFree(conn: DbConnection) {
  if (!restartPending || inMatch(conn) || inOpenLobby(conn)) return;
  console.log(`[${BOT_NAME}] restarting for new code`);
  process.exit(75);
}

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
  } else if (restartPending) restartWhenFree(liveConn);
  else if (now - lastJoinAt > 20_000) joinLobby(liveConn);
}, 5000);
