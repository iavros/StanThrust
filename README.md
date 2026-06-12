# StanThrust

StanThrust is a local Python desktop app for preliminary liquid-engine sizing, geometry generation, thermochemistry-backed performance estimation, transient feed analysis, optimization, and CAD-oriented export.

## UI

StanThrust runs through the Qt desktop UI.

- Desktop UI: `liquid_engine_studio/qt_desktop.py`
- Entry point: `app.py`

The results workspace now includes:

- a schematic tab for the propellant/feed/geometry layout
- a plots tab for burn-time pressure history, preliminary thrust curve, axial field trends, and solver convergence
- measurements, summary, and diagnostics tabs driven from the same solve state

## Quick Start

Install the base runtime packages:

```powershell
python -m pip install -r requirements.txt
```

For the modern Qt UI, also install `PyQt5`:

```powershell
python -m pip install PyQt5
```

Run the app:

```powershell
python app.py
```

## Thermochemistry

StanThrust uses Cantera when available and falls back to conservative approximate estimates when it is not.

- Preferred bundled mechanism: `liquid_engine_studio/data/rocket_mech_equilibrium.yaml`
- Additional bundled mechanisms: `rocket_mech.yaml`, `rocket_mech_minimal.yaml`
- Runtime provider: `liquid_engine_studio/thermochemistry_provider.py`

The combustion/nozzle solver now exposes two internal flow modes:

- `Fast Preview`: the default quasi-1D path for ordinary iteration
- `Refined Solve`: a contour-aware quasi-1D mode with ambient-pressure correction and richer nozzle-loss modeling

StanThrust also carries a reduced-order transient feed model for explicit solves:

- pressure-fed cases track burn-time blowdown, chamber-pressure tailoff, and feed-margin history
- pump-fed cases track inlet-tank pressure drift, pump differential pressure, and pump-speed proxy history

## Current Solver Coverage

The current solved stack includes:

- section-based structural and thermal margins for tanks, chamber, throat, and nozzle
- a Rao-style bell nozzle contour with explicit contour metadata and exportable contour points
- a hard overall diameter cap, with both capped geometry and uncapped required envelope reported in diagnostics
- fast and refined quasi-1D chamber/nozzle flow modes
- transient feed-system histories for pressure-fed and pump-fed architectures
- desktop engineering plots driven from the transient feed history, axial station field, and chamber-iteration trace
- benchmark and regression datasets shared across the app, exports, tests, and report

## CAD Export

StanThrust can export the current solved engine geometry in formats that are directly useful for CAD workflows.

- `Profile DXF`: 2D axisymmetric profile sketch suitable for revolve workflows
- `Solid STEP`: faceted neutral solid for CAD import
- `Solid STL`: revolved mesh solid
- `Measurements CSV`: scalar sizing and solver outputs
- `Stations CSV`: axial station data

The export logic lives in:

- `liquid_engine_studio/exporter.py`

Current note:

- the `STEP` export is a faceted solid generated from the solved revolved profile
- it is intended as a practical import format for tools like Onshape
- it is not yet a full analytic B-rep export from a dedicated CAD kernel

## Solver Report

The repository includes an Overleaf-ready solver report package under:

- `docs/overleaf_solver_report/`

That package contains:

- `main.tex`: the report document
- `generate_assets.py`: regenerates report datasets from the current solver
- `data/`: CSV plot tables and LaTeX macros used by the report

The report currently documents:

- geometry closure and Rao-style nozzle contour construction
- propellant and performance closure
- section-based structural and thermal margin equations
- transient feed-system equations and burn-time plots
- fast vs refined flow-mode behavior
- public collegiate benchmark reconstructions and internal regression baselines

There is also a ready-to-upload bundle at:

- `docs/overleaf_solver_report_bundle.zip`

To regenerate the report assets locally:

```powershell
python docs\overleaf_solver_report\generate_assets.py
```

## Diagnostics

One optional diagnostic helper is included for thermochemistry troubleshooting:

- `scripts/thermochemistry_diagnostics.py`

## Build

Install the packaging dependencies before building:

```bash
python -m pip install pyinstaller pillow
python -m pip install -r requirements.txt
```

Build the single-file Windows executable on Windows:

```powershell
python build_windows_app.py
```

Build output:

- `dist/windows/StanThrust.exe`

Build the native macOS app bundle on macOS:

```bash
python build_macos_app.py
```

Build output:

- `dist/macos/StanThrust.app`

The tracked spec files are:

- `StanThrust_windows.spec`
- `StanThrust_macos.spec`

## Packaging Notes

The packaged app bundles the runtime assets it needs, including:

- `Logo.png`
- `Logo.svg`
- `app_icon.ico`
- `app_icon.icns` when generated for macOS
- bundled Cantera mechanism files under `liquid_engine_studio/data`

## Project Structure

- `app.py`: desktop entrypoint
- `liquid_engine_studio/qt_desktop.py`: main application UI
- `liquid_engine_studio/concept_model.py`: geometry and derived-engineering model
- `liquid_engine_studio/exporter.py`: CAD and report-facing export paths
- `liquid_engine_studio/optimizer_hooks.py`: GA and feasibility-first optimization hooks
- `liquid_engine_studio/combustion_cfd_solver.py`: combustion / CFD proxy path
- `docs/overleaf_solver_report/`: solver report source and generated plot assets
- `scripts/`: optional project utilities and diagnostics
- `tests/`: regression and feature validation coverage

## Regenerable Files

These directories are temporary or generated and can be safely recreated:

- `build/`
- `__pycache__/`
- `.pytest_cache/`

`dist/` is also generated, but keep it when you want to retain the latest packaged executable.
