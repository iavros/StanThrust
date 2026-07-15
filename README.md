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
- `stanshock/`: application package and solver code
- `stanshock/data/`: bundled thermochemistry mechanism files
- `assets/`: icons and application artwork
- `docs/`: solver report source, PDF, and generated datasets
- `packaging/`: CI-facing Windows and macOS packaging scripts
- `tests/`: regression tests used by CI and release builds

## Validation

The automated test suite covers solver coupling, thermochemistry, heat-transfer and shock diagnostics, feed transients, geometry fields, optimizer hooks, uncertainty/provenance utilities, and direct report benchmark cases. The public benchmark tests run reconstructed engines through the solver without optimizer fitting. The release workflow runs the suite before publishing installer assets.
