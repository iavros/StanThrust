# Solver Specs

This folder outlines a future solver architecture for StanThrust.

These documents are intentionally interface-focused. They describe:

- What each solver should be responsible for
- The function handles or entry points it should expose
- The inputs each solver should accept
- The outputs each solver should return
- How solvers should compose with the desktop app, exports, and optimizer

They do **not** implement the solvers and do **not** provide operational propulsion calculations.

## Recommended solver stack

1. `Target Mapping Solver`
   Purpose: normalize user intent into a design request bundle
2. `Geometry Envelope Solver`
   Purpose: create geometry, packaging, and visualization hints
3. `Station Model Solver`
   Purpose: describe station objects and field containers for future analysis
4. `Material Assignment Solver`
   Purpose: attach section materials and derived metadata to geometry regions
5. `Structural Placeholder Solver`
   Purpose: reserve output fields for future section properties without computing real hardware values today
6. `Optimization Adapter`
   Purpose: connect solver inputs/outputs to the GA and objective functions
7. `Renderer Adapter`
   Purpose: translate solver output bundles into view-ready primitives

## Integration guidance

- The desktop UI should collect inputs once and emit a single `design_request` object.
- Each solver should accept and return plain data structures.
- Each solver should be independently testable.
- Each solver should advertise version metadata.
- Downstream solvers should never reach back into UI code directly.
- Exporters should consume stable result bundles rather than querying solver internals.
