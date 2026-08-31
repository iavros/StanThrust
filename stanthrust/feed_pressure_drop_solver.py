"""Feed-system pressure drop and burn-history transient solve."""

import math
from typing import Any, Dict, List, Optional

from stanthrust.hydraulic_chamber_solver import (
    AMBIENT_PRESSURE_KPA,
    solve_hydraulic_chamber,
)

SOLVER_NAME = "Feed Pressure-Drop Solver"
SOLVER_VERSION = "0.4"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _default_dynamic_viscosity_pa_s(propellant_name: str, density_kg_m3: float, role: str) -> float:
    name = str(propellant_name or "").strip().lower()
    if "liquid oxygen" in name or name == "lox":
        return 0.0002
    if "nitrous" in name:
        return 0.00018
    if "peroxide" in name:
        return 0.00125
    if "ethanol" in name:
        return 0.0012
    if "isopropyl" in name or "ipa" in name:
        return 0.0022
    if "methane" in name:
        return 0.00012
    if role == "oxidizer":
        return 0.00025 if density_kg_m3 < 950.0 else 0.0009
    return 0.0011 if density_kg_m3 > 650.0 else 0.0004


def _build_time_grid(burn_time_s: float, history_steps: int) -> List[float]:
    if history_steps <= 1:
        return [0.0, burn_time_s]
    return [burn_time_s * index / float(history_steps - 1) for index in range(history_steps)]


def _pressure_fed_tank_pressure_kpa(
    initial_pressure_kpa: float,
    depletion_fraction: float,
    liquid_fill_fraction: float,
    pressurant_polytropic_index: float,
) -> float:
    fill_fraction = _clamp(liquid_fill_fraction, 0.35, 0.82)
    initial_gas_fraction = max(1e-4, 1.0 - fill_fraction)
    gas_volume_ratio = 1.0 + depletion_fraction * fill_fraction / initial_gas_fraction
    blowdown_factor = gas_volume_ratio ** max(0.9, pressurant_polytropic_index)
    return AMBIENT_PRESSURE_KPA + (initial_pressure_kpa - AMBIENT_PRESSURE_KPA) / max(1.0, blowdown_factor)


def _pump_inlet_tank_pressure_kpa(
    initial_pressure_kpa: float,
    depletion_fraction: float,
    decay_fraction: float,
) -> float:
    decay = max(0.0, decay_fraction) * (depletion_fraction ** 1.15)
    return AMBIENT_PRESSURE_KPA + (initial_pressure_kpa - AMBIENT_PRESSURE_KPA) * max(0.0, 1.0 - decay)


def validate_inputs(design_request: Dict[str, object]) -> Dict[str, object]:
    targets = _as_dict(design_request.get("targets"))
    architecture = _as_dict(design_request.get("architecture"))
    geometry_limits = _as_dict(design_request.get("geometry_limits"))
    propellants = _as_dict(design_request.get("propellants"))
    feed_request = _as_dict(design_request.get("feed_pressure_drop_request"))
    fuel_record = _as_dict(propellants.get("fuel_record"))
    oxidizer_record = _as_dict(propellants.get("oxidizer_record"))
    fuel_name = str(propellants.get("fuel", "Fuel") or "Fuel")
    oxidizer_name = str(propellants.get("oxidizer", "Oxidizer") or "Oxidizer")

    tank_diameter_mm = _safe_float(geometry_limits.get("tank_diameter_mm"), 110.0)
    base_line_diameter_m = _clamp(tank_diameter_mm / 1000.0 * 0.11, 0.008, 0.028)

    use_pumps = bool(architecture.get("use_pumps", False))
    history_steps_default = 31 if use_pumps else 41
    initial_fill_fraction_default = 0.72 if use_pumps else 0.58
    fuel_density_kg_m3 = _clamp(_safe_float(fuel_record.get("density_index"), 0.75) * 1000.0, 450.0, 1250.0)
    oxidizer_density_kg_m3 = _clamp(_safe_float(oxidizer_record.get("density_index"), 0.9) * 1000.0, 650.0, 1600.0)

    normalized = {
        "target_thrust_newtons": _safe_float(targets.get("target_thrust_newtons"), 250.0),
        "target_chamber_pressure_kpa": _safe_float(
            targets.get("target_chamber_pressure_kpa", design_request.get("target_chamber_pressure_kpa")),
            0.0,
        ),
        "burn_time_seconds": _safe_float(targets.get("burn_time_seconds"), 12.0),
        "mixture_ratio": _safe_float(propellants.get("mixture_ratio"), 1.4),
        "use_pumps": use_pumps,
        "regen_cooling": bool(architecture.get("regen_cooling", False)),
        "injector_type": str(architecture.get("injector_type", "impinging") or "impinging"),
        "tank_diameter_mm": tank_diameter_mm,
        "fuel_name": fuel_name,
        "oxidizer_name": oxidizer_name,
        "fuel_density_kg_m3": fuel_density_kg_m3,
        "oxidizer_density_kg_m3": oxidizer_density_kg_m3,
        "fuel_dynamic_viscosity_pa_s": _safe_float(
            feed_request.get("fuel_dynamic_viscosity_pa_s"),
            _default_dynamic_viscosity_pa_s(fuel_name, fuel_density_kg_m3, "fuel"),
        ),
        "oxidizer_dynamic_viscosity_pa_s": _safe_float(
            feed_request.get("oxidizer_dynamic_viscosity_pa_s"),
            _default_dynamic_viscosity_pa_s(oxidizer_name, oxidizer_density_kg_m3, "oxidizer"),
        ),
        "line_roughness_fuel_m": _clamp(_safe_float(feed_request.get("line_roughness_fuel_m"), 1.5e-6), 0.0, 3.0e-4),
        "line_roughness_oxidizer_m": _clamp(
            _safe_float(feed_request.get("line_roughness_oxidizer_m"), 1.5e-6),
            0.0,
            3.0e-4,
        ),
        "line_diameter_fuel_m": _safe_float(feed_request.get("line_diameter_fuel_m"), base_line_diameter_m),
        "line_diameter_oxidizer_m": _safe_float(feed_request.get("line_diameter_oxidizer_m"), base_line_diameter_m * 0.95),
        "line_length_fuel_m": _safe_float(feed_request.get("line_length_fuel_m"), 1.55),
        "line_length_oxidizer_m": _safe_float(feed_request.get("line_length_oxidizer_m"), 1.75),
        "minor_loss_fuel_k": _safe_float(feed_request.get("minor_loss_fuel_k"), 8.0),
        "minor_loss_oxidizer_k": _safe_float(feed_request.get("minor_loss_oxidizer_k"), 9.2),
        "history_steps": _clamp(_safe_int(feed_request.get("history_steps"), history_steps_default), 11, 121),
        "initial_fill_fraction": _clamp(
            _safe_float(feed_request.get("initial_fill_fraction"), initial_fill_fraction_default),
            0.35,
            0.82,
        ),
        "pressurant_polytropic_index": _clamp(
            _safe_float(feed_request.get("pressurant_polytropic_index"), 1.08),
            0.95,
            1.35,
        ),
        "pump_inlet_pressure_decay_fraction": _clamp(
            _safe_float(feed_request.get("pump_inlet_pressure_decay_fraction"), 0.09),
            0.02,
            0.25,
        ),
        "pressure_solve_mode": str(feed_request.get("pressure_solve_mode", "design") or "design").strip().lower(),
        "design_injector_dp_ratio": _clamp(
            _safe_float(feed_request.get("design_injector_dp_ratio"), 0.20), 0.05, 0.50
        ),
        "fuel_injector_discharge_coefficient": _clamp(
            _safe_float(feed_request.get("fuel_injector_discharge_coefficient"), 0.72), 0.20, 1.0
        ),
        "oxidizer_injector_discharge_coefficient": _clamp(
            _safe_float(feed_request.get("oxidizer_injector_discharge_coefficient"), 0.72), 0.20, 1.0
        ),
        "fuel_injector_area_mm2": max(0.0, _safe_float(feed_request.get("fuel_injector_area_mm2"), 0.0)),
        "oxidizer_injector_area_mm2": max(
            0.0, _safe_float(feed_request.get("oxidizer_injector_area_mm2"), 0.0)
        ),
        "fuel_supply_pressure_kpa": max(
            0.0, _safe_float(feed_request.get("fuel_supply_pressure_kpa"), 0.0)
        ),
        "oxidizer_supply_pressure_kpa": max(
            0.0, _safe_float(feed_request.get("oxidizer_supply_pressure_kpa"), 0.0)
        ),
        "fuel_tank_inlet_pressure_kpa": max(
            AMBIENT_PRESSURE_KPA,
            _safe_float(feed_request.get("fuel_tank_inlet_pressure_kpa"), 300.0),
        ),
        "oxidizer_tank_inlet_pressure_kpa": max(
            AMBIENT_PRESSURE_KPA,
            _safe_float(feed_request.get("oxidizer_tank_inlet_pressure_kpa"), 330.0),
        ),
        "design_supply_margin_ratio": _clamp(
            _safe_float(feed_request.get("design_supply_margin_ratio"), 0.08), 0.0, 0.50
        ),
        "fuel_minimum_injector_inlet_pressure_kpa": max(
            0.0,
            _safe_float(feed_request.get("fuel_minimum_injector_inlet_pressure_kpa"), 0.0),
        ),
        "fuel_regen_pressure_drop_kpa": max(
            0.0,
            _safe_float(feed_request.get("fuel_regen_pressure_drop_kpa"), 0.0),
        ),
        "analysis_throat_diameter_mm": max(
            0.0, _safe_float(feed_request.get("analysis_throat_diameter_mm"), 0.0)
        ),
    }
    if normalized["pressure_solve_mode"] not in {"design", "analysis"}:
        normalized["pressure_solve_mode"] = "design"
    return {
        "is_valid": True,
        "messages": [],
        "normalized_request": normalized,
    }


def _hydraulic_inputs(
    req: Dict[str, object],
    upstream_context: Optional[Dict[str, object]],
) -> Dict[str, object]:
    context = upstream_context if isinstance(upstream_context, dict) else {}
    throat_area_m2 = _safe_float(context.get("throat_area_m2"), 0.0)
    throat_diameter_mm = _safe_float(req.get("analysis_throat_diameter_mm"), 0.0)
    if throat_diameter_mm > 0.0:
        throat_area_m2 = math.pi * (throat_diameter_mm / 1000.0) ** 2 / 4.0
    cstar_m_s = _safe_float(context.get("cstar_m_s"), 0.0)
    target_pressure_kpa = _safe_float(
        context.get("target_chamber_pressure_kpa", context.get("chamber_pressure_kpa")),
        _safe_float(req.get("target_chamber_pressure_kpa"), 0.0),
    )
    mode = str(req.get("pressure_solve_mode", "design"))

    def branch(role: str) -> Dict[str, object]:
        prefix = "fuel" if role == "fuel" else "oxidizer"
        return {
            "density_kg_m3": float(req[f"{prefix}_density_kg_m3"]),
            "dynamic_viscosity_pa_s": float(req[f"{prefix}_dynamic_viscosity_pa_s"]),
            "line_diameter_m": float(req[f"line_diameter_{prefix}_m"]),
            "line_length_m": float(req[f"line_length_{prefix}_m"]),
            "minor_loss_k": float(req[f"minor_loss_{prefix}_k"]),
            "roughness_m": float(req[f"line_roughness_{prefix}_m"]),
            "discharge_coefficient": float(req[f"{prefix}_injector_discharge_coefficient"]),
            "injector_area_mm2": float(req[f"{prefix}_injector_area_mm2"]),
            "supply_pressure_kpa": float(req[f"{prefix}_supply_pressure_kpa"]),
        }

    return {
        "mode": mode,
        "target_chamber_pressure_kpa": target_pressure_kpa,
        "throat_area_m2": throat_area_m2,
        "cstar_m_s": cstar_m_s,
        "mixture_ratio": float(req["mixture_ratio"]),
        "design_injector_dp_ratio": float(req["design_injector_dp_ratio"]),
        "fuel_minimum_injector_inlet_pressure_kpa": float(
            req["fuel_minimum_injector_inlet_pressure_kpa"]
        ),
        "fuel_regen_pressure_drop_kpa": float(req["fuel_regen_pressure_drop_kpa"]),
        "fuel": branch("fuel"),
        "oxidizer": branch("oxidizer"),
    }


def _physical_feed_history(
    req: Dict[str, object],
    hydraulic_inputs: Dict[str, object],
    nominal: Dict[str, object],
) -> Dict[str, object]:
    burn_time_s = max(0.1, float(req["burn_time_seconds"]))
    history_steps = int(req["history_steps"])
    use_pumps = bool(req["use_pumps"])
    fill_fraction = float(req["initial_fill_fraction"])
    polytropic_index = float(req["pressurant_polytropic_index"])
    inlet_decay_fraction = float(req["pump_inlet_pressure_decay_fraction"])
    margin_ratio = float(req["design_supply_margin_ratio"])
    mode = str(req["pressure_solve_mode"])

    analysis_basis = dict(hydraulic_inputs)
    analysis_basis["mode"] = "analysis"
    analysis_basis["fuel"] = dict(hydraulic_inputs["fuel"])
    analysis_basis["oxidizer"] = dict(hydraulic_inputs["oxidizer"])
    for role in ("fuel", "oxidizer"):
        nominal_branch = _as_dict(nominal.get(role))
        analysis_basis[role]["injector_area_mm2"] = float(nominal_branch["injector_area_mm2"])
        if mode == "design":
            required = float(nominal_branch["required_supply_pressure_kpa"])
            analysis_basis[role]["supply_pressure_kpa"] = AMBIENT_PRESSURE_KPA + (
                required - AMBIENT_PRESSURE_KPA
            ) * (1.0 + margin_ratio)

    initial_supply = {
        role: float(analysis_basis[role]["supply_pressure_kpa"])
        for role in ("fuel", "oxidizer")
    }
    rows: List[Dict[str, object]] = []
    target_pressure = float(nominal["chamber_pressure_kpa"])
    target_branch = {role: _as_dict(nominal[role]) for role in ("fuel", "oxidizer")}
    for time_s in _build_time_grid(burn_time_s, history_steps):
        burn_fraction = _clamp(time_s / burn_time_s, 0.0, 1.0)
        step_inputs = dict(analysis_basis)
        step_inputs["fuel"] = dict(analysis_basis["fuel"])
        step_inputs["oxidizer"] = dict(analysis_basis["oxidizer"])
        if use_pumps:
            fuel_tank_pressure = _pump_inlet_tank_pressure_kpa(
                float(req["fuel_tank_inlet_pressure_kpa"]), burn_fraction, inlet_decay_fraction
            )
            oxidizer_tank_pressure = _pump_inlet_tank_pressure_kpa(
                float(req["oxidizer_tank_inlet_pressure_kpa"]), burn_fraction, inlet_decay_fraction * 0.92
            )
            fuel_supply = initial_supply["fuel"]
            oxidizer_supply = initial_supply["oxidizer"]
        else:
            fuel_supply = _pressure_fed_tank_pressure_kpa(
                initial_supply["fuel"], burn_fraction, fill_fraction, polytropic_index
            )
            oxidizer_supply = _pressure_fed_tank_pressure_kpa(
                initial_supply["oxidizer"], burn_fraction, fill_fraction, polytropic_index
            )
            fuel_tank_pressure = fuel_supply
            oxidizer_tank_pressure = oxidizer_supply
        step_inputs["fuel"]["supply_pressure_kpa"] = fuel_supply
        step_inputs["oxidizer"]["supply_pressure_kpa"] = oxidizer_supply
        step = solve_hydraulic_chamber(step_inputs)
        fuel = _as_dict(step["fuel"])
        oxidizer = _as_dict(step["oxidizer"])
        fuel_required = target_pressure + float(target_branch["fuel"]["total_pressure_drop_kpa"])
        oxidizer_required = target_pressure + float(target_branch["oxidizer"]["total_pressure_drop_kpa"])
        fuel_margin = fuel_supply - fuel_required
        oxidizer_margin = oxidizer_supply - oxidizer_required
        pump_discharge = min(fuel_supply, oxidizer_supply) if use_pumps else 0.0
        mean_tank = 0.5 * (fuel_tank_pressure + oxidizer_tank_pressure)
        rows.append(
            {
                "time_s": round(time_s, 6),
                "burn_fraction": round(burn_fraction, 6),
                "architecture_mode": "pump-fed-regulated" if use_pumps else "pressure-fed-blowdown",
                "fuel_tank_pressure_kpa": round(fuel_tank_pressure, 4),
                "oxidizer_tank_pressure_kpa": round(oxidizer_tank_pressure, 4),
                "fuel_supply_pressure_kpa": round(fuel_supply, 4),
                "oxidizer_supply_pressure_kpa": round(oxidizer_supply, 4),
                "required_feed_pressure_kpa": round(max(fuel_required, oxidizer_required), 4),
                "chamber_pressure_kpa": round(float(step["chamber_pressure_kpa"]), 4),
                "propellant_mass_flow_kg_s": round(float(step["total_mass_flow_kg_s"]), 7),
                "fuel_mass_flow_kg_s": round(float(step["fuel_mass_flow_kg_s"]), 7),
                "oxidizer_mass_flow_kg_s": round(float(step["oxidizer_mass_flow_kg_s"]), 7),
                "actual_mixture_ratio": round(float(step["actual_mixture_ratio"]), 6),
                "injector_pressure_drop_kpa": round(
                    max(float(fuel["injector_pressure_drop_kpa"]), float(oxidizer["injector_pressure_drop_kpa"])),
                    4,
                ),
                "fuel_line_pressure_drop_kpa": round(float(fuel["pressure_drop_kpa"]), 4),
                "oxidizer_line_pressure_drop_kpa": round(float(oxidizer["pressure_drop_kpa"]), 4),
                "pump_discharge_pressure_kpa": round(pump_discharge, 4),
                "pump_differential_pressure_kpa": round(max(0.0, pump_discharge - mean_tank), 4),
                "pump_speed_fraction": 1.0 if use_pumps else 0.0,
                "fuel_margin_kpa": round(fuel_margin, 4),
                "oxidizer_margin_kpa": round(oxidizer_margin, 4),
                "mass_balance_relative_error": step["mass_balance_relative_error"],
            }
        )

    initial_pc = float(rows[0]["chamber_pressure_kpa"])
    final_pc = float(rows[-1]["chamber_pressure_kpa"])
    return {
        "time_history_rows": rows,
        "summary": {
            "history_step_count": len(rows),
            "initial_chamber_pressure_kpa": round(initial_pc, 4),
            "final_chamber_pressure_kpa": round(final_pc, 4),
            "minimum_chamber_pressure_kpa": round(min(float(row["chamber_pressure_kpa"]) for row in rows), 4),
            "initial_required_feed_pressure_kpa": rows[0]["required_feed_pressure_kpa"],
            "final_required_feed_pressure_kpa": rows[-1]["required_feed_pressure_kpa"],
            "initial_fuel_tank_pressure_kpa": rows[0]["fuel_tank_pressure_kpa"],
            "final_fuel_tank_pressure_kpa": rows[-1]["fuel_tank_pressure_kpa"],
            "initial_oxidizer_tank_pressure_kpa": rows[0]["oxidizer_tank_pressure_kpa"],
            "final_oxidizer_tank_pressure_kpa": rows[-1]["oxidizer_tank_pressure_kpa"],
            "minimum_fuel_margin_kpa": round(min(float(row["fuel_margin_kpa"]) for row in rows), 4),
            "minimum_oxidizer_margin_kpa": round(min(float(row["oxidizer_margin_kpa"]) for row in rows), 4),
            "minimum_feed_margin_kpa": round(
                min(min(float(row["fuel_margin_kpa"]), float(row["oxidizer_margin_kpa"])) for row in rows), 4
            ),
            "final_propellant_mass_flow_kg_s": rows[-1]["propellant_mass_flow_kg_s"],
            "chamber_pressure_drift_percent": round(100.0 * (final_pc - initial_pc) / max(1e-9, initial_pc), 6),
            "maximum_pump_speed_fraction": 1.0 if use_pumps else 0.0,
        },
    }


def _average_history_value(time_history_rows: List[Dict[str, object]], key: str) -> float:
    if not time_history_rows:
        return 0.0
    total = sum(float(row[key]) for row in time_history_rows)
    return total / len(time_history_rows)


def _feed_station_field(value: float, unit: str) -> Dict[str, object]:
    rounded_value = round(value, 4) if unit == "kg/s" else round(value, 2)
    return {
        "value": rounded_value,
        "unit": unit,
        "status": "calculated",
        "source_solver": SOLVER_NAME,
    }


def _build_feed_station_updates(
    use_pumps: bool,
    average_tank_pressure_kpa: float,
    average_pump_discharge_kpa: float,
    average_required_feed_kpa: float,
    average_chamber_pressure_kpa: float,
    fuel_mass_flow_kg_s: float,
    total_mass_flow_kg_s: float,
) -> Dict[str, Dict[str, Dict[str, object]]]:
    pump_bay_pressure_kpa = average_pump_discharge_kpa if use_pumps else average_required_feed_kpa
    return {
        "Fuel Feed Inlet": {
            "pressure_kpa": _feed_station_field(average_tank_pressure_kpa, "kPa"),
            "mass_flow_kg_s": _feed_station_field(fuel_mass_flow_kg_s, "kg/s"),
        },
        "Pump Or Pressurization Bay": {
            "pressure_kpa": _feed_station_field(pump_bay_pressure_kpa, "kPa"),
            "mass_flow_kg_s": _feed_station_field(total_mass_flow_kg_s, "kg/s"),
        },
        "Injector Face": {
            "pressure_kpa": _feed_station_field(average_chamber_pressure_kpa, "kPa"),
            "mass_flow_kg_s": _feed_station_field(total_mass_flow_kg_s, "kg/s"),
        },
    }


def solve(
    design_request: Dict[str, object],
    upstream_context: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    validation = validate_inputs(design_request)
    req = _as_dict(validation.get("normalized_request"))
    use_pumps = bool(req.get("use_pumps", False))
    burn_time_s = max(1.0, float(req.get("burn_time_seconds", 12.0)))
    hydraulic_inputs = _hydraulic_inputs(req, upstream_context)
    closure = solve_hydraulic_chamber(hydraulic_inputs)
    chamber_pressure_kpa = float(closure["chamber_pressure_kpa"])
    total_mass_flow_kg_s = float(closure["total_mass_flow_kg_s"])
    fuel_mass_flow_kg_s = float(closure["fuel_mass_flow_kg_s"])
    fuel_branch = _as_dict(closure["fuel"])
    oxidizer_branch = _as_dict(closure["oxidizer"])
    fuel_total_drop_kpa = float(fuel_branch["total_pressure_drop_kpa"])
    oxidizer_total_drop_kpa = float(oxidizer_branch["total_pressure_drop_kpa"])
    injector_pressure_drop_kpa = max(
        float(fuel_branch["injector_pressure_drop_kpa"]),
        float(oxidizer_branch["injector_pressure_drop_kpa"]),
    )
    total_drop_kpa = max(fuel_total_drop_kpa, oxidizer_total_drop_kpa)
    required_feed_delta_kpa = total_drop_kpa
    required_tank_pressure_kpa = max(
        float(fuel_branch["required_supply_pressure_kpa"]),
        float(oxidizer_branch["required_supply_pressure_kpa"]),
    )
    fuel_required_pump_head_kpa = (
        max(
            0.0,
            float(fuel_branch["required_supply_pressure_kpa"])
            - float(req["fuel_tank_inlet_pressure_kpa"]),
        )
        if use_pumps
        else 0.0
    )
    oxidizer_required_pump_head_kpa = (
        max(
            0.0,
            float(oxidizer_branch["required_supply_pressure_kpa"])
            - float(req["oxidizer_tank_inlet_pressure_kpa"]),
        )
        if use_pumps
        else 0.0
    )
    design_mode = req["pressure_solve_mode"] == "design"
    input_classification = {
        "chamber_pressure_kpa": "selected design requirement" if design_mode else "calculated output",
        "throat_geometry": "calculated design geometry" if design_mode else "specified as-built input",
        "injector_flow_areas": "calculated design geometry" if design_mode else "specified as-built input",
        "supply_pressures": "calculated requirement" if design_mode else "specified boundary condition",
        "injector_discharge_coefficients": "specified characterization input",
        "line_geometry_and_loss_coefficients": "specified hardware inputs",
        "propellant_density_and_viscosity": "property-model inputs",
        "characteristic_velocity": "calculated thermochemistry input",
        "mass_flows": "calculated outputs",
    }

    transient = _physical_feed_history(req, hydraulic_inputs, closure)
    time_history_rows = transient["time_history_rows"]
    transient_summary = transient["summary"]
    average_chamber_pressure_kpa = _average_history_value(time_history_rows, "chamber_pressure_kpa")
    average_required_feed_kpa = _average_history_value(time_history_rows, "required_feed_pressure_kpa")
    average_tank_pressure_kpa = _average_history_value(time_history_rows, "fuel_tank_pressure_kpa")
    average_pump_discharge_kpa = _average_history_value(time_history_rows, "pump_discharge_pressure_kpa")

    segment_rows: List[Dict[str, object]] = [
        {
            "segment": "fuel_branch_total",
            "pressure_drop_kpa": round(fuel_total_drop_kpa, 2),
            "minimum_margin_kpa": transient_summary["minimum_fuel_margin_kpa"],
            "status": "calculated",
            "velocity_m_s": round(float(fuel_branch["velocity_m_s"]), 3),
            "reynolds": round(fuel_branch["reynolds"], 1),
            "major_pressure_drop_kpa": round(fuel_branch["major_pressure_drop_kpa"], 3),
            "minor_pressure_drop_kpa": round(fuel_branch["minor_pressure_drop_kpa"], 3),
            "friction_factor": round(fuel_branch["friction_factor"], 6),
            "flow_regime": fuel_branch["flow_regime"],
            "relative_roughness": round(fuel_branch["relative_roughness"], 7),
            "dynamic_viscosity_pa_s": round(fuel_branch["dynamic_viscosity_pa_s"], 7),
            "friction_iterations": int(fuel_branch["friction_iterations"]),
        },
        {
            "segment": "oxidizer_branch_total",
            "pressure_drop_kpa": round(oxidizer_total_drop_kpa, 2),
            "minimum_margin_kpa": transient_summary["minimum_oxidizer_margin_kpa"],
            "status": "calculated",
            "velocity_m_s": round(oxidizer_branch["velocity_m_s"], 3),
            "reynolds": round(oxidizer_branch["reynolds"], 1),
            "major_pressure_drop_kpa": round(oxidizer_branch["major_pressure_drop_kpa"], 3),
            "minor_pressure_drop_kpa": round(oxidizer_branch["minor_pressure_drop_kpa"], 3),
            "friction_factor": round(oxidizer_branch["friction_factor"], 6),
            "flow_regime": oxidizer_branch["flow_regime"],
            "relative_roughness": round(oxidizer_branch["relative_roughness"], 7),
            "dynamic_viscosity_pa_s": round(oxidizer_branch["dynamic_viscosity_pa_s"], 7),
            "friction_iterations": int(oxidizer_branch["friction_iterations"]),
        },
        {
            "segment": "injector_pressure_drop",
            "pressure_drop_kpa": round(injector_pressure_drop_kpa, 2),
            "status": "calculated",
            "fuel_pressure_drop_kpa": round(float(fuel_branch["injector_pressure_drop_kpa"]), 3),
            "oxidizer_pressure_drop_kpa": round(float(oxidizer_branch["injector_pressure_drop_kpa"]), 3),
            "fuel_total_flow_area_mm2": round(float(fuel_branch["injector_area_mm2"]), 5),
            "oxidizer_total_flow_area_mm2": round(float(oxidizer_branch["injector_area_mm2"]), 5),
        },
        {
            "segment": "required_feed_delta",
            "pressure_drop_kpa": round(required_feed_delta_kpa, 2),
            "minimum_margin_kpa": transient_summary["minimum_feed_margin_kpa"],
            "status": "calculated",
        },
        {
            "segment": "burn_time_feed_tailoff",
            "pressure_drop_kpa": round(
                float(transient_summary["initial_chamber_pressure_kpa"]) - float(transient_summary["final_chamber_pressure_kpa"]),
                2,
            ),
            "status": "calculated",
        },
    ]

    station_field_updates = _build_feed_station_updates(
        use_pumps=use_pumps,
        average_tank_pressure_kpa=average_tank_pressure_kpa,
        average_pump_discharge_kpa=average_pump_discharge_kpa,
        average_required_feed_kpa=average_required_feed_kpa,
        average_chamber_pressure_kpa=average_chamber_pressure_kpa,
        fuel_mass_flow_kg_s=fuel_mass_flow_kg_s,
        total_mass_flow_kg_s=total_mass_flow_kg_s,
    )

    trace = [
        "Validated explicit hydraulic boundary conditions and hardware inputs.",
        "Solved mdot_f + mdot_ox = Pc At / cstar with injector-orifice and Darcy/Colebrook branch equations.",
        "Integrated the frozen injector geometry through the burn-time supply-pressure history.",
    ]
    if upstream_context:
        trace.append("Upstream context keys: {0}".format(", ".join(sorted(upstream_context.keys()))))

    summary = {
        "total_pressure_drop_kpa": round(total_drop_kpa, 2),
        "model_status": "calculated",
        "quality_flag": "hydraulic-chamber-closure-v1",
        "feed_line_model": "simultaneous chamber mass balance, injector orifice flow, and Darcy-Weisbach/Colebrook losses",
        "pressure_solve_mode": req["pressure_solve_mode"],
        "chamber_pressure_kpa": round(chamber_pressure_kpa, 2),
        "injector_pressure_drop_kpa": round(injector_pressure_drop_kpa, 2),
        "fuel_branch_pressure_drop_kpa": round(fuel_total_drop_kpa, 2),
        "oxidizer_branch_pressure_drop_kpa": round(oxidizer_total_drop_kpa, 2),
        "fuel_branch_flow_regime": fuel_branch["flow_regime"],
        "oxidizer_branch_flow_regime": oxidizer_branch["flow_regime"],
        "fuel_branch_friction_factor": round(fuel_branch["friction_factor"], 6),
        "oxidizer_branch_friction_factor": round(oxidizer_branch["friction_factor"], 6),
        "required_feed_delta_kpa": round(required_feed_delta_kpa, 2),
        "required_tank_pressure_kpa": round(required_tank_pressure_kpa, 2),
        "fuel_required_supply_pressure_kpa": round(float(fuel_branch["required_supply_pressure_kpa"]), 3),
        "oxidizer_required_supply_pressure_kpa": round(float(oxidizer_branch["required_supply_pressure_kpa"]), 3),
        "fuel_required_pump_head_kpa": round(fuel_required_pump_head_kpa, 3),
        "oxidizer_required_pump_head_kpa": round(oxidizer_required_pump_head_kpa, 3),
        "fuel_injector_area_mm2": round(float(fuel_branch["injector_area_mm2"]), 6),
        "fuel_injector_pressure_drop_kpa": round(
            float(fuel_branch["injector_pressure_drop_kpa"]), 3
        ),
        "fuel_regen_pressure_drop_kpa": round(
            float(fuel_branch.get("additional_pressure_drop_kpa", 0.0)), 3
        ),
        "fuel_minimum_injector_inlet_pressure_kpa": round(
            float(fuel_branch.get("minimum_injector_inlet_pressure_kpa", 0.0)), 3
        ),
        "fuel_coolant_pressure_constraint_active": bool(
            fuel_branch.get("minimum_injector_inlet_constraint_active", False)
        ),
        "oxidizer_injector_area_mm2": round(float(oxidizer_branch["injector_area_mm2"]), 6),
        "fuel_injector_discharge_coefficient": round(float(fuel_branch["discharge_coefficient"]), 5),
        "oxidizer_injector_discharge_coefficient": round(float(oxidizer_branch["discharge_coefficient"]), 5),
        "actual_mixture_ratio": closure["actual_mixture_ratio"],
        "mixture_ratio_error_percent": closure["mixture_ratio_error_percent"],
        "mass_balance_residual_kg_s": closure["mass_balance_residual_kg_s"],
        "mass_balance_relative_error": closure["mass_balance_relative_error"],
        "hydraulic_iteration_count": closure["iterations"],
        "hydraulic_converged": closure["converged"],
        "propellant_mass_flow_kg_s": round(total_mass_flow_kg_s, 4),
        "burn_time_seconds": round(burn_time_s, 3),
        "history_step_count": transient_summary["history_step_count"],
        "initial_chamber_pressure_kpa": transient_summary["initial_chamber_pressure_kpa"],
        "final_chamber_pressure_kpa": transient_summary["final_chamber_pressure_kpa"],
        "minimum_chamber_pressure_kpa": transient_summary["minimum_chamber_pressure_kpa"],
        "initial_required_feed_pressure_kpa": transient_summary["initial_required_feed_pressure_kpa"],
        "final_required_feed_pressure_kpa": transient_summary["final_required_feed_pressure_kpa"],
        "initial_fuel_tank_pressure_kpa": transient_summary["initial_fuel_tank_pressure_kpa"],
        "final_fuel_tank_pressure_kpa": transient_summary["final_fuel_tank_pressure_kpa"],
        "initial_oxidizer_tank_pressure_kpa": transient_summary["initial_oxidizer_tank_pressure_kpa"],
        "final_oxidizer_tank_pressure_kpa": transient_summary["final_oxidizer_tank_pressure_kpa"],
        "minimum_fuel_margin_kpa": transient_summary["minimum_fuel_margin_kpa"],
        "minimum_oxidizer_margin_kpa": transient_summary["minimum_oxidizer_margin_kpa"],
        "minimum_feed_margin_kpa": transient_summary["minimum_feed_margin_kpa"],
        "final_propellant_mass_flow_kg_s": transient_summary["final_propellant_mass_flow_kg_s"],
        "chamber_pressure_drift_percent": transient_summary["chamber_pressure_drift_percent"],
        "maximum_pump_speed_fraction": transient_summary["maximum_pump_speed_fraction"],
    }

    return {
        "metadata": {
            "solver_name": SOLVER_NAME,
            "solver_version": SOLVER_VERSION,
            "solver_mode": "hydraulic-chamber-v1",
            "input_schema_version": "1.2",
            "output_schema_version": "1.2",
        },
        "status": "ok",
        "payload": {
            "request": req,
            "summary": summary,
            "hydraulic_closure": closure,
            "hydraulic_inputs": hydraulic_inputs,
            "input_classification": input_classification,
            "segment_rows": segment_rows,
            "time_history_rows": time_history_rows,
            "station_field_updates": station_field_updates,
        },
        "warnings": [
            "Hydraulic closure is one-dimensional and requires measured or manufacturer-derived Cd, loss coefficients, and supply conditions for analysis-grade use.",
        ],
        "trace": trace,
    }


def summarize(feed_solver_result: Dict[str, object]) -> Dict[str, object]:
    payload = _as_dict(feed_solver_result.get("payload"))
    summary = _as_dict(payload.get("summary"))
    warnings = feed_solver_result.get("warnings", [])
    warning_lines = list(warnings) if isinstance(warnings, list) else []
    return {
        "title": "Feed Transient Summary",
        "key_values": {
            "status": feed_solver_result.get("status", "unknown"),
            "solver_mode": _as_dict(feed_solver_result.get("metadata")).get("solver_mode"),
            "minimum_feed_margin_kpa": summary.get("minimum_feed_margin_kpa"),
            "chamber_pressure_drift_percent": summary.get("chamber_pressure_drift_percent"),
        },
        "notes": warning_lines,
    }
