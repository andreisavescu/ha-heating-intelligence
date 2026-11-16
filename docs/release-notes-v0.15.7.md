# Heating Intelligence 0.15.7 — Faster startup

## Fixed

- Live monitoring no longer waits for the full Recorder history query and historical thermal calibration before the first coordinator update.
- Historical Recorder analysis now runs in the background after live room, pump, and sensor data are available.
- When the background analysis completes, its validation and multi-zone observation results are merged into the latest live snapshot without overwriting newer sensor readings.
- Fixed the six-hour Recorder cache check so an empty aligned calibration sample set does not cause repeated full-history queries on every coordinator refresh.
- Historical analysis failures are logged without preventing live monitoring from starting.

## What to expect

After installing 0.15.7, the dashboard should populate with live room and pump data while the historical validation is still running. The Predictions thermal-model historical validation section may initially report a pending status; it will populate when the Recorder analysis completes.

The historical analysis still uses the configured calibration history window and six-hour cache interval. This release changes startup sequencing, not the underlying physical-model assumptions or calibration requirements.

## Validation

- GitHub Actions CI passed for the startup fix: unit tests, integration compilation, and Lovelace JavaScript syntax checks.
