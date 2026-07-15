from stanshock.design_model import create_engine_design
from stanshock.heat_transfer_solver import (
    solve_coolant_side_heat_transfer,
    solve_gas_side_heat_transfer,
    solve_engine_heat_transfer,
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
    assert summary["max_hot_wall_temperature_k"] < 3350.0
    assert len(result["sections"]) == 3
    assert all(section["heat_load_kw"] > 0.0 for section in result["sections"])


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

