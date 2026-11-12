# HA Heating Intelligence

<p align="center">
  <img src="brand/icon.png" alt="HA Heating Intelligence logo" width="180">
</p>

A read-only Home Assistant custom integration for analysing underfloor-heating behaviour. It does not control thermostats, pumps, boilers, or HVAC equipment.

## Current scope (0.3.1)

- Add rooms through the Home Assistant UI, assigning each room to the downstairs or upstairs floor.
- Change an existing room's floor assignment; the currently saved floor is preselected.
- Correlate room heating demand with the corresponding manifold pump.
- Optionally monitor room window contacts.
- Configure the underfloor-heating supply-water temperature sensor.
- Monitor ground-floor and upstairs manifold pump power (W), cumulative energy (kWh), and running state.
- Track daily pump energy, runtime, cycle counts, and room-demand hours.
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

For thermostat relays:
- `on`: heating demand is active.
- `off` or `unavailable`: no active demand is inferred.
- `unknown` or a missing source: the demand entity reports the source state and may become unavailable.

This is an early development integration. Validate readings and statistics on your own Home Assistant installation before relying on them.
