# Concept Envelope Solver

## Purpose

Produce concept geometry, section lengths, packaging summaries, CAD stations, and visualization hints.

## What it should own

- Overall engine packaging
- Tank and section envelope lengths
- Chamber/nozzle blockout geometry
- CAD station positions
- Visualization hints for renderer layers
- High-level summary cards for the desktop app

## Primary handles

Handle: `solve_concept_envelope(design_request) -> concept_envelope_result`

- Inputs:
  - `targets`
  - `propellants`
  - `geometry_limits`
  - `architecture`
  - `materials`
- Outputs:
  - `geometry_bundle`
  - `cad_station_bundle`
  - `summary_bundle`
  - `visualization_bundle`
  - `notes`

Handle: `build_measurement_rows(concept_envelope_result) -> measurement_rows`

- Purpose: convert geometry results into exportable measurement rows for UI tables and CSV files

## Expected output sections

- `geometry_bundle`
  - tank envelope lengths
  - chamber envelope length
  - nozzle envelope length
  - injector face envelope
  - feed-system bay envelope
  - total stack length
- `cad_station_bundle`
  - named stations
  - axial positions
  - diameter references
- `visualization_bundle`
  - section tags
  - colors
  - renderer hints

## Consumers

- Desktop summary cards
- Measurement table
- CAD JSON export
- Visualization renderer
- Optimization adapter
