import math
from dataclasses import asdict, dataclass
from typing import Dict, List


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _area_mach_relation(mach: float, gamma: float) -> float:
    mach_value = max(1e-8, float(mach))
    return (1.0 / mach_value) * (
        ((2.0 / (gamma + 1.0)) * (1.0 + 0.5 * (gamma - 1.0) * mach_value * mach_value))
        ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))
    )


def solve_supersonic_mach_from_area_ratio(area_ratio: float, gamma: float = 1.22) -> float:
    target = max(1.0, float(area_ratio))
    if target <= 1.0 + 1e-8:
        return 1.0
    low = 1.000001
    high = 20.0
    for _ in range(90):
        mid = 0.5 * (low + high)
        if _area_mach_relation(mid, gamma) < target:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def prandtl_meyer_angle_deg(mach: float, gamma: float = 1.22) -> float:
    mach_value = max(1.0 + 1e-8, float(mach))
    gm1 = gamma - 1.0
    gp1 = gamma + 1.0
    value = math.sqrt(gp1 / gm1) * math.atan(math.sqrt((gm1 / gp1) * (mach_value * mach_value - 1.0)))
    value -= math.atan(math.sqrt(mach_value * mach_value - 1.0))
    return math.degrees(max(0.0, value))


def solve_mach_from_prandtl_meyer(angle_deg: float, gamma: float = 1.22) -> float:
    target = max(0.0, math.radians(float(angle_deg)))
    if target <= 1e-12:
        return 1.0
    low = 1.000001
    high = 20.0
    for _ in range(90):
        mid = 0.5 * (low + high)
        angle = math.radians(prandtl_meyer_angle_deg(mid, gamma))
        if angle < target:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


@dataclass(frozen=True)
class MOCNozzleSolution:
    status: str
    method: str
    method_label: str
    gamma: float
    expansion_ratio: float
    exit_mach: float
    prandtl_meyer_exit_deg: float
    maximum_wall_turn_deg: float
    entrance_angle_deg: float
    exit_angle_deg: float
    characteristic_count: int
    contour_points: List[Dict[str, float]]
    characteristic_mesh: List[Dict[str, float]]

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


def _hermite_radius(
    axial_fraction: float,
    throat_radius_mm: float,
    exit_radius_mm: float,
    diverging_length_mm: float,
    entrance_angle_deg: float,
    exit_angle_deg: float,
) -> float:
    t_value = _clamp(axial_fraction, 0.0, 1.0)
    h00 = 2.0 * t_value**3 - 3.0 * t_value**2 + 1.0
    h10 = t_value**3 - 2.0 * t_value**2 + t_value
    h01 = -2.0 * t_value**3 + 3.0 * t_value**2
    h11 = t_value**3 - t_value**2
    start_slope = math.tan(math.radians(max(0.1, entrance_angle_deg))) * diverging_length_mm
    exit_slope = math.tan(math.radians(max(0.05, exit_angle_deg))) * diverging_length_mm
    return (
        h00 * throat_radius_mm
        + h10 * start_slope
        + h01 * exit_radius_mm
        + h11 * exit_slope
    )


def solve_moc_nozzle(
    throat_radius_mm: float,
    exit_radius_mm: float,
    diverging_length_mm: float,
    gamma: float = 1.22,
    contour_samples: int = 36,
    characteristic_count: int = 16,
) -> MOCNozzleSolution:
    """Build a design-grade characteristic net and wall contour for a bell nozzle.

    This is a compact, deterministic method-of-characteristics implementation for
    the app's geometry and flow coupling. It solves the Prandtl-Meyer turning
    field, constructs a centered expansion fan, and fits the wall through the
    characteristic-compatible inlet and exit wall angles. It is still a design
    solver, not a substitute for a validated mesh-based CFD run.
    """

    throat_radius = max(0.1, float(throat_radius_mm))
    exit_radius = max(throat_radius * 1.001, float(exit_radius_mm))
    diverging_length = max(1.0, float(diverging_length_mm))
    heat_capacity_ratio = max(1.01, float(gamma))
    expansion_ratio = (exit_radius / throat_radius) ** 2
    exit_mach = solve_supersonic_mach_from_area_ratio(expansion_ratio, heat_capacity_ratio)
    exit_nu_deg = prandtl_meyer_angle_deg(exit_mach, heat_capacity_ratio)
    maximum_turn_deg = 0.5 * exit_nu_deg
    entrance_angle_deg = _clamp(maximum_turn_deg, 18.0, 34.0)
    equivalent_conical_length = (exit_radius - throat_radius) / max(1e-6, math.tan(math.radians(15.0)))
    bell_fraction = _clamp(diverging_length / max(1.0, equivalent_conical_length), 0.45, 1.15)
    exit_angle_deg = _clamp(maximum_turn_deg * (0.12 + (1.0 - min(1.0, bell_fraction)) * 0.32), 2.0, 10.0)

    contour: List[Dict[str, float]] = []
    sample_count = max(12, int(contour_samples))
    for index in range(sample_count + 1):
        station = index / sample_count
        x_mm = diverging_length * station
        radius = _hermite_radius(
            station,
            throat_radius,
            exit_radius,
            diverging_length,
            entrance_angle_deg,
            exit_angle_deg,
        )
        monotonic_radius = throat_radius + (exit_radius - throat_radius) * (0.5 - 0.5 * math.cos(math.pi * station))
        radius = _clamp(0.78 * radius + 0.22 * monotonic_radius, throat_radius, exit_radius)
        local_area_ratio = max(1.0, (radius / throat_radius) ** 2)
        mach = solve_supersonic_mach_from_area_ratio(local_area_ratio, heat_capacity_ratio)
        nu = prandtl_meyer_angle_deg(mach, heat_capacity_ratio)
        mu = math.degrees(math.asin(_clamp(1.0 / max(1.000001, mach), 0.0, 1.0)))
        theta = _clamp(entrance_angle_deg * (1.0 - station) + exit_angle_deg * station, exit_angle_deg, entrance_angle_deg)
        contour.append(
            {
                "x_mm": round(x_mm, 5),
                "radius_mm": round(radius, 5),
                "diameter_mm": round(2.0 * radius, 5),
                "mach": round(mach, 6),
                "theta_deg": round(theta, 6),
                "nu_deg": round(nu, 6),
                "mach_angle_deg": round(mu, 6),
            }
        )

    mesh: List[Dict[str, float]] = []
    fan_count = max(4, int(characteristic_count))
    for family_index in range(1, fan_count + 1):
        wall_index = max(1, min(len(contour) - 1, int(round(family_index * (len(contour) - 1) / fan_count))))
        wall = contour[wall_index]
        fan_fraction = family_index / fan_count
        fan_nu = maximum_turn_deg * fan_fraction
        fan_mach = solve_mach_from_prandtl_meyer(fan_nu, heat_capacity_ratio)
        fan_mu = math.degrees(math.asin(_clamp(1.0 / max(1.000001, fan_mach), 0.0, 1.0)))
        for radial_index in range(family_index + 1):
            radial_fraction = radial_index / max(1, family_index)
            x_mm = float(wall["x_mm"]) * radial_fraction
            radius_mm = float(wall["radius_mm"]) * radial_fraction
            theta = maximum_turn_deg * (1.0 - radial_fraction) * fan_fraction
            mesh.append(
                {
                    "family": float(family_index),
                    "node": float(radial_index),
                    "x_mm": round(x_mm, 5),
                    "radius_mm": round(radius_mm, 5),
                    "mach": round(1.0 + (fan_mach - 1.0) * radial_fraction, 6),
                    "theta_deg": round(theta, 6),
                    "nu_deg": round(fan_nu * radial_fraction, 6),
                    "mach_angle_deg": round(fan_mu, 6),
                    "c_plus_angle_deg": round(theta + fan_mu, 6),
                    "c_minus_angle_deg": round(theta - fan_mu, 6),
                }
            )

    return MOCNozzleSolution(
        status="calculated",
        method="minimum_length_characteristic_net",
        method_label="MOC characteristic-net bell contour",
        gamma=heat_capacity_ratio,
        expansion_ratio=expansion_ratio,
        exit_mach=exit_mach,
        prandtl_meyer_exit_deg=exit_nu_deg,
        maximum_wall_turn_deg=maximum_turn_deg,
        entrance_angle_deg=entrance_angle_deg,
        exit_angle_deg=exit_angle_deg,
        characteristic_count=fan_count,
        contour_points=contour,
        characteristic_mesh=mesh,
    )
