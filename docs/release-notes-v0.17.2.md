# v0.17.2 — modulation output visualisation and hydraulic comparison

- Add a dedicated boiler-output trend in kW from the configured modulation and burner signals, alongside modulation percentage and burner state.
- Persist both the initial modulation-derived output and the gas-calibrated modulation output in the five-minute system history, so the trend remains available after the live value changes.
- Add a modulation-estimated heat-flow Sankey using the same boiler → manifold → room layout as the hydraulic baseline.
- Allocate the modulation-derived boiler output across currently active UFH circuits by configured flow, then across each circuit's served rooms. This is explicitly an estimate, not measured heat delivery.
- Keep the gas-meter-reconciled experimental Sankey separate; it continues to wait for valid heating-gas meter deltas and is not replaced by the modulation estimate.
- Add regression coverage for persistence of modulation output history.
- No heating control, pump control, or boiler control behavior changes.
