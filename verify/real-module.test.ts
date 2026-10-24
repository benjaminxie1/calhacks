// The calhacks `scrabblebot` branch's Brain (benjaminxie1/calhacks,
// src/engine.ts) against the real module: whole matches through the
// reducers, to check every word it sends is accepted, including the last
// auction's play-out, and to compare its score with that branch's sim.
// Needs that branch checked out at CALHACKS_DIR (default ../../calhacks-scrabblebot
// next to this repo); skipped when it isn't there.

import * as fs from "node:fs";
import { TimeDuration } from "spacetimedb";
import { expect, test } from "vitest";
import { AUCTION_DURATION_MS } from "../game";
import { submitBid, submitWord } from "../index";
import {
  call,
  currentAuction,
  getMatch,
  newDb,
  participant,
  simIds,
  startMatch,
  teamWithBot,
  tick,
  VICKREY,
} from "./harness";

const DIR =
  process.env.CALHACKS_DIR ?? new URL("../../../../calhacks-scrabblebot", import.meta.url).pathname;
const ENGINE = `${DIR}/src/engine.ts`;

test.skipIf(!fs.existsSync(ENGINE))(
  "the calhacks Brain wins real matches against the seeded sims, every word accepted",
  async () => {
    const { Brain, unseenPool } = await import(/* @vite-ignore */ ENGINE);
    const N = 12;
    let score = 0;
    let wins = 0;
    let finalPlays = 0;

    for (let n = 0; n < N; n++) {
      const db = newDb();
      db.rng.setSeed(BigInt(n + 1));
      const me = teamWithBot(db, "owner", "Me");
      const matchId = startMatch(db, VICKREY, [me, ...simIds(db).slice(0, 5)]);
      const brain = new Brain({}, n + 1);
      const counts = (pick: (letter: string) => boolean, rows: Iterable<{ letter: string }>) => {
        const c = new Int16Array(26);
        for (const r of rows) if (pick(r.letter)) c[r.letter.charCodeAt(0) - 65]++;
        return c;
      };
      const rackNow = () => {
        const c = new Int16Array(26);
        for (const h of db.db.holding.holdingByMatchBot.filter([matchId, me])) c[h.letter.charCodeAt(0) - 65] += h.count;
        return c;
      };
      const myStart = rackNow();

      while (getMatch(db, matchId).status.tag === "Running") {
        const m = getMatch(db, matchId);
        const a = currentAuction(db, matchId);
        const results = [...db.db.auctionResult.iter()].filter((r) => r.matchId === matchId);
        const auctioned = counts(() => true, [...results, a]);
        // Opponents' starting tiles their plays have shown: played - won.
        const revealed = new Int16Array(26);
        for (const p of db.db.matchParticipant.mpByMatch.filter(matchId)) {
          if (p.botId === me) continue;
          const won = counts(() => true, results.filter((r) => r.winnerBotId === p.botId));
          const played = new Int16Array(26);
          for (const w of db.db.wordPlay.iter())
            if (w.matchId === matchId && w.botId === p.botId) for (const ch of w.word) played[ch.charCodeAt(0) - 65]++;
          for (let c = 0; c < 26; c++) revealed[c] += Math.max(0, played[c]! - won[c]!);
        }
        const pool = unseenPool(auctioned, myStart, revealed);
        const balance = () => Number(participant(db, matchId, me).balance);
        const final = m.bagTotal === 0;
        // Throws (failing the test) if the module refuses a word.
        for (const word of brain.choosePlays(rackNow(), pool, m.bagTotal, balance(), final) as string[]) {
          call(db, "Me", (tx) => submitWord(tx, { matchId, word }));
          if (final) finalPlays++;
        }
        const bid = brain.decideBid({
          letter: a.letter.charCodeAt(0) - 65,
          rack: rackNow(),
          balance: balance(),
          pool,
          bagLeft: m.bagTotal,
          firstPrice: false,
        });
        if (bid > 0) call(db, "Me", (tx) => submitBid(tx, { auctionId: a.id, amount: BigInt(bid) }));
        db.clock.advance(new TimeDuration(AUCTION_DURATION_MS * 1000n));
        tick(db, matchId);
        const r = db.db.auctionResult.auctionId.find(a.id);
        if (r) brain.observePrice(a.letter.charCodeAt(0) - 65, Number(r.paid));
        brain.observeSpend(r && r.winnerBotId === me ? Number(r.paid) : 0);
      }

      const rows = [...db.db.matchParticipant.mpByMatch.filter(matchId)];
      const mine = rows.find((p) => p.botId === me)!;
      score += Number(mine.score);
      if (rows.every((p) => p.score <= mine.score)) wins++;
    }

    process.stdout.write(`real module, calhacks Brain: avg score ${(score / N).toFixed(1)}, wins ${wins}/${N}, final-auction plays ${finalPlays}\n`);
    expect(wins).toBe(N);
  },
  300_000,
);
