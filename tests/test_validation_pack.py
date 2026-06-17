"""Regression tests for Stage 4.1: validation pack.

These tests ensure that concept solver outputs remain stable and fall within
expected physical bounds. They serve as regression gates to catch unintended
changes in solver behavior.
"""

import sys

sys.path.insert(0, r"E:/LIQUID_ENGINE")

from liquid_engine_studio.combustion_cfd_solver import run_combustion_cfd_proxy
from liquid_engine_studio.concept_model import create_concept_design
from liquid_engine_studio.solver_assumptions import get_default_solver_assumptions
from liquid_engine_studio.validation_pack import (
    get_regression_baseline_cases,
    get_regression_baselines,
    validate_concept_design,
)


def test_default_design_validation():
    """Test that a default design passes all analytical checks."""
    design = create_concept_design({})
    report = validate_concept_design(design)

    assert report.passed, (
        f"Validation report failed: {report.summary}\n"
        f"Checks: {[(c.check_name, c.passed, c.message) for c in report.checks]}"
    )
    assert any(check.check_name == "section_margins" and check.passed for check in report.checks)
    print(f"[ok] test_default_design_validation passed: {report.summary}")


def test_high_thrust_design_validation():
    """Test validation for a high-thrust design."""
    design = create_concept_design(
        {
            "target_thrust_newtons": 5000.0,
            "burn_time_seconds": 45.0,
        }
    )
    report = validate_concept_design(design)

    assert report.passed, f"High-thrust validation failed: {report.summary}"
    print(f"[ok] test_high_thrust_design_validation passed: {report.summary}")


def test_low_thrust_design_validation():
    """Test validation for a low-thrust design."""
    design = create_concept_design(
        {
            "target_thrust_newtons": 250.0,
            "burn_time_seconds": 5.0,
        }
    )
    report = validate_concept_design(design)

    assert report.passed, f"Low-thrust validation failed: {report.summary}"
    print(f"[ok] test_low_thrust_design_validation passed: {report.summary}")


def test_pump_fed_design_validation():
    """Test validation for a pump-fed design with explicit pump-head closure."""
    design = create_concept_design(
        {
            "use_pumps": True,
            "regen_cooling": False,
        }
    )
    report = validate_concept_design(design)
    eng = design.derived.engineering_values

    assert report.passed, f"Pump-fed validation failed: {report.summary}"
    assert float(eng.get("pump_discharge_pressure_kpa", 0.0)) > float(
        eng.get("chamber_pressure_kpa", 0.0)
    )
    assert float(eng.get("fuel_pressure_margin_kpa", -1.0)) >= 0.0
    assert float(eng.get("oxidizer_pressure_margin_kpa", -1.0)) >= 0.0
    print(f"[ok] test_pump_fed_design_validation passed: {report.summary}")


def test_blowdown_design_validation():
    """Test validation for a blowdown design."""
    design = create_concept_design(
        {
            "use_pumps": False,
            "regen_cooling": True,
            "film_cooling": True,
        }
    )
    report = validate_concept_design(design)

    assert report.passed, f"Blowdown validation failed: {report.summary}"
    print(f"[ok] test_blowdown_design_validation passed: {report.summary}")


def test_regression_baselines_within_range():
    """Test that default design meets regression baselines."""
    design = create_concept_design({})
    eng_vals = design.derived.engineering_values
    baselines = dict(get_regression_baselines())
    baselines["nozzle_expansion_ratio"] = (1.0, 16.0)

    target_thrust = float(design.inputs.target_thrust_newtons)
    calculated_thrust = float(eng_vals.get("calculated_thrust_newtons", target_thrust))
    calculated_impulse = float(eng_vals.get("calculated_impulse_newton_seconds", 1.0))
    expected_impulse = calculated_thrust * float(
        eng_vals.get("calculated_burn_time_seconds", 1.0)
    )

    thrust_mult = calculated_thrust / max(1.0, target_thrust)
    impulse_ratio = calculated_impulse / max(1.0, expected_impulse)
    chamber_p = float(eng_vals.get("chamber_pressure_kpa", 0.0))
    fuel_tank_p = float(eng_vals.get("fuel_tank_pressure_kpa", 0.0))
    tank_p_ratio = fuel_tank_p / max(1.0, chamber_p)
    dry_mass = design.derived.dry_mass_index
    thermal = design.derived.thermal_margin_index
    packaging = design.derived.packaging_efficiency_index
    nozzle_expansion = float(eng_vals.get("nozzle_expansion_ratio", 1.0))

    observed = {
        "thrust_multiplier": thrust_mult,
        "impulse_ratio": impulse_ratio,
        "chamber_pressure_kpa": chamber_p,
        "tank_pressure_ratio": tank_p_ratio,
        "dry_mass_index": dry_mass,
        "thermal_margin_index": thermal,
        "packaging_efficiency_index": packaging,
        "nozzle_expansion_ratio": nozzle_expansion,
    }

    failures = []
    for key, (min_val, max_val) in baselines.items():
        observed_val = observed.get(key, 0.0)
        if not (min_val <= observed_val <= max_val):
            failures.append(
                f"{key}: {observed_val:.3f} outside [{min_val:.3f}, {max_val:.3f}]"
            )

    assert not failures, "Regression baselines exceeded:\n" + "\n".join(failures)
    print(
        f"[ok] test_regression_baselines_within_range passed: all {len(baselines)} metrics within expected ranges"
    )


def test_section_margin_outputs_exist():
    """Test that section-based structural and thermal outputs are populated."""
    design = create_concept_design({})
    values = design.derived.engineering_values

    for prefix in ("fuel_tank", "oxidizer_tank", "chamber", "throat", "nozzle"):
        assert f"{prefix}_hoop_stress_mpa" in values
        assert f"{prefix}_allowable_stress_mpa" in values
        assert f"{prefix}_structural_margin_ratio" in values
        assert f"{prefix}_estimated_wall_temperature_k" in values
        assert f"{prefix}_thermal_margin_k" in values
        assert float(values[f"{prefix}_structural_margin_ratio"]) >= 1.0


def test_maximum_diameter_is_hard_capped_and_reported():
    """Test that the solved outer envelope honors the target diameter and reports uncapped demand."""
    design = create_concept_design(
        {
            "target_diameter_mm": 90.0,
            "tank_diameter_mm": 110.0,
            "chamber_diameter_mm": 86.0,
            "nozzle_diameter_mm": 88.0,
            "regen_cooling": True,
        }
    )
    values = design.derived.engineering_values

    assert float(design.derived.maximum_diameter_mm) <= float(design.inputs.target_diameter_mm) + 1e-6
    assert float(values.get("maximum_diameter_mm", 0.0)) <= float(design.inputs.target_diameter_mm) + 1e-6
    assert float(values.get("maximum_required_outer_diameter_mm", 0.0)) >= float(values.get("maximum_diameter_mm", 0.0))
    assert values.get("diameter_limit_status") in {"within_target_diameter", "capped_by_target_diameter"}


def test_internal_regression_baseline_cases_pass_and_stay_in_range():
    """Test that canonical internal baseline cases remain within their regression envelopes."""
    baseline_cases = get_regression_baseline_cases()

    for case_id, case in baseline_cases.items():
        design = create_concept_design(case["state"])
        report = validate_concept_design(design)
        values = design.derived.engineering_values

        assert report.passed, f"{case_id} validation failed: {report.summary}"

        observed = {
            "calculated_thrust_newtons": float(values.get("calculated_thrust_newtons", 0.0)),
            "chamber_pressure_kpa": float(values.get("chamber_pressure_kpa", 0.0)),
            "propellant_mass_flow_kg_s": float(values.get("propellant_mass_flow_kg_s", 0.0)),
            "calculated_impulse_newton_seconds": float(values.get("calculated_impulse_newton_seconds", 0.0)),
            "nozzle_expansion_ratio": float(values.get("nozzle_expansion_ratio", 0.0)),
            "total_stack_length_mm": float(design.derived.total_stack_length_mm),
            "thermal_margin_index": float(design.derived.thermal_margin_index),
        }

        failures = []
        for metric_name, (lower_bound, upper_bound) in case["expected_ranges"].items():
            observed_value = observed[metric_name]
            if not (lower_bound <= observed_value <= upper_bound):
                failures.append(
                    f"{metric_name}: {observed_value:.3f} outside [{lower_bound:.3f}, {upper_bound:.3f}]"
                )

        assert not failures, f"{case_id} regression drifted:\n" + "\n".join(failures)


def test_combustion_solver_consistency():
    """Test that combustion solver output integrates consistently with concept design."""
    design = create_concept_design({})
    assumptions = get_default_solver_assumptions()
    result = run_combustion_cfd_proxy(design, assumptions)

    assert isinstance(result, dict), "Combustion result must be a dict"
    assert "station_field_updates" in result, "Result must contain 'station_field_updates'"

    station_updates = result.get("station_field_updates", {})
    assert len(station_updates) > 0, "Station updates should not be empty"

    critical_stations = {"Chamber Mid", "Throat Region", "Nozzle Exit Plane"}
    found_stations = set(station_updates.keys())

    for station in critical_stations:
        if station in found_stations:
            station_data = station_updates[station]
            assert isinstance(station_data, dict), f"Station {station} data must be dict"
            if "temperature_k" in station_data:
                temp = station_data["temperature_k"].get("value")
                assert temp is not None and temp > 0, (
                    f"Temperature for {station} should be positive"
                )

    print("[ok] test_combustion_solver_consistency passed: combustion solver integrates correctly")


def test_refined_flow_mode_metadata():
    """Test that refined flow mode is surfaced in combustion metadata and summary."""
    from dataclasses import replace

    design = create_concept_design({})
    assumptions = replace(get_default_solver_assumptions(), flow_model="refined")
    result = run_combustion_cfd_proxy(design, assumptions)

    metadata = result.get("metadata", {})
    summary = result.get("summary", {})
    assert metadata.get("flow_model") == "refined"
    assert metadata.get("solver_mode") == "quasi-1d-refined"
    assert summary.get("flow_model") == "refined"
    assert summary.get("exit_pressure_kpa", 0.0) > 0.0
    assert summary.get("predicted_isp_seconds", 0.0) > 120.0


def run_all_tests():
    """Run all Stage 4.1 validation tests."""
    tests = [
        test_default_design_validation,
        test_high_thrust_design_validation,
        test_low_thrust_design_validation,
        test_pump_fed_design_validation,
        test_blowdown_design_validation,
        test_regression_baselines_within_range,
        test_section_margin_outputs_exist,
        test_maximum_diameter_is_hard_capped_and_reported,
        test_internal_regression_baseline_cases_pass_and_stay_in_range,
        test_combustion_solver_consistency,
        test_refined_flow_mode_metadata,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as exc:
            print(f"[fail] {test_func.__name__} failed: {exc}")
            failed += 1
        except Exception as exc:
            print(f"[error] {test_func.__name__} error: {exc}")
            failed += 1

    print(
        f"\nStage 4.1 Tests: {passed} passed, {failed} failed out of {len(tests)} total."
    )
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    raise SystemExit(0 if success else 1)
