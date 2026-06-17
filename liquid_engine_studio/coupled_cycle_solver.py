"""
Stage 3.2 coupled cycle loop solver.

This module runs a damped fixed-point solve across the concept geometry,
feed-transient model, combustion/nozzle solver, and structural material outputs.
The goal is not high-fidelity propulsion certification; it is a numerically
consistent concept state where chamber pressure, feed margin, thrust residual,
and section margins are solved together instead of reported as isolated guesses.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from liquid_engine_studio.combustion_cfd_solver import run_combustion_cfd_proxy
from liquid_engine_studio.concept_model import create_concept_design
from liquid_engine_studio.feed_pressure_drop_solver import solve as solve_feed_system
from liquid_engine_studio.material_assignment_solver import assign_materials
from liquid_engine_studio.solver_assumptions import get_default_solver_assumptions
from liquid_engine_studio.structural_material_solver import build_structural_materials_output


SOLVER_NAME = "Coupled Cycle Loop Solver"
SOLVER_VERSION = "0.2"
SOLVER_MODE = "stage-3-coupled-cycle-v2"
ProgressCallback = Callable[[float, str], None]


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _metadata() -> Dict[str, str]:
    return {
        "solver_name": SOLVER_NAME,
        "solver_version": SOLVER_VERSION,
        "solver_mode": SOLVER_MODE,
        "input_schema_version": "1.1",
        "output_schema_version": "1.1",
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
    iteration: int
    chamber_pressure_kpa: float
    fuel_tank_pressure_kpa: float
    oxidizer_tank_pressure_kpa: float
    residual_kpa: float
    thrust_error_fraction: float
    minimum_feed_margin_kpa: float
    minimum_structural_margin_ratio: float
    converged: bool
    notes: List[str]


def validate_inputs(design_request: Dict[str, object]) -> Dict[str, object]:
    """Validate and normalize coupled-cycle solver inputs."""
    req = _as_dict(design_request)
    normalized = {
        "fuel_name": str(req.get("fuel_name", "Fuel") or "Fuel"),
        "oxidizer_name": str(req.get("oxidizer_name", "Oxidizer") or "Oxidizer"),
        "injector_type": str(req.get("injector_type", "impinging") or "impinging"),
        "target_thrust_newtons": _safe_float(req.get("target_thrust_newtons"), 500.0),
        "target_impulse_newton_seconds": _safe_float(req.get("target_impulse_newton_seconds"), 3000.0),
        "target_chamber_pressure_kpa": _safe_float(req.get("target_chamber_pressure_kpa"), 1500.0),
        "target_diameter_mm": _safe_float(req.get("target_diameter_mm"), 110.0),
        "tank_diameter_mm": _safe_float(req.get("tank_diameter_mm"), 100.0),
        "chamber_diameter_mm": _safe_float(req.get("chamber_diameter_mm"), 80.0),
        "nozzle_diameter_mm": _safe_float(req.get("nozzle_diameter_mm"), 95.0),
        "mixture_ratio": _safe_float(req.get("mixture_ratio"), 2.0),
        "burn_time_seconds": _safe_float(req.get("burn_time_seconds"), 12.0),
        "factor_of_safety": _safe_float(req.get("factor_of_safety"), 2.0),
        "fuel_tank_material": str(req.get("fuel_tank_material", "Aluminum 6061-T6") or "Aluminum 6061-T6"),
        "oxidizer_tank_material": str(req.get("oxidizer_tank_material", "Aluminum 6061-T6") or "Aluminum 6061-T6"),
        "feed_system_material": str(req.get("feed_system_material", "Stainless Steel 304") or "Stainless Steel 304"),
        "chamber_material": str(req.get("chamber_material", "Stainless Steel 304") or "Stainless Steel 304"),
        "nozzle_material": str(req.get("nozzle_material", "Stainless Steel 304") or "Stainless Steel 304"),
        "packaging_bias": str(req.get("packaging_bias", "balanced") or "balanced"),
        "use_pumps": bool(req.get("use_pumps", False)),
        "regen_cooling": bool(req.get("regen_cooling", False)),
        "film_cooling": bool(req.get("film_cooling", False)),
        "solver_flow_model": str(req.get("solver_flow_model", "refined") or "refined"),
    }
    return {"normalized_request": normalized}


def _build_feed_request(state: Dict[str, object], chamber_pressure_kpa: float) -> Dict[str, object]:
    return {
        "targets": {
            "target_thrust_newtons": state["target_thrust_newtons"],
            "target_impulse_newton_seconds": state["target_impulse_newton_seconds"],
            "target_diameter_mm": state["target_diameter_mm"],
            "target_chamber_pressure_kpa": chamber_pressure_kpa,
            "burn_time_seconds": state["burn_time_seconds"],
        },
        "propellants": {
            "fuel": state["fuel_name"],
            "oxidizer": state["oxidizer_name"],
            "mixture_ratio": state["mixture_ratio"],
        },
        "geometry_limits": {
            "tank_diameter_mm": state["tank_diameter_mm"],
            "chamber_diameter_mm": state["chamber_diameter_mm"],
            "nozzle_diameter_mm": state["nozzle_diameter_mm"],
        },
        "materials": {
            "fuel_tank_material": state["fuel_tank_material"],
            "oxidizer_tank_material": state["oxidizer_tank_material"],
            "feed_system_material": state["feed_system_material"],
            "chamber_material": state["chamber_material"],
            "nozzle_material": state["nozzle_material"],
        },
        "analysis": {"factor_of_safety": state["factor_of_safety"]},
        "architecture": {
            "injector_type": state["injector_type"],
            "use_pumps": state["use_pumps"],
            "regen_cooling": state["regen_cooling"],
            "film_cooling": state["film_cooling"],
            "packaging_bias": state["packaging_bias"],
        },
    }


def _build_design_for_pressure(state: Dict[str, object], chamber_pressure_kpa: float) -> object:
    design = create_concept_design(state)
    values = design.derived.engineering_values
    values["chamber_pressure_kpa"] = round(chamber_pressure_kpa, 4)
    values["coupled_pressure_override_kpa"] = round(chamber_pressure_kpa, 4)
    return design


def _extract_combustion_pressure(combustion_result: Optional[Dict[str, object]], fallback: float) -> float:
    if not isinstance(combustion_result, dict):
        return fallback
    summary = _as_dict(combustion_result.get("summary"))
    physics = _as_dict(combustion_result.get("physics"))
    results = _as_dict(physics.get("results"))
    return _safe_float(
        summary.get("chamber_pressure_kpa", results.get("chamber_pressure_kpa")),
        fallback,
    )


def _extract_thrust_error(combustion_result: Optional[Dict[str, object]]) -> float:
    if not isinstance(combustion_result, dict):
        return 1.0
    summary = _as_dict(combustion_result.get("summary"))
    return abs(_safe_float(summary.get("thrust_error_fraction"), 1.0))


def _combustion_surrogate(
    design: object,
    chamber_pressure_kpa: float,
    feed_summary: Dict[str, object],
    reason: str,
) -> Dict[str, object]:
    values = dict(getattr(design.derived, "engineering_values", {}))
    target_thrust = _safe_float(values.get("target_thrust_newtons"), _safe_float(design.inputs.target_thrust_newtons, 1.0))
    nominal_thrust = _safe_float(values.get("calculated_thrust_newtons"), target_thrust)
    feed_scale = _safe_float(feed_summary.get("final_chamber_pressure_kpa"), chamber_pressure_kpa) / max(1.0, chamber_pressure_kpa)
    predicted_thrust = nominal_thrust * _clamp(feed_scale, 0.35, 1.08)
    thrust_error = abs(predicted_thrust - target_thrust) / max(1.0, target_thrust)
    mass_flow = _safe_float(values.get("propellant_mass_flow_kg_s"), 0.0) * _clamp(feed_scale, 0.35, 1.08)
    return {
        "metadata": {
            "solver_name": "Combustion Numerical Surrogate",
            "solver_version": "0.1",
            "solver_mode": "coupled-fallback",
            "flow_model": "surrogate",
        },
        "status": "approximate",
        "status_detail": reason,
        "summary": {
            "chamber_pressure_kpa": round(chamber_pressure_kpa, 3),
            "predicted_thrust_newtons": round(predicted_thrust, 3),
            "predicted_isp_seconds": 210.0,
            "mass_flow_kg_s": round(mass_flow, 5),
            "thrust_error_fraction": round(thrust_error, 6),
            "flow_model": "surrogate",
            "flow_model_label": "Coupled numerical surrogate",
        },
        "iteration_trace": [
            {
                "iteration": 1.0,
                "chamber_pressure_kpa": round(chamber_pressure_kpa, 3),
                "relative_error": round(thrust_error, 6),
            }
        ],
        "physics": {
            "results": {
                "chamber_pressure_kpa": round(chamber_pressure_kpa, 3),
                "predicted_thrust_newtons": round(predicted_thrust, 3),
                "mass_flow_kg_s": round(mass_flow, 5),
            },
            "nozzle": {
                "status": "approximate",
                "overall_efficiency": 0.9,
                "loss_fraction": 0.1,
            },
        },
        "station_field_updates": {},
        "warnings": [reason],
    }


def _run_combustion(
    state: Dict[str, object],
    design: object,
    chamber_pressure_kpa: float,
    feed_summary: Dict[str, object],
    max_iterations: int,
) -> Dict[str, object]:
    assumptions = get_default_solver_assumptions()
    flow_model = str(state.get("solver_flow_model", "refined")).strip().lower()
    assumptions = assumptions.__class__(
        **{
            **assumptions.__dict__,
            "flow_model": "refined" if flow_model == "refined" else "fast",
            "max_iterations": max(3, min(200, max_iterations * 8)),
        }
    )
    try:
        return run_combustion_cfd_proxy(
            design,
            assumptions,
            station_count=18,
            max_iterations_override=max(3, min(200, max_iterations * 8)),
            thermochemistry_mode="auto",
        )
    except Exception as exc:
        return _combustion_surrogate(
            design,
            chamber_pressure_kpa,
            feed_summary,
            "Combustion solver fallback used during coupled solve: {0}".format(str(exc)),
        )


def _run_structural(
    state: Dict[str, object],
    combustion_result: Dict[str, object],
) -> Dict[str, object]:
    material_result = assign_materials(state, {"payload": {"geometry_bundle": {}}})
    return build_structural_materials_output(
        state,
        {"payload": {"geometry_bundle": {}}},
        material_result,
        combustion_result,
    )


def _minimum_structural_margin(structural_result: Optional[Dict[str, object]], design: object) -> float:
    values = dict(getattr(design.derived, "engineering_values", {}))
    fallback = _safe_float(values.get("minimum_structural_margin_ratio"), 0.0)
    if not isinstance(structural_result, dict):
        return fallback
    rows = list(_as_dict(structural_result.get("payload")).get("section_property_rows", []))
    margins = []
    for row in rows:
        fields = _as_dict(row.get("fields"))
        margin = _as_dict(fields.get("structural_margin_ratio"))
        margins.append(_safe_float(margin.get("value"), fallback))
    return min(margins) if margins else fallback


def _merge_station_field_updates(
    feed_result: Optional[Dict[str, object]],
    combustion_result: Optional[Dict[str, object]],
    structural_result: Optional[Dict[str, object]],
) -> Dict[str, Dict[str, Dict[str, object]]]:
    updates: Dict[str, Dict[str, Dict[str, object]]] = {}

    for result in (feed_result, combustion_result, structural_result):
        if not isinstance(result, dict):
            continue
        payload = _as_dict(result.get("payload"))
        station_updates = result.get("station_field_updates", payload.get("station_field_updates"))
        if not isinstance(station_updates, dict):
            continue
        for station_label, fields in station_updates.items():
            updates.setdefault(str(station_label), {})
            if isinstance(fields, dict):
                updates[str(station_label)].update(fields)
    return updates


def iterate_coupling_loop(
    design_request: Dict[str, object],
    initial_chamber_pressure_kpa: float,
    initial_design: Optional[object] = None,
    convergence_tolerance_kpa: float = 5.0,
    max_iterations: int = 8,
    progress_callback: Optional[ProgressCallback] = None,
) -> Tuple[ConvergenceInfo, Optional[Dict[str, object]], Optional[Dict[str, object]], Optional[Dict[str, object]], List[Dict[str, object]]]:
    """Run the relaxed coupled feed/combustion/structure numerical loop."""
    state = dict(design_request)
    pressure_guess_kpa = _clamp(initial_chamber_pressure_kpa, 100.0, 10000.0)
    relaxation = 0.42

    final_feed_result = None
    final_combustion_result = None
    final_structural_result = None
    final_design = initial_design
    trace_rows: List[Dict[str, object]] = []
    final_info = ConvergenceInfo(
        iteration=0,
        chamber_pressure_kpa=pressure_guess_kpa,
        fuel_tank_pressure_kpa=pressure_guess_kpa + 200.0,
        oxidizer_tank_pressure_kpa=pressure_guess_kpa + 250.0,
        residual_kpa=pressure_guess_kpa,
        thrust_error_fraction=1.0,
        minimum_feed_margin_kpa=0.0,
        minimum_structural_margin_ratio=0.0,
        converged=False,
        notes=[],
    )

    for iteration in range(1, max(1, max_iterations) + 1):
        iteration_base = 8.0 + 84.0 * (iteration - 1) / max(1, max_iterations)
        iteration_span = 84.0 / max(1, max_iterations)
        if progress_callback is not None:
            progress_callback(iteration_base, "Coupled iteration {0}: preparing design state".format(iteration))
        design = _build_design_for_pressure(state, pressure_guess_kpa)
        final_design = design
        feed_request = _build_feed_request(state, pressure_guess_kpa)
        if progress_callback is not None:
            progress_callback(iteration_base + iteration_span * 0.18, "Coupled iteration {0}: solving feed transient".format(iteration))
        feed_result = solve_feed_system(
            feed_request,
            upstream_context={
                "source": "coupled-cycle-loop",
                "iteration": iteration,
                "target_chamber_pressure_kpa": pressure_guess_kpa,
            },
        )
        final_feed_result = feed_result
        feed_summary = _as_dict(_as_dict(feed_result.get("payload")).get("summary"))

        if progress_callback is not None:
            progress_callback(iteration_base + iteration_span * 0.44, "Coupled iteration {0}: solving chamber and nozzle".format(iteration))
        combustion_result = _run_combustion(
            state,
            design,
            pressure_guess_kpa,
            feed_summary,
            max_iterations,
        )
        final_combustion_result = combustion_result
        if progress_callback is not None:
            progress_callback(iteration_base + iteration_span * 0.72, "Coupled iteration {0}: checking structural margins".format(iteration))
        structural_result = _run_structural(state, combustion_result)
        final_structural_result = structural_result

        combustion_pressure_kpa = _extract_combustion_pressure(combustion_result, pressure_guess_kpa)
        feed_supported_pressure_kpa = min(
            _safe_float(feed_summary.get("initial_chamber_pressure_kpa"), pressure_guess_kpa),
            _safe_float(feed_summary.get("final_chamber_pressure_kpa"), pressure_guess_kpa),
        )
        minimum_feed_margin_kpa = _safe_float(feed_summary.get("minimum_feed_margin_kpa"), 0.0)
        if minimum_feed_margin_kpa < 0.0:
            feed_supported_pressure_kpa += minimum_feed_margin_kpa * 0.45

        physics_target_kpa = min(combustion_pressure_kpa, feed_supported_pressure_kpa)
        next_pressure_kpa = (1.0 - relaxation) * pressure_guess_kpa + relaxation * physics_target_kpa
        next_pressure_kpa = _clamp(next_pressure_kpa, 100.0, 10000.0)

        pressure_residual_kpa = abs(next_pressure_kpa - pressure_guess_kpa)
        thrust_error_fraction = _extract_thrust_error(combustion_result)
        minimum_structural_margin_ratio = _minimum_structural_margin(structural_result, design)
        converged = (
            pressure_residual_kpa <= convergence_tolerance_kpa
            and thrust_error_fraction <= 0.035
            and minimum_feed_margin_kpa >= -convergence_tolerance_kpa
            and minimum_structural_margin_ratio > 1.0
        )
        notes = [
            "feed-supported Pc={0:.1f} kPa".format(feed_supported_pressure_kpa),
            "combustion-supported Pc={0:.1f} kPa".format(combustion_pressure_kpa),
            "relaxed Pc={0:.1f} kPa".format(next_pressure_kpa),
        ]
        if minimum_feed_margin_kpa < 0.0:
            notes.append("feed margin negative: {0:.1f} kPa".format(minimum_feed_margin_kpa))
        if minimum_structural_margin_ratio <= 1.0:
            notes.append("structural margin below unity")

        row = {
            "iteration": iteration,
            "chamber_pressure_kpa": round(next_pressure_kpa, 3),
            "fuel_tank_pressure_kpa": _safe_float(feed_summary.get("final_fuel_tank_pressure_kpa"), next_pressure_kpa + 200.0),
            "oxidizer_tank_pressure_kpa": _safe_float(feed_summary.get("final_oxidizer_tank_pressure_kpa"), next_pressure_kpa + 250.0),
            "feed_supported_pressure_kpa": round(feed_supported_pressure_kpa, 3),
            "combustion_supported_pressure_kpa": round(combustion_pressure_kpa, 3),
            "minimum_feed_margin_kpa": round(minimum_feed_margin_kpa, 3),
            "minimum_structural_margin_ratio": round(minimum_structural_margin_ratio, 4),
            "thrust_error_fraction": round(thrust_error_fraction, 6),
            "residual_kpa": round(pressure_residual_kpa, 3),
            "converged": converged,
            "notes": notes,
        }
        trace_rows.append(row)
        if progress_callback is not None:
            progress_callback(
                iteration_base + iteration_span * 0.95,
                "Coupled iteration {0}: residual {1:.2f} kPa, thrust error {2:.2f}%".format(
                    iteration,
                    pressure_residual_kpa,
                    thrust_error_fraction * 100.0,
                ),
            )
        final_info = ConvergenceInfo(
            iteration=iteration,
            chamber_pressure_kpa=next_pressure_kpa,
            fuel_tank_pressure_kpa=float(row["fuel_tank_pressure_kpa"]),
            oxidizer_tank_pressure_kpa=float(row["oxidizer_tank_pressure_kpa"]),
            residual_kpa=pressure_residual_kpa,
            thrust_error_fraction=thrust_error_fraction,
            minimum_feed_margin_kpa=minimum_feed_margin_kpa,
            minimum_structural_margin_ratio=minimum_structural_margin_ratio,
            converged=converged,
            notes=notes,
        )
        pressure_guess_kpa = next_pressure_kpa
        if converged:
            break

    if progress_callback is not None:
        progress_callback(96.0, "Coupled solve: assembling final solver state")
    if isinstance(final_combustion_result, dict) and isinstance(final_structural_result, dict):
        final_combustion_result["structural_result"] = final_structural_result
    if final_design is not None:
        final_info.notes = list(final_info.notes) + [
            "final concept diameter={0:.1f} mm".format(float(final_design.derived.maximum_diameter_mm))
        ]
    return final_info, final_feed_result, final_combustion_result, final_structural_result, trace_rows


def _prepare_initial_design(
    req: Dict[str, object],
    upstream_context: Optional[Dict[str, object]],
    initial_chamber_pressure_kpa: Optional[float],
    initial_design: Optional[object],
) -> Tuple[float, object]:
    state = dict(req)
    if upstream_context:
        state.update({key: value for key, value in upstream_context.items() if key in state})
    concept_design = initial_design or create_concept_design(state)
    if initial_chamber_pressure_kpa is None:
        engineering_values = getattr(concept_design.derived, "engineering_values", {})
        initial_chamber_pressure_kpa = _safe_float(
            state.get("target_chamber_pressure_kpa"),
            _safe_float(engineering_values.get("chamber_pressure_kpa"), 1500.0),
        )
    return float(initial_chamber_pressure_kpa), concept_design


def solve(
    design_request: Dict[str, object],
    upstream_context: Optional[Dict[str, object]] = None,
    initial_chamber_pressure_kpa: Optional[float] = None,
    initial_design: Optional[object] = None,
    convergence_tolerance_kpa: float = 5.0,
    max_iterations: int = 8,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, object]:
    """Solve the coupled feed/combustion/structure cycle."""
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

    try:
        conv_info, feed_result, combustion_result, structural_result, iteration_trace = iterate_coupling_loop(
            req,
            _clamp(initial_chamber_pressure_kpa, 100.0, 10000.0),
            initial_design,
            convergence_tolerance_kpa,
            max_iterations,
            progress_callback,
        )
    except Exception as exc:
        return _error_result(req, "Iteration loop failed: {0}".format(str(exc)), "Coupling loop raised exception")

    merged_station_updates = _merge_station_field_updates(feed_result, combustion_result, structural_result)
    trace_lines = [
        "Stage 3.2 Coupled Cycle Loop Solver",
        "Initial chamber pressure: {0:.1f} kPa".format(initial_chamber_pressure_kpa),
        "Convergence tolerance: {0:.2f} kPa, max iterations: {1}".format(
            convergence_tolerance_kpa, max_iterations
        ),
        "Solved feed transient, combustion/nozzle, and structural margins with relaxed pressure feedback.",
    ]
    warnings: List[str] = []
    if not conv_info.converged:
        warnings.append(
            "Coupled solve stopped without full convergence. Final pressure residual: {0:.2f} kPa, thrust error: {1:.3f}%.".format(
                conv_info.residual_kpa,
                conv_info.thrust_error_fraction * 100.0,
            )
        )
    if isinstance(combustion_result, dict):
        warnings.extend(str(item) for item in combustion_result.get("warnings", []) if item)
    warnings.append("Stage 3.2 remains a reduced-order numerical cycle model, not a validated hardware design solver.")

    return {
        "metadata": _metadata(),
        "status": "ok" if conv_info.converged else "converged-degraded",
        "payload": {
            "request": req,
            "convergence": {
                "iteration_count": conv_info.iteration,
                "converged": conv_info.converged,
                "final_residual_kpa": round(conv_info.residual_kpa, 3),
                "convergence_tolerance_kpa": float(convergence_tolerance_kpa),
                "thrust_error_fraction": round(conv_info.thrust_error_fraction, 6),
                "minimum_feed_margin_kpa": round(conv_info.minimum_feed_margin_kpa, 3),
                "minimum_structural_margin_ratio": round(conv_info.minimum_structural_margin_ratio, 4),
            },
            "results": {
                "chamber_pressure_kpa": round(conv_info.chamber_pressure_kpa, 3),
                "fuel_tank_pressure_kpa": round(conv_info.fuel_tank_pressure_kpa, 3),
                "oxidizer_tank_pressure_kpa": round(conv_info.oxidizer_tank_pressure_kpa, 3),
                "thrust_error_fraction": round(conv_info.thrust_error_fraction, 6),
                "minimum_feed_margin_kpa": round(conv_info.minimum_feed_margin_kpa, 3),
                "minimum_structural_margin_ratio": round(conv_info.minimum_structural_margin_ratio, 4),
            },
            "iteration_trace": iteration_trace,
            "station_field_updates": merged_station_updates,
            "feed_solver_result": feed_result if isinstance(feed_result, dict) else None,
            "combustion_solver_result": combustion_result if isinstance(combustion_result, dict) else None,
            "structural_solver_result": structural_result if isinstance(structural_result, dict) else None,
        },
        "warnings": warnings,
        "trace": trace_lines,
    }
