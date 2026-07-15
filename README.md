# StanThrust

[![CI Tests](https://github.com/iavros/StanThrust/actions/workflows/tests.yml/badge.svg)](https://github.com/iavros/StanThrust/actions/workflows/tests.yml)
[![Build Installers](https://github.com/iavros/StanThrust/actions/workflows/package.yml/badge.svg)](https://github.com/iavros/StanThrust/actions/workflows/package.yml)

StanThrust is a desktop engineering tool for preliminary liquid-engine sizing, coupled feed and chamber analysis, Cantera-backed thermochemistry, and live solved-geometry visualization. The final design pass uses the most detailed in-app path: coupled feed pressure, Cantera thermochemistry, MOC nozzle geometry, shock feedback, Navier-Stokes viscous station corrections, heat transfer, and material evaluation.

## Download

Installers are published on the [GitHub Releases page](https://github.com/iavros/StanThrust/releases):

- Windows: `StanThrust-Installer.exe`
- macOS: `StanThrust-macOS.dmg`

The desktop app also includes `Check For Update`, which downloads the latest matching release asset for the current operating system.

## Run From Source

For development or review:

```powershell
python -m pip install -r requirements.txt
python app.py
```

Python 3.11 is the supported development runtime. Cantera is required; the solver does not use a missing-thermochemistry fallback.

## How It Works

Each solve follows one shared engineering pipeline:

1. Normalize the mission, packaging, propellant, material, and solver inputs from `stanshock/inputs.py`.
2. Close the engine geometry and chamber-pressure state in `stanshock/design_model.py`.
3. Solve the pump-fed or pressure-fed transient and verify pressure margin.
4. Obtain equilibrium thermochemistry from Cantera and construct the MOC nozzle contour.
5. Run the chamber/nozzle station solve, shock feedback, and final Navier-Stokes viscous correction in `stanshock/combustion_cfd_solver.py`.
6. Evaluate wall heat transfer, material stress, redesign requirements, and final uncertainty bounds.
7. Send the same solved geometry and station fields to the desktop plots, 3D views, saved projects, and exports.

The desktop final pass always uses the most detailed flow path with at least 180 axial stations. Fast and refined modes remain available for diagnostic comparisons, but they are not used as the final saved design basis.

## Features

- Coupled numerical solve across feed system, chamber/nozzle flow, and structural margins.
- Final solve automatically escalates to the Navier-Stokes path with dense axial stations.
- Final coupled payload includes uncertainty bounds for headline pressure, thrust, mass flow, specific impulse, wall temperature, and material margin values.
- Cantera-backed thermochemistry using bundled mechanism files.
- MOC-informed bell nozzle contour with explicit geometry fields.
- Automatic nozzle exit sizing with pressure-matched, underexpanded, and overexpanded targets.
- Reduced-order heat-transfer solve for chamber, throat, and nozzle wall sections.
- Rankine-Hugoniot shock diagnostics for overexpanded nozzle states.
- Material stress, thermal-margin, and redesign recommendations.
- Pressure-fed and pump-fed transient feed histories.
- Live calculated-geometry 3D views for chamber/nozzle, injector, pumps, tanks, regen ribs, and film slots.
- Engineering plots, 2D flow-field visualization, measurements, diagnostics, and CSV/DXF exports.
- Direct real-engine benchmark reconstructions, with no optimizer fitting, to compare solver output against published operating points.
- GitHub Actions release builds for Windows and macOS installer artifacts.

## Solver Report

The technical solver writeup lives in:

- `docs/main.tex`
- `docs/StanThrust_Solver_Report.pdf`
- `docs/data/`

Regenerate the report datasets with:

```powershell
python docs\generate_assets.py
```

The report source is Overleaf-ready and uses generated CSV tables from the same solver code used by the desktop app.

## Repository Layout

- `app.py`: desktop entry point
- `stanshock/inputs.py`: defaults, selectable inputs, catalogs, and solver assumptions
- `stanshock/design_model.py`: coupled engine sizing and solved geometry
- `stanshock/combustion_cfd_solver.py`: all chamber and nozzle CFD functions
- `stanshock/*_solver.py`: focused feed, thermal, structural, MOC, and shock solvers
- `stanshock/uncertainty.py`: uncertainty bounds and output provenance
- `stanshock/qt_desktop.py`: desktop interface and calculated-geometry rendering
- `stanshock/data/`: bundled thermochemistry mechanism files
- `assets/`: icons and application artwork
- `docs/`: solver report source, PDF, and generated datasets
- `packaging/`: CI-facing Windows and macOS packaging scripts
- `tests/`: regression tests used by CI and release builds

## Validation

The automated test suite covers solver coupling, thermochemistry, heat-transfer and shock diagnostics, feed transients, geometry fields, optimizer hooks, uncertainty provenance, and direct report benchmark cases. The public benchmark tests run reconstructed engines through the solver without optimizer fitting. The release workflow runs the suite before publishing installer assets.
