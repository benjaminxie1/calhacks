"""Pull real retail leases from SEC EDGAR through our Apify Actor
(`actors/edgar-lease-finder`), so the lease parser is tested on leases a
landlord's lawyer actually wrote, not just our synthetic one.

    python -m cam.edgar --max 5      # writes data/edgar/<accession>.json
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

from .run import ROOT, load_env

ACTOR = os.environ.get("CAM_EDGAR_ACTOR", "benjaminxie1~edgar-lease-finder")


def find_leases(max_results: int = 5, include_text: bool = True, query: str | None = None) -> list[dict]:
    token = os.environ["APIFY_TOKEN"]
    url = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
    body = {"maxResults": max_results, "includeText": include_text,
            "userAgent": os.environ.get("SEC_USER_AGENT", "CAM audit agent (CalHacks) contact@example.com")}
    if query:
        body["query"] = query
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())


def main() -> None:
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, default=5)
    p.add_argument("--query", default=None)
    a = p.parse_args()
    out = ROOT / "data" / "edgar"
    out.mkdir(parents=True, exist_ok=True)
    for item in find_leases(a.max, True, a.query):
        (out / f"{item['accession']}.json").write_text(json.dumps(item, indent=2))
        print(f"{item['company']} ({item['file_date']}): {', '.join(item['clauses'])}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
