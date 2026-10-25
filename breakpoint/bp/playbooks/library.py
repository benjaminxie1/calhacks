"""The closed action library. Playbooks compose these; a new action type needs code."""
from bp.models import Action

ACTIONS: dict[str, Action] = {
    # journal entries (amounts always come from bp.calculators)
    "share_credit": Action(type="journal_entry", template="share_credit"),
    "capital_credit": Action(type="journal_entry", template="capital_credit"),
    "cap_credit": Action(type="journal_entry", template="cap_credit"),
    "fee_credit": Action(type="journal_entry", template="fee_credit"),
    "tax_credit": Action(type="journal_entry", template="tax_credit"),
    "estimates_credit": Action(type="journal_entry", template="estimates_credit"),
    "gross_up_credit": Action(type="journal_entry", template="gross_up_credit"),
    # people
    "dispute_notice": Action(type="notify", to_role="landlord"),
    "send_payment_proof": Action(type="notify", to_role="landlord"),
    "notify_ap": Action(type="notify", to_role="accounts_payable"),
    "request_tax_bills": Action(type="request_info", to_role="landlord"),
    "request_backup": Action(type="request_info", to_role="landlord"),
    "hold": Action(type="hold"),
    "escalate": Action(type="escalate", to_role="controller"),
}
