# Ledger layout (a git repo of plain files)

| Dir / file | Written by | Shape |
|---|---|---|
| `work_orders/WO-001.json` | ops (sim dispatch) | `{id, asset, work_order, contractor, dispatched_at, eta, rate_usd, needs_deenergized, status}` |
| `ap_inbox/INV-2026-0031.json` | contractor (generator) | `{invoice_id, contractor, work_order_id, asset, line_items:[{desc, qty, unit_usd}], total_usd, completed_at, completion_note, photo_ref, invoice_date}` |
| `evidence/VER-WO-001-01.json` | verifier (Claude) | `{work_order_id, checks:[{kind, op, args, result, observed, at}], hold:{minutes, re_verified_at, passed}, viewpoint_used, verdict, written_by_model}` |
| `decisions/DEC-INV-2026-0031.json` | Droid matcher | `{invoice_id, decision: APPROVE\|HOLD\|DISPUTE\|ESCALATE, amount_usd, reasons:[...], release_condition, evidence_refs:[...], requires_human: bool, droid_session_id}` |
| `journal.csv` | Droid matcher (proposed), human (merged) | one row per entry; AP accrual on APPROVE, nothing on HOLD |

Droid may write only `decisions/` and `journal.csv`. Everything else is read-only to it.
Payment release is a human action outside this repo.
