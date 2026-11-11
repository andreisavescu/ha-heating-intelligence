# HA Heating Intelligence

A read-only Home Assistant custom integration for analysing heating behaviour. It does not control thermostats, pumps, boilers, or HVAC equipment.

## Current scope

- Add rooms through the Home Assistant UI.
- For each room, select a heating-demand relay and room-temperature sensor.
- Optionally select a window contact.
- Configure the underfloor-heating input/supply water temperature sensor globally.
- Expose room-level demand, temperature, window, water-inlet temperature, and overview entities.
- Includes a Lovelace card with a whole-house overview and a room selector for detailed room status.

## Install (development)

1. Add this repository to HACS as a custom repository, category **Integration**.
2. Install **Heating Intelligence**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and select **Heating Intelligence**.
5. Open the integration's **Configure** page:
   - **Global settings**: select the underfloor-heating input water temperature entity.
   - **Add a room**: enter a room name, select its thermostat demand relay and room temperature sensor, and optionally a window contact.

## Lovelace card

The integration serves the card JavaScript at:

`/heating_intelligence/heating-intelligence-card.js`

After installing the integration and restarting Home Assistant:

1. Go to **Settings → Dashboards**.
2. Open the three-dot menu → **Resources**.
3. Add resource URL `/heating_intelligence/heating-intelligence-card.js`.
4. Select **JavaScript module**.
5. Add a Manual card to a dashboard with:

```yaml
type: custom:heating-intelligence-card
```

The card shows the number of rooms currently requesting heat, average configured room temperature, heating-water inlet temperature, and a dropdown to inspect each room's temperature, demand state, and optional window state.

The default overview entity is `sensor.heating_intelligence_overview`. If Home Assistant assigned a different entity ID, specify it in the card config:

```yaml
type: custom:heating-intelligence-card
entity: sensor.your_heating_intelligence_overview
title: Heating overview
```

## Important behaviour

The thermostat relay is interpreted as follows:
- `on`: heating demand is active.
- `off`: no active demand.
- `unavailable` or `unknown`: no active demand is inferred, but the room demand entity reports its source state and becomes unavailable if the source itself is missing.

This is an early development release. The card and room configuration are an initial foundation. Daily heating-time accounting, cycles, manifold pump energy, gas consumption, solar gains, delta-T analysis, and AC heating contribution are planned next. Validate on a test Home Assistant instance before relying on statistics.
