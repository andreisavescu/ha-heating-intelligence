# Heating Intelligence v0.10.0

## Highlights
- Embedded Overview, Heat demand and Predictions tabs make the card suitable for a dedicated dashboard.
- Room-by-room demand timeline with 24-hour and 7-day views, displaying completed cycles and active requests.
- Adaptive room forecasts learn separate heating and coasting response rates from recent same-mode, closed-window observations.
- 1, 3, 6 and 12-hour room temperature estimates with uncertainty ranges, sample counts, confidence and comfort-crossing estimates.
- Energy projections remain explicitly identified as elapsed-day run-rate estimates.

## Notes
- Demand semantics are unchanged: any available source state represents demand; unavailable or missing means no demand.
- Short transitions between 30-second coordinator polls may still be missed.
- The adaptive model requires sufficient recent observations in the current mode. Forecasts are estimates and never control heating equipment.
- Existing configuration and entities are preserved. The integration remains read-only.
