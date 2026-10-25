"""Deterministic matcher (R12-R14). Creates breaks; does nothing else.

Each business day, every landlord statement received that day is recomputed against
the lease abstract (share, cap, admin fee, capital exclusion, gross-up), the county's
tax record for the parcel, and the estimate payments AP made and the bank cleared.
Each way the statement overcharges the lease becomes one break, keyed by which
sources disagree on which field. A printed total within $1 of the line-by-line
recomputation is rounding: recorded and auto-cleared, never a case.

Verify reruns the same check for one break and subtracts the credits already posted
for it: the break is fixed when nothing of its overcharge is left.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from bp import calculators, cam
from bp.agents.events import emit, jsonable
from bp.db import breaks
from bp.ledger import service as ledger
from bp.money import D
from bp.sources import Sources

ACTOR = "matcher"
K_SHARE = "statement-lease:tenant_share"
K_FEE = "statement-lease:admin_fee"
K_CAP = "statement-lease:controllable_cap"
K_CAPITAL = "statement-lease:capital_exclusion"
K_TAX = "statement-county:property_tax"
K_EST = "statement-ap:estimates_credited"
K_GROSS = "statement-lease:gross_up"
K_ROUND = "statement-lease:rounding"

KEY_OF = {"share": K_SHARE, "admin_fee": K_FEE, "cap": K_CAP, "capital": K_CAPITAL, "tax": K_TAX, "estimates": K_EST,
          "gross_up": K_GROSS, "rounding": K_ROUND}
KIND_OF = {v: k for k, v in KEY_OF.items()}
SOURCES_OF = {"share": ["statement", "lease"], "admin_fee": ["statement", "lease"], "cap": ["statement", "lease"],
              "capital": ["statement", "lease"], "tax": ["statement", "county"], "estimates": ["statement", "ap", "bank"],
              "gross_up": ["statement", "lease"], "rounding": ["statement"]}


def detection_key(fkey: str, statement_id: str) -> str:
    kind, _, line = fkey.partition(":")
    return f"{kind}:{statement_id}" + (f":{line}" if line else "")


def finding_key(dkey: str) -> tuple[str, str]:
    """detection key -> (statement_id, finding key as bp.cam.findings names it)."""
    parts = dkey.split(":")
    kind, sid = parts[0], parts[1]
    return sid, kind + (f":{parts[2]}" if len(parts) > 2 else "")


class Matcher:
    def __init__(self, sources: Sources):
        self.src = sources

    # ------------------------------------------------------------------ views (each read goes through its source key)
    def view(self, statement_id: str) -> dict:
        st = self.src("statements").statement(statement_id)
        lease = self.src("leases").lease(st.lease_id)
        center = self.src("reference").center(lease.center_id)
        tax = self.src("county").tax_record(center.apn)
        self.src("ap")
        cleared = self.src("bank").cleared_estimates(st.lease_id, st.year)
        return {"statement": st, "lease": lease, "center": center, "tax": tax, "cleared": cleared,
                "landlord": self.src("reference").landlord(st.landlord_id)}

    def findings(self, statement_id: str) -> dict[str, dict]:
        v = self.view(statement_id)
        return cam.findings(v["statement"], v["lease"], v["tax"], v["cleared"])

    def finding(self, b: dict) -> dict | None:
        sid, fkey = finding_key(b["detection_key"])
        return self.findings(sid).get(fkey)

    @staticmethod
    def overcharge(f: dict):
        if not f.get("calculator"):
            return D(f.get("amount", 0))
        return D(calculators.run(f["calculator"], f["inputs"])["amount_usd"])

    # ------------------------------------------------------------------ helpers
    def _next_id(self, conn) -> str:
        n = conn.execute(text("SELECT count(*) FROM breaks")).scalar() + 1
        return f"BRK-{n:05d}"

    def _create(self, conn, d: date, *, detection_key, break_key, account_id=None, symbol=None, ref=None,
                sources, values, amount_usd=D(0), harmless=False, status="open", reason=None) -> bool:
        res = conn.execute(insert(breaks).values(
            break_id=self._next_id(conn), detection_key=detection_key, break_key=break_key, level="statement",
            account_id=account_id, symbol=symbol, ref=ref, sources=sources, vals=jsonable(values),
            deltas=[], amount_usd=abs(amount_usd), detected_on=d, status=status,
            harmless=harmless, reason=reason, resolved_on=d if status == "auto_cleared" else None,
        ).on_conflict_do_nothing(index_elements=["detection_key"]).returning(breaks.c.break_id))
        bid = res.scalar()
        if bid:
            emit(conn, ACTOR, "break_detected" if status == "open" else "break_auto_cleared",
                 reason or f"{break_key} mismatch", inputs={"detection_key": detection_key, "values": values},
                 evidence=[{"source": x} for x in sources], sim_date=d)
        return bool(bid)

    # ------------------------------------------------------------------ recon
    def recon_statements(self, conn, d: date) -> int:
        n = 0
        for st in self.src("statements").statements_received(d):
            for fkey, f in sorted(self.findings(st.statement_id).items()):
                kind = fkey.split(":")[0]
                harmless = bool(f.get("harmless"))
                n += self._create(
                    conn, d, detection_key=detection_key(fkey, st.statement_id), break_key=KEY_OF[kind],
                    account_id=st.lease_id, symbol=st.landlord_id, ref=st.statement_id, sources=SOURCES_OF[kind],
                    values=f["values"], amount_usd=self.overcharge(f), harmless=harmless,
                    status="auto_cleared" if harmless else "open", reason=f["reason"])
        return n

    def run_transaction(self, conn, d: date) -> int:
        return self.recon_statements(conn, d)

    def recon_timing(self, conn, d: date) -> None:
        """No settlement lag in CAM: a statement is booked the day it arrives."""

    def recon_eod(self, conn, d: date) -> int:
        return 0

    def run_day(self, conn, d: date) -> int:
        return self.run_transaction(conn, d) + self.recon_eod(conn, d)

    def still_broken(self, conn, b: dict, d: date) -> bool:
        """Verify: rerun this break's check and subtract the credits already posted for it."""
        f = self.finding(b)
        if f is None:
            return False
        left = self.overcharge(f) - ledger.corrections_for(conn, b["detection_key"])
        return left != 0

