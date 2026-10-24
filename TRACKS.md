# Cal Hacks 13.0 — sponsor track fit

Event: Oct 23–25, 2026, Palace of Fine Arts, SF (3-day; schedule sent closer to the event). **Submission deadline: not published online; check the hackpack.**
This folder was empty when this was written, so it assumes nothing is built yet.
Reading of the pasted list: *which tracks should we target*.

## 0. Check eligibility before planning around old code

GridTwin (Recursive Agents Hackathon, Oct 17) and Headroom both predate the event.
Most hackathons, Cal Hacks historically included, require the project to be built during the event.
Read the 13.0 hackpack / rules on pre-existing code first. Safe framing: **reuse the architecture and lessons, not the commits.**

## 1. Tier 1 — one "accountable autonomous agent" build can stack all four

| Track | Prize | What it rewards | What you must actually ship |
|---|---|---|---|
| **Hyphenate** — Autonomous Systems for Finance | $8k / $5k / $3k cash; expedited interviews for top 5 | Real enough (realistic data, messy problem, proof). "Would you hire it" (limited access, clean record of work, knows when to ask). Something new. **The moment** (catches what others missed, recovers, defends a decision). | Demo video that *opens* with the moment. Live demo where judges may inject their own scenario. One line on the seats it fills. Explicit real-vs-simulated split and how you know. **Domain is finance** — you need a finance seat. |
| **Microsoft** — Grand Prize: Built for the Real World (+ Customer Impact Award) | Surface laptop per member; remote FDE tech talk; 1:1 FDE mentorship | Customer understanding, measurable usefulness, path from prototype to production, responsible design, end-to-end functionality | Name the customer, a metric, and a production path. A demo-only pitch loses here. |
| **Factory** — Best Use of Droid SDK | $2k cash + credits + swag | Droid does real work *in the running product* on a trigger you designed; thoughtful control over what it can touch; its plans/steps/diffs/results are visible | Droid SDK (TS or Python) running in-product, not just used to write code. README section on where/how Droid is used. Not a chatbot wrapper. |
| **Anthropic** | Not listed yet | A *named* community (not "students"); a problem you've lived or interviewed for; one thing done well, usable Monday; Claude load-bearing and you know where it fails users | Claude in a slot that matters (e.g. the independent verifier). A sentence on the failure mode for the people using it. |

Why these cluster: all four reward scoped access, a clean record of agent work, escalation when unsure, and honesty about what's real. That is the GridTwin story.

## 2. Tier 2 — fit without changing the product

- **SpacetimeDB** (prize TBD): wants it as the *core* real-time backend — live shared ops dashboard, agent coordination, shared state. Only if you'd want a real-time backend anyway.
- **Fetch.ai** (paste: $1.5k/$1k/$500; Fetch's own event page lists $1,250/$750/$500 + two $250 special recognitions, each with an internship interview): multi-agent on uAgents/Agentverse. Judging criteria not published.
- **Notability** (1 yr Pro + merch): keep the design journal in Notability from first sketch (handwriting, diagrams, audio). Cheap add-on if started tonight.
- **Mana.space** (prize TBD): build or plan in Mana, share the pages/chats publicly, show one moment it changed the build. Cheap if you'd use it.

## 3. Skip unless the product changes

Visa (shopping), Meta (human connection), Cartesia (voice must be core), SpaceXAI (Cursor + Grok API mandatory, space data), Moss, Capital One Nessie, Apify as the core, Bitdeer (only if you'd host models there), CRS (credit data must be central, live against sandbox).

## 4. Can't assess from the list

- "Coming soon": Armadin (security), Convex, Elastic, AfterQuery, Annapurna Labs, Bright Data; Atlassian text truncated.
- Criteria behind links not read: Coinbase x402 ($500 USDC per member per track, two tracks), Creao (three tracks, $5k total + Launchpad), Figma.

## 5. The decision that gates everything

**If GridTwin-derived:** target Hyphenate + Microsoft + Anthropic, plus Factory if Droid takes a real seat (e.g. the worker that drafts the fix or the work order).
Open question: Hyphenate says *finance*. GridTwin's README already carries the dollar bridge (PJM penalties, capacity-auction shortfall, lost output per MW) and Headroom computes congestion rent.
Is there a finance-ops seat for a generator or plant owner — settlement / penalty exposure, outage cost accounting, invoice or trade confirmation — that keeps the verifier + hold + escalation core? Decide that before writing code.
Hyphenate's own product runs order-to-cash, treasury, general ledger and close, procure-to-pay, and reporting on top of a company's ERP; those are the "seats" its rubric means. The plant-owner analogs are revenue settlement (order-to-cash) and penalty accrual or outage cost flowing into the close.

**If new:** still aim at the Tier 1 cluster; it is the highest-value theme and the criteria overlap. Pick a domain where one of you has lived the problem (Anthropic's bar) and realistic data exists (Hyphenate's bar).

Sources: fetch.ai event page (dates, venue, Fetch prizes), calhacks.io (3-day format, venue, sponsors), the pasted track table.
