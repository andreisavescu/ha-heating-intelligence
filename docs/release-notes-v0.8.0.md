# Heating Intelligence v0.8.0 — Predictive Intelligence

> Draft release notes. This release is not ready to publish until the implementation, tests, and Home Assistant compatibility checks are complete.

## Highlights

### Predictive room temperature
- Estimate room temperature 1, 3, and 6 hours ahead from recent measured temperature trends.
- Estimate time to reach the configured comfort threshold while heating.
- Estimate time until a room falls below the comfort threshold while cooling.
- Display forecast confidence and explain when there is insufficient recent data.

### Whole-house energy projection
- Project end-of-day gas and electricity consumption from today's observed meter use.
- Clearly identify run-rate projections and their limitations.

### More compact dashboard
- Reduce excessive spacing and repeated visual elements.
- Keep the important live system status visible at a glance.
- Group room-level detail and advanced analysis so the card is easier to scan on mobile.
- Preserve access to detailed diagnostics without making the default view unnecessarily long.

## Method and limitations
- Forecasts are explainable, read-only estimates based on measured data; they do not control thermostats, pumps, or the boiler.
- Room forecasts require a minimum number of recent samples spanning a minimum time window. Open-window observations are excluded from the temperature trend.
- Short-term room forecasts assume recent conditions continue. They are not weather forecasts and may be inaccurate after changes in outdoor temperature, solar gains, occupancy, ventilation, or heating operation.
- Initial daily energy projections are simple elapsed-day run-rate estimates. They do not yet model weather, solar gains, occupancy, or planned heating schedules.
- Confidence is an indicator of data coverage and trend consistency, not a calibrated probability of accuracy.
- The comfort target is configurable in Global settings from 16–25 °C; the default is 21 °C.
- No physical room heat-loss coefficient (W/K) or boiler combustion efficiency is claimed without measured useful heat output.

## Compatibility and migration
- Existing room and sensor configuration should remain compatible; prediction data is additive.
- No new Home Assistant entities are intended for this release. Forecasts are exposed through the existing overview sensor attributes and card.
- Existing history is reused where available. Forecasts will remain unavailable until enough fresh temperature samples have accumulated.

## Verification before publication
- [ ] Full test suite passes on the final release commit.
- [x] Prediction results exposed through the existing overview sensor attributes.
- [x] Comfort target configurable in Global settings (16–25 °C, default 21 °C).
- [ ] Home Assistant config flow and coordinator startup verified.
- [ ] Forecast behaviour checked with realistic rising, falling, flat, stale, and window-open sample series.
- [ ] Card checked on desktop and mobile.
- [ ] Resource cache/versioning verified so users receive the updated card.
- [ ] README and manifest version updated to 0.8.0.
