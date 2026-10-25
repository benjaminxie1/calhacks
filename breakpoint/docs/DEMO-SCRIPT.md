# Demo video script (target 2:30–3:00)

The video opens on the novel case: a landlord grosses up a lease that doesn't allow it. Record at 1080p in a clean
browser window with the dashboard at `http://localhost:8000` (`make api`). Do one full dry run first.

## Before recording

1. `make demo` once, to confirm the numbers (it resets the dashboard database; that's fine).
2. In the dashboard: **Reset**. Set the lease admin lead to **you (UI)**, the controller to **scripted Sam Lee**, and
   the speed to **fast**.
3. Press **Play**. On Mar 10 the Teach mode badge shows 1. Press **Pause**. This is the cold-open state. If you're
   recording in takes, stop here and start the recording.

## Shots

| Time | Screen | Say (roughly) |
|---|---|---|
| 0:00–0:20 | Teach mode, the Bayshore case (Golden Wok Dumpling House #12) open | "March 10th. Our landlord at Bayshore grossed up variable expenses to 95% occupancy. Our lease doesn't allow a gross-up. Breakpoint has never seen this, so instead of guessing it stopped and asked our lease admin lead." |
| 0:20–0:45 | Fill in Teach: trusted source **lease**, actions **gross_up_credit** then **dispute_notice**, fact **gross_up_not_in_lease**. Submit. | "She tells it who's right, what to do, and what makes this case different. The learner turns that into a playbook branch anyone can read." Point at the drafted condition. |
| 0:45–1:00 | Approvals page: approve the credit and the drafted playbook | "Our controller approves both. The agent that prepared the credit can't approve it, and only the executor can write to the books." |
| 1:00–1:10 | Title card or Overview | "Breakpoint: an autonomous CAM audit team. Every spring each landlord sends a common-area-maintenance bill. Errors are common enough that whole firms exist to audit them, and nobody in-house has time to check 40 of them line by line." |
| 1:10–1:35 | Open the **Mar 3 Mission Plaza** case(s) for Hayward (LSE-007): source values, root cause | "Mission Plaza billed our Hayward store $36,965. The lease says $24,127. Breakpoint found all five problems: share on occupied area instead of the whole center, a roof replacement, the cap left off, taxes above the county's bill, and a management fee on everything. $12,838 credited, each dollar tied to a lease clause." |
| 1:35–1:55 | Play to the end. Open the **May 5 Coastline** case: three stores, branch, provenance | "May 5th, Coastline grosses up three of our stores the same way. Nobody was asked. It used the branch learned in March and cites Dana's lesson. Amounts come from calculators, never the model." |
| 1:55–2:10 | Case detail: **ask why** on the Coastline case | "Anyone can ask why, and the auditor answers from the event log with citations." |
| 2:10–2:30 | **Proof** page | "Scored against an answer key: 100% recall, 100% precision, 33 of 33 errors in the right final state with the exact amounts, $54,318 in overcharges credited, and the books match the true world to the cent." Point at the real-vs-simulated table. |
| 2:30–2:55 | Reset, Play to about mid-April, Pause. **Inject overcharge**: "Management fee at 15% of everything" on any store, then Step twice | "And you can try to break it. Pick an overcharge and a store whose statement hasn't arrived. It lands tomorrow. Here it is, detected and credited." |

## Notes for the live judging table

- **What can be injected.** Six types: share on occupied area, management fee at 15% of everything, capital item,
  taxes above the county bill, missed estimate payment, gross-up the lease doesn't allow. The store picker lists only
  stores whose statement hasn't arrived and fits the type (a fee needs a fee clause; a gross-up needs a lease without
  one). The nine clean stores arrive between Apr 13 and May 28, so there's always a choice until the last day. After
  that, Reset.
- **How long a fix takes.** A credit of $2,500 or less finishes the day it lands. A larger one waits one business day
  for the controller. Taxes ask the landlord for the tax bills and wait for the reply. Step a few times.
- **Rehearsed.** 48 injections (every type, four dates, two stores each) were all detected and resolved with the
  answer key's amounts, with the ledger still equal to the true world.
- **The reset button uses `confirm()`.** Click OK.
- **Running with Claude.** With a key in `.env`, the agents decide with Claude. A full replay makes many model calls,
  so it's slower. For the live table, either pre-run it, or set `BP_LLM_AGENTS=investigator,outreach,auditor` so only
  those agents call the model.
