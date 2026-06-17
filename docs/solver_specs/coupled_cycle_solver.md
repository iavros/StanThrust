# Stage 3.2 Coupled Cycle Loop Solver Specification

## Purpose

The Coupled Cycle Loop Solver runs a relaxed numerical fixed-point loop across the concept geometry, transient feed model, chamber/nozzle solve, and structural material outputs. It is intended to produce one internally consistent concept operating point instead of separate feed, flow, and margin estimates.

## Solver Name

- **Canonical name:** `Coupled Cycle Loop Solver`
- **Version:** `0.2`
- **Mode:** `stage-3-coupled-cycle-v2`
- **Module:** `liquid_engine_studio/coupled_cycle_solver.py`

## Function Signature

```python
def solve(
    design_request: Dict[str, object],
    upstream_context: Optional[Dict[str, object]] = None,
    initial_chamber_pressure_kpa: Optional[float] = None,
    initial_design: Optional[object] = None,
    convergence_tolerance_kpa: float = 5.0,
    max_iterations: int = 8,
) -> Dict[str, object]:
```

## Numerical Loop

For each iteration:

1. Build a concept design seeded with the current chamber-pressure guess.
2. Run the transient feed solver with that pressure target.
3. Run the Cantera-backed combustion/nozzle solver from the same seeded design. Missing or broken thermochemistry is treated as a solver error.
4. Run material assignment and structural margin outputs.
5. Compute feed-supported pressure, combustion-supported pressure, feed margin, thrust residual, and minimum structural margin.
6. Relax the next chamber-pressure estimate toward the limiting pressure state.
7. Stop when pressure residual, thrust residual, feed margin, and structural margin all satisfy the convergence criteria, or when the iteration limit is reached.

## Convergence Criteria

The solver reports `ok` when:

- pressure residual <= `convergence_tolerance_kpa`
- thrust error <= 3.5%
- minimum feed margin >= `-convergence_tolerance_kpa`
- minimum structural margin ratio > 1.0

Otherwise it returns `converged-degraded` with a full iteration trace.

## Output Highlights

The payload includes:

- `convergence`: iteration count, residuals, feed margin, thrust error, and structural margin
- `results`: final chamber pressure, tank pressures, feed margin, thrust error, and structural margin
- `iteration_trace`: per-iteration feed-supported pressure, combustion-supported pressure, relaxed pressure, residuals, and notes
- `feed_solver_result`: final transient feed result
- `combustion_solver_result`: final combustion/nozzle result
- `structural_solver_result`: final section margin result
- `station_field_updates`: merged provenance-tagged fields from feed, combustion, and structural solvers

## Integration

The desktop Solve action now uses this coupled loop as the primary explicit solve path. Existing plots and exports continue to consume the final feed, combustion, and structural result shapes, while the convergence plot uses the coupled iteration trace when available.

## Notes

- This is still a reduced-order concept-stage numerical model, not a validated hardware design solver.
- Cantera-backed combustion is required for supported solver runs.
- Dependency or mechanism failures are reported as errors so invalid thermochemistry cannot be mistaken for a solved engine state.
