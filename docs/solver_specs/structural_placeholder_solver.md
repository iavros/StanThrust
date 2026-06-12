# Structural Placeholder Solver

## Purpose

Define how future structural outputs should be shaped, without implementing real structural sizing in the current app.

## Why this exists

The UI and exports need a stable place for future section properties. This solver spec reserves that structure so later work can plug in cleanly.

## Primary handles

Handle: `build_structural_output_schema(design_request, concept_envelope_result, material_assignment_result) -> structural_result`

- Inputs:
  - normalized request
  - geometry bundle
  - material bundle
- Outputs:
  - section property rows
  - calculation-status rows
  - warnings

Handle: `summarize_structural_sections(structural_result) -> structural_summary`

- Purpose: expose a compact UI view of which sections have placeholder vs calculated data

## Reserved output fields

Per section, reserve slots for:

- wall thickness
- liner thickness
- jacket thickness
- rib height
- rib pitch
- flange thickness
- fastener class

Each field should carry:

- `value`
- `unit`
- `status`
- `source_solver`

## Current expected status values

- `placeholder`
- `user-specified`
- `calculated`
- `not-applicable`

## Export integration

- CAD JSON should include these fields when present.
- CSV export may include them as a dedicated structural sheet or separate file later.
