# StanThrust Solver Report

This folder is an Overleaf-ready LaTeX report describing the current StanThrust solver stack in engineering terms, with explicit formulas, generated datasets, and comparison plots.

## Contents

- `main.tex`: primary report document
- `generate_assets.py`: regenerates example CSV data and LaTeX macros from the current solver implementation
- `data/`: generated plot tables and numeric macros used by the report

## What the Report Covers

The current report documents:

- input normalization and geometry closure
- MOC-informed nozzle contour construction
- thrust, impulse, mass-flow, and propellant closure
- section-based structural and thermal margin relations
- transient pressure-fed and pump-fed feed-system behavior
- fast vs refined quasi-1D chamber/nozzle flow modes
- reconstructed collegiate benchmark cases and internal regression baselines

The desktop app now exposes the same reduced-order histories in its Qt results workspace through a dedicated plots tab:

- burn-time pressure history
- preliminary thrust and mass-flow traces
- axial pressure / velocity field views
- chamber-iteration convergence traces

## Regenerate the Example Assets

From the project root:

```powershell
python docs\overleaf_solver_report\generate_assets.py
```

The script writes:

- `data/nozzle_contour.csv`
- `data/axial_profile.csv`
- `data/iteration_trace.csv`
- `data/pressure_modes.csv`
- `data/feed_transient_pressure_fed.csv`
- `data/feed_transient_pump_fed.csv`
- `data/geometry_breakdown.csv`
- `data/propellant_breakdown.csv`
- `data/nozzle_loss_breakdown.csv`
- `data/cooling_sweep.csv`
- `data/flow_mode_comparison.csv`
- `data/benchmark_engines.csv`
- `data/public_benchmark_reference_cases.csv`
- `data/internal_regression_baselines.csv`
- `data/reconstructed_benchmark_cases.csv`
- `data/report_macros.tex`

The benchmark CSV files are generated from the shared solver registry in
`liquid_engine_studio/benchmark_cases.py`, so the report, exports, and automated
regression tests all use the same benchmark definitions.

The transient feed CSV files are generated from the same solver-interface path used by the app, so the report plots reflect the same burn-time feed behavior shown in the desktop UI and exports.

## Use in Overleaf

1. Create a new blank Overleaf project.
2. Upload the full `overleaf_solver_report` folder contents.
3. Set `main.tex` as the main document if Overleaf does not detect it automatically.
4. Compile with `pdfLaTeX`.

The report uses `pgfplots` and reads the generated CSV tables directly.
