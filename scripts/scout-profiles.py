#!/usr/bin/env python3
"""Per-opponent bid profiles from the public tables, for the `scout:<name>` sim seat.

Usage: python3 scripts/scout-profiles.py [min_match_id] > data/scout-profiles.json

market[letter][phase]: median price paid (6-bot matches), phase = thirds of the auctions.
bots[name][class][phase]: that bot's median winning bid / market, class = vowel|cons|rare.
Winning bids come from auction_top_bid_archive, so they're the bot's bids when it
wanted the letter most (upward-biased vs its typical bid).
"""
import json, sys, statistics as st, collections, urllib.request

HOST = "https://maincloud.spacetimedb.com"
MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 900

def post(url, body=b"", token=None):
    req = urllib.request.Request(url, data=body, method="POST")
    if token: req.add_header("Authorization", f"Bearer {token}")
    return json.load(urllib.request.urlopen(req))

tok = post(f"{HOST}/v1/identity")["token"]
q = lambda s: post(f"{HOST}/v1/database/scrabblebot/sql", s.encode(), tok)[0]["rows"]
names = {r[0]: r[1] for r in q("SELECT id, name FROM bot")}
tab = collections.defaultdict(set)
for m, b in q(f"SELECT match_id, bot_id FROM match_participant WHERE match_id > {MIN}"): tab[m].add(b)
top = {r[0]: r[2] for r in q(f"SELECT * FROM auction_top_bid_archive WHERE match_id > {MIN}")}
by = collections.defaultdict(list)
for a, m, l, w, p in q(f"SELECT auction_id, match_id, letter, winner_bot_id, paid FROM auction_result WHERE match_id > {MIN}"):
    if len(tab[m]) == 6: by[m].append((a, l, w[1] if w[0] == 0 else None, p))
cls = lambda l: "rare" if l in "JQXZ" else ("vowel" if l in "AEIOU" else "cons")
price = collections.defaultdict(list); obs = []
for m, au in by.items():
    au.sort(); n = len(au)
    for i, (a, l, w, p) in enumerate(au):
        ph = min(2, 3 * i // n); price[(l, ph)].append(p)
        if w is not None and a in top: obs.append((w, l, ph, top[a]))
A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
mk = {k: st.median(v) for k, v in price.items()}
market = [[mk.get((l, ph), 10) for ph in range(3)] for l in A]
R = collections.defaultdict(lambda: collections.defaultdict(list))
for w, l, ph, b in obs: R[w][(cls(l), ph)].append(b / mk[(l, ph)])
bots = {}
for w, d in R.items():
    allr = [x for v in d.values() for x in v]
    if len(allr) < 300 or w not in names: continue
    base = st.median(allr)
    bots[names[w]] = {c: [round(st.median(d[(c, ph)]), 3) if len(d[(c, ph)]) >= 20 else round(base, 3) for ph in range(3)]
                      for c in ("vowel", "cons", "rare")}
json.dump({"minMatch": MIN, "market": market, "bots": bots}, sys.stdout, indent=1)
