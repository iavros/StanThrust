"""
Stage 3.1 Coupled Cycle Loop Solver - Proof of Concept.

Demonstrates iterative convergence pattern between feed pressure-drop and combustion
CFD solvers as a stepping stone toward full coupled cycle loop architecture.

Current (Stage 3.1 pilot):
  - Accepts design request and creates design baseline
  - Runs iteration loop that calls feed solver and combustion solver
  - Tracks convergence history and iteration traces
  - Merges station_field_updates from both solvers with source provenance
  - Returns status and results with iteration metadata

Future (Stage 3.2+):
  - Deeper solver integration so tank pressures feed back into combustion calculations
  - Tighter coupling where combustion chamber pressure directly updates feed solver inputs
  - Full cycle closure where thrust, chamber pressure, and flow rates converge together

Notes:
  - This Stage 3.1 version establishes the orchestration framework and provenance tracking
  - Full physical coupling would require modifications to feed and combustion solvers
    to accept upstream pressure/flow constraints
"""

from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from liquid_engine_studio.feed_pressure_drop_solver import solve as solve_feed_system
from liquid_engine_studio.combustion_cfd_solver import run_combustion_cfd_proxy
from liquid_engine_studio.material_assignment_solver import assign_materials
from liquid_engine_studio.structural_material_solver import build_structural_materials_output
from liquid_engine_studio.concept_model import create_concept_design


SOLVER_NAME = "Coupled Cycle Loop Solver"
SOLVER_VERSION = "0.1"
SOLVER_MODE = "stage-3-coupled-cycle-v1"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _metadata() -> Dict[str, str]:
    return {
        "solver_name": SOLVER_NAME,
        "solver_version": SOLVER_VERSION,
        "solver_mode": SOLVER_MODE,
        "input_schema_version": "1.0",
        "output_schema_version": "1.0",
    }


def _error_result(req: Dict[str, object], error_message: str, trace_message: str) -> Dict[str, object]:
    return {
        "metadata": _metadata(),
        "status": "error",
        "payload": {"request": req, "error": error_message},
        "warnings": [],
        "trace": [trace_message],
    }


@dataclass
class ConvergenceInfo:
    """Track convergence state across iterations."""
    iteration: int
    chamber_pressure_kpa: float
    fuel_tank_pressure_kpa: float
    oxidizer_tank_pressure_kpa: float
    residual_kpa: float
    converged: bool
    notes: List[str]


def _extract_chamber_pressure_from_combustion(
    combustion_result: Optional[Dict[str, object]],
) -> Optional[float]:
    """Extract calculated chamber pressure from combustion solver output."""
    if not isinstance(combustion_result, dict):
        return None
    payload = _as_dict(combustion_result.get("payload"))
    results = _as_dict(payload.get("results"))
    chamber_pressure = results.get("chamber_pressure_kpa")
    if chamber_pressure is not None:
        try:
            return float(chamber_pressure)
        except (TypeError, ValueError):
            pass
    return None


def _extract_chamber_pressure_from_feed(
    feed_result: Optional[Dict[str, object]],
) -> Optional[float]:
    """Extract chamber pressure estimate from feed solver output."""
    if not isinstance(feed_result, dict):
        return None
    payload = _as_dict(feed_result.get("payload"))
    summary = _as_dict(payload.get("summary"))
    chamber_pressure = summary.get("estimated_chamber_pressure_kpa")
    if chamber_pressure is not None:
        try:
            return float(chamber_pressure)
        except (TypeError, ValueError):
            pass
    return None


def _merge_station_field_updates(
    feed_result: Optional[Dict[str, object]],
    combustion_result: Optional[Dict[str, object]],
) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Merge station_field_updates from feed and combustion solvers with provenance."""
    updates = {}

    # Extract from feed solver
    if isinstance(feed_result, dict):
        payload = _as_dict(feed_result.get("payload"))
        feed_updates = payload.get("station_field_updates")
        if isinstance(feed_updates, dict):
            for station_label, fields in feed_updates.items():
                if station_label not in updates:
                    updates[station_label] = {}
                if isinstance(fields, dict):
                    updates[station_label].update(fields)

    # Extract from combustion solver (may override feed updates for shared fields)
    if isinstance(combustion_result, dict):
        payload = _as_dict(combustion_result.get("payload"))
        comb_updates = payload.get("station_field_updates")
        if isinstance(comb_updates, dict):
            for station_label, fields in comb_updates.items():
                if station_label not in updates:
                    updates[station_label] = {}
                if isinstance(fields, dict):
                    updates[station_label].update(fields)

    # Extract from structural material solver (adds thermal_margin updates)
    try:
        # structural_result may be attached to combustion_result under payload or separately
        structural_result = None
        if isinstance(combustion_result, dict):
            structural_result = _as_dict(combustion_result.get("structural_result")) or None
    except Exception:
        structural_result = None

    if isinstance(structural_result, dict):
        payload = _as_dict(structural_result.get("payload"))
        struct_updates = payload.get("station_field_updates")
        if isinstance(struct_updates, dict):
            for station_label, fields in struct_updates.items():
                if station_label not in updates:
                    updates[station_label] = {}
                if isinstance(fields, dict):
                    updates[station_label].update(fields)

    return updates


def iterate_coupling_loop(
    design_request: Dict[str, object],
    initial_chamber_pressure_kpa: float,
    initial_design: Optional[object] = None,
    convergence_tolerance_kpa: float = 5.0,
    max_iterations: int = 8,
) -> Tuple[ConvergenceInfo, Optional[Dict[str, object]], Optional[Dict[str, object]]]:
    """
    Run the coupled feed/combustion iteration loop.

    Args:
        design_request: normalized design request dict
        initial_chamber_pressure_kpa: starting chamber pressure from concept solver
        initial_design: optional ConceptDesign object for combustion solver input
        convergence_tolerance_kpa: residual threshold for convergence
        max_iterations: maximum number of iterations

    Returns:
        Tuple of (final_convergence_info, final_feed_result, final_combustion_result)
    """
    req = _as_dict(design_request)
    chamber_pressure_kpa = _clamp(
        initial_chamber_pressure_kpa, 100.0, 10000.0
    )
    fuel_tank_pressure_kpa = chamber_pressure_kpa + 200.0
    oxidizer_tank_pressure_kpa = chamber_pressure_kpa + 250.0

    last_chamber_pressure = chamber_pressure_kpa
    final_feed_result = None
    final_combustion_result = None
    trace_list: List[ConvergenceInfo] = []

    for iteration in range(max_iterations):
        iteration_notes: List[str] = []

        # Step 1: Call feed solver with current chamber pressure
        feed_request = dict(req)
        feed_request["target_chamber_pressure_kpa"] = chamber_pressure_kpa

        try:
            feed_result = solve_feed_system(
                feed_request,
                upstream_context={
                    "source": "coupled-cycle-loop",
                    "iteration": iteration + 1,
                    "stage": "convergence-loop",
                },
            )
            final_feed_result = feed_result
            if feed_result.get("status") == "ok":
                payload = _as_dict(feed_result.get("payload"))
                summary = _as_dict(payload.get("summary"))
                fuel_tank_pressure_kpa = _safe_float(
                    summary.get("required_tank_pressure_kpa"), fuel_tank_pressure_kpa
                )
                oxidizer_tank_pressure_kpa = fuel_tank_pressure_kpa + 50.0
                iteration_notes.append(
                    "Feed solver: tank pressure = {0:.1f} kPa".format(fuel_tank_pressure_kpa)
                )
            else:
                iteration_notes.append("Feed solver failed or degraded")
        except Exception as e:
            iteration_notes.append("Feed solver exception: {0}".format(str(e)))

        # Step 2: Call combustion solver with updated tank pressures (through design object)
        if initial_design is not None:
            try:
                combustion_result = run_combustion_cfd_proxy(
                    initial_design,
                    upstream_context={
                        "source": "coupled-cycle-loop",
                        "iteration": iteration + 1,
                        "stage": "convergence-loop",
                        "fuel_tank_pressure_kpa": fuel_tank_pressure_kpa,
                        "oxidizer_tank_pressure_kpa": oxidizer_tank_pressure_kpa,
                    },
                )
                final_combustion_result = combustion_result
                # Assign materials (concept-only) and compute structural margins where possible
                try:
                    material_result = assign_materials(req, {"payload": {"geometry_bundle": {}}})
                    structural_result = build_structural_materials_output(req, {"payload": {}}, material_result, combustion_result)
                    # Attach structural result into combustion result payload for downstream merging
                    try:
                        if isinstance(final_combustion_result, dict):
                            payload = final_combustion_result.setdefault("payload", {})
                            payload["structural_result"] = structural_result
                    except Exception:
                        pass
                except Exception:
                    # Non-fatal: structural/material pipeline is advisory
                    pass
                new_chamber_pressure = _extract_chamber_pressure_from_combustion(
                    combustion_result
                )
                if new_chamber_pressure is not None and isinstance(new_chamber_pressure, (int, float)):
                    new_chamber_pressure = float(new_chamber_pressure)
                    residual_kpa = abs(new_chamber_pressure - chamber_pressure_kpa)
                    chamber_pressure_kpa = _clamp(new_chamber_pressure, 100.0, 10000.0)
                    iteration_notes.append(
                        "Combustion solver: chamber pressure = {0:.1f} kPa, residual = {1:.2f} kPa".format(
                            chamber_pressure_kpa, residual_kpa
                        )
                    )

                    converged = residual_kpa <= convergence_tolerance_kpa
                    conv_info = ConvergenceInfo(
                        iteration=iteration + 1,
                        chamber_pressure_kpa=chamber_pressure_kpa,
                        fuel_tank_pressure_kpa=fuel_tank_pressure_kpa,
                        oxidizer_tank_pressure_kpa=oxidizer_tank_pressure_kpa,
                        residual_kpa=residual_kpa,
                        converged=converged,
                        notes=iteration_notes,
                    )
                    trace_list.append(conv_info)

                    if converged:
                        return conv_info, final_feed_result, final_combustion_result
                else:
                    iteration_notes.append("Combustion solver: no chamber pressure in result")
            except Exception as e:
                iteration_notes.append("Combustion solver exception: {0}".format(str(e)))
        else:
            iteration_notes.append("No design object for combustion solver")

    # Max iterations reached without convergence
    residual_kpa = abs(chamber_pressure_kpa - last_chamber_pressure)
    final_conv_info = ConvergenceInfo(
        iteration=max_iterations,
        chamber_pressure_kpa=chamber_pressure_kpa,
        fuel_tank_pressure_kpa=fuel_tank_pressure_kpa,
        oxidizer_tank_pressure_kpa=oxidizer_tank_pressure_kpa,
        residual_kpa=residual_kpa,
        converged=False,
        notes=["Reached max iterations ({0})".format(max_iterations)],
    )
    trace_list.append(final_conv_info)
    return final_conv_info, final_feed_result, final_combustion_result


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def validate_inputs(design_request: Dict[str, object]) -> Dict[str, object]:
    """Validate and normalize coupled cycle solver inputs."""
    req = _as_dict(design_request)
    normalized = {
        "target_thrust_newtons": _safe_float(req.get("target_thrust_newtons"), 500.0),
        "target_chamber_pressure_kpa": _safe_float(req.get("target_chamber_pressure_kpa"), 1500.0),
        "tank_diameter_mm": _safe_float(req.get("tank_diameter_mm"), 100.0),
        "chamber_diameter_mm": _safe_float(req.get("chamber_diameter_mm"), 80.0),
        "use_pumps": bool(req.get("use_pumps", False)),
        "regen_cooling": bool(req.get("regen_cooling", False)),
        "mixture_ratio": _safe_float(req.get("mixture_ratio"), 2.0),
        "burn_time_seconds": _safe_float(req.get("burn_time_seconds"), 12.0),
    }
    return {"normalized_request": normalized}


def _prepare_initial_design(
    req: Dict[str, object],
    upstream_context: Optional[Dict[str, object]],
    initial_chamber_pressure_kpa: Optional[float],
    initial_design: Optional[object],
) -> Tuple[float, object]:
    if initial_chamber_pressure_kpa is not None and initial_design is not None:
        return initial_chamber_pressure_kpa, initial_design

    design_state = dict(req)
    if upstream_context:
        design_state.update(upstream_context)

    concept_design = create_concept_design(design_state)
    if initial_chamber_pressure_kpa is None:
        engineering_values = getattr(concept_design.derived, "engineering_values", {})
        initial_chamber_pressure_kpa = engineering_values.get("chamber_pressure_kpa", 1500.0)

    if initial_design is None:
        initial_design = concept_design

    return float(initial_chamber_pressure_kpa), initial_design


def _build_iteration_trace(conv_info: ConvergenceInfo) -> List[Dict[str, object]]:
    return [
        {
            "iteration": conv_info.iteration,
            "chamber_pressure_kpa": round(conv_info.chamber_pressure_kpa, 2),
            "fuel_tank_pressure_kpa": round(conv_info.fuel_tank_pressure_kpa, 2),
            "oxidizer_tank_pressure_kpa": round(conv_info.oxidizer_tank_pressure_kpa, 2),
            "residual_kpa": round(conv_info.residual_kpa, 2),
            "converged": conv_info.converged,
            "notes": conv_info.notes,
        }
    ]


def solve(
    design_request: Dict[str, object],
    upstream_context: Optional[Dict[str, object]] = None,
    initial_chamber_pressure_kpa: Optional[float] = None,
    initial_design: Optional[object] = None,
    convergence_tolerance_kpa: float = 5.0,
    max_iterations: int = 8,
) -> Dict[str, object]:
    """
    Solve coupled feed/combustion cycle loop.

    Args:
        design_request: normalized design request dict
        upstream_context: optional context from upstream caller
        initial_chamber_pressure_kpa: starting chamber pressure (default: concept estimate)
        initial_design: optional ConceptDesign object for combustion solver
        convergence_tolerance_kpa: residual threshold for convergence
        max_iterations: maximum iterations

    Returns:
        Result dict with metadata, status, payload (including convergence trace and merged station updates)
    """
    validation = validate_inputs(design_request)
    req = _as_dict(validation.get("normalized_request"))

    try:
        initial_chamber_pressure_kpa, initial_design = _prepare_initial_design(
            req,
            upstream_context,
            initial_chamber_pressure_kpa,
            initial_design,
        )
    except Exception as exc:
        return _error_result(req, "Failed to create design: {0}".format(str(exc)), "Design creation failed")

    initial_chamber_pressure_kpa = _clamp(initial_chamber_pressure_kpa, 100.0, 10000.0)

    try:
        conv_info, feed_result, combustion_result = iterate_coupling_loop(
            req,
            initial_chamber_pressure_kpa,
            initial_design,
            convergence_tolerance_kpa,
            max_iterations,
        )
    except Exception as exc:
        return _error_result(
            req,
            "Iteration loop failed: {0}".format(str(exc)),
            "Coupling loop raised exception",
        )

    merged_station_updates = _merge_station_field_updates(feed_result, combustion_result)
    trace_lines = [
        "Stage 3.1 Coupled Cycle Loop Solver",
        "Initial chamber pressure: {0:.1f} kPa".format(initial_chamber_pressure_kpa),
        "Convergence tolerance: {0:.2f} kPa, max iterations: {1}".format(
            convergence_tolerance_kpa, max_iterations
        ),
    ]
    iteration_trace = _build_iteration_trace(conv_info)

    warnings = []
    if not conv_info.converged:
        warnings.append(
            "Did not achieve convergence in {0} iterations. Final residual: {1:.2f} kPa".format(
                conv_info.iteration, conv_info.residual_kpa
            )
        )
    warnings.append("Stage 3.1 is a concept-stage coupled cycle proxy with conservative feedback loops.")

    return {
        "metadata": _metadata(),
        "status": "ok" if conv_info.converged else "converged-degraded",
        "payload": {
            "request": req,
            "convergence": {
                "iteration_count": conv_info.iteration,
                "converged": conv_info.converged,
                "final_residual_kpa": round(conv_info.residual_kpa, 2),
                "convergence_tolerance_kpa": convergence_tolerance_kpa,
            },
            "results": {
                "chamber_pressure_kpa": round(conv_info.chamber_pressure_kpa, 2),
                "fuel_tank_pressure_kpa": round(conv_info.fuel_tank_pressure_kpa, 2),
                "oxidizer_tank_pressure_kpa": round(conv_info.oxidizer_tank_pressure_kpa, 2),
            },
            "iteration_trace": iteration_trace,
            "station_field_updates": merged_station_updates,
            "feed_solver_result": feed_result if isinstance(feed_result, dict) else None,
            "combustion_solver_result": combustion_result if isinstance(combustion_result, dict) else None,
        },
        "warnings": warnings,
        "trace": trace_lines,
    }




