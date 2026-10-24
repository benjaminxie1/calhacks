"""The closed action library. Playbooks compose these; a new action type needs code."""
from bp.models import Action

ACTIONS: dict[str, Action] = {
    # journal entries (amounts always come from bp.calculators)
    "reverse_unfilled": Action(type="journal_entry", template="reverse_unfilled"),
    "reverse_duplicate": Action(type="journal_entry", template="reverse"),
    "fee_refund": Action(type="journal_entry", template="adjust"),
    "post_dividend": Action(type="journal_entry", template="dividend"),
    "split_adjust": Action(type="journal_entry", template="split_adjust"),
    "reverse_deposit": Action(type="journal_entry", template="reverse_deposit"),
    "book_receivable": Action(type="journal_entry", template="receivable"),
    # people
    "notify_client_service": Action(type="notify", to_role="client_service"),
    "notify_client": Action(type="notify", to_role="client"),
    "request_info_custodian": Action(type="request_info", to_role="custodian_ops"),
    "request_info_broker": Action(type="request_info", to_role="broker_desk"),
    "request_funds": Action(type="request_info", to_role="client"),
    "source_correction_request": Action(type="source_correction_request", to_role="broker_desk"),
    "transfer": Action(type="transfer"),
    "hold": Action(type="hold"),
    "escalate": Action(type="escalate", to_role="controller"),
}
