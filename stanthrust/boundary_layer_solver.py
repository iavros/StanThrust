"""Axisymmetric wall-normal thermal march with grid-refinement diagnostics."""

import math
from typing import Dict, List, Mapping, Sequence, Tuple

DEFAULT_WALL_NORMAL_NODE_COUNT = 96
COARSE_WALL_NORMAL_NODE_COUNT = 48
THERMAL_EDGE_DECAY_LENGTHS = 8.0


def _interpolate(x_values: Sequence[float], y_values: Sequence[float], target: float) -> float:
    if target <= x_values[0]:
        return float(y_values[0])
    if target >= x_values[-1]:
        return float(y_values[-1])
    for index in range(1, len(x_values)):
        if target <= x_values[index]:
            left_x = x_values[index - 1]
            right_x = x_values[index]
            fraction = (target - left_x) / max(1e-15, right_x - left_x)
            return float(y_values[index - 1]) + fraction * (
                float(y_values[index]) - float(y_values[index - 1])
            )
    return float(y_values[-1])


def _cosine_grid(domain_m: float, node_count: int) -> List[float]:
    return [
        0.5
        * domain_m
        * (1.0 - math.cos(math.pi * index / max(1, node_count - 1)))
        for index in range(node_count)
    ]


def _velocity_ratio(eta: float, turbulent: bool) -> float:
    bounded_eta = min(1.0, max(0.0, eta))
    if turbulent:
        return bounded_eta ** (1.0 / 7.0)
    return 2.0 * bounded_eta - 2.0 * bounded_eta**3 + bounded_eta**4


def _momentum_profile_coefficient(turbulent: bool) -> float:
    sample_count = 512
    integral = 0.0
    previous_eta = 0.0
    previous_value = 0.0
    for index in range(1, sample_count + 1):
        eta = index / sample_count
        velocity_ratio = _velocity_ratio(eta, turbulent)
        value = velocity_ratio * (1.0 - velocity_ratio)
        integral += 0.5 * (previous_value + value) * (eta - previous_eta)
        previous_eta = eta
        previous_value = value
    return max(1e-8, integral)


LAMINAR_MOMENTUM_PROFILE_COEFFICIENT = _momentum_profile_coefficient(False)
TURBULENT_MOMENTUM_PROFILE_COEFFICIENT = _momentum_profile_coefficient(True)


def _solve_tridiagonal(
    lower: Sequence[float],
    diagonal: Sequence[float],
    upper: Sequence[float],
    right_hand_side: Sequence[float],
) -> List[float]:
    count = len(diagonal)
    modified_upper = [0.0] * count
    modified_rhs = [0.0] * count
    pivot = diagonal[0]
    if abs(pivot) < 1e-20:
        raise ValueError("Boundary-layer matrix has a singular leading pivot.")
    modified_upper[0] = upper[0] / pivot
    modified_rhs[0] = right_hand_side[0] / pivot
    for index in range(1, count):
        pivot = diagonal[index] - lower[index] * modified_upper[index - 1]
        if abs(pivot) < 1e-20:
            raise ValueError("Boundary-layer matrix became singular during the march.")
        modified_upper[index] = upper[index] / pivot if index < count - 1 else 0.0
        modified_rhs[index] = (
            right_hand_side[index] - lower[index] * modified_rhs[index - 1]
        ) / pivot
    solution = [0.0] * count
    solution[-1] = modified_rhs[-1]
    for index in range(count - 2, -1, -1):
        solution[index] = modified_rhs[index] - modified_upper[index] * solution[index + 1]
    return solution


def _wall_gradient(y_values: Sequence[float], scalar: Sequence[float]) -> float:
    y1 = max(1e-15, float(y_values[1]))
    y2 = max(y1 + 1e-15, float(y_values[2]))
    weight_1 = y2 / (y1 * (y2 - y1))
    weight_2 = -y1 / (y2 * (y2 - y1))
    return max(0.0, weight_1 * float(scalar[1]) + weight_2 * float(scalar[2]))


def _initial_temperature_ratio(
    y_values: Sequence[float], heat_transfer_coefficient: float, conductivity: float
) -> List[float]:
    inverse_scale = max(1e-12, heat_transfer_coefficient / conductivity)
    denominator = 1.0 - math.exp(-inverse_scale * float(y_values[-1]))
    if denominator <= 1e-12:
        return [float(y) / max(1e-12, float(y_values[-1])) for y in y_values]
    profile = [
        (1.0 - math.exp(-inverse_scale * float(y))) / denominator
        for y in y_values
    ]
    profile[0] = 0.0
    profile[-1] = 1.0
    return profile


def _wall_normal_domain(
    station: Mapping[str, object], state: Mapping[str, object], turbulent: bool
) -> Tuple[float, float, bool]:
    wall_radius_m = max(1e-8, float(station.get("radius_mm", 0.0)) / 1000.0)
    momentum_thickness_m = max(
        0.0, float(state.get("momentum_thickness_mm") or 0.0) / 1000.0
    )
    profile_coefficient = (
        TURBULENT_MOMENTUM_PROFILE_COEFFICIENT
        if turbulent
        else LAMINAR_MOMENTUM_PROFILE_COEFFICIENT
    )
    velocity_thickness_m = momentum_thickness_m / profile_coefficient
    conductivity = max(1e-12, float(state["conductivity_w_m_k"]))
    heat_transfer_coefficient = max(
        1e-12, float(state["heat_transfer_coefficient_w_m2_k"])
    )
    thermal_decay_length_m = conductivity / heat_transfer_coefficient
    unconstrained_domain_m = max(
        velocity_thickness_m,
        THERMAL_EDGE_DECAY_LENGTHS * thermal_decay_length_m,
        wall_radius_m * 1e-5,
    )
    geometric_limit_m = wall_radius_m * 0.95
    domain_m = min(unconstrained_domain_m, geometric_limit_m)
    return (
        max(1e-9, domain_m),
        max(1e-9, velocity_thickness_m),
        unconstrained_domain_m > geometric_limit_m,
    )


def _march_grid(
    stations: Sequence[Mapping[str, object]],
    states: Sequence[Mapping[str, object]],
    throat_index: int,
    node_count: int,
    profile_indices: Sequence[int],
) -> List[Dict[str, object]]:
    solved: List[Dict[str, object]] = [{} for _ in states]
    previous_y: List[float] = []
    previous_temperature_ratio: List[float] = []
    previous_x_m = 0.0

    for index in range(throat_index, len(states)):
        station = stations[index]
        state = states[index]
        regime = str(state.get("boundary_layer_regime", "turbulent"))
        turbulent = regime in {"turbulent", "transition-onset"}
        domain_m, velocity_thickness_m, edge_capture_limited = _wall_normal_domain(
            station, state, turbulent
        )
        y_values = _cosine_grid(domain_m, node_count)
        edge_velocity = max(1e-8, float(station["gas_velocity_m_s"]))
        velocity_ratio = [
            _velocity_ratio(
                y / max(1e-12, velocity_thickness_m), turbulent
            )
            if velocity_thickness_m > 1e-12
            else 1.0
            for y in y_values
        ]
        velocity_ratio[0] = 0.0
        velocity_ratio[-1] = 1.0
        conductivity = max(1e-12, float(state["conductivity_w_m_k"]))
        viscosity = max(1e-12, float(state["viscosity_pa_s"]))
        prandtl = max(1e-8, float(state["prandtl"]))
        density = max(1e-12, float(state["density_kg_m3"]))
        specific_heat = conductivity * prandtl / viscosity
        thermal_diffusivity = conductivity / (density * specific_heat)
        heat_transfer_coefficient = max(
            1e-12, float(state["heat_transfer_coefficient_w_m2_k"])
        )
        x_m = float(station["x_mm"]) / 1000.0

        if index == throat_index or not previous_y:
            temperature_ratio = _initial_temperature_ratio(
                y_values, heat_transfer_coefficient, conductivity
            )
            residual = 0.0
        else:
            streamwise_step_m = max(1e-12, x_m - previous_x_m)
            previous_on_grid = [
                _interpolate(previous_y, previous_temperature_ratio, y)
                for y in y_values
            ]
            lower = [0.0] * node_count
            diagonal = [0.0] * node_count
            upper = [0.0] * node_count
            right_hand_side = [0.0] * node_count
            diagonal[0] = 1.0
            diagonal[-1] = 1.0
            right_hand_side[-1] = 1.0
            wall_radius_m = max(
                domain_m / 0.95,
                float(station.get("radius_mm", 0.0)) / 1000.0,
            )
            for normal_index in range(1, node_count - 1):
                y_left = y_values[normal_index - 1]
                y_center = y_values[normal_index]
                y_right = y_values[normal_index + 1]
                control_width = 0.5 * (y_right - y_left)
                radial_center = max(1e-12, wall_radius_m - y_center)
                radial_left_face = max(
                    1e-12, wall_radius_m - 0.5 * (y_left + y_center)
                )
                radial_right_face = max(
                    1e-12, wall_radius_m - 0.5 * (y_center + y_right)
                )
                left_diffusion = (
                    thermal_diffusivity
                    * radial_left_face
                    / (
                        radial_center
                        * control_width
                        * max(1e-15, y_center - y_left)
                    )
                )
                right_diffusion = (
                    thermal_diffusivity
                    * radial_right_face
                    / (
                        radial_center
                        * control_width
                        * max(1e-15, y_right - y_center)
                    )
                )
                streamwise_advection = (
                    max(1e-6, edge_velocity * velocity_ratio[normal_index])
                    / streamwise_step_m
                )
                lower[normal_index] = -left_diffusion
                diagonal[normal_index] = (
                    streamwise_advection + left_diffusion + right_diffusion
                )
                upper[normal_index] = -right_diffusion
                right_hand_side[normal_index] = (
                    streamwise_advection * previous_on_grid[normal_index]
                )
            temperature_ratio = _solve_tridiagonal(
                lower, diagonal, upper, right_hand_side
            )
            temperature_ratio = [min(1.0, max(0.0, value)) for value in temperature_ratio]
            residual = 0.0
            for normal_index in range(1, node_count - 1):
                equation_residual = abs(
                    lower[normal_index] * temperature_ratio[normal_index - 1]
                    + diagonal[normal_index] * temperature_ratio[normal_index]
                    + upper[normal_index] * temperature_ratio[normal_index + 1]
                    - right_hand_side[normal_index]
                )
                equation_scale = max(
                    1e-12,
                    abs(diagonal[normal_index] * temperature_ratio[normal_index]),
                    abs(right_hand_side[normal_index]),
                )
                residual = max(residual, equation_residual / equation_scale)

        gradient = _wall_gradient(y_values, temperature_ratio)
        resolved_heat_transfer_coefficient = conductivity * gradient
        payload: Dict[str, object] = {
            "wall_normal_node_count": node_count,
            "wall_normal_domain_mm": domain_m * 1000.0,
            "wall_normal_domain_fraction": domain_m
            / max(1e-12, float(station.get("radius_mm", 0.0)) / 1000.0),
            "wall_normal_edge_capture_limited": edge_capture_limited,
            "velocity_boundary_layer_thickness_mm": velocity_thickness_m * 1000.0,
            "thermal_wall_gradient_1_m": gradient,
            "resolved_heat_transfer_coefficient_w_m2_k": (
                resolved_heat_transfer_coefficient
            ),
            "thermal_energy_residual": residual,
        }
        if index in profile_indices:
            payload["wall_normal_profile"] = {
                "y_mm": [round(value * 1000.0, 7) for value in y_values],
                "velocity_ratio": [round(value, 7) for value in velocity_ratio],
                "temperature_ratio": [
                    round(value, 7) for value in temperature_ratio
                ],
            }
        solved[index] = payload
        previous_y = y_values
        previous_temperature_ratio = temperature_ratio
        previous_x_m = x_m
    return solved


def apply_axisymmetric_thermal_march(
    stations: List[Mapping[str, object]],
    integral_states: List[Mapping[str, object]],
    throat_index: int,
    wall_normal_node_count: int = DEFAULT_WALL_NORMAL_NODE_COUNT,
) -> List[Dict[str, object]]:
    """Resolve wall-normal nozzle energy diffusion over the integral momentum state."""

    if len(stations) != len(integral_states):
        raise ValueError("Boundary-layer stations and integral states must have equal length.")
    if not stations:
        return []
    start_index = max(0, min(int(throat_index), len(stations) - 1))
    fine_nodes = max(16, int(wall_normal_node_count))
    coarse_nodes = max(12, min(COARSE_WALL_NORMAL_NODE_COUNT, fine_nodes // 2))
    nozzle_count = len(stations) - start_index
    profile_indices = sorted(
        {
            start_index
            + int(round(fraction * max(0, nozzle_count - 1)))
            for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)
        }
    )
    coarse = _march_grid(
        stations, integral_states, start_index, coarse_nodes, profile_indices=[]
    )
    fine = _march_grid(
        stations, integral_states, start_index, fine_nodes, profile_indices
    )
    results: List[Dict[str, object]] = []
    for index, integral_state in enumerate(integral_states):
        state = dict(integral_state)
        if index < start_index:
            state.update(
                {
                    "wall_normal_node_count": 0,
                    "thermal_grid_refinement_error_percent": 0.0,
                    "thermal_energy_residual": 0.0,
                    "wall_normal_edge_capture_limited": False,
                }
            )
            results.append(state)
            continue
        fine_h = max(
            1e-12,
            float(fine[index]["resolved_heat_transfer_coefficient_w_m2_k"]),
        )
        coarse_h = max(
            1e-12,
            float(coarse[index]["resolved_heat_transfer_coefficient_w_m2_k"]),
        )
        regime = str(state.get("boundary_layer_regime", "turbulent"))
        resolved_regime = regime in {
            "laminar",
            "relaminarizing",
            "relaminarized",
            "throat-anchor",
        }
        nominal_h = fine_h if resolved_regime else float(
            state["heat_transfer_coefficient_w_m2_k"]
        )
        lower_h = min(
            fine_h,
            coarse_h,
            float(state["heat_transfer_coefficient_lower_w_m2_k"]),
        )
        upper_h = max(
            fine_h,
            coarse_h,
            float(state["heat_transfer_coefficient_upper_w_m2_k"]),
        )
        diameter_m = max(1e-12, float(stations[index]["diameter_mm"]) / 1000.0)
        conductivity = max(1e-12, float(state["conductivity_w_m_k"]))
        state.update(fine[index])
        state.update(
            {
                "nusselt": nominal_h * diameter_m / conductivity,
                "heat_transfer_coefficient_w_m2_k": nominal_h,
                "heat_transfer_coefficient_lower_w_m2_k": lower_h,
                "heat_transfer_coefficient_upper_w_m2_k": upper_h,
                "boundary_layer_model": (
                    "axisymmetric-wall-normal-energy-and-momentum-integral"
                ),
                "integral_heat_transfer_coefficient_w_m2_k": float(
                    integral_state["heat_transfer_coefficient_w_m2_k"]
                ),
                "coarse_grid_heat_transfer_coefficient_w_m2_k": coarse_h,
                "thermal_grid_refinement_error_percent": (
                    100.0 * abs(fine_h - coarse_h) / fine_h
                ),
                "regime_uncertainty_basis": (
                    "wall-normal-grid-and-laminar-to-turbulent-model-envelope"
                ),
            }
        )
        results.append(state)
    return results
