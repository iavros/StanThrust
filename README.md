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
- a MOC-informed bell nozzle contour with explicit Prandtl-Meyer metadata and renderable contour points
- a hard overall diameter cap, with both capped geometry and uncapped required envelope reported in diagnostics
- fast and refined quasi-1D chamber/nozzle flow modes
- transient feed-system histories for pressure-fed and pump-fed architectures
- a coupled numerical loop that relaxes chamber pressure across feed, chamber/nozzle, and structural-margin solvers
- desktop engineering plots driven from the transient feed history, axial station field, and chamber-iteration trace
- benchmark and regression datasets shared across the app, exports, tests, and report

## Model Rendering And Export

StanThrust renders the current solved engine geometry directly inside the desktop app.

- `3D Model`: live revolved model preview generated from the solved chamber, throat, nozzle, and cooling envelope
- `Profile DXF`: 2D axisymmetric profile sketch suitable for revolve workflows
- `Measurements CSV`: scalar sizing and solver outputs
- `Stations CSV`: axial station data

The export logic lives in:

- `liquid_engine_studio/exporter.py`

Current note:

- the desktop UI no longer exposes 3D STEP/STL export actions; the solved revolved geometry is rendered in-app for review
- the `Profile DXF` export remains available for users who want a lightweight 2D CAD sketch
- CSV exports remain the supported interchange path for sizing and solver data

## Installers

Create the installer on the same operating system you want to distribute. PyInstaller builds are platform-specific, so Windows and macOS need separate artifacts.

For Windows, run this on Windows:

```powershell
python packaging\windows\build_installer.py
```

This creates `dist/installer/StanThrust-Installer.exe`, which is the file to send to Windows users. They do not need Python.

For macOS, run this on macOS:

```bash
python packaging/macos/build_installer.py
```

This creates `dist/installer/StanThrust-macOS.dmg`, which is the file to send to Mac users. They do not need Python.

The app also includes `Check For Update` in the Actions panel. It checks the latest GitHub Release, downloads the matching Windows installer or macOS DMG into the user's Downloads folder, and lets the user run that installer. Publish release assets with the names produced by the packaging scripts so the update checker can find them.

The repository includes a GitHub Actions packaging workflow:

- `.github/workflows/package.yml`

Run it manually from GitHub Actions to produce Windows and macOS artifacts, or push a version tag such as `v1.0.1` to build both installers and attach them to a GitHub Release.

## Solver Report

The repository includes an Overleaf-ready solver report package under:

- `docs/overleaf_solver_report/`

That package contains:

- `main.tex`: the report document
- `generate_assets.py`: regenerates report datasets from the current solver
- `data/`: CSV plot tables and LaTeX macros used by the report

The report currently documents:

- geometry closure and MOC-informed nozzle contour construction
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
python packaging\windows\build_app.py
```

Build output:

- `dist/windows/StanThrust.exe`

Build the native macOS app bundle on macOS:

```bash
python packaging/macos/build_app.py
```

Build output:

- `dist/macos/StanThrust.app`

The tracked spec files are:

- `packaging/windows/StanThrust.spec`
- `packaging/macos/StanThrust.spec`

## Packaging Notes

The packaged app bundles the runtime assets it needs, including:

- `assets/Logo.png`
- `assets/Logo.svg`
- `assets/app_icon.ico`
- `assets/app_icon.icns`
- bundled Cantera mechanism files under `liquid_engine_studio/data`

## Project Structure

- `app.py`: desktop entrypoint
- `liquid_engine_studio/qt_desktop.py`: main application UI
- `liquid_engine_studio/concept_model.py`: geometry and derived-engineering model
- `liquid_engine_studio/exporter.py`: CAD and report-facing export paths
- `liquid_engine_studio/optimizer_hooks.py`: GA and feasibility-first optimization hooks
- `liquid_engine_studio/combustion_cfd_solver.py`: combustion / CFD proxy path
- `assets/`: application icons and brand assets used by the UI and installers
- `packaging/`: Windows and macOS build scripts plus PyInstaller spec files
- `docs/overleaf_solver_report/`: solver report source and generated plot assets
- `scripts/`: optional project utilities and diagnostics
- `tests/`: regression and feature validation coverage

## Regenerable Files

These directories are temporary or generated and can be safely recreated:

- `build/`
- `__pycache__/`
- `.pytest_cache/`

`dist/` is also generated, but keep it when you want to retain the latest packaged executable.
