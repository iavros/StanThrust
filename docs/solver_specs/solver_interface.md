# Common Solver Interface

## Goal

Define a shared pattern so future solvers can be swapped, chained, or versioned without rewriting the app.

## Required metadata

Each solver should declare:

- `solver_name`
- `solver_version`
- `solver_mode`
- `input_schema_version`
- `output_schema_version`

## Standard handles

Handle: `validate_inputs(design_request) -> validation_report`

- Responsibility: confirm required fields exist, ranges are sane, enums are known, and materials/propellants map to supported catalogs
- Returns:
  - `is_valid`
  - `messages`
  - `normalized_request`

Handle: `solve(design_request, upstream_context=None) -> solver_result`

- Responsibility: execute the solver's main responsibility and return a structured result bundle
- Inputs:
  - `design_request`
  - optional `upstream_context`
- Returns:
  - `result_metadata`
  - `payload`
  - `warnings`
  - `dependencies_used`

Handle: `summarize(solver_result) -> summary_block`

- Responsibility: produce a compact description for the UI and exports
- Returns:
  - `title`
  - `key_values`
  - `notes`

## Design request shape

Every solver should expect a `design_request` with these top-level sections:

- `targets`
  - target thrust
  - target impulse
  - target diameter
  - burn time
- `propellants`
  - fuel
  - oxidizer
  - mixture ratio
- `geometry_limits`
  - tank diameter max
  - chamber diameter max
  - nozzle diameter max
- `materials`
  - fuel tank material
  - oxidizer tank material
  - feed system material
  - chamber material
  - nozzle material
- `architecture`
  - pump-fed or blowdown
  - regen cooling enabled
  - film cooling enabled
  - packaging bias

## Result bundle shape

Every solver result should expose:

- `metadata`
- `status`
- `payload`
- `warnings`
- `trace`

`payload` should be solver-specific. `trace` should contain human-readable breadcrumbs for debugging and UI notes.
