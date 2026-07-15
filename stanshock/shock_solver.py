import math
from typing import Dict, List, Mapping, Optional


def _positive_float(value: object, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(numeric):
        return fallback
    return numeric


def normal_shock_relations(mach_1: float, gamma: float = 1.22) -> Dict[str, float]:
    """Return perfect-gas Rankine-Hugoniot jump relations for a normal shock."""

    upstream_mach = float(mach_1)
    heat_capacity_ratio = float(gamma)
    if upstream_mach <= 1.0:
        raise ValueError("Normal shock upstream Mach number must be supersonic.")
    if heat_capacity_ratio <= 1.0:
        raise ValueError("Heat-capacity ratio must be greater than 1.")

    mach_1_squared = upstream_mach * upstream_mach
    pressure_ratio = 1.0 + (2.0 * heat_capacity_ratio / (heat_capacity_ratio + 1.0)) * (
        mach_1_squared - 1.0
    )
    density_ratio = ((heat_capacity_ratio + 1.0) * mach_1_squared) / (
        2.0 + (heat_capacity_ratio - 1.0) * mach_1_squared
    )
    temperature_ratio = pressure_ratio / max(1e-12, density_ratio)
    downstream_mach_squared = (
        1.0 + 0.5 * (heat_capacity_ratio - 1.0) * mach_1_squared
    ) / (
        heat_capacity_ratio * mach_1_squared - 0.5 * (heat_capacity_ratio - 1.0)
    )
    downstream_mach = math.sqrt(max(1e-12, downstream_mach_squared))
    total_pressure_ratio = pressure_ratio * (
        (1.0 + 0.5 * (heat_capacity_ratio - 1.0) * downstream_mach_squared)
        ** (heat_capacity_ratio / (heat_capacity_ratio - 1.0))
    ) / (
        (1.0 + 0.5 * (heat_capacity_ratio - 1.0) * mach_1_squared)
        ** (heat_capacity_ratio / (heat_capacity_ratio - 1.0))
    )
    entropy_change_over_r = (
        heat_capacity_ratio / (heat_capacity_ratio - 1.0) * math.log(max(1e-12, temperature_ratio))
        - math.log(max(1e-12, pressure_ratio))
    )

    return {
        "status": "calculated",
        "model": "rankine-hugoniot-normal-shock",
        "gamma": heat_capacity_ratio,
        "upstream_mach": upstream_mach,
        "downstream_mach": downstream_mach,
        "pressure_ratio": pressure_ratio,
        "density_ratio": density_ratio,
        "temperature_ratio": temperature_ratio,
        "total_pressure_ratio": total_pressure_ratio,
        "entropy_change_over_r": entropy_change_over_r,
    }


def _theta_from_beta(mach: float, beta_rad: float, gamma: float) -> float:
    sine_beta = math.sin(beta_rad)
    numerator = 2.0 * (1.0 / max(1e-12, math.tan(beta_rad))) * (
        mach * mach * sine_beta * sine_beta - 1.0
    )
    denominator = mach * mach * (gamma + math.cos(2.0 * beta_rad)) + 2.0
    return math.atan(numerator / max(1e-12, denominator))


def _bisect_beta(
    mach: float,
    gamma: float,
    target_theta_rad: float,
    low_beta_rad: float,
    high_beta_rad: float,
) -> float:
    low_value = _theta_from_beta(mach, low_beta_rad, gamma) - target_theta_rad
    high_value = _theta_from_beta(mach, high_beta_rad, gamma) - target_theta_rad
    for _ in range(80):
        mid_beta_rad = 0.5 * (low_beta_rad + high_beta_rad)
        mid_value = _theta_from_beta(mach, mid_beta_rad, gamma) - target_theta_rad
        if abs(mid_value) < 1e-12:
            return mid_beta_rad
        if low_value * mid_value <= 0.0:
            high_beta_rad = mid_beta_rad
            high_value = mid_value
        else:
            low_beta_rad = mid_beta_rad
            low_value = mid_value
        if abs(high_beta_rad - low_beta_rad) < 1e-12:
            break
    return 0.5 * (low_beta_rad + high_beta_rad)


def oblique_shock_relations(
    mach_1: float,
    deflection_deg: float,
    gamma: float = 1.22,
    branch: str = "weak",
) -> Dict[str, float]:
    """Solve the theta-beta-M relation and return oblique-shock jump data."""

    upstream_mach = float(mach_1)
    heat_capacity_ratio = float(gamma)
    target_theta_rad = math.radians(float(deflection_deg))
    selected_branch = (branch or "weak").strip().lower()
    if selected_branch not in {"weak", "strong"}:
        selected_branch = "weak"
    if upstream_mach <= 1.0:
        raise ValueError("Oblique shock upstream Mach number must be supersonic.")
    if target_theta_rad <= 0.0:
        raise ValueError("Oblique shock deflection angle must be positive.")

    beta_min = math.asin(1.0 / upstream_mach) + 1e-7
    beta_max = 0.5 * math.pi - 1e-7
    samples: List[tuple] = []
    for index in range(1601):
        beta = beta_min + (beta_max - beta_min) * index / 1600.0
        theta = _theta_from_beta(upstream_mach, beta, heat_capacity_ratio)
        if math.isfinite(theta) and theta >= 0.0:
            samples.append((beta, theta))

    if not samples:
        return {
            "status": "detached",
            "model": "theta-beta-mach",
            "gamma": heat_capacity_ratio,
            "upstream_mach": upstream_mach,
            "deflection_deg": float(deflection_deg),
        }

    max_index = max(range(len(samples)), key=lambda idx: samples[idx][1])
    max_theta = samples[max_index][1]
    if target_theta_rad > max_theta:
        return {
            "status": "detached",
            "model": "theta-beta-mach",
            "gamma": heat_capacity_ratio,
            "upstream_mach": upstream_mach,
            "deflection_deg": float(deflection_deg),
            "max_deflection_deg": math.degrees(max_theta),
        }

    bracket_samples = samples[: max_index + 1] if selected_branch == "weak" else samples[max_index:]
    bracket: Optional[tuple] = None
    for first, second in zip(bracket_samples, bracket_samples[1:]):
        first_error = first[1] - target_theta_rad
        second_error = second[1] - target_theta_rad
        if first_error == 0.0:
            bracket = (first[0], first[0])
            break
        if first_error * second_error <= 0.0:
            bracket = (first[0], second[0])
            break
    if bracket is None:
        beta_rad = min(bracket_samples, key=lambda item: abs(item[1] - target_theta_rad))[0]
    elif bracket[0] == bracket[1]:
        beta_rad = bracket[0]
    else:
        beta_rad = _bisect_beta(
            upstream_mach,
            heat_capacity_ratio,
            target_theta_rad,
            bracket[0],
            bracket[1],
        )

    normal_mach_1 = upstream_mach * math.sin(beta_rad)
    normal_jump = normal_shock_relations(normal_mach_1, heat_capacity_ratio)
    normal_mach_2 = normal_jump["downstream_mach"]
    downstream_mach = normal_mach_2 / max(1e-12, math.sin(beta_rad - target_theta_rad))

    return {
        "status": "calculated",
        "model": "theta-beta-mach",
        "branch": selected_branch,
        "gamma": heat_capacity_ratio,
        "upstream_mach": upstream_mach,
        "downstream_mach": downstream_mach,
        "normal_mach_upstream": normal_mach_1,
        "normal_mach_downstream": normal_mach_2,
        "shock_angle_deg": math.degrees(beta_rad),
        "deflection_deg": float(deflection_deg),
        "pressure_ratio": normal_jump["pressure_ratio"],
        "density_ratio": normal_jump["density_ratio"],
        "temperature_ratio": normal_jump["temperature_ratio"],
        "total_pressure_ratio": normal_jump["total_pressure_ratio"],
        "entropy_change_over_r": normal_jump["entropy_change_over_r"],
    }


def rankine_hugoniot_jump_from_mach(mach_1: float, gamma: float = 1.22) -> Dict[str, float]:
    """Compatibility wrapper for callers that name the governing jump equations."""

    return normal_shock_relations(mach_1, gamma)


def find_nozzle_normal_shock_candidate(
    axial_profile: List[Mapping[str, object]],
    ambient_pressure_kpa: float,
    gamma: float = 1.22,
) -> Dict[str, object]:
    """Solve whether an overexpanded nozzle has an internal normal shock station."""

    if not axial_profile:
        return {
            "status": "not-available",
            "regime": "unknown",
            "model": "rankine-hugoniot-normal-shock",
            "note": "No axial profile was available for shock analysis.",
        }

    sorted_profile = sorted(
        axial_profile,
        key=lambda row: _positive_float(row.get("x_mm"), 0.0),
    )
    ambient_kpa = max(0.001, float(ambient_pressure_kpa))
    exit_pressure_kpa = max(0.001, _positive_float(sorted_profile[-1].get("pressure_kpa"), ambient_kpa))
    pressure_ratio_to_ambient = exit_pressure_kpa / ambient_kpa
    if pressure_ratio_to_ambient < 0.85:
        regime = "overexpanded"
    elif pressure_ratio_to_ambient > 1.15:
        regime = "underexpanded"
    else:
        regime = "pressure-matched"

    base_result: Dict[str, object] = {
        "model": "rankine-hugoniot-normal-shock",
        "gamma": float(gamma),
        "regime": regime,
        "exit_pressure_kpa": exit_pressure_kpa,
        "ambient_pressure_kpa": ambient_kpa,
        "pressure_ratio_to_ambient": pressure_ratio_to_ambient,
    }
    if regime != "overexpanded":
        base_result.update(
            {
                "status": "not-triggered",
                "note": (
                    "Exit pressure does not indicate an internal normal shock. "
                    "Underexpanded flow expands externally; pressure-matched flow does not need a shock correction."
                ),
            }
        )
        return base_result

    best_candidate: Optional[Dict[str, object]] = None
    best_score = float("inf")
    for index, row in enumerate(sorted_profile):
        upstream_mach = _positive_float(row.get("mach"), 0.0)
        upstream_pressure_kpa = max(0.001, _positive_float(row.get("pressure_kpa"), exit_pressure_kpa))
        if upstream_mach <= 1.0001:
            continue
        jump = normal_shock_relations(upstream_mach, gamma)
        downstream_pressure_kpa = upstream_pressure_kpa * jump["pressure_ratio"]
        score = abs(math.log(max(1e-12, downstream_pressure_kpa / ambient_kpa)))
        if score < best_score:
            downstream_temperature_k = _positive_float(row.get("temperature_k"), 0.0) * jump["temperature_ratio"]
            downstream_density_kg_m3 = _positive_float(row.get("density_kg_m3"), 0.0) * jump["density_ratio"]
            best_score = score
            best_candidate = {
                **base_result,
                "status": "normal-shock-candidate",
                "shock_station_index": index,
                "shock_x_mm": _positive_float(row.get("x_mm"), 0.0),
                "shock_radius_mm": _positive_float(row.get("radius_mm"), 0.0),
                "upstream_mach": upstream_mach,
                "downstream_mach": jump["downstream_mach"],
                "upstream_pressure_kpa": upstream_pressure_kpa,
                "downstream_pressure_kpa": downstream_pressure_kpa,
                "downstream_temperature_k": downstream_temperature_k,
                "downstream_density_kg_m3": downstream_density_kg_m3,
                "pressure_ratio": jump["pressure_ratio"],
                "density_ratio": jump["density_ratio"],
                "temperature_ratio": jump["temperature_ratio"],
                "total_pressure_ratio": jump["total_pressure_ratio"],
                "entropy_change_over_r": jump["entropy_change_over_r"],
                "pressure_match_error_fraction": abs(downstream_pressure_kpa - ambient_kpa) / ambient_kpa,
                "note": (
                    "Candidate station uses Rankine-Hugoniot normal-shock jumps on the isentropic "
                    "axial profile. Downstream nozzle flow is diagnostic only and is not re-integrated."
                ),
            }

    if best_candidate is None:
        base_result.update(
            {
                "status": "not-supersonic",
                "note": "The nozzle profile did not contain a supersonic station for shock placement.",
            }
        )
        return base_result
    if float(best_candidate.get("pressure_match_error_fraction", 0.0)) > 0.75:
        best_candidate["status"] = "overexpanded-no-station-match"
        best_candidate["note"] = (
            "Overexpanded flow was detected, but the normal-shock jump scan did not find a "
            "pressure-compatible station inside the current axial flow profile. Treat this as a "
            "separation or external-adjustment warning rather than a resolved internal shock."
        )
    return best_candidate
