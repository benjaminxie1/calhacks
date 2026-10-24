# CAM audit agent

Small-business tenants pay a share of their shopping center's shared costs (CAM: taxes,
insurance, maintenance). Once a year the landlord sends a reconciliation, and the tenant
has a short window (90 days in our sample lease) to dispute it. This agent reads the
lease and the bill, checks the tax pass-through against the county's own record,
computes what the tenant actually owes, and drafts the dispute letter. The owner signs.

**Seat it fills:** the CAM lease auditor a small tenant can't afford to hire.

## How it decides

| Agent | Does | Can't |
| --- | --- | --- |
| Lease parser (`cam/lease_parser.py`) | Claude extracts share, cap, exclusions, fee limit, tax rule and audit window, quoting each clause | Have a clause used if the quote isn't word for word in the lease (`verify_quotes`) |
| Classifier (`cam/classify.py`) | Claude sorts repair lines into ordinary repair, excluded capital, or ambiguous | Guess: "ambiguous" goes to the owner |
| Evidence (`cam/evidence.py`) | Pulls the county tax bill for the parcel through our Apify Actor | Assert fraud: a tax gap is "medium" confidence and the letter asks for the bills |
| Auditor (`cam/auditor.py`) | Code recomputes every dollar and attributes each dollar of difference to one finding | Use a model for any number |
| Negotiator (`cam/negotiator.py`) | Claude drafts the letter from sendable findings | Add a dollar figure the auditor didn't compute (draft is rejected, template used); send anything |

Every step is written to `runs/<id>/trail.jsonl`.

## Run it

```bash
python -m venv .venv && .venv/bin/pip install anthropic pydantic pyyaml pytest
.venv/bin/python -m cam.run                                # offline readers, sample data
.venv/bin/python -m cam.run --reader claude                # needs ANTHROPIC_API_KEY in .env
.venv/bin/python -m cam.run --rule L9=exclude_capital      # owner answers the open question
.venv/bin/pytest -q cam/tests
```

## Real vs simulated (as of Sat Oct 24, morning)

| Piece | Status |
| --- | --- |
| Lease | Synthetic, modeled on standard retail lease language. To be replaced by a real SEC EDGAR exhibit |
| Reconciliation statement | Synthetic, seeded with 5 known errors and 1 ambiguous line (`data/sample/answer_key.yaml`) |
| County tax record | Cached result in the Actor's output shape until the Apify Actor is live |
| Claude path | Written, not yet run (no API key in the build environment) |
| SpacetimeDB, Fetch.ai, Factory Droid | Not built yet |

The sample result: landlord bills $38,285.80 and says $7,085.80 is due; the lease says
$24,127.50, so the tenant is owed a $7,072.50 credit, plus $1,050.00 awaiting the owner's call.
