# HA Heating Intelligence

A Home Assistant custom integration for analysing heating behaviour. It does not control thermostats, pumps, boilers, or HVAC equipment.

## Current scope

- Add rooms through the Home Assistant UI.
- For each room, select a heating-demand relay and room-temperature sensor.
- Optionally select a window contact.
- Expose room-level demand, temperature, and window entities.
- Treat a relay state of `on` as a heat request. Treat `off`, `unavailable`, and `unknown` as no active request, matching installations where the relay becomes unavailable when demand ends.
- Read-only: no service calls or device control.

## Install (development)

1. Add this repository to HACS as a custom repository, category **Integration**.
2. Install **Heating Intelligence**.
3. Restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and select **Heating Intelligence**.
5. Open the integration's **Configure** page to add rooms.

## Room setup

Each room needs:
- A display name
- A thermostat demand entity (binary sensor or relay entity)
- A room temperature entity
- Optionally, a window contact binary sensor

## Notes

This is an early development release. The initial version provides room configuration and read-only room entities. Daily heating-time accounting, manifold pump energy, gas consumption, solar gains, delta-T analysis, and AC heating contribution are planned next. Validate it on a test Home Assistant instance before relying on its statistics.

Issues and contributions are welcome.
