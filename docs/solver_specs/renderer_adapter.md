# Renderer Adapter

## Purpose

Translate solver outputs into view-ready primitives so the visualization layer can evolve independently of the solver layer.

## What it should own

- Mapping geometry sections into renderable primitives
- Exposing labels, station markers, material callouts, and section tags
- Supporting multiple renderer backends in the future

## Primary handles

Handle: `build_render_scene(concept_envelope_result, station_model, material_assignment_result) -> render_scene`

- Inputs:
  - geometry bundle
  - station bundle
  - material bundle
- Outputs:
  - `shapes`
  - `labels`
  - `dimension_lines`
  - `section_tags`
  - `station_markers`
  - `material_callouts`

Handle: `render_scene(renderer_backend, render_scene) -> rendered_view`

- Inputs:
  - backend key such as `canvas`, `svg`, `native-3d`
  - normalized render scene
- Outputs:
  - backend-specific draw result

## Expected future renderer backends

- `canvas-schematic`
- `section-view`
- `exploded-view`
- `native-3d`

## Current UI integration

- The desktop app should only ask for a `render_scene`.
- Backend-specific drawing code should remain isolated from solver code.
