"""Tests for the injector, line-loss, and chamber mass-balance closure."""

import math

import pytest

from stanthrust.design_model import create_engine_design
from stanthrust.hydraulic_chamber_solver import (
    propagate_hydraulic_uncertainty,
    solve_hydraulic_chamber,
)


def _design_inputs():
    return {
        "mode": "design",
        "target_chamber_pressure_kpa": 1500.0,
        "throat_area_m2": math.pi * (0.012**2) / 4.0,
        "cstar_m_s": 1450.0,
        "mixture_ratio": 1.4,
        "design_injector_dp_ratio": 0.20,
        "fuel": {
            "density_kg_m3": 790.0,
            "dynamic_viscosity_pa_s": 0.0012,
            "line_diameter_m": 0.012,
            "line_length_m": 1.2,
            "minor_loss_k": 6.0,
            "roughness_m": 1.5e-6,
            "discharge_coefficient": 0.72,
            "injector_area_mm2": 0.0,
            "supply_pressure_kpa": 0.0,
        },
        "oxidizer": {
            "density_kg_m3": 1140.0,
            "dynamic_viscosity_pa_s": 0.0002,
            "line_diameter_m": 0.011,
            "line_length_m": 1.4,
            "minor_loss_k": 7.5,
            "roughness_m": 1.5e-6,
            "discharge_coefficient": 0.72,
            "injector_area_mm2": 0.0,
            "supply_pressure_kpa": 0.0,
        },
    }


def _as_built_inputs(design_inputs, design_result):
    result = {
        **design_inputs,
        "mode": "analysis",
        "fuel": dict(design_inputs["fuel"]),
        "oxidizer": dict(design_inputs["oxidizer"]),
    }
    for role in ("fuel", "oxidizer"):
        result[role]["injector_area_mm2"] = design_result[role]["injector_area_mm2"]
        result[role]["supply_pressure_kpa"] = design_result[role]["required_supply_pressure_kpa"]
    return result


def test_design_mode_sizes_injector_and_closes_mass_balance():
    inputs = _design_inputs()
    result = solve_hydraulic_chamber(inputs)

    assert result["status"] == "ok"
    assert result["converged"] is True
    assert result["chamber_pressure_kpa"] == pytest.approx(1500.0)
    assert result["mass_balance_relative_error"] < 1e-10
    assert result["actual_mixture_ratio"] == pytest.approx(1.4)
    assert result["fuel"]["injector_area_mm2"] > 0.0
    assert result["oxidizer"]["injector_area_mm2"] > 0.0
    assert result["fuel"]["required_supply_pressure_kpa"] > 1500.0
    assert result["oxidizer"]["required_supply_pressure_kpa"] > 1500.0


def test_coolant_pressure_constraint_resizes_only_fuel_injector_and_supply():
    nominal_inputs = _design_inputs()
    nominal = solve_hydraulic_chamber(nominal_inputs)
    constrained_inputs = _design_inputs()
    constrained_inputs["fuel_minimum_injector_inlet_pressure_kpa"] = 5000.0
    constrained_inputs["fuel_regen_pressure_drop_kpa"] = 35.0
    constrained = solve_hydraulic_chamber(constrained_inputs)

    assert constrained["fuel"]["minimum_injector_inlet_constraint_active"] is True
    assert constrained["fuel"]["injector_pressure_drop_kpa"] == pytest.approx(3500.0)
    assert constrained["fuel"]["injector_area_mm2"] < nominal["fuel"]["injector_area_mm2"]
    assert constrained["fuel"]["required_supply_pressure_kpa"] > 5035.0
    assert constrained["oxidizer"]["injector_area_mm2"] == pytest.approx(
        nominal["oxidizer"]["injector_area_mm2"]
    )


def test_analysis_mode_recovers_nominal_sized_operating_point():
    design_inputs = _design_inputs()
    design = solve_hydraulic_chamber(design_inputs)
    analysis = solve_hydraulic_chamber(_as_built_inputs(design_inputs, design))

    assert analysis["converged"] is True
    assert analysis["chamber_pressure_kpa"] == pytest.approx(1500.0, abs=0.01)
    assert analysis["total_mass_flow_kg_s"] == pytest.approx(
        design["total_mass_flow_kg_s"], rel=2e-5
    )
    assert analysis["actual_mixture_ratio"] == pytest.approx(1.4, rel=2e-5)
    assert analysis["mass_balance_relative_error"] < 1e-5


def test_analysis_pressure_responds_to_supply_pressure():
    design_inputs = _design_inputs()
    design = solve_hydraulic_chamber(design_inputs)
    nominal_inputs = _as_built_inputs(design_inputs, design)
    nominal = solve_hydraulic_chamber(nominal_inputs)
    raised_inputs = _as_built_inputs(design_inputs, design)
    raised_inputs["fuel"]["supply_pressure_kpa"] *= 1.08
    raised_inputs["oxidizer"]["supply_pressure_kpa"] *= 1.08
    raised = solve_hydraulic_chamber(raised_inputs)

    assert raised["chamber_pressure_kpa"] > nominal["chamber_pressure_kpa"]
    assert raised["total_mass_flow_kg_s"] > nominal["total_mass_flow_kg_s"]


def test_analysis_mode_rejects_missing_hardware_boundary_conditions():
    inputs = _design_inputs()
    inputs["mode"] = "analysis"
    with pytest.raises(ValueError, match="fuel supply pressure"):
        solve_hydraulic_chamber(inputs)


def test_analysis_geometry_uses_specified_throat_without_resizing():
    design = create_engine_design(
        {
            "pressure_solve_mode": "analysis",
            "analysis_throat_diameter_mm": 12.3,
        }
    )
    values = design.derived.engineering_values

    assert values["nozzle_throat_diameter_mm"] == pytest.approx(12.3)
    assert values["nozzle_throat_sizing_method"] == "Measured analysis geometry"


def test_uncertainty_propagation_is_deterministic_and_input_driven():
    inputs = _design_inputs()
    first = propagate_hydraulic_uncertainty(
        inputs,
        sample_count=48,
        seed=42,
        thrust_coefficient=1.35,
    )
    second = propagate_hydraulic_uncertainty(
        inputs,
        sample_count=48,
        seed=42,
        thrust_coefficient=1.35,
    )

    assert first == second
    assert first["sample_count_accepted"] == 48
    intervals = {row["name"]: row for row in first["intervals"]}
    assert intervals["chamber_pressure_kpa"]["p05"] < intervals["chamber_pressure_kpa"]["p95"]
    assert intervals["total_mass_flow_kg_s"]["p05"] < intervals["total_mass_flow_kg_s"]["p95"]
    assert intervals["predicted_thrust_newtons"]["p05"] < intervals["predicted_thrust_newtons"]["p95"]
    assert first["pressure_sensitivity"]
    assert "thrust_coefficient" in first["relative_input_ranges"]
