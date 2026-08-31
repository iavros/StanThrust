"""Coupled liquid-feed, injector, and chamber mass-balance calculations."""

from __future__ import annotations

import math
import random
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

AMBIENT_PRESSURE_KPA = 101.325
DEFAULT_RELATIVE_UNCERTAINTIES = {
    "combustion_efficiency": 0.03,
    "fuel_discharge_coefficient": 0.06,
    "oxidizer_discharge_coefficient": 0.06,
    "fuel_injector_area": 0.02,
    "oxidizer_injector_area": 0.02,
    "fuel_supply_pressure": 0.02,
    "oxidizer_supply_pressure": 0.02,
    "fuel_density": 0.015,
    "oxidizer_density": 0.015,
    "fuel_minor_loss": 0.20,
    "oxidizer_minor_loss": 0.20,
    "fuel_roughness": 0.30,
    "oxidizer_roughness": 0.30,
    "throat_diameter": 0.005,
}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, float(value)))


def _float(value: object, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    return result if math.isfinite(result) else float(fallback)


def colebrook_friction_factor(reynolds: float, relative_roughness: float) -> Dict[str, object]:
    """Return the Darcy friction factor with laminar and transition handling."""
    reynolds = max(1.0, float(reynolds))
    relative_roughness = _clamp(relative_roughness, 0.0, 0.05)
    if reynolds < 2300.0:
        return {"friction_factor": 64.0 / reynolds, "flow_regime": "laminar", "iterations": 0}

    seed = 0.25 / (
        math.log10(max(1e-12, relative_roughness / 3.7 + 5.74 / reynolds**0.9)) ** 2
    )
    factor = _clamp(seed, 0.008, 0.12)
    iterations = 0
    for iteration in range(1, 31):
        denominator = relative_roughness / 3.7 + 2.51 / (
            reynolds * math.sqrt(max(1e-12, factor))
        )
        next_factor = 1.0 / max(1e-12, (-2.0 * math.log10(max(1e-12, denominator))) ** 2)
        next_factor = _clamp(next_factor, 0.008, 0.12)
        iterations = iteration
        if abs(next_factor - factor) <= 1e-8:
            factor = next_factor
            break
        factor = 0.5 * (factor + next_factor)

    regime = "turbulent"
    if reynolds < 4000.0:
        blend = (reynolds - 2300.0) / 1700.0
        factor = (1.0 - blend) * (64.0 / reynolds) + blend * factor
        regime = "transitional"
    return {"friction_factor": factor, "flow_regime": regime, "iterations": iterations}


def line_loss_kpa(
    mass_flow_kg_s: float,
    density_kg_m3: float,
    diameter_m: float,
    length_m: float,
    minor_loss_k: float,
    dynamic_viscosity_pa_s: float,
    roughness_m: float,
) -> Dict[str, object]:
    """Calculate major and minor pressure loss for one liquid feed branch."""
    mass_flow_kg_s = max(0.0, float(mass_flow_kg_s))
    density_kg_m3 = max(1.0, float(density_kg_m3))
    diameter_m = max(1e-4, float(diameter_m))
    length_m = max(0.0, float(length_m))
    area_m2 = math.pi * diameter_m**2 / 4.0
    velocity_m_s = mass_flow_kg_s / max(1e-12, density_kg_m3 * area_m2)
    viscosity_pa_s = max(1e-8, float(dynamic_viscosity_pa_s))
    reynolds = max(1.0, density_kg_m3 * velocity_m_s * diameter_m / viscosity_pa_s)
    relative_roughness = max(0.0, float(roughness_m)) / diameter_m
    friction = colebrook_friction_factor(reynolds, relative_roughness)
    major_k = float(friction["friction_factor"]) * length_m / diameter_m
    minor_k = max(0.0, float(minor_loss_k))
    dynamic_pressure_pa = 0.5 * density_kg_m3 * velocity_m_s**2
    major_drop_kpa = major_k * dynamic_pressure_pa / 1000.0
    minor_drop_kpa = minor_k * dynamic_pressure_pa / 1000.0
    return {
        "pressure_drop_kpa": major_drop_kpa + minor_drop_kpa,
        "major_pressure_drop_kpa": major_drop_kpa,
        "minor_pressure_drop_kpa": minor_drop_kpa,
        "velocity_m_s": velocity_m_s,
        "reynolds": reynolds,
        "friction_factor": float(friction["friction_factor"]),
        "flow_regime": str(friction["flow_regime"]),
        "friction_iterations": int(friction["iterations"]),
        "relative_roughness": relative_roughness,
        "dynamic_viscosity_pa_s": viscosity_pa_s,
        "dynamic_pressure_kpa": dynamic_pressure_pa / 1000.0,
        "major_k": major_k,
        "minor_k": minor_k,
        "equivalent_k": major_k + minor_k,
    }


def injector_pressure_drop_kpa(
    mass_flow_kg_s: float,
    density_kg_m3: float,
    discharge_coefficient: float,
    total_flow_area_mm2: float,
) -> float:
    """Return incompressible injector pressure drop from the aggregate flow area."""
    flow_area_m2 = max(1e-12, float(total_flow_area_mm2) * 1e-6)
    coefficient = max(1e-6, float(discharge_coefficient))
    density = max(1.0, float(density_kg_m3))
    velocity_term = float(mass_flow_kg_s) / (coefficient * flow_area_m2)
    return velocity_term**2 / (2.0 * density) / 1000.0


def injector_area_mm2(
    mass_flow_kg_s: float,
    density_kg_m3: float,
    discharge_coefficient: float,
    pressure_drop_kpa: float,
) -> float:
    """Size aggregate injector flow area for a selected pressure drop."""
    coefficient = max(1e-6, float(discharge_coefficient))
    denominator = coefficient * math.sqrt(
        2.0 * max(1.0, float(density_kg_m3)) * max(1.0, float(pressure_drop_kpa) * 1000.0)
    )
    return max(1e-8, float(mass_flow_kg_s) / denominator) * 1e6


def _branch_line(branch: Mapping[str, object], mass_flow_kg_s: float) -> Dict[str, object]:
    return line_loss_kpa(
        mass_flow_kg_s,
        _float(branch.get("density_kg_m3"), 800.0),
        _float(branch.get("line_diameter_m"), 0.012),
        _float(branch.get("line_length_m"), 1.0),
        _float(branch.get("minor_loss_k"), 0.0),
        _float(branch.get("dynamic_viscosity_pa_s"), 0.001),
        _float(branch.get("roughness_m"), 1.5e-6),
    )


def _branch_state_for_flow(branch: Mapping[str, object], mass_flow_kg_s: float) -> Dict[str, object]:
    line = _branch_line(branch, mass_flow_kg_s)
    injector_drop = injector_pressure_drop_kpa(
        mass_flow_kg_s,
        _float(branch.get("density_kg_m3"), 800.0),
        _float(branch.get("discharge_coefficient"), 0.72),
        _float(branch.get("injector_area_mm2"), 0.0),
    )
    additional_drop_kpa = max(0.0, _float(branch.get("additional_pressure_drop_kpa"), 0.0))
    return {
        **line,
        "mass_flow_kg_s": mass_flow_kg_s,
        "injector_pressure_drop_kpa": injector_drop,
        "additional_pressure_drop_kpa": additional_drop_kpa,
        "total_pressure_drop_kpa": (
            float(line["pressure_drop_kpa"]) + additional_drop_kpa + injector_drop
        ),
    }


def _solve_branch_mass_flow(
    supply_pressure_kpa: float,
    chamber_pressure_kpa: float,
    branch: Mapping[str, object],
    tolerance_kg_s: float = 1e-8,
) -> Dict[str, object]:
    available_drop_kpa = max(0.0, float(supply_pressure_kpa) - float(chamber_pressure_kpa))
    if available_drop_kpa <= 0.0:
        state = _branch_state_for_flow(branch, 0.0)
        return {**state, "converged": True, "iterations": 0, "pressure_residual_kpa": 0.0}
    if _float(branch.get("injector_area_mm2"), 0.0) <= 0.0:
        raise ValueError("Injector aggregate flow area must be positive in analysis mode.")

    density = max(1.0, _float(branch.get("density_kg_m3"), 800.0))
    coefficient = max(1e-6, _float(branch.get("discharge_coefficient"), 0.72))
    area_m2 = _float(branch.get("injector_area_mm2"), 0.0) * 1e-6
    upper = coefficient * area_m2 * math.sqrt(2.0 * density * available_drop_kpa * 1000.0)
    upper = max(1e-8, upper)
    lower = 0.0
    best = _branch_state_for_flow(branch, 0.0)
    converged = False
    iterations = 0
    for iteration in range(1, 81):
        mass_flow = 0.5 * (lower + upper)
        state = _branch_state_for_flow(branch, mass_flow)
        residual = float(state["total_pressure_drop_kpa"]) - available_drop_kpa
        best = state
        iterations = iteration
        if abs(residual) <= 1e-6 or upper - lower <= tolerance_kg_s:
            converged = True
            break
        if residual > 0.0:
            upper = mass_flow
        else:
            lower = mass_flow
    return {
        **best,
        "converged": converged,
        "iterations": iterations,
        "pressure_residual_kpa": float(best["total_pressure_drop_kpa"]) - available_drop_kpa,
    }


def _design_closure(inputs: Mapping[str, object]) -> Dict[str, object]:
    chamber_pressure_kpa = _float(inputs.get("target_chamber_pressure_kpa"), 0.0)
    if chamber_pressure_kpa <= AMBIENT_PRESSURE_KPA:
        raise ValueError("Design mode requires a target chamber pressure above ambient pressure.")
    throat_area_m2 = _float(inputs.get("throat_area_m2"), 0.0)
    cstar_m_s = _float(inputs.get("cstar_m_s"), 0.0)
    if throat_area_m2 <= 0.0 or cstar_m_s <= 0.0:
        raise ValueError("Design mode requires positive throat area and characteristic velocity.")

    mixture_ratio = max(0.05, _float(inputs.get("mixture_ratio"), 1.0))
    total_mass_flow = chamber_pressure_kpa * 1000.0 * throat_area_m2 / cstar_m_s
    fuel_mass_flow = total_mass_flow / (1.0 + mixture_ratio)
    oxidizer_mass_flow = total_mass_flow - fuel_mass_flow
    injector_dp_ratio = _clamp(_float(inputs.get("design_injector_dp_ratio"), 0.20), 0.05, 0.50)
    nominal_injector_drop_kpa = chamber_pressure_kpa * injector_dp_ratio
    fuel_minimum_injector_inlet_kpa = max(
        0.0,
        _float(inputs.get("fuel_minimum_injector_inlet_pressure_kpa"), 0.0),
    )
    fuel_regen_pressure_drop_kpa = max(
        0.0,
        _float(inputs.get("fuel_regen_pressure_drop_kpa"), 0.0),
    )

    branches: Dict[str, Dict[str, object]] = {}
    for name, mass_flow in (("fuel", fuel_mass_flow), ("oxidizer", oxidizer_mass_flow)):
        branch = dict(inputs.get(name, {})) if isinstance(inputs.get(name), Mapping) else {}
        injector_drop_kpa = nominal_injector_drop_kpa
        minimum_injector_inlet_kpa = 0.0
        if name == "fuel":
            minimum_injector_inlet_kpa = fuel_minimum_injector_inlet_kpa
            injector_drop_kpa = max(
                injector_drop_kpa,
                minimum_injector_inlet_kpa - chamber_pressure_kpa,
            )
            branch["additional_pressure_drop_kpa"] = fuel_regen_pressure_drop_kpa
        branch["injector_area_mm2"] = injector_area_mm2(
            mass_flow,
            _float(branch.get("density_kg_m3"), 800.0),
            _float(branch.get("discharge_coefficient"), 0.72),
            injector_drop_kpa,
        )
        state = _branch_state_for_flow(branch, mass_flow)
        required_supply = chamber_pressure_kpa + float(state["total_pressure_drop_kpa"])
        branches[name] = {
            **branch,
            **state,
            "required_supply_pressure_kpa": required_supply,
            "supply_pressure_kpa": required_supply,
            "minimum_injector_inlet_pressure_kpa": minimum_injector_inlet_kpa,
            "minimum_injector_inlet_constraint_active": bool(
                minimum_injector_inlet_kpa
                > chamber_pressure_kpa + nominal_injector_drop_kpa + 1e-9
            ),
        }

    return _assemble_result(
        inputs,
        chamber_pressure_kpa,
        branches["fuel"],
        branches["oxidizer"],
        converged=True,
        iterations=1,
        mass_balance_residual_kg_s=0.0,
        trace=[
            {
                "iteration": 1,
                "chamber_pressure_kpa": chamber_pressure_kpa,
                "mass_balance_residual_kg_s": 0.0,
            }
        ],
    )


def _analysis_closure(inputs: Mapping[str, object]) -> Dict[str, object]:
    throat_area_m2 = _float(inputs.get("throat_area_m2"), 0.0)
    cstar_m_s = _float(inputs.get("cstar_m_s"), 0.0)
    if throat_area_m2 <= 0.0 or cstar_m_s <= 0.0:
        raise ValueError("Analysis mode requires positive throat area and characteristic velocity.")
    fuel = dict(inputs.get("fuel", {})) if isinstance(inputs.get("fuel"), Mapping) else {}
    oxidizer = dict(inputs.get("oxidizer", {})) if isinstance(inputs.get("oxidizer"), Mapping) else {}
    for name, branch in (("fuel", fuel), ("oxidizer", oxidizer)):
        if _float(branch.get("supply_pressure_kpa"), 0.0) <= AMBIENT_PRESSURE_KPA:
            raise ValueError(f"Analysis mode requires {name} supply pressure above ambient pressure.")
        if _float(branch.get("injector_area_mm2"), 0.0) <= 0.0:
            raise ValueError(f"Analysis mode requires positive {name} injector aggregate flow area.")

    lower = max(10.0, _float(inputs.get("minimum_chamber_pressure_kpa"), AMBIENT_PRESSURE_KPA))
    upper = min(
        _float(fuel.get("supply_pressure_kpa"), 0.0),
        _float(oxidizer.get("supply_pressure_kpa"), 0.0),
    ) - 1e-5
    if upper <= lower:
        raise ValueError("Supply pressures do not provide a valid chamber-pressure bracket.")

    trace: List[Dict[str, float]] = []
    best: Tuple[float, Dict[str, object], Dict[str, object], float] | None = None
    converged = False
    for iteration in range(1, 101):
        chamber_pressure = 0.5 * (lower + upper)
        fuel_state = _solve_branch_mass_flow(
            _float(fuel.get("supply_pressure_kpa")), chamber_pressure, fuel
        )
        oxidizer_state = _solve_branch_mass_flow(
            _float(oxidizer.get("supply_pressure_kpa")), chamber_pressure, oxidizer
        )
        inflow = float(fuel_state["mass_flow_kg_s"]) + float(oxidizer_state["mass_flow_kg_s"])
        outflow = chamber_pressure * 1000.0 * throat_area_m2 / cstar_m_s
        residual = inflow - outflow
        trace.append(
            {
                "iteration": iteration,
                "chamber_pressure_kpa": chamber_pressure,
                "fuel_mass_flow_kg_s": float(fuel_state["mass_flow_kg_s"]),
                "oxidizer_mass_flow_kg_s": float(oxidizer_state["mass_flow_kg_s"]),
                "chamber_outflow_kg_s": outflow,
                "mass_balance_residual_kg_s": residual,
            }
        )
        if best is None or abs(residual) < abs(best[3]):
            best = (chamber_pressure, fuel_state, oxidizer_state, residual)
        tolerance = max(1e-8, outflow * 1e-6)
        if abs(residual) <= tolerance or upper - lower <= 1e-5:
            converged = True
            break
        if residual > 0.0:
            lower = chamber_pressure
        else:
            upper = chamber_pressure

    if best is None:
        raise RuntimeError("Hydraulic chamber closure did not produce an iteration state.")
    chamber_pressure, fuel_state, oxidizer_state, residual = best
    fuel_state = {**fuel, **fuel_state, "required_supply_pressure_kpa": _float(fuel.get("supply_pressure_kpa"))}
    oxidizer_state = {
        **oxidizer,
        **oxidizer_state,
        "required_supply_pressure_kpa": _float(oxidizer.get("supply_pressure_kpa")),
    }
    return _assemble_result(
        inputs,
        chamber_pressure,
        fuel_state,
        oxidizer_state,
        converged=converged,
        iterations=len(trace),
        mass_balance_residual_kg_s=residual,
        trace=trace,
    )


def _assemble_result(
    inputs: Mapping[str, object],
    chamber_pressure_kpa: float,
    fuel: Mapping[str, object],
    oxidizer: Mapping[str, object],
    *,
    converged: bool,
    iterations: int,
    mass_balance_residual_kg_s: float,
    trace: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    fuel_flow = _float(fuel.get("mass_flow_kg_s"), 0.0)
    oxidizer_flow = _float(oxidizer.get("mass_flow_kg_s"), 0.0)
    total_flow = fuel_flow + oxidizer_flow
    actual_of = oxidizer_flow / max(1e-12, fuel_flow)
    throat_area = _float(inputs.get("throat_area_m2"), 0.0)
    cstar = _float(inputs.get("cstar_m_s"), 0.0)
    chamber_outflow = chamber_pressure_kpa * 1000.0 * throat_area / max(1e-12, cstar)
    result = {
        "status": "ok" if converged else "not-converged",
        "mode": str(inputs.get("mode", "design")),
        "chamber_pressure_kpa": chamber_pressure_kpa,
        "fuel_mass_flow_kg_s": fuel_flow,
        "oxidizer_mass_flow_kg_s": oxidizer_flow,
        "total_mass_flow_kg_s": total_flow,
        "chamber_outflow_kg_s": chamber_outflow,
        "actual_mixture_ratio": actual_of,
        "target_mixture_ratio": _float(inputs.get("mixture_ratio"), actual_of),
        "mixture_ratio_error_percent": 100.0
        * (actual_of - _float(inputs.get("mixture_ratio"), actual_of))
        / max(1e-12, _float(inputs.get("mixture_ratio"), actual_of)),
        "mass_balance_residual_kg_s": mass_balance_residual_kg_s,
        "mass_balance_relative_error": abs(mass_balance_residual_kg_s) / max(1e-12, chamber_outflow),
        "iterations": iterations,
        "converged": converged,
        "cstar_m_s": cstar,
        "throat_area_m2": throat_area,
        "fuel": dict(fuel),
        "oxidizer": dict(oxidizer),
        "iteration_trace": [dict(row) for row in trace],
        "equations": {
            "chamber_mass_balance": "mdot_f + mdot_ox = Pc At / cstar",
            "injector": "mdot = Cd A sqrt(2 rho deltaP)",
            "line_loss": "deltaP = (f L/D + sumK) rho v^2 / 2",
        },
    }
    return _rounded(result)


def solve_hydraulic_chamber(inputs: Mapping[str, object]) -> Dict[str, object]:
    """Solve a design-sizing or as-built hydraulic chamber closure."""
    mode = str(inputs.get("mode", "design") or "design").strip().lower()
    if mode == "design":
        return _design_closure(inputs)
    if mode == "analysis":
        return _analysis_closure(inputs)
    raise ValueError("Hydraulic solve mode must be 'design' or 'analysis'.")


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = _clamp(probability, 0.0, 1.0) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    denominator = math.sqrt(sum(value * value for value in dx) * sum(value * value for value in dy))
    if denominator <= 1e-20:
        return 0.0
    return sum(x * y for x, y in zip(dx, dy)) / denominator


def _latin_hypercube(sample_count: int, parameter_names: Iterable[str], seed: int) -> Dict[str, List[float]]:
    rng = random.Random(seed)
    samples: Dict[str, List[float]] = {}
    for name in parameter_names:
        values = [(index + rng.random()) / sample_count for index in range(sample_count)]
        rng.shuffle(values)
        samples[name] = values
    return samples


def _copy_inputs(inputs: Mapping[str, object]) -> Dict[str, object]:
    copied = dict(inputs)
    copied["fuel"] = dict(inputs.get("fuel", {})) if isinstance(inputs.get("fuel"), Mapping) else {}
    copied["oxidizer"] = (
        dict(inputs.get("oxidizer", {})) if isinstance(inputs.get("oxidizer"), Mapping) else {}
    )
    return copied


def _analysis_basis(inputs: Mapping[str, object], nominal: Mapping[str, object]) -> Dict[str, object]:
    basis = _copy_inputs(inputs)
    basis["mode"] = "analysis"
    for name in ("fuel", "oxidizer"):
        nominal_branch = nominal.get(name, {}) if isinstance(nominal.get(name), Mapping) else {}
        branch = basis[name]
        branch["injector_area_mm2"] = _float(nominal_branch.get("injector_area_mm2"), 0.0)
        branch["supply_pressure_kpa"] = _float(nominal_branch.get("supply_pressure_kpa"), 0.0)
    return basis


def propagate_hydraulic_uncertainty(
    inputs: Mapping[str, object],
    *,
    sample_count: int = 128,
    seed: int = 271828,
    relative_uncertainties: Mapping[str, object] | None = None,
    thrust_coefficient: float | None = None,
    thrust_coefficient_relative_uncertainty: float = 0.025,
) -> Dict[str, object]:
    """Propagate explicit input ranges through the as-built hydraulic closure."""
    sample_count = int(_clamp(sample_count, 24, 2048))
    nominal = solve_hydraulic_chamber(inputs)
    basis = _analysis_basis(inputs, nominal)
    uncertainties = dict(DEFAULT_RELATIVE_UNCERTAINTIES)
    if thrust_coefficient is not None:
        uncertainties["thrust_coefficient"] = _clamp(
            thrust_coefficient_relative_uncertainty, 0.0, 0.95
        )
    if relative_uncertainties:
        for key, value in relative_uncertainties.items():
            uncertainties[str(key)] = _clamp(_float(value), 0.0, 0.95)

    parameter_names = tuple(uncertainties)
    lhs = _latin_hypercube(sample_count, parameter_names, seed)
    samples: List[Dict[str, float]] = []
    accepted_parameter_values: Dict[str, List[float]] = {name: [] for name in parameter_names}
    failed_samples = 0
    for index in range(sample_count):
        sampled = _copy_inputs(basis)

        sample_parameters: Dict[str, float] = {}

        def factor(name: str) -> float:
            relative = uncertainties[name]
            value = 1.0 + relative * (2.0 * lhs[name][index] - 1.0)
            sample_parameters[name] = value
            return value

        efficiency_factor = factor("combustion_efficiency")
        sampled["cstar_m_s"] = _float(basis.get("cstar_m_s")) * efficiency_factor
        sampled["fuel"]["discharge_coefficient"] = _float(
            basis["fuel"].get("discharge_coefficient")
        ) * factor("fuel_discharge_coefficient")
        sampled["oxidizer"]["discharge_coefficient"] = _float(
            basis["oxidizer"].get("discharge_coefficient")
        ) * factor("oxidizer_discharge_coefficient")
        sampled["fuel"]["injector_area_mm2"] = _float(
            basis["fuel"].get("injector_area_mm2")
        ) * factor("fuel_injector_area")
        sampled["oxidizer"]["injector_area_mm2"] = _float(
            basis["oxidizer"].get("injector_area_mm2")
        ) * factor("oxidizer_injector_area")
        sampled["fuel"]["supply_pressure_kpa"] = _float(
            basis["fuel"].get("supply_pressure_kpa")
        ) * factor("fuel_supply_pressure")
        sampled["oxidizer"]["supply_pressure_kpa"] = _float(
            basis["oxidizer"].get("supply_pressure_kpa")
        ) * factor("oxidizer_supply_pressure")
        sampled["fuel"]["density_kg_m3"] = _float(basis["fuel"].get("density_kg_m3")) * factor(
            "fuel_density"
        )
        sampled["oxidizer"]["density_kg_m3"] = _float(
            basis["oxidizer"].get("density_kg_m3")
        ) * factor("oxidizer_density")
        sampled["fuel"]["minor_loss_k"] = _float(basis["fuel"].get("minor_loss_k")) * factor(
            "fuel_minor_loss"
        )
        sampled["oxidizer"]["minor_loss_k"] = _float(
            basis["oxidizer"].get("minor_loss_k")
        ) * factor("oxidizer_minor_loss")
        sampled["fuel"]["roughness_m"] = _float(basis["fuel"].get("roughness_m")) * factor(
            "fuel_roughness"
        )
        sampled["oxidizer"]["roughness_m"] = _float(
            basis["oxidizer"].get("roughness_m")
        ) * factor("oxidizer_roughness")
        throat_factor = factor("throat_diameter")
        sampled["throat_area_m2"] = _float(basis.get("throat_area_m2")) * throat_factor**2
        try:
            result = solve_hydraulic_chamber(sampled)
        except (ValueError, RuntimeError):
            failed_samples += 1
            continue
        if not bool(result.get("converged")):
            failed_samples += 1
            continue
        pressure_kpa = _float(result.get("chamber_pressure_kpa"))
        total_flow = _float(result.get("total_mass_flow_kg_s"))
        mixture_ratio = _float(result.get("actual_mixture_ratio"))
        thrust = 0.0
        if thrust_coefficient is not None:
            cf_factor = factor("thrust_coefficient")
            thrust = (
                float(thrust_coefficient)
                * cf_factor
                * pressure_kpa
                * 1000.0
                * _float(sampled.get("throat_area_m2"))
            )
        for name, value in sample_parameters.items():
            accepted_parameter_values[name].append(value)
        samples.append(
            {
                "chamber_pressure_kpa": pressure_kpa,
                "total_mass_flow_kg_s": total_flow,
                "mixture_ratio": mixture_ratio,
                "predicted_thrust_newtons": thrust,
            }
        )

    if not samples:
        raise RuntimeError("Every hydraulic uncertainty sample failed to converge.")

    def interval(field: str, unit: str) -> Dict[str, object]:
        values = [row[field] for row in samples]
        return {
            "name": field,
            "unit": unit,
            "p05": _percentile(values, 0.05),
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
            "minimum": min(values),
            "maximum": max(values),
        }

    pressure_values = [row["chamber_pressure_kpa"] for row in samples]
    sensitivities = []
    for name in parameter_names:
        values = accepted_parameter_values[name]
        sensitivities.append(
            {
                "input": name,
                "pressure_correlation": _pearson(values, pressure_values),
            }
        )
    sensitivities.sort(key=lambda row: abs(float(row["pressure_correlation"])), reverse=True)
    intervals = [
        interval("chamber_pressure_kpa", "kPa"),
        interval("total_mass_flow_kg_s", "kg/s"),
        interval("mixture_ratio", "O/F"),
    ]
    if thrust_coefficient is not None:
        intervals.append(interval("predicted_thrust_newtons", "N"))
    return _rounded(
        {
            "method": "deterministic-seeded-latin-hypercube",
            "nominal": nominal,
            "sample_count_requested": sample_count,
            "sample_count_accepted": len(samples),
            "failed_samples": failed_samples,
            "seed": seed,
            "relative_input_ranges": uncertainties,
            "intervals": intervals,
            "pressure_sensitivity": sensitivities,
            "notes": [
                "Intervals are P05/P50/P95 predictions from explicit hydraulic input ranges.",
                "Design-mode hardware is frozen at nominal sized areas before uncertainty is propagated.",
                "These are model-input intervals, not hot-fire validation limits.",
            ],
        }
    )


def _rounded(value: object) -> object:
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_rounded(item) for item in value)
    if isinstance(value, MutableMapping):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {key: _rounded(item) for key, item in value.items()}
    return value
