"""Independent verifier (Claude). Gets the DoD checks and raw tool access. Never reads contractor or matcher prose.

Flow: run every check -> if all pass, hold HOLD_MINUTES sim-minutes -> re-run everything -> verdict.
Writes ledger/evidence/VER-<WO>-<n>.json with observed values, timestamps, viewpoint used, model id.
Fails closed: any tool error or ambiguous read is FAIL with the reason.
"""
