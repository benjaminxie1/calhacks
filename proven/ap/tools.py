"""In-process MCP tools Droid gets (droid-sdk[mcp]). Read-only except request_reverify, which queues work.

  get_work_order(work_order_id) -> ledger/work_orders/<id>.json
  get_rate_card() -> ledger/rate_card.yaml
  get_verification(work_order_id) -> latest ledger/evidence/VER-<id>-*.json or {"status": "none"}
  request_reverify(work_order_id, reason) -> queues a verifier run; returns a ticket
  list_invoices(contractor) -> ids in ap_inbox (for duplicate detection)
No tool pays, edits work orders, or touches the sim's truth.
"""
