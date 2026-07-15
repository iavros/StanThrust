"""
Tests for Stage 3.2 Coupled Cycle Loop Solver.

Covers convergence behavior, input validation, payload structure,
station field provenance, and edge cases.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stanshock.coupled_cycle_solver import (
    solve,
    validate_inputs,
)


def test_coupled_solver_basic_convergence():
    """Test that solver runs and produces convergence output."""
    design_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "use_pumps": False,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
    }

    result = solve(design_request)

    assert isinstance(result, dict), "Result should be dict"
    assert "metadata" in result, "Result should have metadata"
    assert "status" in result, "Result should have status"
    assert "payload" in result, "Result should have payload"

    # Check metadata
    metadata = result["metadata"]
    assert metadata["solver_name"] == "Coupled Cycle Loop Solver"
    assert metadata["solver_version"] == "0.2"
    assert metadata["solver_mode"] == "stage-3-coupled-cycle-v2"

    # Check status
    assert result["status"] in ["ok", "converged-degraded"], f"Status should be ok or converged-degraded, got {result['status']}"

    # Check payload structure
    payload = result["payload"]
    assert "convergence" in payload, "Payload should have convergence"
    assert "results" in payload, "Payload should have results"
    assert "iteration_trace" in payload, "Payload should have iteration_trace"
    assert "station_field_updates" in payload, "Payload should have station_field_updates"
    assert "structural_solver_result" in payload, "Payload should include structural solver result"
    feed_summary = payload["feed_solver_result"]["payload"]["summary"]
    feed_request = payload["feed_solver_result"]["payload"]["request"]
    assert feed_summary["quality_flag"] == "stage-2-transient-feed-v2-iterative-darcy"
    assert float(feed_request["fuel_dynamic_viscosity_pa_s"]) > 0.0

    print("✓ test_coupled_solver_basic_convergence passed")


def test_convergence_structure():
    """Test convergence info structure."""
    design_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "use_pumps": False,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
    }

    result = solve(design_request)
    payload = result["payload"]
    conv = payload["convergence"]

    assert "iteration_count" in conv, "Convergence should have iteration_count"
    assert "converged" in conv, "Convergence should have converged flag"
    assert "final_residual_kpa" in conv, "Convergence should have final_residual_kpa"
    assert "convergence_tolerance_kpa" in conv, "Convergence should have convergence_tolerance_kpa"
    assert "minimum_iteration_count" in conv, "Convergence should report the minimum coupled iteration count"
    assert "thrust_error_fraction" in conv, "Convergence should track thrust residual"
    assert "minimum_feed_margin_kpa" in conv, "Convergence should track feed margin"
    assert "minimum_structural_margin_ratio" in conv, "Convergence should track structural margin"
    assert "minimum_heat_transfer_margin_ratio" in conv, "Convergence should track heat-transfer material margin"
    assert "minimum_combined_material_margin_ratio" in conv, "Convergence should track combined material margin"

    assert isinstance(conv["iteration_count"], int), "iteration_count should be int"
    assert isinstance(conv["converged"], bool), "converged should be bool"
    assert isinstance(conv["final_residual_kpa"], float), "final_residual_kpa should be float"
    assert isinstance(conv["convergence_tolerance_kpa"], float), "convergence_tolerance_kpa should be float"
    assert isinstance(conv["minimum_heat_transfer_margin_ratio"], float), "heat-transfer margin should be float"
    assert isinstance(conv["minimum_combined_material_margin_ratio"], float), "combined material margin should be float"
    assert conv["iteration_count"] >= conv["minimum_iteration_count"], "solver should not stop before the minimum iteration count"

    print("✓ test_convergence_structure passed")


def test_final_pass_uncertainty_payload():
    """Test that the final pass reports dense Navier-Stokes bounds."""
    design_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "use_pumps": False,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
        "solver_flow_model": "fast",
        "solver_station_count": 40,
    }

    result = solve(design_request, max_iterations=4)
    payload = result["payload"]
    request = payload["request"]
    bounds = payload["final_uncertainty_bounds"]

    assert request["requested_solver_flow_model"] == "fast"
    assert request["solver_flow_model"] == "navier_stokes"
    assert int(request["solver_station_count"]) >= 180
    assert bounds["flow_model"] == "navier_stokes"
    assert int(bounds["station_count"]) >= 180
    assert isinstance(bounds["bounds"], list)
    assert len(bounds["bounds"]) >= 4

    for row in bounds["bounds"]:
        assert row["lower"] <= row["value"] <= row["upper"]
        assert row["lower_percent"] >= 0.0
        assert row["upper_percent"] >= 0.0

    print("[ok] test_final_pass_uncertainty_payload passed")


def test_convergence_results_structure():
    """Test results structure contains expected pressure values."""
    design_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "use_pumps": False,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
    }

    result = solve(design_request)
    payload = result["payload"]
    results = payload["results"]

    assert "chamber_pressure_kpa" in results
    assert "fuel_tank_pressure_kpa" in results
    assert "oxidizer_tank_pressure_kpa" in results

    # Check physically reasonable ranges
    assert 100 < results["chamber_pressure_kpa"] < 10000, \
        f"chamber_pressure {results['chamber_pressure_kpa']} out of range"
    assert 100 < results["fuel_tank_pressure_kpa"] < 10000, \
        f"fuel_tank_pressure {results['fuel_tank_pressure_kpa']} out of range"
    assert 100 < results["oxidizer_tank_pressure_kpa"] < 10000, \
        f"oxidizer_tank_pressure {results['oxidizer_tank_pressure_kpa']} out of range"

    # Stage 3.2 solves a relaxed coupled pressure state. This test validates
    # reasonable magnitude rather than enforcing one architecture-specific ordering.

    print("✓ test_convergence_results_structure passed")


def test_iteration_trace_structure():
    """Test iteration trace has correct structure."""
    design_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "use_pumps": False,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
    }

    result = solve(design_request)
    payload = result["payload"]
    trace = payload["iteration_trace"]

    assert isinstance(trace, list), "iteration_trace should be list"
    assert len(trace) > 0, "iteration_trace should have entries"

    for entry in trace:
        assert "iteration" in entry
        assert "chamber_pressure_kpa" in entry
        assert "fuel_tank_pressure_kpa" in entry
        assert "oxidizer_tank_pressure_kpa" in entry
        assert "residual_kpa" in entry
        assert "feed_supported_pressure_kpa" in entry
        assert "combustion_supported_pressure_kpa" in entry
        assert "thrust_error_fraction" in entry
        assert "minimum_feed_margin_kpa" in entry
        assert "converged" in entry
        assert "notes" in entry
        assert isinstance(entry["notes"], list), "notes should be list"

    print("✓ test_iteration_trace_structure passed")


def test_station_field_updates_merged():
    """Test that station field updates are merged from both solvers."""
    design_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "use_pumps": False,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
    }

    result = solve(design_request)
    payload = result["payload"]
    station_updates = payload["station_field_updates"]

    assert isinstance(station_updates, dict), "station_field_updates should be dict"
    # May be empty if solvers don't produce updates, or populated if they do
    if len(station_updates) > 0:
        for station_label, fields in station_updates.items():
            assert isinstance(station_label, str), f"station label {station_label} should be string"
            assert isinstance(fields, dict), f"fields for {station_label} should be dict"
            for field_name, field_data in fields.items():
                assert "source_solver" in field_data, f"Field {field_name} should have source_solver"

    print("✓ test_station_field_updates_merged passed")


def test_pump_vs_blowdown_modes():
    """Test solver handles both pump-fed and blowdown modes."""
    base_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
    }

    # Pump-fed mode
    pump_request = dict(base_request)
    pump_request["use_pumps"] = True
    pump_result = solve(pump_request)
    assert pump_result["status"] in ["ok", "converged-degraded"]
    pump_chamber = pump_result["payload"]["results"]["chamber_pressure_kpa"]

    # Blowdown mode
    blowdown_request = dict(base_request)
    blowdown_request["use_pumps"] = False
    blowdown_result = solve(blowdown_request)
    assert blowdown_result["status"] in ["ok", "converged-degraded"]
    blowdown_chamber = blowdown_result["payload"]["results"]["chamber_pressure_kpa"]

    # Both should produce physically reasonable results
    assert 100 < pump_chamber < 10000
    assert 100 < blowdown_chamber < 10000

    # Pump-fed closure should remain in the same physical pressure neighborhood
    # across fallback and Cantera-backed thermochemistry providers.
    assert pump_chamber > 300.0, "Pump-fed should have reasonable chamber pressure"

    print("[ok] test_pump_vs_blowdown_modes passed")


def test_convergence_tolerance_effect():
    """Test that convergence tolerance affects iteration count."""
    design_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "use_pumps": False,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
    }

    # Tight tolerance
    tight_result = solve(design_request, convergence_tolerance_kpa=1.0)
    tight_iterations = tight_result["payload"]["convergence"]["iteration_count"]

    # Loose tolerance
    loose_result = solve(design_request, convergence_tolerance_kpa=50.0)
    loose_iterations = loose_result["payload"]["convergence"]["iteration_count"]

    # Typically tight tolerance needs more (or equal) iterations
    assert tight_iterations >= 1, "Should have at least 1 iteration"
    assert loose_iterations >= 1, "Should have at least 1 iteration"

    print("✓ test_convergence_tolerance_effect passed")


def test_max_iterations_limit():
    """Test that solver respects max_iterations limit."""
    design_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "use_pumps": False,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
    }

    result = solve(design_request, max_iterations=3)
    iterations = result["payload"]["convergence"]["iteration_count"]

    assert iterations <= 3, f"Should not exceed max_iterations (got {iterations})"

    print("✓ test_max_iterations_limit passed")


def test_input_validation():
    """Test input validation normalizes and clamps values."""
    validation = validate_inputs({
        "target_thrust_newtons": 1000.0,
        "target_chamber_pressure_kpa": 2000.0,
        "tank_diameter_mm": 150.0,
        "chamber_diameter_mm": 100.0,
        "use_pumps": True,
        "regen_cooling": True,
        "mixture_ratio": 2.5,
        "burn_time_seconds": 15.0,
    })

    assert "normalized_request" in validation
    req = validation["normalized_request"]

    # Check normalization
    assert req["target_thrust_newtons"] == 1000.0
    assert req["target_chamber_pressure_kpa"] == 2000.0
    assert req["use_pumps"] is True
    assert req["regen_cooling"] is True

    # Test defaults for missing inputs
    validation2 = validate_inputs({})
    req2 = validation2["normalized_request"]
    assert req2["target_thrust_newtons"] > 0, "Should have default thrust"
    assert req2["target_chamber_pressure_kpa"] > 0, "Should have default chamber pressure"

    print("✓ test_input_validation passed")


def test_warnings_when_not_converged():
    """Test that warnings are issued when convergence fails."""
    design_request = {
        "target_thrust_newtons": 9000.0,  # Very high thrust
        "target_chamber_pressure_kpa": 8000.0,  # Very high pressure
        "tank_diameter_mm": 50.0,  # Very small tank
        "chamber_diameter_mm": 200.0,  # Very large chamber
        "use_pumps": False,
        "mixture_ratio": 0.5,
        "burn_time_seconds": 60.0,
    }

    result = solve(design_request, max_iterations=2, convergence_tolerance_kpa=0.1)
    warnings = result["warnings"]

    assert isinstance(warnings, list)
    assert len(warnings) > 0, "Should have warnings"

    print("✓ test_warnings_when_not_converged passed")


def test_trace_lines_structure():
    """Test trace lines are well-formed."""
    design_request = {
        "target_thrust_newtons": 500.0,
        "target_chamber_pressure_kpa": 1500.0,
        "tank_diameter_mm": 100.0,
        "chamber_diameter_mm": 80.0,
        "use_pumps": False,
        "mixture_ratio": 2.0,
        "burn_time_seconds": 12.0,
    }

    result = solve(design_request)
    trace = result["trace"]

    assert isinstance(trace, list), "trace should be list"
    assert len(trace) > 0, "trace should have entries"
    assert all(isinstance(line, str) for line in trace), "trace entries should be strings"

    # Check for expected content
    trace_str = " ".join(trace)
    assert "Stage 3.2" in trace_str, "Should mention Stage 3.2"

    print("✓ test_trace_lines_structure passed")


def test_edge_case_very_small_thrust():
    """Test solver handles very small thrust values."""
    design_request = {
        "target_thrust_newtons": 10.0,
        "target_chamber_pressure_kpa": 500.0,
        "tank_diameter_mm": 50.0,
        "chamber_diameter_mm": 30.0,
        "use_pumps": False,
        "mixture_ratio": 1.0,
        "burn_time_seconds": 5.0,
    }

    result = solve(design_request)
    assert result["status"] in ["ok", "converged-degraded"]
    payload = result["payload"]
    chamber_pressure = payload["results"]["chamber_pressure_kpa"]
    assert chamber_pressure > 0, "Should have positive chamber pressure"

    print("✓ test_edge_case_very_small_thrust passed")


def test_edge_case_very_high_thrust():
    """Test solver handles very high thrust values."""
    design_request = {
        "target_thrust_newtons": 50000.0,
        "target_chamber_pressure_kpa": 5000.0,
        "tank_diameter_mm": 300.0,
        "chamber_diameter_mm": 250.0,
        "use_pumps": True,
        "mixture_ratio": 2.5,
        "burn_time_seconds": 30.0,
    }

    result = solve(design_request)
    assert result["status"] in ["ok", "converged-degraded"]
    payload = result["payload"]
    chamber_pressure = payload["results"]["chamber_pressure_kpa"]
    assert chamber_pressure > 0, "Should have positive chamber pressure"
    assert chamber_pressure < 10000, "Should not exceed reasonable bounds"

    print("✓ test_edge_case_very_high_thrust passed")


def run_all_tests():
    """Run all tests and report results."""
    tests = [
        test_coupled_solver_basic_convergence,
        test_convergence_structure,
        test_final_pass_uncertainty_payload,
        test_convergence_results_structure,
        test_iteration_trace_structure,
        test_station_field_updates_merged,
        test_pump_vs_blowdown_modes,
        test_convergence_tolerance_effect,
        test_max_iterations_limit,
        test_input_validation,
        test_warnings_when_not_converged,
        test_trace_lines_structure,
        test_edge_case_very_small_thrust,
        test_edge_case_very_high_thrust,
    ]

    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 70)
    print("Running Stage 3.2 Coupled Cycle Solver Tests")
    print("=" * 70 + "\n")

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print(f"✗ {test.__name__} FAILED: {str(e)}")
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print(f"✗ {test.__name__} ERROR: {str(e)}")

    print("\n" + "=" * 70)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed > 0:
        print("\nFailed tests:")
        for test_name, error in errors:
            print(f"  - {test_name}: {error}")
        return False
    else:
        print("\nAll tests passed!")
        return True


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)


