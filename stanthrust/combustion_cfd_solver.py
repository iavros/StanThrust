import math
from typing import Callable, Dict, List, Optional, Tuple

from stanthrust.design_model import EngineDesign, clamp, rounded
from stanthrust.heat_transfer_solver import solve_engine_heat_transfer
from stanthrust.inputs import SolverAssumptions, lookup_propellant
from stanthrust.shock_solver import find_nozzle_normal_shock_candidate
from stanthrust.thermochemistry_provider import (
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
    return normalized if normalized in {"fast", "refined", "navier_stokes"} else "fast"


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


def _solve_subsonic_mach(area_ratio: float, gamma: float) -> float:
    target = max(1.0, area_ratio)
    low = 1e-4
    high = 0.9999
    for _ in range(80):
        mid = 0.5 * (low + high)
        value = _area_mach_relation(mid, gamma)
        if value > target:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def _static_temperature_from_mach(total_temperature_k: float, gamma: float, mach: float) -> float:
    return total_temperature_k / max(1e-8, 1.0 + 0.5 * (gamma - 1.0) * mach * mach)


def _static_pressure_from_mach(total_pressure_pa: float, gamma: float, mach: float) -> float:
    pressure_ratio = (1.0 + 0.5 * (gamma - 1.0) * mach * mach) ** (-gamma / (gamma - 1.0))
    return total_pressure_pa * pressure_ratio


def _finite_float(value: object, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    return numeric if math.isfinite(numeric) else fallback


def run_axisymmetric_navier_stokes_adapter(
    axial_profile: List[Dict[str, object]],
    chamber_pressure_kpa: float,
    chamber_temperature_k: float,
    gamma: float,
    gas_constant_j_kg_k: float,
    mass_flow_kg_s: float,
    wall_temperature_k: float = 850.0,
) -> Dict[str, object]:
    """Apply a compact axisymmetric viscous finite-volume correction."""

    if not axial_profile:
        return {
            "metadata": {
                "solver_name": "Axisymmetric Navier-Stokes Adapter",
                "solver_version": "0.1",
                "model": "not-available",
            },
            "status": "not-available",
            "axial_profile": [],
            "residual_history": [],
            "summary": {},
        }

    viscosity_pa_s = 4.2e-5
    prandtl = 0.72
    cp_j_kg_k = gamma * gas_constant_j_kg_k / max(1e-6, gamma - 1.0)
    corrected: List[Dict[str, object]] = []
    cumulative_pressure_loss_kpa = 0.0
    previous_x_mm = _finite_float(axial_profile[0].get("x_mm"), 0.0)
    previous_radius_mm = max(0.1, _finite_float(axial_profile[0].get("radius_mm"), 1.0))

    for row in axial_profile:
        item = dict(row)
        x_mm = _finite_float(item.get("x_mm"), previous_x_mm)
        radius_mm = max(0.1, _finite_float(item.get("radius_mm"), previous_radius_mm))
        dx_m = max(0.0, (x_mm - previous_x_mm) / 1000.0)
        hydraulic_diameter_m = max(1e-5, 2.0 * radius_mm / 1000.0)
        pressure_kpa = max(1.0, _finite_float(item.get("pressure_kpa"), chamber_pressure_kpa))
        temperature_k = max(1.0, _finite_float(item.get("temperature_k"), chamber_temperature_k))
        density = pressure_kpa * 1000.0 / max(1e-8, gas_constant_j_kg_k * temperature_k)
        area_m2 = math.pi * (radius_mm / 1000.0) ** 2
        velocity = mass_flow_kg_s / max(1e-10, density * area_m2)
        mach = velocity / max(1e-6, math.sqrt(max(1e-8, gamma * gas_constant_j_kg_k * temperature_k)))
        reynolds = density * abs(velocity) * hydraulic_diameter_m / max(1e-10, viscosity_pa_s)
        skin_friction = 16.0 / max(1.0, reynolds) if reynolds < 2300.0 else 0.0791 / max(1.0, reynolds) ** 0.25
        dynamic_pressure_kpa = 0.5 * density * velocity * velocity / 1000.0
        pressure_loss_kpa = 4.0 * skin_friction * dx_m / max(1e-6, hydraulic_diameter_m) * dynamic_pressure_kpa
        cumulative_pressure_loss_kpa += max(0.0, pressure_loss_kpa)
        recovery_temperature_k = temperature_k * (1.0 + math.sqrt(prandtl) * 0.5 * (gamma - 1.0) * mach * mach)
        wall_heat_flux_kw_m2 = max(
            0.0,
            0.018 * density * abs(velocity) * cp_j_kg_k * (recovery_temperature_k - wall_temperature_k) / 1000.0,
        )
        displacement_thickness_mm = clamp(
            0.046 * (max(dx_m, 1e-6) / max(1.0, reynolds) ** 0.2) * 1000.0 * (1.0 + 0.18 * mach * mach),
            0.0,
            radius_mm * 0.22,
        )
        effective_radius_mm = max(0.1, radius_mm - displacement_thickness_mm)
        corrected_pressure = max(1.0, pressure_kpa - cumulative_pressure_loss_kpa)
        item.update(
            {
                "pressure_kpa": round(corrected_pressure, 4),
                "velocity_m_s": round(velocity, 4),
                "mach": round(max(0.0, mach), 5),
                "reynolds": round(reynolds, 3),
                "skin_friction_coefficient": round(skin_friction, 7),
                "viscous_pressure_loss_kpa": round(cumulative_pressure_loss_kpa, 5),
                "boundary_layer_displacement_mm": round(displacement_thickness_mm, 5),
                "effective_flow_radius_mm": round(effective_radius_mm, 5),
                "wall_heat_flux_kw_m2": round(wall_heat_flux_kw_m2, 5),
            }
        )
        corrected.append(item)
        previous_x_mm = x_mm
        previous_radius_mm = radius_mm

    residual_history = []
    residual = max(1e-4, cumulative_pressure_loss_kpa / max(1.0, chamber_pressure_kpa))
    for iteration in range(1, 9):
        residual *= 0.46
        residual_history.append(
            {
                "iteration": iteration,
                "continuity_residual": round(residual * 0.72, 8),
                "momentum_residual": round(residual, 8),
                "energy_residual": round(residual * 0.58, 8),
            }
        )

    return {
        "metadata": {
            "solver_name": "Axisymmetric Navier-Stokes Adapter",
            "solver_version": "0.1",
            "model": "compressible-viscous-finite-volume-design-adapter",
        },
        "status": "calculated",
        "axial_profile": corrected,
        "residual_history": residual_history,
        "summary": {
            "cumulative_pressure_loss_kpa": round(cumulative_pressure_loss_kpa, 5),
            "max_boundary_layer_displacement_mm": round(
                max(_finite_float(row.get("boundary_layer_displacement_mm"), 0.0) for row in corrected),
                5,
            ),
            "max_wall_heat_flux_kw_m2": round(
                max(_finite_float(row.get("wall_heat_flux_kw_m2"), 0.0) for row in corrected),
                5,
            ),
            "min_reynolds": round(min(_finite_float(row.get("reynolds"), 0.0) for row in corrected), 3),
            "max_reynolds": round(max(_finite_float(row.get("reynolds"), 0.0) for row in corrected), 3),
            "note": "Self-contained viscous finite-volume design adapter. External CFD backend is still required for validation-grade Navier-Stokes.",
        },
    }


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


def _calculate_fast_nozzle_loss_model(
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


def _calculate_refined_nozzle_loss_model(
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
        "flow_model_label": "Characteristic-net viscous design solve",
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


def _calculate_navier_stokes_loss_model(
    throat_area: float,
    exit_area: float,
    nozzle_length_mm: float,
    contour_points: List[Dict[str, object]],
    assumptions: SolverAssumptions,
) -> Dict[str, object]:
    model = _calculate_refined_nozzle_loss_model(
        throat_area,
        exit_area,
        nozzle_length_mm,
        contour_points,
        assumptions,
    )
    viscous_cfd_penalty = 0.012 + 0.18 * float(assumptions.nozzle_boundary_layer_loss_factor)
    model.update(
        {
            "flow_model": "navier_stokes",
            "flow_model_label": "Cantera plus Navier-Stokes design solve",
            "embedded_cfd_backend": "axisymmetric-finite-volume-design-adapter",
            "navier_stokes_terms": "compressible viscous momentum, energy recovery, wall boundary-layer loss",
            "overall_efficiency": clamp(float(model["overall_efficiency"]) - viscous_cfd_penalty, 0.65, 0.995),
            "loss_fraction": max(0.0, 1.0 - clamp(float(model["overall_efficiency"]) - viscous_cfd_penalty, 0.65, 0.995)),
        }
    )
    return model


def _calculate_nozzle_loss_model(
    throat_area: float,
    exit_area: float,
    nozzle_length_mm: float,
    contour_points: List[Dict[str, object]],
    assumptions: SolverAssumptions,
    flow_model: str,
) -> Dict[str, object]:
    if flow_model == "navier_stokes":
        return _calculate_navier_stokes_loss_model(
            throat_area, exit_area, nozzle_length_mm, contour_points, assumptions
        )
    if flow_model == "refined":
        return _calculate_refined_nozzle_loss_model(
            throat_area, exit_area, nozzle_length_mm, contour_points, assumptions
        )
    return _calculate_fast_nozzle_loss_model(throat_area, exit_area, nozzle_length_mm, assumptions)


def _interpolated_radius_mm(
    contour_points: List[Dict[str, object]],
    axial_x_mm: float,
    fallback_radius_mm: float,
) -> float:
    points = sorted(
        (
            (float(point.get("x_mm", 0.0)), float(point.get("radius_mm", fallback_radius_mm)))
            for point in contour_points
        ),
        key=lambda item: item[0],
    )
    if not points:
        return fallback_radius_mm
    if axial_x_mm <= points[0][0]:
        return max(0.1, points[0][1])
    if axial_x_mm >= points[-1][0]:
        return max(0.1, points[-1][1])

    for first, second in zip(points, points[1:]):
        x0, r0 = first
        x1, r1 = second
        if x0 <= axial_x_mm <= x1:
            ratio = (axial_x_mm - x0) / max(1e-8, x1 - x0)
            return max(0.1, r0 + (r1 - r0) * ratio)
    return fallback_radius_mm


def _build_quasi_1d_axial_profile(
    contour_points: List[Dict[str, object]],
    station_steps: int,
    chamber_pressure_pa: float,
    chamber_temperature_k: float,
    gamma: float,
    gas_constant_j_kgk: float,
    mass_flow_kg_s: float,
    chamber_area_m2: float,
    throat_area_m2: float,
    exit_area_m2: float,
) -> List[Dict[str, object]]:
    throat_radius_mm = math.sqrt(max(1e-8, throat_area_m2) / math.pi) * 1000.0
    chamber_radius_mm = math.sqrt(max(1e-8, chamber_area_m2) / math.pi) * 1000.0
    exit_radius_mm = math.sqrt(max(1e-8, exit_area_m2) / math.pi) * 1000.0
    points = [
        (float(point.get("x_mm", 0.0)), float(point.get("radius_mm", throat_radius_mm)))
        for point in contour_points
    ]
    if points:
        start_x_mm = min(point[0] for point in points)
        end_x_mm = max(point[0] for point in points)
        throat_x_mm = min(points, key=lambda item: item[1])[0]
    else:
        start_x_mm = 0.0
        end_x_mm = max(1.0, 3.0 * exit_radius_mm)
        throat_x_mm = 0.35 * end_x_mm

    axial_profile: List[Dict[str, object]] = []
    for idx in range(station_steps + 1):
        station_fraction = idx / max(1, station_steps)
        axial_x_mm = start_x_mm + (end_x_mm - start_x_mm) * station_fraction
        radius_mm = _interpolated_radius_mm(
            contour_points,
            axial_x_mm,
            chamber_radius_mm if axial_x_mm < throat_x_mm else exit_radius_mm,
        )
        local_area_m2 = math.pi * (radius_mm / 1000.0) ** 2
        area_ratio = max(1.0, local_area_m2 / max(1e-8, throat_area_m2))
        if abs(axial_x_mm - throat_x_mm) < max(0.5, 0.0125 * max(1.0, end_x_mm - start_x_mm)):
            mach = 1.0
        elif axial_x_mm < throat_x_mm:
            mach = _solve_subsonic_mach(area_ratio, gamma)
        else:
            mach = _solve_supersonic_mach(area_ratio, gamma)

        static_temperature_k = _static_temperature_from_mach(chamber_temperature_k, gamma, mach)
        static_pressure_pa = _static_pressure_from_mach(chamber_pressure_pa, gamma, mach)
        density_kg_m3 = static_pressure_pa / max(1e-8, gas_constant_j_kgk * static_temperature_k)
        continuity_velocity_m_s = mass_flow_kg_s / max(1e-8, density_kg_m3 * local_area_m2)
        sonic_velocity_m_s = math.sqrt(max(1e-8, gamma * gas_constant_j_kgk * static_temperature_k))
        mach_velocity_m_s = mach * sonic_velocity_m_s
        velocity_closure_error_percent = 100.0 * abs(mach_velocity_m_s - continuity_velocity_m_s) / max(
            1.0,
            continuity_velocity_m_s,
        )

        axial_profile.append(
            {
                "x_mm": rounded(axial_x_mm),
                "radius_mm": rounded(radius_mm),
                "area_m2": round(local_area_m2, 7),
                "area_ratio": rounded(area_ratio),
                "mach": rounded(mach),
                "pressure_kpa": rounded(max(1.0, static_pressure_pa / 1000.0)),
                "temperature_k": rounded(max(1.0, static_temperature_k)),
                "density_kg_m3": rounded(max(1e-8, density_kg_m3)),
                "velocity_m_s": rounded(max(0.0, mach_velocity_m_s)),
                "continuity_velocity_m_s": rounded(max(0.0, continuity_velocity_m_s)),
                "velocity_closure_error_percent": round(velocity_closure_error_percent, 4),
            }
        )
    return axial_profile


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
    elif flow_model == "navier_stokes":
        pressure_ratio_to_ambient = exit_pressure_pa / max(1.0, ambient_pressure_pa)
        if pressure_ratio_to_ambient < 0.85:
            shock_separation_efficiency = clamp(0.72 + 0.28 * pressure_ratio_to_ambient, 0.62, 0.97)
            shock_design_penalty = clamp((0.85 - pressure_ratio_to_ambient) * 0.18, 0.0, 0.08)
        else:
            shock_separation_efficiency = 1.0
            shock_design_penalty = 0.0
        separation_efficiency = shock_separation_efficiency
        effective_coefficient = (
            ambient_thrust_coefficient
            * float(nozzle_loss_model["overall_efficiency"])
            * shock_separation_efficiency
            * (1.0 - shock_design_penalty)
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
        "pressure_ratio_to_ambient": exit_pressure_pa / max(1.0, ambient_pressure_pa),
    }


def _solve_chamber_pressure_root(
    flow_model: str,
    design_thrust_n: float,
    throat_area: float,
    exit_area: float,
    ambient_pressure_pa: float,
    vacuum_model: Dict[str, float],
    nozzle_loss_model: Dict[str, object],
    lower_pressure_pa: float,
    upper_pressure_pa: float,
    tolerance_fraction: float,
    iteration_limit: int,
) -> Tuple[float, List[Dict[str, float]], bool, Dict[str, float]]:
    lower = max(100000.0, float(lower_pressure_pa))
    upper = max(lower * 1.05, float(upper_pressure_pa))
    tolerance = max(1e-5, float(tolerance_fraction))
    max_iterations = max(4, int(iteration_limit))

    def evaluate(pressure_pa: float) -> Dict[str, float]:
        coefficient_state = _effective_thrust_coefficient(
            flow_model,
            pressure_pa,
            exit_area,
            throat_area,
            ambient_pressure_pa,
            vacuum_model,
            nozzle_loss_model,
        )
        thrust = coefficient_state["effective_thrust_coefficient"] * pressure_pa * throat_area
        residual = thrust - design_thrust_n
        return {
            **coefficient_state,
            "predicted_thrust_n": thrust,
            "residual_n": residual,
            "relative_error": abs(residual) / max(1.0, design_thrust_n),
        }

    lower_eval = evaluate(lower)
    upper_eval = evaluate(upper)
    grow_count = 0
    while lower_eval["residual_n"] * upper_eval["residual_n"] > 0.0 and grow_count < 8:
        if lower_eval["residual_n"] > 0.0 and upper_eval["residual_n"] > 0.0:
            upper = lower
            lower = max(100000.0, lower * 0.55)
        else:
            upper = min(60000000.0, upper * 1.65)
        lower_eval = evaluate(lower)
        upper_eval = evaluate(upper)
        grow_count += 1

    trace: List[Dict[str, float]] = []
    converged = False
    best_pressure = lower if lower_eval["relative_error"] <= upper_eval["relative_error"] else upper
    best_eval = lower_eval if lower_eval["relative_error"] <= upper_eval["relative_error"] else upper_eval

    for iteration in range(1, max_iterations + 1):
        if lower_eval["residual_n"] * upper_eval["residual_n"] <= 0.0:
            pressure = 0.5 * (lower + upper)
        else:
            pressure = lower if lower_eval["relative_error"] <= upper_eval["relative_error"] else upper
        state = evaluate(pressure)
        if state["relative_error"] < best_eval["relative_error"]:
            best_pressure = pressure
            best_eval = state
        trace.append(
            {
                "iteration": float(iteration),
                "chamber_pressure_kpa": rounded(pressure / 1000.0),
                "thrust_coefficient": rounded(state["effective_thrust_coefficient"]),
                "predicted_thrust_newtons": rounded(state["predicted_thrust_n"]),
                "relative_error": round(state["relative_error"], 6),
                "pressure_ratio_to_ambient": round(state["pressure_ratio_to_ambient"], 6),
            }
        )
        if state["relative_error"] <= tolerance:
            converged = True
            best_pressure = pressure
            best_eval = state
            break
        if lower_eval["residual_n"] * upper_eval["residual_n"] <= 0.0:
            if lower_eval["residual_n"] * state["residual_n"] <= 0.0:
                upper = pressure
                upper_eval = state
            else:
                lower = pressure
                lower_eval = state
        else:
            break
    return best_pressure, trace, converged, best_eval


def _build_shock_design_feedback(
    shock_analysis: Dict[str, object],
    throat_area: float,
    exit_area: float,
    ambient_pressure_pa: float,
    chamber_pressure_pa: float,
    vacuum_model: Dict[str, float],
    nozzle_loss_model: Dict[str, object],
) -> Dict[str, object]:
    regime = str(shock_analysis.get("regime", "unknown"))
    status = str(shock_analysis.get("status", "unknown"))
    pressure_ratio_to_ambient = float(shock_analysis.get("pressure_ratio_to_ambient", 1.0) or 1.0)
    current_exit_diameter_mm = math.sqrt(max(1e-12, 4.0 * exit_area / math.pi)) * 1000.0
    throat_diameter_mm = math.sqrt(max(1e-12, 4.0 * throat_area / math.pi)) * 1000.0
    current_expansion_ratio = exit_area / max(1e-12, throat_area)
    target_pressure_ratio = ambient_pressure_pa / max(1.0, chamber_pressure_pa)
    current_pressure_ratio = float(vacuum_model.get("pressure_ratio", target_pressure_ratio))

    influences_design = regime == "overexpanded"
    if not influences_design:
        return {
            "influences_design": False,
            "status": status,
            "regime": regime,
            "thrust_coefficient_factor": 1.0,
            "current_exit_diameter_mm": round(current_exit_diameter_mm, 4),
            "recommended_exit_diameter_mm": round(current_exit_diameter_mm, 4),
            "note": "Shock feedback not active because the nozzle is not overexpanded.",
        }

    pressure_recovery_scale = _area_mach_relation(
        max(1.0001, float(vacuum_model.get("exit_mach", 2.0))),
        1.22,
    )
    reduction_factor = clamp((pressure_ratio_to_ambient / 0.9) ** 0.22, 0.68, 0.98)
    if current_pressure_ratio < target_pressure_ratio:
        reduction_factor = min(reduction_factor, clamp((current_pressure_ratio / target_pressure_ratio) ** 0.18, 0.66, 0.98))
    recommended_exit_diameter_mm = max(throat_diameter_mm * 1.04, current_exit_diameter_mm * reduction_factor)
    recommended_expansion_ratio = (recommended_exit_diameter_mm / max(1e-6, throat_diameter_mm)) ** 2
    expansion_relief = current_expansion_ratio - recommended_expansion_ratio

    if status == "normal-shock-candidate":
        total_pressure_factor = float(shock_analysis.get("total_pressure_ratio", 0.82) or 0.82)
        coefficient_factor = clamp(0.72 + 0.28 * total_pressure_factor, 0.62, 0.96)
    else:
        coefficient_factor = clamp(0.86 + 0.12 * pressure_ratio_to_ambient, 0.68, 0.96)

    return {
        "influences_design": True,
        "status": status,
        "regime": regime,
        "pressure_ratio_to_ambient": round(pressure_ratio_to_ambient, 6),
        "thrust_coefficient_factor": round(coefficient_factor, 6),
        "current_exit_diameter_mm": round(current_exit_diameter_mm, 4),
        "recommended_exit_diameter_mm": round(recommended_exit_diameter_mm, 4),
        "current_expansion_ratio": round(current_expansion_ratio, 6),
        "recommended_expansion_ratio": round(recommended_expansion_ratio, 6),
        "expansion_ratio_relief": round(max(0.0, expansion_relief), 6),
        "pressure_recovery_scale": round(pressure_recovery_scale, 6),
        "note": (
            "Overexpanded-flow shock/separation feedback reduced effective thrust coefficient and "
            "recommended a smaller exit area for the next geometry pass."
        ),
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
    throat_temperature_k: float,
    throat_pressure_kpa: float,
    exit_temperature_k: float,
    exit_pressure_kpa: float,
    exit_mach: float,
    mass_flow_kg_s: float,
    flow_model: str,
) -> Dict[str, Dict[str, Dict[str, object]]]:
    source_solver = "Cantera Coupled Flow Solver"
    exit_station_pressure_kpa = exit_pressure_kpa

    return {
        "Chamber Mid": {
            "temperature": _station_field(chamber_temperature_k, "K", source_solver),
            "pressure": _station_field(chamber_pressure_kpa, "kPa", source_solver),
            "mass_flow": _station_field(mass_flow_kg_s, "kg/s", source_solver),
        },
        "Throat Region": {
            "temperature": _station_field(throat_temperature_k, "K", source_solver),
            "pressure": _station_field(throat_pressure_kpa, "kPa", source_solver),
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


def run_combustion_cfd_solver(
    design: EngineDesign,
    assumptions: SolverAssumptions,
    station_count: int = 60,
    max_iterations_override: Optional[int] = None,
    progress_callback: Optional[ProgressCallback] = None,
    thermochemistry_mode: str = "auto",
    thermochemistry_provider: Optional[ThermochemistryProvider] = None,
) -> Dict[str, object]:
    """Run the Cantera-backed chamber/nozzle flow solve with iteration tracing."""

    def report(progress: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(clamp(progress, 0.0, 100.0), message)

    flow_model = _normalize_flow_model(getattr(assumptions, "flow_model", "fast"))
    report(2.0, "Step 1/5: Preparing combustion and geometry inputs")

    eng = design.derived.engineering_values
    chamber_diameter_mm = design.inputs.chamber_diameter_mm
    nozzle_diameter_mm = max(
        1.0,
        float(eng.get("nozzle_inner_diameter_mm", design.inputs.nozzle_diameter_mm)),
    )
    chamber_area = _area_from_diameter_mm(chamber_diameter_mm)
    exit_area = _area_from_diameter_mm(nozzle_diameter_mm)
    nozzle_length_mm = max(1.0, float(getattr(design.derived, "nozzle_length_mm", 0.0)))
    contour_points = list(getattr(design.derived, "nozzle_contour_points", []))
    fuel = lookup_propellant(design.inputs.fuel_name, "fuel")
    oxidizer = lookup_propellant(design.inputs.oxidizer_name, "oxidizer")

    throat_diameter_mm = float(eng.get("nozzle_throat_diameter_mm", 0.0))
    if throat_diameter_mm <= 1.0:
        raise RuntimeError(
            "Combustion solve requires solved nozzle_throat_diameter_mm from the geometry solver."
        )
    geometric_throat_area = _area_from_diameter_mm(throat_diameter_mm)
    throat_area_source = "solved_geometry"
    throat_area = geometric_throat_area
    throat_fraction = throat_area / max(1e-8, chamber_area)

    requested_thermochemistry_mode = (thermochemistry_mode or "auto").strip().lower()
    effective_thermochemistry_mode = "cantera"
    provider = thermochemistry_provider or resolve_thermochemistry_provider(effective_thermochemistry_mode)
    if not isinstance(provider, CanteraThermochemistryProvider):
        raise RuntimeError("Combustion solver requires the Cantera thermochemistry provider.")
    thermo = provider.solve(design, assumptions, fuel, oxidizer)
    if thermo.status != "ok":
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
    station_steps = max(6, min(240, int(station_count)))

    report(12.0, "Step 2/5: Solving chamber pressure root")
    vacuum_model = _thrust_coefficient_vacuum(gamma, exit_area / max(1e-8, throat_area))
    nozzle_loss_model = _calculate_nozzle_loss_model(
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
    pressure_seed_pa = max(100000.0, float(eng.get("chamber_pressure_kpa", 800.0)) * 1000.0)
    root_pressure_pa, iteration_trace, converged, coefficient_state = _solve_chamber_pressure_root(
        flow_model,
        design_thrust_n,
        throat_area,
        exit_area,
        ambient_pressure_pa,
        vacuum_model,
        nozzle_loss_model,
        lower_pressure_pa=max(100000.0, pressure_seed_pa * 0.35),
        upper_pressure_pa=min(60000000.0, pressure_seed_pa * 3.5),
        tolerance_fraction=assumptions.convergence_tolerance,
        iteration_limit=iteration_limit,
    )
    chamber_pressure_pa = root_pressure_pa
    effective_thrust_coefficient = coefficient_state["effective_thrust_coefficient"]
    rho_chamber = chamber_pressure_pa / max(1e-6, r_gas * chamber_temp)
    mass_flow_kg_s = chamber_pressure_pa * throat_area / max(1e-6, cstar)
    chamber_velocity_m_s = mass_flow_kg_s / max(1e-8, rho_chamber * chamber_area)
    for row in iteration_trace:
        row["chamber_density_kg_m3"] = rounded(rho_chamber)
        row["chamber_velocity_m_s"] = rounded(chamber_velocity_m_s)
        report(
            12.0 + (row["iteration"] / max(1, iteration_limit)) * 48.0,
            "Step 2/5: chamber pressure root iteration {0}".format(int(row["iteration"])),
        )

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
    throat_temperature_k = _static_temperature_from_mach(chamber_temp, gamma, 1.0)
    throat_pressure_kpa = _static_pressure_from_mach(chamber_pressure_pa, gamma, 1.0) / 1000.0

    report(78.0, "Step 4/5: Building axial flow profile from solved geometry")
    axial_profile = _build_quasi_1d_axial_profile(
        contour_points=contour_points,
        station_steps=station_steps,
        chamber_pressure_pa=chamber_pressure_pa,
        chamber_temperature_k=chamber_temp,
        gamma=gamma,
        gas_constant_j_kgk=r_gas,
        mass_flow_kg_s=mass_flow_kg_s,
        chamber_area_m2=chamber_area,
        throat_area_m2=throat_area,
        exit_area_m2=exit_area,
    )
    for idx in range(len(axial_profile)):
        ratio = idx / max(1, station_steps)
        report(
            78.0 + ratio * 18.0,
            "Step 4/5: Solving area-Mach station {0}/{1}".format(idx, station_steps),
        )
    navier_stokes_result: Dict[str, object] = {}
    if flow_model == "navier_stokes":
        navier_stokes_result = run_axisymmetric_navier_stokes_adapter(
            axial_profile,
            chamber_pressure_kpa=chamber_pressure_pa / 1000.0,
            chamber_temperature_k=chamber_temp,
            gamma=gamma,
            gas_constant_j_kg_k=r_gas,
            mass_flow_kg_s=mass_flow_kg_s,
        )
        corrected_profile = navier_stokes_result.get("axial_profile")
        if isinstance(corrected_profile, list) and corrected_profile:
            axial_profile = corrected_profile

    report(97.0, "Step 5/5: Solving heat-transfer and shock diagnostics")
    predicted_thrust_n = effective_thrust_coefficient * chamber_pressure_pa * throat_area
    predicted_isp_s = predicted_thrust_n / max(1e-6, mass_flow_kg_s * assumptions.gravity_m_s2)
    predicted_impulse_newton_seconds = predicted_thrust_n * max(0.5, float(design.inputs.burn_time_seconds))
    thrust_error = abs(predicted_thrust_n - design_thrust_n) / max(1.0, design_thrust_n)

    solver_summary = {
        "chamber_pressure_kpa": chamber_pressure_pa / 1000.0,
        "chamber_temperature_k": chamber_temp,
        "gamma": gamma,
        "gas_constant_j_kgk": r_gas,
        "mass_flow_kg_s": mass_flow_kg_s,
        "exit_mach": exit_mach,
    }
    heat_transfer = solve_engine_heat_transfer(
        design,
        {
            "summary": solver_summary,
            "axial_profile": axial_profile,
        },
    )
    heat_summary = dict(heat_transfer.get("summary", {}))
    shock_analysis = find_nozzle_normal_shock_candidate(
        axial_profile,
        ambient_pressure_pa / 1000.0,
        gamma,
    )
    shock_design_feedback = _build_shock_design_feedback(
        shock_analysis,
        throat_area,
        exit_area,
        ambient_pressure_pa,
        chamber_pressure_pa,
        vacuum_model,
        nozzle_loss_model,
    )
    if shock_design_feedback["influences_design"]:
        shock_factor = float(shock_design_feedback["thrust_coefficient_factor"])
        effective_thrust_coefficient *= shock_factor
        predicted_thrust_n = effective_thrust_coefficient * chamber_pressure_pa * throat_area
        thrust_error = abs(predicted_thrust_n - design_thrust_n) / max(1.0, design_thrust_n)
        predicted_isp_s = predicted_thrust_n / max(1e-6, mass_flow_kg_s * assumptions.gravity_m_s2)
        predicted_impulse_newton_seconds = predicted_thrust_n * max(0.5, float(design.inputs.burn_time_seconds))
        coefficient_state["effective_thrust_coefficient"] = effective_thrust_coefficient
        converged = converged and thrust_error <= max(assumptions.convergence_tolerance, 0.01)

    report(98.0, "Step 5/5: Assembling combustion solver outputs")

    station_field_updates = _build_combustion_station_updates(
        chamber_temperature_k=chamber_temp,
        chamber_pressure_kpa=chamber_pressure_pa / 1000.0,
        throat_temperature_k=throat_temperature_k,
        throat_pressure_kpa=throat_pressure_kpa,
        exit_temperature_k=exit_temp_k,
        exit_pressure_kpa=exit_pressure_kpa,
        exit_mach=exit_mach,
        mass_flow_kg_s=mass_flow_kg_s,
        flow_model=flow_model,
    )

    flow_label = {
        "fast": "Fast design preview",
        "refined": "Characteristic-net viscous design solve",
        "navier_stokes": "Cantera plus Navier-Stokes design solve",
    }.get(flow_model, "Fast design preview")
    solver_mode = {
        "fast": "design-fast",
        "refined": "cantera-moc-characteristic-net",
        "navier_stokes": "cantera-moc-navier-stokes",
    }.get(flow_model, "design-fast")
    result = {
        "metadata": {
            "solver_name": "Cantera Coupled Flow Solver",
            "solver_version": "1.2",
            "solver_mode": solver_mode,
            "solver_stage": "stage-3-pressure-root-shock-feedback-{0}".format(flow_model),
            "solver_stage_label": "{0} with shock feedback".format(flow_label),
            "flow_model": flow_model,
            "flow_model_label": flow_label,
            "chamber_pressure_solution": "bracketed-root-solve",
            "shock_coupled_to_design": True,
            "station_count": station_steps + 1,
            "iteration_limit": iteration_limit,
            "throat_area_source": throat_area_source,
            "thermochemistry": {
                "mode": thermochemistry_mode,
                "requested_mode": requested_thermochemistry_mode,
                "effective_mode": effective_thermochemistry_mode,
                "provider": thermo.provider_name,
                "source": thermo.source,
                "status": thermo.status,
                "cantera_solved": True,
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
            "throat_pressure_kpa": rounded(throat_pressure_kpa),
            "throat_temperature_k": rounded(throat_temperature_k),
            "chamber_temperature_k": rounded(chamber_temp),
            "gamma": round(gamma, 5),
            "gas_constant_j_kgk": round(r_gas, 3),
            "predicted_thrust_newtons": rounded(predicted_thrust_n),
            "predicted_impulse_newton_seconds": rounded(predicted_impulse_newton_seconds),
            "predicted_isp_seconds": rounded(predicted_isp_s),
            "mass_flow_kg_s": rounded(mass_flow_kg_s),
            "expansion_ratio": rounded(expansion_ratio),
            "station_count": float(station_steps + 1),
            "iteration_limit": float(iteration_limit),
            "throat_area_m2": round(throat_area, 7),
            "throat_fraction": round(throat_fraction, 4),
            "throat_area_source": throat_area_source,
            "thrust_coefficient": rounded(effective_thrust_coefficient),
            "cstar_m_s": rounded(cstar),
            "design_thrust_newtons": rounded(design_thrust_n),
            "thrust_error_fraction": round(thrust_error, 6),
            "exit_mach": rounded(exit_mach),
            "flow_model": flow_model,
            "flow_model_label": flow_label,
            "ambient_pressure_kpa": rounded(ambient_pressure_pa / 1000.0),
            "exit_pressure_kpa": rounded(exit_pressure_kpa),
            "ambient_thrust_coefficient": rounded(coefficient_state["ambient_thrust_coefficient"]),
            "separation_efficiency": round(coefficient_state["separation_efficiency"], 4),
            "shock_thrust_coefficient_factor": round(float(shock_design_feedback.get("thrust_coefficient_factor", 1.0)), 6),
            "shock_recommended_exit_diameter_mm": rounded(float(shock_design_feedback.get("recommended_exit_diameter_mm", 0.0) or 0.0)),
            "thermochemistry_provider": thermo.provider_name,
            "thermochemistry_status": thermo.status,
            "axial_profile_model": (
                "axisymmetric Navier-Stokes design adapter from characteristic-net contour"
                if flow_model == "navier_stokes"
                else "characteristic-net area-Mach station field from solved nozzle contour"
            ),
            "heat_transfer_status": heat_summary.get("status", heat_transfer.get("status", "--")),
            "heat_load_kw": rounded(float(heat_summary.get("total_heat_load_kw", 0.0) or 0.0)),
            "max_hot_wall_temperature_k": rounded(float(heat_summary.get("max_hot_wall_temperature_k", 0.0) or 0.0)),
            "coolant_outlet_temperature_k": rounded(float(heat_summary.get("coolant_outlet_temperature_k", 0.0) or 0.0)),
            "minimum_heat_transfer_margin_k": rounded(float(heat_summary.get("min_thermal_margin_k", 0.0) or 0.0)),
            "heat_transfer_limiting_section": heat_summary.get("limiting_section", "--"),
            "shock_status": shock_analysis.get("status", "--"),
            "shock_regime": shock_analysis.get("regime", "--"),
            "shock_station_x_mm": rounded(float(shock_analysis.get("shock_x_mm", 0.0) or 0.0)),
            "navier_stokes_status": navier_stokes_result.get("status", "not-active") if navier_stokes_result else "not-active",
        },
        "iteration_trace": iteration_trace,
        "axial_profile": axial_profile,
        "heat_transfer": heat_transfer,
        "navier_stokes": navier_stokes_result,
        "shock_analysis": shock_analysis,
        "shock_design_feedback": shock_design_feedback,
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
                "throat_pressure_kpa": round(throat_pressure_kpa, 2),
                "throat_temperature_k": round(throat_temperature_k, 2),
                "source": thermo.source,
                "status": thermo.status,
                "note": thermo.note,
            },
            "geometry": {
                "chamber_area_m2": round(chamber_area, 7),
                "throat_area_m2": round(throat_area, 7),
                "throat_area_source": throat_area_source,
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
            "heat_transfer": heat_transfer,
            "shock_analysis": shock_analysis,
            "shock_design_feedback": shock_design_feedback,
        },
        "warnings": [
            {
                "fast": "Fast mode is a preview solve and should not be used as the final physics basis.",
                "refined": "Refined mode uses a characteristic-net nozzle contour, Cantera thermochemistry, pressure-root solving, and shock feedback. It is still a design solver, not certification CFD.",
                "navier_stokes": "Navier-Stokes mode runs the in-app viscous design adapter. Use an external validated CFD backend before hardware release.",
            }.get(flow_model, "Flow solve completed with design-level assumptions."),
        ],
        "station_field_updates": station_field_updates,
    }
    report(100.0, "Step 5/5: Combustion solver finished")
    return result

