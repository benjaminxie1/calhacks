// Team relay: allies tell each other their hidden starting tiles, once per
// match, through an ntfy-style pub/sub topic (TEAM_RELAY=https://ntfy.sh/<topic>):
// POST <url> publishes a message, GET <url>/json streams them as JSON lines.
// Everything else allies need is public game state.
//
// A message: {m: match id, b: bot id, r: starting tiles ("AEIRS"),
//             h: bot ids whose tiles the sender holds for this match}.
// Team bidding switches on for a match only once every ally in it holds
// everyone's tiles, so all of them agree on who bids.

export interface RelayMsg {
  m: string;
  b: string;
  r: string;
  h: string[];
}

export class Relay {
  private racks = new Map<string, Map<string, string>>(); // match -> bot -> tiles
  private holds = new Map<string, Map<string, Set<string>>>(); // match -> bot -> held ids
  private sent = new Map<string, number>(); // match -> size of the hold set last sent
  private mine = new Map<string, string>();

  constructor(
    private url: string,
    private me: string,
    private say: (s: string) => void,
  ) {
    void this.listen();
  }

  private async listen() {
    for (;;) {
      try {
        const res = await fetch(`${this.url}/json?since=10m`);
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        const dec = new TextDecoder();
        let buf = "";
        for await (const chunk of res.body as unknown as AsyncIterable<Uint8Array>) {
          buf += dec.decode(chunk, { stream: true });
          let nl;
          while ((nl = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, nl);
            buf = buf.slice(nl + 1);
            this.onLine(line);
          }
        }
      } catch (e) {
        this.say(`relay stream: ${e instanceof Error ? e.message : e}`);
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  private onLine(line: string) {
    try {
      const ev = JSON.parse(line);
      if (ev.event !== "message") return;
      const msg = JSON.parse(ev.message) as RelayMsg;
      if (typeof msg.m !== "string" || typeof msg.b !== "string" || !/^[A-Z]{0,12}$/.test(msg.r) || !Array.isArray(msg.h)) return;
      this.store(msg);
    } catch {
      // not ours
    }
  }

  private store(msg: RelayMsg) {
    if (!this.racks.has(msg.m)) this.racks.set(msg.m, new Map());
    if (!this.holds.has(msg.m)) this.holds.set(msg.m, new Map());
    this.racks.get(msg.m)!.set(msg.b, msg.r);
    const h = this.holds.get(msg.m)!;
    const s = h.get(msg.b) ?? new Set<string>();
    for (const x of msg.h) s.add(String(x));
    h.set(msg.b, s);
    if (msg.b !== this.me) this.publish(msg.m);
  }

  // Announce our starting tiles for a match (first call wins).
  start(match: string, tiles: string) {
    if (this.mine.has(match)) return;
    this.mine.set(match, tiles);
    this.store({ m: match, b: this.me, r: tiles, h: [this.me] });
    this.publish(match);
  }

  // (Re)send when we hold more allies' tiles than we last told them.
  private publish(match: string) {
    const tiles = this.mine.get(match);
    if (tiles === undefined) return;
    const held = [...(this.racks.get(match)?.keys() ?? [])];
    this.holds.get(match)?.set(this.me, new Set(held));
    if ((this.sent.get(match) ?? 0) >= held.length) return;
    this.sent.set(match, held.length);
    const body: RelayMsg = { m: match, b: this.me, r: tiles, h: held };
    fetch(this.url, { method: "POST", body: JSON.stringify(body) }).catch((e) =>
      this.say(`relay post: ${e instanceof Error ? e.message : e}`),
    );
  }

  // Starting tiles of every bot in `bots`, if every one of them has told
  // us it holds all of them too; otherwise undefined (play solo).
  ready(match: string, bots: string[]): Map<string, string> | undefined {
    const r = this.racks.get(match);
    const h = this.holds.get(match);
    if (!r || !h) return undefined;
    for (const b of bots) {
      if (!r.has(b)) return undefined;
      const s = h.get(b);
      if (!s || !bots.every((x) => s.has(x))) return undefined;
    }
    return r;
  }

  forget(match: string) {
    this.racks.delete(match);
    this.holds.delete(match);
    this.sent.delete(match);
    this.mine.delete(match);
  }
}
