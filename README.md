# HA Heating Intelligence

<p align="center">
  <img src="brand/icon.png" alt="HA Heating Intelligence logo" width="180">
</p>

A read-only Home Assistant custom integration for analysing underfloor-heating behaviour. It does not control thermostats, pumps, boilers, or HVAC equipment.

## Current scope (0.5.0)

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
- Group entities into one central house device, one device per configured room, and one device per manifold pump. Secondary analytics and counters are marked as diagnostic entities.
- Provide a Lovelace card with whole-house overview, pump readings, room details, and floor-level demand/pump correlation.
- Analyse room thermal behaviour: temperature changes over 1, 6 and 24 hours, average heating response time to a 0.5 °C rise, cooling rate while there is no demand and windows are closed, and estimated hours per 1 °C of heat retention.
- Measure how long windows remain open while a room is requesting heat.
- Optionally collect outdoor temperature, solar radiation, indoor–outdoor ΔT, cumulative gas consumption, and cumulative electricity consumption.
- Calculate daily heating degree days (18 °C base), daily gas/electricity energy, and gas kWh per degree day; calculate exploratory correlations between daily gas use, degree days, ΔT, and solar radiation.
- Retain sparse raw room-temperature and environmental observations for up to 400 days so derived thermal and efficiency metrics can be recalculated.

## Install

1. Add this repository to HACS as a custom repository, category **Integration**.
2. Install **Heating Intelligence** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and select **Heating Intelligence**.
4. Open the integration's **Configure** page to add rooms, assign floors, select global sensors, and configure pump power and energy sensors. In **Global settings**, optionally select outdoor temperature, solar radiation, ΔT, cumulative gas, and cumulative electricity sensors.

## Home Assistant devices and entities

The integration creates a central **Heating Intelligence** device, with each configured room and manifold pump represented as its own child device. Room temperature, heating demand, and optional window status are grouped under that room. Pump running state, power, and cumulative energy are grouped under that pump. Analytics, counters, daily summaries, runtime totals, and pump synchronization are marked as diagnostic entities where appropriate. Existing entity unique IDs and names are retained so the device reorganization does not intentionally create replacement entities.

## Lovelace card

The integration serves the card JavaScript at:

`/heating_intelligence/heating-intelligence-card.js`

If it is not automatically registered, add it under **Settings → Dashboards → Resources** as a **JavaScript module**, then add a Manual card:

```yaml
type: custom:heating-intelligence-card
```

The default overview entity is `sensor.heating_intelligence_overview`. If Home Assistant assigned a different entity ID, specify it in the card config.

## Important behaviour

For configured room demand entities, **availability is the signal**:
- Any present state (including `on`, `off`, or `unknown`) means demand is active.
- `unavailable` or a missing entity means no active demand.

Room history is stored in Home Assistant's integration storage and survives restarts. Version 0.5.0 also stores sparse room-temperature and environmental observations (up to 400 days). Temperature samples are recorded at least every 10 minutes or after a 0.15 °C change. Heating response is the average observed time to a 0.5 °C rise during demand; cooling rate uses periods with no demand and closed windows. These metrics need enough observations and may initially be unavailable.

Whole-house efficiency inputs are optional. Configure cumulative gas and electricity meters (not daily-reset sensors). Meter deltas are accumulated from the first valid reading after setup; earlier consumption is not reconstructed. Gas meters in m³ are converted using the configurable factor (default 10.55 kWh/m³); kWh, Wh, and MWh units are supported. Outdoor temperature is used to calculate daily heating degree days with an 18 °C base. Daily averages are based on integration observations, not recorder backfill. Correlations are exploratory and require at least three days with valid paired data; they are not proof of causation. Solar radiation is expected in W/m². If an observation gap exceeds 90 seconds, the ongoing cycle remains open but its duration timer restarts so unobserved downtime is not counted. Daily totals use Home Assistant local dates; weekly totals start on Monday; monthly totals use calendar months. Demand intervals are split at local midnight, including daylight-saving transitions. Existing history is not backfilled: room analytics begin when version 0.4.0 first observes each configured room.

The coordinator samples every 30 seconds. HA state timestamps are used to place detected transitions accurately, but multiple state changes that occur between two samples can still be missed. Very short demand cycles may therefore be undercounted. Validate readings and statistics on your own Home Assistant installation before relying on them.
