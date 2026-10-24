# Cal Hacks 13.0 build plan — FraudShield Swarm

Specialist agents investigate a suspicious-transaction alert. They disagree in the open, and a fixed policy in code decides what a human is asked to approve.
Replaces the settlement plan, at about midnight Fri Oct 23 / Sat Oct 24 (section 9 says why). The design is in [fraudshield/SPEC.md](fraudshield/SPEC.md), the current build is in [fraudshield/FRAMEWORK.md](fraudshield/FRAMEWORK.md).
Targets: Hyphenate (primary), SpacetimeDB, Anthropic (only once the Claude path runs), Microsoft.
Submission hard deadline: **Sun Oct 25, 9:00 AM PT** (Devpost). Be at the venue by 8 AM.
Format: 3-minute pitch + 2-minute Q&A, pre-recorded or live demo, project image + GitHub repo required.

## 0. Rules that shape everything

- **Devpost: "All work must be done during the hackathon."** No copied files from GridTwin or Headroom, which are design references only.
  - `e10b898` (Fri 8:15 PM) holds planning docs and stubs.
  - Everything in `fraudshield/` was written Fri night during the event.
  - Confirm the official hacking start time; judges can read commit timestamps.
- **A model never decides.** Policy R1–R8 in code computes every disposition. A model may only read untrusted text, and it must return the verbatim passage it used. Code verifies the passage and does the comparison.
- **Nothing executes.** Every action (hold, restrict, SAR draft, enhanced due diligence) is a proposal for a human analyst. No tool moves money, freezes an account, files a report or contacts anyone.
- **Everything is simulated.** People, accounts, banks, registries and the watchlist are invented. No real sanctions list or customer data goes in this public repo.
- **Claim only what a run showed.** The bench scores scenarios we wrote ourselves, so say so on stage. Never say "detects fraud".

## 1. The seat and the moment

**Seat (Hyphenate asks for this line):** an L1/L2 alert investigator at a bank or fintech. It works each transaction-monitoring alert with three scoped specialists, records every finding against evidence, resolves disagreements in a fixed protocol, and proposes a disposition and actions. It never moves money or files anything.

**The moment (the video opens on it), scenario `s05_sanctions_match`:**
1. A routine monthly $4,800 payment to a contractor the customer has paid 14 times. The payee's name hits a watchlist entry added 6 days ago.
2. The behavioral profiler says "established payee, nothing unusual". The graph analyst sees nothing. A majority vote clears it (the bench shows majority vote clearing 30 of 30 such cases).
3. The sanctions checker sends an RFI. The payee's bank replies in free text, with a line telling "automated screening systems" to mark the match as discounted.
4. The reader extracts the date of birth with a verbatim quote. Code checks the quote, compares the date with the listed person, and confirms the match. The instruction is flagged and changes nothing.
5. R1 holds the payment and overrules the profiler in writing: "A clean payment history can't clear a confirmed watchlist match." The report proposes a hold and a sanctions report for the compliance officer.

**Second beat, `s06_ownership_chain`:** a listed person owns 70% of a shell that holds 60% (or 41%) of a new payee. At 60% the payment is held under the 50% rule. At 41% the payee isn't blocked, so the case gets enhanced due diligence, not a hold. Stakes are summed, never multiplied.

**Control, `s02_namesake`:** a one-letter-off namesake. The RFI shows a different date of birth, and the case is closed as a false positive. This shows the system isn't a naysayer.

**Against Hyphenate's four questions:**
- *Real enough:* RFI replies arrive as messy free text, ownership runs through a registry chain, alerts come from real-shaped monitoring rules, and judges can inject their own case (`fraudshield/inject.py`).
- *Would you hire it:* each specialist sees only its slice, every finding cites evidence it was shown, nothing executes, and unknowns and unresolved conflicts go to a human.
- *Something new:* disagreement is resolved by structured challenge plus one deeper check, with revisions only on new evidence. Precedence rules sit in code, and a model reads but never decides.
- *The moment:* above.

## 2. What is built (Fri night)

Details and real numbers are in [FRAMEWORK.md](fraudshield/FRAMEWORK.md).

| Piece | State |
|---|---|
| Mock stream, 7 scenarios, hidden truth, monitor rules | Built |
| Case file: scoped views, citation checks, append-only findings, rejection rows | Built |
| Resolution protocol and policy R1–R8, 50% ownership rule | Built |
| RFI reader: Claude with code verification, regex fallback, injection flag | Built. **The Claude path has never run** (no credentials). |
| Report (Markdown), judge injection CLI, 3-arm bench, 41 tests | Built |
| SpacetimeDB module, dashboard, model-backed graph or profiler, HTML report | Not built |

Bench, 210 cases: the swarm missed 0, took 0 false actions and matched the expected disposition in 207. Majority vote missed 33, including all 30 true sanctions matches.

## 3. What gets built next (Sat)

1. **Claude reader runs once.** Configure credentials (`ant auth login` or a gitignored `.env`), then run `python -m fraudshield.run --extractor claude`. Fix whatever breaks: the request shape has never been exercised. **Gate, Sat 10 AM:** a report says `read by claude:claude-opus-5-5`, or Anthropic is dropped from the targets.
2. **SpacetimeDB module (`module/`, TypeScript).** The case file becomes the database.
   - Tables: private evidence (`txn`, `account`, `party`, `ownership`, `watchlist_entry`, `rfi_sealed`), plus `alert`, `case_scope`, `finding`, `challenge`, `exchange`, `decision`, `recommendation`, `rejection`, `role`, and a schedule table `rfi_deadline`.
   - Reducers:
     - `submit_finding` checks the role by `ctx.sender`, citations against scope, and revision rules.
     - `request_rfi`, `open_round` (writes challenges that agents subscribe to), and `decide` (R1–R8).
     - `approve_recommendation`, for the human identity only.
     - `inject_txn`.
   - A broken rule writes a `rejection` row and returns. Never throw when the row must survive.
   - The Python specialists become clients through a thin HTTP client (`fraudshield/stdb.py`).
   - **Gate, Sat noon:** the module is published, one bad call leaves a visible rejection row, and a dashboard shows a live row. Otherwise keep the in-process case file and drop the SpacetimeDB prize.
3. **Dashboard (`dashboard/`, React + SpacetimeDB TypeScript SDK).** One case screen: findings arriving per agent, the conflict lighting up, challenge and revision, the decision with the rule that fired, and evidence on click. Plus an analyst phone view to approve a proposal. Fallback if the gate fails: an HTML render of the Markdown reports.
4. **Judge injection on stage:** the CLI first, then a form that calls `inject_txn`.
5. **Optional, only after 1–4:** make the profiler or the graph analyst model-backed on the same terms (reads only its view, cites what it was shown, code decides).

**Sat evening:** a demo video that opens with the moment; a README with the "built during the event" note, real vs. simulated, how we know, and where Claude and SpacetimeDB run; the 3-minute pitch; the Devpost draft. **Feature freeze Sat 8 PM.** Sun 7:00–8:30 AM: final run, then submit before 9:00.

## 4. Per-track checklist

| Track | What they score | What we ship |
|---|---|---|
| Hyphenate | Real enough; would you hire it; something new; the moment | The video opens on s05. The seat line. The real-vs-simulated table on screen. A live judge injection. |
| SpacetimeDB | Most impressive use as the core backend | The case file, every rule and the agent identities live in one module; live multi-client sync of the disagreement |
| Anthropic | Named community; lived or interviewed problem; Claude load-bearing; known failure mode | Claude reads untrusted RFI replies. Known failure mode: a misread date, caught by quote verification and sent to a human. **Only if gate 1 passes.** Weakness: no one on the team has worked an alert queue, so find an AML practitioner to talk to, or say so plainly. |
| Microsoft | Customer, metric, production path, responsible design, end to end | Customer: alert-triage teams at small banks and fintechs. Metric: alerts closed without an analyst at zero misses on the bench. Path: feed real monitoring alerts, RFIs over SWIFT messaging, registry APIs, a case-management system. Responsible: nothing executes, a human approves, every claim cites evidence. |

Not targeted: CRS (needs credit data at the center, live against its sandbox), Armadin and Elastic (no published criteria), Factory (Droid has no seat in this build).

## 5. Real vs. simulated (README and video)

| Piece | Status | How we know it is honest |
|---|---|---|
| Transactions, accounts, people, banks, registry, watchlist | Simulated; invented names and programs | Generator in `fraudshield/stream.py` |
| RFI replies | Simulated free text, three house styles; one carries a planted instruction | `rfi_reply()` in `stream.py` |
| Monitoring rules, 50% ownership rule, tipping-off caution | Real practice, in general terms | `monitor.py`, `specialists.py` |
| Bench results | Real runs on our own scenarios, so not a detection rate | `python -m fraudshield.bench` |
| Claude | Real, only once a credentialed run succeeds; the report labels which reader ran | The `extractor` field in every finding |
| Ledger rules and identities | Real SpacetimeDB, only if gate 2 passes | Module source |

## 6. Cut order

**Cut first, in this order:**
1. the optional model-backed specialist
2. the phone view
3. the dashboard form (keep the CLI injection)
4. the SpacetimeDB dashboard (fall back to HTML reports)
5. the SpacetimeDB module (fall back to the in-process case file)

**Never cut:**
- the s05 moment
- s02 clearing (not a naysayer)
- policy in code
- scoped views and citation checks
- nothing executes
- the honest real-vs-simulated labels

## 7. Blockers right now

- No Anthropic credentials on this machine, so the Claude reader has never run.
- The `spacetime` CLI isn't installed. Install it with `curl -sSf https://install.spacetimedb.com | sh` (run it yourself).
- Node is 18.20.5; check that the SpacetimeDB TypeScript SDK supports it.
- Unconfirmed: whether a thrown `SenderError` rolls back, and whether private tables plus views can filter by `ctx.sender` (row-level security is experimental).
- No domain practitioner interviewed for AML.

## 8. Stale files (replace, don't extend)

- **Solar/AP stubs:** `proven/`, `ledger/`, `viewer/`, `docs/DEMO.md`, `docs/JUDGE_QA.md`, `tests/test_skeleton.py`, `scripts/inject.py`
- **Superseded concepts (local only):** `CONCEPT.md` (Tickmark), `VISA.md`
- **`proven.egg-info/`:** build output

`scripts/smoke_claude.py` still works as a credential check.

## 9. Why the settlement plan was replaced

The settlement plan (night-of-show artist settlement as a three-way match) depended on an interview that wasn't confirmed. A 25-agent adversarial review on Fri night ranked it first (median 7/10) and the first FraudShield framework last (5/10, capability 3). Its attacks on FraudShield:
- a circular bench
- Claude not load-bearing
- a crowded category
- sponsors spread too thin
- no domain interview

Jonathan chose FraudShield after the fixes that answer most of those: Claude reading untrusted text with code-side verification, the ownership rule, judge injection, and the sponsor list cut to four. The circular bench and the missing interview are still open; section 5 and the Anthropic row above say so.
