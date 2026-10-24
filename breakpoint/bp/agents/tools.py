"""Tool registry with per-agent scopes (R15).

Each agent receives only its own tools. Calling anything outside the scope is
denied, logged as an event, and raised. Ledger and bank writes are not tools at
all: they need a credential that only the executor is issued (bp.ledger.service).
"""
from __future__ import annotations

from datetime import date

from bp.agents.events import emit

SCOPES: dict[str, set[str]] = {
    "matcher": {"read_sources", "create_break"},
    "investigator": {"read_sources", "read_breaks", "read_playbooks", "read_events", "write_case_note",
                     "set_root_cause", "match_playbook"},
    "preparer": {"read_case", "read_playbooks", "run_calculator", "create_proposal"},
    "outreach": {"read_directory", "read_case_summary", "send_message", "follow_up", "record_reply"},
    "approver": {"read_proposal", "read_evidence", "read_playbooks", "approve_proposal", "reject_proposal"},
    "executor": {"read_approved_proposal", "execute_proposal"},
    "learner": {"read_teach_sessions", "read_cases", "read_sop", "draft_playbook"},
    "auditor": {"read_events"},
}
ALL_TOOLS = set().union(*SCOPES.values())


class ToolDenied(PermissionError):
    pass


class AgentTools:
    def __init__(self, agent: str, impls: dict | None = None):
        if agent not in SCOPES:
            raise ToolDenied(f"unknown agent {agent!r}")
        self.agent = agent
        self.impls = impls or {}

    def allowed(self, tool: str) -> bool:
        return tool in SCOPES[self.agent]

    def check(self, conn, tool: str, sim_date: date | None = None, case_id: str | None = None) -> None:
        if not self.allowed(tool):
            emit(conn, self.agent, "tool_denied", f"{self.agent} may not call {tool}", case_id=case_id,
                 inputs={"tool": tool}, sim_date=sim_date)
            raise ToolDenied(f"{self.agent} may not call {tool}")

    def call(self, conn, tool: str, *args, sim_date: date | None = None, case_id: str | None = None, **kw):
        self.check(conn, tool, sim_date, case_id)
        fn = self.impls.get(tool)
        if fn is None:
            raise ToolDenied(f"{tool} has no implementation bound for {self.agent}")
        return fn(conn, *args, **kw)
