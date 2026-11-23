## v0.19.1 — Route energy graphs to the System tab

- Ensure instant modulation power and all daily, weekly, monthly and heating-season energy graphs appear in the System tab rather than the Overview tab.

## v0.19.0 — Separate modulation and gas-reconciled energy periods

- Expose two independent room-energy series: burner-modulation estimates and gas-meter-reconciled useful heat.
- Add daily, weekly, monthly and heating-season sensors for both methods, allocated by configured active UFH circuit flow.
- Add matching whole-boiler period totals; do not create instantaneous gas-reconciled power.
- Retain the existing 66/34 daily modulation calibration and gas-meter reconciliation assumptions.
- Replace the System tab energy Sankeys with the eight requested room-energy period graphs, retain instant modulation power, and keep System trends.
- Remove orphaned duplicate modulation entities from the short-lived intermediate unique IDs.

## v0.18.9 — Restore historical energy Sankey diagrams

- Render daily, weekly, monthly and heating-season gas-reconciled room energy from their recorded kWh allocations, independently of current UFH circuit or pump activity.
- Keep the live modulation power Sankey gated by confirmed active configured UFH flow.
- Keep historical allocations labelled and scaled as energy (kWh); do not reinterpret them as live thermal power.
- Reuse the former per-room UFH energy entity unique IDs for burner-modulation daily, weekly, monthly and lifetime totals, preserving entity registry references and Recorder history; gas-reconciled entities remain separate.
- Correct calculation metadata: the smoothed gas-derived calibration factor is applied before modulation energy is allocated to rooms.
- No energy ledgers, calibration logic, gas correction, room allocation calculations or heating-control behavior changed.

## v0.18.8 — Fix Heating Intelligence card render failure

- Remove the obsolete estimated thermal-output metric from the UFH circuit-flow section. It referenced an undefined `totalPower` variable and caused a JavaScript `ReferenceError` during card rendering.
- Keep active circuit count, configured active flow, per-manifold flow and individual circuit status.
- Do not infer thermal output from configured flow.
- No backend calculations or heating-control behavior changes.

## v0.18.3 — Use modulation as the sole live boiler-output signal

- Remove the burner binary as a gate for live boiler output, modulation-estimated energy and room exposure; the binary can lag modulation transitions by approximately 30 seconds.
- Treat modulation above 0% as active output and 0% as zero output, independent of burner state.
- Use modulation-active intervals for room exposure and gas-reconciliation time weighting; do not substitute rated boiler output when modulation is unavailable.
- Keep burner state available as a diagnostic only. No boiler, pump, thermostat or heating-control behavior changes.
- Add a per-room **Estimated Modulation Heating Power** sensor (W), allocated using the same active configured UFH flow weights and served-room split as the Home view and modulation Sankey.
- Make each Home room's live **Heat** wattage tappable so it opens that room's new modulation-power entity in Home Assistant; retain the legacy UFH power entity unchanged.

## v0.18.2 — Fix gas-reconciled daily and heating-season Sankeys

- Read daily gas-reconciled room energy from the latest ISO-date bucket returned by the energy model.
- Read heating-season room energy from the current-season flat room map exposed by the model, rather than looking up the season key twice.
- Keep weekly and monthly room-energy handling unchanged.
- Label Sankey summaries with the correct reporting period.

## v0.18.1 — Calibrated room power in the Home view

- Replace the legacy 9°C hydraulic per-room watt figures in the Home room tiles with live burner-modulation output allocated using the same active configured UFH flow weights and served-room split as the modulation Sankey.
- Prefer the gas-calibrated modulation output; use the raw modulation estimate only while a calibration value is not yet available.
- Show an em dash when no valid burner output or eligible active UFH flow is available rather than displaying a legacy estimate.
- Keep room demand status and temperature display unchanged.

## v0.18.0 — Gas-reconciled boiler energy and heating-season reporting

- Retire the assumed 9°C hydraulic calculation as the primary boiler/room power model; retain its historical estimates as clearly labelled legacy diagnostics.
- Use burner modulation as the live boiler output signal: 25 kW × modulation percentage, with a 3.2 kW minimum while firing, multiplied by the daily smoothed calibration factor.
- Learn calibration from the previous complete day's gas-reconciled useful heat divided by its raw modulation estimate. Stage the new factor for the following day using 66% of the previous factor + 34% of the observed daily factor.
- Expose the previous, observed daily and smoothed calibration factors in the model diagnostics.
- Keep heating-gas-derived useful energy authoritative for room allocation and reconciliation. Calibrated modulation output and active configured UFH circuit-flow weights determine the room exposure shares; DHW is excluded.
- Add gas-reconciled room-energy Sankeys for daily, weekly, monthly and heating-season periods.
- Define the heating season as 1 September through 1 May. Persist seasonal room, allocated and unallocated energy totals separately from the 30-day daily ledger.
- No boiler, pump, thermostat or heating-control behavior changes.

## Testing

- CI passed on PR #67: unit tests, integration compilation and Lovelace JavaScript syntax validation.

## v0.17.6 — Card entity tap-through fix

- Fix entity lookup in the Lovelace card by normalizing friendly names and entity IDs, including diacritics and punctuation, instead of relying on exact friendly-name matches.
- Extend native Home Assistant more-info/history tap-through to entity-backed values in room lists, pump metrics, and room demand-cycle and thermal analytics sections.
- Keep values without a matching entity display-only; preserve keyboard activation for linked values.
- No boiler, pump, or heating-control behavior changes.

## v0.17.5 — Calculation formulas and entity tap-through

- Add `calculation_description`, `calculation_formula`, and `calculation_assumptions` attributes to calculated Home Assistant entities across boiler, modulation, UFH, room analytics, attic heat-flow, efficiency, and pump metrics. Directly measured/source entities are not assigned invented formulas.
- Expose the latest boiler-modulation room-allocation interval diagnostics, including burner state, modulation, estimated output, interval duration, room allocation weights/shares, interval energy, and exposure.
- Make entity-backed values in the card's Room Details section keyboard-accessible and open Home Assistant's standard more-info/history dialog when tapped. Values without a matching entity remain display-only.
- No boiler, pump, or heating-control behavior changes.

## v0.17.4 — Room modulation energy registration and Sankey tab placement

- Fix per-room modulation-estimated energy sensors so they register with the correct room device metadata.
- Present daily, weekly, monthly and lifetime modulation energy sensors consistently with the existing room thermal-energy entities.
- Expose the estimate method and source in sensor attributes; keep the entities visible alongside the other room sensors.
- Keep both Power Sankey diagrams and both Daily Energy Sankey diagrams in the System tab, not Analysis.
- Preserve the existing hydraulic and modulation allocation calculations and all older energy entities.
- No heating, pump or boiler control behavior changes.

## Upgrade notes

- The four modulation energy sensors are created for every configured room.
- Existing sensors are retained; no entity cleanup or history deletion is performed in this release.

## Testing

- CI passed on PR #61, including unit tests, integration compilation and Lovelace JavaScript syntax validation.

## v0.16.1 — responsive three-layer heat-flow visualisation

- Simplify the Sankey diagram to three layers: combined boiler output, downstairs/upstairs manifolds, and served rooms.
- Connect each manifold directly to its served rooms; remove the intermediate UFH circuit layer.
- Scale flow widths according to estimated thermal power at each branch.
- Make the diagram responsive on mobile screens without forced horizontal scrolling.
- Preserve the 0.16.0 heat-flow and System trends functionality.

## v0.16.0 — Sankey heat-flow visualisation

- Replace the basic system overview with a proportional Sankey-style heat-flow diagram.
- Visualise active estimated UFH thermal power from each manifold through active circuits to their served rooms.
- Scale link widths to the estimated power allocated to each branch and show live wattage totals.
- Keep configured flow and estimated thermal output clearly distinguished from measured boiler output and room heat delivery.
- Retain the System trends dashboard and selectable 24-hour, 7-day and 30-day history views.

## v0.15.19 — room settings menu and editable heating demand

- Group room floor, heating-demand, air-quality, area, ACH and window-contact settings under a single **Edit rooms** menu.
- Add the ability to change or clear a room's heating-demand entity after creation.
- Give the UFH circuit configuration menu a clear **Configure UFH circuits** label.

## v0.15.18 — room power registration and floor-based Home layout

- Fix room heating power sensor device association so the per-room power entity registers correctly.
- Organize the Home view into separate Downstairs rooms and Upstairs rooms sections, in that order.
- Show Downstairs and Upstairs heating pumps side by side beneath the room sections.

## v0.15.17 — compact home overview layout

- Display the Rooms and Heating pumps panels side by side in a two-column layout, matching the two-column summary above.
- Tighten room and pump row typography and spacing so the panels remain usable on mobile screens.

## v0.15.16 — Room energy registration and UFH layout

- Fix room thermal energy sensor device association so all four energy sensors register under their room device.
- Display UFH circuit zones in a two-column layout, with active flow prominent and configured flow in smaller text below.

## v0.15.15 — room thermal energy and manifold cleanup

- Add persistent per-room estimated UFH thermal energy sensors for today, this week, this month and lifetime total.
- Estimate delivered heat from manually configured circuit flow and the assumed 9°C water temperature drop, integrating only confirmed active circulation.
- Separate each circuit's demand-trigger rooms from its served rooms; split shared-circuit energy equally across served rooms to avoid double-counting, including sensorless rooms.
- Expose the estimate method in sensor attributes.
- Allow rooms to be created with only a name and floor; demand, temperature, humidity, CO₂ and window entities are optional and can be configured later.
- Remove the obsolete manifold electrical-stage diagnostic entities while preserving pump power, energy, cycle and runtime statistics.
- Preserve the UFH circuit flow section in the Demand view.

## v0.15.14 — place UFH flow in Demand view

- Route the UFH circuit flow section to the Demand tab alongside the demand timeline.
- Keep the weekly room-demand heatmap in the Analysis view.

## v0.15.13 — UFH hydraulic flow display

- Add a UFH circuit flow section to the Demand tab, grouped by downstairs and upstairs manifold.
- Show every configured circuit as Running or Stopped, with configured and active flow in L/min.
- Summarize active circuit count, total active flow and estimated thermal output in kW.
- Keep manually configured flowmeter values static; active flow is zero while a circuit is stopped.
- Clarify that thermal output is an estimate based on the assumed 9°C water temperature drop, not measured heat delivery.

## 0.15.5 — simplified card and readable demand timeline

- Add a compact Home view as the default card tab, surfacing room demand, indoor temperature, pump state and water inlet temperature.
- Keep detailed diagnostics available in the Analysis view, with Demand and Predictions in their own tabs.
- Set the room-demand timeline to a rolling 4-hour default, with 2-hour and 24-hour views for closer inspection or broader context.
- Improve demand-cycle tooltips with exact start/end timestamps and cycle duration.
- Keep this release focused on the Lovelace card; no heating-control behavior or backend sensor configuration changes.

## 0.15.4 — Recorder-backed validated calibration learning

- Persist compact, validated room-response calibration snapshots derived from Home Assistant Recorder history across integration restarts.
- Promote room-response fits only when they pass chronological holdout validation, coefficient-stability checks, and predictor identifiability requirements.
- Preserve the previously accepted calibration when new Recorder data is insufficient or a candidate fit fails validation.
- Expose the accepted calibration version, promotion timestamp, accepted rooms and rejection diagnostics in the thermal-model diagnostics.
- Keep raw Recorder history out of persistent integration storage; only the compact accepted calibration state is retained.
- Keep normalized room-response rates in 1/hour clearly distinguished from absolute physical conductances, thermal capacities, delivered heat and room heat shares. The multi-zone physical simulator remains withheld until those physical quantities are independently identified.

## 0.15.0 — multi-zone thermal model foundation and Recorder history coverage

- Add a Home Assistant-independent lumped RC network solver with bounded integration steps, explicit energy accounting for gains and fixed boundaries, and a maximum-substep safety guard.
- Map configured room temperatures, demand/window states, CO₂, humidity and ACH alongside outdoor weather, attic, UFH floor context, boiler water temperatures, burner/modulation and pump telemetry.
- Accept optional measured room volume and estimate ventilation conductance only when both room volume and ACH are configured.
- Report air-only sensible heat capacity from configured room volume separately from effective room/building capacity. Ventilation conductance uses the configured ACH and volume, with an illustrative ±10% ACH sensitivity (not a confidence interval); missing room volumes remain explicitly uncovered.
- Expose roof conductance only when roof U-value and area are explicitly configured.
- Surface existing learned room heating/coasting rates, confidence and sample counts as calibration evidence; do not misrepresent temperature slopes as physical heat capacities or envelope U-values.
- Query a configurable 7–365-day window of configured Home Assistant Recorder history locally every six hours (90 days by default) and show per-entity data coverage, native state-change counts and distinct states for calibration planning. Raw history remains in Recorder and is not copied into integration storage or sent externally.
- Build a transient 30-minute aligned calibration dataset from room temperatures, demand/window states, weather, solar, attic, gas, boiler, AC, water-temperature and configured pump telemetry. Last-known values are carried forward only up to 45 minutes; explicit `unknown`/`unavailable` states invalidate the value. Report per-channel freshness and core/room-state coverage. Aligned rows remain in coordinator memory for subsequent fitting and are not persisted.
- Validate the weather-normalized daily gas model on a chronological 70/30 split: only the earlier segment is used to fit coefficients and reference weather; the later segment is scored against a training-mean baseline with MAE, RMSE and R². Small samples without at least 12 training and 6 holdout days are withheld.
- Aggregate room-level multi-zone holdout errors into a house-level, interval-weighted validation summary. Rooms without valid holdout fits are counted separately; this remains a prediction diagnostic, not independent plant-energy validation.
- Combine the aggregate cooling time-constant resampling range with the whole-house heat-loss assumption range to report an aggregate building-capacity sensitivity envelope. This is not a confidence interval and is never allocated to individual rooms or slabs.
- The RC solver now reports stored-energy change, integrated internal gains, fixed-boundary heat exchange and energy-balance error. It rejects pathological integration requests requiring more than 100,000 explicit substeps.
- Expose a per-room physical-parameter readiness matrix with air-only versus effective capacity, unresolved envelope conductance, and each unidentified inter-zone link. The simulator remains withheld while these physical parameters are missing; aggregate capacity is not distributed and normalized G/C rates are not relabeled as W/K.
- Fit a first-pass non-negative multi-zone free-coasting response model from simultaneous room and outdoor temperatures. It requires all configured rooms to have known demand-off/closed-window states and low measured solar radiation; configured AC and boiler telemetry must also confirm no active heating. It reports chronological holdout error and normalized `G/C` response rates in 1/hour. These are not absolute W/K conductances or room capacities; the physical simulation remains withheld until absolute capacities and links are independently identified.
- Keep the physical simulation withheld until required thermal capacities and conductive links are identified. Solar/internal gains and UFH/plant heat output remain uncalculated where calibrated inputs are missing.

## 0.14.4 — manifold pump stage visibility

- Add a dedicated valve/pump stage panel to each manifold card in the Lovelace overview, including current detected stage, valve-open time, inferred pump-start time, valve-to-pump delay, idle baseline and valve actuator signature.
- Add diagnostic sensors to each manifold device for detected stage, valve opening timestamp, inferred pump-start timestamp, valve-to-pump delay, idle power baseline and valve actuator power level.
- Preserve existing pump power, energy, runtime, cycle and running-state entities.
- Stage detection remains an electrical-signature estimate, not a direct measurement of water flow.

## 0.14.3 — manifold pump-stage diagnostics and daily thermal finalisation

- Distinguish the initial manifold valve-actuator electrical signature from the later sustained pump electrical signature.
- Track valve opening and inferred pump startup independently for each configured manifold.
- Expose stage, valve-open timestamp, inferred pump-start timestamp, idle baseline and valve signature level in the overview pump attributes.
- Optionally configure the boiler burner binary sensor and modulation sensor; capture their values when a pump start is inferred.
- Label pump-stage detection as an electrical-signature estimate, not a direct measurement of water flow.
- Finalise the previous local calendar day's heat-loss estimate at 02:00 and persist it so subsequent refreshes do not recalculate it.
- Preserve rolling 7-day and 30-day calculations. Record daily solar-radiation average as context; it is not applied as an unvalidated correction to the physical estimate.
- Add unit tests for pump-stage transitions and delayed, idempotent daily finalisation; run them in GitHub Actions.

## 0.14.2 — room air-quality entity registration fix

- Fix room humidity and CO₂ entities failing to resolve their configured room device association.
- Preserve the existing entity IDs and room-device grouping; no room reconfiguration is required.

# HA Heating Intelligence

<p align="center">
  <img src="brand/icon.png" alt="HA Heating Intelligence logo" width="180">
</p>

A read-only Home Assistant custom integration for analysing underfloor-heating behaviour. It does not control thermostats, pumps, boilers, or HVAC equipment.

## Current scope (0.16.1 development)

- Editable multi-step house profile for envelope construction, insulation, glazing/orientation/shading, heating plant, thermal mass, ventilation, occupancy and internal gains. Profile completeness is reported separately from measured thermal performance; no U-values or EPC rating are inferred from insulation thickness alone.
- Optional per-room humidity and CO2 sensor configuration, native live Home Assistant sensors, combined room-level interpretation and whole-house/floor coverage, averages, highest-CO2 room and humidity exception summaries. Room setup also accepts optional measured ACH values from ventilation/CO2-decay experiments; existing rooms can add or change ACH and window contacts in the room-area settings flow.
- Rolling self-learning room thermal-response model. It learns separate heating and coasting temperature-change rates from non-overlapping, same-mode intervals with a configured window contact confirming closed windows, over a rolling 30-day window. It reports sample sufficiency and confidence and produces 1/3/6/12-hour projections when enough data exists. The overview combines these room models with the existing measured whole-house heat-loss estimates.
- The overview card exposes the house profile, measured thermal-performance status, whole-house and floor air analysis (including entered room ACH), and learned room model. All outputs remain read-only and descriptive; occupancy, solar gains, thermal storage and operating changes can affect observed rates.
- Optional personal weather-station configuration: select a Home Assistant weather entity and/or individual outdoor temperature, solar radiation, wind speed/direction/gust, humidity, dew-point and pressure sensors. Standard weather attributes are used as fallbacks where available.
- Optional attic temperature and humidity sensors. The integration compares upstairs average temperature, attic temperature and outdoor temperature, then estimates conductive heat flow across the roof and upstairs ceiling only when explicit U-values and areas are configured. No U-value is inferred from insulation thickness alone.

### 0.14.0 model interpretation

- House-profile completeness is a count of populated optional fields, not a rating of building quality. Insulation thickness and construction labels are never converted directly into U-values.
- Whole-house heat-loss estimates retain their explicit assumptions (boiler seasonal efficiency, AC heating COP and configured envelope area) and report the number of valid completed days used.
- CO2 bands are indicative: below 800 ppm, 800–1199 ppm, 1200–1999 ppm and 2000 ppm or higher. Humidity bands are below 30%, 30–60% and above 60%. Room exceptions remain visible even when the whole-house average is lower.
- Each thermal mode needs at least six valid non-overlapping intervals spanning two hours, with a valid room demand state and a configured, available window contact confirming the windows are closed. The integration treats the configured demand entity being unavailable as no demand; a missing entity or an "unknown" state is not considered valid. When an AC power sensor is configured, intervals with input above 50 W, intervals while that sensor is unavailable, and legacy samples that predate AC-state tracking are excluded from room-rate learning until enough clean history is collected. The model learns from the rolling last 30 days and reports "learning" rather than inventing a forecast when evidence is insufficient. Forecasts assume the current demand mode and closed windows continue; they are withheld while the window is open or its contact is unavailable, and while detected AC electrical input is active or unverified. Solar gains and occupancy can still influence the learned rates.


## Current scope (0.14.0)

- Optional per-room relative humidity and CO2 sensors, configurable when adding a room or later from options.
- Live room humidity and CO2 readings exposed as native Home Assistant sensors and shown with indicative comfort/ventilation bands in the card.

## Current scope (0.13.2)

- Add rooms through the Home Assistant UI, assigning each room to the downstairs or upstairs floor.
- Change an existing room's floor assignment; the currently saved floor is preselected.
- Correlate room heating demand with the corresponding manifold pump.
- Optionally monitor room window contacts.
- Configure the underfloor-heating supply-water temperature sensor.
- Monitor ground-floor and upstairs manifold pump power (W), cumulative energy (kWh), and running state.
- Track daily pump energy, runtime, cycle counts, and room-demand hours.
- Persist room-level demand history and calculate daily, Monday-based weekly, and calendar-month demand hours.
- Track room heating cycles, including daily/weekly/monthly cycle counts, lifetime cycle count, average completed cycle duration, shortest/longest weekly cycles, and the current cycle duration.
- Expose room analytics as individual Home Assistant sensors and in the Lovelace overview card.
- Group entities into one central house device, one device per configured room, and one device per manifold pump. On setup, repair existing entity-registry device assignments using their existing unique IDs, preserving entity IDs and history. Secondary analytics and counters are marked as diagnostic entities.
- Provide a Lovelace card with whole-house overview, pump readings, room details, and floor-level demand/pump correlation.
- Analyse room thermal behaviour: temperature changes over 1, 6 and 24 hours, average heating response time to a 0.5 °C rise, cooling rate while there is no demand and windows are closed, and estimated hours per 1 °C of heat retention.
- Measure how long windows remain open while a room is requesting heat.
- Optionally collect outdoor temperature, solar radiation, indoor–outdoor ΔT, cumulative gas consumption, and three independent cumulative electricity counters for the downstairs manifold pump, upstairs manifold pump, and boiler pump.
- Calculate daily heating degree days (18 °C base), daily gas and per-pump/combined electricity energy, each pump's percentage share, and gas kWh per degree day; calculate exploratory correlations between daily gas/electricity use, degree days, ΔT, and solar radiation.
- Retain sparse raw room-temperature, environmental, and cumulative meter observations for up to 400 days so derived thermal and efficiency metrics can be recalculated.

## Stage 4: operational intelligence

The overview card now includes explainable, rule-based operational insights derived from room, floor, pump, and thermal observations. These include a floor requesting heat while its pump is stopped, sustained window-open time during demand, frequent daily room heating cycles, slow observed heating response (only with at least three response samples), and elevated observed cooling rates. These are conservative diagnostic prompts, not fault declarations or proof of causation. The integration remains read-only.

Version 0.6.0 also removes the two retired legacy daily pump-electricity entities from the entity registry by their exact historical entity IDs. Current pump power, cumulative energy, and whole-house electricity calculations are unchanged.

## 0.6.1 — room demand availability

Room Heating Demand binary sensors now remain available whenever the integration coordinator is healthy. The configured source entity's availability is interpreted only as the demand signal: any present state (`on`, `off`, or `unknown`) means demand; `unavailable` or a missing source means no demand (`off`). A source becoming unavailable no longer makes the Heating Intelligence entity itself unavailable.

## 0.7.0 — advanced diagnostics

- Room efficiency score (0–100) with visible component contributions. Scores are withheld until at least 12 temperature samples and two valid metrics are available.
- Observed heat-loss indicator based on closed-window, no-demand cooling rate (°C/h), plus the existing heat-retention estimate. This is not a physical heat-loss coefficient in W/K; room area and measured delivered heat are not available.
- Boiler consumption analysis combines gas energy, gas per heating-degree-day, and heating-system electricity. The gas/electricity ratio is a consumption proxy, not boiler combustion efficiency. Combustion efficiency requires measured useful heat output.
- Historical anomaly prompts compare daily gas/electricity consumption with at least three prior recorded days and flag unusually high cooling or slow heating response. Weather, occupancy and incomplete current-day readings can affect comparisons.
- The Lovelace card adds per-room scores, score components, cooling indicators, boiler consumption proxies and historical anomaly prompts.

All diagnostics are read-only and heuristic. Missing or insufficient data is shown as collecting/unavailable rather than assigned a misleading score.

## 0.7.1 — room floor-area analysis

- Configure an optional floor area (m²) when adding a room, or later through **Set room floor areas** in the integration options.
- Existing room configurations remain valid; areas are optional and can be left blank.
- The room detail and diagnostics card show configured room areas and per-floor total configured area.
- When area and valid closed-window cooling observations are available, floor cooling is summarised as an area-weighted observed cooling rate. This supports more representative floor comparisons but is still not a physical heat-loss coefficient (W/K).

## 0.8.1 — predictive intelligence and compact dashboard

- Add explainable room temperature forecasts for 1, 3 and 6 hours, time-to-comfort and time-below-comfort estimates.
- Add a configurable comfort target (16–25 °C, default 21 °C).
- Add end-of-day gas and heating-electricity run-rate projections with confidence and explicit limitations.
- Expose predictions, recent room-temperature history and 14-day energy history through the existing overview sensor attributes; no prediction entities are added.
- Compact the Lovelace card with collapsible analysis, room temperature history/forecast charts and daily energy history.

## 0.8.2 — automatic card resource versioning

- Automatically register the Lovelace card resource when using storage-managed dashboard resources.
- Automatically update the resource URL to match the installed integration version, forcing browsers and the Home Assistant Android app to fetch the new card after an integration update.
- Preserve the existing resource entry rather than requiring users to edit its URL on every release.
- YAML-managed Lovelace resources remain user-managed; update their URL query version manually when upgrading.

## 0.10.1 — floor thermal performance, weather normalisation and area-input fixes

- Add downstairs/upstairs comparisons using area-weighted room temperature, observed closed-window cooling rate, heating response time and daily demand hours.
- Show the number and percentage of rooms with configured surface areas so incomplete area coverage is visible.
- Reuse the existing optional room-area settings; no room reconfiguration is required.
- Fix the missing room-area input in the Set room floor area options form when no value was previously configured.
- Allow room areas to be entered to 0.01 m² precision (for example, 10.92 m²), both when adding a room and when editing its area.
- Add a conservative daily gas-use regression using completed days with heating degree days, indoor–outdoor ΔT and solar radiation.
- Report actual gas use, model-expected gas use and a weather-normalised comparison index at median observed weather conditions.
- Require at least 21 complete days with gas, outdoor temperature, ΔT and solar data before presenting the model.
- Keep all calculations descriptive and read-only; no physical heat-loss coefficient or boiler efficiency is inferred.

## 0.13.2 — completed-day thermal performance

- Finalise daily W/m²K and W/K once per local calendar day using the previous completed day, rather than a fluctuating live estimate.
- Calculate 7-day and 30-day W/m²K from combined delivered-heat and degree-hour totals over completed days only.
- Split degree-hour integration at local midnight so the final sample interval is attributed to the correct day.
- Keep estimated delivered heat today as a live diagnostic; thermal performance values use completed-day data.

## 0.13.1 — configuration-flow fix

- Fix optional global sensor selectors so they can be left blank when configuring the integration, including the optional return-water temperature sensor.
- Preserve the complete 0.13.0 thermal-performance and AC-heating functionality; no new sensors or settings are introduced.

## 0.13.0 — thermal performance and delivered heat

- Use the heating-only cumulative gas meter for heating analysis; domestic hot water gas is excluded. Changing the selected meter establishes a fresh baseline, and annual resets do not create negative consumption.
- Estimate useful delivered heat from gas energy multiplied by the configurable seasonal boiler-efficiency assumption (default 90%), plus AC electricity multiplied by a configurable heating COP (default 3.0). These are assumptions, not measured heat output.
- Integrate indoor–outdoor ΔT over time to calculate daily degree-hours, then estimate whole-house heat-loss coefficient (W/K) and normalized W/m²K.
- Add daily and rolling 7-day/30-day W/m²K estimates with minimum data thresholds.
- Expose estimated delivered heat today, daily W/K, and daily/7-day/30-day W/m²K as diagnostic Home Assistant sensors.
- Configure boiler efficiency, AC electrical power and cumulative energy sensors, AC heating COP (default 3.0), and total external envelope area (default 361.26 m²). AC energy is treated as heating-season consumption; do not use a counter that includes summer cooling. Solar/internal gains and thermal storage affect the estimate; it is not a certified U-value measurement.

## 0.12.0 — advanced heating intelligence

- Add optional return-water temperature configuration alongside the existing flow/inlet sensor, with current and rolling 24-hour flow/return ΔT statistics.
- Add exploratory solar contribution analysis comparing solar radiation with weather-normalised gas use (gas per heating degree day); at least 10 valid daily observations are required. This is an association, not a measured solar heat-gain quantity.
- Validate 1, 3, 6 and 12-hour room forecasts against later measured temperatures and report mean absolute error with sample counts.
- Add monthly and seasonal energy / heating-degree-day summaries from retained daily history.
- Add robust high-consumption anomaly prompts using historical median and median absolute deviation, plus unusual water-side ΔT observations. These are diagnostic prompts, not fault declarations.
- Keep room selection and information popovers stable during Home Assistant state updates; focused controls defer full card rendering until interaction ends, and open explanations are restored after a refresh.
- Preserve existing entities, settings, dashboard views and read-only behaviour. Return-water measurement is optional; all new analyses clearly show when data is insufficient.

## Install

1. Add this repository to HACS as a custom repository, category **Integration**.
2. Install **Heating Intelligence** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and select **Heating Intelligence**.
4. Open the integration's **Configure** page to add rooms, assign floors, select global sensors, and configure pump power and energy sensors. In **Global settings**, optionally select outdoor temperature, solar radiation, ΔT, heating-only cumulative gas, boiler power/energy, and AC power/energy. Configure manifold pump power and energy separately in Pump settings. Set AC heating COP in Global settings.

## Home Assistant devices and entities

The integration creates a central **Heating Intelligence** device, with each configured room and manifold pump represented as its own child device. Room temperature, heating demand, and optional window status are grouped under that room. Pump running state, power, and cumulative energy are grouped under that pump. Analytics, counters, daily summaries, runtime totals, and pump synchronization are marked as diagnostic entities where appropriate. Existing entity unique IDs and names are retained so the device reorganization does not intentionally create replacement entities.

## Lovelace card

The integration serves its card JavaScript from:

`/heating_intelligence/heating-intelligence-card.js`

When Lovelace resources are managed through the Home Assistant UI (the default), Heating Intelligence automatically creates or updates the card resource on integration setup. The resource URL receives the installed integration version as a query parameter (for example, `?v=0.10.1`), so each integration update automatically changes the URL and bypasses stale browser/app caches. You no longer need to edit the resource URL manually.

If Lovelace resources are configured in YAML (`lovelace: resource_mode: yaml`), Home Assistant does not allow integrations to modify that list. In that case, keep the resource as a JavaScript module and update its URL query version manually when upgrading.

Add a Manual card:

```yaml
type: custom:heating-intelligence-card
```

The default overview entity is `sensor.heating_intelligence_overview`. If Home Assistant assigned a different entity ID, specify it in the card config.

## Important behaviour

For configured room demand entities, **availability is the signal**:
- Any present state (including `on`, `off`, or `unknown`) means demand is active.
- `unavailable` or a missing entity means no active demand.

Room history is stored in Home Assistant's integration storage and survives restarts. Version 0.5.0 also stores sparse room-temperature and environmental observations (up to 400 days). Temperature samples are recorded at least every 10 minutes or after a 0.15 °C change. Heating response is the average observed time to a 0.5 °C rise during demand; cooling rate uses periods with no demand and closed windows. These metrics need enough observations and may initially be unavailable.

Whole-house efficiency inputs are optional. Configure a cumulative gas meter and, optionally, separate cumulative energy counters for the downstairs manifold pump, upstairs manifold pump, and boiler pump. Use each pump's overall/lifetime energy counter, not its daily or monthly reset counters. The three electricity deltas are tracked independently, summed into combined pump electricity, and shown with each pump's percentage share. Meter deltas are accumulated from the first valid reading after setup; earlier consumption is not reconstructed. The previous single electricity meter option remains supported for backward compatibility, but should not be configured alongside the three pump counters. Gas meters in m³ are converted using the configurable factor (default 10.55 kWh/m³); kWh, Wh, and MWh units are supported. Outdoor temperature is used to calculate daily heating degree days with an 18 °C base. Daily averages are based on integration observations, not recorder backfill. Correlations are exploratory and require at least three days with valid paired data; they are not proof of causation. Solar radiation is expected in W/m². If an observation gap exceeds 90 seconds, the ongoing cycle remains open but its duration timer restarts so unobserved downtime is not counted. Daily totals use Home Assistant local dates; weekly totals start on Monday; monthly totals use calendar months. Demand intervals are split at local midnight, including daylight-saving transitions. Existing history is not backfilled: room analytics begin when version 0.4.0 first observes each configured room.

The coordinator samples every 10 seconds. HA state timestamps are used to place detected transitions accurately, but multiple state changes that occur between two samples can still be missed. Very short demand cycles may therefore be undercounted. Validate readings and statistics on your own Home Assistant installation before relying on them.

## 0.8.0 — predictive intelligence

- Forecast room temperature 1, 3 and 6 hours ahead from recent measured trends, with sample-based confidence and explicit insufficient-data states.
- Estimate time to reach the configured comfort temperature while heating, or time until temperature falls below it while cooling.
- Configure the comfort target in **Global settings** (16–25 °C; default 21 °C).
- Project end-of-day gas and heating-electricity use from the elapsed-day meter run rate. This is a simple baseline, not a weather-aware model.
- Expose forecasts as attributes of the existing overview sensor; no new forecast entities are created.
- The card groups secondary analysis into collapsible sections and keeps its expanded sections open during live updates.
- Room detail includes a 24-hour temperature chart with a distinct forecast line and comfort-target reference.
- System analysis includes a 14-day gas/electricity consumption chart.
- Forecasts are read-only estimates and never control heating equipment.


## 0.10.1 — adaptive forecasts and demand timeline

- Embedded Overview, Heat demand and Predictions tabs for a dedicated dashboard card.
- Room-by-room 24-hour and 7-day demand timeline from persisted cycle timestamps, including ongoing requests.
- Adaptive room forecasts learn separate heating and coasting rates from recent same-mode, closed-window observations.
- Add 1, 3, 6 and 12-hour forecasts with uncertainty ranges, model sample counts, confidence and comfort-crossing estimates.
- Energy projections remain labelled as elapsed-day run-rate estimates.
- Read-only operation and existing entities/configuration are preserved.


## 0.10.1 — card tab navigation fix

- Fix the missing embedded Overview, Heat demand and Predictions tab bar in the Lovelace card.
- Keep the tab selection stable across Home Assistant state updates.
- Separate the demand timeline and prediction/diagnostic sections from the overview.


## 0.11.1 — explainability and historical analytics

- Add reusable, touch-friendly information controls beside non-obvious card metrics, explaining definitions, calculation, interpretation, data requirements and limitations.
- Clarify that gas per heating degree day is a daily weather-demand indicator, whole-house efficiency is a collection of comparative indicators, and room efficiency scores are heuristic rather than measured physical efficiencies.
- Add a weekly room-demand heatmap using recorded demand-cycle timestamps, showing minutes of demand by room, day and hour.
- Add latest-seven-days versus preceding-seven-days comparisons for gas and pump electricity, plus weather-normalised gas when the model is available.
- Preserve existing tabs, metrics, entities, configuration and read-only behaviour.


### v0.15 chronological validation
Recorder calibration now includes a chronological train/holdout check for each room's free-cooling response. The first 70% of eligible intervals fit a first-order response; the later 30% are scored against a persistence baseline using MAE and RMSE. Outdoor observations older than 90 minutes are excluded. A passing holdout indicates improved short-horizon prediction only; it does not establish uniquely physical room capacity or envelope conductance. The default Recorder inventory window is now 90 days (configurable from 7 to 365 days). The thermal-model dataset remains 30-minute aligned; control-state transitions retain their native Recorder timestamps so PWM pulse timing is not lost to downsampling.


The Recorder summary also reports an aggregate effective whole-building heat capacity when a 7-day energy-derived house heat-loss coefficient and clean low-solar free-cooling history are both available. It calculates the aggregate estimate as H_house × tau_house. This value is not assigned to individual rooms or floor slabs and remains conditional on the configured boiler-efficiency / AC-COP assumptions and unmeasured-gain limitations. The multi-zone inventory also calculates each configured room's ventilation conductance from volume and ACH even when outdoor temperature is temporarily unavailable. It reports a residual whole-house envelope-loss estimate only when every configured room has positive volume and ACH, and only when the independently estimated 7-day whole-house heat-loss coefficient exceeds the summed ventilation conductance. The residual assumes that the aggregate coefficient already includes ventilation and uses the same indoor/outdoor boundary; it is withheld for incomplete coverage or inconsistent values. It is not distributed among rooms because room-level conductances remain unidentified. The normalized multi-zone response fit now screens both pairwise predictor correlations and the rank/condition number of the complete standardized predictor matrix; joint dependencies among three or more drivers also cause coefficients to be withheld. A full-rank matrix is necessary but does not by itself prove physical identifiability. The multi-zone inventory also reconciles the Recorder-derived effective whole-building capacity against the air-only capacity calculated from configured room volumes. With complete volume coverage it reports an aggregate implied non-air storage remainder, or flags an inconsistency if the estimated whole-building capacity falls below the air-only minimum. This remainder is not assigned to rooms, walls or slabs; it is an aggregate consistency diagnostic only.


The UFH observability screen now uses aligned Recorder history for each manifold to compare adjacent-room temperature rates during pump-on and pump-off intervals, and reports supply/return delta-T when both readings are fresh. This is only an excitation diagnostic: pump electrical power is not delivered heat, water delta-T without flow is not thermal output, and no slab capacity is inferred. The floor nodes expose this evidence while keeping slab temperature, capacity and heat output withheld.


Recorder calibration now preserves each energy meter's `unit_of_measurement` and derives interval energy only from fresh, monotonic cumulative meters with explicit energy units. Gas volume units (for example m³) are not converted without a configured calorific value and meter basis. For verified gas kWh and AC electricity kWh it reports delivered-heat sensitivity scenarios (boiler efficiency 0.80/0.90/0.98 and AC COP 2/3/5); these are assumptions, not measured efficiencies. The results remain whole-system input proxies and are not allocated to individual rooms or UFH slabs.


A further Recorder diagnostic pairs verified cumulative gas and AC energy increments with concurrent room-temperature rates on the same 30-minute intervals. It reports whole-house input-power versus average room-response association and separate per-room statistical associations for each meter. These are observational correlations only: they do not allocate gas or AC heat to rooms, identify causal response, or estimate room/slab capacities. Meter unit, freshness, monotonicity and interval checks are applied before a pair is accepted.


The input-to-room response diagnostic now scans concurrent and delayed room-temperature responses (up to two hours at the default 30-minute cadence). Each response interval is admitted only when every configured room has a valid temperature, preventing changing room coverage from shifting the aggregate response metric. Per-room lag scans are reported separately for gas and AC. The strongest observed lag is descriptive only and must not be interpreted as a physical delay or causal effect.


The Recorder calibration diagnostics now include a room-level excitation screen. It counts demand-on/off intervals and transitions per room, compares temperature-rate distributions between those modes, measures pairwise demand co-activation, counts distinct simultaneous room-demand patterns, and checks overlap between floor-pump activity and room demand. This is a prerequisite/observability screen only: demand is not delivered heat, pump electricity is not thermal output, and the screen does not identify room-level watts, capacities or conductances.


The passive multi-zone fit now reports per-driver temperature-gradient excitation on its training segment: standard deviation, observed range, positive/negative/near-zero interval counts, and whether each room-to-room gradient reverses direction with adequate sample counts. Room links with weak or one-sided gradients are explicitly listed. This is an excitation-quality diagnostic, not a new physical parameter estimate; the existing full-matrix rank/condition checks still govern coefficient withholding, and holdout data are not used to assess training excitation.


The normalized free-coasting multi-zone regression now includes deterministic moving-block bootstrap coefficient-stability diagnostics. It resamples contiguous four-interval blocks from the chronological training segment (80 deterministic replicates when at least 40 training intervals are available) and reports the 5th/50th/95th percentiles plus relative range for each normalized coefficient. These are resampling sensitivity ranges, not formal confidence intervals; they exclude sensor bias, unmeasured gains, model misspecification and uncertainty in delivered room heat. The chronological holdout remains untouched.


The multi-zone response fit also compares normalized coefficients across three contiguous chronological sections of the training segment. Each section must have sufficient observations, nontrivial predictor excitation, a full-rank standardized predictor matrix and an acceptable condition number before its coefficients are included. Per-predictor minimum, median, maximum, median absolute deviation and relative range are reported. The final chronological holdout is excluded. This is a descriptive temporal-stability diagnostic, not a confidence interval or proof of physical parameter constancy.


The aggregate house effective-capacity estimate now reports a deterministic moving-block resampling sensitivity range when at least 40 cooling-response intervals are available. It resamples contiguous four-interval blocks and reports 5th/50th/95th percentiles of the implied aggregate capacity. With shorter histories, the range is withheld. These are sensitivity diagnostics, not formal confidence intervals, and do not include uncertainty in the heat-loss coefficient or unmeasured gains.


The whole-house measured-performance estimate now exposes explicit assumption sensitivity for the 7-day and 30-day heat-loss coefficients. It recalculates nine combinations of boiler efficiency (0.80, 0.90, 0.98) and AC heating COP (2, 3, 5), reporting the resulting H and U-value ranges. A window is withheld unless it meets the existing completed-day and degree-hour coverage thresholds. These ranges are conditional scenarios, not statistical confidence intervals or measured plant efficiencies.


Gas-meter conversion is now tracked separately from gas energy: daily records retain the raw m³ delta and whether the meter basis was volumetric or already energy-based. The whole-house heat-loss sensitivity uses three conversion factors (95%, 100%, 105% of the configured kWh/m³ factor) only when all days in the selected window have a consistently identified m³ basis. Combined with the three boiler-efficiency and three AC-COP assumptions, this yields 27 scenarios. Legacy days without conversion-basis metadata, mixed meter units and unsupported units are withheld rather than guessed.


The weather-normalised gas regression now uses a chronological 70/30 training/holdout split. Coefficients and median reference weather are derived from training days only; the later holdout is scored against a training-mean baseline with MAE, RMSE and R². Multi-zone room holdout metrics are also aggregated with interval-count weighting at house level. The aggregate building-capacity sensitivity combines the cooling time-constant resampling distribution with the whole-house heat-loss assumption range; it remains a sensitivity envelope, not a formal confidence interval or room-level allocation. The RC solver now audits the energy balance between stored-energy change, internal gains and fixed-boundary exchange, and rejects integration requests requiring more than 100,000 substeps.


The multi-zone observation model now returns an explicit simulation-readiness matrix for every room and every room pair. It distinguishes a known air-only capacity from the still-missing effective room capacity, lists unidentified inter-zone links and keeps room-envelope conductances withheld. This is intended to make the remaining calibration blockers actionable without fabricating physical values.

The plant-input/room-response alignment now adds Fisher 95% intervals for Pearson associations, chronological split-half repeatability checks, an input-variation gate, and a check for ambiguous neighbouring lag peaks. A correlation is labelled only as a repeatable descriptive association when input variation is adequate, the direction persists across both history halves, and the lag peak is distinguishable. The Fisher intervals are nominal and assume independent pairs, so serial correlation may make them optimistic; intervals spanning zero are withheld from repeatable-association status. These diagnostics still do not establish causality, delivered room heat, or physical thermal parameters.

The plant-input/room-response diagnostic now also captures outdoor temperature, solar radiation, the fraction of rooms requesting heat and the fraction with open windows. For each lag it reports a partial correlation after linear adjustment for covariates with sufficient coverage. The adjustment design is rank-checked and withheld when collinear or ill-conditioned; it requires enough complete cases and reports a nominal Fisher interval using partial-correlation degrees of freedom (number of pairs minus adjusted covariates minus three). A deterministic moving-block resampling range is added when at least 40 adjusted pairs are available. This is a sensitivity diagnostic, not a formal confidence interval, and it cannot remove unmeasured confounding or prove causal heat delivery. Raw correlations remain visible alongside the adjusted result.



Multi-zone chronological holdout scoring now includes per-room residual diagnostics: mean bias, residual dispersion, maximum absolute error, lag-one residual correlation, residual drift per day, and a timestamp/cadence audit of the held-out segment. When at least 20 holdout intervals are available, deterministic moving-block resampling reports 5th/50th/95th percentile MAE and RMSE sensitivity ranges. These are descriptive diagnostics and resampling sensitivity, not formal confidence intervals. The house-level validation summary also counts rooms that beat or fail to beat persistence and rooms with residual diagnostics. Predictive holdout performance does not identify absolute thermal parameters: the per-room result explicitly continues to withhold physical parameter identification until independent room capacity and room-level heat input are available.


### Explicit absolute-parameter readiness report
- Recorder diagnostics now produce a per-room physical-calibration readiness report alongside normalized multi-zone response identification.
- The report combines room temperature/state coverage, configured volume/ACH, normalized response fit status, chronological holdout error, coefficient resampling stability and temporal stability.
- It lists verified system-level energy meters separately from water-flow and room/zone heat-output channels. It does not treat pump electrical power or supply-return delta-T without flow as delivered heat.
- Absolute room capacities, W/K conductances and physical simulation readiness remain explicitly false until independent room capacity, measured thermal output and a defensible room-level heat allocation exist.
- The report includes the concrete additional evidence required to proceed. It is a readiness/diagnostic layer, not a new parameter estimator and does not manufacture missing measurements.


### Hydronic flow and measured water-side heat output
- Global settings now accept an optional water-flow-rate sensor. The Recorder calibration dataset retains its native unit and freshness metadata.
- When fresh positive flow and fresh supply/return temperatures are available, the calibration summary calculates water-side thermal transfer using `Q̇ = ṁ × cp × (T_supply − T_return)`.
- Supported flow units are L/min, L/s, m³/h, kg/h and kg/s. The default water properties are 997 kg/m³ and 4186 J/(kg·K); these assumptions are exposed in the result.
- The summary reports valid-sample coverage and median / 10th / 90th percentile output. It also integrates valid adjacent samples with a trapezoidal rule into a water-side kWh estimate; gaps outside the configured cadence are not bridged. Unsupported units, stale inputs, zero/negative flow and non-positive supply-return delta-T are excluded explicitly.
- This is system-side hydronic output only. It is not automatically equal to heat delivered to rooms, and it is not allocated among rooms or used to claim calibrated room capacities. Hydraulic bypasses, buffer tanks and distribution losses still matter.

### UFH response observability safeguards
The UFH pump response diagnostic uses pump electrical power only to mark circulation state. A floor interval is accepted only when every configured room on that floor has a fresh temperature; an incomplete room set resets the adjacent-interval comparison rather than averaging whichever sensors happen to be available. Explicitly open-window intervals are excluded, and missing or irregular 30-minute cadence is not bridged. The diagnostic reports per-room and complete-floor temperature-rate summaries for pump-on and pump-off periods. These remain observed temperature responses: they are not thermal output, slab temperature, room heat delivery or slab capacity. A floor with no configured rooms is reported separately rather than treated as an empty response dataset.
### Room-level solar response diagnostics
- Add a per-room 0–120 minute solar-irradiance/temperature-slope lag scan using fresh Recorder values and exact 30-minute cadence.
- Select the lag on the chronological training segment and check the same adjusted association on the later holdout; adjust for sufficiently covered outdoor temperature and room demand.
- Exclude explicitly open-window intervals, stale temperatures/irradiance and cadence gaps; reject unsupported irradiance units.
- Surface configured per-room window area/orientation as context only. No solar gain watts, glazing transmittance, or absolute room RC parameters are inferred.
- Include the evidence in the physical-readiness audit without changing the rule that physical simulation remains withheld until independent capacities, delivered-heat allocation and output validation exist.
### Passive multi-zone fit data-quality gates
- The normalized free-coasting RC fit now rejects stale room temperatures, demand/window states, solar irradiance, outdoor temperature and configured AC/boiler telemetry using the same 45-minute Recorder freshness limit.
- Solar irradiance is checked in W/m² (or converted from kW/m²) before applying the low-solar threshold; explicitly unsupported units are excluded rather than compared numerically.
- Exclusion counts distinguish stale measurements from missing states, active heating and high solar conditions.
- These checks improve the validity of normalized G/C evidence; they do not convert the fit into absolute conductances or thermal capacities.
### Multi-zone fit conditioning and bootstrap safeguards
- Report the conventional 2-norm condition number of the standardized training design matrix (square root of the Gram-matrix eigenvalue ratio), alongside pairwise correlations and matrix rank.
- Apply the same full-rank and condition-number gate to every deterministic moving-block bootstrap resample; non-identifiable resamples are rejected and counted instead of contributing arbitrary coefficient allocations.
- The bootstrap range remains a resampling sensitivity diagnostic, not a formal confidence interval.
Solar-response evidence is only labelled repeatable when the training and holdout partial-correlation nominal 95% Fisher intervals both exclude zero and the association direction is consistent. These intervals assume independent pairs; serial correlation can make them optimistic, so they remain descriptive screening evidence rather than formal inference.
- When a room has a configured window sensor, unknown or stale window state is excluded from solar-response analysis rather than being treated as closed. Rooms without a configured window sensor remain explicitly distinguishable from rooms whose configured sensor is unavailable.
- Where fresh room sensors exist, solar-response adjustment also includes room CO₂ and relative humidity as internal-activity/moisture proxies. These are nuisance covariates only: neither CO₂ nor humidity is converted into occupant heat or moisture watts. The minimum pair count is raised to preserve adequate training and holdout sample counts for the expanded adjustment design.
### Independent room-output readiness gate
A configured room/zone heat-output channel is counted as independent measured thermal output only when it has a non-negative numeric reading, a fresh value (within 45 minutes), and an explicit W or kW unit. Stale, untyped and energy-unit values do not clear the physical-calibration blocker.
Water-flow evidence in the physical-readiness audit is counted only for positive fresh readings with a supported volumetric or mass-flow unit. This is separate from the water-side output calculation, which additionally requires fresh supply/return temperatures and a positive delta-T.
Room coverage in the physical-readiness audit uses the same 45-minute freshness rule for temperature, demand and window state. Stale readings no longer inflate the apparent amount of calibration-ready history.
Historical AC power is normalized from W, kW, MW and mW before the passive-fit AC-heating exclusion threshold is applied. Unsupported explicit units are excluded rather than compared as if they were watts; missing legacy units retain the native-watt assumption.

### Native control-event and PWM timing inventory
- The Recorder calibration report now inventories configured room-demand switches and the configured boiler-burner entity using their original `last_changed` timestamps.
- Explicit `on`→`off` and `off`→`on` transitions produce measured dwell intervals; complete on/off cycles are counted only within uninterrupted known-state sequences.
- `unknown` and `unavailable` states break continuity. The inventory never converts them into `off`, bridges across them, or invents pulse durations.
- Reports per-entity sample counts, explicit transitions, complete on/off intervals and cycle counts, dwell-time median/minimum/maximum, and interrupted active intervals. Raw history is not retained in the summary.
- This is controller-event timing evidence, not proof of PWM duty-cycle accuracy or delivered heat. A switch stream that alternates `on`/`unavailable` cannot establish pulse durations until explicit `off` transitions are recorded.
- High-resolution event timestamps remain separate from the 30-minute thermal identification dataset; no synthetic 10-second rows are generated.


## v0.15.12 — Generic UFH circuit configuration

- Configure each of the 16 circuits independently with a user-defined name, pipe length and floor.
- Bind the circuit to the manifold pump for its selected floor, preventing accidental cross-floor pump assignment.
- Select up to four room heating-demand sources per circuit; any selected demand being ON activates the demand condition (OR).
- Retain a floor-wide demand option for sensorless circuits such as bathrooms; any configured room on that floor can request the circuit.
- Keep the persistent per-circuit flowmeter number entity as the adjustable flow input, and label it with the configured circuit name while preserving its unique ID and restored value.
- Keep the 180-second pump confirmation and assumed 9°C water temperature drop unchanged.

## v0.15.10 — Floor-wide demand for sensorless UFH circuits

- Allow a UFH circuit to be configured without a dedicated room or temperature sensor.
- Add a floor-wide demand mode: a circuit is considered requested when any configured room on its selected floor requests heat.
- Keep floor-wide demand isolated to the circuit's selected manifold/floor; demand on the other floor does not activate it.
- Preserve the independent pump-start timer: it begins when associated pump power rises above 7 W and must remain above that threshold for 180 seconds. A delayed Wi-Fi demand relay can arrive during that interval; if it is already on when the timer expires, the circuit activates immediately.
- Retain selected-room OR/AND demand mode for existing circuits and preserve the assumed 9°C water temperature drop for estimated thermal output.

## v0.15.8 — UFH hydraulic flow inputs

- Add 16 persistent, manually adjustable UFH circuit flow inputs (0.00–6.00 L/min, 0.01 L/min steps).
- Add per-circuit configuration for room association, pipe length, manifold pump, and one or more room-demand sources with OR/AND logic.
- Combine configured room demand with the associated manifold pump's >5 W circulation state before counting a circuit's configured flow as active.
- Keep manually entered flowmeter values distinct from automatically measured flow. The resulting active-flow totals are conditional estimates, not independent per-circuit measurements.
- Expose the hydraulic snapshot in the Heating Intelligence overview attributes.
\n\n## v0.15.9 — Timed UFH flow and estimated thermal output\n\n- Start each configured circuit's flow only after its associated pump power remains above 7 W while its configured demand is on for 180 seconds.\n- Stop circuit flow immediately when configured demand turns off or pump power falls to 7 W or below.\n- Estimate circuit thermal output using the manually entered flow rate and a fixed assumed 9°C water temperature drop (approximately 628 W per L/min).\n- Expose per-circuit and total estimated thermal power. These are model estimates, not measured heat output.\n