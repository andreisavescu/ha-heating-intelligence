# HA Heating Intelligence

<p align="center">
  <img src="brand/icon.png" alt="HA Heating Intelligence logo" width="180">
</p>

A read-only Home Assistant custom integration for analysing underfloor-heating behaviour. It does not control thermostats, pumps, boilers, or HVAC equipment.

## Current scope (0.11.0)

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

## Install

1. Add this repository to HACS as a custom repository, category **Integration**.
2. Install **Heating Intelligence** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and select **Heating Intelligence**.
4. Open the integration's **Configure** page to add rooms, assign floors, select global sensors, and configure pump power and energy sensors. In **Global settings**, optionally select outdoor temperature, solar radiation, ΔT, cumulative gas, and the three cumulative pump electricity counters.

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

The coordinator samples every 30 seconds. HA state timestamps are used to place detected transitions accurately, but multiple state changes that occur between two samples can still be missed. Very short demand cycles may therefore be undercounted. Validate readings and statistics on your own Home Assistant installation before relying on them.

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


## 0.11.0 — explainability and historical analytics

- Add reusable, touch-friendly information controls beside non-obvious card metrics, explaining definitions, calculation, interpretation, data requirements and limitations.
- Clarify that gas per heating degree day is a daily weather-demand indicator, whole-house efficiency is a collection of comparative indicators, and room efficiency scores are heuristic rather than measured physical efficiencies.
- Add a weekly room-demand heatmap using recorded demand-cycle timestamps, showing minutes of demand by room, day and hour.
- Add latest-seven-days versus preceding-seven-days comparisons for gas and pump electricity, plus weather-normalised gas when the model is available.
- Preserve existing tabs, metrics, entities, configuration and read-only behaviour.
