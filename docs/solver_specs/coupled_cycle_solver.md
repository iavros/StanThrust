# Stage 3.1 Coupled Cycle Loop Solver Specification

## Purpose

The Coupled Cycle Loop Solver demonstrates iterative convergence between feed pressure-drop and combustion CFD solvers. It serves as a proof-of-concept for orchestrating multi-solver feedback toward full cycle closure.

**Current role (Stage 3.1 pilot):** Establish orchestration framework, provenance tracking, and iteration tracing that will evolve into deeper coupling in Stage 3.2+.

## Solver Name

- **Canonical name:** `Coupled Cycle Loop Solver`
- **Version:** `0.1` (pilot)
- **Mode:** `stage-3-coupled-cycle-v1`
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

## Inputs

### Design Request (normalized dict)

- `target_thrust_newtons` (float): Target thrust in newtons (default: 500 N)
- `target_chamber_pressure_kpa` (float): Target chamber pressure in kPa (default: 1500 kPa)
- `tank_diameter_mm` (float): Tank diameter in mm (default: 100 mm)
- `chamber_diameter_mm` (float): Chamber diameter in mm (default: 80 mm)
- `use_pumps` (bool): Use pump-fed mode vs. blowdown (default: False)
- `regen_cooling` (bool): Enable regenerative cooling (default: False)
- `mixture_ratio` (float): Propellant mixture ratio (default: 2.0)
- `burn_time_seconds` (float): Burn duration in seconds (default: 12 s)

### Optional Parameters

- `upstream_context` (dict): Metadata about caller and solver stage
- `initial_chamber_pressure_kpa` (float): Override initial chamber pressure (default: derived from concept design)
- `initial_design` (ConceptDesign): Pre-created design object (default: auto-generated)
- `convergence_tolerance_kpa` (float): Residual threshold for convergence (default: 5 kPa)
- `max_iterations` (int): Maximum iterations before stopping (default: 8)

## Iteration Loop

The solver executes the following cycle for up to `max_iterations`:

1. **Feed Solver Step**
   - Calls `feed_pressure_drop_solver.solve()` with current chamber pressure
   - Extracts required tank pressures from feed solver output
   - Updates fuel and oxidizer tank pressure estimates

2. **Combustion Solver Step**
   - Calls `combustion_cfd_proxy.run_combustion_cfd_proxy()` with updated design
   - Extracts chamber pressure from combustion solver result
   - Computes residual: `|new_chamber_pressure - old_chamber_pressure|`

3. **Convergence Check**
   - If residual ≤ `convergence_tolerance_kpa`: **converged**, return
   - If iteration count ≥ `max_iterations`: **max iterations reached**, return
   - Otherwise: **continue** to next iteration

## Outputs

### Return Structure

```python
{
    "metadata": {
        "solver_name": "Coupled Cycle Loop Solver",
        "solver_version": "0.1",
        "solver_mode": "stage-3-coupled-cycle-v1",
        "input_schema_version": "1.0",
        "output_schema_version": "1.0",
    },
    "status": "ok" | "converged-degraded" | "error",
    "payload": {
        "request": {...},  # Normalized input dict
        "convergence": {
            "iteration_count": int,
            "converged": bool,
            "final_residual_kpa": float,
            "convergence_tolerance_kpa": float,
        },
        "results": {
            "chamber_pressure_kpa": float,
            "fuel_tank_pressure_kpa": float,
            "oxidizer_tank_pressure_kpa": float,
        },
        "iteration_trace": [
            {
                "iteration": int,
                "chamber_pressure_kpa": float,
                "fuel_tank_pressure_kpa": float,
                "oxidizer_tank_pressure_kpa": float,
                "residual_kpa": float,
                "converged": bool,
                "notes": [str],
            },
            # ... per iteration ...
        ],
        "station_field_updates": {
            # Merged from feed and combustion solvers with provenance
            "station_label": {
                "field_name": {
                    "value": float,
                    "unit": str,
                    "status": "calculated" | "placeholder",
                    "source_solver": str,
                },
                # ...
            },
            # ...
        },
        "feed_solver_result": {...},  # Raw result from feed solver (Stage 2.1)
        "combustion_solver_result": {...},  # Raw result from combustion solver (Stage 2.2)
    },
    "warnings": [str],
    "trace": [str],
}
```

### Status Codes

- **`ok`**: Converged within tolerance and iteration limits
- **`converged-degraded`**: Ran until max iterations; residual did not meet tolerance (but results are returned for inspection)
- **`error`**: Failed to create concept design or critical exception during iteration

## Provenance and Station Field Updates

The solver merges `station_field_updates` from both upstream solvers:

1. **Feed Pressure-Drop Solver** (Stage 2.1) contributes:
   - Fuel Feed Inlet: tank pressure, fuel mass flow
   - Pump Or Pressurization Bay: averaged pressure, total mass flow
   - Injector Face: chamber pressure, total mass flow

2. **Combustion CFD Proxy Solver** (Stage 2.2) contributes:
   - Chamber Mid: temperature, pressure, mass flow
   - Throat Region: temperature, pressure (reduced), Mach=1.0, mass flow
   - Nozzle Exit Plane: temperature, pressure (ambient), exit Mach, mass flow

Each field entry includes a `source_solver` tag preserving which solver calculated it.

## Convergence Criteria

- **Converged:** Residual ≤ tolerance
- **Not converged (but stopped):** Max iterations reached without meeting residual threshold
- **Error:** Failed to initialize or exception during iteration

## Key Design Decisions

1. **Modular Architecture**: Coupled solver orchestrates existing Stage 2 solvers without modifying them, allowing independent testing and incremental coupling.

2. **Provenance Tracking**: Explicit `source_solver` tags in all station field updates maintain accountability as multiple solvers contribute to final design.

3. **Iteration Trace**: Per-iteration snapshots (chamber pressure, tank pressures, residual, notes) enable debugging and visualization of convergence behavior.

4. **Conservative Default Tolerances**: 5 kPa tolerance and 8-iteration limit are conservative for concept-stage; production use may adjust based on solver fidelity.

5. **Graceful Degradation**: If convergence is not achieved, results are still returned (status: `converged-degraded`) with warnings, allowing designers to inspect partial closure.

## Future Evolution (Stage 3.2+)

- Deeper coupling so feed solver outputs directly constrain combustion solver inputs
- Bi-directional pressure/flow feedback loops
- Support for multi-pass optimization where coupled cycle results seed the next iteration
- Adaptive convergence tolerance based on design trajectory
- Integration with structural and material solvers for full design closure

## Integration with Desktop UI

- Stage 3.1 can be called from the desktop app as an optional "refined solver" pass
- Results can be exported alongside Stage 2 results for comparison
- Iteration traces can be visualized in solver metadata preview panel

## Testing

See `tests/test_coupled_cycle_solver.py` for comprehensive test coverage including:
- Convergence detection and max iteration limits
- Input validation and normalization
- Payload structure validation
- Station field provenance tagging
- Pump vs. blowdown mode handling
- Edge cases (very small/large thrust values)

## Notes

- This is a **concept-stage** coupled cycle solver; it does not validate against real propulsion data
- Full coupling would require tighter integration where feed and combustion solvers accept upstream constraints
- Stage 3.1 establishes the orchestration framework; Stage 3.2+ will deepen solver coupling

