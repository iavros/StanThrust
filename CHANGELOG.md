# Changelog

All notable changes to StanThrust are recorded here. Versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html); while the major
version is `0`, minor releases may change internal module layout.

## 0.2.0 - 2026-08-30

The solver gains an explicit hydraulic closure and a validated thermal path, and
the desktop interface has been rebuilt around them.

### Solver

- **Hydraulic chamber closure** (`hydraulic_chamber_solver.py`). Injector flow,
  Darcy-Weisbach line loss, and the chamber mass balance are solved together, in
  either design-sizing or hardware-analysis mode. Seeded Latin-hypercube
  propagation reports P05/P50/P95 intervals for chamber pressure, mass flow,
  mixture ratio, and thrust.
- **Chamber and nozzle station solve** (`chamber_nozzle_solver.py`) replaces the
  previous `combustion_cfd_solver` module. It marches the area-Mach solution
  along the calculated contour, applies the Cantera equilibrium state, feeds back
  normal-shock behaviour, and applies the viscous quasi-one-dimensional
  correction.
- **Axisymmetric wall-normal thermal march** (`boundary_layer_solver.py`) on a
  96-node finite-volume grid, checked against a 48-node refinement at every
  axial station. Momentum thickness, transition state, and
  acceleration-driven relaminarization are marched through the divergent nozzle.
- **Real coolant and material properties.** `fluid_properties.py` reads
  temperature- and pressure-dependent coolant states from CoolProp at every
  station; `material_properties.py` supplies temperature-dependent conductivity
  and allowable stress. The solver no longer falls back to constant properties.
- **Coupled coolant-pressure feedback.** The phase-safe jacket pressure now
  carries into fuel injector area, fuel supply pressure, and pump-head sizing.
- **Thermal validation** (`thermal_validation.py`) against seven fixed NASA
  TP-3380 LOX/GH2 calorimeter points and a no-calibration reconstruction of NASA
  TP-2726 reading 121, including its 1030:1 bell contour. Without coefficient
  fitting the wall-normal model gives about 20% MAPE and passes the fixed 30%
  mean-error screen; its near-throat area-ratio-20 station remains an
  approximately 133% outlier.
- **Solved-geometry visualization data** (`visualization_geometry.py`) so the
  drawn liner, channel, rib, and jacket radii come from the solve rather than
  from display-only approximations.

### Interface

- **Rebuilt as a layered package.** The single 5,015-line `qt_desktop` module is
  replaced by `stanthrust/ui/`, with presentation, input declarations, scene
  drawing, and the solve pipeline separated. `stanthrust/theme.py` is now the one
  source of colour, type, and spacing tokens, shared with the Matplotlib
  canvases.
- **Menu bar, toolbar, and shortcuts** replace the oversized header panel. `F5`
  solves; project open/save and the DXF, measurement CSV, station CSV, and CAD
  JSON exports live under **File**.
- **Input panel** is a category rail over stacked pages instead of one long
  scroll. Paired fuel and oxidizer fields sit side by side, and every field
  carries a description shown in the panel footer and as a tooltip.
- **Overview** reports thrust, specific impulse, chamber pressure, and mass flow
  once each, alongside envelope, margins, configuration, flow and
  thermochemistry, the solve pipeline, and the validation checks. Unsolved values
  say so rather than showing a placeholder.
- **Plots** are chosen from a grouped list and drawn one at a time at full size.
- **Data** is a single searchable, category-filterable table with copy support.
- **Report** and **Log** gain monospaced layout, copy, and severity filtering.
- **CAD JSON export** and the **uncertainty bounds** reporting option are now
  reachable from the interface; both existed in the codebase but had no entry
  point.

### 3D views

- Surfaces are lit from their own normals (Lambert, fill, Blinn specular, and rim
  terms) instead of being shaded by depth, with shading interpolated across each
  quad. Cylinders and the bell contour now read as curved solids.
- Perspective projection, back-face culling on translucent shells, contact
  shadows, and a reduced station-ring wireframe.
- The chamber view redraws in roughly 30-50 ms rather than 217 ms. The idle
  rotation runs at a lower cadence and stops for good once the model is orbited
  by hand.

### Fixed

- Objective weights survive a project save/load round trip. They were previously
  normalised on save and then quantised into the three-decimal inputs on load.
- The engine profile in the schematic no longer draws at a fixed small scale, and
  its dimension labels are no longer truncated.
- Plot margins, legends, and the flow-field colour bar are positioned in pixels
  rather than as a fraction of the canvas, so they stay legible and unclipped at
  any canvas size.
- Import failures now distinguish a missing package from a compiled extension
  that will not load, and name the likely causes: a wheel that does not match the
  running Python, or a Windows security policy blocking an unsigned binary.

### Project

- Added this changelog, an MIT `LICENSE`, and `pyproject.toml` carrying the
  pytest and ruff configuration. `python -m pytest` no longer needs `PYTHONPATH`.
- CI lints with ruff in addition to running the tests.
- Removed unreachable code: `SolverMetadata`, `score_current_design`,
  `material_allowable_stress`, `build_default_case`, a dead bell-angle
  computation in the contour builder, and several unused view helpers.
- Removed development residue from the test suite: hand-rolled test runners that
  duplicated pytest, progress prints, and a hard-coded `sys.path` entry pointing
  at a directory that does not exist.
- Supported runtimes are now stated as Python 3.11 to 3.13.

## 0.1.4 and earlier

See the [release history](https://github.com/iavros/StanThrust/releases) for
prior versions.
