// Tournament mode for the data flywheel: every few minutes, refit the
// letter prices (seedPrice) from the tournament's own finished auctions and
// write them into params.json. The bots re-read params.json at each new
// match, so nothing restarts. No sim search: just a capped, shrunk average.
//
//   npx tsx scripts/tourney-refit.ts                # latest tournament, every 5 min
//   TOURNAMENT_ID=7 INTERVAL_S=300 npx tsx scripts/tourney-refit.ts
//   ONCE=1 DRY_RUN=1 npx tsx scripts/tourney-refit.ts   # one pass, print only
//
// Writes ./params.json and every bots/*/params.json (the clones that
// deploy/run-all.sh runs), or the comma-separated PARAMS_FILES. Other keys
// in each file are kept. Turn the flywheel loop and code auto-pull off
// while this runs, or their git pulls will fight over params.json.

import * as fs from "node:fs";
import * as path from "node:path";
import { SEED_PRICE } from "../src/engine.js";

const HOST = process.env.STDB_HOST ?? "https://maincloud.spacetimedb.com";
const DB = process.env.STDB_DB ?? "scrabblebot";
const INTERVAL_S = Number(process.env.INTERVAL_S ?? 300);
const ONCE = process.env.ONCE === "1";
const DRY_RUN = process.env.DRY_RUN === "1";
// Prior weight, in auctions: a letter seen n times moves n/(n+PRIOR) of the
// way from the current seed toward its tournament average.
const PRIOR = Number(process.env.PRIOR ?? 15);
// Never leave [LO, HI] × the code's SEED_PRICE.
const LO = Number(process.env.CAP_LO ?? 0.7);
const HI = Number(process.env.CAP_HI ?? 1.6);
// With no tournament auctions yet, fall back to the last N casual matches.
const FALLBACK_MATCHES = Number(process.env.FALLBACK_MATCHES ?? 60);

const root = path.join(path.dirname(new URL(import.meta.url).pathname), "..");

function targets(): string[] {
  if (process.env.PARAMS_FILES) return process.env.PARAMS_FILES.split(",").map((s) => s.trim()).filter(Boolean);
  const out = [path.join(root, "params.json")];
  const bots = path.join(root, "bots");
  if (fs.existsSync(bots))
    for (const d of fs.readdirSync(bots)) {
      if (d.startsWith("_")) continue; // _flywheel clone
      if (fs.existsSync(path.join(bots, d, "package.json"))) out.push(path.join(bots, d, "params.json"));
    }
  return out;
}

let tok = "";
async function sql(q: string): Promise<unknown[][]> {
  for (let attempt = 0; ; attempt++) {
    try {
      if (!tok) {
        const r = await fetch(`${HOST}/v1/identity`, { method: "POST" });
        if (!r.ok) throw new Error(`identity: HTTP ${r.status}`);
        tok = ((await r.json()) as { token: string }).token;
      }
      const r = await fetch(`${HOST}/v1/database/${DB}/sql`, { method: "POST", headers: { Authorization: `Bearer ${tok}` }, body: q });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
      return ((await r.json()) as { rows: unknown[][] }[])[0]!.rows;
    } catch (e) {
      if (attempt >= 3) throw e;
      tok = "";
      await new Promise((res) => setTimeout(res, 2000 * 2 ** attempt));
    }
  }
}

// SATS JSON: Option is [0, v] / [1, []]; plain enum is [tag, []].
const some = (v: unknown) => (v as [number, unknown])[0] === 0;

async function matchIds(): Promise<{ label: string; ids: Set<number> }> {
  // tournament: id, status, swiss_rounds_total, current_round, ...
  let tid = process.env.TOURNAMENT_ID ? Number(process.env.TOURNAMENT_ID) : undefined;
  if (tid === undefined) {
    const ts = await sql("SELECT * FROM tournament");
    tid = ts.length ? Math.max(...ts.map((t) => Number(t[0]))) : undefined;
  }
  if (tid !== undefined) {
    // tournament_match: id, tournament_id, match_id, round, phase
    const tm = await sql(`SELECT * FROM tournament_match WHERE tournament_id = ${tid}`);
    if (tm.length) return { label: `tournament ${tid}`, ids: new Set(tm.map((r) => Number(r[2]))) };
  }
  // match_state: id, status, ...; status 2 = Ended
  const ms = await sql("SELECT * FROM match_state");
  const ended = ms.filter((r) => (r[1] as [number])[0] === 2).map((r) => Number(r[0])).sort((a, b) => a - b);
  return { label: `last ${FALLBACK_MATCHES} matches (no tournament auctions yet${tid !== undefined ? ` in ${tid}` : ""})`, ids: new Set(ended.slice(-FALLBACK_MATCHES)) };
}

function readJson(f: string): Record<string, unknown> {
  try {
    return JSON.parse(fs.readFileSync(f, "utf8"));
  } catch {
    return {};
  }
}

async function tick() {
  const { label, ids } = await matchIds();
  if (!ids.size) return console.log(`[refit] no matches yet`);
  const minId = Math.min(...ids);
  // auction_result: auction_id, match_id, letter, winner_bot_id, paid, closed_at
  const rows = (await sql(`SELECT * FROM auction_result WHERE match_id >= ${minId}`)).filter((r) => ids.has(Number(r[1])) && some(r[3]));
  const sum = new Array(26).fill(0);
  const n = new Array(26).fill(0);
  for (const r of rows) {
    const c = String(r[2]).charCodeAt(0) - 65;
    if (c < 0 || c > 25) continue;
    sum[c] += Number(r[4]);
    n[c]++;
  }
  const files = targets();
  const base = readJson(files[0]!);
  const prev = Array.isArray(base.seedPrice) && base.seedPrice.length === 26 ? (base.seedPrice as number[]) : SEED_PRICE;
  const seed = prev.map((p, c) => {
    const w = n[c] / (n[c] + PRIOR);
    const fit = n[c] ? w * (sum[c] / n[c]) + (1 - w) * p : p;
    const capped = Math.min(HI * SEED_PRICE[c]!, Math.max(LO * SEED_PRICE[c]!, fit));
    return Math.max(1, Math.round(capped * 10) / 10);
  });
  const moved = seed.map((s, c) => s - prev[c]!).reduce((a, x) => a + Math.abs(x), 0);
  console.log(`[refit] ${new Date().toISOString()} ${label}: ${rows.length} auctions; seed moved ${moved.toFixed(1)} coins in total`);
  console.log(`[refit] seedPrice ${JSON.stringify(seed)}`);
  if (DRY_RUN) return;
  for (const f of files) {
    const cur = readJson(f);
    const next = { ...cur, seedPrice: seed };
    const tmp = `${f}.tmp-${process.pid}`;
    fs.writeFileSync(tmp, JSON.stringify(next, null, 2) + "\n");
    fs.renameSync(tmp, f); // atomic: a bot never reads a half-written file
  }
  console.log(`[refit] wrote ${files.join(", ")}`);
}

for (;;) {
  try {
    await tick();
  } catch (e) {
    console.error(`[refit] failed: ${e instanceof Error ? e.message : e}`);
  }
  if (ONCE) break;
  await new Promise((res) => setTimeout(res, INTERVAL_S * 1000));
}
