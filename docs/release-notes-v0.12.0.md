# Heating Intelligence v0.12.0 — Advanced Heating Intelligence

## Heating-water analysis
- Add an optional return-water temperature sensor in Global settings.
- Use the existing heating-water inlet sensor as flow/supply temperature.
- Calculate current flow-to-return ΔT and rolling 24-hour average, minimum and maximum from five-minute samples.
- Show clear collecting-data states when the return sensor is not configured or history is insufficient.

## Solar contribution analysis
- Compare daily solar radiation with gas consumption normalised by heating degree days.
- Show high-solar versus low-solar gas-per-HDD averages and their exploratory difference.
- Require at least 10 valid days; clearly disclose confounders and avoid presenting this as measured solar heat gain.

## Predictive-model validation
- Persist hourly forecast snapshots for each room.
- Compare 1, 3, 6 and 12-hour forecasts with later measured room temperatures.
- Report sample count and mean absolute error (MAE) for each horizon.

## Advanced anomaly detection
- Add robust high-consumption outlier detection using historical median and median absolute deviation, requiring at least 10 baseline days.
- Flag unusually low or high flow/return ΔT as observations, not fault diagnoses.

## Seasonal and historical analysis
- Add monthly gas, pump electricity and heating-degree-day summaries.
- Aggregate retained monthly data into seasonal summaries.

## Lovelace card interaction fix
- Prevent full card re-render while the room selector or an information button has focus.
- Apply deferred updates after interaction ends.
- Preserve the selected room, active tab, open sections and open information explanation across refreshes.

## Compatibility
- Preserve existing entities, configuration, tabs and read-only behaviour.
- Return-water measurement is optional. No heating controls or automatic actions are introduced.
