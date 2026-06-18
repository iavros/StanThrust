import math

from stanshock.shock_solver import (
    find_nozzle_normal_shock_candidate,
    normal_shock_relations,
    oblique_shock_relations,
    rankine_hugoniot_jump_from_mach,
)


def test_normal_shock_matches_standard_mach_two_case():
    result = normal_shock_relations(2.0, gamma=1.4)

    assert result["status"] == "calculated"
    assert math.isclose(result["pressure_ratio"], 4.5, rel_tol=1e-6)
    assert math.isclose(result["density_ratio"], 2.6666667, rel_tol=1e-6)
    assert math.isclose(result["temperature_ratio"], 1.6875, rel_tol=1e-6)
    assert math.isclose(result["downstream_mach"], 0.5773503, rel_tol=1e-5)
    assert 0.70 < result["total_pressure_ratio"] < 0.73
    assert result["entropy_change_over_r"] > 0.0


def test_rankine_hugoniot_wrapper_uses_normal_shock_solver():
    direct = normal_shock_relations(2.35, gamma=1.32)
    wrapped = rankine_hugoniot_jump_from_mach(2.35, gamma=1.32)

    assert wrapped["model"] == "rankine-hugoniot-normal-shock"
    assert math.isclose(wrapped["pressure_ratio"], direct["pressure_ratio"], rel_tol=1e-12)
    assert math.isclose(wrapped["downstream_mach"], direct["downstream_mach"], rel_tol=1e-12)


def test_oblique_shock_theta_beta_m_weak_branch():
    result = oblique_shock_relations(2.0, 10.0, gamma=1.4, branch="weak")

    assert result["status"] == "calculated"
    assert 38.0 < result["shock_angle_deg"] < 41.0
    assert result["downstream_mach"] > 1.0
    assert result["pressure_ratio"] > 1.0
    assert result["total_pressure_ratio"] < 1.0


def test_nozzle_shock_candidate_uses_overexpanded_station_pressure_match():
    axial_profile = [
        {"x_mm": 0.0, "radius_mm": 30.0, "mach": 0.2, "pressure_kpa": 900.0},
        {"x_mm": 10.0, "radius_mm": 18.0, "mach": 1.0, "pressure_kpa": 480.0},
        {"x_mm": 20.0, "radius_mm": 22.0, "mach": 1.5, "pressure_kpa": 45.0, "temperature_k": 1800.0, "density_kg_m3": 0.2},
        {"x_mm": 30.0, "radius_mm": 26.0, "mach": 2.0, "pressure_kpa": 50.0, "temperature_k": 1500.0, "density_kg_m3": 0.08},
    ]

    result = find_nozzle_normal_shock_candidate(axial_profile, ambient_pressure_kpa=101.3, gamma=1.4)

    assert result["status"] == "normal-shock-candidate"
    assert result["regime"] == "overexpanded"
    assert result["shock_x_mm"] == 20.0
    assert result["downstream_pressure_kpa"] > result["upstream_pressure_kpa"]
    assert result["downstream_mach"] < 1.0

