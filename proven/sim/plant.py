"""5 MW solar plant: 4 inverters (INV-1..4), cooling, SCADA with per-asset `last_update_ts`.

Contract (to implement Sat morning):
  Plant(scenario) -> .tick(minutes) -> .telemetry(asset) -> dict with values AND last_update_ts
  .camera(asset, viewpoint) -> photo path chosen by hidden state (see camera.py)
  .dispatch(asset, work_order) -> ticket dict; the contractor "completes" after rate_card minutes
  .truth -> hidden ground truth; never exposed through tools
Faults mutate telemetry; F6 freezes values but not time, so only `last_update_ts` goes stale.
"""
