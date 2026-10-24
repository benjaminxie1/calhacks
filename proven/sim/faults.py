"""Fault library. Three only (PLAN.md scope):

F0 control      nothing wrong; correct decision is APPROVE on a clean invoice, NO work on none.
F3 fan relapse  INV-1 fan_rpm 0, heatsink climbs; a reset clears the alarm for ~10 min then it returns.
                The canonical false "done": the contractor's note says temps normal; the hold catches it.
F6 stale SCADA  INV-3 telemetry frozen at last good values; only last_update_ts is stale.
                Every threshold check passes; only a freshness check or a camera view catches it.
Each fault: apply(plant), observe(plant) -> telemetry overrides, on_work(plant, asset, work_order) -> bool fixed,
            is_resolved(plant) -> bool (hidden truth).
"""
