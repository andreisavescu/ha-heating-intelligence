# Thermal experiment analysis (0.15.6 development)

## Purpose

The November 15–16, 2026 boiler interruption and AC-heating period is a
useful independent excitation experiment for the multi-zone model. This
development stage adds pure analysis primitives for Recorder/CSV-shaped
samples. It does not change heating controls or claim that room-air readings
measure slab temperature or delivered heat.

## Event annotations

The user reported that the boiler and upstairs UFH circulation pump stopped
at approximately 12:00 local time on November 15. AC heating was reported to
start later, with bedroom doors closed; the upstairs UFH pump remained off.
These are user-supplied experimental conditions. The CSV does not independently
contain the boiler or upstairs pump state.

The climate entity records its own state changes. They are retained separately
from the manually supplied boiler/pump event annotations. All timestamps are
normalized to UTC for analysis; display layers may convert them to local time.

## Measurement handling

- Temperature response uses the nearest observed samples at declared interval
  endpoints, with a maximum endpoint-distance tolerance. It does not invent
  interpolated measurements.
- AC electrical energy is integrated from trapezoidal power samples only when
  both endpoint readings are finite, non-negative, and no more than five
  minutes apart. Negative values, unavailable states, and long gaps are
  excluded and counted.
- The AC sensor represents combined electrical input. It is not split across
  bedrooms and is not converted into delivered thermal energy without a
  separately justified COP model.
- The model does not infer a UFH slab temperature or capacity from room-air
  temperatures alone.

## Validation boundary

The new module is independent of Home Assistant and can be tested with
synthetic Recorder-shaped rows. The next stage is to run the full exported
experiment through these primitives, inspect data coverage and event timing,
then add the resulting reproducible regression case and connect the qualified
diagnostics to calibration. Chronological holdout periods must remain separate
from fitting data.
