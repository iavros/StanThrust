import math
from typing import Dict, List, Mapping, Optional

from stanshock.design_model import (
    AMBIENT_TEMPERATURE_K,
    EngineDesign,
    MATERIAL_TEMPERATURE_LIMIT_K,
    PROPELLANT_DENSITY_KG_M3,
    clamp,
)


MATERIAL_THERMAL_CONDUCTIVITY_W_M_K = {
    "Aluminum 6061-T6": 167.0,
    "Aluminum 7075-T6": 130.0,
    "Stainless Steel 304": 16.2,
    "Stainless Steel 316": 14.6,
    "Carbon Steel 1018": 51.0,
    "Titanium Grade 5": 6.7,
    "Copper C110": 385.0,
    "Inconel 625": 9.8,
}

COOLANT_PROPERTIES = {
    "methane": {"cp_j_kg_k": 3500.0, "viscosity_pa_s": 0.00012, "conductivity_w_m_k": 0.20},
    "ethanol": {"cp_j_kg_k": 2440.0, "viscosity_pa_s": 0.00120, "conductivity_w_m_k": 0.171},
    "isopropyl alcohol": {"cp_j_kg_k": 2600.0, "viscosity_pa_s": 0.00210, "conductivity_w_m_k": 0.135},
}


def _value(mapping: Mapping[str, object], key: str, fallback: float) -> float:
    try:
        numeric = float(mapping.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(numeric):
        return fallback
    return numeric


def _round_payload(payload: Dict[str, object]) -> Dict[str, object]:
    rounded_payload: Dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, float):
            rounded_payload[key] = round(value, 6)
        else:
            rounded_payload[key] = value
    return rounded_payload


def _static_temperature_from_mach(total_temperature_k: float, gamma: float, mach: float) -> float:
    return total_temperature_k / max(1e-8, 1.0 + 0.5 * (gamma - 1.0) * mach * mach)


def _static_pressure_from_mach(total_pressure_kpa: float, gamma: float, mach: float) -> float:
    ratio = (1.0 + 0.5 * (gamma - 1.0) * mach * mach) ** (-gamma / (gamma - 1.0))
    return total_pressure_kpa * ratio


def _gas_viscosity_pa_s(temperature_k: float) -> float:
    reference_mu = 1.716e-5
    reference_t = 273.15
    sutherland = 110.4
    mu = reference_mu * (temperature_k / reference_t) ** 1.5 * (
        (reference_t + sutherland) / max(1e-6, temperature_k + sutherland)
    )
    return clamp(mu, 2.0e-5, 9.0e-5)


def _coolant_properties(fuel_name: str, density_kg_m3: float) -> Dict[str, float]:
    key = (fuel_name or "").strip().lower()
    base = dict(
        COOLANT_PROPERTIES.get(
            key,
            {"cp_j_kg_k": 2600.0, "viscosity_pa_s": 0.00120, "conductivity_w_m_k": 0.15},
        )
    )
    base["density_kg_m3"] = max(80.0, density_kg_m3)
    base["prandtl"] = (
        base["cp_j_kg_k"] * base["viscosity_pa_s"] / max(1e-9, base["conductivity_w_m_k"])
    )
    return base


def solve_gas_side_heat_transfer(
    chamber_pressure_kpa: float,
    chamber_temperature_k: float,
    mach: float,
    hydraulic_diameter_mm: float,
    gamma: float = 1.22,
    gas_constant_j_kg_k: float = 355.0,
) -> Dict[str, float]:
    """Solve gas-side convection from the local station state."""

    local_mach = max(0.01, float(mach))
    diameter_m = max(1e-5, float(hydraulic_diameter_mm) / 1000.0)
    static_temperature_k = _static_temperature_from_mach(chamber_temperature_k, gamma, local_mach)
    static_pressure_pa = _static_pressure_from_mach(chamber_pressure_kpa, gamma, local_mach) * 1000.0
    density_kg_m3 = static_pressure_pa / max(1e-9, gas_constant_j_kg_k * static_temperature_k)
    sonic_velocity_m_s = math.sqrt(max(1e-9, gamma * gas_constant_j_kg_k * static_temperature_k))
    velocity_m_s = local_mach * sonic_velocity_m_s
    viscosity_pa_s = _gas_viscosity_pa_s(static_temperature_k)
    conductivity_w_m_k = clamp(0.025 + 2.8e-5 * static_temperature_k, 0.045, 0.18)
    prandtl = 0.72
    reynolds = density_kg_m3 * velocity_m_s * diameter_m / max(1e-12, viscosity_pa_s)
    if reynolds < 2300.0:
        nusselt = 3.66 + 0.008 * math.sqrt(max(0.0, reynolds))
    else:
        nusselt = 0.026 * (reynolds ** 0.8) * (prandtl ** 0.4)
    heat_transfer_coefficient = nusselt * conductivity_w_m_k / diameter_m
    recovery_factor = math.sqrt(prandtl)
    recovery_temperature_k = static_temperature_k * (
        1.0 + recovery_factor * 0.5 * (gamma - 1.0) * local_mach * local_mach
    )

    return _round_payload(
        {
            "mach": local_mach,
            "static_temperature_k": static_temperature_k,
            "static_pressure_kpa": static_pressure_pa / 1000.0,
            "density_kg_m3": density_kg_m3,
            "velocity_m_s": velocity_m_s,
            "reynolds": reynolds,
            "nusselt": nusselt,
            "prandtl": prandtl,
            "conductivity_w_m_k": conductivity_w_m_k,
            "viscosity_pa_s": viscosity_pa_s,
            "heat_transfer_coefficient_w_m2_k": clamp(heat_transfer_coefficient, 150.0, 90000.0),
            "recovery_temperature_k": clamp(
                recovery_temperature_k,
                static_temperature_k,
                chamber_temperature_k * 1.05,
            ),
        }
    )


def solve_coolant_side_heat_transfer(
    coolant_mass_flow_kg_s: float,
    channel_count: float,
    channel_width_mm: float,
    channel_depth_mm: float,
    hydraulic_diameter_mm: float,
    coolant_density_kg_m3: float,
    coolant_cp_j_kg_k: float,
    coolant_viscosity_pa_s: float,
    coolant_conductivity_w_m_k: float,
) -> Dict[str, float]:
    """Solve coolant-side convection in rectangular regen channels."""

    channel_count_value = max(1.0, float(channel_count))
    width_m = max(1e-5, float(channel_width_mm) / 1000.0)
    depth_m = max(1e-5, float(channel_depth_mm) / 1000.0)
    hydraulic_diameter_m = max(
        1e-5,
        float(hydraulic_diameter_mm) / 1000.0
        if hydraulic_diameter_mm > 0.0
        else 2.0 * width_m * depth_m / (width_m + depth_m),
    )
    flow_area_m2 = channel_count_value * width_m * depth_m
    velocity_m_s = coolant_mass_flow_kg_s / max(1e-12, coolant_density_kg_m3 * flow_area_m2)
    reynolds = (
        coolant_density_kg_m3
        * velocity_m_s
        * hydraulic_diameter_m
        / max(1e-12, coolant_viscosity_pa_s)
    )
    prandtl = coolant_cp_j_kg_k * coolant_viscosity_pa_s / max(1e-12, coolant_conductivity_w_m_k)
    laminar_nusselt = 3.66
    turbulent_nusselt = 0.023 * (max(1.0, reynolds) ** 0.8) * (prandtl ** 0.4)
    if reynolds <= 2300.0:
        nusselt = laminar_nusselt
        regime = "laminar"
    elif reynolds >= 10000.0:
        nusselt = turbulent_nusselt
        regime = "turbulent"
    else:
        blend = (reynolds - 2300.0) / (10000.0 - 2300.0)
        nusselt = laminar_nusselt * (1.0 - blend) + turbulent_nusselt * blend
        regime = "transition"
    heat_transfer_coefficient = nusselt * coolant_conductivity_w_m_k / hydraulic_diameter_m

    return _round_payload(
        {
            "flow_area_m2": flow_area_m2,
            "velocity_m_s": velocity_m_s,
            "reynolds": reynolds,
            "prandtl": prandtl,
            "nusselt": nusselt,
            "regime": regime,
            "heat_transfer_coefficient_w_m2_k": clamp(heat_transfer_coefficient, 50.0, 120000.0),
        }
    )


def _solve_wall_section(
    section_name: str,
    chamber_pressure_kpa: float,
    chamber_temperature_k: float,
    gamma: float,
    gas_constant_j_kg_k: float,
    diameter_mm: float,
    length_mm: float,
    mach: float,
    wall_thickness_mm: float,
    wall_conductivity_w_m_k: float,
    material_limit_k: float,
    coolant_state: Optional[Dict[str, float]],
    coolant_bulk_temperature_k: float,
    film_effectiveness: float,
) -> Dict[str, object]:
    gas = solve_gas_side_heat_transfer(
        chamber_pressure_kpa=chamber_pressure_kpa,
        chamber_temperature_k=chamber_temperature_k,
        mach=mach,
        hydraulic_diameter_mm=diameter_mm,
        gamma=gamma,
        gas_constant_j_kg_k=gas_constant_j_kg_k,
    )
    film_reference_temperature_k = AMBIENT_TEMPERATURE_K if coolant_state is None else coolant_bulk_temperature_k
    recovery_temperature_k = float(gas["recovery_temperature_k"])
    effective_recovery_temperature_k = recovery_temperature_k - clamp(film_effectiveness, 0.0, 0.75) * (
        recovery_temperature_k - film_reference_temperature_k
    )
    area_m2 = math.pi * max(1e-5, diameter_mm / 1000.0) * max(1e-5, length_mm / 1000.0)
    gas_h = float(gas["heat_transfer_coefficient_w_m2_k"])
    wall_resistance = max(1e-6, wall_thickness_mm / 1000.0) / max(1e-6, wall_conductivity_w_m_k)

    if coolant_state is not None:
        coolant = solve_coolant_side_heat_transfer(
            coolant_mass_flow_kg_s=coolant_state["mass_flow_kg_s"],
            channel_count=coolant_state["channel_count"],
            channel_width_mm=coolant_state["channel_width_mm"],
            channel_depth_mm=coolant_state["channel_depth_mm"],
            hydraulic_diameter_mm=coolant_state["hydraulic_diameter_mm"],
            coolant_density_kg_m3=coolant_state["density_kg_m3"],
            coolant_cp_j_kg_k=coolant_state["cp_j_kg_k"],
            coolant_viscosity_pa_s=coolant_state["viscosity_pa_s"],
            coolant_conductivity_w_m_k=coolant_state["conductivity_w_m_k"],
        )
        coolant_h = float(coolant["heat_transfer_coefficient_w_m2_k"])
        sink_temperature_k = coolant_bulk_temperature_k
        sink_resistance = 1.0 / max(1e-6, coolant_h)
        heat_sink = "regen-coolant"
    else:
        coolant = {}
        coolant_h = 12.0
        sink_temperature_k = AMBIENT_TEMPERATURE_K
        sink_resistance = 1.0 / coolant_h
        heat_sink = "ambient"

    total_resistance = 1.0 / max(1e-6, gas_h) + wall_resistance + sink_resistance
    heat_flux_w_m2 = max(0.0, (effective_recovery_temperature_k - sink_temperature_k) / total_resistance)
    heat_load_w = heat_flux_w_m2 * area_m2
    hot_wall_temperature_k = effective_recovery_temperature_k - heat_flux_w_m2 / max(1e-6, gas_h)
    cold_wall_temperature_k = sink_temperature_k + heat_flux_w_m2 * sink_resistance
    coolant_temperature_rise_k = 0.0
    coolant_outlet_temperature_k = coolant_bulk_temperature_k
    if coolant_state is not None:
        coolant_temperature_rise_k = heat_load_w / max(
            1e-6,
            coolant_state["mass_flow_kg_s"] * coolant_state["cp_j_kg_k"],
        )
        coolant_outlet_temperature_k = coolant_bulk_temperature_k + coolant_temperature_rise_k

    return {
        "name": section_name,
        "diameter_mm": round(diameter_mm, 4),
        "length_mm": round(length_mm, 4),
        "mach": round(mach, 5),
        "heat_sink": heat_sink,
        "film_effectiveness": round(film_effectiveness, 5),
        "gas": gas,
        "coolant": coolant,
        "gas_side_h_w_m2_k": round(gas_h, 4),
        "coolant_side_h_w_m2_k": round(coolant_h, 4),
        "wall_conductivity_w_m_k": round(wall_conductivity_w_m_k, 4),
        "wall_thickness_mm": round(wall_thickness_mm, 4),
        "effective_recovery_temperature_k": round(effective_recovery_temperature_k, 4),
        "heat_flux_w_m2": round(heat_flux_w_m2, 4),
        "heat_load_kw": round(heat_load_w / 1000.0, 6),
        "hot_wall_temperature_k": round(hot_wall_temperature_k, 4),
        "cold_wall_temperature_k": round(cold_wall_temperature_k, 4),
        "coolant_inlet_temperature_k": round(coolant_bulk_temperature_k, 4),
        "coolant_outlet_temperature_k": round(coolant_outlet_temperature_k, 4),
        "coolant_temperature_rise_k": round(coolant_temperature_rise_k, 4),
        "thermal_margin_k": round(material_limit_k - hot_wall_temperature_k, 4),
        "thermal_margin_ratio": round(material_limit_k / max(1e-6, hot_wall_temperature_k), 6),
    }


def solve_engine_heat_transfer(
    design: EngineDesign,
    combustion_result: Optional[Mapping[str, object]] = None,
) -> Dict[str, object]:
    """Solve a reduced-order wall heat-transfer model for the current engine."""

    values = dict(design.derived.engineering_values)
    summary = dict(combustion_result.get("summary", {})) if combustion_result else {}
    axial_profile = list(combustion_result.get("axial_profile", [])) if combustion_result else []
    chamber_pressure_kpa = _value(summary, "chamber_pressure_kpa", _value(values, "chamber_pressure_kpa", 1200.0))
    chamber_temperature_k = _value(summary, "chamber_temperature_k", 3350.0)
    gamma = _value(summary, "gamma", 1.22)
    gas_constant_j_kg_k = _value(summary, "gas_constant_j_kgk", 355.0)
    chamber_diameter_mm = _value(values, "chamber_inner_diameter_mm", design.inputs.chamber_diameter_mm)
    throat_diameter_mm = _value(values, "nozzle_throat_diameter_mm", chamber_diameter_mm * 0.45)
    nozzle_exit_diameter_mm = _value(values, "nozzle_inner_diameter_mm", design.inputs.nozzle_diameter_mm)
    chamber_length_mm = max(1.0, float(design.derived.chamber_length_mm))
    nozzle_length_mm = max(1.0, float(design.derived.nozzle_length_mm))
    chamber_material = design.inputs.chamber_material
    nozzle_material = design.inputs.nozzle_material
    chamber_limit_k = MATERIAL_TEMPERATURE_LIMIT_K.get(chamber_material, 900.0)
    nozzle_limit_k = MATERIAL_TEMPERATURE_LIMIT_K.get(nozzle_material, 900.0)
    chamber_conductivity = MATERIAL_THERMAL_CONDUCTIVITY_W_M_K.get(chamber_material, 16.0)
    nozzle_conductivity = MATERIAL_THERMAL_CONDUCTIVITY_W_M_K.get(nozzle_material, 16.0)
    chamber_wall_mm = _value(values, "regen_inner_wall_thickness_mm", 0.0) if design.inputs.regen_cooling else 0.0
    if chamber_wall_mm <= 0.0:
        chamber_wall_mm = _value(values, "chamber_wall_thickness_mm", 1.0)
    nozzle_wall_mm = _value(values, "regen_inner_wall_thickness_mm", 0.0) if design.inputs.regen_cooling else 0.0
    if nozzle_wall_mm <= 0.0:
        nozzle_wall_mm = _value(values, "nozzle_structural_wall_thickness_mm", 1.0)

    fuel_density = PROPELLANT_DENSITY_KG_M3.get(
        design.inputs.fuel_name.strip().lower(),
        _value(values, "fuel_density_kg_m3", 750.0),
    )
    fuel_coolant = _coolant_properties(design.inputs.fuel_name, fuel_density)
    coolant_state: Optional[Dict[str, float]] = None
    if design.inputs.regen_cooling and _value(values, "regen_channel_count", 0.0) > 0.0:
        regen_coolant_mass_flow_kg_s = _value(values, "regen_coolant_mass_flow_kg_s", 0.0)
        if regen_coolant_mass_flow_kg_s <= 1e-4:
            propellant_mass_flow_kg_s = _value(
                summary,
                "mass_flow_kg_s",
                _value(values, "propellant_mass_flow_kg_s", 0.05),
            )
            fuel_mass_flow_kg_s = propellant_mass_flow_kg_s / max(1.0, 1.0 + design.inputs.mixture_ratio)
            regen_coolant_mass_flow_kg_s = clamp(
                fuel_mass_flow_kg_s * (0.22 + 0.12 * design.oxidizer.thermal_severity),
                0.003,
                max(0.003, fuel_mass_flow_kg_s * 0.85),
            )
        coolant_state = {
            "mass_flow_kg_s": max(1e-5, regen_coolant_mass_flow_kg_s),
            "channel_count": max(1.0, _value(values, "regen_channel_count", 1.0)),
            "channel_width_mm": max(0.05, _value(values, "regen_channel_width_mm", 1.0)),
            "channel_depth_mm": max(0.05, _value(values, "regen_channel_depth_mm", 1.0)),
            "hydraulic_diameter_mm": max(0.05, _value(values, "regen_hydraulic_diameter_mm", 1.0)),
            "density_kg_m3": fuel_coolant["density_kg_m3"],
            "cp_j_kg_k": fuel_coolant["cp_j_kg_k"],
            "viscosity_pa_s": fuel_coolant["viscosity_pa_s"],
            "conductivity_w_m_k": fuel_coolant["conductivity_w_m_k"],
        }

    film_coverage = _value(values, "film_coverage_fraction", 0.0) if design.inputs.film_cooling else 0.0
    film_base = clamp(film_coverage * 0.55, 0.0, 0.65)
    profile_mach = [float(row.get("mach", 0.0)) for row in axial_profile if row.get("mach") is not None]
    exit_mach = max(profile_mach) if profile_mach else _value(summary, "exit_mach", 2.0)
    sections = [
        {
            "name": "Nozzle Exit Plane",
            "diameter_mm": nozzle_exit_diameter_mm,
            "length_mm": nozzle_length_mm * 0.35,
            "mach": max(1.05, exit_mach),
            "wall_mm": nozzle_wall_mm,
            "conductivity": nozzle_conductivity,
            "limit_k": nozzle_limit_k,
            "film": film_base * 0.25,
        },
        {
            "name": "Throat Region",
            "diameter_mm": throat_diameter_mm,
            "length_mm": max(throat_diameter_mm * 1.8, nozzle_length_mm * 0.18),
            "mach": 1.0,
            "wall_mm": nozzle_wall_mm,
            "conductivity": nozzle_conductivity,
            "limit_k": nozzle_limit_k,
            "film": film_base * 0.45,
        },
        {
            "name": "Chamber Mid",
            "diameter_mm": chamber_diameter_mm,
            "length_mm": chamber_length_mm * 0.8,
            "mach": 0.12,
            "wall_mm": chamber_wall_mm,
            "conductivity": chamber_conductivity,
            "limit_k": chamber_limit_k,
            "film": film_base * 0.85,
        },
    ]

    coolant_temperature_k = AMBIENT_TEMPERATURE_K
    solved_sections: List[Dict[str, object]] = []
    for section in sections:
        solved = _solve_wall_section(
            section_name=str(section["name"]),
            chamber_pressure_kpa=chamber_pressure_kpa,
            chamber_temperature_k=chamber_temperature_k,
            gamma=gamma,
            gas_constant_j_kg_k=gas_constant_j_kg_k,
            diameter_mm=float(section["diameter_mm"]),
            length_mm=float(section["length_mm"]),
            mach=float(section["mach"]),
            wall_thickness_mm=float(section["wall_mm"]),
            wall_conductivity_w_m_k=float(section["conductivity"]),
            material_limit_k=float(section["limit_k"]),
            coolant_state=coolant_state,
            coolant_bulk_temperature_k=coolant_temperature_k,
            film_effectiveness=float(section["film"]),
        )
        solved_sections.append(solved)
        if coolant_state is not None:
            coolant_temperature_k = float(solved["coolant_outlet_temperature_k"])

    total_heat_load_kw = sum(float(section["heat_load_kw"]) for section in solved_sections)
    max_hot_wall_temperature_k = max(float(section["hot_wall_temperature_k"]) for section in solved_sections)
    min_thermal_margin_k = min(float(section["thermal_margin_k"]) for section in solved_sections)
    limiting_section = min(solved_sections, key=lambda section: float(section["thermal_margin_k"]))
    status = "calculated-regenerative" if coolant_state is not None else "calculated-passive"
    note = (
        "Regenerative channels use the calculated channel geometry and fuel coolant flow."
        if coolant_state is not None
        else "Passive wall solve uses gas-side convection, wall conduction, and ambient outer convection."
    )
    if design.inputs.film_cooling:
        note += " Film cooling lowers the gas-side recovery temperature near the wall and decays downstream."

    return {
        "metadata": {
            "solver_name": "Heat Transfer Solver",
            "solver_version": "1.0",
            "model": "gas-wall-coolant-resistance-network",
        },
        "status": status,
        "summary": {
            "status": status,
            "total_heat_load_kw": round(total_heat_load_kw, 4),
            "max_hot_wall_temperature_k": round(max_hot_wall_temperature_k, 3),
            "min_thermal_margin_k": round(min_thermal_margin_k, 3),
            "coolant_inlet_temperature_k": AMBIENT_TEMPERATURE_K,
            "coolant_outlet_temperature_k": round(coolant_temperature_k, 3),
            "limiting_section": str(limiting_section["name"]),
            "film_cooling_active": bool(design.inputs.film_cooling),
            "regen_cooling_active": bool(coolant_state is not None),
            "note": note,
        },
        "coolant": coolant_state or {},
        "sections": solved_sections,
    }
