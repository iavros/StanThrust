# StanThrust

[![CI Tests](https://github.com/iavros/StanThrust/actions/workflows/tests.yml/badge.svg)](https://github.com/iavros/StanThrust/actions/workflows/tests.yml)
[![Build Installers](https://github.com/iavros/StanThrust/actions/workflows/package.yml/badge.svg)](https://github.com/iavros/StanThrust/actions/workflows/package.yml)

StanThrust is a desktop engineering tool for preliminary liquid-engine sizing, coupled feed and chamber analysis, Cantera-backed thermochemistry, and live solved-geometry visualization.

## Download

Installers are published on the [latest GitHub Release](https://github.com/iavros/StanThrust/releases/latest):

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
- Cantera-backed thermochemistry using bundled mechanism files.
- MOC-informed bell nozzle contour with explicit geometry fields.
- Pressure-fed and pump-fed transient feed histories.
- Live 3D views for chamber/nozzle, injector, pumps, and tanks.
- Engineering plots, measurements, diagnostics, and CSV/DXF exports.
- GitHub Actions release builds for Windows and macOS installer artifacts.

## Solver Report

The technical solver writeup lives in:

- `docs/overleaf_solver_report/main.tex`
- `docs/overleaf_solver_report/StanThrust_Solver_Report.pdf`
- `docs/overleaf_solver_report/data/`

Regenerate the report datasets with:

```powershell
python docs\overleaf_solver_report\generate_assets.py
```

The report source is Overleaf-ready and uses generated CSV tables from the same solver code used by the desktop app.

## Repository Layout

- `app.py`: desktop entry point
- `stanshock/`: application package and solver code
- `stanshock/data/`: bundled thermochemistry mechanism files
- `assets/`: icons and application artwork
- `docs/overleaf_solver_report/`: solver report source, PDF, and generated datasets
- `packaging/`: CI-facing Windows and macOS packaging scripts
- `tests/`: regression tests used by CI and release builds

## Validation

The automated test suite covers solver coupling, thermochemistry, feed transients, geometry fields, optimizer hooks, uncertainty/provenance utilities, and report benchmark cases. The release workflow runs the suite before publishing installer assets.
