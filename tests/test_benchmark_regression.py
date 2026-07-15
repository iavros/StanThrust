"""Benchmark reconstruction and regression checks."""

from stanshock.benchmark_cases import (
    build_internal_baseline_rows,
    build_public_benchmark_reference_rows,
    build_reconstructed_benchmark_rows,
    get_public_benchmark_cases,
)
from stanshock.solver_assumptions import get_default_solver_assumptions


def test_public_benchmark_reference_catalog_is_complete():
    """Assert the public collegiate benchmark catalog stays coherent and presentable."""
    rows = build_public_benchmark_reference_rows()
    cases = get_public_benchmark_cases()

    assert len(rows) == len(cases) >= 3
    for row in rows:
        assert row["engine"]
        assert row["team"]
        assert row["fuel_name"]
        assert row["oxidizer_name"]
        assert row["source_label"].startswith("[")
        assert str(row["source_url"]).startswith("https://")
        assert float(row["reference_thrust_n"]) > 0.0
        assert float(row["reference_chamber_pressure_kpa"]) > 0.0


def test_internal_baseline_rows_are_within_declared_ranges():
    """Assert generated internal baseline rows sit inside their stored regression envelopes."""
    rows = build_internal_baseline_rows()
    assert len(rows) >= 3

    metric_names = (
        "calculated_thrust_newtons",
        "chamber_pressure_kpa",
        "propellant_mass_flow_kg_s",
        "calculated_impulse_newton_seconds",
        "nozzle_expansion_ratio",
        "total_stack_length_mm",
        "thermal_margin_index",
    )
    for row in rows:
        for metric_name in metric_names:
            observed = float(row[f"{metric_name}_observed"])
            lower_bound = float(row[f"{metric_name}_min"])
            upper_bound = float(row[f"{metric_name}_max"])
            assert lower_bound <= observed <= upper_bound, (
                f"{row['case_id']} {metric_name} drifted to {observed:.3f} "
                f"outside [{lower_bound:.3f}, {upper_bound:.3f}]"
            )


def test_reconstructed_public_benchmark_outputs_are_stable_with_cantera():
    """Assert public benchmark reconstructions remain in expected neighborhoods."""
    import cantera  # noqa: F401

    rows = build_reconstructed_benchmark_rows(get_default_solver_assumptions())
    rows_by_engine = {row["engine"]: row for row in rows}
    for row in rows:
        assert row["optimizer_used"] == "no"
        assert row["solver_validation_path"] == "direct_navier_stokes_solver"
        assert int(row["solver_station_count"]) >= 160

    expected_windows = {
        "Elysium": {
            "simulated_thrust_n": (1180.0, 1265.0),
            "simulated_chamber_pressure_kpa": (890.0, 970.0),
            "simulated_isp_seconds": (150.0, 162.0),
        },
        "Juno": {
            "simulated_thrust_n": (1375.0, 1460.0),
            "simulated_chamber_pressure_kpa": (2520.0, 2665.0),
            "simulated_isp_seconds": (188.0, 200.0),
        },
        "Iron Lotus": {
            "simulated_thrust_n": (9950.0, 10580.0),
            "simulated_chamber_pressure_kpa": (2950.0, 3125.0),
            "simulated_isp_seconds": (152.0, 166.0),
        },
    }

    for engine, windows in expected_windows.items():
        row = rows_by_engine[engine]
        for metric_name, (lower_bound, upper_bound) in windows.items():
            observed = float(row[metric_name])
            assert lower_bound <= observed <= upper_bound, (
                f"{engine} {metric_name} drifted to {observed:.3f} "
                f"outside [{lower_bound:.3f}, {upper_bound:.3f}]"
            )


def run_all_tests():
    tests = [
        test_public_benchmark_reference_catalog_is_complete,
        test_internal_baseline_rows_are_within_declared_ranges,
        test_reconstructed_public_benchmark_outputs_are_stable_with_cantera,
    ]

    passed = 0
    failed = 0
    for test_func in tests:
        try:
            test_func()
            print(f"[ok] {test_func.__name__} passed")
            passed += 1
        except AssertionError as exc:
            print(f"[fail] {test_func.__name__} failed: {exc}")
            failed += 1
        except Exception as exc:
            print(f"[error] {test_func.__name__} error: {exc}")
            failed += 1

    print(f"\nBenchmark Tests: {passed} passed, {failed} failed out of {len(tests)} total.")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all_tests() else 1)
