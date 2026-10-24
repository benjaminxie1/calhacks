"""Evidence agent: checks the landlord's tax pass-through against the county's
own tax bill for the parcel. Live data comes from our Apify Actor; without an
APIFY_TOKEN it falls back to a cached result, and every report says which."""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .models import TaxRecord

DEFAULT_ACTOR = os.environ.get("CAM_APIFY_ACTOR", "benjaminxie1~county-tax-lookup")


def from_apify(apn: str, county: str, actor: str = DEFAULT_ACTOR) -> TaxRecord:
    token = os.environ["APIFY_TOKEN"]
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?token={token}"
    body = json.dumps({"apn": apn, "county": county}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        items = json.loads(resp.read())
    if not items:
        raise RuntimeError(f"Apify Actor returned no record for APN {apn}")
    item = items[0]
    return TaxRecord(source="apify", apn=apn, county=county, tax_year=item["tax_year"],
                     total_tax_billed=float(item["total_tax_billed"]), url=item.get("url"),
                     fetched_at=item.get("fetched_at") or datetime.now(timezone.utc).isoformat())


def from_cache(path: Path) -> TaxRecord:
    raw = json.loads(path.read_text())
    return TaxRecord(**{k: v for k, v in raw.items() if k in TaxRecord.model_fields})


def lookup(apn: str, county: str, cache: Path | None) -> TaxRecord | None:
    if os.environ.get("APIFY_TOKEN"):
        return from_apify(apn, county)
    return from_cache(cache) if cache and cache.exists() else None
