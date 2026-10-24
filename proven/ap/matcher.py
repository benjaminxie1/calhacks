"""Droid matcher. Trigger: a new file in ledger/ap_inbox/.

Session(cwd=ledger/, autonomy LOW, restrict_tools allowlist, on_permission denies writes outside
DROID_WRITABLE, mcp_servers=[tools]). Streams ToolCall/ToolResult (steps) and EditAction (diffs)
to the finance view, asks for structured output = MatchDecision, writes decisions/DEC-<invoice>.json
and a proposed journal.csv row. If the Droid run fails validation, fall back to fallback.match()
and mark the decision `matcher: "fallback"` so the demo never silently fakes Droid's work.
"""
