# Material Assignment Solver

## Purpose

Attach material metadata to each major section so downstream solvers and exporters can reason about section identity consistently.

## What it should own

- Mapping section names to selected materials
- Validating material choices against catalogs
- Returning material metadata bundles for sections
- Providing renderer/export-friendly material labels

## Primary handles

Handle: `assign_materials(design_request, concept_envelope_result) -> material_assignment_result`

- Inputs:
  - selected materials
  - geometry sections
- Outputs:
  - `section_materials`
  - `material_notes`
  - `validation_messages`

Handle: `describe_section_materials(material_assignment_result) -> material_summary_rows`

- Purpose: produce readable rows for notes, exports, and future comparison views

## Expected output sections

- `section_materials`
  - fuel tank
  - oxidizer tank
  - feed system
  - chamber
  - nozzle
- `material_notes`
- `compatibility_flags`

## Consumers

- Desktop notes panel
- Project save/load
- CAD JSON export
- Future structural and thermal solvers
