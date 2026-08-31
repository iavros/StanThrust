"""Stationwise conjugate wall and coolant heat-transfer solve."""

import math
from typing import Dict, List, Mapping, Optional

from stanthrust.boundary_layer_solver import (
    DEFAULT_WALL_NORMAL_NODE_COUNT,
    apply_axisymmetric_thermal_march,
)
from stanthrust.design_model import (
    AMBIENT_TEMPERATURE_K,
    EngineDesign,
    clamp,
)
from stanthrust.fluid_properties import (
    PROPERTY_SOURCE as COOLANT_PROPERTY_SOURCE,
)
from stanthrust.fluid_properties import (
    coolant_default_inlet_temperature_k,
    coolant_phase_envelope,
    coolant_property_state,
    coolant_single_phase_pressure_requirement_kpa,
)
from stanthrust.material_properties import (
    MATERIAL_TEMPERATURE_LIMIT_K,
    material_property_state,
    material_thermal_conductivity,
)
from stanthrust.moc_nozzle_solver import (
    static_pressure_from_mach,
    static_temperature_from_mach,
)

NASA_TP_3380_NUSSELT_COEFFICIENT_RANGE = (0.023, 0.026)
GAS_NUSSELT_COEFFICIENT = sum(NASA_TP_3380_NUSSELT_COEFFICIENT_RANGE) / 2.0
RELAMINARIZATION_ACCELERATION_THRESHOLD = 2.0e-6
TRANSITION_MOMENTUM_THICKNESS_REYNOLDS = 400.0


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


def _gas_viscosity_pa_s(temperature_k: float) -> float:
    reference_mu = 1.716e-5
    reference_t = 273.15
    sutherland = 110.4
    mu = reference_mu * (temperature_k / reference_t) ** 1.5 * (
        (reference_t + sutherland) / max(1e-6, temperature_k + sutherland)
    )
    return clamp(mu, 2.0e-5, 9.0e-5)


def solve_gas_side_heat_transfer(
    chamber_pressure_kpa: float,
    chamber_temperature_k: float,
    mach: float,
    hydraulic_diameter_mm: float,
    gamma: float = 1.22,
    gas_constant_j_kg_k: float = 355.0,
    viscosity_pa_s: Optional[float] = None,
    conductivity_w_m_k: Optional[float] = None,
    prandtl: Optional[float] = None,
    static_temperature_k: Optional[float] = None,
    static_pressure_kpa: Optional[float] = None,
) -> Dict[str, object]:
    """Solve gas-side convection from the local station state."""

    local_mach = max(0.01, float(mach))
    diameter_m = max(1e-5, float(hydraulic_diameter_mm) / 1000.0)
    local_static_temperature_k = (
        max(1.0, float(static_temperature_k))
        if static_temperature_k is not None
        else static_temperature_from_mach(chamber_temperature_k, local_mach, gamma)
    )
    local_static_pressure_kpa = (
        max(0.001, float(static_pressure_kpa))
        if static_pressure_kpa is not None
        else static_pressure_from_mach(chamber_pressure_kpa, local_mach, gamma)
    )
    static_pressure_pa = local_static_pressure_kpa * 1000.0
    density_kg_m3 = static_pressure_pa / max(
        1e-9, gas_constant_j_kg_k * local_static_temperature_k
    )
    sonic_velocity_m_s = math.sqrt(
        max(1e-9, gamma * gas_constant_j_kg_k * local_static_temperature_k)
    )
    velocity_m_s = local_mach * sonic_velocity_m_s
    local_viscosity_pa_s = (
        max(1e-8, float(viscosity_pa_s))
        if viscosity_pa_s is not None
        else _gas_viscosity_pa_s(local_static_temperature_k)
    )
    local_conductivity_w_m_k = (
        max(1e-5, float(conductivity_w_m_k))
        if conductivity_w_m_k is not None
        else clamp(0.025 + 2.8e-5 * local_static_temperature_k, 0.045, 0.18)
    )
    local_prandtl = max(0.05, float(prandtl)) if prandtl is not None else 0.72
    transport_source = "provided-local-state" if any(
        item is not None for item in (viscosity_pa_s, conductivity_w_m_k, prandtl)
    ) else "internal-temperature-closure"
    reynolds = density_kg_m3 * velocity_m_s * diameter_m / max(1e-12, local_viscosity_pa_s)
    if reynolds < 2300.0:
        nusselt = 3.66 + 0.008 * math.sqrt(max(0.0, reynolds))
        nusselt_lower = nusselt
        nusselt_upper = nusselt
        correlation_coefficient = None
    else:
        correlation_coefficient = GAS_NUSSELT_COEFFICIENT
        base_nusselt = (reynolds ** 0.8) * (local_prandtl ** 0.3)
        nusselt = correlation_coefficient * base_nusselt
        nusselt_lower = NASA_TP_3380_NUSSELT_COEFFICIENT_RANGE[0] * base_nusselt
        nusselt_upper = NASA_TP_3380_NUSSELT_COEFFICIENT_RANGE[1] * base_nusselt
    heat_transfer_coefficient = nusselt * local_conductivity_w_m_k / diameter_m
    recovery_factor = math.sqrt(local_prandtl)
    recovery_temperature_k = local_static_temperature_k * (
        1.0 + recovery_factor * 0.5 * (gamma - 1.0) * local_mach * local_mach
    )

    return _round_payload(
        {
            "mach": local_mach,
            "static_temperature_k": local_static_temperature_k,
            "static_pressure_kpa": static_pressure_pa / 1000.0,
            "density_kg_m3": density_kg_m3,
            "velocity_m_s": velocity_m_s,
            "reynolds": reynolds,
            "nusselt": nusselt,
            "nusselt_lower": nusselt_lower,
            "nusselt_upper": nusselt_upper,
            "nusselt_correlation_coefficient": correlation_coefficient,
            "correlation_reference": "NASA TP-3380 chamber/throat calorimeter correlation",
            "prandtl": local_prandtl,
            "conductivity_w_m_k": local_conductivity_w_m_k,
            "viscosity_pa_s": local_viscosity_pa_s,
            "transport_source": transport_source,
            "heat_transfer_coefficient_w_m2_k": max(1e-12, heat_transfer_coefficient),
            "heat_transfer_coefficient_lower_w_m2_k": max(
                1e-12, nusselt_lower * local_conductivity_w_m_k / diameter_m
            ),
            "heat_transfer_coefficient_upper_w_m2_k": max(
                1e-12, nusselt_upper * local_conductivity_w_m_k / diameter_m
            ),
            "recovery_temperature_k": clamp(
                recovery_temperature_k,
                local_static_temperature_k,
                chamber_temperature_k * 1.05,
            ),
        }
    )


def march_nozzle_boundary_layer(
    stations: List[Mapping[str, object]],
    turbulent_states: List[Mapping[str, object]],
    throat_index: int,
) -> List[Dict[str, object]]:
    """Select divergent-nozzle heat transfer from local boundary-layer state."""

    if len(stations) != len(turbulent_states):
        raise ValueError("Boundary-layer stations and gas states must have equal length.")
    if not stations:
        return []

    start_index = max(0, min(int(throat_index), len(stations) - 1))
    results: List[Dict[str, object]] = []
    wall_distance_m = 0.0
    velocity_fifth_integral = 0.0
    relaminarized = False
    previous_regime = "turbulent"

    for index, (station, turbulent_state) in enumerate(zip(stations, turbulent_states)):
        state = dict(turbulent_state)
        turbulent_h = float(state["heat_transfer_coefficient_w_m2_k"])
        turbulent_lower = float(state["heat_transfer_coefficient_lower_w_m2_k"])
        turbulent_upper = float(state["heat_transfer_coefficient_upper_w_m2_k"])
        if index < start_index:
            state.update(
                {
                    "boundary_layer_regime": "turbulent-correlation",
                    "boundary_layer_model": "NASA-TP-3380-chamber-throat-closure",
                    "wall_distance_from_throat_m": 0.0,
                    "momentum_thickness_mm": None,
                    "momentum_thickness_reynolds": None,
                    "laminar_heat_transfer_coefficient_w_m2_k": None,
                    "turbulent_heat_transfer_coefficient_w_m2_k": turbulent_h,
                    "relaminarized": False,
                }
            )
            results.append(state)
            continue

        wall_step_m = 0.0
        if index > start_index:
            previous_station = stations[index - 1]
            dx_m = (float(station["x_mm"]) - float(previous_station["x_mm"])) / 1000.0
            dr_m = (
                float(station.get("radius_mm", 0.0))
                - float(previous_station.get("radius_mm", 0.0))
            ) / 1000.0
            wall_step_m = math.hypot(dx_m, dr_m)
            wall_distance_m += wall_step_m

        velocity_m_s = max(1e-8, float(station["gas_velocity_m_s"]))
        kinematic_viscosity_m2_s = max(
            1e-12, float(station["gas_kinematic_viscosity_m2_s"])
        )
        if index > start_index:
            previous_velocity_m_s = max(
                1e-8, float(stations[index - 1]["gas_velocity_m_s"])
            )
            velocity_fifth_integral += 0.5 * (
                previous_velocity_m_s**5 + velocity_m_s**5
            ) * wall_step_m
        reynolds_s = velocity_m_s * wall_distance_m / kinematic_viscosity_m2_s
        if wall_distance_m > 0.0 and reynolds_s > 0.0 and velocity_fifth_integral > 0.0:
            momentum_thickness_m = math.sqrt(
                0.45
                * kinematic_viscosity_m2_s
                * velocity_fifth_integral
                / velocity_m_s**6
            )
            momentum_thickness_reynolds = (
                velocity_m_s * momentum_thickness_m / kinematic_viscosity_m2_s
            )
            conductivity_w_m_k = max(1e-12, float(state["conductivity_w_m_k"]))
            prandtl = max(1e-8, float(state["prandtl"]))
            laminar_nusselt = 0.332 * math.sqrt(reynolds_s) * prandtl ** (1.0 / 3.0)
            laminar_h = laminar_nusselt * conductivity_w_m_k / wall_distance_m
        else:
            momentum_thickness_m = 0.0
            momentum_thickness_reynolds = 0.0
            laminar_nusselt = float(state["nusselt"])
            laminar_h = turbulent_h

        acceleration_parameter = float(station.get("acceleration_parameter", 0.0))
        velocity_gradient_s = float(station.get("velocity_gradient_s", 0.0))
        favorable_gradient = velocity_gradient_s > 0.0
        thwaites_pressure_gradient_parameter = (
            momentum_thickness_m**2
            * velocity_gradient_s
            / kinematic_viscosity_m2_s
        )
        if (
            favorable_gradient
            and acceleration_parameter >= RELAMINARIZATION_ACCELERATION_THRESHOLD
        ):
            relaminarized = True
        elif not favorable_gradient:
            relaminarized = False

        if index == start_index:
            regime = "throat-anchor"
            effective_h = turbulent_h
        elif relaminarized:
            regime = (
                "relaminarized"
                if previous_regime != "turbulent"
                else "relaminarizing"
            )
            effective_h = laminar_h
        elif momentum_thickness_reynolds < TRANSITION_MOMENTUM_THICKNESS_REYNOLDS:
            regime = "laminar"
            effective_h = laminar_h
        else:
            regime = (
                "transition-onset"
                if previous_regime in {"laminar", "relaminarized"}
                else "turbulent"
            )
            effective_h = turbulent_h

        lower_h = min(laminar_h, turbulent_lower)
        upper_h = max(laminar_h, turbulent_upper)
        effective_nusselt = (
            effective_h
            * max(1e-8, float(station.get("diameter_mm", 1.0)))
            / 1000.0
            / max(1e-12, float(state["conductivity_w_m_k"]))
        )
        state.update(
            {
                "nusselt": effective_nusselt,
                "heat_transfer_coefficient_w_m2_k": effective_h,
                "heat_transfer_coefficient_lower_w_m2_k": lower_h,
                "heat_transfer_coefficient_upper_w_m2_k": upper_h,
                "boundary_layer_regime": regime,
                "boundary_layer_model": "momentum-thickness-transition-and-relaminarization",
                "wall_distance_from_throat_m": wall_distance_m,
                "momentum_thickness_mm": momentum_thickness_m * 1000.0,
                "momentum_thickness_reynolds": momentum_thickness_reynolds,
                "thwaites_pressure_gradient_parameter": thwaites_pressure_gradient_parameter,
                "transition_momentum_thickness_reynolds": TRANSITION_MOMENTUM_THICKNESS_REYNOLDS,
                "acceleration_parameter": acceleration_parameter,
                "relaminarization_acceleration_threshold": RELAMINARIZATION_ACCELERATION_THRESHOLD,
                "laminar_nusselt": laminar_nusselt,
                "laminar_heat_transfer_coefficient_w_m2_k": laminar_h,
                "turbulent_heat_transfer_coefficient_w_m2_k": turbulent_h,
                "relaminarized": relaminarized,
                "regime_uncertainty_basis": "laminar-to-turbulent-model-envelope",
            }
        )
        results.append(_round_payload(state))
        previous_regime = "turbulent" if regime in {"turbulent", "transition-onset"} else regime

    return apply_axisymmetric_thermal_march(
        stations,
        results,
        start_index,
        wall_normal_node_count=DEFAULT_WALL_NORMAL_NODE_COUNT,
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
    diameter_mm: float,
    length_mm: float,
    wall_thickness_mm: float,
    wall_conductivity_w_m_k: float,
    material_name: str,
    material_limit_k: float,
    coolant_state: Optional[Dict[str, float]],
    coolant_bulk_temperature_k: float,
    film_effectiveness: float,
    gas_side_state: Mapping[str, object],
) -> Dict[str, object]:
    gas = dict(gas_side_state)
    film_reference_temperature_k = AMBIENT_TEMPERATURE_K if coolant_state is None else coolant_bulk_temperature_k
    recovery_temperature_k = float(gas["recovery_temperature_k"])
    effective_recovery_temperature_k = recovery_temperature_k - clamp(film_effectiveness, 0.0, 0.75) * (
        recovery_temperature_k - film_reference_temperature_k
    )
    area_m2 = math.pi * max(1e-5, diameter_mm / 1000.0) * max(1e-5, length_mm / 1000.0)
    gas_h = float(gas["heat_transfer_coefficient_w_m2_k"])
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
        coolant.update(
            {
                "fluid": coolant_state.get("fluid"),
                "phase": coolant_state.get("phase"),
                "density_kg_m3": coolant_state["density_kg_m3"],
                "cp_j_kg_k": coolant_state["cp_j_kg_k"],
                "viscosity_pa_s": coolant_state["viscosity_pa_s"],
                "conductivity_w_m_k": coolant_state["conductivity_w_m_k"],
                "property_backend": coolant_state.get("backend"),
                "property_backend_version": coolant_state.get("backend_version"),
            }
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

    wall_conductivity = wall_conductivity_w_m_k
    hot_wall_temperature_k = effective_recovery_temperature_k
    cold_wall_temperature_k = sink_temperature_k
    heat_flux_w_m2 = 0.0
    for _ in range(6):
        wall_resistance = max(1e-6, wall_thickness_mm / 1000.0) / max(1e-6, wall_conductivity)
        total_resistance = 1.0 / max(1e-6, gas_h) + wall_resistance + sink_resistance
        heat_flux_w_m2 = max(0.0, (effective_recovery_temperature_k - sink_temperature_k) / total_resistance)
        hot_wall_temperature_k = effective_recovery_temperature_k - heat_flux_w_m2 / max(1e-6, gas_h)
        cold_wall_temperature_k = sink_temperature_k + heat_flux_w_m2 * sink_resistance
        mean_wall_temperature_k = 0.5 * (hot_wall_temperature_k + cold_wall_temperature_k)
        updated_conductivity = material_thermal_conductivity(material_name, mean_wall_temperature_k)
        if abs(updated_conductivity - wall_conductivity) <= 1e-5 * max(1.0, wall_conductivity):
            wall_conductivity = updated_conductivity
            break
        wall_conductivity = 0.5 * (wall_conductivity + updated_conductivity)
    heat_load_w = heat_flux_w_m2 * area_m2
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
        "mach": round(float(gas["mach"]), 5),
        "heat_sink": heat_sink,
        "film_effectiveness": round(film_effectiveness, 5),
        "gas": gas,
        "coolant": coolant,
        "gas_side_h_w_m2_k": round(gas_h, 4),
        "coolant_side_h_w_m2_k": round(coolant_h, 4),
        "wall_conductivity_w_m_k": round(wall_conductivity, 4),
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
    """March conjugate wall heat transfer over the solved axial geometry."""

    values = dict(design.derived.engineering_values)
    summary = dict(combustion_result.get("summary", {})) if combustion_result else {}
    flow_profile = list(combustion_result.get("axial_profile", [])) if combustion_result else []
    chamber_pressure_kpa = _value(summary, "chamber_pressure_kpa", _value(values, "chamber_pressure_kpa", 1200.0))
    chamber_temperature_k = _value(summary, "chamber_temperature_k", 3350.0)
    gamma = _value(summary, "gamma", 1.22)
    gas_constant_j_kg_k = _value(summary, "gas_constant_j_kgk", 355.0)
    chamber_length_mm = max(1.0, float(design.derived.chamber_length_mm))
    nozzle_length_mm = max(1.0, float(design.derived.nozzle_length_mm))
    total_length_mm = chamber_length_mm + nozzle_length_mm
    chamber_diameter_mm = _value(values, "chamber_inner_diameter_mm", design.inputs.chamber_diameter_mm)
    throat_diameter_mm = _value(values, "nozzle_throat_diameter_mm", chamber_diameter_mm * 0.45)
    chamber_wall_mm = _value(values, "chamber_wall_thickness_mm", 1.0)
    nozzle_wall_mm = _value(values, "nozzle_structural_wall_thickness_mm", 1.0)
    if design.inputs.regen_cooling:
        common_inner_wall = _value(values, "regen_inner_wall_thickness_mm", 0.0)
        if common_inner_wall > 0.0:
            chamber_wall_mm = max(chamber_wall_mm, common_inner_wall)
            nozzle_wall_mm = max(nozzle_wall_mm, common_inner_wall)

    def flow_at(nozzle_x_mm: float) -> Dict[str, float]:
        if not flow_profile:
            fraction = clamp(nozzle_x_mm / nozzle_length_mm, 0.0, 1.0)
            return {"mach": 0.12 + 1.88 * fraction}
        rows = sorted(flow_profile, key=lambda row: _value(row, "x_mm", 0.0))
        if nozzle_x_mm <= _value(rows[0], "x_mm", 0.0):
            return {
                key: _value(rows[0], key, 0.0)
                for key in (
                    "mach",
                    "pressure_kpa",
                    "temperature_k",
                    "gas_viscosity_pa_s",
                    "gas_conductivity_w_m_k",
                    "gas_prandtl",
                )
            }
        for left, right in zip(rows, rows[1:]):
            x0 = _value(left, "x_mm", 0.0)
            x1 = _value(right, "x_mm", x0)
            if nozzle_x_mm <= x1:
                blend = clamp((nozzle_x_mm - x0) / max(1e-9, x1 - x0), 0.0, 1.0)
                return {
                    key: _value(left, key, 0.0) * (1.0 - blend) + _value(right, key, 0.0) * blend
                    for key in (
                        "mach",
                        "pressure_kpa",
                        "temperature_k",
                        "gas_viscosity_pa_s",
                        "gas_conductivity_w_m_k",
                        "gas_prandtl",
                    )
                }
        return {
            key: _value(rows[-1], key, 0.0)
            for key in (
                "mach",
                "pressure_kpa",
                "temperature_k",
                "gas_viscosity_pa_s",
                "gas_conductivity_w_m_k",
                "gas_prandtl",
            )
        }

    stations: List[Dict[str, object]] = []
    chamber_station_count = max(25, min(81, len(flow_profile) // 2 if flow_profile else 31))
    chamber_mach = max(0.03, min(0.25, _value(flow_profile[0], "mach", 0.12) if flow_profile else 0.12))
    chamber_transport = flow_at(0.0) if flow_profile else {}
    for index in range(chamber_station_count):
        x_mm = chamber_length_mm * index / max(1, chamber_station_count - 1)
        stations.append(
            {
                "x_mm": x_mm,
                "diameter_mm": chamber_diameter_mm,
                "mach": chamber_mach,
                "gas_viscosity_pa_s": _value(chamber_transport, "gas_viscosity_pa_s", 0.0),
                "gas_conductivity_w_m_k": _value(
                    chamber_transport, "gas_conductivity_w_m_k", 0.0
                ),
                "gas_prandtl": _value(chamber_transport, "gas_prandtl", 0.0),
                "flow_pressure_kpa": _value(
                    chamber_transport, "pressure_kpa", chamber_pressure_kpa
                ),
                "flow_temperature_k": _value(
                    chamber_transport, "temperature_k", chamber_temperature_k
                ),
            }
        )
    contour = sorted(design.derived.nozzle_contour_points, key=lambda row: _value(row, "x_mm", 0.0))
    for point in contour:
        nozzle_x = _value(point, "x_mm", 0.0)
        local_flow = flow_at(nozzle_x)
        stations.append(
            {
                "x_mm": chamber_length_mm + nozzle_x,
                "diameter_mm": 2.0 * _value(point, "radius_mm", throat_diameter_mm * 0.5),
                "mach": max(0.01, _value(local_flow, "mach", 0.12)),
                "flow_pressure_kpa": _value(local_flow, "pressure_kpa", chamber_pressure_kpa),
                "flow_temperature_k": _value(local_flow, "temperature_k", chamber_temperature_k),
                "gas_viscosity_pa_s": _value(local_flow, "gas_viscosity_pa_s", 0.0),
                "gas_conductivity_w_m_k": _value(
                    local_flow, "gas_conductivity_w_m_k", 0.0
                ),
                "gas_prandtl": _value(local_flow, "gas_prandtl", 0.0),
            }
        )
    stations.sort(key=lambda row: float(row["x_mm"]))
    deduplicated: List[Dict[str, object]] = []
    for row in stations:
        if deduplicated and abs(float(row["x_mm"]) - float(deduplicated[-1]["x_mm"])) < 1e-6:
            deduplicated[-1] = row
        else:
            deduplicated.append(row)
    stations = deduplicated

    for station in stations:
        local_temperature_k = max(
            1.0, _value(station, "flow_temperature_k", chamber_temperature_k)
        )
        local_pressure_pa = max(
            1.0, _value(station, "flow_pressure_kpa", chamber_pressure_kpa) * 1000.0
        )
        local_mach = max(0.01, _value(station, "mach", 0.12))
        local_viscosity_pa_s = _value(station, "gas_viscosity_pa_s", 0.0)
        if local_viscosity_pa_s <= 0.0:
            local_viscosity_pa_s = _gas_viscosity_pa_s(local_temperature_k)
        density_kg_m3 = local_pressure_pa / max(
            1e-12, gas_constant_j_kg_k * local_temperature_k
        )
        station["gas_velocity_m_s"] = local_mach * math.sqrt(
            max(1e-12, gamma * gas_constant_j_kg_k * local_temperature_k)
        )
        station["gas_kinematic_viscosity_m2_s"] = local_viscosity_pa_s / max(
            1e-12, density_kg_m3
        )

    for index, station in enumerate(stations):
        if index == 0:
            left = station
            right = stations[index + 1]
        elif index == len(stations) - 1:
            left = stations[index - 1]
            right = station
        else:
            left = stations[index - 1]
            right = stations[index + 1]
        dx_m = (float(right["x_mm"]) - float(left["x_mm"])) / 1000.0
        velocity_gradient_s = (
            float(right["gas_velocity_m_s"]) - float(left["gas_velocity_m_s"])
        ) / max(1e-12, dx_m)
        velocity_m_s = max(1e-6, float(station["gas_velocity_m_s"]))
        acceleration_parameter = (
            float(station["gas_kinematic_viscosity_m2_s"])
            * velocity_gradient_s
            / (velocity_m_s * velocity_m_s)
        )
        station["velocity_gradient_s"] = velocity_gradient_s
        station["acceleration_parameter"] = acceleration_parameter
        station["relaminarization_risk"] = bool(
            float(station["x_mm"]) >= chamber_length_mm - 1e-6
            and acceleration_parameter >= RELAMINARIZATION_ACCELERATION_THRESHOLD
        )

    throat_station_index = min(
        range(len(stations)), key=lambda idx: float(stations[idx]["diameter_mm"])
    )
    turbulent_gas_states = [
        solve_gas_side_heat_transfer(
            chamber_pressure_kpa=chamber_pressure_kpa,
            chamber_temperature_k=chamber_temperature_k,
            mach=float(station["mach"]),
            hydraulic_diameter_mm=float(station["diameter_mm"]),
            gamma=gamma,
            gas_constant_j_kg_k=gas_constant_j_kg_k,
            viscosity_pa_s=(
                float(station["gas_viscosity_pa_s"])
                if float(station.get("gas_viscosity_pa_s", 0.0)) > 0.0
                else None
            ),
            conductivity_w_m_k=(
                float(station["gas_conductivity_w_m_k"])
                if float(station.get("gas_conductivity_w_m_k", 0.0)) > 0.0
                else None
            ),
            prandtl=(
                float(station["gas_prandtl"])
                if float(station.get("gas_prandtl", 0.0)) > 0.0
                else None
            ),
            static_temperature_k=(
                float(station["flow_temperature_k"])
                if float(station.get("flow_temperature_k", 0.0)) > 0.0
                else None
            ),
            static_pressure_kpa=(
                float(station["flow_pressure_kpa"])
                if float(station.get("flow_pressure_kpa", 0.0)) > 0.0
                else None
            ),
        )
        for station in stations
    ]
    gas_side_states = march_nozzle_boundary_layer(
        stations, turbulent_gas_states, throat_station_index
    )

    channel_count = int(round(_value(values, "regen_channel_count", 0.0)))
    coolant_mass_flow = _value(values, "regen_coolant_mass_flow_kg_s", 0.0)
    regen_active = bool(design.inputs.regen_cooling and channel_count > 0 and coolant_mass_flow > 0.0)
    if design.inputs.regen_cooling and not regen_active:
        raise ValueError("Regenerative cooling requires calculated channel count and coolant mass flow.")
    default_coolant_temperature_k = (
        coolant_default_inlet_temperature_k(design.inputs.fuel_name)
        if regen_active
        else AMBIENT_TEMPERATURE_K
    )
    coolant_inlet_temperature_k = (
        design.inputs.regen_coolant_inlet_temperature_k
        if regen_active and design.inputs.regen_coolant_inlet_temperature_k > 0.0
        else default_coolant_temperature_k
    )
    coolant_temperature_k = coolant_inlet_temperature_k
    base_coolant_pressure_kpa = max(
        chamber_pressure_kpa * (1.0 + max(0.05, design.inputs.design_injector_dp_ratio)),
        _value(values, "fuel_required_supply_pressure_kpa", chamber_pressure_kpa * 1.2),
    )
    phase_requirement: Dict[str, object] = {}
    phase_envelope: Dict[str, float] = {}
    predicted_regen_drop_kpa = 0.0
    minimum_single_phase_pressure_kpa = 0.0
    if regen_active:
        phase_envelope = coolant_phase_envelope(design.inputs.fuel_name)
        predicted_temperature_rise_k = max(
            0.0,
            _value(values, "regen_coolant_temperature_rise_k", 0.0),
        )
        phase_design_temperature_k = coolant_inlet_temperature_k + predicted_temperature_rise_k
        if design.inputs.fuel_name.strip().lower() == "methane":
            phase_design_temperature_k = max(
                phase_design_temperature_k,
                phase_envelope["critical_temperature_k"],
            )
        phase_requirement = coolant_single_phase_pressure_requirement_kpa(
            design.inputs.fuel_name,
            phase_design_temperature_k,
            design.inputs.design_supply_margin_ratio,
        )
        minimum_single_phase_pressure_kpa = float(
            phase_requirement["minimum_single_phase_pressure_kpa"]
        )
        predicted_regen_drop_kpa = max(
            0.0,
            _value(values, "regen_pressure_drop_kpa", 0.0),
        )
    calculated_pressure_requirement_kpa = (
        minimum_single_phase_pressure_kpa + predicted_regen_drop_kpa
    )
    if regen_active and design.inputs.pressure_solve_mode == "analysis":
        specified_pressure_kpa = max(
            design.inputs.regen_coolant_inlet_pressure_kpa,
            design.inputs.fuel_supply_pressure_kpa,
        )
        coolant_pressure_kpa = specified_pressure_kpa or base_coolant_pressure_kpa
        if coolant_pressure_kpa + 1e-6 < calculated_pressure_requirement_kpa:
            raise ValueError(
                "The specified regenerative-coolant inlet pressure of {0:.2f} kPa is below "
                "the calculated single-phase requirement of {1:.2f} kPa."
                .format(coolant_pressure_kpa, calculated_pressure_requirement_kpa)
            )
    else:
        if design.inputs.regen_coolant_inlet_pressure_kpa > 0.0:
            coolant_pressure_kpa = max(
                base_coolant_pressure_kpa,
                design.inputs.regen_coolant_inlet_pressure_kpa,
                minimum_single_phase_pressure_kpa,
            )
        else:
            coolant_pressure_kpa = max(
                base_coolant_pressure_kpa,
                calculated_pressure_requirement_kpa,
            )
    coolant_inlet_pressure_kpa = coolant_pressure_kpa
    coolant_inlet_phase: Optional[str] = None
    rib_thickness_mm = max(0.05, _value(values, "regen_rib_thickness_mm", 1.0))
    channel_depth_mm = max(0.05, _value(values, "regen_channel_depth_mm", 1.0))
    film_coverage = _value(values, "film_coverage_fraction", 0.0) if design.inputs.film_cooling else 0.0

    solved_by_index: Dict[int, Dict[str, object]] = {}
    traversal = range(len(stations) - 1, -1, -1) if regen_active else range(len(stations))
    for index in traversal:
        station = stations[index]
        x_mm = float(station["x_mm"])
        diameter_mm = float(station["diameter_mm"])
        if len(stations) == 1:
            cell_length_mm = total_length_mm
        elif index == 0:
            cell_length_mm = 0.5 * (float(stations[1]["x_mm"]) - x_mm)
        elif index == len(stations) - 1:
            cell_length_mm = 0.5 * (x_mm - float(stations[index - 1]["x_mm"]))
        else:
            cell_length_mm = 0.5 * (float(stations[index + 1]["x_mm"]) - float(stations[index - 1]["x_mm"]))
        in_chamber = x_mm < chamber_length_mm - 1e-6
        material = design.inputs.chamber_material if in_chamber else design.inputs.nozzle_material
        wall_mm = chamber_wall_mm if in_chamber else nozzle_wall_mm
        conductivity = material_thermal_conductivity(material, AMBIENT_TEMPERATURE_K)
        limit_k = MATERIAL_TEMPERATURE_LIMIT_K.get(material, 900.0)
        local_coolant: Optional[Dict[str, float]] = None
        local_width_mm = 0.0
        if regen_active:
            coolant_properties = coolant_property_state(
                design.inputs.fuel_name,
                coolant_temperature_k,
                coolant_pressure_kpa,
            )
            current_phase = str(coolant_properties["phase"] or "").lower()
            if coolant_inlet_phase is None:
                coolant_inlet_phase = current_phase
            elif (
                "liquid" in coolant_inlet_phase
                and "gas" in current_phase
                and coolant_pressure_kpa < phase_envelope["critical_pressure_kpa"]
            ):
                raise ValueError(
                    "The regenerative coolant changed from liquid to gas at axial station {0}; "
                    "the current single-phase channel model cannot evaluate boiling flow."
                    .format(index)
                )
            hot_wall_radius_mm = diameter_mm * 0.5 + wall_mm
            local_pitch_mm = 2.0 * math.pi * hot_wall_radius_mm / channel_count
            local_width_mm = max(0.05, local_pitch_mm - rib_thickness_mm)
            hydraulic_mm = 2.0 * local_width_mm * channel_depth_mm / max(1e-9, local_width_mm + channel_depth_mm)
            local_coolant = {
                "fluid": coolant_properties["fluid"],
                "phase": coolant_properties["phase"],
                "backend": coolant_properties["backend"],
                "backend_version": coolant_properties["backend_version"],
                "mass_flow_kg_s": coolant_mass_flow,
                "channel_count": float(channel_count),
                "channel_width_mm": local_width_mm,
                "channel_depth_mm": channel_depth_mm,
                "hydraulic_diameter_mm": hydraulic_mm,
                "density_kg_m3": coolant_properties["density_kg_m3"],
                "cp_j_kg_k": coolant_properties["cp_j_kg_k"],
                "viscosity_pa_s": coolant_properties["viscosity_pa_s"],
                "conductivity_w_m_k": coolant_properties["conductivity_w_m_k"],
            }
        downstream_distance = x_mm / max(1.0, total_length_mm)
        film_effectiveness = clamp(film_coverage * math.exp(-2.6 * downstream_distance), 0.0, 0.72)
        solved = _solve_wall_section(
            section_name="Axial Station {0:03d}".format(index),
            diameter_mm=diameter_mm,
            length_mm=max(0.02, cell_length_mm),
            wall_thickness_mm=wall_mm,
            wall_conductivity_w_m_k=conductivity,
            material_name=material,
            material_limit_k=limit_k,
            coolant_state=local_coolant,
            coolant_bulk_temperature_k=coolant_temperature_k,
            film_effectiveness=film_effectiveness,
            gas_side_state=gas_side_states[index],
        )
        gas_h = max(1e-6, float(solved["gas_side_h_w_m2_k"]))
        coolant_h = max(1e-6, float(solved["coolant_side_h_w_m2_k"]))
        recovery_k = float(solved["effective_recovery_temperature_k"])
        sink_k = float(solved["coolant_inlet_temperature_k"])
        wall_m = wall_mm / 1000.0

        def bounded_hot_wall(gas_factor: float, coolant_factor: float, conductivity_factor: float) -> float:
            local_gas_h = gas_h * gas_factor
            local_coolant_h = coolant_h * coolant_factor
            local_k = float(solved["wall_conductivity_w_m_k"]) * conductivity_factor
            resistance = 1.0 / local_gas_h + wall_m / max(1e-9, local_k) + 1.0 / local_coolant_h
            heat_flux = max(0.0, (recovery_k - sink_k) / max(1e-12, resistance))
            return recovery_k - heat_flux / local_gas_h

        gas_payload = dict(solved["gas"])
        gas_lower_factor = float(
            gas_payload["heat_transfer_coefficient_lower_w_m2_k"]
        ) / gas_h
        gas_upper_factor = float(
            gas_payload["heat_transfer_coefficient_upper_w_m2_k"]
        ) / gas_h
        hot_wall_bounds = sorted(
            (
                bounded_hot_wall(gas_lower_factor, 1.10, 1.08),
                bounded_hot_wall(gas_upper_factor, 0.90, 0.92),
            )
        )
        solved.update(
            {
                "station_index": index,
                "x_mm": round(x_mm, 5),
                "radius_mm": round(diameter_mm * 0.5, 5),
                "material": material,
                "material_properties": material_property_state(
                    material,
                    0.5 * (
                        float(solved["hot_wall_temperature_k"])
                        + float(solved["cold_wall_temperature_k"])
                    ),
                ),
                "channel_width_mm": round(local_width_mm, 5),
                "coolant_pressure_kpa": round(coolant_pressure_kpa, 5) if regen_active else None,
                "hot_wall_temperature_lower_k": round(hot_wall_bounds[0], 4),
                "hot_wall_temperature_upper_k": round(hot_wall_bounds[1], 4),
                "uncertainty_basis": "{0}, coolant h +/-10%, wall conductivity +/-8%".format(
                    gas_side_states[index].get(
                        "regime_uncertainty_basis",
                        "NASA TP-3380 Cg interval 0.023-0.026",
                    )
                ),
                "gas_velocity_m_s": round(float(station["gas_velocity_m_s"]), 5),
                "gas_kinematic_viscosity_m2_s": round(
                    float(station["gas_kinematic_viscosity_m2_s"]), 10
                ),
                "acceleration_parameter": round(
                    float(station["acceleration_parameter"]), 10
                ),
                "relaminarization_risk": bool(station["relaminarization_risk"]),
                "boundary_layer_applicability": "regime-selected:{0}".format(
                    gas_side_states[index]["boundary_layer_regime"]
                ),
                "boundary_layer_regime": gas_side_states[index][
                    "boundary_layer_regime"
                ],
                "momentum_thickness_mm": gas_side_states[index].get(
                    "momentum_thickness_mm"
                ),
                "momentum_thickness_reynolds": gas_side_states[index].get(
                    "momentum_thickness_reynolds"
                ),
                "wall_normal_node_count": int(
                    gas_side_states[index].get("wall_normal_node_count", 0)
                ),
                "wall_normal_domain_mm": gas_side_states[index].get(
                    "wall_normal_domain_mm"
                ),
                "wall_normal_domain_fraction": gas_side_states[index].get(
                    "wall_normal_domain_fraction", 0.0
                ),
                "wall_normal_edge_capture_limited": bool(
                    gas_side_states[index].get(
                        "wall_normal_edge_capture_limited", False
                    )
                ),
                "thermal_energy_residual": gas_side_states[index].get(
                    "thermal_energy_residual", 0.0
                ),
                "thermal_grid_refinement_error_percent": gas_side_states[index].get(
                    "thermal_grid_refinement_error_percent", 0.0
                ),
                "relaminarized": bool(
                    gas_side_states[index].get("relaminarized", False)
                ),
            }
        )
        if regen_active and local_coolant is not None:
            coolant = dict(solved["coolant"])
            reynolds = max(1.0, _value(coolant, "reynolds", 1.0))
            friction_factor = 64.0 / reynolds if reynolds < 2300.0 else 0.3164 / reynolds ** 0.25
            hydraulic_m = max(1e-6, local_coolant["hydraulic_diameter_mm"] / 1000.0)
            velocity = _value(coolant, "velocity_m_s", 0.0)
            pressure_drop_pa = (
                friction_factor
                * (max(0.0, cell_length_mm) / 1000.0)
                / hydraulic_m
                * 0.5
                * local_coolant["density_kg_m3"]
                * velocity**2
            )
            coolant_pressure_kpa -= pressure_drop_pa / 1000.0
            solved["coolant_pressure_drop_kpa"] = round(pressure_drop_pa / 1000.0, 6)
            solved["coolant_friction_factor"] = round(friction_factor, 7)
            coolant_temperature_k = float(solved["coolant_outlet_temperature_k"])
        else:
            solved["coolant_pressure_drop_kpa"] = 0.0
            solved["coolant_friction_factor"] = 0.0
        solved_by_index[index] = solved

    axial_stations = [solved_by_index[index] for index in range(len(stations))]
    throat_index = throat_station_index
    chamber_mid_index = min(
        range(len(axial_stations)), key=lambda idx: abs(float(axial_stations[idx]["x_mm"]) - chamber_length_mm * 0.5)
    )
    section_indices = [len(axial_stations) - 1, throat_index, chamber_mid_index]
    section_names = ["Nozzle Exit Plane", "Throat Region", "Chamber Mid"]
    solved_sections: List[Dict[str, object]] = []
    for name, index in zip(section_names, section_indices):
        section = dict(axial_stations[index])
        section["name"] = name
        solved_sections.append(section)

    chamber_boundary_mm = chamber_length_mm
    chamber_rows = [row for row in axial_stations if float(row["x_mm"]) <= chamber_boundary_mm]
    nozzle_rows = [row for row in axial_stations if float(row["x_mm"]) >= chamber_boundary_mm]
    region_rows = {
        "Chamber Mid": max(chamber_rows, key=lambda row: float(row["hot_wall_temperature_upper_k"])),
        "Throat Region": axial_stations[throat_index],
        "Nozzle Exit Plane": max(nozzle_rows, key=lambda row: float(row["hot_wall_temperature_upper_k"])),
    }
    section_envelopes = []
    for name in section_names:
        envelope = dict(region_rows[name])
        envelope["name"] = name
        envelope["is_region_limiting_station"] = True
        section_envelopes.append(envelope)

    total_heat_load_kw = sum(float(row["heat_load_kw"]) for row in axial_stations)
    max_hot_wall_temperature_k = max(float(row["hot_wall_temperature_k"]) for row in axial_stations)
    max_hot_wall_temperature_lower_k = max(float(row["hot_wall_temperature_lower_k"]) for row in axial_stations)
    max_hot_wall_temperature_upper_k = max(float(row["hot_wall_temperature_upper_k"]) for row in axial_stations)
    min_thermal_margin_k = min(float(row["thermal_margin_k"]) for row in axial_stations)
    max_heat_flux_w_m2 = max(float(row["heat_flux_w_m2"]) for row in axial_stations)
    limiting_station = min(axial_stations, key=lambda row: float(row["thermal_margin_k"]))
    limiting_region = "Chamber" if float(limiting_station["x_mm"]) <= chamber_length_mm else "Nozzle"
    coolant_pressure_drop_kpa = coolant_inlet_pressure_kpa - coolant_pressure_kpa if regen_active else 0.0
    maximum_coolant_temperature_k = (
        max(float(row["coolant_outlet_temperature_k"]) for row in axial_stations)
        if regen_active
        else AMBIENT_TEMPERATURE_K
    )
    final_phase_requirement = (
        coolant_single_phase_pressure_requirement_kpa(
            design.inputs.fuel_name,
            maximum_coolant_temperature_k,
            design.inputs.design_supply_margin_ratio,
        )
        if regen_active
        else {}
    )
    final_minimum_single_phase_pressure_kpa = (
        max(
            minimum_single_phase_pressure_kpa,
            float(final_phase_requirement["minimum_single_phase_pressure_kpa"]),
        )
        if regen_active
        else 0.0
    )
    final_phase_boundary_pressure_kpa = (
        max(
            float(phase_requirement.get("phase_boundary_pressure_kpa", 0.0)),
            float(final_phase_requirement.get("phase_boundary_pressure_kpa", 0.0)),
        )
        if regen_active
        else 0.0
    )
    final_phase_basis = (
        phase_requirement.get("basis")
        if minimum_single_phase_pressure_kpa
        >= float(final_phase_requirement.get("minimum_single_phase_pressure_kpa", 0.0))
        else final_phase_requirement.get("basis")
    )
    coolant_required_inlet_pressure_kpa = (
        final_minimum_single_phase_pressure_kpa + coolant_pressure_drop_kpa
        if regen_active
        else 0.0
    )
    coolant_pressure_margin_kpa = (
        coolant_inlet_pressure_kpa - coolant_required_inlet_pressure_kpa
        if regen_active
        else 0.0
    )
    coolant_pressure_requirement_met = (
        not regen_active or coolant_pressure_margin_kpa >= -0.05
    )
    coolant_pressure_redesign_required = bool(
        regen_active
        and design.inputs.pressure_solve_mode == "design"
        and coolant_required_inlet_pressure_kpa > base_coolant_pressure_kpa + 0.05
    )
    status = "calculated-regenerative" if regen_active else "calculated-passive"
    gas_transport_sources = {
        str(dict(row["gas"]).get("transport_source", "internal-temperature-closure"))
        for row in axial_stations
    }
    cantera_transport_active = gas_transport_sources == {"provided-local-state"}
    gas_transport_status = (
        "calculated-stationwise" if cantera_transport_active else "internal-temperature-closure"
    )
    relaminarization_risk_stations = [
        row for row in axial_stations if bool(row["relaminarization_risk"])
    ]
    maximum_acceleration_parameter = max(
        float(row["acceleration_parameter"]) for row in axial_stations
    )
    boundary_layer_regime_counts: Dict[str, int] = {}
    for row in axial_stations:
        regime = str(row["boundary_layer_regime"])
        boundary_layer_regime_counts[regime] = boundary_layer_regime_counts.get(regime, 0) + 1
    maximum_thermal_grid_error_percent = max(
        float(row.get("thermal_grid_refinement_error_percent", 0.0))
        for row in axial_stations
    )
    maximum_thermal_energy_residual = max(
        float(row.get("thermal_energy_residual", 0.0)) for row in axial_stations
    )
    edge_capture_limited_station_count = sum(
        bool(row.get("wall_normal_edge_capture_limited", False))
        for row in axial_stations
    )
    boundary_layer_profiles = [
        {
            "station_index": row["station_index"],
            "x_mm": row["x_mm"],
            "radius_mm": row["radius_mm"],
            "regime": row["boundary_layer_regime"],
            **dict(row["gas"]["wall_normal_profile"]),
        }
        for row in axial_stations
        if dict(row["gas"]).get("wall_normal_profile")
    ]
    note = (
        "Counterflow coolant is marched from nozzle exit to injector over the exact axial wall contour."
        if regen_active
        else "The passive wall is evaluated at every axial station with ambient outer convection."
    )
    if design.inputs.film_cooling:
        note += " Film effectiveness follows the calculated coverage and an explicit downstream decay correlation."

    return {
        "metadata": {
            "solver_name": "Heat Transfer Solver",
            "solver_version": "2.3",
            "model": "stationwise-conjugate-wall-coolant-march",
            "coolant_direction": "nozzle-exit-to-injector" if regen_active else "not-active",
            "coolant_property_source": COOLANT_PROPERTY_SOURCE if regen_active else None,
            "material_property_model": "temperature-dependent-tabulated-interpolation",
            "gas_transport_model": gas_transport_status,
        },
        "status": status,
        "summary": {
            "status": status,
            "axial_station_count": len(axial_stations),
            "gas_transport_status": gas_transport_status,
            "boundary_layer_model": (
                "axisymmetric-wall-normal-energy-and-momentum-integral"
            ),
            "wall_normal_node_count": DEFAULT_WALL_NORMAL_NODE_COUNT,
            "maximum_thermal_grid_refinement_error_percent": round(
                maximum_thermal_grid_error_percent, 6
            ),
            "boundary_layer_profile_count": len(boundary_layer_profiles),
            "maximum_thermal_energy_relative_residual": round(
                maximum_thermal_energy_residual, 12
            ),
            "edge_capture_limited_station_count": (
                edge_capture_limited_station_count
            ),
            "computational_complexity": {
                "time": "O(Nx*Ny)",
                "marching_memory": "O(Ny)",
                "stored_profile_memory": "O(Nprofile*Ny)",
            },
            "relaminarization_acceleration_threshold": RELAMINARIZATION_ACCELERATION_THRESHOLD,
            "transition_momentum_thickness_reynolds": TRANSITION_MOMENTUM_THICKNESS_REYNOLDS,
            "boundary_layer_regime_counts": boundary_layer_regime_counts,
            "gas_transport_source": summary.get(
                "gas_transport_source",
                "cantera-frozen-composition:gri30.yaml"
                if cantera_transport_active
                else "internal-temperature-closure",
            ),
            "gas_transport_mass_fraction_coverage": summary.get(
                "gas_transport_mass_fraction_coverage"
            ),
            "total_heat_load_kw": round(total_heat_load_kw, 4),
            "max_heat_flux_w_m2": round(max_heat_flux_w_m2, 3),
            "max_hot_wall_temperature_k": round(max_hot_wall_temperature_k, 3),
            "max_hot_wall_temperature_lower_k": round(max_hot_wall_temperature_lower_k, 3),
            "max_hot_wall_temperature_upper_k": round(max_hot_wall_temperature_upper_k, 3),
            "min_thermal_margin_k": round(min_thermal_margin_k, 3),
            "coolant_inlet_temperature_k": round(coolant_inlet_temperature_k, 3),
            "coolant_outlet_temperature_k": round(coolant_temperature_k, 3),
            "coolant_inlet_pressure_kpa": round(coolant_inlet_pressure_kpa, 3) if regen_active else None,
            "coolant_outlet_pressure_kpa": round(coolant_pressure_kpa, 3) if regen_active else None,
            "coolant_pressure_drop_kpa": round(coolant_pressure_drop_kpa, 3),
            "coolant_phase_boundary_pressure_kpa": round(
                final_phase_boundary_pressure_kpa, 3
            ),
            "coolant_minimum_single_phase_pressure_kpa": round(
                final_minimum_single_phase_pressure_kpa, 3
            ),
            "coolant_required_inlet_pressure_kpa": round(
                coolant_required_inlet_pressure_kpa, 3
            ),
            "coolant_pressure_margin_kpa": round(coolant_pressure_margin_kpa, 3),
            "coolant_pressure_requirement_met": coolant_pressure_requirement_met,
            "coolant_pressure_redesign_required": coolant_pressure_redesign_required,
            "coolant_phase_pressure_basis": final_phase_basis,
            "coolant_critical_temperature_k": round(
                float(phase_envelope.get("critical_temperature_k", 0.0)), 3
            ),
            "coolant_critical_pressure_kpa": round(
                float(phase_envelope.get("critical_pressure_kpa", 0.0)), 3
            ),
            "limiting_section": limiting_region,
            "limiting_station_x_mm": round(float(limiting_station["x_mm"]), 3),
            "film_cooling_active": bool(design.inputs.film_cooling),
            "regen_cooling_active": regen_active,
            "maximum_acceleration_parameter": round(maximum_acceleration_parameter, 10),
            "relaminarization_risk_station_count": len(relaminarization_risk_stations),
            "boundary_layer_status": "regime-selection-and-envelope-active",
            "note": note,
        },
        "coolant": {
            "mass_flow_kg_s": coolant_mass_flow,
            "channel_count": channel_count,
            "inlet_temperature_k": coolant_inlet_temperature_k,
            "inlet_pressure_kpa": coolant_inlet_pressure_kpa,
            "minimum_single_phase_pressure_kpa": final_minimum_single_phase_pressure_kpa,
            "required_inlet_pressure_kpa": coolant_required_inlet_pressure_kpa,
            "pressure_margin_kpa": coolant_pressure_margin_kpa,
            "property_source": COOLANT_PROPERTY_SOURCE,
        }
        if regen_active
        else {},
        "sections": solved_sections,
        "section_envelopes": section_envelopes,
        "axial_stations": axial_stations,
        "boundary_layer_profiles": boundary_layer_profiles,
    }
