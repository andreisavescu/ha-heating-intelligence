# Multi-zone thermal model (v0.15.0)

## Objective

Replace the current single whole-house thermal interpretation with a coupled,
read-only physical model. Existing room learning and measured energy analytics
remain available as observations and validation signals; this model does not
replace them.

## Model topology

The network is assembled from lumped thermal nodes connected by thermal
conductances. Each dynamic node has a temperature and heat capacity. A
connection transfers heat according to:

    Q_dot(i -> j) = G_ij * (T_i - T_j)

Each dynamic node evolves according to:

    C_i * dT_i/dt = gains_i + sum_j(G_ji * (T_j - T_i))

Here G is in W/K, C is in Wh/K, gains are in W, and time is integrated in
hours. Outdoor temperature is a fixed boundary node. Solar and internal gains
are heat inputs, not unexplained corrections to the measured heat-loss value.

## Planned node families

- Room air: one node per configured room, initialized from its temperature
  sensor.
- Room / building mass: one or more slower nodes representing furnishings,
  internal structure and thermally coupled mass. These must be calibrated from
  observations; they are not assumed equal to room air temperature.
- UFH slab / screed: a separate node per configured manifold/floor initially,
  with optional room coupling when floor-loop assignment and floor areas are
  known. Supply/return water temperatures and pump state provide operating
  context; electrical pump power is not treated as delivered thermal power.
- Building envelope: wall, roof, floor and glazing paths represented by
  conductances to the outdoor boundary. Geometry, orientation, shading and
  material profile are used only where enough information exists.
- Attic: a separate air node connected to upstairs/roof nodes and outdoors
  when attic temperature is configured.
- Buffer and DHW: independent storage nodes only when corresponding
  temperature sensors and usable tank metadata are configured. They must not
  be inferred from boiler or pump electrical readings.

## Observations and inputs

Existing room temperature, demand, window, AC-power and optional CO2/humidity
observations feed room state and ventilation diagnostics. The configured
outdoor temperature and PWS weather readings provide boundary conditions and
solar/wind context. Gas and AC energy remain independent delivered-heat
estimates with explicit efficiency/COP assumptions. Pump electricity is
auxiliary electrical consumption and is not a proxy for water-side heat flow.

Solar gains require orientation, glazing area and shading information. Until
those are configured and calibrated, solar radiation is reported as context
and is not converted into an invented room heat gain. Likewise, CO2-decay
ACH is used as a measured ventilation input where available; otherwise
infiltration remains unknown rather than silently assigned a default.

## Learning and calibration

1. Start with a transparent network using configured values and explicitly
   labelled defaults only for numerical solver initialization.
2. Estimate unknown conductances and effective capacities from sufficiently
   clean, time-aligned observations. Exclude open-window periods, unverified
   demand states, active/unavailable AC heating and invalid sensor periods.
3. Fit on one time window and validate on held-out periods. Report sample
   coverage, parameter bounds, residual error and confidence.
4. Keep parameters bounded and physically interpretable. Do not claim a
   parameter is measured when it is only weakly identifiable from available
   sensors.
5. Compare predicted room temperatures, daily gas/AC delivered-heat estimates,
   attic gradients and UFH response with actual observations before exposing
   derived performance metrics as reliable.

## Implementation stages

- Stage 1: pure RC network solver and numerical tests (implemented).
- Stage 2: observation mapping for configured rooms, CO2/humidity/ACH,
  outdoor weather, attic, UFH floor context and boiler/pump telemetry
  (initial implementation; physical connections remain incomplete).
- Stage 3: parameter estimation, data-quality checks and confidence reporting
  (in progress; no calibrated parameters are claimed yet).
- Stage 4: overview entity exposure and initial Lovelace status/coverage view
  (in progress; detailed network visualization remains outstanding).
- Stage 5: validation against real operating history and documentation.

## Current implementation boundary

The coordinator now publishes a multi-zone observation inventory under
`thermal_model.multi_zone`. Room configuration also accepts an optional measured room volume
in m³. When both volume and ACH are supplied, the model estimates a
ventilation conductance using air density 1.2 kg/m³ and specific heat
1005 J/(kg·K), with the source and inputs shown explicitly. This path is
reported separately from conductive envelope links. It includes room-air observations, an attic node
when configured, an outdoor boundary, floor-level UFH context, and a heating
plant telemetry summary. A roof conductance is exposed only when both roof
U-value and roof area are explicitly configured. The attic-floor path is
retained as pending until it can be coupled to an upstairs thermal zone.

UFH slab temperature/capacity and plant thermal output are intentionally
unavailable: room-air temperature, screed thickness, water temperatures and
pump electrical power alone do not identify those quantities. The dashboard
shows this readiness state and the missing parameter lists. Solar radiation,
window areas by orientation, glazing/shading descriptions and internal-gain
classification are exposed as context only; no solar or occupancy heat watts
are invented without the missing calibrated inputs. The RC solver is not yet
run against this incomplete inventory.

## Safety and interpretation

The integration remains read-only. It does not control the boiler, pumps,
thermostats, valves or HVAC equipment. Model outputs are estimates, not
direct measurements of heat flow. If the available data cannot distinguish
two physical parameters, the model must expose that uncertainty rather than
inventing precision.


## Room response evidence exposed to calibration

Each room node now carries the existing rolling 30-day heating and coasting
response rates, their confidence scores and valid interval counts when the
room learner has enough clean observations. These are observed temperature
slopes in °C/h, not physical RC parameters.

A temperature slope by itself cannot distinguish thermal storage from heat
transfer to outdoors or adjacent rooms. In particular, the current demand
signal is not a measurement of delivered thermal power. Therefore these
observations are not converted into room heat capacity, envelope conductance
or a fitted time constant. They are exposed to show which rooms have usable
response history and to guide later calibration using independent heat-input
and boundary observations.


## Recorder history coverage

The coordinator queries the configured room temperature, demand, window,
CO2 and humidity entities plus configured outdoor, solar, attic, boiler,
water-temperature, AC and pump entities over a rolling seven-day Recorder
window. The query runs in the Recorder executor and is refreshed no more
often than every six hours, not on each ten-second coordinator update.

The result is reduced to per-entity sample counts, numeric sample counts,
first/last timestamps, observed span and median update interval. Raw state
history is not copied into the integration's persistent store and is not
transmitted externally. The dashboard uses this coverage summary to show
whether sufficient source data exists before parameter identification.

Recorder retention remains controlled by the user's Home Assistant Recorder
configuration. With the default ten-day detailed-history retention, the
seven-day window should fit; if entities are excluded from Recorder or
history retention is shorter, coverage will be incomplete. This stage is
data-readiness assessment, not yet a claim that the RC parameters have been
calibrated or independently validated.
