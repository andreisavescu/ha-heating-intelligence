# Heating Intelligence v0.20.2

## Added

- Attic-floor room conductance coverage and aggregate W/K consistency diagnostics.
- Explicit attic-to-outdoor roof-path readiness using configured roof U-value and area only.
- Per-room envelope, attic-floor and ventilation path diagnostics.
- Network-level pending path, unresolved reference and physical-simulation blocker summaries.
- Measured attic temperature as an upstairs-only predictor in normalized multi-zone response identification.
- Recursive coupled multi-zone historical holdout replay, with per-room and aggregate MAE, RMSE, bias, persistence baseline and data-quality exclusions.
- Lovelace diagnostics for attic paths, network readiness and held-out replay performance.

## Model boundaries

- The replay uses normalized G/C response coefficients, not absolute W/K conductances.
- Attic-floor room shares are not auto-distributed; each upstairs room requires an explicit configured share.
- Roof conductance requires explicit roof U-value and roof area. Attic-floor insulation is not assumed to insulate the roof.
- Missing room, attic and UFH slab capacities remain explicit blockers. Boiler/pump telemetry is not treated as a dynamic thermal node or measured room heat delivery.
- The physical RC solver remains gated until absolute parameters and independent validation are available.

## Compatibility

- Existing heating control, energy allocation, pump timers, gas-reconciled energy and burner-modulation accounting are unchanged.
- Recorder data remains local to Home Assistant; raw historical samples are not copied into persistent integration storage.
