# v0.17.2 — hydraulic vs modulation power and daily energy Sankeys

## Heat-flow comparisons

The System view now presents exactly four Sankey diagrams, one after another:

1. **Power Sankey — hydraulic method:** live estimated power from the hydraulic circuit-flow calculation, shown from boiler to downstairs/upstairs manifolds and individual rooms.
2. **Power Sankey — modulation method:** live boiler output estimated from burner modulation, allocated across active UFH circuits by configured flow and then to served rooms.
3. **Daily energy Sankey — hydraulic method:** accumulated daily hydraulic energy, shown from boiler to manifolds and rooms.
4. **Daily energy Sankey — modulation method:** accumulated modulation-derived energy for intervals with active UFH circulation, shown using the same manifold and room structure.

## Daily energy history

- Persist daily modulation-derived boiler energy and room allocations.
- Allocate modulation energy across active UFH circuits by configured flow, then among their served rooms.
- Retain daily modulation energy history for 30 days.
- Show both daily Sankeys for the same date so the two methods can be compared directly.
- Keep the gas-meter-reconciled experiment as a separate calibration/validation mechanism, not as a third method in the four-diagram comparison.

## Historical output

- Persist modulation-estimated and gas-calibrated modulation output in five-minute system history.
- Show output trends in kW alongside modulation percentage and burner activity.

## Notes

- Modulation-derived output and room allocations are estimates, not direct heat-meter measurements.
- Hydraulic estimates continue to use configured circuit flow and the assumed 9°C water temperature drop.
- No heating, pump, or boiler control behavior changes.
