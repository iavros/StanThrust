# StanThrust Solver Report

This folder is an Overleaf-ready LaTeX report describing the current StanThrust solver stack in engineering terms, with explicit formulas, generated datasets, and comparison plots.

## Contents

- `main.tex`: primary report document
- `StanThrust_Solver_Report.pdf`: checked PDF build of the report
- `generate_assets.py`: regenerates example CSV data and LaTeX macros from the current solver implementation
- `data/`: generated plot tables and numeric macros used by the report

## What the Report Covers

The current report documents:

- input normalization and geometry closure
- MOC characteristic-net nozzle contour construction
- thrust, impulse, mass-flow, and propellant closure
- section-based structural and thermal margin relations
- transient pressure-fed and pump-fed feed-system behavior
- fast, refined, and Navier-Stokes chamber/nozzle flow modes
- final-pass uncertainty bounds for pressure, thrust, mass flow, specific impulse, wall temperature, and material margin
- gas, wall, coolant, and viscous station heat-transfer calculations
- Rankine-Hugoniot shock diagnostics and shock-driven design feedback for overexpanded nozzles
- material stress, thermal margins, and redesign recommendations
- live-render geometry fields used by the desktop 3D model views
- regen rib and film-slot visualization rules used by the live 3D model
- reconstructed collegiate benchmark cases and internal regression baselines
- direct benchmark solver runs with no optimizer fitting

The desktop app now exposes the same solver histories in its Qt results workspace through a dedicated plots tab:

- burn-time pressure history
- solved thrust and mass-flow traces
- axial pressure / velocity field views
- chamber-iteration convergence traces
- Mach and area-ratio evolution
- thermal and density field trends
- coupled residual, feed-margin, structural-margin, and material-margin diagnostics
- axisymmetric 2D nozzle flow-field visualization

## Regenerate the Example Assets

From the project root:

```powershell
python docs\generate_assets.py
```

The script writes:

- `data/nozzle_contour.csv`
- `data/axial_profile.csv`
- `data/iteration_trace.csv`
- `data/pressure_modes.csv`
- `data/feed_transient_pressure_fed.csv`
- `data/feed_transient_pump_fed.csv`
- `data/geometry_breakdown.csv`
- `data/render_geometry.csv`
- `data/propellant_breakdown.csv`
- `data/internal_regression_summary.csv`
- `data/individual_benchmark_runs.csv`
- `data/nozzle_loss_breakdown.csv`
- `data/cooling_sweep.csv`
- `data/flow_mode_comparison.csv`
- `data/benchmark_engines.csv`
- `data/public_benchmark_reference_cases.csv`
- `data/internal_regression_baselines.csv`
- `data/reconstructed_benchmark_cases.csv`
- `data/report_macros.tex`

The benchmark CSV files are generated from the shared solver registry in
`stanshock/benchmark_cases.py`, so the report, exports, and automated
regression tests all use the same benchmark definitions. Public benchmark
cases are run directly through the Navier-Stokes solver path. The generator
does not run an optimizer or tune the cases to match the reference values.

The transient feed CSV files are generated from the same solver-interface path used by the app, so the report plots reflect the same burn-time feed behavior shown in the desktop UI and exports.

## Use in Overleaf

1. Create a new blank Overleaf project.
2. Upload the full `docs` folder contents.
3. Set `main.tex` as the main document if Overleaf does not detect it automatically.
4. Compile with `pdfLaTeX`.

The report uses `pgfplots` and reads the generated CSV tables directly.
