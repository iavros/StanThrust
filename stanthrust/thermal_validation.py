"""Fixed, no-calibration validation cases for thermal-model closures."""

import csv
import math
from pathlib import Path
from statistics import median
from typing import Dict, List

from stanthrust.heat_transfer_solver import (
    GAS_NUSSELT_COEFFICIENT,
    NASA_TP_3380_NUSSELT_COEFFICIENT_RANGE,
    march_nozzle_boundary_layer,
    solve_gas_side_heat_transfer,
)
from stanthrust.moc_nozzle_solver import solve_supersonic_mach_from_area_ratio
from stanthrust.thermochemistry_provider import _import_cantera

NASA_TP_3380 = {
    "case_id": "nasa-tp-3380-lox-gh2-calorimeter",
    "title": "Subscale plug-nozzle rocket calorimeter chamber",
    "source": "NASA TP-3380, Quentmeyer and Roncace, 1993",
    "source_url": "https://ntrs.nasa.gov/citations/19940008097",
    "chamber_pressure_mpa": 4.14,
    "mixture_ratio": 6.0,
    "hot_wall_temperature_k": 730.0,
    "measured_throat_heat_flux_mw_m2": 54.0,
    "outer_chamber_diameter_mm": 66.0,
    "throat_centerbody_diameter_mm": 53.3,
    "annular_hydraulic_diameter_mm": 12.7,
    "measured_nusselt_coefficient_lower": NASA_TP_3380_NUSSELT_COEFFICIENT_RANGE[0],
    "measured_nusselt_coefficient_upper": NASA_TP_3380_NUSSELT_COEFFICIENT_RANGE[1],
}

NASA_TP_3380_DATA_PATH = Path(__file__).resolve().parent / "data" / "nasa_tp3380_calorimeter.csv"
NASA_TP_2726_DATA_PATH = Path(__file__).resolve().parent / "data" / "nasa_tp2726_nozzle_heat_flux.csv"
NASA_TP_2726_CONTOUR_PATH = Path(__file__).resolve().parent / "data" / "nasa_tp2726_nozzle_contour.csv"

NASA_TP_2726 = {
    "case_id": "nasa-tp-2726-reading-121",
    "title": "1030:1 conventional bell-nozzle hot-fire heat transfer",
    "source": "NASA TP-2726, Kacynski, Pavli, and Smith, 1987",
    "source_url": "https://ntrs.nasa.gov/citations/19870015991",
    "chamber_pressure_kpa": 2482.0,
    "mixture_ratio": 4.11,
    "fuel_inlet_temperature_k": 295.0,
    "oxidizer_inlet_temperature_k": 288.3,
    "cstar_efficiency": 0.968,
    "throat_diameter_mm": 25.4,
    "exit_area_ratio": 1030.0,
    "relaminarization_threshold": 2.0e-6,
    "preliminary_mape_target_percent": 30.0,
}


def _percentile(values: List[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    location = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = int(math.floor(location))
    upper = int(math.ceil(location))
    if lower == upper:
        return ordered[lower]
    blend = location - lower
    return ordered[lower] * (1.0 - blend) + ordered[upper] * blend


def _load_nasa_tp3380_cases() -> List[Dict[str, object]]:
    with NASA_TP_3380_DATA_PATH.open("r", encoding="ascii", newline="") as handle:
        rows = list(csv.DictReader(handle))
    cases: List[Dict[str, object]] = []
    for row in rows:
        if row.get("measurement_basis") != "direct":
            raise ValueError("Thermal validation accepts directly measured calorimeter rows only.")
        cases.append(
            {
                "case_id": str(row["case_id"]),
                "mixture_ratio": float(row["mixture_ratio"]),
                "chamber_pressure_bar": float(row["chamber_pressure_bar"]),
                "hot_wall_temperature_k": float(row["hot_wall_temperature_k"]),
                "measured_throat_heat_flux_mw_m2": float(row["measured_throat_heat_flux_mw_m2"]),
                "measurement_basis": "direct",
            }
        )
    return cases


def _load_nasa_tp2726_measurements() -> List[Dict[str, object]]:
    with NASA_TP_2726_DATA_PATH.open("r", encoding="ascii", newline="") as handle:
        rows = list(csv.DictReader(handle))
    measurements: List[Dict[str, object]] = []
    for row in rows:
        if row.get("measurement_basis") != "direct":
            raise ValueError("NASA TP-2726 validation accepts direct measurements only.")
        measurements.append(
            {
                "reading": int(row["reading"]),
                "area_ratio": float(row["area_ratio"]),
                "inner_wall_temperature_k": float(row["inner_wall_temperature_k"]),
                "measured_heat_flux_kw_m2": float(row["measured_heat_flux_kw_m2"]),
                "measurement_basis": "direct",
                "included": row["included"].strip().lower() == "yes",
                "exclusion_reason": str(row.get("exclusion_reason", "")).strip(),
            }
        )
    return measurements


def _load_nasa_tp2726_contour() -> List[Dict[str, float]]:
    with NASA_TP_2726_CONTOUR_PATH.open("r", encoding="ascii", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        {
            "x_m": float(row["x_cm"]) / 100.0,
            "radius_m": float(row["radius_cm"]) / 100.0,
        }
        for row in rows
    ]


def _interpolate_profile_value(
    profile: List[Dict[str, float]],
    target_area_ratio: float,
    key: str,
) -> float:
    if target_area_ratio <= profile[0]["area_ratio"]:
        return profile[0][key]
    for left, right in zip(profile, profile[1:]):
        if target_area_ratio <= right["area_ratio"]:
            span = max(1e-12, right["area_ratio"] - left["area_ratio"])
            blend = (target_area_ratio - left["area_ratio"]) / span
            return left[key] * (1.0 - blend) + right[key] * blend
    return profile[-1][key]


def _equilibrate_hydrogen_oxygen(
    chamber_pressure_pa: float,
    mixture_ratio: float,
    fuel_inlet_temperature_k: float,
    oxidizer_inlet_temperature_k: float,
) -> Dict[str, object]:
    ct = _import_cantera()
    gas = ct.Solution("gri30.yaml")
    pressure_pa = max(100000.0, float(chamber_pressure_pa))
    of_ratio = max(0.1, float(mixture_ratio))

    gas.TPY = fuel_inlet_temperature_k, pressure_pa, {"H2": 1.0}
    fuel_enthalpy_j_kg = float(gas.enthalpy_mass)
    gas.TPY = oxidizer_inlet_temperature_k, pressure_pa, {"O2": 1.0}
    oxidizer_enthalpy_j_kg = float(gas.enthalpy_mass)
    inlet_enthalpy_j_kg = (
        fuel_enthalpy_j_kg + of_ratio * oxidizer_enthalpy_j_kg
    ) / (1.0 + of_ratio)
    gas.Y = {"H2": 1.0 / (1.0 + of_ratio), "O2": of_ratio / (1.0 + of_ratio)}
    gas.HP = inlet_enthalpy_j_kg, pressure_pa
    gas.equilibrate("HP")
    return {
        "gas": gas,
        "pressure_pa": pressure_pa,
        "chamber_temperature_k": float(gas.T),
        "gamma": float(gas.cp_mass / gas.cv_mass),
        "gas_constant_j_kg_k": float(gas.cp_mass - gas.cv_mass),
        "frozen_mass_fractions": gas.Y,
    }


def _solve_lox_hydrogen_throat_state(
    chamber_pressure_pa: float,
    mixture_ratio: float,
    oxidizer_inlet_temperature_k: float = 90.0,
    fuel_inlet_temperature_k: float = 298.15,
) -> Dict[str, float]:
    """Return a frozen-composition throat state from a Cantera HP equilibrium solve."""

    state = _equilibrate_hydrogen_oxygen(
        chamber_pressure_pa,
        mixture_ratio,
        fuel_inlet_temperature_k,
        oxidizer_inlet_temperature_k,
    )
    gas = state["gas"]
    pressure_pa = float(state["pressure_pa"])
    chamber_temperature_k = float(state["chamber_temperature_k"])
    gamma = float(state["gamma"])
    gas_constant_j_kg_k = float(state["gas_constant_j_kg_k"])
    throat_temperature_k = chamber_temperature_k / (1.0 + 0.5 * (gamma - 1.0))
    throat_pressure_pa = pressure_pa * (1.0 + 0.5 * (gamma - 1.0)) ** (-gamma / (gamma - 1.0))

    frozen_mass_fractions = state["frozen_mass_fractions"]
    gas.TPY = throat_temperature_k, throat_pressure_pa, frozen_mass_fractions
    viscosity_pa_s = float(gas.viscosity)
    conductivity_w_m_k = float(gas.thermal_conductivity)
    prandtl = float(gas.cp_mass * viscosity_pa_s / conductivity_w_m_k)
    return {
        "chamber_temperature_k": chamber_temperature_k,
        "gamma": gamma,
        "gas_constant_j_kg_k": gas_constant_j_kg_k,
        "throat_temperature_k": throat_temperature_k,
        "throat_pressure_kpa": throat_pressure_pa / 1000.0,
        "viscosity_pa_s": viscosity_pa_s,
        "conductivity_w_m_k": conductivity_w_m_k,
        "prandtl": prandtl,
    }


def evaluate_nasa_tp3380_calorimeter() -> Dict[str, object]:
    """Predict seven published throat heat-flux points without fitting parameters."""

    predictions: List[Dict[str, object]] = []
    hydraulic_diameter_mm = float(NASA_TP_3380["annular_hydraulic_diameter_mm"])
    for case in _load_nasa_tp3380_cases():
        chamber_pressure_kpa = float(case["chamber_pressure_bar"]) * 100.0
        throat_state = _solve_lox_hydrogen_throat_state(
            chamber_pressure_pa=chamber_pressure_kpa * 1000.0,
            mixture_ratio=float(case["mixture_ratio"]),
        )
        convection = solve_gas_side_heat_transfer(
            chamber_pressure_kpa=chamber_pressure_kpa,
            chamber_temperature_k=throat_state["chamber_temperature_k"],
            mach=1.0,
            hydraulic_diameter_mm=hydraulic_diameter_mm,
            gamma=throat_state["gamma"],
            gas_constant_j_kg_k=throat_state["gas_constant_j_kg_k"],
            viscosity_pa_s=throat_state["viscosity_pa_s"],
            conductivity_w_m_k=throat_state["conductivity_w_m_k"],
            prandtl=throat_state["prandtl"],
        )
        temperature_difference_k = max(
            0.0,
            float(convection["recovery_temperature_k"]) - float(case["hot_wall_temperature_k"]),
        )
        predicted = float(convection["heat_transfer_coefficient_w_m2_k"]) * temperature_difference_k / 1e6
        predicted_lower = (
            float(convection["heat_transfer_coefficient_lower_w_m2_k"]) * temperature_difference_k / 1e6
        )
        predicted_upper = (
            float(convection["heat_transfer_coefficient_upper_w_m2_k"]) * temperature_difference_k / 1e6
        )
        measured = float(case["measured_throat_heat_flux_mw_m2"])
        absolute_percent_error = abs(predicted - measured) / measured * 100.0
        predictions.append(
            {
                **case,
                "predicted_throat_heat_flux_mw_m2": predicted,
                "predicted_lower_mw_m2": predicted_lower,
                "predicted_upper_mw_m2": predicted_upper,
                "absolute_percent_error": absolute_percent_error,
                "inside_correlation_interval": predicted_lower <= measured <= predicted_upper,
                "chamber_temperature_k": throat_state["chamber_temperature_k"],
                "throat_temperature_k": throat_state["throat_temperature_k"],
                "reynolds": float(convection["reynolds"]),
                "prandtl": throat_state["prandtl"],
            }
        )

    errors = [float(row["absolute_percent_error"]) for row in predictions]
    covered = sum(bool(row["inside_correlation_interval"]) for row in predictions)
    nominal = min(predictions, key=lambda row: abs(float(row["mixture_ratio"]) - 6.0) + abs(float(row["chamber_pressure_bar"]) - 41.4))
    return {
        "case_id": "nasa-tp-3380-seven-point-calorimeter-series",
        "source": NASA_TP_3380["source"],
        "source_url": NASA_TP_3380["source_url"],
        "dataset_url": "https://doi.org/10.1016/j.dib.2021.107173",
        "case_count": len(predictions),
        "predictions": predictions,
        "mean_absolute_percent_error": sum(errors) / len(errors),
        "median_absolute_percent_error": median(errors),
        "p95_absolute_percent_error": _percentile(errors, 0.95),
        "maximum_absolute_percent_error": max(errors),
        "correlation_interval_coverage_fraction": covered / len(predictions),
        "nominal_predicted_throat_heat_flux_mw_m2": nominal["predicted_throat_heat_flux_mw_m2"],
        "nominal_measured_throat_heat_flux_mw_m2": nominal["measured_throat_heat_flux_mw_m2"],
        "annular_hydraulic_diameter_mm": hydraulic_diameter_mm,
        "thermochemistry_model": "Cantera GRI-Mech 3.0 HP equilibrium",
        "throat_transport_model": "Cantera frozen-composition local transport",
        "optimizer_used": False,
        "calibration_used": False,
        "independent_of_correlation_source": False,
        "validation_level": "geometry-matched-experimental-heat-flux-series",
        "interpretation": (
            "This reconstructs the campaign used to publish the coefficient interval. It verifies geometry, "
            "thermochemistry, transport, and correlation plumbing, but it is not an independent predictive dataset."
        ),
    }


def evaluate_nasa_tp_3380_correlation() -> Dict[str, object]:
    """Check the gas-side closure against the report's measured fitted range.

    The report used an annular plug-nozzle calorimeter, so its 54 MW/m2 throat
    value is retained as a published reference and is not treated as a direct
    geometry-equivalent prediction for StanThrust's conventional bell nozzle.
    """

    lower = float(NASA_TP_3380["measured_nusselt_coefficient_lower"])
    upper = float(NASA_TP_3380["measured_nusselt_coefficient_upper"])
    coefficient = float(GAS_NUSSELT_COEFFICIENT)
    return {
        **NASA_TP_3380,
        "implemented_nusselt_coefficient": coefficient,
        "coefficient_inside_measured_range": lower <= coefficient <= upper,
        "distance_from_range_midpoint_percent": abs(coefficient - 0.5 * (lower + upper))
        / (0.5 * (lower + upper))
        * 100.0,
        "validation_level": "experimental-correlation-closure",
        "optimizer_used": False,
        "geometry_equivalent_heat_flux_comparison": False,
        "note": (
            "The measured Nusselt coefficient range directly validates the implemented gas-side "
            "closure. The reported throat heat flux is context only because the NASA article uses "
            "an annular plug-nozzle geometry rather than a conventional bell nozzle."
        ),
    }


def evaluate_nasa_tp2726_bell_nozzle() -> Dict[str, object]:
    """Reconstruct NASA TP-2726 reading 121 without fitting model coefficients."""

    pressure_pa = float(NASA_TP_2726["chamber_pressure_kpa"]) * 1000.0
    mixture_ratio = float(NASA_TP_2726["mixture_ratio"])
    chamber_state = _equilibrate_hydrogen_oxygen(
        pressure_pa,
        mixture_ratio,
        float(NASA_TP_2726["fuel_inlet_temperature_k"]),
        float(NASA_TP_2726["oxidizer_inlet_temperature_k"]),
    )
    gas = chamber_state["gas"]
    equilibrium_temperature_k = float(chamber_state["chamber_temperature_k"])
    cstar_efficiency = float(NASA_TP_2726["cstar_efficiency"])
    chamber_temperature_k = equilibrium_temperature_k * cstar_efficiency**2
    gamma = float(chamber_state["gamma"])
    gas_constant_j_kg_k = float(chamber_state["gas_constant_j_kg_k"])
    frozen_mass_fractions = chamber_state["frozen_mass_fractions"]
    throat_radius_m = float(NASA_TP_2726["throat_diameter_mm"]) / 2000.0

    contour_profile: List[Dict[str, float]] = []
    turbulent_states: List[Dict[str, object]] = []
    for point in _load_nasa_tp2726_contour():
        area_ratio = (point["radius_m"] / throat_radius_m) ** 2
        mach = solve_supersonic_mach_from_area_ratio(max(1.0, area_ratio), gamma)
        state_factor = 1.0 + 0.5 * (gamma - 1.0) * mach * mach
        temperature_k = chamber_temperature_k / state_factor
        local_pressure_pa = pressure_pa * state_factor ** (-gamma / (gamma - 1.0))
        gas.TPY = temperature_k, local_pressure_pa, frozen_mass_fractions
        viscosity_pa_s = float(gas.viscosity)
        conductivity_w_m_k = float(gas.thermal_conductivity)
        prandtl = float(gas.cp_mass * viscosity_pa_s / conductivity_w_m_k)
        density_kg_m3 = local_pressure_pa / (gas_constant_j_kg_k * temperature_k)
        velocity_m_s = mach * math.sqrt(gamma * gas_constant_j_kg_k * temperature_k)
        diameter_mm = point["radius_m"] * 2000.0
        station = {
            "area_ratio": area_ratio,
            "x_m": point["x_m"],
            "x_mm": point["x_m"] * 1000.0,
            "radius_mm": point["radius_m"] * 1000.0,
            "diameter_mm": diameter_mm,
            "mach": mach,
            "temperature_k": temperature_k,
            "pressure_pa": local_pressure_pa,
            "viscosity_pa_s": viscosity_pa_s,
            "conductivity_w_m_k": conductivity_w_m_k,
            "prandtl": prandtl,
            "density_kg_m3": density_kg_m3,
            "velocity_m_s": velocity_m_s,
            "gas_velocity_m_s": velocity_m_s,
            "gas_kinematic_viscosity_m2_s": viscosity_pa_s / density_kg_m3,
        }
        contour_profile.append(station)
        turbulent_states.append(
            solve_gas_side_heat_transfer(
                chamber_pressure_kpa=pressure_pa / 1000.0,
                chamber_temperature_k=chamber_temperature_k,
                mach=mach,
                hydraulic_diameter_mm=diameter_mm,
                gamma=gamma,
                gas_constant_j_kg_k=gas_constant_j_kg_k,
                viscosity_pa_s=viscosity_pa_s,
                conductivity_w_m_k=conductivity_w_m_k,
                prandtl=prandtl,
                static_temperature_k=temperature_k,
                static_pressure_kpa=local_pressure_pa / 1000.0,
            )
        )

    for index, station in enumerate(contour_profile):
        if index == 0:
            left = station
            right = contour_profile[index + 1]
        elif index == len(contour_profile) - 1:
            left = contour_profile[index - 1]
            right = station
        else:
            left = contour_profile[index - 1]
            right = contour_profile[index + 1]
        velocity_gradient_s = (right["velocity_m_s"] - left["velocity_m_s"]) / max(
            1e-12, right["x_m"] - left["x_m"]
        )
        kinematic_viscosity_m2_s = station["viscosity_pa_s"] / station["density_kg_m3"]
        station["velocity_gradient_s"] = velocity_gradient_s
        station["acceleration_parameter"] = (
            kinematic_viscosity_m2_s
            * velocity_gradient_s
            / max(1e-12, station["velocity_m_s"] ** 2)
        )

    boundary_layer_states = march_nozzle_boundary_layer(
        contour_profile, turbulent_states, throat_index=0
    )
    thermal_profile = [
        {**station, **boundary_layer}
        for station, boundary_layer in zip(contour_profile, boundary_layer_states)
    ]

    source_rows = _load_nasa_tp2726_measurements()
    predictions: List[Dict[str, object]] = []
    threshold = float(NASA_TP_2726["relaminarization_threshold"])
    for measurement in source_rows:
        if not measurement["included"]:
            continue
        area_ratio = float(measurement["area_ratio"])
        mach = solve_supersonic_mach_from_area_ratio(area_ratio, gamma)
        nearest_state = min(
            thermal_profile, key=lambda row: abs(float(row["area_ratio"]) - area_ratio)
        )
        recovery_temperature_k = _interpolate_profile_value(
            thermal_profile, area_ratio, "recovery_temperature_k"
        )
        heat_transfer_coefficient = _interpolate_profile_value(
            thermal_profile, area_ratio, "heat_transfer_coefficient_w_m2_k"
        )
        heat_transfer_coefficient_lower = _interpolate_profile_value(
            thermal_profile, area_ratio, "heat_transfer_coefficient_lower_w_m2_k"
        )
        heat_transfer_coefficient_upper = _interpolate_profile_value(
            thermal_profile, area_ratio, "heat_transfer_coefficient_upper_w_m2_k"
        )
        wall_temperature_k = float(measurement["inner_wall_temperature_k"])
        temperature_difference_k = max(
            0.0, recovery_temperature_k - wall_temperature_k
        )
        predicted = heat_transfer_coefficient * temperature_difference_k / 1000.0
        predicted_lower = heat_transfer_coefficient_lower * temperature_difference_k / 1000.0
        predicted_upper = heat_transfer_coefficient_upper * temperature_difference_k / 1000.0
        measured = float(measurement["measured_heat_flux_kw_m2"])
        signed_percent_error = (predicted - measured) / measured * 100.0
        acceleration_parameter = _interpolate_profile_value(
            thermal_profile, area_ratio, "acceleration_parameter"
        )
        relaminarization_risk = bool(nearest_state["relaminarized"]) or acceleration_parameter >= threshold
        predictions.append(
            {
                **measurement,
                "x_m": _interpolate_profile_value(contour_profile, area_ratio, "x_m"),
                "mach": mach,
                "predicted_heat_flux_kw_m2": predicted,
                "predicted_lower_kw_m2": predicted_lower,
                "predicted_upper_kw_m2": predicted_upper,
                "heat_transfer_coefficient_w_m2_k": heat_transfer_coefficient,
                "absolute_percent_error": abs(signed_percent_error),
                "signed_percent_error": signed_percent_error,
                "inside_model_envelope": predicted_lower <= measured <= predicted_upper,
                "acceleration_parameter": acceleration_parameter,
                "relaminarization_risk": relaminarization_risk,
                "boundary_layer_regime": str(nearest_state["boundary_layer_regime"]),
                "momentum_thickness_reynolds": float(
                    nearest_state["momentum_thickness_reynolds"]
                ),
                "wall_normal_node_count": int(
                    nearest_state["wall_normal_node_count"]
                ),
                "wall_normal_domain_mm": float(
                    nearest_state["wall_normal_domain_mm"]
                ),
                "thermal_grid_refinement_error_percent": float(
                    nearest_state["thermal_grid_refinement_error_percent"]
                ),
                "boundary_layer_applicability": "regime-envelope-evaluated",
            }
        )

    errors = [float(row["absolute_percent_error"]) for row in predictions]
    signed_errors = [float(row["signed_percent_error"]) for row in predictions]
    covered = sum(bool(row["inside_model_envelope"]) for row in predictions)
    predicted_flux = [float(row["predicted_heat_flux_kw_m2"]) for row in predictions]
    regime_counts: Dict[str, int] = {}
    for row in predictions:
        regime = str(row["boundary_layer_regime"])
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
    mape = sum(errors) / len(errors)
    target = float(NASA_TP_2726["preliminary_mape_target_percent"])
    return {
        "case_id": NASA_TP_2726["case_id"],
        "source": NASA_TP_2726["source"],
        "source_url": NASA_TP_2726["source_url"],
        "source_measurement_count": len(source_rows),
        "case_count": len(predictions),
        "excluded_measurement_count": len(source_rows) - len(predictions),
        "predictions": predictions,
        "mean_absolute_percent_error": mape,
        "median_absolute_percent_error": median(errors),
        "p95_absolute_percent_error": _percentile(errors, 0.95),
        "maximum_absolute_percent_error": max(errors),
        "mean_signed_percent_error": sum(signed_errors) / len(signed_errors),
        "model_envelope_coverage_fraction": covered / len(predictions),
        "monotonic_heat_flux_prediction": all(
            right < left for left, right in zip(predicted_flux, predicted_flux[1:])
        ),
        "relaminarization_risk_station_count": sum(
            bool(row["relaminarization_risk"]) for row in predictions
        ),
        "boundary_layer_regime_counts": regime_counts,
        "maximum_acceleration_parameter": max(
            float(row["acceleration_parameter"]) for row in predictions
        ),
        "preliminary_mape_target_percent": target,
        "meets_preliminary_accuracy_target": mape <= target,
        "chamber_temperature_k": chamber_temperature_k,
        "equilibrium_chamber_temperature_k": equilibrium_temperature_k,
        "measured_cstar_efficiency": cstar_efficiency,
        "thermochemistry_model": "Cantera GRI-Mech 3.0 HP equilibrium",
        "transport_model": "Cantera frozen-composition local transport",
        "gas_side_model": (
            "momentum-integral regime selection with wall-normal thermal march"
        ),
        "wall_normal_thermal_model": (
            "implicit-axisymmetric-finite-volume-energy-march"
        ),
        "optimizer_used": False,
        "calibration_used": False,
        "independent_of_correlation_source": True,
        "validation_level": "independent-conventional-bell-nozzle-hot-fire",
        "interpretation": (
            "The transition-aware march is evaluated without tuning. The laminar-to-turbulent "
            "envelope records boundary-layer regime uncertainty independently of the nominal result."
        ),
    }
