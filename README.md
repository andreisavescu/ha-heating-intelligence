# HA Heating Intelligence

<p align="center">
  <img src="brand/icon.png" alt="HA Heating Intelligence logo" width="180">
</p>

A read-only Home Assistant custom integration for analysing underfloor-heating behaviour. It does not control thermostats, pumps, boilers, or HVAC equipment.

## Current scope (0.4.0)

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
- Provide a Lovelace card with whole-house overview, pump readings, room details, and floor-level demand/pump correlation.

## Install

1. Add this repository to HACS as a custom repository, category **Integration**.
2. Install **Heating Intelligence** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and select **Heating Intelligence**.
4. Open the integration's **Configure** page to add rooms, assign floors, select global sensors, and configure pump power and energy sensors.

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

Room history is stored in Home Assistant's integration storage and survives restarts. If an observation gap exceeds 90 seconds, the ongoing cycle remains open but its duration timer restarts so unobserved downtime is not counted. Daily totals use Home Assistant local dates; weekly totals start on Monday; monthly totals use calendar months. Demand intervals are split at local midnight, including daylight-saving transitions. Existing history is not backfilled: room analytics begin when version 0.4.0 first observes each configured room.

The coordinator samples every 30 seconds. HA state timestamps are used to place detected transitions accurately, but multiple state changes that occur between two samples can still be missed. Very short demand cycles may therefore be undercounted. Validate readings and statistics on your own Home Assistant installation before relying on them.
