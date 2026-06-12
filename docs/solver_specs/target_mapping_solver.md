# Target Mapping Solver

## Purpose

Convert raw UI inputs into a normalized request bundle that downstream solvers can consume consistently.

## What it should do

- Normalize units and naming
- Resolve dropdown and custom text inputs into catalog-backed records where available
- Separate user intent from hard constraints
- Build a stable `design_request` object for the solver pipeline

## What it should not do

- It should not generate geometry
- It should not run optimization
- It should not render anything

## Primary handles

Handle: `map_targets(raw_ui_state) -> design_request`

- Inputs:
  - raw form fields from the desktop app
- Outputs:
  - `design_request`
  - `mapping_notes`

Handle: `build_solver_context(design_request) -> solver_context`

- Inputs:
  - normalized request
- Outputs:
  - shared catalogs
  - active solver versions
  - export schema version

## Expected outputs

- `targets`
- `constraints`
- `propellant_records`
- `material_records`
- `architecture_flags`
- `mapping_notes`

## UI integration

- The desktop app should call this first before any downstream solver executes.
- Save/load flows should preserve the raw user input state and optionally the mapped request.
