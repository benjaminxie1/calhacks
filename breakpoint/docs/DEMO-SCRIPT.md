# Demo video script (target 2:30–3:00)

The plan says the video opens with the split moment. Record at 1080p in a clean browser window with the dashboard at
`http://localhost:8000` (`make api`). Do one full dry run first.

## Before recording

1. `make demo` once, to confirm the numbers (it resets the dashboard database; that's fine).
2. In the dashboard: **Reset**. Set the ops lead to **you (UI)**, the controller to **scripted Sam Lee**, and the speed to **fast**.
3. Press **Play**. On Aug 13 the Teach mode badge shows 1. Press **Pause**. This is the cold-open state. If you're
   recording in takes, stop here and start the recording.

## Shots

| Time | Screen | Say (roughly) |
|---|---|---|
| 0:00–0:20 | Teach mode, the NFLX case open | "August 13th. NFLX just split 2-for-1. The custodian doubled everyone's shares; our ledger didn't. Breakpoint has never seen this, so instead of guessing it stopped and asked our ops lead." |
| 0:20–0:45 | Fill in Teach: trusted source **custodian**, action **split_adjust**, fact **split_on_symbol**. Submit. | "She tells it who's right, what to do, and what makes this case different. The learner turns that into a playbook branch anyone can read." Point at the drafted condition. |
| 0:45–1:00 | Approvals page: approve the fix and the drafted playbook | "Our controller approves both. The agent that prepared the fix can't approve it, and only the executor can write to the ledger." |
| 1:00–1:10 | Title card or Overview | "Breakpoint: an autonomous reconciliation team for a retail brokerage. It fills the trade-recon analyst and settlements-ops analyst seats." |
| 1:10–1:35 | Play to the end. Overview fills in: known breaks resolving | "Five systems, 22,000 orders, a full quarter. Partial fills, stale commissions, duplicate reports and missed dividends all resolve themselves, and each fix cites the playbook and who taught it." |
| 1:35–2:00 | Open the **October COST** case: agent timeline, branch, provenance | "October 15th, a COST split hits ten holders. Nobody was asked. It used the branch learned in August and cites Dana's lesson. Amounts come from calculators, never the model." |
| 2:00–2:15 | Case detail: **ask why** on the COST case | "Anyone can ask why, and the auditor answers from the event log with citations." |
| 2:15–2:35 | **Proof** page | "Scored against an answer key: 100% recall, 100% precision, 33 of 33 breaks in the right final state with the exact amounts, and the books match the true world to the cent." Point at the real-vs-simulated table. |
| 2:35–2:55 | **Inject break**: B1 on any account/symbol, then Step twice | "And you can try to break it. Pick a break, and it lands tomorrow. Here it is, detected and fixed." |

## Notes for the live judging table

- **Injection limits.** Live injection supports B1, B2, B3 and H2.
  - B2 needs a sim date after Aug 17 and a standard or active tier account. The premium fee didn't change, so the API
    says so for anything else.
  - B1, B3 and H2 surface when the custodian settles (T+1), so they can't be injected on the last day. The API refuses
    with a reason. Reset first if the replay has finished.
- **How long a fix takes.** B1 and H2 finish within a day of detection. B3 waits about two business days for the broker
  desk to confirm. Step a few times.
- **The reset button uses `confirm()`.** Click OK.
- **Running with Claude.** With a key in `.env`, the agents decide with Claude. A full replay makes about 90 model
  calls, so it's slower. For the live table, either pre-run it, or set `BP_LLM_AGENTS=investigator,outreach,auditor`
  so only those agents call the model.
