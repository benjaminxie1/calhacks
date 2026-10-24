"""FraudShield Swarm: specialist agents investigate a suspicious-transaction alert.

A transaction-monitoring rule raises an alert. Three specialists (graph analyst,
sanctions checker, behavioral profiler) each assess it from a scoped view of the
data and file findings into an append-only case file. Where they disagree, a
bounded resolution protocol asks each side to answer the other's structured
claim with new evidence. A deterministic policy, not an agent, turns the final
positions into a disposition and proposed actions. The synthesizer writes the
report from structured findings only. Nothing executes: every action is a
proposal for a human analyst.

All data is simulated: fictional people, accounts and watchlist.
"""
