# Station Model Solver

## Purpose

Create a stable station-by-station data structure so future analysis layers can populate it without changing the UI/export contract.

## What it should do

- Define engine stations
- Attach names, axial positions, and geometry references
- Reserve fields for future thermofluid values
- Return explicit status flags for calculated vs placeholder data

## Primary handles

Handle: `build_station_map(design_request, concept_envelope_result) -> station_model`

- Inputs:
  - normalized request
  - concept geometry and CAD stations
- Outputs:
  - ordered `station_rows`

Handle: `annotate_station_fields(station_model, analysis_context) -> station_model`

- Purpose: allow later solver layers to enrich stations with additional values

## Station row contract

Each station row should contain:

- `station_label`
- `station_role`
- `axial_position_mm`
- `envelope_diameter_mm`
- `geometry_reference`
- `field_status`

Reserved analysis fields should exist for:

- temperature
- pressure
- mass flow
- Mach number
- velocity
- density

## Export integration

- CSV and JSON exports should read from this station model directly.
- Missing fields should never be omitted silently.
- Each field should include either a real value or a clear status marker.
