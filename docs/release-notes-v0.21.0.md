# v0.21.0 — UFH topology audit and heat-delivery calibration diagnostics

## Added
- A UFH topology auditor covering circuit identity, floor and pump association, pipe length, demand-source mapping, served-room mapping, and configured flow coverage.
- Separate validation of thermostat demand sources and rooms served by each circuit. Shared and floor-wide demand configurations are preserved and are not treated as mapping errors.
- Per-floor configured-flow totals and per-room allocation weights. Shared circuits split their configured flow equally across explicitly served rooms, matching the existing boiler energy allocation convention.
- A dedicated `sensor.heating_intelligence_ufh_hydraulic_audit` diagnostic entity with detailed audit findings and heat-delivery calibration attributes.
- Daily comparison of gas-reconciled and modulation-estimated heat ledgers, including absolute differences and percentage deviations.

## Measurement boundaries
- Manual circuit flow readings are allocation weights, not independently measured thermal output.
- Gas-reconciled and modulation-derived energy remain estimates. Their agreement is a consistency check, not independent validation of delivered heat.
- Water-side heat transfer remains withheld unless fresh measured flow and supply/return temperature inputs are available.
- No room-level heat meter or physical room heat delivery is claimed.
- Existing circuit demand behaviour, pump timing and energy-allocation algorithms are unchanged.
