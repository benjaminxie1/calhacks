"""Live error injection (R26): a judge picks an error and a store whose statement hasn't
arrived yet; that landlord's statement lands on the next replay day carrying the error,
with an answer-key row."""
from __future__ import annotations

import json
import random
from datetime import date

from bp.calendar import END
from bp.sources.store import SourceStore
from bp.world.generate import statement_txn
from bp.world.inject import answer_rows, plant

TYPES = {"share": "Share on occupied area, not GLA", "admin_fee": "Management fee at 15% of everything",
         "capital": "Roof replacement passed through", "tax": "Taxes above the county bill",
         "estimates": "One estimate payment not credited", "gross_up": "Gross-up the lease doesn't allow"}


def pending_leases(store: SourceStore, on: date) -> list[str]:
    """Stores whose statement hasn't arrived and carries no planted error, so a live one can be added cleanly."""
    plan = json.loads((store.dir / "plan.json").read_text()) if (store.dir / "plan.json").exists() else {}
    return sorted(s.lease_id for s in store.statements if s.received_on >= on and s.lease_id not in plan
                  and not s.statement_id.endswith("-LIVE"))


def build(store: SourceStore, kind: str, lease_id: str, on: date, seq: int) -> dict:
    if kind not in TYPES:
        raise ValueError(f"injectable types: {sorted(TYPES)}")
    if on > END:
        raise ValueError(f"the replay ends {END}; reset the replay to inject again")
    if lease_id not in store.lease_by_id:
        raise ValueError(f"unknown lease {lease_id}")
    if lease_id not in pending_leases(store, on):
        raise ValueError(f"{lease_id}'s statement has already arrived or already carries a planted error; pick one of "
                         f"{', '.join(pending_leases(store, on)[:8])}")
    lease = store.lease(lease_id)
    if kind == "admin_fee" and lease.admin_fee_pct is None:
        raise ValueError(f"{lease_id}'s lease has no admin fee clause; pick another store")
    if kind == "gross_up" and lease.gross_up_pct is not None:
        raise ValueError(f"{lease_id}'s lease allows a gross-up; pick a store whose lease doesn't")
    sid = f"STMT-{lease_id}-2025"
    truth = next(s for s in store.truth_statements if s.statement_id == sid).model_copy(update={"received_on": on})
    if kind == "gross_up" and truth.occupancy_pct >= 95:
        raise ValueError(f"{lease_id}'s center is {truth.occupancy_pct}% occupied; a gross-up to 95% adds nothing")
    st, exp = plant(truth, truth, lease, [kind], random.Random(seq), lease.monthly_estimate_2025)
    n = {"n": 0}

    def bid(k):
        n["n"] += 1
        return f"BRK-LIVE-{seq:03d}"

    old_book = next(t for t in store.booking if t.source_ref == sid)
    old_truth = next(t for t in store.truth if t.source_ref == sid)
    return {
        "statements": [st.model_dump(mode="json")],
        "booking": [statement_txn(st, old_book.txn_id).model_dump(mode="json")],
        "truth": [statement_txn(truth, old_truth.txn_id).model_dump(mode="json")],
        "answer_key": [r.model_dump(mode="json") for r in answer_rows(lease, st, [kind], exp, bid, live=True)],
    }
