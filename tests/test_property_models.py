"""Tests for coolant and material property models and the thermal validation runs."""

import pytest

from stanthrust.design_model import create_engine_design
from stanthrust.fluid_properties import (
    coolant_default_inlet_temperature_k,
    coolant_phase_envelope,
    coolant_property_state,
)
from stanthrust.heat_transfer_solver import solve_engine_heat_transfer
from stanthrust.inputs import lookup_propellant
from stanthrust.material_properties import material_property_state
from stanthrust.thermal_validation import (
    evaluate_nasa_tp2726_bell_nozzle,
    evaluate_nasa_tp3380_calorimeter,
    evaluate_nasa_tp_3380_correlation,
)


def test_ethanol_properties_match_coolprop_reference_state():
    state = coolant_property_state("Ethanol", 293.15, 2000.0)

    assert state["density_kg_m3"] == pytest.approx(791.09, rel=0.01)
    assert state["cp_j_kg_k"] == pytest.approx(2393.64, rel=0.01)
    assert state["viscosity_pa_s"] == pytest.approx(0.0012078, rel=0.02)
    assert state["conductivity_w_m_k"] == pytest.approx(0.16547, rel=0.02)


def test_methane_uses_cryogenic_default_and_pressure_dependent_state():
    assert coolant_default_inlet_temperature_k("Methane") == 110.0
    low_pressure = coolant_property_state("Methane", 110.0, 1500.0)
    high_pressure = coolant_property_state("Methane", 110.0, 5000.0)

    assert high_pressure["density_kg_m3"] > low_pressure["density_kg_m3"]
    assert low_pressure["phase"]


def test_unsupported_regen_coolant_is_rejected_instead_of_estimated():
    with pytest.raises(ValueError, match="Supported coolants"):
        coolant_property_state("Isopropyl Alcohol", 293.15, 2000.0)


def test_unsupported_propellant_is_rejected_instead_of_synthesized():
    with pytest.raises(ValueError, match="Supported options"):
        lookup_propellant("Custom Fuel", "fuel")


def test_methane_regen_calculates_supercritical_pressure_requirement():
    design = create_engine_design({"fuel_name": "Methane", "regen_cooling": True})
    combustion = {
        "summary": {
            "chamber_pressure_kpa": 1550.0,
            "chamber_temperature_k": 3350.0,
            "gamma": 1.22,
            "gas_constant_j_kgk": 355.0,
        },
        "axial_profile": [
            {"x_mm": 0.0, "mach": 0.1},
            {"x_mm": 25.0, "mach": 1.0},
            {"x_mm": 50.0, "mach": 2.35},
        ],
    }

    result = solve_engine_heat_transfer(design, combustion)
    summary = result["summary"]
    envelope = coolant_phase_envelope("Methane")

    assert summary["coolant_pressure_requirement_met"] is True
    assert summary["coolant_pressure_redesign_required"] is True
    assert summary["coolant_phase_pressure_basis"] == "critical-pressure"
    assert summary["coolant_minimum_single_phase_pressure_kpa"] > envelope[
        "critical_pressure_kpa"
    ]
    assert summary["coolant_required_inlet_pressure_kpa"] > summary[
        "coolant_minimum_single_phase_pressure_kpa"
    ]


def test_methane_regen_analysis_rejects_insufficient_specified_pressure():
    design = create_engine_design(
        {
            "fuel_name": "Methane",
            "regen_cooling": True,
            "pressure_solve_mode": "analysis",
            "analysis_throat_diameter_mm": 12.0,
            "regen_coolant_inlet_pressure_kpa": 1800.0,
            "fuel_supply_pressure_kpa": 1800.0,
        }
    )

    with pytest.raises(ValueError, match="below the calculated single-phase requirement"):
        solve_engine_heat_transfer(design)


def test_material_properties_change_with_temperature_and_report_provenance():
    cold = material_property_state("Inconel 625", 294.15)
    hot = material_property_state("Inconel 625", 1033.15)

    assert hot["thermal_conductivity_w_m_k"] > cold["thermal_conductivity_w_m_k"]
    assert hot["allowable_stress_mpa"] < cold["allowable_stress_mpa"]
    assert hot["allowable_stress_lower_mpa"] < hot["allowable_stress_mpa"]
    assert hot["in_property_range"] is True
    assert "Special Metals" in hot["source"]


def test_nasa_calorimeter_correlation_validation_is_fixed_and_not_optimized():
    result = evaluate_nasa_tp_3380_correlation()

    assert result["coefficient_inside_measured_range"] is True
    assert result["distance_from_range_midpoint_percent"] == pytest.approx(0.0)
    assert result["measured_throat_heat_flux_mw_m2"] == 54.0
    assert result["optimizer_used"] is False


def test_nasa_calorimeter_series_is_geometry_matched_and_not_calibrated():
    result = evaluate_nasa_tp3380_calorimeter()

    assert result["case_count"] == 7
    assert result["annular_hydraulic_diameter_mm"] == pytest.approx(12.7)
    assert result["optimizer_used"] is False
    assert result["calibration_used"] is False
    assert result["independent_of_correlation_source"] is False
    assert result["validation_level"] == "geometry-matched-experimental-heat-flux-series"
    assert all(row["measurement_basis"] == "direct" for row in result["predictions"])


def test_nasa_calorimeter_series_reports_bounded_error_and_uncertainty_coverage():
    result = evaluate_nasa_tp3380_calorimeter()

    assert result["median_absolute_percent_error"] < 8.0
    assert result["p95_absolute_percent_error"] < 12.0
    assert result["correlation_interval_coverage_fraction"] >= 5.0 / 7.0
    assert result["nominal_predicted_throat_heat_flux_mw_m2"] == pytest.approx(
        result["nominal_measured_throat_heat_flux_mw_m2"], rel=0.08
    )


def test_nasa_calorimeter_series_is_deterministic():
    first = evaluate_nasa_tp3380_calorimeter()
    second = evaluate_nasa_tp3380_calorimeter()

    assert first["predictions"] == second["predictions"]
    assert first["mean_absolute_percent_error"] == second["mean_absolute_percent_error"]


def test_nasa_tp2726_bell_nozzle_validation_is_independent_and_not_fitted():
    result = evaluate_nasa_tp2726_bell_nozzle()

    assert result["source_measurement_count"] == 12
    assert result["case_count"] == 10
    assert result["excluded_measurement_count"] == 2
    assert result["optimizer_used"] is False
    assert result["calibration_used"] is False
    assert result["independent_of_correlation_source"] is True
    assert result["validation_level"] == "independent-conventional-bell-nozzle-hot-fire"
    assert all(row["measurement_basis"] == "direct" for row in result["predictions"])


def test_nasa_tp2726_bell_nozzle_reports_current_applicability_limit():
    result = evaluate_nasa_tp2726_bell_nozzle()

    assert result["monotonic_heat_flux_prediction"] is True
    assert result["meets_preliminary_accuracy_target"] is True
    assert 15.0 < result["mean_absolute_percent_error"] < 25.0
    assert result["mean_signed_percent_error"] > 0.0
    assert result["p95_absolute_percent_error"] > 75.0
    assert result["maximum_absolute_percent_error"] > 100.0
    assert result["model_envelope_coverage_fraction"] >= 0.8
    assert result["boundary_layer_regime_counts"] == {"relaminarized": 10}
    assert result["maximum_acceleration_parameter"] > 0.0
    assert result["predictions"][-1]["heat_transfer_coefficient_w_m2_k"] < 150.0
    assert all(row["wall_normal_node_count"] == 96 for row in result["predictions"])
    assert max(
        row["thermal_grid_refinement_error_percent"]
        for row in result["predictions"]
    ) < 5.0


def test_nasa_tp2726_bell_nozzle_validation_is_deterministic():
    first = evaluate_nasa_tp2726_bell_nozzle()
    second = evaluate_nasa_tp2726_bell_nozzle()

    assert first["predictions"] == second["predictions"]
    assert first["mean_absolute_percent_error"] == second["mean_absolute_percent_error"]
