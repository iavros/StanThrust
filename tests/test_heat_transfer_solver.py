"""Tests for the conjugate wall and coolant solve."""

import pytest

from stanthrust.design_model import create_engine_design
from stanthrust.heat_transfer_solver import (
    solve_coolant_side_heat_transfer,
    solve_engine_heat_transfer,
    solve_gas_side_heat_transfer,
)


def test_gas_side_heat_transfer_returns_positive_station_coefficients():
    result = solve_gas_side_heat_transfer(
        chamber_pressure_kpa=1400.0,
        chamber_temperature_k=3350.0,
        mach=1.0,
        hydraulic_diameter_mm=18.0,
        gamma=1.22,
        gas_constant_j_kg_k=355.0,
    )

    assert result["heat_transfer_coefficient_w_m2_k"] > 0.0
    assert result["reynolds"] > 0.0
    assert result["recovery_temperature_k"] > result["static_temperature_k"]


def test_coolant_side_heat_transfer_tracks_regen_channel_flow():
    result = solve_coolant_side_heat_transfer(
        coolant_mass_flow_kg_s=0.06,
        channel_count=32,
        channel_width_mm=2.4,
        channel_depth_mm=2.8,
        hydraulic_diameter_mm=2.58,
        coolant_density_kg_m3=789.0,
        coolant_cp_j_kg_k=2440.0,
        coolant_viscosity_pa_s=0.0012,
        coolant_conductivity_w_m_k=0.171,
    )

    assert result["velocity_m_s"] > 0.0
    assert result["reynolds"] > 0.0
    assert result["heat_transfer_coefficient_w_m2_k"] > 50.0
    assert result["regime"] in {"laminar", "transition", "turbulent"}


def test_engine_heat_transfer_solves_regenerative_network_from_design_geometry():
    design = create_engine_design(
        {
            "fuel_name": "Ethanol",
            "oxidizer_name": "Liquid Oxygen",
            "regen_cooling": True,
            "film_cooling": True,
        }
    )
    result = solve_engine_heat_transfer(
        design,
        {
            "summary": {
                "chamber_pressure_kpa": 1550.0,
                "chamber_temperature_k": 3350.0,
                "gamma": 1.22,
                "gas_constant_j_kgk": 355.0,
                "exit_mach": 2.35,
            },
            "axial_profile": [{"mach": 0.1}, {"mach": 1.0}, {"mach": 2.35}],
        },
    )

    summary = result["summary"]
    assert result["status"] == "calculated-regenerative"
    assert summary["regen_cooling_active"] is True
    assert summary["film_cooling_active"] is True
    assert summary["total_heat_load_kw"] > 0.0
    assert summary["coolant_outlet_temperature_k"] > summary["coolant_inlet_temperature_k"]
    assert summary["coolant_pressure_drop_kpa"] > 0.0
    assert summary["coolant_outlet_pressure_kpa"] < summary["coolant_inlet_pressure_kpa"]
    assert summary["max_hot_wall_temperature_k"] < 3350.0
    assert summary["boundary_layer_model"] == (
        "axisymmetric-wall-normal-energy-and-momentum-integral"
    )
    assert summary["wall_normal_node_count"] == 96
    assert summary["maximum_thermal_grid_refinement_error_percent"] >= 0.0
    assert summary["boundary_layer_profile_count"] >= 3
    assert summary["maximum_thermal_energy_relative_residual"] < 1e-6
    assert summary["computational_complexity"]["time"] == "O(Nx*Ny)"
    assert summary["relaminarization_acceleration_threshold"] == pytest.approx(2.0e-6)
    assert summary["maximum_acceleration_parameter"] >= 0.0
    assert summary["boundary_layer_status"] == (
        "regime-selection-and-envelope-active"
    )
    assert len(result["sections"]) == 3
    assert len(result["axial_stations"]) >= 40
    assert all("acceleration_parameter" in row for row in result["axial_stations"])
    assert all("boundary_layer_applicability" in row for row in result["axial_stations"])
    nozzle_rows = [
        row
        for row in result["axial_stations"]
        if row["gas"].get("wall_normal_node_count", 0) > 0
    ]
    assert nozzle_rows
    assert all(row["gas"]["wall_normal_node_count"] == 96 for row in nozzle_rows)
    assert all(row["gas"]["thermal_energy_residual"] < 1e-6 for row in nozzle_rows)
    assert len(result["boundary_layer_profiles"]) == summary["boundary_layer_profile_count"]
    for profile in result["boundary_layer_profiles"]:
        assert profile["temperature_ratio"][0] == 0.0
        assert profile["temperature_ratio"][-1] == 1.0
        assert profile["velocity_ratio"][0] == 0.0
        assert profile["velocity_ratio"][-1] == 1.0
    assert result["metadata"]["coolant_direction"] == "nozzle-exit-to-injector"
    assert all(row["x_mm"] <= result["axial_stations"][index + 1]["x_mm"] for index, row in enumerate(result["axial_stations"][:-1]))
    assert all(section["heat_load_kw"] > 0.0 for section in result["sections"])
    coolant_rows = [row["coolant"] for row in result["axial_stations"] if row["coolant"]]
    assert len({round(row["density_kg_m3"], 3) for row in coolant_rows}) > 1
    assert len({round(row["cp_j_kg_k"], 3) for row in coolant_rows}) > 1
    assert all(row["property_backend_version"] for row in coolant_rows)


def test_engine_heat_transfer_still_calculates_passive_wall_case():
    design = create_engine_design({"regen_cooling": False, "film_cooling": False})
    result = solve_engine_heat_transfer(
        design,
        {
            "summary": {
                "chamber_pressure_kpa": 1200.0,
                "chamber_temperature_k": 3200.0,
                "gamma": 1.22,
                "gas_constant_j_kgk": 355.0,
                "exit_mach": 2.0,
            },
            "axial_profile": [{"mach": 0.1}, {"mach": 1.0}, {"mach": 2.0}],
        },
    )

    assert result["status"] == "calculated-passive"
    assert result["summary"]["regen_cooling_active"] is False
    assert result["summary"]["total_heat_load_kw"] > 0.0
    assert result["sections"][0]["heat_sink"] == "ambient"
    assert len(result["axial_stations"]) >= 40
