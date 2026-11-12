# Heating Intelligence v0.9.1 — Floor thermal performance and weather-normalised efficiency

## Features
- Downstairs/upstairs thermal comparisons with area-weighted room temperature, observed cooling rate, heating response and daily demand hours.
- Visible room-area coverage so incomplete configuration is explicit.
- Weather-normalised daily gas comparison using heating degree days, indoor–outdoor ΔT and solar radiation.
- Actual gas use, weather-model expected gas use and a comparison index referenced to median observed weather.

## Room area input fixes
- Fix the missing numeric input in the **Set room floor area** options form when a room has no previously configured area.
- Room areas accept 0.01 m² increments, including values such as 10.92 m².
- The same precision is available when adding a new room.
- Existing saved areas are retained when editing other room settings.

## Data requirements and interpretation
- Room areas remain optional.
- The weather model requires at least 21 completed days with gas, outdoor temperature, ΔT and solar readings.
- Weather-normalised gas is a comparative regression index, not a physical heat-loss coefficient or boiler combustion-efficiency measurement.
- All analytics remain read-only and do not control heating equipment.
