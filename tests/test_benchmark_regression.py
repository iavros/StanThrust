"""Benchmark reconstruction and regression checks."""

from stanthrust.benchmark_cases import (
    build_internal_baseline_rows,
    build_public_benchmark_reference_rows,
    build_reconstructed_benchmark_rows,
    get_public_benchmark_cases,
)
from stanthrust.inputs import get_default_solver_assumptions


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
        assert row["solver_validation_path"] == "direct_fixed-pressure_final-mode"
        assert int(row["solver_station_count"]) >= 160

    expected_windows = {
        "Elysium": {
            "simulated_thrust_n": (1370.0, 1460.0),
            "simulated_chamber_pressure_kpa": (1030.0, 1038.0),
            "simulated_isp_seconds": (184.0, 194.0),
        },
        "Juno": {
            "simulated_thrust_n": (1730.0, 1840.0),
            "simulated_chamber_pressure_kpa": (3090.0, 3115.0),
            "simulated_isp_seconds": (225.0, 237.0),
        },
        "Iron Lotus": {
            "simulated_thrust_n": (12100.0, 12800.0),
            "simulated_chamber_pressure_kpa": (3435.0, 3460.0),
            "simulated_isp_seconds": (190.0, 202.0),
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
        assert abs(float(row["thrust_error_percent"])) <= 16.0
        assert abs(float(row["chamber_pressure_error_percent"])) <= 0.1
        if row["mass_flow_error_percent"] != "":
            assert abs(float(row["mass_flow_error_percent"])) <= 22.0
        if row["isp_error_percent"] != "":
            assert abs(float(row["isp_error_percent"])) <= 8.0
