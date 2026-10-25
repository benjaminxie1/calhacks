"""Seeded true-world generator (R1-R3).

Builds one true world for Golden Wok Restaurant Group, a 40-store restaurant chain that
leases every store in a California shopping center, then derives each system's view of
it: the lease abstracts and lease text, each center's 2025 operating costs, the annual
CAM statement each landlord sends during the 2026 reconciliation season, AP's estimate
payments, the bank, the county tax records, the AP booking feed and the directory.

The truth is the statement each lease actually calls for (bp.cam). Errors are planted
later by bp.world.inject, into what the landlords send. All arithmetic is Decimal or
integer; the RNG only ever produces ints. The same seed produces byte-identical files.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from bp import cam, config
from bp.calendar import business_days, is_business_day, next_bd
from bp.models import (
    BankItem, Center, Clause, EstimatePayment, Landlord, Lease, LedgerTxn, Person, Posting, Statement, StatementLine,
    TaxRecord,
)
from bp.money import D, cents

N_LEASES = 40
TENANT = "Golden Wok Restaurant Group"
SHOWCASE = "LSE-007"  # Golden Wok Kitchen at Mission Plaza: the CAM core sample, line for line

LANDLORDS = [
    ("LL-MPH", "Mission Plaza Holdings LLC", "Rachel Kim"),
    ("LL-BAY", "Bayshore Retail Partners", "Tom Alvarez"),
    ("LL-CST", "Coastline Property Group", "Megan Cho"),
    ("LL-SVR", "Sierra Vista Realty", "Doug Pratt"),
    ("LL-GSC", "Golden State Centers", "Anita Rao"),
    ("LL-PCF", "Pacific Crest Properties", "Luis Romero"),
    ("LL-VLY", "Valley Commons REIT", "Hannah Park"),
    ("LL-HBR", "Harbor Point Realty", "Chris Nolan"),
    ("LL-OAK", "Oak Grove Retail Trust", "Priya Desai"),
    ("LL-RDG", "Redwood Gate Partners", "Sam Whitfield"),
    ("LL-SUN", "Sunset Ridge Holdings", "Erin Walsh"),
    ("LL-CAP", "Capitol Square Investors", "Victor Lin"),
]
NO_REPLY = {"LL-SVR"}  # this landlord never answers a request (shows escalation); mirrored in bp.agents.outreach

CITIES = [  # (city, county)
    ("Hayward", "Alameda"), ("San Mateo", "San Mateo"), ("Santa Cruz", "Santa Cruz"), ("Roseville", "Placer"),
    ("Fremont", "Alameda"), ("Walnut Creek", "Contra Costa"), ("Fresno", "Fresno"), ("Oakland", "Alameda"),
    ("Sacramento", "Sacramento"), ("Santa Rosa", "Sonoma"), ("San Jose", "Santa Clara"), ("Stockton", "San Joaquin"),
    ("Pleasanton", "Alameda"), ("Daly City", "San Mateo"), ("Modesto", "Stanislaus"), ("Concord", "Contra Costa"),
    ("Sunnyvale", "Santa Clara"), ("Elk Grove", "Sacramento"), ("Petaluma", "Sonoma"), ("Monterey", "Monterey"),
]
CENTER_WORDS = ["Plaza", "Commons", "Marketplace", "Town Center", "Village", "Square", "Crossing", "Station",
                "Shopping Center", "Promenade"]
CENTER_NAMES = ["Mission", "Bayshore", "Seacliff", "Granite Bay", "Ardenwood", "Ygnacio", "Shaw Avenue", "Lakeshore",
                "Arden", "Montgomery", "Westgate", "March Lane", "Rose", "Serramonte", "McHenry", "Clayton",
                "Murphy", "Laguna", "Washington", "Del Monte"]
STORES = ["Golden Wok Kitchen", "Golden Wok Express", "Golden Wok Noodle Bar", "Golden Wok Dumpling House"]

# The planting plan (bp.world.inject applies it). Which leases receive which errors, and when statements arrive.
GROSS_UP_FIRST = "LSE-012"  # Bayshore Retail Partners, Mar 10: the novel moment
GROSS_UP_LATER = ["LSE-021", "LSE-022", "LSE-023"]  # Coastline Property Group, May 5: fixed from the March lesson
TAX_NO_REPLY = "LSE-031"  # Sierra Vista Realty: the tax-bill request goes unanswered

PEOPLE = [
    Person(role="ops_lead", name="Dana Ortiz", owns="Lease administration lead: teaches new fixes; first contact for "
           "anything unknown", email="dana.ortiz@goldenwok.example"),
    Person(role="controller", name="Sam Lee",
           owns="Approves credits over $2,500, everything outside a playbook, and all new playbooks",
           email="sam.lee@goldenwok.example"),
    Person(role="accounts_payable", name="Alex Kim", owns="Holds disputed amounts out of landlord payments",
           email="alex.kim@goldenwok.example"),
    Person(role="landlord", name="Landlord property manager", owns="Answers statement questions; sends tax bills",
           reply_sla_days=2, email="pm@landlord.example"),
]


def acct(lease_id: str, kind: str) -> str:
    return f"lease:{lease_id}:{kind}"


@dataclass
class World:
    seed: int
    landlords: list[Landlord]
    centers: list[Center]
    leases: list[Lease]
    lease_text: dict[str, str]
    people: list[Person]
    tax_records: list[TaxRecord] = field(default_factory=list)
    payments: list[EstimatePayment] = field(default_factory=list)
    bank: list[BankItem] = field(default_factory=list)
    truth_statements: list[Statement] = field(default_factory=list)  # what each lease calls for
    statements: list[Statement] = field(default_factory=list)  # what the landlords send (planted later)
    truth: list[LedgerTxn] = field(default_factory=list)  # correct booking of every true event
    booking: list[LedgerTxn] = field(default_factory=list)  # what AP sends to the ledger (books statements as billed)
    answer_key: list = field(default_factory=list)
    plan: dict = field(default_factory=dict)  # lease_id -> planted error kinds


# ------------------------------------------------------------------ lease text

def lease_text(l: Lease, c: Center, ll: Landlord) -> str:
    cl = l.clauses
    parts = [
        "<!-- SYNTHETIC lease generated from a standard multi-tenant retail form (Breakpoint world). -->",
        "# SHOPPING CENTER LEASE", "",
        f"**Landlord:** {ll.name}", f"**Tenant:** {l.tenant_entity}, a California corporation",
        f"**Shopping Center:** {c.name}, {c.address}, {c.city}, California",
        f"**Assessor's Parcel Number:** {c.apn}", "",
        "## ARTICLE 1. BASIC LEASE PROVISIONS", "",
        f"1.1 **Premises.** {l.suite}, containing approximately {l.premises_sf:,} rentable square feet.", "",
        f"1.2 **Shopping Center Area.** The Gross Leasable Area of the Shopping Center is {l.gla_sf:,} square feet.", "",
        "## ARTICLE 5. OPERATING EXPENSES AND REAL ESTATE TAXES", "",
    ]
    titles = {"share": "Tenant's Share", "opex": "Operating Expenses Defined", "exclusions": "Exclusions",
              "cap": "Controllable Expense Cap", "admin_fee": "Administrative Fee", "taxes": "Real Estate Taxes",
              "gross_up": "Gross-Up", "reconciliation": "Annual Reconciliation", "audit": "Audit Right"}
    for k in ["share", "opex", "exclusions", "cap", "admin_fee", "taxes", "gross_up", "reconciliation", "audit"]:
        if k in cl:
            parts += [f"{cl[k].section} **{titles[k]}.** {cl[k].quote}", ""]
    return "\n".join(parts)


def _clauses(premises, gla, share, cap, fee, fee_excl, gross_pct, window) -> dict[str, Clause]:
    out, n = {}, 0

    def add(k, text):
        nonlocal n
        n += 1
        out[k] = Clause(section=f"5.{n}", quote=text)

    add("share", "\"Tenant's Share\" means the percentage obtained by dividing the rentable square feet of the Premises "
                 "by the total Gross Leasable Area of the Shopping Center, whether or not leased or occupied, which "
                 f"percentage is agreed to be {share}%.")
    add("opex", "\"Operating Expenses\" means all reasonable costs actually incurred by Landlord to operate, maintain, "
                "repair and insure the Common Areas of the Shopping Center, including landscaping, parking lot sweeping "
                "and repair, security, janitorial service, Common Area utilities, and premiums for property and "
                "liability insurance.")
    add("exclusions", "Operating Expenses shall not include: (a) costs of a capital nature, including roof replacement, "
                      "structural repairs and replacement of building systems, as determined under generally accepted "
                      "accounting principles; (b) costs reimbursed by insurance or warranties; (c) leasing commissions, "
                      "attorneys' fees and costs of disputes with other tenants; (d) depreciation; and (e) any "
                      "management or administrative fee except as provided in this Article.")
    if cap is not None:
        add("cap", "Notwithstanding anything to the contrary, Tenant's Share of Controllable Operating Expenses for any "
                   "calendar year shall not exceed Tenant's Share of Controllable Operating Expenses for the prior "
                   f"calendar year by more than {_words(cap)} percent ({cap}%). \"Controllable Operating Expenses\" "
                   "means all Operating Expenses other than insurance premiums, Real Estate Taxes and Common Area "
                   "utilities.")
    if fee is not None:
        ex = {"insurance": "insurance premiums", "taxes": "Real Estate Taxes", "capital": "any capital costs",
              "utilities": "Common Area utilities"}
        named = [ex[e] for e in fee_excl]
        tail = (", excluding " + (", ".join(named[:-1]) + " and " + named[-1] if len(named) > 1 else named[0])) if named else ""
        add("admin_fee", f"Landlord may include an administrative fee not to exceed {_words(fee)} percent ({fee}%) of "
                         f"Operating Expenses{tail}.")
    add("taxes", "Tenant shall pay Tenant's Share of Real Estate Taxes actually levied against the Shopping Center "
                 "parcel and paid by Landlord for the calendar year, net of any refund or abatement.")
    if gross_pct is not None:
        add("gross_up", "If the Shopping Center is less than " + f"{gross_pct}% occupied during any calendar year, "
                        "Landlord may adjust janitorial and Common Area utility costs to the amount they would have "
                        f"been had the Shopping Center been {gross_pct}% occupied.")
    add("reconciliation", "Within one hundred twenty (120) days after the end of each calendar year, Landlord shall "
                          "deliver a statement of actual Operating Expenses and Real Estate Taxes for that year. Any "
                          "overpayment by Tenant shall be credited against the next Rent due.")
    add("audit", f"Tenant may, within {_words(window)} ({window}) days after receipt of the annual statement, dispute "
                 "the statement in writing and inspect Landlord's books and records. If the audit discloses an "
                 "overcharge of more than three percent (3%), Landlord shall pay the reasonable cost of the audit.")
    return out


def _words(n) -> str:
    return {4: "four", 5: "five", 6: "six", 10: "ten", 12: "twelve", 15: "fifteen", 5.5: "five and one-half",
            60: "sixty", 90: "ninety", 120: "one hundred twenty"}.get(int(n) if D(n) == D(n).to_integral_value() else n,
                                                                   str(n))


# ------------------------------------------------------------------ generation

def _dollars(rng, sf: Decimal, lo: int, hi: int) -> Decimal:
    """sf x a rate drawn in cents per square foot, rounded to whole dollars."""
    return (sf * D(rng.randint(lo, hi)) / D(100)).to_integral_value()


def _received_dates(rng, n: int) -> list[date]:
    days = [d for d in business_days() if date(2026, 2, 9) <= d <= date(2026, 5, 22)]
    return sorted(days[rng.randrange(len(days))] for _ in range(n))


def generate(seed: int = config.SEED) -> World:
    rng = random.Random(seed)
    landlords = [Landlord(landlord_id=i, name=n, contact=c, email=f"{c.split()[0].lower()}@{i[3:].lower()}.example",
                          reply_sla_days=2) for i, n, c in LANDLORDS]

    # landlord per lease: the plan's landlords are fixed; the other eight own the rest
    owner = {SHOWCASE: "LL-MPH", GROSS_UP_FIRST: "LL-BAY", TAX_NO_REPLY: "LL-SVR", **{x: "LL-CST" for x in GROSS_UP_LATER}}
    general = [x[0] for x in LANDLORDS[4:]]
    lease_ids = [f"LSE-{i:03d}" for i in range(1, N_LEASES + 1)]
    for lid in lease_ids:
        owner.setdefault(lid, general[rng.randrange(len(general))])

    plan = _plan(rng, lease_ids)
    centers, leases, texts = [], [], {}
    taxes, payments, bank, truth_stmts = [], [], [], []
    received = _received_dates(rng, N_LEASES)
    fixed_dates = {SHOWCASE: date(2026, 3, 3), GROSS_UP_FIRST: date(2026, 3, 10),
                   **{x: date(2026, 5, 5) for x in GROSS_UP_LATER}}
    free = [d for d in received]
    rng.shuffle(free)

    for k, lid in enumerate(lease_ids):
        city, county = CITIES[k % len(CITIES)]
        name = f"{CENTER_NAMES[k % len(CENTER_NAMES)]} {CENTER_WORDS[(k * 7 + 3) % len(CENTER_WORDS)]}"
        cid = f"CTR-{k + 1:03d}"
        apn = f"{rng.randint(100, 999)}-{rng.randint(1000, 9999):04d}-{rng.randint(1, 99):03d}-{rng.randint(1, 9):02d}"
        if lid == SHOWCASE:
            name, city, county, apn = "Mission Plaza", "Hayward", "Alameda", "428-0061-012-03"
            gla, premises = D(32000), D(2400)
        else:
            gla = D(rng.randint(24, 52) * 1000)
            premises = D(rng.randint(18, 42) * 100)
        address = f"{rng.randint(100, 9800)} {['Mission Blvd', 'El Camino Real', 'Main St', 'Stevens Creek Blvd', 'Sunrise Ave', 'Pacific Ave', 'Blackstone Ave', 'Broadway'][rng.randrange(8)]}"
        centers.append(Center(center_id=cid, name=name, address=address, city=city, county=county, apn=apn,
                              gla_sf=gla, landlord_id=owner[lid]))
        share = (premises * D(100) / gla).quantize(D("0.01"))
        # lease terms
        r = rng.randint(1, 100)
        cap = D(5) if r <= 70 else D(4) if r <= 80 else D(6) if r <= 88 else None
        r = rng.randint(1, 100)
        fee = D(10) if r <= 60 else D(12) if r <= 72 else D(15) if r <= 82 else D(5) if r <= 90 else None
        r = rng.randint(1, 100)
        fee_excl = (["insurance", "taxes", "capital"] if r <= 70 else ["taxes", "capital"] if r <= 90 else ["capital"])
        gross_pct = D(95) if rng.randint(1, 100) <= 25 else None
        window = [90, 90, 90, 120, 60][rng.randrange(5)]
        if lid == SHOWCASE:
            cap, fee, fee_excl, gross_pct, window = D(5), D(10), ["insurance", "taxes", "capital"], None, 90
        if lid in [GROSS_UP_FIRST] + GROSS_UP_LATER:
            gross_pct = None  # these leases have no gross-up clause: that is the lesson
        if "cap" in plan.get(lid, []) and cap is None:
            cap = D(5)
        if "admin_fee" in plan.get(lid, []) and fee is None:
            fee = D(10)
        if fee == D(15) and "admin_fee" in plan.get(lid, []):
            fee = D(10)
        clauses = _clauses(premises, gla, share, cap, fee, fee_excl, gross_pct, window)

        # the center's actual 2025 operating lines (whole dollars)
        if lid == SHOWCASE:
            vals = [("Landscaping", "controllable", 22000), ("Parking lot sweeping and repair", "controllable", 31000),
                    ("Security patrol", "controllable", 40000), ("Repairs and maintenance", "controllable", 72000),
                    ("Janitorial", "controllable", 40000), ("Common area utilities", "utilities", 38000),
                    ("Property and liability insurance", "insurance", 41000)]
            base = [StatementLine(line_id=f"L{i + 1}", description=dsc, category=cat, amount=D(a), tenant_amount=D(0))
                    for i, (dsc, cat, a) in enumerate(vals)]
            county_tax, prior, occ_sf = D(31000), D(180000), D(29268)
        else:
            vals = [("Landscaping", "controllable", 45, 80), ("Parking lot sweeping and repair", "controllable", 70, 110),
                    ("Security patrol", "controllable", 90, 140), ("Repairs and maintenance", "controllable", 170, 260),
                    ("Janitorial", "controllable", 95, 150), ("Common area utilities", "utilities", 95, 140),
                    ("Property and liability insurance", "insurance", 100, 145)]
            base = [StatementLine(line_id=f"L{i + 1}", description=dsc, category=cat, amount=_dollars(rng, gla, lo, hi),
                                  tenant_amount=D(0)) for i, (dsc, cat, lo, hi) in enumerate(vals)]
            county_tax = _dollars(rng, gla, 85, 160)
            ctl = cam.controllable(base)
            binds = "cap" in plan.get(lid, []) or (cap is not None and rng.randint(1, 100) <= 10)
            growth = rng.randint(70, 140) if binds and cap is not None else rng.randint(0, int((cap or D(5)) * 10) - 10)
            prior = (ctl * D(1000) / D(1000 + growth)).to_integral_value()
            occ = rng.randint(840, 990) if lid not in [GROSS_UP_FIRST] + GROSS_UP_LATER else rng.randint(840, 900)
            occ_sf = (gla * D(occ) / D(1000)).to_integral_value()
        base.append(StatementLine(line_id=f"L{len(base) + 1}", description="Real estate taxes", category="taxes",
                                  amount=county_tax, tenant_amount=D(0)))
        occ_pct = (occ_sf * D(100) / gla).quantize(D("0.01"))

        store = STORES[k % len(STORES)]
        lease_tmp = Lease(lease_id=lid, store=f"{store} #{k + 1}" if lid != SHOWCASE else "Golden Wok Kitchen",
                          tenant_entity="Golden Wok Kitchen, Inc." if lid == SHOWCASE else f"{TENANT}, Inc.",
                          center_id=cid, landlord_id=owner[lid], suite=f"Suite {rng.randint(1, 9) * 100 + rng.randint(1, 20)}",
                          premises_sf=premises, gla_sf=gla, share_pct=share, cap_pct=cap, admin_fee_pct=fee,
                          admin_fee_base_excludes=fee_excl if fee is not None else [], capital_excluded=True,
                          taxes_actual_paid=True, gross_up_pct=gross_pct, audit_window_days=window,
                          monthly_estimate_2025=D(0), monthly_estimate_2026=D(0), clauses=clauses)
        lines = cam.correct_lines(lease_tmp, base, occ_pct, prior, county_tax)
        total = sum((l.tenant_amount for l in lines), D(0))
        if lid == SHOWCASE:
            monthly = D(2600)
        else:
            monthly = (total * D(rng.randint(92, 108)) / D(1200)).to_integral_value()
        lease = lease_tmp.model_copy(update={"monthly_estimate_2025": monthly,
                                             "monthly_estimate_2026": (monthly * D(104) / D(100)).to_integral_value()})
        if lid == SHOWCASE:
            lease = lease.model_copy(update={"suite": "Suite 106"})
        leases.append(lease)
        ll = next(x for x in landlords if x.landlord_id == owner[lid])
        texts[lid] = lease_text(lease, centers[-1], ll)

        taxes.append(TaxRecord(apn=apn, county=county, tax_year="2025-26", total_tax=county_tax, installments=[
            {"due": "2025-12-10", "amount": str(county_tax / 2), "status": "paid"},
            {"due": "2026-04-10", "amount": str(county_tax - county_tax / 2), "status": "paid"}]))

        # AP estimate payments: 2025 (historical) and 2026 through May; all clear the bank
        for y, m in [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, 6)]:
            amt = monthly if y == 2025 else lease.monthly_estimate_2026
            paid = date(y, m, 1)
            while not is_business_day(paid):
                paid += timedelta(days=1)
            pid = f"PAY-{lid[4:]}-{y}{m:02d}"
            payments.append(EstimatePayment(payment_id=pid, lease_id=lid, period=f"{y}-{m:02d}", amount=amt, paid_on=paid))
            bank.append(BankItem(payment_id=pid, lease_id=lid, amount=amt, status="cleared",
                                 status_date=next_bd(paid) if paid.year == 2026 else paid + timedelta(days=2)))

        rec = fixed_dates.get(lid) or free.pop()
        stmt_date = rec - timedelta(days=rng.randint(3, 9))
        truth_stmts.append(Statement(
            statement_id=f"STMT-{lid}-2025", lease_id=lid, landlord_id=owner[lid], year=2025, statement_date=stmt_date,
            received_on=rec, share_billed_pct=share, occupied_sf=occ_sf, occupancy_pct=occ_pct,
            prior_year_controllable=prior, lines=lines, tenant_total=total, estimates_credited=monthly * 12,
            balance_due=total - monthly * 12))

    w = World(seed=seed, landlords=landlords, centers=centers, leases=leases, lease_text=texts, people=PEOPLE,
              tax_records=taxes, payments=payments, bank=bank, truth_statements=truth_stmts,
              statements=[s.model_copy(deep=True) for s in truth_stmts], plan=plan)
    _book(w)
    return w


def _plan(rng, lease_ids: list[str]) -> dict[str, list[str]]:
    """Which errors each landlord's statement will carry. One error per statement, except the showcase."""
    plan = {SHOWCASE: ["share", "capital", "cap", "admin_fee", "tax"], GROSS_UP_FIRST: ["gross_up"],
            TAX_NO_REPLY: ["tax"], **{x: ["gross_up"] for x in GROSS_UP_LATER}}
    pool = [x for x in lease_ids if x not in plan]
    rng.shuffle(pool)
    for kind, n in [("tax", 3), ("share", 3), ("admin_fee", 4), ("cap", 3), ("capital", 3), ("estimates", 3),
                    ("rounding", 6)]:
        for _ in range(n):
            plan[pool.pop()] = [kind]
    return dict(sorted(plan.items()))


def statement_txn(s: Statement, txn_id: str) -> LedgerTxn:
    lid = s.lease_id
    return LedgerTxn(txn_id=txn_id, date=s.received_on, settle_date=s.received_on, kind="statement",
                     source_ref=s.statement_id, reason=f"{s.year} CAM reconciliation from {s.landlord_id}, as billed",
                     postings=[Posting(account=acct(lid, "expense"), asset="USD", amount=s.tenant_total),
                               Posting(account=acct(lid, "prepaid"), asset="USD", amount=-s.estimates_credited),
                               Posting(account=acct(lid, "payable"), asset="USD", amount=-s.balance_due)])


def _book(w: World) -> None:
    """Truth and the booking feed: opening prepaid balances, 2026 estimate payments, and each statement on receipt.
    Until bp.world.inject plants errors, the feed and the truth are identical."""
    start = business_days()[0]
    n = 0

    def tid():
        nonlocal n
        n += 1
        return f"TXN-{n:06d}"

    by_lease: dict[str, list[EstimatePayment]] = {}
    for p in w.payments:
        by_lease.setdefault(p.lease_id, []).append(p)
    for l in w.leases:
        opening = sum((p.amount for p in by_lease[l.lease_id] if p.paid_on < start), D(0))
        w.truth.append(LedgerTxn(txn_id=tid(), date=start, settle_date=start, kind="opening", source_ref=f"OPEN-{l.lease_id}",
                                 reason="opening balance: CAM estimates paid Jan 2025 - Jan 2026",
                                 postings=[Posting(account=acct(l.lease_id, "prepaid"), asset="USD", amount=opening),
                                           Posting(account="cash:operating", asset="USD", amount=-opening)]))
    events = []
    for p in w.payments:
        if p.paid_on >= start:
            events.append((p.paid_on, 0, p.payment_id, p))
    for s in w.truth_statements:
        events.append((s.received_on, 1, s.statement_id, s))
    for d, _, _, x in sorted(events, key=lambda e: e[:3]):
        if isinstance(x, EstimatePayment):
            w.truth.append(LedgerTxn(txn_id=tid(), date=d, settle_date=d, kind="estimate", source_ref=x.payment_id,
                                     reason=f"CAM estimate {x.period}",
                                     postings=[Posting(account=acct(x.lease_id, "prepaid"), asset="USD", amount=x.amount),
                                               Posting(account="cash:operating", asset="USD", amount=-x.amount)]))
        else:
            w.truth.append(statement_txn(x, tid()))
    w.booking = [t.model_copy(deep=True) for t in w.truth]


# ------------------------------------------------------------------ files

FILES = [
    "landlords.jsonl", "centers.jsonl", "leases.jsonl", "people.jsonl", "tax_records.jsonl", "payments.jsonl",
    "bank.jsonl", "statements.jsonl", "truth_statements.jsonl", "booking.jsonl", "truth.jsonl", "answer_key.jsonl",
]


def _line(x) -> str:
    obj = x if isinstance(x, dict) else x.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def write(w: World, out: Path) -> dict[str, str]:
    out.mkdir(parents=True, exist_ok=True)
    groups = {
        "landlords.jsonl": w.landlords, "centers.jsonl": w.centers, "leases.jsonl": w.leases, "people.jsonl": w.people,
        "tax_records.jsonl": w.tax_records, "payments.jsonl": w.payments, "bank.jsonl": w.bank,
        "statements.jsonl": w.statements, "truth_statements.jsonl": w.truth_statements, "booking.jsonl": w.booking,
        "truth.jsonl": w.truth, "answer_key.jsonl": w.answer_key,
    }
    hashes = {}
    for name in FILES:
        body = "".join(_line(x) + "\n" for x in groups[name])
        (out / name).write_text(body)
        hashes[name] = hashlib.sha256(body.encode()).hexdigest()
    (out / "leases").mkdir(exist_ok=True)
    for lid, text in sorted(w.lease_text.items()):
        (out / "leases" / f"{lid}.md").write_text(text)
        hashes[f"leases/{lid}.md"] = hashlib.sha256(text.encode()).hexdigest()
    (out / "plan.json").write_text(json.dumps(w.plan, indent=1, sort_keys=True))
    (out / "manifest.json").write_text(json.dumps({"seed": w.seed, "hashes": hashes}, indent=1, sort_keys=True))
    return hashes


def build(seed: int = config.SEED, clean: bool = False, out: Path | None = None) -> tuple[World, dict]:
    from bp.world.inject import inject

    w = generate(seed)
    if not clean:
        inject(w)
    return w, write(w, out or config.world_dir(seed, clean))


if __name__ == "__main__":
    import argparse
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--clean", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    w, h = build(a.seed, a.clean)
    print(f"world seed={a.seed} clean={a.clean}: {len(w.leases)} leases, {len(w.statements)} statements, "
          f"{len(w.booking)} booking txns, {len(w.answer_key)} planted errors in {time.time() - t0:.1f}s")
    print(f"written to {config.world_dir(a.seed, a.clean)}")
