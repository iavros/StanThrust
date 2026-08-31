# StanThrust

[![CI Tests](https://github.com/iavros/StanThrust/actions/workflows/tests.yml/badge.svg)](https://github.com/iavros/StanThrust/actions/workflows/tests.yml)
[![Build Installers](https://github.com/iavros/StanThrust/actions/workflows/package.yml/badge.svg)](https://github.com/iavros/StanThrust/actions/workflows/package.yml)

A desktop tool for preliminary liquid rocket engine sizing. StanThrust solves the
feed system, chamber, nozzle, cooling jacket, and structural margins as one
coupled system, then shows the solved geometry, station fields, and margins in a
single workspace.

## Contents

- [Install](#install)
- [Run from source](#run-from-source)
- [Using the workspace](#using-the-workspace)
- [How a solve works](#how-a-solve-works)
- [Design and analysis modes](#design-and-analysis-modes)
- [Repository layout](#repository-layout)
- [Solver report](#solver-report)
- [Validation](#validation)
- [License](#license)

## Install

Installers are published on the
[Releases page](https://github.com/iavros/StanThrust/releases):

| Platform | Asset |
| --- | --- |
| Windows | `StanThrust-Installer.exe` |
| macOS | `StanThrust-macOS.dmg` |

The application can also fetch the latest matching asset itself, from
**Help → Check for Updates**.

## Run from source

Python 3.11 to 3.13 are the supported runtimes; CI builds on 3.11.

```bash
python -m pip install -r requirements.txt
python app.py
```

Cantera and CoolProp are hard requirements. The solver has no
missing-thermochemistry or constant-coolant-property fallbacks; if either
library is unavailable it fails loudly rather than substituting placeholder
properties.

Both ship as compiled extensions, so use a supported Python version rather than
the newest release: wheels for a just-released Python are often unavailable, and
on Windows they can also be blocked by Smart App Control, which rejects unsigned
binaries that have no established reputation. The symptom is an import that
fails with `DLL load failed ... An Application Control policy has blocked this
file` even though `pip` reports the package as installed. Installing into a
Python 3.11-3.13 environment resolves it; `python app.py --self-test-cantera`
confirms the fix.

Useful checks without launching the interface:

```bash
python app.py --self-test-cantera
```

```bash
python app.py --self-test-properties
```

```bash
python app.py --self-test-desktop
```

Tests and linting:

```bash
python -m pytest
```

```bash
python -m ruff check .
```

## Using the workspace

The window is split into design inputs on the left and result views on the
right.

**Inputs.** Categories are reachable from the rail at the top of the panel.
*Mission*, *Envelope*, and *Architecture* are always visible; *Materials*,
*Objectives*, *Solver*, and *Hydraulics* appear when the workspace is switched
from **Essential** to **Full** in the toolbar. Every field carries a description
that shows in the panel footer and as a tooltip.

**Result views.**

| Tab | Contents |
| --- | --- |
| Overview | Headline performance, envelope, margins, solve pipeline, and validation checks |
| Schematic | Propellant stack, feed routing, and the solved chamber and nozzle contour |
| 3D Model | Chamber/nozzle, pumps, injector, and tank views built from the solved geometry; drag to orbit |
| Plots | Burn transient, axial field, thermal march, and convergence plots |
| Data | Every solved value, filterable by category and searchable |
| Report | Plain-text design report for the current preview or solved state |
| Log | Solver log with level filtering, plus the keyed diagnostic snapshot |

**Actions.** `F5` runs the coupled solve. Project save/load and the DXF, CSV,
and CAD JSON exports live under **File**. Editing any input refreshes the
geometry preview immediately; the toolbar chip shows whether the numbers on
screen come from that preview or from a completed solve.

## How a solve works

Every solve follows the same pipeline.

1. **Normalize inputs.** Mission, packaging, propellant, material, and solver
   inputs are bounded and defaulted in `stanthrust/inputs.py`.
2. **Build geometry.** The engine envelope is solved from either the design
   requirements or the specified as-built hardware.
3. **Close the hydraulics.** Injector flow, Darcy-Weisbach line loss,
   regenerative-jacket pressure loss, chamber mass balance, and the pump-fed or
   pressure-fed burn history are solved simultaneously.
4. **Solve thermochemistry and contour.** Equilibrium properties come from
   Cantera; the nozzle contour comes from the method-of-characteristics solver.
5. **Solve the flow.** `stanthrust/chamber_nozzle_solver.py` runs the station
   solve, the shock feedback, and the final viscous quasi-one-dimensional
   correction.
6. **Solve the thermal path.** The equilibrium product composition is frozen and
   mapped into Cantera's GRI-Mech transport phase with at least 0.999999
   product-mass coverage, so viscosity, conductivity, heat capacity, and Prandtl
   number are evaluated at every solved axial temperature and pressure. The
   NASA TP-3380 closure covers the chamber and throat; momentum thickness,
   transition state, and acceleration-driven relaminarization are then marched
   through the divergent nozzle. The nozzle thermal boundary layer is resolved on
   a 96-node axisymmetric wall-normal finite-volume grid, checked against a
   48-node refinement at every axial station. Wall conduction, counterflow
   coolant temperature, and coolant pressure loss march over the complete axial
   geometry, with CoolProp recalculating the coolant state at every station. The
   resulting phase-safe jacket pressure feeds back into fuel injector area, fuel
   supply pressure, and pump-head sizing.
7. **Evaluate structure.** Temperature-dependent allowables, conservative
   property uncertainty, stress, and redesign requirements are evaluated at each
   region's limiting station.
8. **Publish.** The same solved geometry and station fields feed the plots, the
   3D views, saved projects, and every export.

The final saved pass always escalates to the viscous quasi-one-dimensional path
with at least 180 axial stations. The faster *Fast preview* and *Refined solve*
flow models remain available for diagnostic comparison but are not used as the
saved design basis.

## Design and analysis modes

**Design sizing** takes a chamber pressure and solves for injector flow area and
required supply pressure. With regenerative cooling it also calculates the
saturation or critical-pressure boundary, applies the selected supply margin,
integrates jacket loss, and repeats the coupled solve until the coolant-pressure
feedback is stable.

**Hardware analysis** instead requires measured throat diameter, injector flow
areas, discharge coefficients, line geometry, loss coefficients, and supply
pressures, then predicts chamber pressure, mass flow, mixture ratio, and thrust.
It rejects a regenerative-cooling boundary condition below the calculated
single-phase requirement.

Both modes report a seeded Latin-hypercube P05/P50/P95 interval for hydraulic
chamber pressure, mass flow, mixture ratio, and thrust, and both distinguish
specified inputs, calculated design geometry, boundary conditions, and
calculated outputs in the exports.

## Repository layout

```
app.py                        Desktop entry point and packaged-runtime self tests
pyproject.toml                pytest and ruff configuration
stanthrust/
  theme.py                    Design tokens shared by the interface and the plots
  inputs.py                   Defaults, catalogues, and solver assumptions
  design_model.py             Coupled engine sizing and solved geometry
  chamber_nozzle_solver.py    Chamber and nozzle station flow
  hydraulic_chamber_solver.py Injector, line loss, mass balance, uncertainty
  heat_transfer_solver.py     Conjugate wall and coolant march
  boundary_layer_solver.py    Wall-normal thermal march and grid diagnostics
  coupled_cycle_solver.py     The coupled feed/flow/structure loop
  fluid_properties.py         Pressure- and temperature-dependent coolants
  material_properties.py      Temperature-dependent conductivity and allowables
  thermochemistry_provider.py Cantera equilibrium and frozen transport
  moc_nozzle_solver.py        Area-Mach relations and the bell contour
  shock_solver.py             Normal and oblique shock relations
  structural_material_solver.py  Stress, thermal margin, and redesign advice
  uncertainty.py              Uncertainty bounds and output provenance
  visualization_geometry.py   Renderer-ready dimensions from the solved geometry
  exporter.py                 Project persistence and CAD/CSV/DXF export
  plotting.py                 Matplotlib canvases embedded in the interface
  ui/                         Qt interface (see stanthrust/ui/__init__.py)
  data/                       Bundled mechanism and reference data files
assets/                       Icons and application artwork
docs/                         Solver report source, PDF, and generated datasets
packaging/                    Windows and macOS packaging scripts
tests/                        Regression tests used by CI and release builds
```

## Solver report

The technical writeup lives in `docs/main.tex` and
`docs/StanThrust_Solver_Report.pdf`, with generated tables in `docs/data/`.
Regenerate the datasets and LaTeX macros with:

```bash
python docs/generate_assets.py
```

The report source is Overleaf-ready and reads its tables from the same solver
code the desktop application uses.

## Validation

The test suite covers solver coupling, thermochemistry, heat-transfer and shock
diagnostics, coolant and material property references, feed transients, geometry
fields, uncertainty provenance, and the report benchmark cases. It also verifies
transport-species coverage for every supported fuel and oxidizer pair and
confirms that production thermal stations receive the local Cantera properties.

**Thermal validation.** Seven fixed NASA TP-3380 LOX/GH2 calorimeter points act
as a geometry and closure reconstruction check. A second no-calibration runner
reconstructs NASA TP-2726 reading 121, including its 1030:1 conventional bell
contour, measured wall temperatures, and ten source-accepted heat-flux stations.
Without coefficient fitting the current wall-normal model gives about 20% MAPE
and passes the fixed 30% mean-error screen, but its near-throat area-ratio-20
station remains an approximately 133% outlier.

**Reported diagnostics.** Every production thermal station reports acceleration
parameter, momentum thickness, momentum-thickness Reynolds number, selected
boundary-layer regime, wall-normal grid residual, and 48-to-96-node refinement
error.

**Engine benchmarks.** The public engine benchmark tests run reconstructed real
engines through the solver with no optimiser fitting, so the comparison against
published operating points measures solver accuracy rather than curve fit
quality.

The release workflow runs the full suite before publishing installer assets.

## License

Released under the [MIT License](LICENSE).

Cantera, CoolProp, Matplotlib, NumPy, and PyQt5 are redistributed inside the
installers under their own licenses.
