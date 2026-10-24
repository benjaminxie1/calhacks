"""One DoD template per work-order type. Check kinds: telemetry(threshold|fresh|peer), visual, citation, safety.

Example (replace_fan_filter on INV-1):
  - telemetry fresh:     INV-1.last_update_ts within 5 min
  - telemetry threshold: INV-1.fan_rpm > 800
  - telemetry threshold: INV-1.heatsink_c <= model + 5 for the whole hold window
  - visual:              viewpoint INV-1/rear shows fan spinning / cabinet closed (verifier picks the viewpoint)
  - safety:              asset was de-energized while work was open (needs_deenergized)
  - citation:            the OEM manual clause the thresholds come from (URL cached)
Templates are data (dict/YAML), not free code. Claude may fill parameters; it may not add kinds.
"""
