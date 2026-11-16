# Heating Intelligence 0.15.6 — Multi-zone thermal storage response

## Added

- Added offline experiment-analysis primitives for Recorder-shaped exports, including finite-value filtering, duplicate timestamp handling, bounded-gap trapezoidal power integration, room temperature endpoint response, and climate mode history.
- Added passive thermal-storage response diagnostics to Recorder-backed calibration. The analysis searches for clean coasting periods where combined AC input and the configured upstairs pump are off, room windows are known closed, and room temperatures are fresh.
- Reports room-air coast slopes, valid periods, sample exclusions, and operating-condition coverage in `thermal_model.historical_validation.thermal_storage_response_analysis`.
- Added regression tests for passive coast identification, AC/pump exclusion, unknown pump state, invalid power readings, unsupported units, endpoint coverage, and event provenance.

## Interpretation and limitations

- The passive coast analysis is observational. It does not estimate UFH slab temperature or absolute slab thermal capacity.
- A slow room-air response after heating shutdown is compatible with release of stored heat, but does not isolate screed from walls, furnishings, solar gains, or internal gains.
- Combined AC electrical power is used only to exclude active AC periods; it is not allocated to rooms or converted to delivered heat.
- Pump electrical power is used only to classify pump state; it is not treated as thermal output.
- The physical multi-zone solver remains withheld until absolute capacities, inter-zone conductances, and independently validated thermal input are identified.

## Home Assistant testing

After installing 0.15.6, open the integration's global diagnostic entity and inspect:

`thermal_model → historical_validation → thermal_storage_response_analysis`

The upstairs pump must be configured with an ID containing `upstairs` or a floor value of `upstairs`, and the AC power sensor must be configured. Configure a boiler burner sensor and window contacts to strengthen the passive-period qualification. The first Recorder analysis is bounded by the configured calibration history window and refreshed on the existing six-hour cache interval.
