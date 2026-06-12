import math
from typing import Callable, Dict, List, Optional

from liquid_engine_studio.concept_model import ConceptDesign, clamp, rounded
from liquid_engine_studio.propellants import lookup_propellant
from liquid_engine_studio.solver_assumptions import SolverAssumptions
from liquid_engine_studio.thermochemistry_provider import (
    CanteraThermochemistryProvider,
    ThermochemistryProvider,
    resolve_thermochemistry_provider,
)


ProgressCallback = Callable[[float, str], None]


def _area_from_diameter_mm(diameter_mm: float) -> float:
    radius_m = max(1e-6, diameter_mm / 2000.0)
    return math.pi * radius_m * radius_m


def _normalize_flow_model(flow_model: str) -> str:
    normalized = (flow_model or "fast").strip().lower()
    return normalized if normalized in {"fast", "refined"} else "fast"


def _area_mach_relation(mach: float, gamma: float) -> float:
    return (1.0 / max(1e-8, mach)) * (
        ((2.0 / (gamma + 1.0)) * (1.0 + 0.5 * (gamma - 1.0) * mach * mach))
        ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
    )


def _solve_supersonic_mach(area_ratio: float, gamma: float) -> float:
    target = max(1.0, area_ratio)
    low = 1.0001
    high = 20.0
    for _ in range(80):
        mid = 0.5 * (low + high)
        value = _area_mach_relation(mid, gamma)
        if value < target:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def _thrust_coefficient_vacuum(gamma: float, area_ratio: float) -> Dict[str, float]:
    exit_mach = _solve_supersonic_mach(area_ratio, gamma)
    pressure_ratio = (1.0 + 0.5 * (gamma - 1.0) * exit_mach * exit_mach) ** (-gamma / (gamma - 1.0))
    cf = math.sqrt(
        max(
            1e-8,
            (2.0 * gamma * gamma / (gamma - 1.0))
            * (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (gamma - 1.0))
            * (1.0 - pressure_ratio ** ((gamma - 1.0) / gamma)),
        )
    ) + pressure_ratio * area_ratio
    return {
        "exit_mach": exit_mach,
        "pressure_ratio": pressure_ratio,
        "thrust_coefficient": cf,
    }


def _estimate_fast_nozzle_loss_model(
    throat_area: float,
    exit_area: float,
    nozzle_length_mm: float,
    assumptions: SolverAssumptions,
) -> Dict[str, object]:
    throat_radius_m = math.sqrt(max(1e-8, throat_area) / math.pi)
    exit_radius_m = math.sqrt(max(1e-8, exit_area) / math.pi)
    throat_diameter_mm = max(1e-6, throat_radius_m * 2000.0)
    nozzle_length_m = max(1e-6, nozzle_length_mm / 1000.0)
    nozzle_half_angle_rad = math.atan(max(0.0, exit_radius_m - throat_radius_m) / nozzle_length_m)
    nozzle_half_angle_deg = math.degrees(nozzle_half_angle_rad)

    reference_half_angle_deg = max(1.0, float(assumptions.nozzle_divergence_half_angle_deg))
    reference_half_angle_rad = math.radians(reference_half_angle_deg)
    length_to_throat_ratio = max(0.1, float(nozzle_length_mm) / max(1.0, throat_diameter_mm))
    expansion_ratio = max(1.0, exit_area / max(1e-8, throat_area))

    divergence_efficiency = clamp((1.0 + math.cos(nozzle_half_angle_rad)) * 0.5, 0.82, 1.0)
    if nozzle_half_angle_rad > reference_half_angle_rad:
        excess_angle = (nozzle_half_angle_rad / max(1e-6, reference_half_angle_rad)) - 1.0
        divergence_efficiency = clamp(divergence_efficiency * (1.0 - 0.08 * excess_angle), 0.78, 1.0)

    boundary_layer_penalty = float(assumptions.nozzle_boundary_layer_loss_factor) * max(
        0.0,
        (math.sqrt(expansion_ratio) - 1.0) * max(0.35, 1.85 - length_to_throat_ratio),
    )
    boundary_layer_efficiency = clamp(1.0 - boundary_layer_penalty, 0.88, 0.997)

    discharge_coefficient = clamp(float(assumptions.nozzle_discharge_coefficient), 0.84, 1.0)
    assumption_efficiency = clamp(float(assumptions.nozzle_efficiency), 0.72, 1.05)
    geometry_efficiency = divergence_efficiency * boundary_layer_efficiency * discharge_coefficient
    overall_efficiency = clamp(assumption_efficiency * geometry_efficiency, 0.68, 1.0)

    return {
        "status": "calculated",
        "flow_model": "fast",
        "flow_model_label": "Fast quasi-1D preview",
        "nozzle_half_angle_deg": nozzle_half_angle_deg,
        "reference_half_angle_deg": reference_half_angle_deg,
        "length_to_throat_ratio": length_to_throat_ratio,
        "expansion_ratio": expansion_ratio,
        "divergence_efficiency": divergence_efficiency,
        "boundary_layer_efficiency": boundary_layer_efficiency,
        "discharge_coefficient": discharge_coefficient,
        "assumption_efficiency": assumption_efficiency,
        "geometry_efficiency": geometry_efficiency,
        "overall_efficiency": overall_efficiency,
        "loss_fraction": max(0.0, 1.0 - overall_efficiency),
    }


def _estimate_refined_nozzle_loss_model(
    throat_area: float,
    exit_area: float,
    nozzle_length_mm: float,
    contour_points: List[Dict[str, object]],
    assumptions: SolverAssumptions,
) -> Dict[str, object]:
    throat_radius_m = math.sqrt(max(1e-8, throat_area) / math.pi)
    throat_diameter_mm = max(1e-6, throat_radius_m * 2000.0)
    exit_radius_m = math.sqrt(max(1e-8, exit_area) / math.pi)
    exit_diameter_mm = max(1e-6, exit_radius_m * 2000.0)
    expansion_ratio = max(1.0, exit_area / max(1e-8, throat_area))
    nozzle_length_m = max(1e-6, nozzle_length_mm / 1000.0)
    equivalent_half_angle_deg = math.degrees(
        math.atan(max(0.0, exit_radius_m - throat_radius_m) / nozzle_length_m)
    )
    reference_half_angle_deg = max(1.0, float(assumptions.nozzle_divergence_half_angle_deg))

    diverging_points = [
        point
        for point in contour_points
        if str(point.get("section", "")).strip().lower() in {"diverging_arc", "bell", "exit"}
    ]
    local_angles_deg: List[float] = []
    exit_angle_deg = equivalent_half_angle_deg
    contour_length_mm = 0.0
    cumulative_turn_deg = 0.0
    previous_angle_deg: Optional[float] = None

    for first, second in zip(diverging_points, diverging_points[1:]):
        dx_mm = float(second.get("x_mm", 0.0)) - float(first.get("x_mm", 0.0))
        dr_mm = float(second.get("radius_mm", 0.0)) - float(first.get("radius_mm", 0.0))
        if dx_mm <= 1e-6:
            continue
        segment_angle_deg = math.degrees(math.atan(max(0.0, dr_mm) / dx_mm))
        local_angles_deg.append(segment_angle_deg)
        contour_length_mm += math.hypot(dx_mm, dr_mm)
        if previous_angle_deg is not None:
            cumulative_turn_deg += abs(segment_angle_deg - previous_angle_deg)
        previous_angle_deg = segment_angle_deg
        exit_angle_deg = segment_angle_deg

    weighted_half_angle_deg = (
        sum(local_angles_deg) / len(local_angles_deg) if local_angles_deg else equivalent_half_angle_deg
    )
    max_wall_angle_deg = max(local_angles_deg) if local_angles_deg else equivalent_half_angle_deg
    length_to_throat_ratio = max(0.1, contour_length_mm / max(1.0, throat_diameter_mm))
    bell_quality = clamp(1.0 - abs(exit_angle_deg - 8.5) / 18.0, 0.84, 1.0)
    divergence_efficiency = clamp(
        0.992 - 0.0012 * max(weighted_half_angle_deg - 8.0, 0.0) - 0.0008 * max(max_wall_angle_deg - 26.0, 0.0),
        0.9,
        0.999,
    )
    curvature_efficiency = clamp(1.0 - cumulative_turn_deg / 1200.0, 0.94, 0.999)
    discharge_coefficient = clamp(
        float(assumptions.nozzle_discharge_coefficient) + 0.015 * bell_quality,
        0.9,
        0.999,
    )
    boundary_layer_penalty = (
        float(assumptions.nozzle_boundary_layer_loss_factor)
        * 0.55
        * max(0.0, math.sqrt(expansion_ratio) - 1.0)
        / max(0.8, length_to_throat_ratio)
    )
    boundary_layer_efficiency = clamp(1.0 - boundary_layer_penalty, 0.93, 0.999)
    assumption_efficiency = clamp(float(assumptions.nozzle_efficiency), 0.78, 1.05)
    geometry_efficiency = (
        divergence_efficiency
        * curvature_efficiency
        * boundary_layer_efficiency
        * discharge_coefficient
    )
    overall_efficiency = clamp(assumption_efficiency * geometry_efficiency, 0.72, 1.0)

    return {
        "status": "calculated",
        "flow_model": "refined",
        "flow_model_label": "Refined quasi-1D solve",
        "nozzle_half_angle_deg": weighted_half_angle_deg,
        "reference_half_angle_deg": reference_half_angle_deg,
        "length_to_throat_ratio": length_to_throat_ratio,
        "expansion_ratio": expansion_ratio,
        "divergence_efficiency": divergence_efficiency,
        "boundary_layer_efficiency": boundary_layer_efficiency,
        "discharge_coefficient": discharge_coefficient,
        "assumption_efficiency": assumption_efficiency,
        "geometry_efficiency": geometry_efficiency,
        "overall_efficiency": overall_efficiency,
        "loss_fraction": max(0.0, 1.0 - overall_efficiency),
        "curvature_efficiency": curvature_efficiency,
        "exit_angle_deg": exit_angle_deg,
        "max_wall_angle_deg": max_wall_angle_deg,
        "bell_quality": bell_quality,
        "contour_length_mm": contour_length_mm,
        "exit_diameter_mm": exit_diameter_mm,
    }


def _estimate_nozzle_loss_model(
    throat_area: float,
    exit_area: float,
    nozzle_length_mm: float,
    contour_points: List[Dict[str, object]],
    assumptions: SolverAssumptions,
    flow_model: str,
) -> Dict[str, object]:
    if flow_model == "refined":
        return _estimate_refined_nozzle_loss_model(
            throat_area, exit_area, nozzle_length_mm, contour_points, assumptions
        )
    return _estimate_fast_nozzle_loss_model(throat_area, exit_area, nozzle_length_mm, assumptions)


def _effective_thrust_coefficient(
    flow_model: str,
    chamber_pressure_pa: float,
    exit_area: float,
    throat_area: float,
    ambient_pressure_pa: float,
    vacuum_model: Dict[str, float],
    nozzle_loss_model: Dict[str, object],
) -> Dict[str, float]:
    area_ratio = exit_area / max(1e-8, throat_area)
    cf_vac = float(vacuum_model["thrust_coefficient"])
    pressure_ratio = float(vacuum_model["pressure_ratio"])
    exit_pressure_pa = pressure_ratio * chamber_pressure_pa
    ambient_correction = min(
        (ambient_pressure_pa / max(1e-6, chamber_pressure_pa)) * area_ratio * 0.03,
        0.14 * cf_vac,
    )
    ambient_thrust_coefficient = max(0.18, cf_vac - ambient_correction)
    if flow_model == "refined":
        pressure_ratio_to_ambient = exit_pressure_pa / max(1.0, ambient_pressure_pa)
        separation_efficiency = (
            clamp(0.94 + 0.08 * pressure_ratio_to_ambient, 0.9, 1.0)
            if pressure_ratio_to_ambient < 0.75
            else 1.0
        )
        effective_coefficient = (
            ambient_thrust_coefficient
            * float(nozzle_loss_model["overall_efficiency"])
            * separation_efficiency
        )
    else:
        separation_efficiency = 1.0
        effective_coefficient = cf_vac * float(nozzle_loss_model["overall_efficiency"])
    return {
        "effective_thrust_coefficient": effective_coefficient,
        "ambient_thrust_coefficient": ambient_thrust_coefficient,
        "ambient_correction": ambient_correction,
        "separation_efficiency": separation_efficiency,
        "exit_pressure_kpa": exit_pressure_pa / 1000.0,
    }


def _station_field(value: float, unit: str, source_solver: str) -> Dict[str, object]:
    if unit == "kg/s":
        display_value = round(value, 4)
    elif unit == "":
        display_value = round(value, 4)
    else:
        display_value = rounded(value)
    return {
        "value": display_value,
        "unit": unit,
        "status": "calculated",
        "source_solver": source_solver,
    }


def _build_combustion_station_updates(
    chamber_temperature_k: float,
    chamber_pressure_kpa: float,
    exit_temperature_k: float,
    exit_pressure_kpa: float,
    exit_mach: float,
    mass_flow_kg_s: float,
    flow_model: str,
) -> Dict[str, Dict[str, Dict[str, object]]]:
    source_solver = "Combustion CFD Proxy Solver"
    exit_station_pressure_kpa = exit_pressure_kpa if flow_model == "refined" else 101.3

    return {
        "Chamber Mid": {
            "temperature": _station_field(chamber_temperature_k, "K", source_solver),
            "pressure": _station_field(chamber_pressure_kpa, "kPa", source_solver),
            "mass_flow": _station_field(mass_flow_kg_s, "kg/s", source_solver),
        },
        "Throat Region": {
            "temperature": _station_field(chamber_temperature_k, "K", source_solver),
            "pressure": _station_field(chamber_pressure_kpa * 0.95, "kPa", source_solver),
            "mach": _station_field(1.0, "", source_solver),
            "mass_flow": _station_field(mass_flow_kg_s, "kg/s", source_solver),
        },
        "Nozzle Exit Plane": {
            "temperature": _station_field(exit_temperature_k, "K", source_solver),
            "pressure": _station_field(exit_station_pressure_kpa, "kPa", source_solver),
            "mach": _station_field(exit_mach, "", source_solver),
            "mass_flow": _station_field(mass_flow_kg_s, "kg/s", source_solver),
        },
    }


def run_combustion_cfd_proxy(
    design: ConceptDesign,
    assumptions: SolverAssumptions,
    station_count: int = 14,
    max_iterations_override: Optional[int] = None,
    progress_callback: Optional[ProgressCallback] = None,
    thermochemistry_mode: str = "auto",
    thermochemistry_provider: Optional[ThermochemistryProvider] = None,
) -> Dict[str, object]:
    """Runs a lightweight quasi-1D combustion/nozzle proxy solver with iteration tracing."""

    def report(progress: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(clamp(progress, 0.0, 100.0), message)

    flow_model = _normalize_flow_model(getattr(assumptions, "flow_model", "fast"))
    report(2.0, "Step 1/5: Preparing combustion and geometry inputs")

    eng = design.derived.engineering_values
    chamber_diameter_mm = design.inputs.chamber_diameter_mm
    nozzle_diameter_mm = design.inputs.nozzle_diameter_mm
    chamber_area = _area_from_diameter_mm(chamber_diameter_mm)
    exit_area = _area_from_diameter_mm(nozzle_diameter_mm)
    nozzle_length_mm = max(1.0, float(getattr(design.derived, "nozzle_length_mm", 0.0)))
    contour_points = list(getattr(design.derived, "nozzle_contour_points", []))
    fuel = lookup_propellant(design.inputs.fuel_name, "fuel")
    oxidizer = lookup_propellant(design.inputs.oxidizer_name, "oxidizer")

    throat_fraction = clamp(
        0.21
        + (fuel.cooling_affinity - 0.5) * 0.015
        - (oxidizer.thermal_severity - 0.6) * 0.010
        + (0.01 if design.inputs.use_pumps else -0.005),
        0.16,
        0.24,
    )
    fast_throat_area = chamber_area * throat_fraction
    if flow_model == "refined":
        throat_diameter_mm = max(1.0, float(eng.get("nozzle_throat_diameter_mm", 0.0)))
        geometric_throat_area = _area_from_diameter_mm(throat_diameter_mm)
        throat_area = 0.55 * fast_throat_area + 0.45 * geometric_throat_area
    else:
        throat_area = fast_throat_area

    requested_thermochemistry_mode = (thermochemistry_mode or "auto").strip().lower()
    effective_thermochemistry_mode = "cantera"
    provider = thermochemistry_provider or resolve_thermochemistry_provider(effective_thermochemistry_mode)
    if not isinstance(provider, CanteraThermochemistryProvider):
        raise RuntimeError("Combustion solver requires the Cantera thermochemistry provider.")
    thermo = provider.estimate(design, assumptions, fuel, oxidizer)
    if thermo.status not in {"ok", "approximate", "placeholder"}:
        raise RuntimeError("Cantera thermochemistry failed: {0}".format(thermo.note or thermo.status))

    gamma = thermo.gamma
    r_gas = thermo.gas_constant_j_kgk
    chamber_temp = thermo.chamber_temperature_k

    cstar_ideal = math.sqrt(r_gas * chamber_temp) / max(
        1e-6,
        gamma * math.sqrt((2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (gamma - 1.0))),
    )
    cstar_efficiency = clamp(
        0.90
        + fuel.cooling_affinity * 0.04
        + (0.03 if design.inputs.regen_cooling else 0.0)
        + (0.01 if design.inputs.use_pumps else -0.01)
        - oxidizer.thermal_severity * 0.03,
        0.84,
        1.02,
    )
    cstar_efficiency = cstar_efficiency * max(0.7, float(assumptions.combustion_efficiency))
    cstar_efficiency = cstar_efficiency * max(0.8, float(thermo.cstar_efficiency_factor))
    cstar_efficiency = clamp(cstar_efficiency, 0.72, 1.15)
    cstar = cstar_ideal * cstar_efficiency

    nozzle_efficiency = clamp(float(assumptions.nozzle_efficiency), 0.75, 1.05)
    ambient_pressure_pa = max(1.0, float(getattr(assumptions, "ambient_pressure_kpa", 101.3)) * 1000.0)

    iteration_limit = assumptions.max_iterations
    if max_iterations_override is not None:
        iteration_limit = max(3, min(200, int(max_iterations_override)))
    station_steps = max(6, min(120, int(station_count)))

    report(12.0, "Step 2/5: Iterating chamber state")
    vacuum_model = _thrust_coefficient_vacuum(gamma, exit_area / max(1e-8, throat_area))
    nozzle_loss_model = _estimate_nozzle_loss_model(
        throat_area, exit_area, nozzle_length_mm, contour_points, assumptions, flow_model
    )
    coefficient_state = _effective_thrust_coefficient(
        flow_model,
        max(100000.0, float(eng.get("chamber_pressure_kpa", 800.0)) * 1000.0),
        exit_area,
        throat_area,
        ambient_pressure_pa,
        vacuum_model,
        nozzle_loss_model,
    )
    effective_thrust_coefficient = coefficient_state["effective_thrust_coefficient"]
    thrust_bias = clamp(
        0.97 + fuel.cooling_affinity * 0.02 - oxidizer.thermal_severity * 0.015 + (0.01 if design.inputs.use_pumps else -0.01),
        0.92,
        1.04,
    )
    design_thrust_n = max(1.0, float(design.inputs.target_thrust_newtons) * thrust_bias)
    target_chamber_pressure_pa = design_thrust_n / max(
        1e-6, effective_thrust_coefficient * throat_area
    )
    chamber_pressure_pa = max(
        100000.0, float(eng.get("chamber_pressure_kpa", 800.0)) * 1000.0
    )
    converged = False
    mass_flow_kg_s = 0.0
    iteration_trace: List[Dict[str, float]] = []
    for iteration in range(1, iteration_limit + 1):
        coefficient_state = _effective_thrust_coefficient(
            flow_model,
            chamber_pressure_pa,
            exit_area,
            throat_area,
            ambient_pressure_pa,
            vacuum_model,
            nozzle_loss_model,
        )
        effective_thrust_coefficient = coefficient_state["effective_thrust_coefficient"]
        chamber_pressure_pa = 0.72 * chamber_pressure_pa + 0.28 * target_chamber_pressure_pa
        if flow_model == "refined":
            target_chamber_pressure_pa = design_thrust_n / max(
                1e-6, effective_thrust_coefficient * throat_area
            )
        rho_chamber = chamber_pressure_pa / max(1e-6, r_gas * chamber_temp)
        mass_flow_kg_s = chamber_pressure_pa * throat_area / max(1e-6, cstar)
        predicted_thrust_n = effective_thrust_coefficient * chamber_pressure_pa * throat_area
        error = abs(predicted_thrust_n - design_thrust_n) / max(1.0, design_thrust_n)
        chamber_velocity_m_s = mass_flow_kg_s / max(1e-8, rho_chamber * chamber_area)

        iteration_trace.append(
            {
                "iteration": float(iteration),
                "chamber_pressure_kpa": rounded(chamber_pressure_pa / 1000.0),
                "chamber_density_kg_m3": rounded(rho_chamber),
                "chamber_velocity_m_s": rounded(chamber_velocity_m_s),
                "thrust_coefficient": rounded(effective_thrust_coefficient),
                "relative_error": round(error, 6),
            }
        )

        report(
            12.0 + (iteration / max(1, iteration_limit)) * 48.0,
            "Step 2/5: Solving {0} chamber iteration {1}".format(flow_model, iteration),
        )
        if error < assumptions.convergence_tolerance:
            converged = True
            break

    report(64.0, "Step 3/5: Solving nozzle contour, expansion, and losses")
    expansion_ratio = exit_area / max(1e-8, throat_area)
    pressure_ratio = vacuum_model["pressure_ratio"]
    exit_mach = vacuum_model["exit_mach"]
    characteristic_velocity = cstar
    exit_velocity_m_s = cstar * vacuum_model["thrust_coefficient"]
    exit_temp_k = chamber_temp / (1.0 + 0.5 * (gamma - 1.0) * exit_mach * exit_mach)
    coefficient_state = _effective_thrust_coefficient(
        flow_model,
        chamber_pressure_pa,
        exit_area,
        throat_area,
        ambient_pressure_pa,
        vacuum_model,
        nozzle_loss_model,
    )
    effective_thrust_coefficient = coefficient_state["effective_thrust_coefficient"]
    exit_pressure_kpa = coefficient_state["exit_pressure_kpa"]

    report(78.0, "Step 4/5: Building simplified CFD axial profile")
    axial_profile = []
    for idx in range(station_steps + 1):
        ratio = idx / max(1, station_steps)
        axial_x_mm = ratio * max(1.0, design.derived.total_stack_length_mm)
        local_pressure_kpa = (chamber_pressure_pa / 1000.0) * (1.0 - 0.78 * ratio)
        local_temp_k = chamber_temp * (1.0 - 0.46 * ratio)
        local_velocity = exit_velocity_m_s * (0.08 + 0.92 * ratio)
        axial_profile.append(
            {
                "x_mm": rounded(axial_x_mm),
                "pressure_kpa": rounded(max(80.0, local_pressure_kpa)),
                "temperature_k": rounded(max(220.0, local_temp_k)),
                "velocity_m_s": rounded(local_velocity),
            }
        )
        report(
            78.0 + ratio * 18.0,
            "Step 4/5: Generating profile station {0}/{1}".format(idx, station_steps),
        )

    report(98.0, "Step 5/5: Assembling combustion solver outputs")
    predicted_thrust_n = effective_thrust_coefficient * chamber_pressure_pa * throat_area
    predicted_isp_s = predicted_thrust_n / max(1e-6, mass_flow_kg_s * assumptions.gravity_m_s2)
    predicted_impulse_newton_seconds = predicted_thrust_n * max(0.5, float(design.inputs.burn_time_seconds))
    thrust_error = abs(predicted_thrust_n - design_thrust_n) / max(1.0, design_thrust_n)

    station_field_updates = _build_combustion_station_updates(
        chamber_temperature_k=chamber_temp,
        chamber_pressure_kpa=chamber_pressure_pa / 1000.0,
        exit_temperature_k=exit_temp_k,
        exit_pressure_kpa=exit_pressure_kpa,
        exit_mach=exit_mach,
        mass_flow_kg_s=mass_flow_kg_s,
        flow_model=flow_model,
    )

    result = {
        "metadata": {
            "solver_name": "Combustion CFD Proxy Solver",
            "solver_version": "1.1",
            "solver_mode": "quasi-1d-{0}".format(flow_model),
            "solver_stage": "stage-2-nozzle-loss-{0}".format(flow_model),
            "flow_model": flow_model,
            "flow_model_label": (
                "Refined quasi-1D solve" if flow_model == "refined" else "Fast quasi-1D preview"
            ),
            "station_count": station_steps + 1,
            "iteration_limit": iteration_limit,
            "thermochemistry": {
                "mode": thermochemistry_mode,
                "requested_mode": requested_thermochemistry_mode,
                "effective_mode": effective_thermochemistry_mode,
                "provider": thermo.provider_name,
                "source": thermo.source,
                "status": thermo.status,
                "fallback_used": thermo.status != "ok",
                "note": thermo.note,
            },
        },
        "status": "converged" if (converged or thrust_error < 0.01) else "max-iterations",
        "status_detail": "flow={0}; thermochemistry={1}; nozzle={2}".format(flow_model, thermo.status, nozzle_loss_model["status"]),
        "converged": converged,
        "iterations": len(iteration_trace),
        "summary": {
            "chamber_pressure_kpa": rounded(chamber_pressure_pa / 1000.0),
            "exit_velocity_m_s": rounded(exit_velocity_m_s),
            "characteristic_velocity_m_s": rounded(characteristic_velocity),
            "predicted_thrust_newtons": rounded(predicted_thrust_n),
            "predicted_impulse_newton_seconds": rounded(predicted_impulse_newton_seconds),
            "predicted_isp_seconds": rounded(predicted_isp_s),
            "mass_flow_kg_s": rounded(mass_flow_kg_s),
            "expansion_ratio": rounded(expansion_ratio),
            "station_count": float(station_steps + 1),
            "iteration_limit": float(iteration_limit),
            "throat_area_m2": round(throat_area, 7),
            "throat_fraction": round(throat_fraction, 4),
            "thrust_coefficient": rounded(effective_thrust_coefficient),
            "cstar_m_s": rounded(cstar),
            "design_thrust_newtons": rounded(design_thrust_n),
            "thrust_error_fraction": round(thrust_error, 6),
            "exit_mach": rounded(exit_mach),
            "flow_model": flow_model,
            "flow_model_label": "Refined quasi-1D solve" if flow_model == "refined" else "Fast quasi-1D preview",
            "ambient_pressure_kpa": rounded(ambient_pressure_pa / 1000.0),
            "exit_pressure_kpa": rounded(exit_pressure_kpa),
            "ambient_thrust_coefficient": rounded(coefficient_state["ambient_thrust_coefficient"]),
            "separation_efficiency": round(coefficient_state["separation_efficiency"], 4),
            "thermochemistry_provider": thermo.provider_name,
            "thermochemistry_status": thermo.status,
        },
        "iteration_trace": iteration_trace,
        "axial_profile": axial_profile,
        "physics": {
            "assumptions": {
                "flow_model": flow_model,
                "ambient_pressure_kpa": assumptions.ambient_pressure_kpa,
                "gamma": assumptions.gamma,
                "gas_constant_j_kgk": assumptions.gas_constant_j_kgk,
                "chamber_temperature_k": assumptions.chamber_temperature_k,
                "combustion_efficiency": assumptions.combustion_efficiency,
                "nozzle_efficiency": assumptions.nozzle_efficiency,
                "gravity_m_s2": assumptions.gravity_m_s2,
            },
            "thermochemistry": {
                "gamma": round(gamma, 5),
                "gas_constant_j_kgk": round(r_gas, 3),
                "chamber_temperature_k": round(chamber_temp, 2),
                "source": thermo.source,
                "status": thermo.status,
                "note": thermo.note,
            },
            "geometry": {
                "chamber_area_m2": round(chamber_area, 7),
                "throat_area_m2": round(throat_area, 7),
                "exit_area_m2": round(exit_area, 7),
                "area_ratio": round(expansion_ratio, 4),
            },
            "propellants": {
                "fuel": fuel.name,
                "oxidizer": oxidizer.name,
                "mixture_ratio": round(design.inputs.mixture_ratio, 3),
            },
            "coefficients": {
                "thrust_coefficient": round(vacuum_model["thrust_coefficient"], 4),
                "effective_thrust_coefficient": round(effective_thrust_coefficient, 4),
                "nozzle_efficiency": round(nozzle_efficiency, 4),
                "nozzle_geometry_efficiency": round(nozzle_loss_model["geometry_efficiency"], 4),
                "divergence_efficiency": round(nozzle_loss_model["divergence_efficiency"], 4),
                "boundary_layer_efficiency": round(nozzle_loss_model["boundary_layer_efficiency"], 4),
                "discharge_coefficient": round(nozzle_loss_model["discharge_coefficient"], 4),
                "nozzle_half_angle_deg": round(nozzle_loss_model["nozzle_half_angle_deg"], 3),
                "nozzle_length_mm": round(nozzle_length_mm, 2),
                "nozzle_length_to_throat_ratio": round(nozzle_loss_model["length_to_throat_ratio"], 4),
                "nozzle_loss_fraction": round(nozzle_loss_model["loss_fraction"], 4),
                "ambient_thrust_coefficient": round(coefficient_state["ambient_thrust_coefficient"], 4),
                "separation_efficiency": round(coefficient_state["separation_efficiency"], 4),
                "ambient_correction": round(coefficient_state["ambient_correction"], 6),
                "cstar_m_s": round(cstar, 2),
                "cstar_ideal_m_s": round(cstar_ideal, 2),
                "cstar_efficiency": round(cstar_efficiency, 4),
                "exit_mach": round(exit_mach, 4),
                "pressure_ratio": round(pressure_ratio, 6),
            },
            "results": {
                "target_thrust_newtons": round(design.inputs.target_thrust_newtons, 2),
                "design_thrust_newtons": round(design_thrust_n, 2),
                "predicted_thrust_newtons": round(predicted_thrust_n, 2),
                "predicted_isp_seconds": round(predicted_isp_s, 2),
                "mass_flow_kg_s": round(mass_flow_kg_s, 4),
                "chamber_pressure_kpa": round(chamber_pressure_pa / 1000.0, 2),
                "exit_pressure_kpa": round(exit_pressure_kpa, 2),
            },
            "nozzle": {
                "status": nozzle_loss_model["status"],
                "flow_model": flow_model,
                "flow_model_label": nozzle_loss_model.get("flow_model_label", ""),
                "half_angle_deg": round(nozzle_loss_model["nozzle_half_angle_deg"], 3),
                "reference_half_angle_deg": round(nozzle_loss_model["reference_half_angle_deg"], 3),
                "divergence_efficiency": round(nozzle_loss_model["divergence_efficiency"], 4),
                "boundary_layer_efficiency": round(nozzle_loss_model["boundary_layer_efficiency"], 4),
                "geometry_efficiency": round(nozzle_loss_model["geometry_efficiency"], 4),
                "overall_efficiency": round(nozzle_loss_model["overall_efficiency"], 4),
                "loss_fraction": round(nozzle_loss_model["loss_fraction"], 4),
                "separation_efficiency": round(coefficient_state["separation_efficiency"], 4),
                "ambient_correction": round(coefficient_state["ambient_correction"], 6),
                "exit_pressure_kpa": round(exit_pressure_kpa, 3),
                "curvature_efficiency": round(float(nozzle_loss_model.get("curvature_efficiency", 1.0)), 4),
                "exit_angle_deg": round(float(nozzle_loss_model.get("exit_angle_deg", nozzle_loss_model["nozzle_half_angle_deg"])), 3),
                "bell_quality": round(float(nozzle_loss_model.get("bell_quality", 1.0)), 4),
            },
        },
        "warnings": [
            (
                "Refined mode uses contour-aware throat sizing, ambient-pressure correction, and a bell-nozzle loss model; it remains a reduced-order solver, not validated CFD."
                if flow_model == "refined"
                else "This is a quasi-1D proxy with geometry-aware nozzle losses, not validated high-fidelity CFD."
            ),
        ],
        "station_field_updates": station_field_updates,
    }
    report(100.0, "Step 5/5: Combustion solver finished")
    return result

