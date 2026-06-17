import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from liquid_engine_studio.propellants import PropellantOption, lookup_propellant


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def rounded(value: float) -> float:
    return round(value, 1)


PROPELLANT_DENSITY_KG_M3 = {
    "methane": 422.0,
    "ethanol": 789.0,
    "isopropyl alcohol": 786.0,
    "liquid oxygen": 1141.0,
    "nitrous oxide": 750.0,
    "hydrogen peroxide": 1400.0,
}

INJECTOR_TYPES = ("impinging", "pintle")


MATERIAL_ALLOWABLE_STRESS_MPA = {
    "Aluminum 6061-T6": 95.0,
    "Aluminum 7075-T6": 145.0,
    "Stainless Steel 304": 120.0,
    "Stainless Steel 316": 125.0,
    "Carbon Steel 1018": 110.0,
    "Titanium Grade 5": 240.0,
    "Copper C110": 70.0,
    "Inconel 625": 230.0,
}

MATERIAL_TEMPERATURE_LIMIT_K = {
    "Aluminum 6061-T6": 425.0,
    "Aluminum 7075-T6": 410.0,
    "Stainless Steel 304": 1080.0,
    "Stainless Steel 316": 1110.0,
    "Carbon Steel 1018": 880.0,
    "Titanium Grade 5": 720.0,
    "Copper C110": 640.0,
    "Inconel 625": 1250.0,
}

AMBIENT_TEMPERATURE_K = 293.0
CONCEPT_CHAMBER_TEMPERATURE_K = 3350.0
AMBIENT_PRESSURE_KPA = 101.3


def _lookup_density_kg_m3(option: PropellantOption) -> float:
    key = option.name.strip().lower()
    if key in PROPELLANT_DENSITY_KG_M3:
        return PROPELLANT_DENSITY_KG_M3[key]
    return clamp(option.density_index * 1000.0, 420.0, 1500.0)


def _estimate_wall_thickness_mm(
    pressure_kpa: float, diameter_mm: float, material: str, factor_of_safety: float
) -> float:
    allowable_mpa = MATERIAL_ALLOWABLE_STRESS_MPA.get(material, 105.0)
    radius_m = diameter_mm / 2000.0
    thickness_m = (
        (pressure_kpa * 1000.0)
        * radius_m
        * factor_of_safety
        / (allowable_mpa * 1_000_000.0)
    )
    return clamp(thickness_m * 1000.0, 1.0, 16.0)


def _estimate_hoop_stress_mpa(pressure_kpa: float, diameter_mm: float, wall_thickness_mm: float) -> float:
    radius_m = max(1e-6, diameter_mm / 2000.0)
    wall_thickness_m = max(1e-6, wall_thickness_mm / 1000.0)
    return (pressure_kpa * 1000.0 * radius_m / wall_thickness_m) / 1_000_000.0


def _temperature_limit_k(material: str) -> float:
    return MATERIAL_TEMPERATURE_LIMIT_K.get(material, 700.0)


def _area_ratio_from_mach(mach_number: float, gamma: float) -> float:
    mach_number = max(1e-6, mach_number)
    gamma_factor = (gamma + 1.0) / (2.0 * (gamma - 1.0))
    pressure_term = 1.0 + (gamma - 1.0) * 0.5 * mach_number * mach_number
    normalized_term = (2.0 / (gamma + 1.0)) * pressure_term
    return (1.0 / mach_number) * (normalized_term**gamma_factor)


def _estimate_supersonic_mach_from_area_ratio(area_ratio: float, gamma: float = 1.22) -> float:
    """Estimate nozzle exit Mach from A/A* on the supersonic branch."""
    if area_ratio <= 1.0:
        return 1.0

    mach_number = clamp(area_ratio**0.2, 1.2, 4.5)

    for _ in range(30):
        error = _area_ratio_from_mach(mach_number, gamma) - area_ratio

        small_step = 1e-6
        higher_mach = mach_number + small_step
        higher_error = _area_ratio_from_mach(higher_mach, gamma) - area_ratio
        slope = (higher_error - error) / small_step

        if abs(slope) < 1e-12:
            break

        correction = error / slope
        mach_number -= correction
        mach_number = clamp(mach_number, 1.0 + 1e-6, 10.0)

        if abs(correction) < 1e-6:
            break

    return float(clamp(mach_number, 1.0, 10.0))


def _prandtl_meyer_angle_deg(mach_number: float, gamma: float = 1.22) -> float:
    mach_number = max(1.0 + 1e-6, float(mach_number))
    gm1 = gamma - 1.0
    gp1 = gamma + 1.0
    angle_rad = math.sqrt(gp1 / gm1) * math.atan(math.sqrt(gm1 / gp1 * (mach_number * mach_number - 1.0)))
    angle_rad -= math.atan(math.sqrt(mach_number * mach_number - 1.0))
    return math.degrees(max(0.0, angle_rad))


def _moc_nozzle_angle_metadata(
    expansion_ratio: float,
    bell_length_fraction: float,
    gamma: float = 1.22,
) -> Dict[str, float]:
    exit_mach = _estimate_supersonic_mach_from_area_ratio(expansion_ratio, gamma=gamma)
    prandtl_meyer_exit_deg = _prandtl_meyer_angle_deg(exit_mach, gamma=gamma)
    ideal_turn_angle_deg = prandtl_meyer_exit_deg * 0.5
    entrance_angle_deg = clamp(ideal_turn_angle_deg, 18.0, 34.0)
    truncation_fraction = clamp(1.0 - bell_length_fraction, 0.0, 0.45)
    exit_angle_deg = clamp(ideal_turn_angle_deg * (0.13 + truncation_fraction * 0.28), 3.0, 10.5)
    return {
        "gamma": gamma,
        "exit_mach": exit_mach,
        "prandtl_meyer_exit_deg": prandtl_meyer_exit_deg,
        "ideal_turn_angle_deg": ideal_turn_angle_deg,
        "entrance_angle_deg": entrance_angle_deg,
        "exit_angle_deg": exit_angle_deg,
    }


def _build_section_margin_metrics(
    pressure_kpa: float,
    diameter_mm: float,
    wall_thickness_mm: float,
    material: str,
    wall_temperature_k: float,
) -> Dict[str, float]:
    allowable_stress_mpa = MATERIAL_ALLOWABLE_STRESS_MPA.get(material, 105.0)
    hoop_stress_mpa = _estimate_hoop_stress_mpa(pressure_kpa, diameter_mm, wall_thickness_mm)
    structural_margin_ratio = allowable_stress_mpa / max(1e-6, hoop_stress_mpa)
    temperature_limit_k = _temperature_limit_k(material)
    thermal_margin_k = temperature_limit_k - wall_temperature_k
    thermal_margin_ratio = temperature_limit_k / max(1e-6, wall_temperature_k)
    thermal_margin_index = clamp(thermal_margin_ratio * 100.0, 0.0, 100.0)
    return {
        "pressure_kpa": rounded(pressure_kpa),
        "diameter_mm": rounded(diameter_mm),
        "wall_thickness_mm": rounded(wall_thickness_mm),
        "allowable_stress_mpa": rounded(allowable_stress_mpa),
        "hoop_stress_mpa": rounded(hoop_stress_mpa),
        "structural_margin_ratio": round(structural_margin_ratio, 3),
        "wall_temperature_k": rounded(wall_temperature_k),
        "temperature_limit_k": rounded(temperature_limit_k),
        "thermal_margin_k": rounded(thermal_margin_k),
        "thermal_margin_ratio": round(thermal_margin_ratio, 3),
        "thermal_margin_index": round(thermal_margin_index, 2),
    }


def _cap_outer_diameter(required_outer_diameter_mm: float, limit_outer_diameter_mm: float) -> Tuple[float, float, bool]:
    capped_outer_diameter_mm = min(float(required_outer_diameter_mm), float(limit_outer_diameter_mm))
    excess_diameter_mm = max(0.0, float(required_outer_diameter_mm) - float(limit_outer_diameter_mm))
    return capped_outer_diameter_mm, excess_diameter_mm, excess_diameter_mm > 1e-6


def _append_contour_sample(
    samples: List[Tuple[float, float, str]],
    axial_mm: float,
    radius_mm: float,
    section: str,
) -> None:
    x_value = max(0.0, float(axial_mm))
    radius_value = max(0.1, float(radius_mm))
    if samples and abs(samples[-1][0] - x_value) < 1e-6 and abs(samples[-1][1] - radius_value) < 1e-6:
        samples[-1] = (x_value, radius_value, section)
        return
    samples.append((x_value, radius_value, section))


def _solve_bell_control_point(
    start_x_mm: float,
    start_radius_mm: float,
    exit_x_mm: float,
    exit_radius_mm: float,
    entrance_angle_rad: float,
    exit_angle_rad: float,
) -> Tuple[float, float]:
    slope_start = math.tan(entrance_angle_rad)
    slope_exit = math.tan(exit_angle_rad)
    denominator = slope_start - slope_exit
    if abs(denominator) < 1e-6:
        return ((start_x_mm + exit_x_mm) * 0.5, (start_radius_mm + exit_radius_mm) * 0.5)
    control_x_mm = (
        exit_radius_mm
        - slope_exit * exit_x_mm
        - start_radius_mm
        + slope_start * start_x_mm
    ) / denominator
    control_x_mm = clamp(control_x_mm, start_x_mm + 0.15, exit_x_mm - 0.15)
    control_radius_mm = start_radius_mm + slope_start * (control_x_mm - start_x_mm)
    return control_x_mm, control_radius_mm


@dataclass(frozen=True)
class PressureSolution:
    chamber_pressure_kpa: float
    fuel_tank_pressure_kpa: float
    oxidizer_tank_pressure_kpa: float
    pump_differential_pressure_kpa: float
    pump_discharge_pressure_kpa: float
    injector_pressure_drop_kpa: float
    required_feed_pressure_kpa: float
    feed_line_pressure_drop_kpa: float
    fuel_pressure_margin_kpa: float
    oxidizer_pressure_margin_kpa: float
    pressure_target_adjustment_kpa: float
    residual_kpa: float
    iterations: int
    status: str


def _solve_pressure_state(
    use_pumps: bool,
    target_chamber_pressure_kpa: float,
    propellant_mass_flow_kg_s: float,
    feed_system_bay_length_mm: float,
) -> PressureSolution:
    """Solve a feasible concept-stage pressure state with explicit margins."""
    feed_line_pressure_drop_kpa = (
        48.0 + propellant_mass_flow_kg_s * 21.0 + feed_system_bay_length_mm * 0.24
    )
    injector_ratio = 0.16

    if use_pumps:
        fuel_tank_pressure_kpa = clamp(
            260.0 + feed_line_pressure_drop_kpa * 0.32,
            180.0,
            1600.0,
        )
        oxidizer_tank_pressure_kpa = clamp(
            290.0 + feed_line_pressure_drop_kpa * 0.36,
            200.0,
            1800.0,
        )
        pump_head_reserve_kpa = clamp(55.0 + feed_line_pressure_drop_kpa * 0.04, 35.0, 140.0)
        max_feasible_chamber_kpa = min(
            (4200.0 - pump_head_reserve_kpa + fuel_tank_pressure_kpa - feed_line_pressure_drop_kpa)
            / (1.0 + injector_ratio),
            (4200.0 - pump_head_reserve_kpa + oxidizer_tank_pressure_kpa - feed_line_pressure_drop_kpa)
            / (1.0 + injector_ratio),
            7000.0,
        )
        chamber_pressure_kpa = clamp(
            min(target_chamber_pressure_kpa, max_feasible_chamber_kpa),
            650.0,
            7000.0,
        )
        injector_pressure_drop_kpa = chamber_pressure_kpa * injector_ratio
        required_feed_pressure_kpa = (
            chamber_pressure_kpa + injector_pressure_drop_kpa + feed_line_pressure_drop_kpa
        )
        pump_differential_pressure_kpa = clamp(
            max(
                required_feed_pressure_kpa - fuel_tank_pressure_kpa,
                required_feed_pressure_kpa - oxidizer_tank_pressure_kpa,
            )
            + pump_head_reserve_kpa,
            160.0,
            4200.0,
        )
        fuel_pressure_margin_kpa = (
            fuel_tank_pressure_kpa + pump_differential_pressure_kpa - required_feed_pressure_kpa
        )
        oxidizer_pressure_margin_kpa = (
            oxidizer_tank_pressure_kpa + pump_differential_pressure_kpa - required_feed_pressure_kpa
        )
        pump_discharge_pressure_kpa = min(
            fuel_tank_pressure_kpa + pump_differential_pressure_kpa,
            oxidizer_tank_pressure_kpa + pump_differential_pressure_kpa,
        )
        status = "feasible" if chamber_pressure_kpa >= target_chamber_pressure_kpa - 1e-6 else "capped_by_pump_head"
    else:
        fuel_tank_reserve_kpa = 140.0
        oxidizer_tank_reserve_kpa = 180.0
        max_feasible_chamber_kpa = min(
            (6200.0 - fuel_tank_reserve_kpa - feed_line_pressure_drop_kpa) / (1.0 + injector_ratio),
            (6400.0 - oxidizer_tank_reserve_kpa - feed_line_pressure_drop_kpa) / (1.0 + injector_ratio),
            5200.0,
        )
        chamber_pressure_kpa = clamp(
            min(target_chamber_pressure_kpa, max_feasible_chamber_kpa),
            600.0,
            5200.0,
        )
        injector_pressure_drop_kpa = chamber_pressure_kpa * injector_ratio
        required_feed_pressure_kpa = (
            chamber_pressure_kpa + injector_pressure_drop_kpa + feed_line_pressure_drop_kpa
        )
        fuel_tank_pressure_kpa = clamp(
            required_feed_pressure_kpa + fuel_tank_reserve_kpa,
            850.0,
            6200.0,
        )
        oxidizer_tank_pressure_kpa = clamp(
            required_feed_pressure_kpa + oxidizer_tank_reserve_kpa,
            900.0,
            6400.0,
        )
        fuel_pressure_margin_kpa = fuel_tank_pressure_kpa - required_feed_pressure_kpa
        oxidizer_pressure_margin_kpa = oxidizer_tank_pressure_kpa - required_feed_pressure_kpa
        pump_differential_pressure_kpa = 0.0
        pump_discharge_pressure_kpa = 0.0
        status = (
            "feasible"
            if chamber_pressure_kpa >= target_chamber_pressure_kpa - 1e-6
            else "capped_by_tank_pressure"
        )

    return PressureSolution(
        chamber_pressure_kpa=chamber_pressure_kpa,
        fuel_tank_pressure_kpa=fuel_tank_pressure_kpa,
        oxidizer_tank_pressure_kpa=oxidizer_tank_pressure_kpa,
        pump_differential_pressure_kpa=pump_differential_pressure_kpa,
        pump_discharge_pressure_kpa=pump_discharge_pressure_kpa,
        injector_pressure_drop_kpa=injector_pressure_drop_kpa,
        required_feed_pressure_kpa=required_feed_pressure_kpa,
        feed_line_pressure_drop_kpa=feed_line_pressure_drop_kpa,
        fuel_pressure_margin_kpa=max(0.0, fuel_pressure_margin_kpa),
        oxidizer_pressure_margin_kpa=max(0.0, oxidizer_pressure_margin_kpa),
        pressure_target_adjustment_kpa=max(0.0, target_chamber_pressure_kpa - chamber_pressure_kpa),
        residual_kpa=0.0,
        iterations=1,
        status=status,
    )


def _build_nozzle_contour_points(
    chamber_diameter_mm: float,
    throat_diameter_mm: float,
    exit_diameter_mm: float,
    converging_length_mm: float,
    diverging_length_mm: float,
    converging_angle_deg: Optional[float] = None,
    diverging_angle_deg: Optional[float] = None,
    converging_samples: int = 10,
    diverging_samples: int = 22,
) -> List[Dict[str, object]]:
    chamber_radius = max(0.1, chamber_diameter_mm / 2.0)
    throat_radius = max(0.1, throat_diameter_mm / 2.0)
    exit_radius = max(0.1, exit_diameter_mm / 2.0)
    total_length = max(1.0, converging_length_mm + diverging_length_mm)
    conical_reference_half_angle_rad = math.radians(15.0)
    expansion_ratio = max(1.0, pow(exit_radius / max(0.1, throat_radius), 2))
    equivalent_conical_length_mm = max(
        diverging_length_mm,
        (exit_radius - throat_radius) / max(1e-6, math.tan(conical_reference_half_angle_rad)),
    )
    actual_bell_length_fraction = clamp(
        diverging_length_mm / max(1.0, equivalent_conical_length_mm),
        0.55,
        1.1,
    )
    converging_angle_rad = math.radians(
        converging_angle_deg
        if converging_angle_deg is not None
        else math.degrees(math.atan2(chamber_radius - throat_radius, max(1.0, converging_length_mm)))
    )
    reference_exit_angle_deg = 14.5 - 1.25 * math.log(max(expansion_ratio, 1.0))
    moc_angles = _moc_nozzle_angle_metadata(expansion_ratio, actual_bell_length_fraction)
    bell_exit_angle_deg = clamp(
        diverging_angle_deg if diverging_angle_deg is not None else moc_angles["exit_angle_deg"],
        3.0,
        max(10.5, reference_exit_angle_deg),
    )
    bell_entrance_angle_deg = clamp(
        moc_angles["entrance_angle_deg"],
        max(18.0, bell_exit_angle_deg + 10.0),
        34.0,
    )
    bell_entrance_angle_rad = math.radians(bell_entrance_angle_deg)
    bell_exit_angle_rad = math.radians(bell_exit_angle_deg)
    upstream_blend_radius_mm = max(1.25 * throat_radius, throat_radius + 2.0)
    downstream_blend_radius_mm = max(0.382 * throat_radius, 2.0)

    samples: List[Tuple[float, float, str]] = []

    upstream_arc_dx_mm = upstream_blend_radius_mm * math.sin(converging_angle_rad)
    upstream_arc_dr_mm = upstream_blend_radius_mm * (1.0 - math.cos(converging_angle_rad))
    upstream_tangent_x_mm = converging_length_mm - upstream_arc_dx_mm
    upstream_tangent_radius_mm = throat_radius + upstream_arc_dr_mm
    straight_start_x_mm = upstream_tangent_x_mm - (
        (chamber_radius - upstream_tangent_radius_mm) / max(1e-6, math.tan(converging_angle_rad))
    )

    if straight_start_x_mm > 0.0:
        _append_contour_sample(samples, 0.0, chamber_radius, "chamber_entry")
        _append_contour_sample(samples, straight_start_x_mm, chamber_radius, "chamber_entry")
        for index in range(1, max(2, converging_samples)):
            t_value = index / float(max(1, converging_samples - 1))
            axial_mm = straight_start_x_mm + (upstream_tangent_x_mm - straight_start_x_mm) * t_value
            radius_mm = chamber_radius - (chamber_radius - upstream_tangent_radius_mm) * t_value
            _append_contour_sample(samples, axial_mm, radius_mm, "converging")
        arc_samples = max(4, converging_samples // 2 + 2)
        for index in range(1, arc_samples):
            phi = converging_angle_rad * (1.0 - index / float(arc_samples - 1))
            axial_mm = converging_length_mm - upstream_blend_radius_mm * math.sin(phi)
            radius_mm = throat_radius + upstream_blend_radius_mm * (1.0 - math.cos(phi))
            _append_contour_sample(
                samples,
                axial_mm,
                radius_mm,
                "throat" if index == arc_samples - 1 else "throat_blend_in",
            )
    else:
        _append_contour_sample(samples, 0.0, chamber_radius, "chamber_entry")
        hermite_exit_slope = -math.tan(converging_angle_rad) * converging_length_mm
        for index in range(1, max(2, converging_samples)):
            t_value = index / float(max(1, converging_samples - 1))
            h00 = 2.0 * pow(t_value, 3) - 3.0 * pow(t_value, 2) + 1.0
            h10 = pow(t_value, 3) - 2.0 * pow(t_value, 2) + t_value
            h01 = -2.0 * pow(t_value, 3) + 3.0 * pow(t_value, 2)
            h11 = pow(t_value, 3) - pow(t_value, 2)
            radius_mm = (
                h00 * chamber_radius
                + h10 * 0.0
                + h01 * throat_radius
                + h11 * hermite_exit_slope
            )
            axial_mm = converging_length_mm * t_value
            _append_contour_sample(
                samples,
                axial_mm,
                radius_mm,
                "throat" if index == max(2, converging_samples) - 1 else "converging",
            )

    downstream_arc_end_x_mm = downstream_blend_radius_mm * math.sin(bell_entrance_angle_rad)
    downstream_arc_end_radius_mm = throat_radius + downstream_blend_radius_mm * (
        1.0 - math.cos(bell_entrance_angle_rad)
    )

    if downstream_arc_end_x_mm >= diverging_length_mm - 0.5:
        downstream_arc_end_x_mm = max(0.25, diverging_length_mm * 0.2)
        downstream_arc_end_radius_mm = throat_radius + (exit_radius - throat_radius) * 0.18

    bell_control_x_mm, bell_control_radius_mm = _solve_bell_control_point(
        downstream_arc_end_x_mm,
        downstream_arc_end_radius_mm,
        diverging_length_mm,
        exit_radius,
        bell_entrance_angle_rad,
        bell_exit_angle_rad,
    )

    arc_samples = max(5, diverging_samples // 4 + 2)
    for index in range(1, arc_samples):
        phi = bell_entrance_angle_rad * (index / float(arc_samples - 1))
        axial_mm = converging_length_mm + downstream_blend_radius_mm * math.sin(phi)
        radius_mm = throat_radius + downstream_blend_radius_mm * (1.0 - math.cos(phi))
        _append_contour_sample(samples, axial_mm, radius_mm, "diverging_arc")

    bezier_samples = max(6, diverging_samples)
    for index in range(1, bezier_samples):
        t_value = index / float(bezier_samples - 1)
        one_minus_t = 1.0 - t_value
        moc_relax = 0.5 - 0.5 * math.cos(math.pi * t_value)
        axial_mm = converging_length_mm + (
            one_minus_t * one_minus_t * downstream_arc_end_x_mm
            + 2.0 * one_minus_t * t_value * bell_control_x_mm
            + t_value * t_value * diverging_length_mm
        )
        radius_mm = (
            one_minus_t * one_minus_t * downstream_arc_end_radius_mm
            + 2.0 * one_minus_t * t_value * bell_control_radius_mm
            + t_value * t_value * exit_radius
        )
        ideal_radius_mm = downstream_arc_end_radius_mm + (exit_radius - downstream_arc_end_radius_mm) * moc_relax
        radius_mm = radius_mm * 0.68 + ideal_radius_mm * 0.32
        _append_contour_sample(
            samples,
            axial_mm,
            radius_mm,
            "exit" if index == bezier_samples - 1 else "bell",
        )

    points: List[Dict[str, object]] = []
    for axial_mm, radius_mm, section in samples:
        points.append(
            {
                "x_mm": rounded(axial_mm),
                "radius_mm": rounded(radius_mm),
                "diameter_mm": rounded(radius_mm * 2.0),
                "section": section,
                "normalized_x": round(axial_mm / total_length, 4),
            }
        )
    return points


@dataclass
class DesignInputs:
    fuel_name: str
    oxidizer_name: str
    mixture_ratio: float
    injector_type: str
    target_thrust_newtons: float
    target_impulse_newton_seconds: float
    target_diameter_mm: float
    burn_time_seconds: float
    tank_diameter_mm: float
    chamber_diameter_mm: float
    nozzle_diameter_mm: float
    fuel_tank_material: str
    oxidizer_tank_material: str
    feed_system_material: str
    chamber_material: str
    nozzle_material: str
    factor_of_safety: float
    packaging_bias: str
    use_pumps: bool
    regen_cooling: bool
    film_cooling: bool

    @classmethod
    def from_mapping(cls, raw_state: Dict[str, object]) -> "DesignInputs":
        injector_type = str(raw_state.get("injector_type", "impinging") or "impinging").strip().lower()
        if injector_type not in INJECTOR_TYPES:
            injector_type = "impinging"
        return cls(
            fuel_name=(str(raw_state.get("fuel_name", "Fuel")).strip() or "Fuel"),
            oxidizer_name=(
                str(raw_state.get("oxidizer_name", "Oxidizer")).strip() or "Oxidizer"
            ),
            mixture_ratio=clamp(float(raw_state.get("mixture_ratio", 1.4) or 1.4), 0.1, 10.0),
            injector_type=injector_type,
            target_thrust_newtons=clamp(
                float(raw_state.get("target_thrust_newtons", 250.0) or 250.0), 1.0, 50000.0
            ),
            target_impulse_newton_seconds=clamp(
                float(raw_state.get("target_impulse_newton_seconds", 3000.0) or 3000.0),
                1.0,
                500000.0,
            ),
            target_diameter_mm=clamp(
                float(raw_state.get("target_diameter_mm", 110.0) or 110.0), 20.0, 300.0
            ),
            burn_time_seconds=clamp(
                float(raw_state.get("burn_time_seconds", 12.0) or 12.0), 1.0, 120.0
            ),
            tank_diameter_mm=clamp(
                float(raw_state.get("tank_diameter_mm", 110.0) or 110.0), 30.0, 300.0
            ),
            chamber_diameter_mm=clamp(
                float(raw_state.get("chamber_diameter_mm", 68.0) or 68.0), 20.0, 200.0
            ),
            nozzle_diameter_mm=clamp(
                float(raw_state.get("nozzle_diameter_mm", 95.0) or 95.0), 20.0, 250.0
            ),
            fuel_tank_material=str(
                raw_state.get("fuel_tank_material", "Aluminum 6061-T6") or "Aluminum 6061-T6"
            ),
            oxidizer_tank_material=str(
                raw_state.get("oxidizer_tank_material", "Aluminum 6061-T6")
                or "Aluminum 6061-T6"
            ),
            feed_system_material=str(
                raw_state.get("feed_system_material", "Stainless Steel 304")
                or "Stainless Steel 304"
            ),
            chamber_material=str(
                raw_state.get("chamber_material", "Stainless Steel 304")
                or "Stainless Steel 304"
            ),
            nozzle_material=str(
                raw_state.get("nozzle_material", "Stainless Steel 304")
                or "Stainless Steel 304"
            ),
            factor_of_safety=clamp(float(raw_state.get("factor_of_safety", 2.0) or 2.0), 1.0, 5.0),
            packaging_bias=str(raw_state.get("packaging_bias", "balanced") or "balanced"),
            use_pumps=bool(raw_state.get("use_pumps", False)),
            regen_cooling=bool(raw_state.get("regen_cooling", False)),
            film_cooling=bool(raw_state.get("film_cooling", False)),
        )

    def as_state(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class MeasurementRow:
    label: str
    value: str
    numeric_value: Optional[float] = None
    unit: str = ""


@dataclass
class SummaryCard:
    label: str
    value: str


@dataclass
class StationSample:
    label: str
    axial_position_mm: float
    envelope_diameter_mm: float
    area_index: float
    shell_complexity_index: float
    temperature_note: str
    pressure_note: str
    mass_flow_note: str
    mach_note: str
    thermal_margin_index: float = 0.0
    thermal_margin_note: str = ""
    # Numeric, machine-friendly fields (Stage 2.4 promotion)
    temperature_k: Optional[float] = None
    pressure_kpa: Optional[float] = None
    mass_flow_kg_s: Optional[float] = None
    mach_number: Optional[float] = None
    density_kg_m3: Optional[float] = None
    velocity_m_s: Optional[float] = None


@dataclass
class SubsystemPlaceholder:
    label: str
    status: str
    note: str


@dataclass
class FeedMode:
    label: str
    tank_pressure_band: str
    control_note: str


@dataclass
class SolverMeta:
    solver_name: str
    solver_version: str
    solver_mode: str


@dataclass
class DerivedDesign:
    solver_meta: SolverMeta
    feed_mode: FeedMode
    fuel_tank_length_mm: float
    oxidizer_tank_length_mm: float
    chamber_length_mm: float
    nozzle_length_mm: float
    injector_plate_diameter_mm: float
    feed_system_bay_length_mm: float
    coolant_jacket_thickness_mm: float
    total_stack_length_mm: float
    maximum_diameter_mm: float
    chamber_surface_area_mm2: float
    nozzle_shell_area_mm2: float
    dry_mass_index: float
    packaging_efficiency_index: float
    thermal_margin_index: float
    complexity_index: float
    cad_stations_mm: Dict[str, float] = field(default_factory=dict)
    visualization_hints: Dict[str, float] = field(default_factory=dict)
    station_rows: List[StationSample] = field(default_factory=list)
    subsystem_placeholders: List[SubsystemPlaceholder] = field(default_factory=list)
    measurement_rows: List[MeasurementRow] = field(default_factory=list)
    nozzle_contour_points: List[Dict[str, object]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    summary: List[SummaryCard] = field(default_factory=list)
    calculation_stages: List[str] = field(default_factory=list)
    engineering_values: Dict[str, float] = field(default_factory=dict)


@dataclass
class ConceptDesign:
    inputs: DesignInputs
    fuel: PropellantOption
    oxidizer: PropellantOption
    derived: DerivedDesign

    def as_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["inputs"]["mixture_ratio"] = rounded(self.inputs.mixture_ratio)
        payload["inputs"]["burn_time_seconds"] = rounded(self.inputs.burn_time_seconds)
        return payload

    def as_input_state(self) -> Dict[str, object]:
        return self.inputs.as_state()


@dataclass
class GeometrySizing:
    thrust_scale: float
    impulse_scale: float
    mixture_bias: float
    fuel_tank_length_mm: float
    oxidizer_tank_length_mm: float
    chamber_length_mm: float
    nozzle_throat_diameter_mm: float
    nozzle_converging_length_mm: float
    nozzle_diverging_length_mm: float
    nozzle_length_mm: float
    nozzle_expansion_ratio: float
    nozzle_converging_angle_deg: float
    nozzle_diverging_angle_deg: float
    nozzle_contour_points: List[Dict[str, object]]
    nozzle_equivalent_conical_length_mm: float
    nozzle_bell_length_fraction: float
    nozzle_bell_exit_angle_deg: float
    nozzle_bell_entrance_angle_deg: float
    nozzle_moc_gamma: float
    nozzle_moc_exit_mach: float
    nozzle_moc_prandtl_meyer_exit_deg: float
    nozzle_moc_turn_angle_deg: float
    nozzle_throat_entry_blend_radius_mm: float
    nozzle_throat_exit_blend_radius_mm: float
    injector_plate_diameter_mm: float
    injector_plate_thickness_mm: float
    feed_system_bay_length_mm: float
    coolant_jacket_thickness_mm: float
    total_stack_length_mm: float
    chamber_surface_area_mm2: float
    nozzle_shell_area_mm2: float
    complexity_index: float
    dry_mass_index: float
    packaging_efficiency_index: float
    thermal_margin_index: float


def _calculate_geometry_sizing(
    inputs: DesignInputs,
    fuel: PropellantOption,
    oxidizer: PropellantOption,
    packaging: Dict[str, float],
) -> GeometrySizing:
    thrust_scale = clamp(pow(inputs.target_thrust_newtons / 250.0, 0.22), 0.7, 2.4)
    impulse_scale = clamp(pow(inputs.target_impulse_newton_seconds / 3000.0, 0.16), 0.75, 2.2)

    burn_factor = 0.72 + (inputs.burn_time_seconds / 28.0)
    mixture_bias = clamp(inputs.mixture_ratio / (1.0 + inputs.mixture_ratio), 0.18, 0.86)
    fuel_density_term = 1.0 + (0.62 - fuel.density_index) * 0.42
    oxidizer_density_term = 1.0 + (0.68 - oxidizer.density_index) * 0.36
    blowdown_factor = 1.0 if inputs.use_pumps else 1.2
    regen_relief = 0.92 if inputs.regen_cooling else 1.0
    film_extension = 1.04 if inputs.film_cooling else 1.0
    propellant_thermal_factor = 1.0 + oxidizer.thermal_severity * 0.12 - fuel.cooling_affinity * 0.08

    fuel_tank_length_mm = (
        inputs.tank_diameter_mm
        * packaging["tank_length"]
        * burn_factor
        * impulse_scale
        * fuel_density_term
        * (1.04 - mixture_bias * 0.22)
        * blowdown_factor
    )
    oxidizer_tank_length_mm = (
        inputs.tank_diameter_mm
        * packaging["tank_length"]
        * burn_factor
        * impulse_scale
        * oxidizer_density_term
        * (0.88 + mixture_bias * 0.36)
        * blowdown_factor
    )
    chamber_length_mm = (
        inputs.chamber_diameter_mm
        * packaging["chamber_length"]
        * thrust_scale
        * propellant_thermal_factor
        * regen_relief
    )
    nozzle_throat_diameter_mm = clamp(
        inputs.chamber_diameter_mm * (0.38 + thrust_scale * 0.03),
        10.0,
        inputs.nozzle_diameter_mm * 0.72,
    )
    nozzle_converging_length_mm = clamp(
        inputs.chamber_diameter_mm * (0.42 + thrust_scale * 0.07),
        10.0,
        inputs.chamber_diameter_mm * 1.55,
    )
    nozzle_diverging_length_mm = clamp(
        inputs.nozzle_diameter_mm * (0.58 + thrust_scale * 0.09) * film_extension,
        18.0,
        inputs.nozzle_diameter_mm * 1.8,
    )
    nozzle_length_mm = nozzle_converging_length_mm + nozzle_diverging_length_mm
    nozzle_expansion_ratio = pow(inputs.nozzle_diameter_mm / max(1.0, nozzle_throat_diameter_mm), 2)

    nozzle_converging_angle_deg = clamp(
        math.degrees(
            math.atan(
                (inputs.chamber_diameter_mm - nozzle_throat_diameter_mm)
                / max(1.0, 2.0 * nozzle_converging_length_mm)
            )
        ),
        12.0,
        38.0,
    )
    nozzle_diverging_angle_deg = clamp(
        math.degrees(
            math.atan(
                (inputs.nozzle_diameter_mm - nozzle_throat_diameter_mm)
                / max(1.0, 2.0 * nozzle_diverging_length_mm)
            )
        ),
        6.0,
        22.0,
    )
    nozzle_equivalent_conical_length_mm = max(
        nozzle_diverging_length_mm,
        (
            (inputs.nozzle_diameter_mm - nozzle_throat_diameter_mm)
            / max(1e-6, 2.0 * math.tan(math.radians(15.0)))
        ),
    )
    nozzle_bell_length_fraction = clamp(
        nozzle_diverging_length_mm / max(1.0, nozzle_equivalent_conical_length_mm),
        0.55,
        1.1,
    )
    moc_angles = _moc_nozzle_angle_metadata(nozzle_expansion_ratio, nozzle_bell_length_fraction)
    nozzle_bell_exit_angle_deg = clamp(
        moc_angles["exit_angle_deg"],
        3.0,
        10.5,
    )
    nozzle_bell_entrance_angle_deg = clamp(
        moc_angles["entrance_angle_deg"],
        max(18.0, nozzle_bell_exit_angle_deg + 10.0),
        34.0,
    )
    nozzle_contour_points = _build_nozzle_contour_points(
        inputs.chamber_diameter_mm,
        nozzle_throat_diameter_mm,
        inputs.nozzle_diameter_mm,
        nozzle_converging_length_mm,
        nozzle_diverging_length_mm,
        converging_angle_deg=nozzle_converging_angle_deg,
        diverging_angle_deg=None,
    )
    nozzle_throat_entry_blend_radius_mm = max(
        1.25 * nozzle_throat_diameter_mm / 2.0,
        nozzle_throat_diameter_mm / 2.0 + 2.0,
    )
    nozzle_throat_exit_blend_radius_mm = max(0.382 * nozzle_throat_diameter_mm / 2.0, 2.0)

    injector_plate_diameter_mm = inputs.chamber_diameter_mm * (1.09 if inputs.film_cooling else 1.03)
    injector_plate_thickness_mm = clamp(inputs.chamber_diameter_mm * 0.075, 3.5, 16.0)
    feed_system_bay_length_mm = inputs.chamber_diameter_mm * 1.18 if inputs.use_pumps else inputs.tank_diameter_mm * 1.02
    coolant_jacket_thickness_mm = (
        clamp(inputs.chamber_diameter_mm * 0.085, 4.0, 14.0) if inputs.regen_cooling else 0.0
    )
    total_stack_length_mm = (
        fuel_tank_length_mm
        + oxidizer_tank_length_mm
        + chamber_length_mm
        + nozzle_length_mm
        + feed_system_bay_length_mm
        + (18.0 if inputs.use_pumps else 32.0)
    )

    chamber_radius = inputs.chamber_diameter_mm / 2.0
    chamber_surface_area_mm2 = 2.0 * math.pi * chamber_radius * chamber_length_mm
    nozzle_shell_area_mm2 = math.pi * inputs.nozzle_diameter_mm * nozzle_length_mm * 0.82
    complexity_index = clamp(
        34.0
        + (12.0 if inputs.use_pumps else 8.0)
        + (14.0 if inputs.regen_cooling else 0.0)
        + (9.0 if inputs.film_cooling else 0.0)
        + (fuel.handling_complexity + oxidizer.handling_complexity) * 12.0,
        20.0,
        95.0,
    )
    dry_mass_index = clamp(
        18.0
        + chamber_surface_area_mm2 / 6200.0
        + nozzle_shell_area_mm2 / 9000.0
        + total_stack_length_mm / 42.0
        + complexity_index * 0.45,
        12.0,
        98.0,
    )
    packaging_efficiency_index = clamp(
        92.0
        - (total_stack_length_mm / max(1.0, max(inputs.tank_diameter_mm, inputs.nozzle_diameter_mm))) * 2.2
        - complexity_index * 0.14
        + (4.0 if inputs.packaging_bias == "compact" else 0.0)
        + (2.0 if inputs.use_pumps else -1.5),
        5.0,
        96.0,
    )
    chamber_loading = chamber_length_mm / max(1.0, inputs.chamber_diameter_mm)
    thermal_margin_index = clamp(
        54.0
        + fuel.cooling_affinity * 18.0
        - oxidizer.thermal_severity * 12.0
        + (16.0 if inputs.regen_cooling else 0.0)
        + (8.0 if inputs.film_cooling else 0.0)
        - chamber_loading * 4.5
        - (3.0 if not inputs.use_pumps else 0.0),
        8.0,
        94.0,
    )

    return GeometrySizing(
        thrust_scale=thrust_scale,
        impulse_scale=impulse_scale,
        mixture_bias=mixture_bias,
        fuel_tank_length_mm=fuel_tank_length_mm,
        oxidizer_tank_length_mm=oxidizer_tank_length_mm,
        chamber_length_mm=chamber_length_mm,
        nozzle_throat_diameter_mm=nozzle_throat_diameter_mm,
        nozzle_converging_length_mm=nozzle_converging_length_mm,
        nozzle_diverging_length_mm=nozzle_diverging_length_mm,
        nozzle_length_mm=nozzle_length_mm,
        nozzle_expansion_ratio=nozzle_expansion_ratio,
        nozzle_converging_angle_deg=nozzle_converging_angle_deg,
        nozzle_diverging_angle_deg=nozzle_diverging_angle_deg,
        nozzle_contour_points=nozzle_contour_points,
        nozzle_equivalent_conical_length_mm=nozzle_equivalent_conical_length_mm,
        nozzle_bell_length_fraction=nozzle_bell_length_fraction,
        nozzle_bell_exit_angle_deg=nozzle_bell_exit_angle_deg,
        nozzle_bell_entrance_angle_deg=nozzle_bell_entrance_angle_deg,
        nozzle_moc_gamma=moc_angles["gamma"],
        nozzle_moc_exit_mach=moc_angles["exit_mach"],
        nozzle_moc_prandtl_meyer_exit_deg=moc_angles["prandtl_meyer_exit_deg"],
        nozzle_moc_turn_angle_deg=moc_angles["ideal_turn_angle_deg"],
        nozzle_throat_entry_blend_radius_mm=nozzle_throat_entry_blend_radius_mm,
        nozzle_throat_exit_blend_radius_mm=nozzle_throat_exit_blend_radius_mm,
        injector_plate_diameter_mm=injector_plate_diameter_mm,
        injector_plate_thickness_mm=injector_plate_thickness_mm,
        feed_system_bay_length_mm=feed_system_bay_length_mm,
        coolant_jacket_thickness_mm=coolant_jacket_thickness_mm,
        total_stack_length_mm=total_stack_length_mm,
        chamber_surface_area_mm2=chamber_surface_area_mm2,
        nozzle_shell_area_mm2=nozzle_shell_area_mm2,
        complexity_index=complexity_index,
        dry_mass_index=dry_mass_index,
        packaging_efficiency_index=packaging_efficiency_index,
        thermal_margin_index=thermal_margin_index,
    )


def get_packaging_multiplier(packaging_bias: str) -> Dict[str, float]:
    if packaging_bias == "compact":
        return {"tank_length": 1.88, "chamber_length": 1.38, "nozzle_length": 1.08}
    if packaging_bias == "serviceable":
        return {"tank_length": 2.45, "chamber_length": 1.82, "nozzle_length": 1.34}
    return {"tank_length": 2.14, "chamber_length": 1.58, "nozzle_length": 1.2}


def get_feed_mode(use_pumps: bool) -> FeedMode:
    if use_pumps:
        return FeedMode(
            label="Pump-fed design",
            tank_pressure_band="lower pressure envelope",
            control_note="Electric impeller modules included in the layout envelope",
        )
    return FeedMode(
        label="Blowdown design",
        tank_pressure_band="higher pressure tank envelope",
        control_note="Pressurant volume reserved and pump modules removed",
    )


def _measurement_row_from_value(
    label: str,
    values: Dict[str, object],
    key: str,
    unit: str,
    fallback: object = "--",
) -> MeasurementRow:
    value = values.get(key, fallback)
    display_value = str(value) if unit == "" else f"{value} {unit}"
    numeric_value = None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = None
    return MeasurementRow(label, display_value, numeric_value, unit)


def _add_measurement_rows(
    rows: List[MeasurementRow],
    values: Dict[str, object],
    row_specs: List[Tuple[str, str, str, object]],
) -> None:
    for label, key, unit, fallback in row_specs:
        rows.append(_measurement_row_from_value(label, values, key, unit, fallback))


def build_measurement_rows(inputs: DesignInputs, derived: DerivedDesign) -> List[MeasurementRow]:
    values = derived.engineering_values
    injector_type = str(values.get("injector_type", inputs.injector_type))
    chamber_volume_l = rounded(
        math.pi * pow(inputs.chamber_diameter_mm / 2000.0, 2) * derived.chamber_length_mm * 1000.0
    )

    rows = [
        MeasurementRow("Chamber body length", f"{rounded(derived.chamber_length_mm)} mm", rounded(derived.chamber_length_mm), "mm"),
        MeasurementRow("Chamber surface area", f"{rounded(derived.chamber_surface_area_mm2)} mm^2", rounded(derived.chamber_surface_area_mm2), "mm^2"),
        MeasurementRow("Chamber volume", f"{chamber_volume_l} L", chamber_volume_l, "L"),
    ]

    base_rows = [
        ("Fuel tank outer diameter", "fuel_tank_outer_diameter_mm", "mm", inputs.tank_diameter_mm),
        ("Fuel tank inner diameter", "fuel_tank_inner_diameter_mm", "mm", "--"),
        ("Fuel tank wall thickness", "fuel_tank_wall_thickness_mm", "mm", "--"),
        ("Fuel tank required length", "fuel_tank_required_length_mm", "mm", "--"),
        ("Fuel tank propellant mass", "fuel_mass_kg", "kg", "--"),
        ("Fuel tank propellant volume", "fuel_volume_l", "L", "--"),
        ("Fuel tank feed mass flow", "fuel_mass_flow_kg_s", "kg/s", "--"),
        ("Fuel tank pressurization (modeled)", "fuel_tank_pressure_kpa", "kPa", "--"),
        ("Oxidizer tank outer diameter", "oxidizer_tank_outer_diameter_mm", "mm", inputs.tank_diameter_mm),
        ("Oxidizer tank inner diameter", "oxidizer_tank_inner_diameter_mm", "mm", "--"),
        ("Oxidizer tank wall thickness", "oxidizer_tank_wall_thickness_mm", "mm", "--"),
        ("Oxidizer tank required length", "oxidizer_tank_required_length_mm", "mm", "--"),
        ("Oxidizer tank propellant mass", "oxidizer_mass_kg", "kg", "--"),
        ("Oxidizer tank propellant volume", "oxidizer_volume_l", "L", "--"),
        ("Oxidizer tank feed mass flow", "oxidizer_mass_flow_kg_s", "kg/s", "--"),
        ("Oxidizer tank pressurization (modeled)", "oxidizer_tank_pressure_kpa", "kPa", "--"),
        ("Chamber inner diameter", "chamber_inner_diameter_mm", "mm", inputs.chamber_diameter_mm),
        ("Chamber outer diameter", "chamber_outer_diameter_mm", "mm", inputs.chamber_diameter_mm),
        ("Chamber wall thickness", "chamber_wall_thickness_mm", "mm", 0.0),
        ("Chamber pressure", "chamber_pressure_kpa", "kPa", "--"),
        ("Injector face diameter", "injector_face_diameter_mm", "mm", "--"),
        ("Injector face thickness", "injector_face_thickness_mm", "mm", "--"),
    ]
    _add_measurement_rows(rows, values, base_rows)

    if inputs.regen_cooling:
        regen_rows = [
            ("Regen chamber outer diameter", "chamber_regen_outer_diameter_mm", "mm", "--"),
            ("Regen nozzle outer diameter", "nozzle_regen_outer_diameter_mm", "mm", "--"),
            ("Regen inner wall thickness", "regen_inner_wall_thickness_mm", "mm", "--"),
            ("Regen channel depth", "regen_channel_depth_mm", "mm", "--"),
            ("Regen outer jacket thickness", "regen_outer_jacket_thickness_mm", "mm", "--"),
            ("Regen total radial thickness", "regen_total_radial_thickness_mm", "mm", "--"),
            ("Regen rib height", "regen_rib_height_mm", "mm", "--"),
            ("Regen rib thickness", "regen_rib_thickness_mm", "mm", "--"),
            ("Regen channel width", "regen_channel_width_mm", "mm", "--"),
            ("Regen channel pitch", "regen_channel_pitch_mm", "mm", "--"),
            ("Regen channel count", "regen_channel_count", "count", "--"),
            ("Regen hydraulic diameter", "regen_hydraulic_diameter_mm", "mm", "--"),
            ("Regen coolant mass flow", "regen_coolant_mass_flow_kg_s", "kg/s", "--"),
            ("Regen coolant velocity", "regen_coolant_velocity_m_s", "m/s", "--"),
            ("Regen pressure drop", "regen_pressure_drop_kpa", "kPa", "--"),
            ("Regen thermal model status", "regen_thermal_model_status", "", "--"),
            ("Regen minimum thermal margin", "regen_min_thermal_margin_index", "index", "--"),
            ("Regen limiting section", "regen_limiting_section", "", "--"),
            ("Regen chamber thermal margin", "regen_section_chamber_mid_margin_index", "index", "--"),
            ("Regen throat thermal margin", "regen_section_throat_region_margin_index", "index", "--"),
            ("Regen nozzle exit thermal margin", "regen_section_nozzle_exit_plane_margin_index", "index", "--"),
        ]
        _add_measurement_rows(rows, values, regen_rows)
    else:
        rows.append(MeasurementRow("Regen thermal model status", "not calculated in concept-only mode", None, ""))
        rows.append(MeasurementRow("Regen minimum thermal margin", "0.0", 0.0, "index"))

    nozzle_rows = [
        ("Nozzle inner diameter", "nozzle_inner_diameter_mm", "mm", inputs.nozzle_diameter_mm),
        ("Nozzle outer diameter", "nozzle_outer_diameter_mm", "mm", inputs.nozzle_diameter_mm),
        ("Nozzle wall thickness", "nozzle_wall_thickness_mm", "mm", 0.0),
    ]
    _add_measurement_rows(rows, values, nozzle_rows)

    for prefix, label in (
        ("fuel_tank", "Fuel tank"),
        ("oxidizer_tank", "Oxidizer tank"),
        ("chamber", "Chamber"),
        ("throat", "Throat region"),
        ("nozzle", "Nozzle"),
    ):
        wall_key = f"{prefix}_wall_thickness_mm"
        if prefix == "nozzle":
            wall_key = "nozzle_structural_wall_thickness_mm"
        margin_rows = [
            (f"{label} hoop stress", f"{prefix}_hoop_stress_mpa", "MPa", "--"),
            (f"{label} allowable stress", f"{prefix}_allowable_stress_mpa", "MPa", "--"),
            (f"{label} structural wall", wall_key, "mm", "--"),
            (f"{label} structural margin", f"{prefix}_structural_margin_ratio", "x", "--"),
            (f"{label} wall temperature", f"{prefix}_estimated_wall_temperature_k", "K", "--"),
            (f"{label} temperature limit", f"{prefix}_temperature_limit_k", "K", "--"),
            (f"{label} thermal margin", f"{prefix}_thermal_margin_k", "K", "--"),
        ]
        _add_measurement_rows(rows, values, margin_rows)

    if inputs.film_cooling:
        film_rows = [
            ("Film mass flow", "film_mass_flow_kg_s", "kg/s", "--"),
            ("Film slot height", "film_slot_height_mm", "mm", "--"),
            ("Film slot width", "film_slot_width_mm", "mm", "--"),
            ("Film slot count", "film_slot_count", "count", "--"),
            ("Film injection angle", "film_injection_angle_deg", "deg", "--"),
            ("Film injection velocity", "film_injection_velocity_m_s", "m/s", "--"),
            ("Film coverage fraction", "film_coverage_fraction", "fraction", "--"),
        ]
        _add_measurement_rows(rows, values, film_rows)

    if inputs.use_pumps:
        pump_rows = [
            ("Fuel impeller diameter", "fuel_impeller_diameter_mm", "mm", "--"),
            ("Fuel impeller hub diameter", "fuel_impeller_hub_diameter_mm", "mm", "--"),
            ("Fuel impeller eye diameter", "fuel_impeller_eye_diameter_mm", "mm", "--"),
            ("Fuel impeller blade count", "fuel_impeller_blade_count", "count", "--"),
            ("Fuel impeller blade angle", "fuel_impeller_blade_angle_deg", "deg", "--"),
            ("Fuel impeller blade thickness", "fuel_impeller_blade_thickness_mm", "mm", "--"),
            ("Fuel impeller tip clearance", "fuel_impeller_tip_clearance_mm", "mm", "--"),
            ("Oxidizer impeller diameter", "oxidizer_impeller_diameter_mm", "mm", "--"),
            ("Oxidizer impeller hub diameter", "oxidizer_impeller_hub_diameter_mm", "mm", "--"),
            ("Oxidizer impeller eye diameter", "oxidizer_impeller_eye_diameter_mm", "mm", "--"),
            ("Oxidizer impeller blade count", "oxidizer_impeller_blade_count", "count", "--"),
            ("Oxidizer impeller blade angle", "oxidizer_impeller_blade_angle_deg", "deg", "--"),
            ("Oxidizer impeller blade thickness", "oxidizer_impeller_blade_thickness_mm", "mm", "--"),
            ("Oxidizer impeller tip clearance", "oxidizer_impeller_tip_clearance_mm", "mm", "--"),
            ("Electric motor power", "electric_motor_power_kw", "kW", "--"),
            ("Electric motor torque", "electric_motor_torque_nm", "N*m", "--"),
            ("Electric motor speed", "electric_motor_speed_rpm", "rpm", "--"),
        ]
        _add_measurement_rows(rows, values, pump_rows)

    rows.extend(
        [
            MeasurementRow("Nozzle concept length", f"{rounded(derived.nozzle_length_mm)} mm", rounded(derived.nozzle_length_mm), "mm"),
            MeasurementRow("Nozzle contour points", f"{len(derived.nozzle_contour_points)} points", float(len(derived.nozzle_contour_points)), "points"),
            MeasurementRow("Pump or pressurization bay length", f"{rounded(derived.feed_system_bay_length_mm)} mm", rounded(derived.feed_system_bay_length_mm), "mm"),
            MeasurementRow("Cooling jacket allowance", f"{rounded(derived.coolant_jacket_thickness_mm)} mm", rounded(derived.coolant_jacket_thickness_mm), "mm"),
            MeasurementRow("Overall stacked assembly length", f"{rounded(derived.total_stack_length_mm)} mm", rounded(derived.total_stack_length_mm), "mm"),
        ]
    )
    geometry_rows = [
        ("Maximum outer diameter", "maximum_diameter_mm", "mm", rounded(derived.maximum_diameter_mm)),
        ("Target diameter limit", "target_outer_diameter_limit_mm", "mm", inputs.target_diameter_mm),
        ("Uncapped maximum outer diameter", "maximum_required_outer_diameter_mm", "mm", rounded(derived.maximum_diameter_mm)),
        ("Diameter limit status", "diameter_limit_status", "", "--"),
        ("Nozzle converging length", "nozzle_converging_length_mm", "mm", "--"),
        ("Nozzle throat diameter", "nozzle_throat_diameter_mm", "mm", "--"),
        ("Nozzle diverging length", "nozzle_diverging_length_mm", "mm", "--"),
        ("Nozzle expansion ratio", "nozzle_expansion_ratio", "", "--"),
    ]
    _add_measurement_rows(rows, values, geometry_rows)

    if injector_type == "pintle":
        injector_rows = [
            ("Pintle tip diameter", "pintle_tip_diameter_mm", "mm", "--"),
            ("Pintle stem diameter", "pintle_stem_diameter_mm", "mm", "--"),
            ("Pintle annulus gap", "pintle_annulus_gap_mm", "mm", "--"),
            ("Pintle projection length", "pintle_projection_length_mm", "mm", "--"),
        ]
    else:
        injector_rows = [
            ("Impinging injector orifice diameter", "impinging_orifice_diameter_mm", "mm", "--"),
            ("Impinging injector angle", "impinging_angle_deg", "deg", "--"),
            ("Impinging injector pair spacing", "impinging_pair_spacing_mm", "mm", "--"),
            ("Impinging injector element count", "impinging_element_count", "", "--"),
        ]
    _add_measurement_rows(rows, values, injector_rows)
    return rows


def build_notes(inputs: DesignInputs, design: ConceptDesign) -> List[str]:
    derived = design.derived
    values = derived.engineering_values
    notes = [
        "This project intentionally stays at the level of concept visualization and software architecture. The measurements and scores shown are non-operational placeholders for CAD blockout and software planning only. They are not manufacturing dimensions, propulsion calculations, test parameters, or build instructions.",
        "{mode} selected: {note}.".format(
            mode=derived.feed_mode.label, note=derived.feed_mode.control_note
        ),
        "Propellant set is {fuel} / {oxidizer} with an O/F input of {mixture}.".format(
            fuel=inputs.fuel_name,
            oxidizer=inputs.oxidizer_name,
            mixture=rounded(inputs.mixture_ratio),
        ),
        "Concept targets captured for future solver integration: thrust {thrust} N, impulse {impulse} N*s, diameter {diameter} mm.".format(
            thrust=rounded(inputs.target_thrust_newtons),
            impulse=rounded(inputs.target_impulse_newton_seconds),
            diameter=rounded(inputs.target_diameter_mm),
        ),
        "Solver mode is {mode} using catalog-driven conceptual coefficients rather than propulsion-grade performance calculations.".format(
            mode=derived.solver_meta.solver_mode
        ),
        "CAD measurements shown here are envelope placeholders for concept blockout, not manufacturing or test values.",
        "Conceptual indices: packaging {packaging}, thermal margin {thermal}, dry-mass proxy {mass}.".format(
            packaging=rounded(derived.packaging_efficiency_index),
            thermal=rounded(derived.thermal_margin_index),
            mass=rounded(derived.dry_mass_index),
        ),
        "Pump, impeller, electric motor, injector geometry, and the MOC-informed bell nozzle contour remain reduced-order sizing outputs in this app; they are intended for preliminary iteration rather than detailed hardware release.",
        "Station exports include conceptual geometry and explicit not-calculated markers for thermofluid fields that are outside concept mode.",
        "Materials are now used for section-based structural stress and wall-temperature margin estimates for the tanks, chamber, throat, and nozzle.",
        "Selected injector family: {0}.".format(values.get("injector_type", inputs.injector_type)),
    ]

    if inputs.regen_cooling:
        notes.append(
            "Regenerative cooling enabled: outer diameter accounts for inner wall thickness, channel depth, and outer jacket thickness. Final OD = input diameter + 2 × (inner wall + channel depth + outer jacket)."
        )
        notes.append(
            "Per-section thermal margin proxies are exported for the feed inlet, pump bay, injector face, chamber mid, throat, and nozzle exit stations."
        )
    else:
        notes.append(
            "Regenerative cooling disabled: the chamber shell is shown without a cooling jacket path."
        )

    if inputs.film_cooling:
        notes.append(
            "Film cooling enabled: the injector-side geometry reserves a perimeter flow feature in the concept rendering."
        )

    if not inputs.use_pumps:
        notes.append(
            "Blowdown mode increases tank-length allowance to reserve pressurant headspace in the conceptual packaging model."
        )

    notes.append(
        "The current layout envelope estimate is {length} mm long within a maximum body width of {width} mm.".format(
            length=rounded(derived.total_stack_length_mm),
            width=rounded(derived.maximum_diameter_mm),
        )
    )
    if values.get("diameter_limit_status") == "capped_by_target_diameter":
        notes.append(
            "The outer envelope was hard-capped at the target diameter. The unconstrained maximum outer diameter was {required} mm against a target limit of {limit} mm.".format(
                required=values.get("maximum_required_outer_diameter_mm", "--"),
                limit=values.get("target_outer_diameter_limit_mm", "--"),
            )
        )
    return notes


class ConceptSolver:
    """Concept-only solver for geometry, packaging, and abstract design scores."""

    solver_name = "Concept Envelope Solver"
    solver_version = "2.0"
    solver_mode = "geometry-and-index"

    @staticmethod
    def _build_station_rows(
        inputs: DesignInputs,
        feed_system_bay_length_mm: float,
        chamber_length_mm: float,
        nozzle_length_mm: float,
        chamber_envelope_diameter_mm: float,
        throat_diameter_mm: float,
        nozzle_exit_diameter_mm: float,
        regen_thermal_margins: Dict[str, float],
        regen_thermal_note: str,
        *,
        chamber_pressure_kpa: float = 0.0,
        fuel_tank_pressure_kpa: float = 0.0,
        propellant_mass_flow_kg_s: float = 0.0,
        nozzle_expansion_ratio: float = 1.0,
        nozzle_throat_diameter_mm: float = 0.0,
    ) -> List[StationSample]:
        chamber_mid = feed_system_bay_length_mm + chamber_length_mm * 0.45
        throat_axial = feed_system_bay_length_mm + chamber_length_mm + nozzle_length_mm * 0.24
        exit_axial = feed_system_bay_length_mm + chamber_length_mm + nozzle_length_mm
        feed_diameter = max(inputs.chamber_diameter_mm * 0.42, 16.0)
        throat_diameter = max(throat_diameter_mm, 18.0)

        # Basic station annotator: where the concept solver already computes
        # pressures, mass flows, and nozzle geometry we promote a few human-
        # readable station fields from placeholder text to calculated strings.
        note = "Not calculated in concept-only mode"
        thermal_note = regen_thermal_note or note

        def fmt_kpa(v: float) -> str:
            return f"{rounded(v)} kPa" if v and v > 0.0 else note

        def fmt_mass(v: float) -> str:
            return f"{rounded(v)} kg/s" if v and v > 0.0 else note

        def fmt_temp_k(v: float) -> str:
            return f"{rounded(v)} K" if v and v > 0.0 else note

        exit_area_ratio = max(1.0, nozzle_expansion_ratio)
        exit_mach_est = _estimate_supersonic_mach_from_area_ratio(exit_area_ratio)

        def margin(key: str) -> float:
            return rounded(float(regen_thermal_margins.get(key, 0.0)))

        return [
                StationSample(
                    label="Fuel Feed Inlet",
                    axial_position_mm=0.0,
                    envelope_diameter_mm=rounded(feed_diameter),
                    area_index=0.42,
                    shell_complexity_index=0.36,
                    temperature_note=fmt_temp_k(AMBIENT_TEMPERATURE_K),
                    pressure_note=fmt_kpa(fuel_tank_pressure_kpa),
                    mass_flow_note=fmt_mass(propellant_mass_flow_kg_s),
                    mach_note=str(round(0.02, 3)),
                    thermal_margin_index=margin("feed_inlet"),
                    thermal_margin_note=thermal_note,
                    temperature_k=AMBIENT_TEMPERATURE_K,
                    pressure_kpa=float(fuel_tank_pressure_kpa or 0.0),
                    mass_flow_kg_s=float(propellant_mass_flow_kg_s or 0.0),
                    mach_number=0.02,
                ),
                StationSample(
                    label="Pump Or Pressurization Bay",
                    axial_position_mm=rounded(feed_system_bay_length_mm * 0.52),
                    envelope_diameter_mm=rounded(inputs.chamber_diameter_mm * 0.78),
                    area_index=0.57,
                    shell_complexity_index=0.66 if inputs.use_pumps else 0.48,
                    temperature_note=fmt_temp_k(AMBIENT_TEMPERATURE_K),
                    pressure_note=fmt_kpa((fuel_tank_pressure_kpa + chamber_pressure_kpa) * 0.5),
                    mass_flow_note=fmt_mass(propellant_mass_flow_kg_s),
                    mach_note=str(round(0.05, 3)),
                    thermal_margin_index=margin("pump_bay"),
                    thermal_margin_note=thermal_note,
                    temperature_k=AMBIENT_TEMPERATURE_K,
                    pressure_kpa=float(((fuel_tank_pressure_kpa or 0.0) + (chamber_pressure_kpa or 0.0)) * 0.5),
                    mass_flow_kg_s=float(propellant_mass_flow_kg_s or 0.0),
                    mach_number=0.05,
                ),
                StationSample(
                    label="Injector Face",
                    axial_position_mm=rounded(feed_system_bay_length_mm),
                    envelope_diameter_mm=rounded(chamber_envelope_diameter_mm),
                    area_index=0.74,
                    shell_complexity_index=0.72 if inputs.film_cooling else 0.58,
                    temperature_note=fmt_temp_k(CONCEPT_CHAMBER_TEMPERATURE_K),
                    pressure_note=fmt_kpa(chamber_pressure_kpa),
                    mass_flow_note=fmt_mass(propellant_mass_flow_kg_s),
                    mach_note=str(round(0.3, 2)),
                    thermal_margin_index=margin("injector_face"),
                    thermal_margin_note=thermal_note,
                    temperature_k=CONCEPT_CHAMBER_TEMPERATURE_K,
                    pressure_kpa=float(chamber_pressure_kpa or 0.0),
                    mass_flow_kg_s=float(propellant_mass_flow_kg_s or 0.0),
                    mach_number=0.3,
                ),
                StationSample(
                    label="Chamber Mid",
                    axial_position_mm=rounded(chamber_mid),
                    envelope_diameter_mm=rounded(chamber_envelope_diameter_mm),
                    area_index=0.82,
                    shell_complexity_index=0.7 if inputs.regen_cooling else 0.52,
                    temperature_note=fmt_temp_k(CONCEPT_CHAMBER_TEMPERATURE_K),
                    pressure_note=fmt_kpa(chamber_pressure_kpa),
                    mass_flow_note=fmt_mass(propellant_mass_flow_kg_s),
                    mach_note=str(round(0.15, 3)),
                    thermal_margin_index=margin("chamber_mid"),
                    thermal_margin_note=thermal_note,
                    temperature_k=CONCEPT_CHAMBER_TEMPERATURE_K,
                    pressure_kpa=float(chamber_pressure_kpa or 0.0),
                    mass_flow_kg_s=float(propellant_mass_flow_kg_s or 0.0),
                    mach_number=0.15,
                ),
                StationSample(
                    label="Throat Region",
                    axial_position_mm=rounded(throat_axial),
                    envelope_diameter_mm=rounded(throat_diameter),
                    area_index=0.34,
                    shell_complexity_index=0.78,
                    temperature_note=fmt_temp_k(CONCEPT_CHAMBER_TEMPERATURE_K),
                    pressure_note=fmt_kpa(max(1.0, chamber_pressure_kpa * 0.95)),
                    mass_flow_note=fmt_mass(propellant_mass_flow_kg_s),
                    mach_note=str(round(1.0, 3)),
                    thermal_margin_index=margin("throat_region"),
                    thermal_margin_note=thermal_note,
                    temperature_k=CONCEPT_CHAMBER_TEMPERATURE_K,
                    pressure_kpa=float(max(1.0, chamber_pressure_kpa * 0.95)),
                    mass_flow_kg_s=float(propellant_mass_flow_kg_s or 0.0),
                    mach_number=1.0,
                ),
                StationSample(
                    label="Nozzle Exit Plane",
                    axial_position_mm=rounded(exit_axial),
                    envelope_diameter_mm=rounded(nozzle_exit_diameter_mm),
                    area_index=1.0,
                    shell_complexity_index=0.48,
                    temperature_note=fmt_temp_k(CONCEPT_CHAMBER_TEMPERATURE_K),
                    pressure_note=fmt_kpa(AMBIENT_PRESSURE_KPA),
                    mass_flow_note=fmt_mass(propellant_mass_flow_kg_s),
                    mach_note=str(round(exit_mach_est, 3)),
                    thermal_margin_index=margin("nozzle_exit_plane"),
                    thermal_margin_note=thermal_note,
                    temperature_k=CONCEPT_CHAMBER_TEMPERATURE_K,
                    pressure_kpa=AMBIENT_PRESSURE_KPA,
                    mass_flow_kg_s=float(propellant_mass_flow_kg_s or 0.0),
                    mach_number=float(exit_mach_est),
                ),
        ]

    @staticmethod
    def _build_subsystem_placeholders(inputs: DesignInputs) -> List[SubsystemPlaceholder]:
        drive_note = (
            "Electric drive envelope reserved beside the pump cartridge. Motor class, winding, cooling, and power electronics are not sized in concept mode."
            if inputs.use_pumps
            else "Drive module removed in blowdown mode; pressurant packaging envelope retained instead."
        )
        pump_note = (
            "Impeller pump cartridge envelope reserved for fuel and oxidizer paths. Rotor geometry, inducer detail, blade count, and shaft speed are intentionally not calculated."
            if inputs.use_pumps
            else "No pump cartridge in blowdown mode."
        )
        return [
            SubsystemPlaceholder(
                label="Fuel Feed Module",
                status="concept envelope",
                note=pump_note,
            ),
            SubsystemPlaceholder(
                label="Oxidizer Feed Module",
                status="concept envelope",
                note=pump_note,
            ),
            SubsystemPlaceholder(
                label="Electrical Drive Module",
                status="future detailed design",
                note=drive_note,
            ),
        ]

    def solve(self, raw_state: Dict[str, object]) -> ConceptDesign:
        inputs = DesignInputs.from_mapping(raw_state)
        packaging = get_packaging_multiplier(inputs.packaging_bias)
        feed_mode = get_feed_mode(inputs.use_pumps)
        fuel = lookup_propellant(inputs.fuel_name, "fuel")
        oxidizer = lookup_propellant(inputs.oxidizer_name, "oxidizer")

        geometry = _calculate_geometry_sizing(inputs, fuel, oxidizer, packaging)
        thrust_scale = geometry.thrust_scale
        mixture_bias = geometry.mixture_bias
        fuel_tank_length_mm = geometry.fuel_tank_length_mm
        oxidizer_tank_length_mm = geometry.oxidizer_tank_length_mm
        chamber_length_mm = geometry.chamber_length_mm
        nozzle_throat_diameter_mm = geometry.nozzle_throat_diameter_mm
        nozzle_converging_length_mm = geometry.nozzle_converging_length_mm
        nozzle_diverging_length_mm = geometry.nozzle_diverging_length_mm
        nozzle_length_mm = geometry.nozzle_length_mm
        nozzle_expansion_ratio = geometry.nozzle_expansion_ratio
        nozzle_converging_angle_deg = geometry.nozzle_converging_angle_deg
        nozzle_diverging_angle_deg = geometry.nozzle_diverging_angle_deg
        nozzle_contour_points = geometry.nozzle_contour_points
        nozzle_equivalent_conical_length_mm = geometry.nozzle_equivalent_conical_length_mm
        nozzle_bell_length_fraction = geometry.nozzle_bell_length_fraction
        nozzle_bell_exit_angle_deg = geometry.nozzle_bell_exit_angle_deg
        nozzle_bell_entrance_angle_deg = geometry.nozzle_bell_entrance_angle_deg
        nozzle_moc_gamma = geometry.nozzle_moc_gamma
        nozzle_moc_exit_mach = geometry.nozzle_moc_exit_mach
        nozzle_moc_prandtl_meyer_exit_deg = geometry.nozzle_moc_prandtl_meyer_exit_deg
        nozzle_moc_turn_angle_deg = geometry.nozzle_moc_turn_angle_deg
        nozzle_throat_entry_blend_radius_mm = geometry.nozzle_throat_entry_blend_radius_mm
        nozzle_throat_exit_blend_radius_mm = geometry.nozzle_throat_exit_blend_radius_mm
        injector_plate_diameter_mm = geometry.injector_plate_diameter_mm
        injector_plate_thickness_mm = geometry.injector_plate_thickness_mm
        feed_system_bay_length_mm = geometry.feed_system_bay_length_mm
        coolant_jacket_thickness_mm = geometry.coolant_jacket_thickness_mm
        total_stack_length_mm = geometry.total_stack_length_mm
        chamber_surface_area_mm2 = geometry.chamber_surface_area_mm2
        nozzle_shell_area_mm2 = geometry.nozzle_shell_area_mm2
        complexity_index = geometry.complexity_index
        dry_mass_index = geometry.dry_mass_index
        packaging_efficiency_index = geometry.packaging_efficiency_index
        thermal_margin_index = geometry.thermal_margin_index

        thrust_delivery_factor = clamp(
            0.86
            + (0.03 if inputs.use_pumps else -0.02)
            + (0.04 if inputs.regen_cooling else 0.0)
            + (0.02 if inputs.film_cooling else 0.0)
            + fuel.cooling_affinity * 0.05
            - oxidizer.thermal_severity * 0.03,
            0.75,
            1.05,
        )
        calculated_thrust_newtons = inputs.target_thrust_newtons * thrust_delivery_factor
        calculated_burn_time_seconds = max(
            0.5, inputs.target_impulse_newton_seconds / max(1.0, calculated_thrust_newtons)
        )
        calculated_impulse_newton_seconds = calculated_thrust_newtons * calculated_burn_time_seconds
        effective_exhaust_velocity_mps = clamp(
            1720.0
            + thermal_margin_index * 3.8
            + (85.0 if inputs.use_pumps else -20.0)
            + (40.0 if inputs.regen_cooling else 0.0),
            1400.0,
            2450.0,
        )
        propellant_mass_flow_kg_s = calculated_thrust_newtons / effective_exhaust_velocity_mps
        propellant_mass_used_kg = propellant_mass_flow_kg_s * calculated_burn_time_seconds

        mixture_ratio = max(0.1, inputs.mixture_ratio)
        fuel_mass_kg = propellant_mass_used_kg / (1.0 + mixture_ratio)
        oxidizer_mass_kg = propellant_mass_used_kg - fuel_mass_kg

        fuel_density_kg_m3 = _lookup_density_kg_m3(fuel)
        oxidizer_density_kg_m3 = _lookup_density_kg_m3(oxidizer)
        ullage_factor = 1.08 if inputs.use_pumps else 1.16
        fuel_volume_m3 = (fuel_mass_kg / max(1.0, fuel_density_kg_m3)) * ullage_factor
        oxidizer_volume_m3 = (oxidizer_mass_kg / max(1.0, oxidizer_density_kg_m3)) * ullage_factor

        fuel_mass_flow_kg_s = propellant_mass_flow_kg_s / (1.0 + mixture_ratio)
        oxidizer_mass_flow_kg_s = propellant_mass_flow_kg_s - fuel_mass_flow_kg_s
        fuel_volume_flow_m3_s = fuel_mass_flow_kg_s / max(1.0, fuel_density_kg_m3)
        oxidizer_volume_flow_m3_s = oxidizer_mass_flow_kg_s / max(1.0, oxidizer_density_kg_m3)

        if inputs.regen_cooling:
            regen_channel_depth_mm = clamp(coolant_jacket_thickness_mm * 0.62, 1.5, 8.0)
            regen_rib_height_mm = clamp(coolant_jacket_thickness_mm * 0.42, 0.8, 5.0)
            regen_inner_wall_thickness_mm = clamp(coolant_jacket_thickness_mm * 0.18, 0.5, 2.0)
            regen_outer_jacket_thickness_mm = clamp(coolant_jacket_thickness_mm * 0.15, 0.4, 1.5)
            regen_channel_pitch_mm = clamp(inputs.nozzle_diameter_mm * 0.055, 3.0, 9.5)
            regen_channel_width_mm = clamp(
                regen_channel_pitch_mm - coolant_jacket_thickness_mm * 0.22,
                1.2,
                regen_channel_pitch_mm - 0.5,
            )
            regen_rib_thickness_mm = clamp(regen_channel_pitch_mm - regen_channel_width_mm, 0.8, 3.5)
            regen_channel_count = max(
                18.0,
                float(int(round((math.pi * inputs.nozzle_diameter_mm) / max(1.0, regen_channel_pitch_mm)))),
            )
            regen_coolant_mass_flow_kg_s = clamp(
                fuel_mass_flow_kg_s * (0.22 + 0.12 * oxidizer.thermal_severity),
                0.01,
                max(0.01, fuel_mass_flow_kg_s * 0.85),
            )
            regen_coolant_density_kg_m3 = max(120.0, fuel_density_kg_m3 * 0.82)
            regen_flow_area_m2 = max(
                1e-6,
                regen_channel_count * (regen_channel_width_mm * regen_channel_depth_mm) * 1e-6,
            )
            regen_coolant_velocity_m_s = regen_coolant_mass_flow_kg_s / (
                regen_coolant_density_kg_m3 * regen_flow_area_m2
            )
            regen_hydraulic_diameter_mm = max(
                0.5,
                (2.0 * regen_channel_width_mm * regen_channel_depth_mm)
                / max(1e-6, (regen_channel_width_mm + regen_channel_depth_mm)),
            )
            regen_pressure_drop_kpa = clamp(
                (regen_coolant_velocity_m_s ** 2)
                * 0.018
                * nozzle_length_mm
                / max(1.0, regen_hydraulic_diameter_mm)
                / 1000.0,
                20.0,
                900.0,
            )
            regen_total_radial_thickness_mm = (
                regen_inner_wall_thickness_mm + regen_channel_depth_mm + regen_outer_jacket_thickness_mm
            )
            regen_heat_flux_proxy_kw = clamp(
                propellant_mass_flow_kg_s
                * (0.35 + fuel.cooling_affinity * 0.22 + oxidizer.thermal_severity * 0.48)
                * 410.0,
                18.0,
                720.0,
            )
            regen_coolant_heat_capacity_kj_kgk = 3.75
            regen_coolant_temperature_rise_k = clamp(
                regen_heat_flux_proxy_kw
                / max(1e-6, regen_coolant_mass_flow_kg_s * regen_coolant_heat_capacity_kj_kgk),
                4.0,
                320.0,
            )
            regen_thermal_model_status = "calculated"
            regen_thermal_note = (
                "Cooling geometry and coolant-side pressure drop estimated from section dimensions and propellant flow."
            )
        else:
            regen_channel_depth_mm = 0.0
            regen_rib_height_mm = 0.0
            regen_inner_wall_thickness_mm = 0.0
            regen_outer_jacket_thickness_mm = 0.0
            regen_total_radial_thickness_mm = 0.0
            regen_channel_pitch_mm = 0.0
            regen_channel_width_mm = 0.0
            regen_rib_thickness_mm = 0.0
            regen_channel_count = 0.0
            regen_coolant_mass_flow_kg_s = 0.0
            regen_coolant_velocity_m_s = 0.0
            regen_hydraulic_diameter_mm = 0.0
            regen_pressure_drop_kpa = 0.0
            regen_heat_flux_proxy_kw = 0.0
            regen_coolant_heat_capacity_kj_kgk = 0.0
            regen_coolant_temperature_rise_k = 0.0
            regen_thermal_model_status = "not-active"
            regen_thermal_note = "Regenerative cooling disabled; structural wall temperatures are estimated without coolant passage relief."

        if inputs.film_cooling:
            film_mass_flow_kg_s = clamp(
                propellant_mass_flow_kg_s * (0.045 + 0.025 * oxidizer.thermal_severity),
                0.002,
                max(0.002, propellant_mass_flow_kg_s * 0.18),
            )
            film_slot_height_mm = clamp(injector_plate_diameter_mm * 0.018, 0.25, 1.8)
            film_slot_width_mm = clamp(math.pi * injector_plate_diameter_mm * 0.32, 12.0, 190.0)
            film_injection_angle_deg = clamp(24.0 + mixture_bias * 16.0, 18.0, 42.0)
            film_coverage_fraction = clamp(0.42 + 0.12 * fuel.cooling_affinity, 0.35, 0.90)
            film_flow_area_m2 = max(
                1e-6,
                (film_slot_height_mm / 1000.0)
                * (film_slot_width_mm / 1000.0)
                * film_coverage_fraction,
            )
            film_injection_velocity_m_s = clamp(
                film_mass_flow_kg_s / max(1e-6, fuel_density_kg_m3 * film_flow_area_m2),
                4.0,
                120.0,
            )
            film_slot_count = max(8.0, float(int(round(injector_plate_diameter_mm * 0.55))))
        else:
            film_mass_flow_kg_s = 0.0
            film_slot_height_mm = 0.0
            film_slot_width_mm = 0.0
            film_injection_angle_deg = 0.0
            film_coverage_fraction = 0.0
            film_injection_velocity_m_s = 0.0
            film_slot_count = 0.0

        injector_face_thickness_mm = injector_plate_thickness_mm
        injector_face_diameter_mm = injector_plate_diameter_mm
        if inputs.injector_type == "pintle":
            pintle_tip_diameter_mm = clamp(inputs.chamber_diameter_mm * 0.24, 7.5, 26.0)
            pintle_stem_diameter_mm = clamp(pintle_tip_diameter_mm * 0.62, 5.0, 18.0)
            pintle_annulus_gap_mm = clamp(0.95 + propellant_mass_flow_kg_s * 2.4, 0.8, 4.2)
            pintle_projection_length_mm = clamp(inputs.chamber_diameter_mm * 0.55, 12.0, 62.0)
            impinging_orifice_diameter_mm = 0.0
            impinging_angle_deg = 0.0
            impinging_pair_spacing_mm = 0.0
            impinging_element_count = 0.0
        else:
            pintle_tip_diameter_mm = 0.0
            pintle_stem_diameter_mm = 0.0
            pintle_annulus_gap_mm = 0.0
            pintle_projection_length_mm = 0.0
            impinging_orifice_diameter_mm = clamp(
                1.2 + math.sqrt(max(0.0, propellant_mass_flow_kg_s)) * 1.6,
                1.0,
                5.8,
            )
            impinging_angle_deg = clamp(28.0 + mixture_bias * 16.0, 22.0, 56.0)
            impinging_pair_spacing_mm = clamp(inputs.chamber_diameter_mm * 0.085, 3.0, 12.0)
            impinging_element_count = 2.0

        chamber_pressure_kpa = clamp(
            680.0
            + thrust_scale * 920.0
            + (170.0 if inputs.use_pumps else 40.0)
            + (90.0 if inputs.regen_cooling else 0.0),
            600.0,
            6500.0,
        )
        pressure_solution = _solve_pressure_state(
            inputs.use_pumps,
            chamber_pressure_kpa,
            propellant_mass_flow_kg_s,
            feed_system_bay_length_mm,
        )
        chamber_pressure_kpa = pressure_solution.chamber_pressure_kpa
        fuel_tank_pressure_kpa = pressure_solution.fuel_tank_pressure_kpa
        oxidizer_tank_pressure_kpa = pressure_solution.oxidizer_tank_pressure_kpa
        pump_differential_pressure_kpa = pressure_solution.pump_differential_pressure_kpa
        solver_iterations = pressure_solution.iterations

        fuel_tank_wall_thickness_mm = _estimate_wall_thickness_mm(
            fuel_tank_pressure_kpa,
            inputs.tank_diameter_mm,
            inputs.fuel_tank_material,
            inputs.factor_of_safety,
        )
        oxidizer_tank_wall_thickness_mm = _estimate_wall_thickness_mm(
            oxidizer_tank_pressure_kpa,
            inputs.tank_diameter_mm,
            inputs.oxidizer_tank_material,
            inputs.factor_of_safety,
        )

        if inputs.use_pumps:
            pump_efficiency = clamp(
                0.52 + fuel.cooling_affinity * 0.05 - oxidizer.thermal_severity * 0.02,
                0.46,
                0.78,
            )
            fuel_impeller_diameter_mm = clamp(
                36.0 + math.sqrt(max(0.0, fuel_volume_flow_m3_s * 60000.0)) * 4.6
                + pump_differential_pressure_kpa * 0.012,
                28.0,
                inputs.tank_diameter_mm * 0.92,
            )
            oxidizer_impeller_diameter_mm = clamp(
                36.0 + math.sqrt(max(0.0, oxidizer_volume_flow_m3_s * 60000.0)) * 4.6
                + pump_differential_pressure_kpa * 0.014,
                28.0,
                inputs.tank_diameter_mm * 0.92,
            )
            fuel_impeller_width_mm = clamp(fuel_impeller_diameter_mm * 0.18, 5.0, 28.0)
            oxidizer_impeller_width_mm = clamp(oxidizer_impeller_diameter_mm * 0.18, 5.0, 28.0)
            fuel_impeller_hub_diameter_mm = clamp(fuel_impeller_diameter_mm * 0.42, 12.0, 38.0)
            oxidizer_impeller_hub_diameter_mm = clamp(
                oxidizer_impeller_diameter_mm * 0.42, 12.0, 38.0
            )
            fuel_impeller_eye_diameter_mm = clamp(fuel_impeller_diameter_mm * 0.48, 16.0, 42.0)
            oxidizer_impeller_eye_diameter_mm = clamp(
                oxidizer_impeller_diameter_mm * 0.48, 16.0, 42.0
            )
            fuel_impeller_blade_count = float(5 if pump_differential_pressure_kpa < 1200.0 else 6)
            oxidizer_impeller_blade_count = float(
                6 if pump_differential_pressure_kpa < 1600.0 else 7
            )
            fuel_impeller_blade_angle_deg = clamp(18.0 + thrust_scale * 5.5, 16.0, 31.0)
            oxidizer_impeller_blade_angle_deg = clamp(19.0 + thrust_scale * 5.8, 16.0, 32.0)
            fuel_impeller_tip_clearance_mm = clamp(fuel_impeller_diameter_mm * 0.0075, 0.25, 0.9)
            oxidizer_impeller_tip_clearance_mm = clamp(
                oxidizer_impeller_diameter_mm * 0.0075, 0.25, 0.9
            )
            fuel_impeller_blade_thickness_mm = clamp(fuel_impeller_width_mm * 0.18, 1.2, 4.8)
            oxidizer_impeller_blade_thickness_mm = clamp(
                oxidizer_impeller_width_mm * 0.18, 1.2, 4.8
            )
            electric_motor_power_kw = clamp(
                (
                    pump_differential_pressure_kpa
                    * 1000.0
                    * (fuel_volume_flow_m3_s + oxidizer_volume_flow_m3_s)
                )
                / max(0.35, pump_efficiency)
                / 1000.0,
                0.25,
                120.0,
            )
            electric_motor_speed_rpm = clamp(
                5400.0 + thrust_scale * 1800.0 + 600.0,
                3000.0,
                18000.0,
            )
            electric_motor_torque_nm = electric_motor_power_kw * 9550.0 / max(
                1000.0, electric_motor_speed_rpm
            )
        else:
            pump_differential_pressure_kpa = 0.0
            pump_efficiency = 0.0
            fuel_impeller_diameter_mm = 0.0
            oxidizer_impeller_diameter_mm = 0.0
            fuel_impeller_width_mm = 0.0
            oxidizer_impeller_width_mm = 0.0
            fuel_impeller_hub_diameter_mm = 0.0
            oxidizer_impeller_hub_diameter_mm = 0.0
            fuel_impeller_eye_diameter_mm = 0.0
            oxidizer_impeller_eye_diameter_mm = 0.0
            fuel_impeller_blade_count = 0.0
            oxidizer_impeller_blade_count = 0.0
            fuel_impeller_blade_angle_deg = 0.0
            oxidizer_impeller_blade_angle_deg = 0.0
            fuel_impeller_tip_clearance_mm = 0.0
            oxidizer_impeller_tip_clearance_mm = 0.0
            fuel_impeller_blade_thickness_mm = 0.0
            oxidizer_impeller_blade_thickness_mm = 0.0
            electric_motor_power_kw = 0.0
            electric_motor_speed_rpm = 0.0
            electric_motor_torque_nm = 0.0

        chamber_structural_wall_thickness_mm = _estimate_wall_thickness_mm(
            chamber_pressure_kpa,
            inputs.chamber_diameter_mm,
            inputs.chamber_material,
            inputs.factor_of_safety,
        )
        ambient_pressure_kpa = 101.3
        throat_pressure_kpa = chamber_pressure_kpa * 0.92
        throat_structural_wall_thickness_mm = _estimate_wall_thickness_mm(
            throat_pressure_kpa,
            nozzle_throat_diameter_mm,
            inputs.nozzle_material,
            inputs.factor_of_safety,
        )
        nozzle_section_pressure_kpa = max(ambient_pressure_kpa, chamber_pressure_kpa * 0.38)
        nozzle_structural_wall_thickness_mm = _estimate_wall_thickness_mm(
            nozzle_section_pressure_kpa,
            inputs.nozzle_diameter_mm,
            inputs.nozzle_material,
            inputs.factor_of_safety,
        )

        if inputs.regen_cooling:
            regen_inner_wall_thickness_mm = max(
                regen_inner_wall_thickness_mm,
                chamber_structural_wall_thickness_mm,
                nozzle_structural_wall_thickness_mm,
            )
            regen_total_radial_thickness_mm = (
                regen_inner_wall_thickness_mm + regen_channel_depth_mm + regen_outer_jacket_thickness_mm
            )
            chamber_required_outer_diameter_mm = inputs.chamber_diameter_mm + 2.0 * regen_total_radial_thickness_mm
            nozzle_required_outer_diameter_mm = inputs.nozzle_diameter_mm + 2.0 * regen_total_radial_thickness_mm
        else:
            chamber_required_outer_diameter_mm = inputs.chamber_diameter_mm + 2.0 * chamber_structural_wall_thickness_mm
            nozzle_required_outer_diameter_mm = inputs.nozzle_diameter_mm + 2.0 * nozzle_structural_wall_thickness_mm

        outer_diameter_limit_mm = max(1.0, inputs.target_diameter_mm)
        fuel_tank_outer_diameter_mm = min(inputs.tank_diameter_mm, outer_diameter_limit_mm)
        oxidizer_tank_outer_diameter_mm = min(inputs.tank_diameter_mm, outer_diameter_limit_mm)
        chamber_regen_outer_diameter_mm, chamber_diameter_excess_mm, chamber_diameter_capped = _cap_outer_diameter(
            chamber_required_outer_diameter_mm,
            outer_diameter_limit_mm,
        )
        nozzle_regen_outer_diameter_mm, nozzle_diameter_excess_mm, nozzle_diameter_capped = _cap_outer_diameter(
            nozzle_required_outer_diameter_mm,
            outer_diameter_limit_mm,
        )
        maximum_required_diameter_mm = max(
            fuel_tank_outer_diameter_mm,
            oxidizer_tank_outer_diameter_mm,
            chamber_required_outer_diameter_mm,
            nozzle_required_outer_diameter_mm,
        )
        maximum_diameter_mm = max(
            fuel_tank_outer_diameter_mm,
            oxidizer_tank_outer_diameter_mm,
            chamber_regen_outer_diameter_mm,
            nozzle_regen_outer_diameter_mm,
        )
        diameter_limit_excess_mm = max(0.0, maximum_required_diameter_mm - outer_diameter_limit_mm)
        diameter_limit_status = "capped_by_target_diameter" if diameter_limit_excess_mm > 1e-6 else "within_target_diameter"

        fuel_tank_wall_temperature_k = clamp(
            292.0
            + fuel.thermal_severity * 18.0
            - fuel.cooling_affinity * 14.0
            + (8.0 if inputs.use_pumps else 0.0),
            255.0,
            340.0,
        )
        if oxidizer.name.strip().lower() == "liquid oxygen":
            oxidizer_tank_wall_temperature_k = 125.0
        elif oxidizer.name.strip().lower() == "nitrous oxide":
            oxidizer_tank_wall_temperature_k = 286.0
        else:
            oxidizer_tank_wall_temperature_k = 308.0
        hot_wall_base_temperature_k = clamp(
            820.0
            + oxidizer.thermal_severity * 235.0
            - fuel.cooling_affinity * 135.0
            + (45.0 if inputs.use_pumps else 0.0),
            700.0,
            1240.0,
        )
        chamber_wall_temperature_k = clamp(
            hot_wall_base_temperature_k
            - (160.0 if inputs.regen_cooling else 0.0)
            - (55.0 if inputs.film_cooling else 0.0),
            520.0,
            1280.0,
        )
        throat_wall_temperature_k = clamp(
            hot_wall_base_temperature_k
            + 92.0
            - (215.0 if inputs.regen_cooling else 0.0)
            - (58.0 if inputs.film_cooling else 0.0),
            560.0,
            1325.0,
        )
        nozzle_wall_temperature_k = clamp(
            hot_wall_base_temperature_k
            - 110.0
            + nozzle_diverging_length_mm * 0.18
            - (95.0 if inputs.regen_cooling else 0.0),
            460.0,
            1180.0,
        )

        section_margin_values = {
            "fuel_tank": _build_section_margin_metrics(
                fuel_tank_pressure_kpa,
                inputs.tank_diameter_mm,
                fuel_tank_wall_thickness_mm,
                inputs.fuel_tank_material,
                fuel_tank_wall_temperature_k,
            ),
            "oxidizer_tank": _build_section_margin_metrics(
                oxidizer_tank_pressure_kpa,
                inputs.tank_diameter_mm,
                oxidizer_tank_wall_thickness_mm,
                inputs.oxidizer_tank_material,
                oxidizer_tank_wall_temperature_k,
            ),
            "chamber": _build_section_margin_metrics(
                chamber_pressure_kpa,
                inputs.chamber_diameter_mm,
                chamber_structural_wall_thickness_mm,
                inputs.chamber_material,
                chamber_wall_temperature_k,
            ),
            "throat": _build_section_margin_metrics(
                throat_pressure_kpa,
                nozzle_throat_diameter_mm,
                throat_structural_wall_thickness_mm,
                inputs.nozzle_material,
                throat_wall_temperature_k,
            ),
            "nozzle": _build_section_margin_metrics(
                nozzle_section_pressure_kpa,
                inputs.nozzle_diameter_mm,
                nozzle_structural_wall_thickness_mm,
                inputs.nozzle_material,
                nozzle_wall_temperature_k,
            ),
        }
        regen_section_thermal_margins = {
            "feed_inlet": round(
                (
                    section_margin_values["fuel_tank"]["thermal_margin_index"]
                    + section_margin_values["oxidizer_tank"]["thermal_margin_index"]
                )
                * 0.5,
                2,
            ),
            "pump_bay": round(
                clamp(
                    (
                        section_margin_values["fuel_tank"]["thermal_margin_index"]
                        + section_margin_values["oxidizer_tank"]["thermal_margin_index"]
                    )
                    * 0.5
                    + (4.0 if inputs.use_pumps else 0.0),
                    0.0,
                    100.0,
                ),
                2,
            ),
            "injector_face": round(clamp(section_margin_values["chamber"]["thermal_margin_index"] - 6.0, 0.0, 100.0), 2),
            "chamber_mid": section_margin_values["chamber"]["thermal_margin_index"],
            "throat_region": section_margin_values["throat"]["thermal_margin_index"],
            "nozzle_exit_plane": section_margin_values["nozzle"]["thermal_margin_index"],
        }
        regen_min_thermal_margin_index = min(regen_section_thermal_margins.values())
        regen_limiting_section = min(regen_section_thermal_margins, key=regen_section_thermal_margins.get)
        thermal_margin_index = round(
            (
                section_margin_values["chamber"]["thermal_margin_index"]
                + section_margin_values["throat"]["thermal_margin_index"]
                + section_margin_values["nozzle"]["thermal_margin_index"]
            )
            / 3.0,
            2,
        )

        fuel_tank_inner_diameter_mm = max(16.0, fuel_tank_outer_diameter_mm - 2.0 * fuel_tank_wall_thickness_mm)
        oxidizer_tank_inner_diameter_mm = max(
            16.0, oxidizer_tank_outer_diameter_mm - 2.0 * oxidizer_tank_wall_thickness_mm
        )
        fuel_section_area_m2 = math.pi * pow(fuel_tank_inner_diameter_mm / 2000.0, 2)
        oxidizer_section_area_m2 = math.pi * pow(oxidizer_tank_inner_diameter_mm / 2000.0, 2)
        fuel_tank_required_length_mm = (fuel_volume_m3 / max(1e-6, fuel_section_area_m2)) * 1000.0
        oxidizer_tank_required_length_mm = (
            oxidizer_volume_m3 / max(1e-6, oxidizer_section_area_m2)
        ) * 1000.0

        cad_stations_mm = {
            "feed_bay_start": 0.0,
            "feed_bay_end": rounded(feed_system_bay_length_mm),
            "chamber_end": rounded(feed_system_bay_length_mm + chamber_length_mm),
            "nozzle_exit_plane": rounded(
                feed_system_bay_length_mm + chamber_length_mm + nozzle_length_mm
            ),
        }
        visualization_hints = {
            "fuel_color_weight": fuel.visualization_weight,
            "oxidizer_color_weight": oxidizer.visualization_weight,
            "pump_module_width_factor": 1.0 if inputs.use_pumps else 1.18,
            "show_pump_rotors": 1.0 if inputs.use_pumps else 0.0,
            "station_marker_count": 6.0,
        }
        station_rows = self._build_station_rows(
            inputs,
            feed_system_bay_length_mm,
            chamber_length_mm,
            nozzle_length_mm,
            chamber_regen_outer_diameter_mm,
            nozzle_throat_diameter_mm,
            nozzle_regen_outer_diameter_mm,
            regen_section_thermal_margins,
            regen_thermal_note,
            chamber_pressure_kpa=chamber_pressure_kpa,
            fuel_tank_pressure_kpa=fuel_tank_pressure_kpa,
            propellant_mass_flow_kg_s=propellant_mass_flow_kg_s,
            nozzle_expansion_ratio=nozzle_expansion_ratio,
            nozzle_throat_diameter_mm=nozzle_throat_diameter_mm,
        )
        subsystem_placeholders = self._build_subsystem_placeholders(inputs)

        derived = DerivedDesign(
            solver_meta=SolverMeta(
                solver_name=self.solver_name,
                solver_version=self.solver_version,
                solver_mode=self.solver_mode,
            ),
            feed_mode=feed_mode,
            fuel_tank_length_mm=fuel_tank_length_mm,
            oxidizer_tank_length_mm=oxidizer_tank_length_mm,
            chamber_length_mm=chamber_length_mm,
            nozzle_length_mm=nozzle_length_mm,
            injector_plate_diameter_mm=injector_plate_diameter_mm,
            feed_system_bay_length_mm=feed_system_bay_length_mm,
            coolant_jacket_thickness_mm=coolant_jacket_thickness_mm,
            total_stack_length_mm=total_stack_length_mm,
            maximum_diameter_mm=maximum_diameter_mm,
            chamber_surface_area_mm2=chamber_surface_area_mm2,
            nozzle_shell_area_mm2=nozzle_shell_area_mm2,
            dry_mass_index=dry_mass_index,
            packaging_efficiency_index=packaging_efficiency_index,
            thermal_margin_index=thermal_margin_index,
            complexity_index=complexity_index,
            cad_stations_mm=cad_stations_mm,
            visualization_hints=visualization_hints,
            station_rows=station_rows,
            subsystem_placeholders=subsystem_placeholders,
            nozzle_contour_points=nozzle_contour_points,
            calculation_stages=[
                "1) Inputs normalized and bounded for concept-mode solving.",
                "2) Propellant catalog factors resolved for density and thermal behavior.",
                "3) Geometry envelope solved using thrust, impulse, and architecture inputs.",
                "4) Feed-module impeller, electric motor, MOC-informed bell nozzle contour, and injector family dimensions estimated with reduced-order engineering closures.",
                "5) Flow, pressure, regenerative-cooling, and propellant sizing solved with architecture-aware pressure closure and explicit reserve margins.",
                "6) Section-based structural and thermal margins evaluated for tanks, chamber, throat, and nozzle.",
                "7) Stations, measurements, notes, and export bundles prepared.",
            ],
            engineering_values={
                "injector_type": inputs.injector_type,
                "target_thrust_newtons": rounded(inputs.target_thrust_newtons),
                "calculated_thrust_newtons": rounded(calculated_thrust_newtons),
                "target_impulse_newton_seconds": rounded(inputs.target_impulse_newton_seconds),
                "calculated_impulse_newton_seconds": rounded(calculated_impulse_newton_seconds),
                "input_burn_time_seconds": rounded(inputs.burn_time_seconds),
                "calculated_burn_time_seconds": rounded(calculated_burn_time_seconds),
                "effective_exhaust_velocity_mps": rounded(effective_exhaust_velocity_mps),
                "propellant_mass_flow_kg_s": rounded(propellant_mass_flow_kg_s),
                "fuel_mass_flow_kg_s": rounded(fuel_mass_flow_kg_s),
                "oxidizer_mass_flow_kg_s": rounded(oxidizer_mass_flow_kg_s),
                "propellant_mass_used_kg": rounded(propellant_mass_used_kg),
                "fuel_mass_kg": rounded(fuel_mass_kg),
                "oxidizer_mass_kg": rounded(oxidizer_mass_kg),
                "fuel_density_kg_m3": rounded(fuel_density_kg_m3),
                "oxidizer_density_kg_m3": rounded(oxidizer_density_kg_m3),
                "fuel_volume_l": rounded(fuel_volume_m3 * 1000.0),
                "oxidizer_volume_l": rounded(oxidizer_volume_m3 * 1000.0),
                "fuel_volume_flow_l_min": rounded(fuel_volume_flow_m3_s * 60000.0),
                "oxidizer_volume_flow_l_min": rounded(oxidizer_volume_flow_m3_s * 60000.0),
                "fuel_tank_required_length_mm": rounded(fuel_tank_required_length_mm),
                "oxidizer_tank_required_length_mm": rounded(oxidizer_tank_required_length_mm),
                "fuel_tank_inner_diameter_mm": rounded(fuel_tank_inner_diameter_mm),
                "oxidizer_tank_inner_diameter_mm": rounded(oxidizer_tank_inner_diameter_mm),
                "target_outer_diameter_limit_mm": rounded(outer_diameter_limit_mm),
                "maximum_required_outer_diameter_mm": rounded(maximum_required_diameter_mm),
                "maximum_diameter_mm": rounded(maximum_diameter_mm),
                "diameter_limit_excess_mm": rounded(diameter_limit_excess_mm),
                "diameter_limit_status": diameter_limit_status,
                "fuel_tank_outer_diameter_mm": rounded(fuel_tank_outer_diameter_mm),
                "oxidizer_tank_outer_diameter_mm": rounded(oxidizer_tank_outer_diameter_mm),
                "fuel_tank_pressure_kpa": rounded(fuel_tank_pressure_kpa),
                "oxidizer_tank_pressure_kpa": rounded(oxidizer_tank_pressure_kpa),
                "fuel_tank_wall_thickness_mm": rounded(fuel_tank_wall_thickness_mm),
                "oxidizer_tank_wall_thickness_mm": rounded(oxidizer_tank_wall_thickness_mm),
                "factor_of_safety": rounded(inputs.factor_of_safety),
                "chamber_pressure_kpa": rounded(chamber_pressure_kpa),
                "required_feed_pressure_kpa": rounded(pressure_solution.required_feed_pressure_kpa),
                "injector_pressure_drop_kpa": rounded(pressure_solution.injector_pressure_drop_kpa),
                "feed_line_pressure_drop_kpa": rounded(pressure_solution.feed_line_pressure_drop_kpa),
                "fuel_pressure_margin_kpa": rounded(pressure_solution.fuel_pressure_margin_kpa),
                "oxidizer_pressure_margin_kpa": rounded(pressure_solution.oxidizer_pressure_margin_kpa),
                "pump_discharge_pressure_kpa": rounded(pressure_solution.pump_discharge_pressure_kpa),
                "pressure_target_adjustment_kpa": rounded(pressure_solution.pressure_target_adjustment_kpa),
                "pressure_solution_residual_kpa": rounded(pressure_solution.residual_kpa),
                "pressure_solution_status": pressure_solution.status,
                "pump_differential_pressure_kpa": rounded(pump_differential_pressure_kpa),
                "pump_efficiency": round(pump_efficiency, 3),
                "fuel_impeller_diameter_mm": rounded(fuel_impeller_diameter_mm),
                "oxidizer_impeller_diameter_mm": rounded(oxidizer_impeller_diameter_mm),
                "fuel_impeller_width_mm": rounded(fuel_impeller_width_mm),
                "oxidizer_impeller_width_mm": rounded(oxidizer_impeller_width_mm),
                "fuel_impeller_hub_diameter_mm": rounded(fuel_impeller_hub_diameter_mm),
                "oxidizer_impeller_hub_diameter_mm": rounded(oxidizer_impeller_hub_diameter_mm),
                "fuel_impeller_eye_diameter_mm": rounded(fuel_impeller_eye_diameter_mm),
                "oxidizer_impeller_eye_diameter_mm": rounded(oxidizer_impeller_eye_diameter_mm),
                "fuel_impeller_blade_count": fuel_impeller_blade_count,
                "oxidizer_impeller_blade_count": oxidizer_impeller_blade_count,
                "fuel_impeller_blade_angle_deg": round(fuel_impeller_blade_angle_deg, 2),
                "oxidizer_impeller_blade_angle_deg": round(oxidizer_impeller_blade_angle_deg, 2),
                "fuel_impeller_tip_clearance_mm": rounded(fuel_impeller_tip_clearance_mm),
                "oxidizer_impeller_tip_clearance_mm": rounded(oxidizer_impeller_tip_clearance_mm),
                "fuel_impeller_blade_thickness_mm": rounded(fuel_impeller_blade_thickness_mm),
                "oxidizer_impeller_blade_thickness_mm": rounded(oxidizer_impeller_blade_thickness_mm),
                "electric_motor_power_kw": round(electric_motor_power_kw, 3),
                "electric_motor_speed_rpm": round(electric_motor_speed_rpm, 1),
                "electric_motor_torque_nm": round(electric_motor_torque_nm, 3),
                "nozzle_throat_diameter_mm": rounded(nozzle_throat_diameter_mm),
                "nozzle_inner_diameter_mm": rounded(inputs.nozzle_diameter_mm),
                "nozzle_outer_diameter_mm": rounded(nozzle_regen_outer_diameter_mm),
                "nozzle_wall_thickness_mm": rounded(max(0.0, (nozzle_regen_outer_diameter_mm - inputs.nozzle_diameter_mm) / 2.0)),
                "nozzle_structural_wall_thickness_mm": rounded(nozzle_structural_wall_thickness_mm),
                "nozzle_converging_length_mm": rounded(nozzle_converging_length_mm),
                "nozzle_diverging_length_mm": rounded(nozzle_diverging_length_mm),
                "nozzle_expansion_ratio": round(nozzle_expansion_ratio, 3),
                "nozzle_converging_angle_deg": round(nozzle_converging_angle_deg, 2),
                "nozzle_diverging_angle_deg": round(nozzle_diverging_angle_deg, 2),
                "nozzle_contour_method": "moc_bell",
                "nozzle_contour_method_label": "MOC-informed bell contour",
                "nozzle_moc_gamma": round(nozzle_moc_gamma, 3),
                "nozzle_moc_exit_mach": round(nozzle_moc_exit_mach, 4),
                "nozzle_moc_prandtl_meyer_exit_deg": round(nozzle_moc_prandtl_meyer_exit_deg, 3),
                "nozzle_moc_turn_angle_deg": round(nozzle_moc_turn_angle_deg, 3),
                "nozzle_reference_conical_half_angle_deg": 15.0,
                "nozzle_reference_conical_length_mm": rounded(nozzle_equivalent_conical_length_mm),
                "nozzle_bell_length_fraction": round(nozzle_bell_length_fraction, 3),
                "nozzle_bell_entrance_angle_deg": round(nozzle_bell_entrance_angle_deg, 2),
                "nozzle_bell_exit_angle_deg": round(nozzle_bell_exit_angle_deg, 2),
                "nozzle_throat_entry_blend_radius_mm": rounded(nozzle_throat_entry_blend_radius_mm),
                "nozzle_throat_exit_blend_radius_mm": rounded(nozzle_throat_exit_blend_radius_mm),
                "chamber_required_outer_diameter_mm": rounded(chamber_required_outer_diameter_mm),
                "chamber_diameter_limit_excess_mm": rounded(chamber_diameter_excess_mm),
                "chamber_diameter_limit_capped": 1.0 if chamber_diameter_capped else 0.0,
                "chamber_regen_outer_diameter_mm": rounded(chamber_regen_outer_diameter_mm),
                "chamber_inner_diameter_mm": rounded(inputs.chamber_diameter_mm),
                "chamber_outer_diameter_mm": rounded(chamber_regen_outer_diameter_mm),
                "chamber_wall_thickness_mm": rounded(chamber_structural_wall_thickness_mm),
                "throat_wall_thickness_mm": rounded(throat_structural_wall_thickness_mm),
                "nozzle_required_outer_diameter_mm": rounded(nozzle_required_outer_diameter_mm),
                "nozzle_diameter_limit_excess_mm": rounded(nozzle_diameter_excess_mm),
                "nozzle_diameter_limit_capped": 1.0 if nozzle_diameter_capped else 0.0,
                "nozzle_regen_outer_diameter_mm": rounded(nozzle_regen_outer_diameter_mm),
                "regen_inner_wall_thickness_mm": rounded(regen_inner_wall_thickness_mm),
                "regen_channel_depth_mm": rounded(regen_channel_depth_mm),
                "regen_outer_jacket_thickness_mm": rounded(regen_outer_jacket_thickness_mm),
                "regen_total_radial_thickness_mm": rounded(regen_total_radial_thickness_mm),
                "regen_rib_height_mm": rounded(regen_rib_height_mm),
                "regen_rib_thickness_mm": rounded(regen_rib_thickness_mm),
                "regen_channel_width_mm": rounded(regen_channel_width_mm),
                "regen_channel_pitch_mm": rounded(regen_channel_pitch_mm),
                "regen_channel_count": regen_channel_count,
                "regen_hydraulic_diameter_mm": rounded(regen_hydraulic_diameter_mm),
                "regen_coolant_mass_flow_kg_s": rounded(regen_coolant_mass_flow_kg_s),
                "regen_coolant_velocity_m_s": rounded(regen_coolant_velocity_m_s),
                "regen_pressure_drop_kpa": rounded(regen_pressure_drop_kpa),
                "regen_heat_flux_proxy_kw": round(regen_heat_flux_proxy_kw, 2),
                "regen_coolant_heat_capacity_kj_kgk": round(regen_coolant_heat_capacity_kj_kgk, 3),
                "regen_coolant_temperature_rise_k": round(regen_coolant_temperature_rise_k, 2),
                "regen_thermal_model_status": regen_thermal_model_status,
                "regen_thermal_note": regen_thermal_note,
                "regen_min_thermal_margin_index": round(regen_min_thermal_margin_index, 2),
                "regen_limiting_section": regen_limiting_section,
                "regen_section_feed_inlet_margin_index": round(regen_section_thermal_margins["feed_inlet"], 2),
                "regen_section_pump_bay_margin_index": round(regen_section_thermal_margins["pump_bay"], 2),
                "regen_section_injector_face_margin_index": round(regen_section_thermal_margins["injector_face"], 2),
                "regen_section_chamber_mid_margin_index": round(regen_section_thermal_margins["chamber_mid"], 2),
                "regen_section_throat_region_margin_index": round(regen_section_thermal_margins["throat_region"], 2),
                "regen_section_nozzle_exit_plane_margin_index": round(regen_section_thermal_margins["nozzle_exit_plane"], 2),
                "minimum_structural_margin_ratio": round(
                    min(section["structural_margin_ratio"] for section in section_margin_values.values()),
                    3,
                ),
                "minimum_thermal_margin_k": rounded(
                    min(section["thermal_margin_k"] for section in section_margin_values.values())
                ),
                "film_mass_flow_kg_s": rounded(film_mass_flow_kg_s),
                "film_slot_height_mm": rounded(film_slot_height_mm),
                "film_slot_width_mm": rounded(film_slot_width_mm),
                "film_slot_count": film_slot_count,
                "film_injection_angle_deg": round(film_injection_angle_deg, 2),
                "film_coverage_fraction": round(film_coverage_fraction, 3),
                "film_injection_velocity_m_s": rounded(film_injection_velocity_m_s),
                "injector_face_diameter_mm": rounded(injector_face_diameter_mm),
                "injector_face_thickness_mm": rounded(injector_face_thickness_mm),
                "pintle_tip_diameter_mm": rounded(pintle_tip_diameter_mm),
                "pintle_stem_diameter_mm": rounded(pintle_stem_diameter_mm),
                "pintle_annulus_gap_mm": rounded(pintle_annulus_gap_mm),
                "pintle_projection_length_mm": rounded(pintle_projection_length_mm),
                "impinging_orifice_diameter_mm": rounded(impinging_orifice_diameter_mm),
                "impinging_angle_deg": round(impinging_angle_deg, 2),
                "impinging_pair_spacing_mm": rounded(impinging_pair_spacing_mm),
                "impinging_element_count": impinging_element_count,
                "solver_iterations": float(solver_iterations),
                "total_stack_length_mm": rounded(total_stack_length_mm),
                "fuel_tank_allowable_stress_mpa": section_margin_values["fuel_tank"]["allowable_stress_mpa"],
                "fuel_tank_hoop_stress_mpa": section_margin_values["fuel_tank"]["hoop_stress_mpa"],
                "fuel_tank_structural_margin_ratio": section_margin_values["fuel_tank"]["structural_margin_ratio"],
                "fuel_tank_estimated_wall_temperature_k": section_margin_values["fuel_tank"]["wall_temperature_k"],
                "fuel_tank_temperature_limit_k": section_margin_values["fuel_tank"]["temperature_limit_k"],
                "fuel_tank_thermal_margin_k": section_margin_values["fuel_tank"]["thermal_margin_k"],
                "fuel_tank_thermal_margin_ratio": section_margin_values["fuel_tank"]["thermal_margin_ratio"],
                "fuel_tank_thermal_margin_index": section_margin_values["fuel_tank"]["thermal_margin_index"],
                "oxidizer_tank_allowable_stress_mpa": section_margin_values["oxidizer_tank"]["allowable_stress_mpa"],
                "oxidizer_tank_hoop_stress_mpa": section_margin_values["oxidizer_tank"]["hoop_stress_mpa"],
                "oxidizer_tank_structural_margin_ratio": section_margin_values["oxidizer_tank"]["structural_margin_ratio"],
                "oxidizer_tank_estimated_wall_temperature_k": section_margin_values["oxidizer_tank"]["wall_temperature_k"],
                "oxidizer_tank_temperature_limit_k": section_margin_values["oxidizer_tank"]["temperature_limit_k"],
                "oxidizer_tank_thermal_margin_k": section_margin_values["oxidizer_tank"]["thermal_margin_k"],
                "oxidizer_tank_thermal_margin_ratio": section_margin_values["oxidizer_tank"]["thermal_margin_ratio"],
                "oxidizer_tank_thermal_margin_index": section_margin_values["oxidizer_tank"]["thermal_margin_index"],
                "chamber_allowable_stress_mpa": section_margin_values["chamber"]["allowable_stress_mpa"],
                "chamber_hoop_stress_mpa": section_margin_values["chamber"]["hoop_stress_mpa"],
                "chamber_structural_margin_ratio": section_margin_values["chamber"]["structural_margin_ratio"],
                "chamber_estimated_wall_temperature_k": section_margin_values["chamber"]["wall_temperature_k"],
                "chamber_temperature_limit_k": section_margin_values["chamber"]["temperature_limit_k"],
                "chamber_thermal_margin_k": section_margin_values["chamber"]["thermal_margin_k"],
                "chamber_thermal_margin_ratio": section_margin_values["chamber"]["thermal_margin_ratio"],
                "chamber_thermal_margin_index": section_margin_values["chamber"]["thermal_margin_index"],
                "throat_allowable_stress_mpa": section_margin_values["throat"]["allowable_stress_mpa"],
                "throat_hoop_stress_mpa": section_margin_values["throat"]["hoop_stress_mpa"],
                "throat_structural_margin_ratio": section_margin_values["throat"]["structural_margin_ratio"],
                "throat_estimated_wall_temperature_k": section_margin_values["throat"]["wall_temperature_k"],
                "throat_temperature_limit_k": section_margin_values["throat"]["temperature_limit_k"],
                "throat_thermal_margin_k": section_margin_values["throat"]["thermal_margin_k"],
                "throat_thermal_margin_ratio": section_margin_values["throat"]["thermal_margin_ratio"],
                "throat_thermal_margin_index": section_margin_values["throat"]["thermal_margin_index"],
                "nozzle_allowable_stress_mpa": section_margin_values["nozzle"]["allowable_stress_mpa"],
                "nozzle_hoop_stress_mpa": section_margin_values["nozzle"]["hoop_stress_mpa"],
                "nozzle_structural_margin_ratio": section_margin_values["nozzle"]["structural_margin_ratio"],
                "nozzle_estimated_wall_temperature_k": section_margin_values["nozzle"]["wall_temperature_k"],
                "nozzle_temperature_limit_k": section_margin_values["nozzle"]["temperature_limit_k"],
                "nozzle_thermal_margin_k": section_margin_values["nozzle"]["thermal_margin_k"],
                "nozzle_thermal_margin_ratio": section_margin_values["nozzle"]["thermal_margin_ratio"],
                "nozzle_thermal_margin_index": section_margin_values["nozzle"]["thermal_margin_index"],
            },
        )

        design = ConceptDesign(inputs=inputs, fuel=fuel, oxidizer=oxidizer, derived=derived)
        cooling_methods = []
        if inputs.regen_cooling:
            cooling_methods.append("Regen")
        if inputs.film_cooling:
            cooling_methods.append("Film")
        cooling_strategy = " + ".join(cooling_methods) if cooling_methods else "None"
        derived.summary = [
            SummaryCard("Feed Mode", derived.feed_mode.label),
            SummaryCard("Estimated Layout Length", f"{rounded(total_stack_length_mm)} mm"),
            SummaryCard("Injector Family", inputs.injector_type),
            SummaryCard("Cooling Strategy", cooling_strategy),
        ]
        derived.measurement_rows = build_measurement_rows(inputs, derived)
        derived.notes = build_notes(inputs, design)
        return design


def create_concept_design(raw_state: Dict[str, object]) -> ConceptDesign:
    return ConceptSolver().solve(raw_state)
