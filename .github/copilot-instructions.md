# GitHub Copilot Instructions

## Project Overview

This repository is `Liquid Engine Concept Studio`, a local Python desktop app for **concept visualization, software architecture, and CAD blockout planning**.

The app is intentionally scoped to:

- concept geometry
- layout packaging
- UI/UX for a local desktop workflow
- persistent project files
- CAD-friendly exports
- optimization scaffolding
- renderer abstractions
- future solver architecture planning

The app is **not** currently an operational propulsion design tool.

## Tech Stack

- Language: Python 3.8+
- UI: `tkinter` / `ttk`
- App entry point: `app.py`
- Main package: `liquid_engine_studio`
- Legacy browser prototype still exists in `src/` and `index.html`, but the Python desktop app is the primary implementation

## Current Repository Structure

Primary files:

- `app.py`
- `liquid_engine_studio/desktop.py`
- `liquid_engine_studio/concept_model.py`
- `liquid_engine_studio/renderers.py`
- `liquid_engine_studio/exporter.py`
- `liquid_engine_studio/project_io.py`
- `liquid_engine_studio/objectives.py`
- `liquid_engine_studio/optimizer_hooks.py`
- `liquid_engine_studio/propellants.py`
- `liquid_engine_studio/materials.py`
- `liquid_engine_studio/defaults.py`

Architecture docs:

- `docs/solver_specs/README.md`
- `docs/solver_specs/solver_interface.md`
- `docs/solver_specs/target_mapping_solver.md`
- `docs/solver_specs/concept_envelope_solver.md`
- `docs/solver_specs/station_model_solver.md`
- `docs/solver_specs/material_assignment_solver.md`
- `docs/solver_specs/structural_placeholder_solver.md`
- `docs/solver_specs/optimization_adapter.md`
- `docs/solver_specs/renderer_adapter.md`

## Architectural Intent

Prefer code that keeps these layers separate:

1. UI Layer
   - `desktop.py`
   - collects user input
   - updates views
   - should not contain solver logic

2. Model / Solver Layer
   - `concept_model.py`
   - concept-only geometry and summary generation
   - pure data in, pure data out where possible

3. Optimization Layer
   - `optimizer_hooks.py`
   - GA plumbing over concept variables only
   - scores solver outputs through objective functions

4. Objective Layer
   - `objectives.py`
   - pluggable scoring functions
   - should remain independent from UI code

5. Renderer Layer
   - `renderers.py`
   - converts solver outputs into view-ready primitives
   - should be swappable later

6. Export / Persistence Layer
   - `exporter.py`
   - `project_io.py`
   - should consume stable result bundles, not UI widgets

## Coding Preferences

When suggesting or generating code:

- Prefer plain data structures and dataclasses.
- Keep functions focused and composable.
- Separate UI concerns from calculations.
- Prefer explicit field names over positional tuples.
- Keep exported schemas stable and easy to inspect.
- Use ASCII only unless a file already requires Unicode.
- Avoid hidden magic and implicit side effects.
- Prefer readable code over clever code.

## How To Extend The App

Good extensions:

- new concept-only input fields
- richer renderer scenes
- comparison views
- save/load schema improvements
- new export formats
- new objective functions
- adapter layers around future solver families
- additional placeholder sections for future analysis

When adding future solver hooks:

- define handles first
- define request/response shape next
- add status fields such as `placeholder`, `calculated`, `user-specified`, `not-applicable`
- make missing analysis fields explicit instead of silently omitting them

## UI Guidance

The app is a desktop engineering workspace, not a marketing site.

UI changes should:

- stay clear and practical
- fit inside a scrollable desktop layout
- use labels that distinguish `concept`, `placeholder`, and `future solver`
- keep safety notes visible when adding solver-related inputs
- avoid crowding wide forms without scrollbars

If adding more inputs:

- prefer grouped sections such as `Targets`, `Propellants`, `Geometry Limits`, `Materials`, `Architecture`, `Optimization`
- add vertical scrolling where needed
- keep export and project actions visible

## Export Guidance

Exports should be usable for CAD blockout and software workflow handoff.

Good export content:

- geometry envelope values
- named stations
- materials by section
- summary cards
- solver metadata
- explicit placeholder statuses

Do not generate export fields that imply real engineering validity unless they are clearly marked as placeholders.

## Optimization Guidance

The GA in this repo should optimize **concept variables only**.

It should:

- consume normalized request/state
- evaluate candidates via solver outputs
- score with pluggable objectives
- preserve constraints and metadata

It should not:

- bypass solver layers
- score based only on raw UI fields when richer solver output exists
- imply that a best candidate is a build-ready design

## Solver Naming Guidance

If adding new solver modules, prefer names like:

- `target_mapping_solver.py`
- `concept_envelope_solver.py`
- `station_model_solver.py`
- `material_assignment_solver.py`
- `structural_placeholder_solver.py`
- `renderer_adapter.py`

Follow the outlines in `docs/solver_specs/`.

## When Writing Docs

- Be concrete about ownership and interfaces.
- Distinguish current behavior from future intent.
- Use phrases like `concept-only`, `placeholder`, `future solver`, `reserved field`, and `not calculated in the current release` where appropriate.
- Do not write docs that read like operational propulsion guidance.

## What Good Looks Like In This Repo

A strong contribution for this project usually:

- improves the desktop workflow
- makes solver boundaries clearer
- improves data flow between UI, model, optimization, renderer, and exports
- adds future-ready structure without pretending to calculate real hardware values
- leaves the codebase easier to extend than before
