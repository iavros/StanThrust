"""Coupled cycle loop solver.

Runs one coupled design solve across the design geometry, the feed-transient
model, the chamber/nozzle flow solver, shock feedback, and the structural
material outputs. The goal is not hardware certification; it is a numerically
consistent design state where chamber pressure, feed margin, thrust residual,
shock response, and section margins are solved together rather than reported as
isolated subsystem values.
"""

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from stanthrust.chamber_nozzle_solver import solve_chamber_nozzle_flow
from stanthrust.design_model import create_engine_design
from stanthrust.feed_pressure_drop_solver import solve as solve_feed_system
from stanthrust.hydraulic_chamber_solver import propagate_hydraulic_uncertainty
from stanthrust.inputs import get_default_solver_assumptions, lookup_propellant
from stanthrust.structural_material_solver import assign_materials, build_structural_materials_output

SOLVER_NAME = "Coupled Cycle Loop Solver"
SOLVER_VERSION = "0.3"
FINAL_FLOW_MODEL = "viscous"
FINAL_MIN_STATION_COUNT = 180
SOLVER_MODE = "coupled-cycle-v3"
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
        "input_schema_version": "1.2",
        "output_schema_version": "1.2",
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
    minimum_heat_transfer_margin_ratio: float
    minimum_combined_material_margin_ratio: float
    coolant_required_inlet_pressure_kpa: float
    coolant_pressure_margin_kpa: float
    coolant_pressure_requirement_met: bool
    converged: bool
    notes: List[str]


def validate_inputs(design_request: Dict[str, object]) -> Dict[str, object]:
    """Validate and normalize coupled-cycle solver inputs."""
    req = _as_dict(design_request)
    normalized = {
        "fuel_name": str(req.get("fuel_name", "Ethanol") or "Ethanol"),
        "oxidizer_name": str(req.get("oxidizer_name", "Liquid Oxygen") or "Liquid Oxygen"),
        "injector_type": str(req.get("injector_type", "impinging") or "impinging"),
        "target_thrust_newtons": _safe_float(req.get("target_thrust_newtons"), 500.0),
        "target_impulse_newton_seconds": _safe_float(req.get("target_impulse_newton_seconds"), 3000.0),
        "target_chamber_pressure_kpa": _safe_float(req.get("target_chamber_pressure_kpa"), 1500.0),
        "target_diameter_mm": _safe_float(req.get("target_diameter_mm"), 110.0),
        "tank_diameter_mm": _safe_float(req.get("tank_diameter_mm"), 100.0),
        "chamber_diameter_mm": _safe_float(req.get("chamber_diameter_mm"), 80.0),
        "nozzle_diameter_mm": _safe_float(req.get("nozzle_diameter_mm"), 95.0),
        "nozzle_exit_mode": str(req.get("nozzle_exit_mode", "auto") or "auto"),
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
        "solver_flow_model": str(req.get("solver_flow_model", "viscous") or "viscous"),
        "solver_station_count": _safe_float(req.get("solver_station_count"), 160.0),
        "pressure_solve_mode": str(req.get("pressure_solve_mode", "design") or "design").strip().lower(),
        "combustion_efficiency": _clamp(_safe_float(req.get("combustion_efficiency"), 0.95), 0.50, 1.0),
        "design_injector_dp_ratio": _clamp(_safe_float(req.get("design_injector_dp_ratio"), 0.20), 0.05, 0.50),
        "fuel_injector_discharge_coefficient": _clamp(
            _safe_float(req.get("fuel_injector_discharge_coefficient"), 0.72), 0.20, 1.0
        ),
        "oxidizer_injector_discharge_coefficient": _clamp(
            _safe_float(req.get("oxidizer_injector_discharge_coefficient"), 0.72), 0.20, 1.0
        ),
        "fuel_injector_area_mm2": max(0.0, _safe_float(req.get("fuel_injector_area_mm2"), 0.0)),
        "oxidizer_injector_area_mm2": max(0.0, _safe_float(req.get("oxidizer_injector_area_mm2"), 0.0)),
        "fuel_supply_pressure_kpa": max(0.0, _safe_float(req.get("fuel_supply_pressure_kpa"), 0.0)),
        "oxidizer_supply_pressure_kpa": max(0.0, _safe_float(req.get("oxidizer_supply_pressure_kpa"), 0.0)),
        "fuel_tank_inlet_pressure_kpa": max(101.325, _safe_float(req.get("fuel_tank_inlet_pressure_kpa"), 300.0)),
        "oxidizer_tank_inlet_pressure_kpa": max(
            101.325, _safe_float(req.get("oxidizer_tank_inlet_pressure_kpa"), 330.0)
        ),
        "design_supply_margin_ratio": _clamp(_safe_float(req.get("design_supply_margin_ratio"), 0.08), 0.0, 0.50),
        "regen_coolant_inlet_temperature_k": max(
            0.0, _safe_float(req.get("regen_coolant_inlet_temperature_k"), 0.0)
        ),
        "regen_coolant_inlet_pressure_kpa": max(
            0.0, _safe_float(req.get("regen_coolant_inlet_pressure_kpa"), 0.0)
        ),
        "fuel_minimum_injector_inlet_pressure_kpa": max(
            0.0, _safe_float(req.get("fuel_minimum_injector_inlet_pressure_kpa"), 0.0)
        ),
        "fuel_regen_pressure_drop_kpa": max(
            0.0, _safe_float(req.get("fuel_regen_pressure_drop_kpa"), 0.0)
        ),
        "analysis_throat_diameter_mm": max(0.0, _safe_float(req.get("analysis_throat_diameter_mm"), 0.0)),
        "line_diameter_fuel_m": max(0.001, _safe_float(req.get("line_diameter_fuel_m"), 0.012)),
        "line_diameter_oxidizer_m": max(0.001, _safe_float(req.get("line_diameter_oxidizer_m"), 0.0114)),
        "line_length_fuel_m": max(0.01, _safe_float(req.get("line_length_fuel_m"), 1.55)),
        "line_length_oxidizer_m": max(0.01, _safe_float(req.get("line_length_oxidizer_m"), 1.75)),
        "minor_loss_fuel_k": max(0.0, _safe_float(req.get("minor_loss_fuel_k"), 8.0)),
        "minor_loss_oxidizer_k": max(0.0, _safe_float(req.get("minor_loss_oxidizer_k"), 9.2)),
        "line_roughness_fuel_m": max(0.0, _safe_float(req.get("line_roughness_fuel_m"), 1.5e-6)),
        "line_roughness_oxidizer_m": max(0.0, _safe_float(req.get("line_roughness_oxidizer_m"), 1.5e-6)),
        "uncertainty_sample_count": int(_clamp(_safe_float(req.get("uncertainty_sample_count"), 32), 24, 1024)),
    }
    if normalized["pressure_solve_mode"] not in {"design", "analysis"}:
        normalized["pressure_solve_mode"] = "design"
    return {"normalized_request": normalized}


def _build_feed_request(state: Dict[str, object], chamber_pressure_kpa: float) -> Dict[str, object]:
    fuel = lookup_propellant(str(state["fuel_name"]), "fuel")
    oxidizer = lookup_propellant(str(state["oxidizer_name"]), "oxidizer")
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
            "fuel_record": asdict(fuel),
            "oxidizer_record": asdict(oxidizer),
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
        "feed_pressure_drop_request": {
            "pressure_solve_mode": state["pressure_solve_mode"],
            "design_injector_dp_ratio": state["design_injector_dp_ratio"],
            "fuel_injector_discharge_coefficient": state["fuel_injector_discharge_coefficient"],
            "oxidizer_injector_discharge_coefficient": state["oxidizer_injector_discharge_coefficient"],
            "fuel_injector_area_mm2": state["fuel_injector_area_mm2"],
            "oxidizer_injector_area_mm2": state["oxidizer_injector_area_mm2"],
            "fuel_supply_pressure_kpa": state["fuel_supply_pressure_kpa"],
            "oxidizer_supply_pressure_kpa": state["oxidizer_supply_pressure_kpa"],
            "fuel_tank_inlet_pressure_kpa": state["fuel_tank_inlet_pressure_kpa"],
            "oxidizer_tank_inlet_pressure_kpa": state["oxidizer_tank_inlet_pressure_kpa"],
            "design_supply_margin_ratio": state["design_supply_margin_ratio"],
            "fuel_minimum_injector_inlet_pressure_kpa": state[
                "fuel_minimum_injector_inlet_pressure_kpa"
            ],
            "fuel_regen_pressure_drop_kpa": state["fuel_regen_pressure_drop_kpa"],
            "analysis_throat_diameter_mm": state["analysis_throat_diameter_mm"],
            "line_diameter_fuel_m": state["line_diameter_fuel_m"],
            "line_diameter_oxidizer_m": state["line_diameter_oxidizer_m"],
            "line_length_fuel_m": state["line_length_fuel_m"],
            "line_length_oxidizer_m": state["line_length_oxidizer_m"],
            "minor_loss_fuel_k": state["minor_loss_fuel_k"],
            "minor_loss_oxidizer_k": state["minor_loss_oxidizer_k"],
            "line_roughness_fuel_m": state["line_roughness_fuel_m"],
            "line_roughness_oxidizer_m": state["line_roughness_oxidizer_m"],
        },
    }


def _build_design_for_pressure(state: Dict[str, object], chamber_pressure_kpa: float) -> object:
    design_state = dict(state)
    design_state["target_chamber_pressure_kpa"] = chamber_pressure_kpa
    design = create_engine_design(design_state)
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


def _run_combustion(
    state: Dict[str, object],
    design: object,
    chamber_pressure_kpa: float,
    feed_summary: Dict[str, object],
    max_iterations: int,
    fixed_chamber_pressure_kpa: Optional[float] = None,
) -> Dict[str, object]:
    assumptions = get_default_solver_assumptions()
    flow_model = str(state.get("solver_flow_model", "viscous")).strip().lower()
    if flow_model == "navier_stokes":
        flow_model = "viscous"
    if flow_model not in {"fast", "refined", "viscous"}:
        flow_model = "viscous"
    assumptions = assumptions.__class__(
        **{
            **assumptions.__dict__,
            "flow_model": flow_model,
            "combustion_efficiency": float(state["combustion_efficiency"]),
            "max_iterations": max(3, min(200, max_iterations * 8)),
        }
    )
    station_count = int(_clamp(_safe_float(state.get("solver_station_count"), 160.0), 24.0, 240.0))
    return solve_chamber_nozzle_flow(
        design,
        assumptions,
        station_count=station_count,
        max_iterations_override=max(3, min(200, max_iterations * 8)),
        thermochemistry_mode="auto",
        fixed_chamber_pressure_kpa=fixed_chamber_pressure_kpa,
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


def _material_margin_summary(structural_result: Optional[Dict[str, object]], design: object) -> Dict[str, float]:
    stress_margin = _minimum_structural_margin(structural_result, design)
    summary = _as_dict(_as_dict(structural_result.get("payload") if isinstance(structural_result, dict) else {}).get("summary"))
    heat_margin = _safe_float(summary.get("minimum_heat_transfer_margin_ratio"), stress_margin)
    combined_margin = _safe_float(summary.get("minimum_combined_margin_ratio"), min(stress_margin, heat_margin))
    return {
        "minimum_structural_margin_ratio": stress_margin,
        "minimum_heat_transfer_margin_ratio": heat_margin,
        "minimum_combined_material_margin_ratio": combined_margin,
    }


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


def _bound_row(
    name: str,
    value: float,
    unit: str,
    lower_percent: float,
    upper_percent: float,
    basis: str,
) -> Dict[str, object]:
    lower = value * (1.0 - lower_percent / 100.0)
    upper = value * (1.0 + upper_percent / 100.0)
    return {
        "name": name,
        "value": round(value, 6),
        "unit": unit,
        "lower": round(lower, 6),
        "upper": round(upper, 6),
        "lower_percent": round(lower_percent, 3),
        "upper_percent": round(upper_percent, 3),
        "basis": basis,
    }


def _sampled_bound_row(
    name: str,
    value: float,
    unit: str,
    interval: Dict[str, object],
    basis: str,
) -> Dict[str, object]:
    lower = min(value, _safe_float(interval.get("p05"), value))
    upper = max(value, _safe_float(interval.get("p95"), value))
    return {
        "name": name,
        "value": round(value, 6),
        "unit": unit,
        "lower": round(lower, 6),
        "upper": round(upper, 6),
        "lower_percent": round(100.0 * max(0.0, value - lower) / max(1e-12, abs(value)), 3),
        "upper_percent": round(100.0 * max(0.0, upper - value) / max(1e-12, abs(value)), 3),
        "basis": basis,
        "interval_method": "P05-P95 input propagation",
    }


def _build_final_uncertainty_bounds(
    conv_info: ConvergenceInfo,
    combustion_result: Optional[Dict[str, object]],
    structural_result: Optional[Dict[str, object]],
    hydraulic_uncertainty: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    combustion_summary = _as_dict(_as_dict(combustion_result).get("summary"))
    combustion_metadata = _as_dict(_as_dict(combustion_result).get("metadata"))
    viscous_correction = _as_dict(_as_dict(combustion_result).get("viscous_correction"))
    viscous_summary = _as_dict(viscous_correction.get("summary"))

    chamber_pressure_kpa = max(1.0, _safe_float(combustion_summary.get("chamber_pressure_kpa"), conv_info.chamber_pressure_kpa))
    residual_percent = 100.0 * conv_info.residual_kpa / chamber_pressure_kpa
    thrust_error_percent = 100.0 * conv_info.thrust_error_fraction
    shock_feedback = _as_dict(_as_dict(combustion_result).get("shock_design_feedback"))
    shock_penalty = 2.0 if bool(shock_feedback.get("influences_design")) else 0.0
    residual_penalty = min(4.0, residual_percent * 0.25)
    thrust_penalty = min(4.0, thrust_error_percent * 0.35)
    viscous_pressure_loss_kpa = max(
        0.0, _safe_float(viscous_summary.get("cumulative_pressure_loss_kpa"), 0.0)
    )
    viscous_pressure_loss_fraction = viscous_pressure_loss_kpa / chamber_pressure_kpa
    viscous_penalty = min(2.0, viscous_pressure_loss_fraction * 100.0)

    solver_basis = "Cantera thermochemistry, MOC nozzle contour, shock feedback, viscous quasi-1D correction"
    common_penalty = residual_penalty + thrust_penalty + shock_penalty + viscous_penalty
    interval_rows = {
        str(row.get("name")): _as_dict(row)
        for row in _as_dict(hydraulic_uncertainty).get("intervals", [])
        if isinstance(row, dict)
    }
    pressure_interval = interval_rows.get("chamber_pressure_kpa")
    thrust_interval = interval_rows.get("predicted_thrust_newtons")
    flow_interval = interval_rows.get("total_mass_flow_kg_s")
    pressure_row = (
        _sampled_bound_row(
            "chamber_pressure_kpa",
            chamber_pressure_kpa,
            "kPa",
            pressure_interval,
            "Hydraulic input propagation through chamber mass balance",
        )
        if pressure_interval
        else _bound_row(
            "chamber_pressure_kpa",
            chamber_pressure_kpa,
            "kPa",
            2.0 + common_penalty,
            3.0 + common_penalty,
            "Coupled pressure residual and final flow solve",
        )
    )
    thrust_value = _safe_float(combustion_summary.get("predicted_thrust_newtons"), 0.0)
    thrust_row = (
        _sampled_bound_row(
            "predicted_thrust_newtons",
            thrust_value,
            "N",
            thrust_interval,
            "Hydraulic inputs, throat tolerance, and thrust-coefficient range",
        )
        if thrust_interval
        else _bound_row(
            "predicted_thrust_newtons",
            thrust_value,
            "N",
            3.0 + common_penalty,
            4.0 + common_penalty,
            "Pressure-root thrust balance, nozzle loss model, and shock feedback",
        )
    )
    hydraulic_nominal = _as_dict(_as_dict(hydraulic_uncertainty).get("nominal"))
    flow_value = _safe_float(
        hydraulic_nominal.get("total_mass_flow_kg_s"),
        _safe_float(combustion_summary.get("mass_flow_kg_s"), 0.0),
    )
    flow_row = (
        _sampled_bound_row(
            "mass_flow_kg_s",
            flow_value,
            "kg/s",
            flow_interval,
            "Hydraulic input propagation and Cantera c-star efficiency range",
        )
        if flow_interval
        else _bound_row(
            "mass_flow_kg_s",
            flow_value,
            "kg/s",
            3.0 + residual_penalty,
            4.0 + residual_penalty,
            "Cantera c-star and solved throat area",
        )
    )
    isp_value = _safe_float(combustion_summary.get("predicted_isp_seconds"), 0.0)
    if thrust_interval and flow_interval and isp_value > 0.0:
        isp_interval = {
            "p05": _safe_float(thrust_interval.get("p05"), thrust_value)
            / max(1e-12, _safe_float(flow_interval.get("p95"), flow_value) * 9.80665),
            "p95": _safe_float(thrust_interval.get("p95"), thrust_value)
            / max(1e-12, _safe_float(flow_interval.get("p05"), flow_value) * 9.80665),
        }
        isp_row = _sampled_bound_row(
            "predicted_isp_seconds",
            isp_value,
            "s",
            isp_interval,
            "Conservative thrust and mass-flow P05/P95 combination",
        )
    else:
        isp_row = _bound_row(
            "predicted_isp_seconds",
            isp_value,
            "s",
            4.0 + common_penalty,
            5.0 + common_penalty,
            "Solved thrust divided by propellant mass flow",
        )
    fields = [
        pressure_row,
        thrust_row,
        flow_row,
        isp_row,
        _bound_row(
            "max_hot_wall_temperature_k",
            _safe_float(combustion_summary.get("max_hot_wall_temperature_k"), 0.0),
            "K",
            6.0 + viscous_penalty,
            10.0 + viscous_penalty,
            "Heat-transfer station solution and wall model",
        ),
        _bound_row(
            "minimum_combined_material_margin_ratio",
            conv_info.minimum_combined_material_margin_ratio,
            "x",
            8.0 + viscous_penalty,
            12.0 + viscous_penalty,
            "Stress and heat-transfer material evaluation",
        ),
    ]

    return {
        "basis": solver_basis,
        "flow_model": combustion_metadata.get("flow_model", "viscous"),
        "station_count": combustion_metadata.get("station_count"),
        "convergence_residual_kpa": round(conv_info.residual_kpa, 6),
        "thrust_error_fraction": round(conv_info.thrust_error_fraction, 8),
        "viscous_pressure_loss_kpa": round(viscous_pressure_loss_kpa, 8),
        "viscous_pressure_loss_fraction": round(viscous_pressure_loss_fraction, 8),
        "bounds": fields,
        "hydraulic_input_propagation": hydraulic_uncertainty,
        "notes": [
            "Pressure, mass-flow, thrust, and specific-impulse bounds use explicit hydraulic input propagation when available.",
            "Thermal and material bounds remain engineering screening bands pending the axial thermal milestone.",
            "They are not a substitute for external CFD validation or engine hot-fire data.",
        ],
    }


def iterate_coupling_loop(
    design_request: Dict[str, object],
    initial_chamber_pressure_kpa: float,
    initial_design: Optional[object] = None,
    convergence_tolerance_kpa: float = 5.0,
    max_iterations: int = 8,
    progress_callback: Optional[ProgressCallback] = None,
    minimum_iterations: int = 3,
) -> Tuple[ConvergenceInfo, Optional[Dict[str, object]], Optional[Dict[str, object]], Optional[Dict[str, object]], List[Dict[str, object]]]:
    """Run the relaxed coupled feed/combustion/structure numerical loop."""
    state = dict(design_request)
    pressure_iteration_kpa = _clamp(initial_chamber_pressure_kpa, 100.0, 10000.0)
    relaxation = 0.42
    iteration_limit = max(1, max_iterations)
    minimum_required_iterations = max(1, min(iteration_limit, minimum_iterations))

    final_feed_result = None
    final_combustion_result = None
    final_structural_result = None
    final_design = initial_design
    trace_rows: List[Dict[str, object]] = []
    final_info = ConvergenceInfo(
        iteration=0,
        chamber_pressure_kpa=pressure_iteration_kpa,
        fuel_tank_pressure_kpa=pressure_iteration_kpa + 200.0,
        oxidizer_tank_pressure_kpa=pressure_iteration_kpa + 250.0,
        residual_kpa=pressure_iteration_kpa,
        thrust_error_fraction=1.0,
        minimum_feed_margin_kpa=0.0,
        minimum_structural_margin_ratio=0.0,
        minimum_heat_transfer_margin_ratio=0.0,
        minimum_combined_material_margin_ratio=0.0,
        coolant_required_inlet_pressure_kpa=0.0,
        coolant_pressure_margin_kpa=0.0,
        coolant_pressure_requirement_met=True,
        converged=False,
        notes=[],
    )

    for iteration in range(1, iteration_limit + 1):
        iteration_base = 8.0 + 84.0 * (iteration - 1) / iteration_limit
        iteration_span = 84.0 / iteration_limit
        if progress_callback is not None:
            progress_callback(iteration_base, "Coupled iteration {0}: preparing design state".format(iteration))
        design = _build_design_for_pressure(state, pressure_iteration_kpa)
        final_design = design
        if progress_callback is not None:
            progress_callback(iteration_base + iteration_span * 0.18, "Coupled iteration {0}: solving Cantera chamber state".format(iteration))
        combustion_result = _run_combustion(
            state,
            design,
            pressure_iteration_kpa,
            {},
            max_iterations,
            fixed_chamber_pressure_kpa=pressure_iteration_kpa,
        )
        final_combustion_result = combustion_result
        combustion_summary = _as_dict(combustion_result.get("summary"))
        heat_transfer = _as_dict(combustion_result.get("heat_transfer"))
        heat_summary = _as_dict(heat_transfer.get("summary"))
        coolant_required_inlet_pressure_kpa = _safe_float(
            heat_summary.get("coolant_required_inlet_pressure_kpa"), 0.0
        )
        coolant_minimum_injector_inlet_kpa = _safe_float(
            heat_summary.get("coolant_minimum_single_phase_pressure_kpa"), 0.0
        )
        coolant_regen_pressure_drop_kpa = _safe_float(
            heat_summary.get("coolant_pressure_drop_kpa"), 0.0
        )
        coolant_pressure_margin_kpa = _safe_float(
            heat_summary.get("coolant_pressure_margin_kpa"), 0.0
        )
        coolant_pressure_requirement_met = bool(
            heat_summary.get("coolant_pressure_requirement_met", True)
        )
        coolant_redesign_active = False
        if bool(state.get("regen_cooling")) and str(state.get("pressure_solve_mode")) == "design":
            previous_inlet_kpa = _safe_float(state.get("regen_coolant_inlet_pressure_kpa"), 0.0)
            previous_minimum_kpa = _safe_float(
                state.get("fuel_minimum_injector_inlet_pressure_kpa"), 0.0
            )
            previous_drop_kpa = _safe_float(state.get("fuel_regen_pressure_drop_kpa"), 0.0)
            state["regen_coolant_inlet_pressure_kpa"] = coolant_required_inlet_pressure_kpa
            state["fuel_minimum_injector_inlet_pressure_kpa"] = coolant_minimum_injector_inlet_kpa
            state["fuel_regen_pressure_drop_kpa"] = coolant_regen_pressure_drop_kpa
            coolant_redesign_active = any(
                abs(current - previous) > 0.05
                for current, previous in (
                    (coolant_required_inlet_pressure_kpa, previous_inlet_kpa),
                    (coolant_minimum_injector_inlet_kpa, previous_minimum_kpa),
                    (coolant_regen_pressure_drop_kpa, previous_drop_kpa),
                )
            )
        feed_request = _build_feed_request(state, pressure_iteration_kpa)
        if progress_callback is not None:
            progress_callback(
                iteration_base + iteration_span * 0.44,
                "Coupled iteration {0}: closing feed, injector, and chamber mass balance".format(iteration),
            )
        feed_result = solve_feed_system(
            feed_request,
            upstream_context={
                "source": "coupled-cycle-loop",
                "iteration": iteration,
                "target_chamber_pressure_kpa": pressure_iteration_kpa,
                "chamber_pressure_kpa": pressure_iteration_kpa,
                "cstar_m_s": combustion_summary.get("cstar_m_s"),
                "throat_area_m2": combustion_summary.get("throat_area_m2"),
                "propellant_mass_flow_kg_s": combustion_summary.get("mass_flow_kg_s"),
            },
        )
        final_feed_result = feed_result
        feed_payload = _as_dict(feed_result.get("payload"))
        feed_summary = _as_dict(feed_payload.get("summary"))
        hydraulic_closure = _as_dict(feed_payload.get("hydraulic_closure"))
        shock_feedback = _as_dict(combustion_result.get("shock_design_feedback"))
        shock_redesign_active = False
        if bool(shock_feedback.get("influences_design")) and str(state.get("nozzle_exit_mode", "auto")) == "auto":
            recommended_exit_mm = _safe_float(shock_feedback.get("recommended_exit_diameter_mm"), 0.0)
            current_exit_mm = _safe_float(
                getattr(design.derived, "engineering_values", {}).get("nozzle_inner_diameter_mm"),
                _safe_float(state.get("nozzle_diameter_mm"), 0.0),
            )
            if recommended_exit_mm > 0.0 and recommended_exit_mm < current_exit_mm - 0.05:
                state["nozzle_diameter_mm"] = recommended_exit_mm
                state["nozzle_exit_mode"] = "manual"
                shock_redesign_active = True
        if progress_callback is not None:
            progress_callback(iteration_base + iteration_span * 0.72, "Coupled iteration {0}: checking structural margins".format(iteration))
        structural_result = _run_structural(state, combustion_result)
        final_structural_result = structural_result

        combustion_pressure_kpa = _extract_combustion_pressure(combustion_result, pressure_iteration_kpa)
        feed_supported_pressure_kpa = _safe_float(
            hydraulic_closure.get("chamber_pressure_kpa"),
            _safe_float(feed_summary.get("chamber_pressure_kpa"), pressure_iteration_kpa),
        )
        minimum_feed_margin_kpa = _safe_float(feed_summary.get("minimum_feed_margin_kpa"), 0.0)
        physics_target_kpa = feed_supported_pressure_kpa
        next_pressure_kpa = (1.0 - relaxation) * pressure_iteration_kpa + relaxation * physics_target_kpa
        next_pressure_kpa = _clamp(next_pressure_kpa, 100.0, 10000.0)

        pressure_residual_kpa = abs(next_pressure_kpa - pressure_iteration_kpa)
        thrust_error_fraction = _extract_thrust_error(combustion_result)
        material_margins = _material_margin_summary(structural_result, design)
        minimum_structural_margin_ratio = material_margins["minimum_structural_margin_ratio"]
        minimum_heat_transfer_margin_ratio = material_margins["minimum_heat_transfer_margin_ratio"]
        minimum_combined_material_margin_ratio = material_margins["minimum_combined_material_margin_ratio"]
        pressure_mode = str(state.get("pressure_solve_mode", "design"))
        thrust_requirement_met = pressure_mode == "analysis" or thrust_error_fraction <= 0.035
        hydraulic_converged = bool(hydraulic_closure.get("converged", False))
        hydraulic_residual = abs(_safe_float(hydraulic_closure.get("mass_balance_relative_error"), 1.0))
        criteria_met = (
            pressure_residual_kpa <= convergence_tolerance_kpa
            and thrust_requirement_met
            and hydraulic_converged
            and hydraulic_residual <= 1e-5
            and minimum_structural_margin_ratio > 1.0
            and minimum_combined_material_margin_ratio > 1.0
            and not shock_redesign_active
            and coolant_pressure_requirement_met
            and not coolant_redesign_active
        )
        converged = criteria_met and iteration >= minimum_required_iterations
        notes = [
            "feed-supported Pc={0:.1f} kPa".format(feed_supported_pressure_kpa),
            "Cantera state evaluated at Pc={0:.1f} kPa".format(combustion_pressure_kpa),
            "relaxed Pc={0:.1f} kPa".format(next_pressure_kpa),
            "hydraulic mass residual={0:.3e}".format(hydraulic_residual),
        ]
        if minimum_feed_margin_kpa < 0.0:
            notes.append("feed margin negative: {0:.1f} kPa".format(minimum_feed_margin_kpa))
        if minimum_structural_margin_ratio <= 1.0:
            notes.append("structural margin below unity")
        if minimum_heat_transfer_margin_ratio <= 1.0:
            notes.append("heat-transfer material margin below unity")
        if shock_redesign_active:
            notes.append(
                "shock feedback resized next nozzle exit to {0:.2f} mm".format(
                    _safe_float(state.get("nozzle_diameter_mm"), 0.0)
                )
            )
        if coolant_redesign_active:
            notes.append(
                "coolant pressure feedback set fuel jacket inlet to {0:.1f} kPa".format(
                    coolant_required_inlet_pressure_kpa
                )
            )
        if not coolant_pressure_requirement_met:
            notes.append(
                "coolant pressure margin negative: {0:.1f} kPa".format(
                    coolant_pressure_margin_kpa
                )
            )

        row = {
            "iteration": iteration,
            "chamber_pressure_kpa": round(next_pressure_kpa, 3),
            "fuel_tank_pressure_kpa": _safe_float(feed_summary.get("final_fuel_tank_pressure_kpa"), next_pressure_kpa + 200.0),
            "oxidizer_tank_pressure_kpa": _safe_float(feed_summary.get("final_oxidizer_tank_pressure_kpa"), next_pressure_kpa + 250.0),
            "feed_supported_pressure_kpa": round(feed_supported_pressure_kpa, 3),
            "combustion_supported_pressure_kpa": round(combustion_pressure_kpa, 3),
            "minimum_feed_margin_kpa": round(minimum_feed_margin_kpa, 3),
            "minimum_structural_margin_ratio": round(minimum_structural_margin_ratio, 4),
            "minimum_heat_transfer_margin_ratio": round(minimum_heat_transfer_margin_ratio, 4),
            "minimum_combined_material_margin_ratio": round(minimum_combined_material_margin_ratio, 4),
            "thrust_error_fraction": round(thrust_error_fraction, 6),
            "residual_kpa": round(pressure_residual_kpa, 3),
            "criteria_met": criteria_met,
            "converged": converged,
            "shock_redesign_active": shock_redesign_active,
            "coolant_redesign_active": coolant_redesign_active,
            "coolant_required_inlet_pressure_kpa": round(
                coolant_required_inlet_pressure_kpa, 3
            ),
            "coolant_pressure_margin_kpa": round(coolant_pressure_margin_kpa, 3),
            "coolant_pressure_requirement_met": coolant_pressure_requirement_met,
            "hydraulic_converged": hydraulic_converged,
            "hydraulic_mass_balance_relative_error": round(hydraulic_residual, 10),
            "pressure_solve_mode": pressure_mode,
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
            minimum_heat_transfer_margin_ratio=minimum_heat_transfer_margin_ratio,
            minimum_combined_material_margin_ratio=minimum_combined_material_margin_ratio,
            coolant_required_inlet_pressure_kpa=coolant_required_inlet_pressure_kpa,
            coolant_pressure_margin_kpa=coolant_pressure_margin_kpa,
            coolant_pressure_requirement_met=coolant_pressure_requirement_met,
            converged=converged,
            notes=notes,
        )
        pressure_iteration_kpa = next_pressure_kpa
        if converged:
            break

    if progress_callback is not None:
        progress_callback(96.0, "Coupled solve: assembling final solver state")
    if isinstance(final_combustion_result, dict) and isinstance(final_structural_result, dict):
        final_combustion_result["structural_result"] = final_structural_result
    if final_design is not None:
        final_info.notes = list(final_info.notes) + [
            "final design diameter={0:.1f} mm".format(float(final_design.derived.maximum_diameter_mm))
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
    base_design = initial_design or create_engine_design(state)
    if initial_chamber_pressure_kpa is None:
        engineering_values = getattr(base_design.derived, "engineering_values", {})
        initial_chamber_pressure_kpa = _safe_float(
            state.get("target_chamber_pressure_kpa"),
            _safe_float(engineering_values.get("chamber_pressure_kpa"), 1500.0),
        )
    return float(initial_chamber_pressure_kpa), base_design


def solve(
    design_request: Dict[str, object],
    upstream_context: Optional[Dict[str, object]] = None,
    initial_chamber_pressure_kpa: Optional[float] = None,
    initial_design: Optional[object] = None,
    convergence_tolerance_kpa: float = 5.0,
    max_iterations: int = 8,
    progress_callback: Optional[ProgressCallback] = None,
    minimum_iterations: int = 3,
) -> Dict[str, object]:
    """Solve the coupled feed/combustion/structure cycle."""
    validation = validate_inputs(design_request)
    req = _as_dict(validation.get("normalized_request"))
    requested_flow_model = str(req.get("solver_flow_model", FINAL_FLOW_MODEL) or FINAL_FLOW_MODEL)
    req["requested_solver_flow_model"] = requested_flow_model
    req["solver_flow_model"] = FINAL_FLOW_MODEL
    req["solver_station_count"] = int(
        max(FINAL_MIN_STATION_COUNT, _safe_float(req.get("solver_station_count"), FINAL_MIN_STATION_COUNT))
    )
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
            minimum_iterations,
        )
    except Exception as exc:
        return _error_result(req, "Iteration loop failed: {0}".format(str(exc)), "Coupling loop raised exception")

    hydraulic_uncertainty: Optional[Dict[str, object]] = None
    uncertainty_warning = ""
    try:
        feed_payload = _as_dict(_as_dict(feed_result).get("payload"))
        hydraulic_inputs = _as_dict(feed_payload.get("hydraulic_inputs"))
        combustion_summary = _as_dict(_as_dict(combustion_result).get("summary"))
        if hydraulic_inputs:
            combustion_physics = _as_dict(_as_dict(combustion_result).get("physics"))
            combustion_coefficients = _as_dict(combustion_physics.get("coefficients"))
            hydraulic_uncertainty = propagate_hydraulic_uncertainty(
                hydraulic_inputs,
                sample_count=int(req["uncertainty_sample_count"]),
                thrust_coefficient=_safe_float(
                    combustion_coefficients.get("effective_thrust_coefficient"),
                    _safe_float(combustion_summary.get("thrust_coefficient"), 0.0),
                ),
            )
    except Exception as exc:
        uncertainty_warning = "Hydraulic uncertainty propagation failed: {0}".format(str(exc))

    merged_station_updates = _merge_station_field_updates(feed_result, combustion_result, structural_result)
    trace_lines = [
        SOLVER_NAME,
        "Initial chamber pressure: {0:.1f} kPa".format(initial_chamber_pressure_kpa),
        "Convergence tolerance: {0:.2f} kPa, max iterations: {1}".format(
            convergence_tolerance_kpa, max_iterations
        ),
        "Minimum coupled iterations before stopping: {0}".format(max(1, min(max(1, max_iterations), minimum_iterations))),
        "Solved feed transient, chamber/nozzle pressure root, shock design feedback, and structural margins together.",
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
    if uncertainty_warning:
        warnings.append(uncertainty_warning)
    warnings.append(
        "This is a coupled design solver. External CFD and hot-fire test validation "
        "are still required before hardware release."
    )
    final_uncertainty_bounds = _build_final_uncertainty_bounds(
        conv_info,
        combustion_result,
        structural_result,
        hydraulic_uncertainty,
    )

    return {
        "metadata": _metadata(),
        "status": "ok" if conv_info.converged else "converged-degraded",
        "payload": {
            "request": req,
            "convergence": {
                "iteration_count": conv_info.iteration,
                "minimum_iteration_count": max(1, min(max(1, max_iterations), minimum_iterations)),
                "converged": conv_info.converged,
                "final_residual_kpa": round(conv_info.residual_kpa, 3),
                "convergence_tolerance_kpa": float(convergence_tolerance_kpa),
                "thrust_error_fraction": round(conv_info.thrust_error_fraction, 6),
                "minimum_feed_margin_kpa": round(conv_info.minimum_feed_margin_kpa, 3),
                "minimum_structural_margin_ratio": round(conv_info.minimum_structural_margin_ratio, 4),
                "minimum_heat_transfer_margin_ratio": round(conv_info.minimum_heat_transfer_margin_ratio, 4),
                "minimum_combined_material_margin_ratio": round(conv_info.minimum_combined_material_margin_ratio, 4),
                "coolant_required_inlet_pressure_kpa": round(
                    conv_info.coolant_required_inlet_pressure_kpa, 3
                ),
                "coolant_pressure_margin_kpa": round(conv_info.coolant_pressure_margin_kpa, 3),
                "coolant_pressure_requirement_met": conv_info.coolant_pressure_requirement_met,
            },
            "results": {
                "chamber_pressure_kpa": round(conv_info.chamber_pressure_kpa, 3),
                "fuel_tank_pressure_kpa": round(conv_info.fuel_tank_pressure_kpa, 3),
                "oxidizer_tank_pressure_kpa": round(conv_info.oxidizer_tank_pressure_kpa, 3),
                "thrust_error_fraction": round(conv_info.thrust_error_fraction, 6),
                "minimum_feed_margin_kpa": round(conv_info.minimum_feed_margin_kpa, 3),
                "minimum_structural_margin_ratio": round(conv_info.minimum_structural_margin_ratio, 4),
                "minimum_heat_transfer_margin_ratio": round(conv_info.minimum_heat_transfer_margin_ratio, 4),
                "minimum_combined_material_margin_ratio": round(conv_info.minimum_combined_material_margin_ratio, 4),
                "coolant_required_inlet_pressure_kpa": round(
                    conv_info.coolant_required_inlet_pressure_kpa, 3
                ),
                "coolant_pressure_margin_kpa": round(conv_info.coolant_pressure_margin_kpa, 3),
            },
            "iteration_trace": iteration_trace,
            "final_uncertainty_bounds": final_uncertainty_bounds,
            "hydraulic_uncertainty": hydraulic_uncertainty,
            "station_field_updates": merged_station_updates,
            "feed_solver_result": feed_result if isinstance(feed_result, dict) else None,
            "combustion_solver_result": combustion_result if isinstance(combustion_result, dict) else None,
            "structural_solver_result": structural_result if isinstance(structural_result, dict) else None,
        },
        "warnings": warnings,
        "trace": trace_lines,
    }
