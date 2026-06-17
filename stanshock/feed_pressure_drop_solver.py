from typing import Any, Dict, List, Optional


SOLVER_NAME = "Feed Pressure-Drop Solver"
SOLVER_VERSION = "0.3"
AMBIENT_PRESSURE_KPA = 101.3


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


def _round_or_none(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _line_loss_kpa(
    mass_flow_kg_s: float,
    density_kg_m3: float,
    diameter_m: float,
    length_m: float,
    minor_k: float,
) -> Dict[str, float]:
    density = max(60.0, density_kg_m3)
    diameter = max(0.003, diameter_m)
    area = 3.141592653589793 * diameter * diameter * 0.25
    velocity = mass_flow_kg_s / max(1e-8, density * area)

    viscosity_pa_s = 0.00022 if density > 900.0 else 0.0007
    reynolds = max(1.0, density * velocity * diameter / max(1e-8, viscosity_pa_s))
    if reynolds < 2300.0:
        friction_factor = 64.0 / reynolds
    else:
        friction_factor = 0.3164 / (reynolds ** 0.25)
    equivalent_k = friction_factor * (length_m / diameter) + max(0.0, minor_k)
    dp_pa = equivalent_k * 0.5 * density * velocity * velocity
    return {
        "pressure_drop_kpa": dp_pa / 1000.0,
        "velocity_m_s": velocity,
        "reynolds": reynolds,
        "friction_factor": friction_factor,
        "equivalent_k": equivalent_k,
    }


def _estimate_chamber_pressure_kpa(thrust_n: float, use_pumps: bool) -> float:
    thrust_term = (max(1.0, thrust_n) / 250.0) ** 0.58
    architecture_scale = 1.08 if use_pumps else 0.95
    return _clamp(1350.0 * thrust_term * architecture_scale, 500.0, 18000.0)


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

    tank_diameter_mm = _safe_float(geometry_limits.get("tank_diameter_mm"), 110.0)
    base_line_diameter_m = _clamp(tank_diameter_mm / 1000.0 * 0.11, 0.008, 0.028)

    use_pumps = bool(architecture.get("use_pumps", False))
    history_steps_default = 31 if use_pumps else 41
    initial_fill_fraction_default = 0.72 if use_pumps else 0.58

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
        "fuel_density_kg_m3": _clamp(_safe_float(fuel_record.get("density_index"), 0.75) * 1000.0, 450.0, 1250.0),
        "oxidizer_density_kg_m3": _clamp(_safe_float(oxidizer_record.get("density_index"), 0.9) * 1000.0, 650.0, 1600.0),
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
    }
    return {
        "is_valid": True,
        "messages": [],
        "normalized_request": normalized,
    }


def _simulate_feed_history(
    req: Dict[str, object],
    chamber_pressure_kpa: float,
    injector_dp_ratio: float,
    fuel_total_drop_kpa: float,
    oxidizer_total_drop_kpa: float,
    total_mass_flow_kg_s: float,
    fuel_mass_flow_kg_s: float,
    oxidizer_mass_flow_kg_s: float,
    required_tank_pressure_kpa: float,
) -> Dict[str, object]:
    burn_time_s = max(1.0, float(req.get("burn_time_seconds", 12.0)))
    history_steps = int(req.get("history_steps", 31))
    use_pumps = bool(req.get("use_pumps", False))
    fill_fraction = float(req.get("initial_fill_fraction", 0.58))
    polytropic_index = float(req.get("pressurant_polytropic_index", 1.08))
    inlet_decay_fraction = float(req.get("pump_inlet_pressure_decay_fraction", 0.09))

    nominal_required_feed_kpa = chamber_pressure_kpa * (1.0 + injector_dp_ratio) + max(
        fuel_total_drop_kpa, oxidizer_total_drop_kpa
    )
    time_history_rows: List[Dict[str, object]] = []

    if use_pumps:
        fuel_tank_initial_kpa = max(AMBIENT_PRESSURE_KPA + 140.0, chamber_pressure_kpa * 0.18)
        oxidizer_tank_initial_kpa = max(AMBIENT_PRESSURE_KPA + 165.0, chamber_pressure_kpa * 0.21)
        nominal_pump_discharge_kpa = nominal_required_feed_kpa * 1.05
        pump_discharge_cap_kpa = nominal_required_feed_kpa * 1.14
    else:
        fuel_tank_initial_kpa = required_tank_pressure_kpa * 1.01
        oxidizer_tank_initial_kpa = required_tank_pressure_kpa * 1.03
        nominal_pump_discharge_kpa = 0.0
        pump_discharge_cap_kpa = 0.0

    previous_chamber_pressure_kpa = chamber_pressure_kpa
    for time_s in _build_time_grid(burn_time_s, history_steps):
        burn_fraction = _clamp(time_s / burn_time_s, 0.0, 1.0)

        if use_pumps:
            fuel_tank_pressure_kpa = _pump_inlet_tank_pressure_kpa(
                fuel_tank_initial_kpa,
                burn_fraction,
                inlet_decay_fraction,
            )
            oxidizer_tank_pressure_kpa = _pump_inlet_tank_pressure_kpa(
                oxidizer_tank_initial_kpa,
                burn_fraction,
                inlet_decay_fraction * 0.92,
            )
        else:
            fuel_tank_pressure_kpa = _pressure_fed_tank_pressure_kpa(
                fuel_tank_initial_kpa,
                burn_fraction,
                fill_fraction,
                polytropic_index,
            )
            oxidizer_tank_pressure_kpa = _pressure_fed_tank_pressure_kpa(
                oxidizer_tank_initial_kpa,
                burn_fraction,
                fill_fraction,
                polytropic_index,
            )

        chamber_guess_kpa = chamber_pressure_kpa if time_s <= 0.0 else previous_chamber_pressure_kpa
        pump_discharge_pressure_kpa = 0.0
        pump_differential_pressure_kpa = 0.0
        pump_speed_fraction = 0.0

        for _ in range(4):
            flow_scale = _clamp(chamber_guess_kpa / max(1.0, chamber_pressure_kpa), 0.38, 1.12)
            fuel_drop_kpa = fuel_total_drop_kpa * (flow_scale ** 2)
            oxidizer_drop_kpa = oxidizer_total_drop_kpa * (flow_scale ** 2)

            if use_pumps:
                required_discharge_kpa = chamber_pressure_kpa * (1.0 + injector_dp_ratio) + max(
                    fuel_drop_kpa, oxidizer_drop_kpa
                )
                control_reserve_kpa = max(18.0, required_discharge_kpa * (0.025 + 0.015 * burn_fraction))
                mean_tank_pressure_kpa = 0.5 * (fuel_tank_pressure_kpa + oxidizer_tank_pressure_kpa)
                pump_discharge_pressure_kpa = min(
                    pump_discharge_cap_kpa,
                    required_discharge_kpa + control_reserve_kpa,
                )
                pump_differential_pressure_kpa = max(0.0, pump_discharge_pressure_kpa - mean_tank_pressure_kpa)
                pump_speed_fraction = _clamp(
                    pump_discharge_pressure_kpa / max(1.0, nominal_pump_discharge_kpa),
                    0.82,
                    1.18,
                )
                available_feed_kpa = pump_discharge_pressure_kpa - max(fuel_drop_kpa, oxidizer_drop_kpa)
                chamber_support_kpa = available_feed_kpa / max(1.0, 1.0 + injector_dp_ratio)
                chamber_guess_kpa = min(chamber_pressure_kpa, chamber_support_kpa)
            else:
                available_fuel_feed_kpa = fuel_tank_pressure_kpa - fuel_drop_kpa
                available_oxidizer_feed_kpa = oxidizer_tank_pressure_kpa - oxidizer_drop_kpa
                available_feed_kpa = min(available_fuel_feed_kpa, available_oxidizer_feed_kpa)
                chamber_support_kpa = available_feed_kpa / max(1.0, 1.0 + injector_dp_ratio)
                chamber_guess_kpa = min(chamber_pressure_kpa, chamber_support_kpa)

            chamber_guess_kpa = _clamp(chamber_guess_kpa, AMBIENT_PRESSURE_KPA + 35.0, chamber_pressure_kpa)

        actual_chamber_pressure_kpa = chamber_guess_kpa
        actual_flow_scale = _clamp(actual_chamber_pressure_kpa / max(1.0, chamber_pressure_kpa), 0.38, 1.12)
        actual_total_mass_flow_kg_s = total_mass_flow_kg_s * actual_flow_scale
        actual_fuel_mass_flow_kg_s = fuel_mass_flow_kg_s * actual_flow_scale
        actual_oxidizer_mass_flow_kg_s = oxidizer_mass_flow_kg_s * actual_flow_scale
        actual_fuel_drop_kpa = fuel_total_drop_kpa * (actual_flow_scale ** 2)
        actual_oxidizer_drop_kpa = oxidizer_total_drop_kpa * (actual_flow_scale ** 2)
        actual_injector_drop_kpa = actual_chamber_pressure_kpa * injector_dp_ratio

        if use_pumps:
            required_feed_pressure_kpa = actual_chamber_pressure_kpa + actual_injector_drop_kpa + max(
                actual_fuel_drop_kpa, actual_oxidizer_drop_kpa
            )
            fuel_margin_kpa = fuel_tank_pressure_kpa + pump_differential_pressure_kpa - required_feed_pressure_kpa
            oxidizer_margin_kpa = oxidizer_tank_pressure_kpa + pump_differential_pressure_kpa - required_feed_pressure_kpa
        else:
            required_feed_pressure_kpa = actual_chamber_pressure_kpa + actual_injector_drop_kpa + max(
                actual_fuel_drop_kpa, actual_oxidizer_drop_kpa
            )
            fuel_margin_kpa = fuel_tank_pressure_kpa - required_feed_pressure_kpa
            oxidizer_margin_kpa = oxidizer_tank_pressure_kpa - required_feed_pressure_kpa

        time_history_rows.append(
            {
                "time_s": round(time_s, 4),
                "burn_fraction": round(burn_fraction, 6),
                "architecture_mode": "pump-fed-controlled" if use_pumps else "pressure-fed-blowdown",
                "chamber_pressure_kpa": round(actual_chamber_pressure_kpa, 4),
                "required_feed_pressure_kpa": round(required_feed_pressure_kpa, 4),
                "fuel_tank_pressure_kpa": round(fuel_tank_pressure_kpa, 4),
                "oxidizer_tank_pressure_kpa": round(oxidizer_tank_pressure_kpa, 4),
                "fuel_branch_pressure_drop_kpa": round(actual_fuel_drop_kpa, 4),
                "oxidizer_branch_pressure_drop_kpa": round(actual_oxidizer_drop_kpa, 4),
                "injector_pressure_drop_kpa": round(actual_injector_drop_kpa, 4),
                "fuel_margin_kpa": round(fuel_margin_kpa, 4),
                "oxidizer_margin_kpa": round(oxidizer_margin_kpa, 4),
                "pump_discharge_pressure_kpa": round(pump_discharge_pressure_kpa, 4),
                "pump_differential_pressure_kpa": round(pump_differential_pressure_kpa, 4),
                "pump_speed_fraction": round(pump_speed_fraction, 5),
                "propellant_mass_flow_kg_s": round(actual_total_mass_flow_kg_s, 5),
                "fuel_mass_flow_kg_s": round(actual_fuel_mass_flow_kg_s, 5),
                "oxidizer_mass_flow_kg_s": round(actual_oxidizer_mass_flow_kg_s, 5),
                "flow_scale": round(actual_flow_scale, 6),
            }
        )
        previous_chamber_pressure_kpa = actual_chamber_pressure_kpa

    initial_row = time_history_rows[0]
    final_row = time_history_rows[-1]
    minimum_feed_margin_kpa = min(
        min(float(row["fuel_margin_kpa"]), float(row["oxidizer_margin_kpa"])) for row in time_history_rows
    )
    minimum_chamber_pressure_kpa = min(float(row["chamber_pressure_kpa"]) for row in time_history_rows)
    maximum_pump_speed_fraction = max(float(row["pump_speed_fraction"]) for row in time_history_rows)
    return {
        "time_history_rows": time_history_rows,
        "summary": {
            "history_step_count": len(time_history_rows),
            "initial_chamber_pressure_kpa": _round_or_none(initial_row.get("chamber_pressure_kpa")),
            "final_chamber_pressure_kpa": _round_or_none(final_row.get("chamber_pressure_kpa")),
            "initial_required_feed_pressure_kpa": _round_or_none(initial_row.get("required_feed_pressure_kpa")),
            "final_required_feed_pressure_kpa": _round_or_none(final_row.get("required_feed_pressure_kpa")),
            "initial_fuel_tank_pressure_kpa": _round_or_none(initial_row.get("fuel_tank_pressure_kpa")),
            "final_fuel_tank_pressure_kpa": _round_or_none(final_row.get("fuel_tank_pressure_kpa")),
            "initial_oxidizer_tank_pressure_kpa": _round_or_none(initial_row.get("oxidizer_tank_pressure_kpa")),
            "final_oxidizer_tank_pressure_kpa": _round_or_none(final_row.get("oxidizer_tank_pressure_kpa")),
            "minimum_chamber_pressure_kpa": round(minimum_chamber_pressure_kpa, 3),
            "minimum_fuel_margin_kpa": round(min(float(row["fuel_margin_kpa"]) for row in time_history_rows), 3),
            "minimum_oxidizer_margin_kpa": round(min(float(row["oxidizer_margin_kpa"]) for row in time_history_rows), 3),
            "minimum_feed_margin_kpa": round(minimum_feed_margin_kpa, 3),
            "final_propellant_mass_flow_kg_s": _round_or_none(final_row.get("propellant_mass_flow_kg_s"), 5),
            "chamber_pressure_drift_percent": round(
                100.0
                * (
                    float(initial_row["chamber_pressure_kpa"]) - float(final_row["chamber_pressure_kpa"])
                )
                / max(1.0, float(initial_row["chamber_pressure_kpa"])),
                3,
            ),
            "maximum_pump_speed_fraction": round(maximum_pump_speed_fraction, 4),
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

    thrust_n = max(1.0, float(req.get("target_thrust_newtons", 250.0)))
    tank_diameter_mm = max(30.0, float(req.get("tank_diameter_mm", 110.0)))
    use_pumps = bool(req.get("use_pumps", False))
    regen_cooling = bool(req.get("regen_cooling", False))
    mixture_ratio = max(0.1, float(req.get("mixture_ratio", 1.4)))
    burn_time_s = max(1.0, float(req.get("burn_time_seconds", 12.0)))

    requested_chamber_pressure_kpa = _safe_float(req.get("target_chamber_pressure_kpa"), 0.0)
    if isinstance(upstream_context, dict):
        requested_chamber_pressure_kpa = _safe_float(
            upstream_context.get("target_chamber_pressure_kpa", upstream_context.get("chamber_pressure_kpa")),
            requested_chamber_pressure_kpa,
        )
    chamber_pressure_kpa = (
        _clamp(requested_chamber_pressure_kpa, 150.0, 18000.0)
        if requested_chamber_pressure_kpa > 0.0
        else _estimate_chamber_pressure_kpa(thrust_n, use_pumps)
    )
    injector_dp_ratio = 0.18 if str(req.get("injector_type", "impinging")) == "impinging" else 0.14
    injector_pressure_drop_kpa = chamber_pressure_kpa * injector_dp_ratio

    thermal_margin_factor = 1.05 if regen_cooling else 1.0
    isp_proxy_s = 212.0 + (18.0 if use_pumps else 6.0) + (6.0 if regen_cooling else 0.0)
    total_mass_flow_kg_s = thrust_n / max(1e-6, 9.80665 * isp_proxy_s)
    oxidizer_mass_fraction = mixture_ratio / (1.0 + mixture_ratio)
    oxidizer_mass_flow_kg_s = total_mass_flow_kg_s * oxidizer_mass_fraction
    fuel_mass_flow_kg_s = total_mass_flow_kg_s - oxidizer_mass_flow_kg_s

    fuel_branch = _line_loss_kpa(
        mass_flow_kg_s=fuel_mass_flow_kg_s,
        density_kg_m3=float(req.get("fuel_density_kg_m3", 760.0)),
        diameter_m=float(req.get("line_diameter_fuel_m", 0.012)),
        length_m=float(req.get("line_length_fuel_m", 1.55)),
        minor_k=float(req.get("minor_loss_fuel_k", 8.0)),
    )
    oxidizer_branch = _line_loss_kpa(
        mass_flow_kg_s=oxidizer_mass_flow_kg_s,
        density_kg_m3=float(req.get("oxidizer_density_kg_m3", 980.0)),
        diameter_m=float(req.get("line_diameter_oxidizer_m", 0.011)),
        length_m=float(req.get("line_length_oxidizer_m", 1.75)),
        minor_k=float(req.get("minor_loss_oxidizer_k", 9.2)),
    )

    distribution_loss_kpa = _clamp(5.5 * thermal_margin_factor * (110.0 / tank_diameter_mm), 2.5, 18.0)
    fuel_total_drop_kpa = fuel_branch["pressure_drop_kpa"] + distribution_loss_kpa
    oxidizer_total_drop_kpa = oxidizer_branch["pressure_drop_kpa"] + distribution_loss_kpa
    branch_total_drop_kpa = max(fuel_total_drop_kpa, oxidizer_total_drop_kpa)
    total_drop_kpa = branch_total_drop_kpa + injector_pressure_drop_kpa

    pressure_margin_factor = 1.12 if use_pumps else 1.18
    required_feed_delta_kpa = total_drop_kpa * pressure_margin_factor
    required_tank_pressure_kpa = chamber_pressure_kpa + required_feed_delta_kpa

    transient = _simulate_feed_history(
        req,
        chamber_pressure_kpa=chamber_pressure_kpa,
        injector_dp_ratio=injector_dp_ratio,
        fuel_total_drop_kpa=fuel_total_drop_kpa,
        oxidizer_total_drop_kpa=oxidizer_total_drop_kpa,
        total_mass_flow_kg_s=total_mass_flow_kg_s,
        fuel_mass_flow_kg_s=fuel_mass_flow_kg_s,
        oxidizer_mass_flow_kg_s=oxidizer_mass_flow_kg_s,
        required_tank_pressure_kpa=required_tank_pressure_kpa,
    )
    time_history_rows = transient["time_history_rows"]
    transient_summary = transient["summary"]
    average_chamber_pressure_kpa = _average_history_value(time_history_rows, "chamber_pressure_kpa")
    average_required_feed_kpa = _average_history_value(time_history_rows, "required_feed_pressure_kpa")
    average_tank_pressure_kpa = _average_history_value(time_history_rows, "fuel_tank_pressure_kpa")
    average_pump_discharge_kpa = _average_history_value(time_history_rows, "pump_discharge_pressure_kpa")

    segment_rows: List[Dict[str, object]] = [
        {
            "segment": "fuel_branch_total",
            "estimated_pressure_drop_kpa": round(fuel_total_drop_kpa, 2),
            "minimum_margin_kpa": transient_summary["minimum_fuel_margin_kpa"],
            "status": "calculated",
            "velocity_m_s": round(fuel_branch["velocity_m_s"], 3),
            "reynolds": round(fuel_branch["reynolds"], 1),
        },
        {
            "segment": "oxidizer_branch_total",
            "estimated_pressure_drop_kpa": round(oxidizer_total_drop_kpa, 2),
            "minimum_margin_kpa": transient_summary["minimum_oxidizer_margin_kpa"],
            "status": "calculated",
            "velocity_m_s": round(oxidizer_branch["velocity_m_s"], 3),
            "reynolds": round(oxidizer_branch["reynolds"], 1),
        },
        {
            "segment": "injector_pressure_drop",
            "estimated_pressure_drop_kpa": round(injector_pressure_drop_kpa, 2),
            "status": "calculated",
        },
        {
            "segment": "required_feed_delta",
            "estimated_pressure_drop_kpa": round(required_feed_delta_kpa, 2),
            "minimum_margin_kpa": transient_summary["minimum_feed_margin_kpa"],
            "status": "calculated",
        },
        {
            "segment": "burn_time_feed_tailoff",
            "estimated_pressure_drop_kpa": round(
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
        "Validated request for reduced-order transient feed-pressure model.",
        "Solved branch losses with Darcy-Weisbach + minor-loss terms and injector closure.",
        "Integrated burn-time feed history with pressure-fed blowdown or pump-head control logic.",
    ]
    if upstream_context:
        trace.append("Upstream context keys: {0}".format(", ".join(sorted(upstream_context.keys()))))

    summary = {
        "total_pressure_drop_kpa": round(total_drop_kpa, 2),
        "model_status": "calculated",
        "quality_flag": "stage-2-transient-feed-v1",
        "estimated_chamber_pressure_kpa": round(chamber_pressure_kpa, 2),
        "injector_pressure_drop_kpa": round(injector_pressure_drop_kpa, 2),
        "fuel_branch_pressure_drop_kpa": round(fuel_total_drop_kpa, 2),
        "oxidizer_branch_pressure_drop_kpa": round(oxidizer_total_drop_kpa, 2),
        "required_feed_delta_kpa": round(required_feed_delta_kpa, 2),
        "required_tank_pressure_kpa": round(required_tank_pressure_kpa, 2),
        "estimated_propellant_mass_flow_kg_s": round(total_mass_flow_kg_s, 4),
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
            "solver_mode": "stage-2-transient-feed-v1",
            "input_schema_version": "1.1",
            "output_schema_version": "1.1",
        },
        "status": "ok",
        "payload": {
            "request": req,
            "summary": summary,
            "segment_rows": segment_rows,
            "time_history_rows": time_history_rows,
            "station_field_updates": station_field_updates,
        },
        "warnings": [
            "Reduced-order transient only: this is not yet a calibrated hydraulic network or valve-level feed simulation.",
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
