"""Regression tests for combustion flow modes."""

import sys
from dataclasses import replace

sys.path.insert(0, r"E:/StanThrust")

from stanthrust.combustion_cfd_solver import run_combustion_cfd_solver
from stanthrust.design_model import create_engine_design
from stanthrust.inputs import get_default_solver_assumptions


def test_flow_modes_emit_distinct_metadata_and_outputs():
    """Assert the flow-mode switch changes metadata and produces the refined nozzle fields."""
    design = create_engine_design({})
    values = design.derived.engineering_values
    base_assumptions = get_default_solver_assumptions()

    fast_result = run_combustion_cfd_solver(
        design,
        replace(base_assumptions, flow_model="fast"),
        station_count=18,
    )
    refined_result = run_combustion_cfd_solver(
        design,
        replace(base_assumptions, flow_model="refined"),
        station_count=18,
    )

    assert fast_result["metadata"]["flow_model"] == "fast"
    assert refined_result["metadata"]["flow_model"] == "refined"
    assert fast_result["metadata"]["solver_mode"] == "design-fast"
    assert refined_result["metadata"]["solver_mode"] == "cantera-moc-characteristic-net"
    assert fast_result["metadata"]["throat_area_source"] == "solved_geometry"
    assert refined_result["metadata"]["throat_area_source"] == "solved_geometry"

    refined_nozzle = refined_result["physics"]["nozzle"]
    assert refined_nozzle["flow_model_label"] == "Characteristic-net viscous design solve"
    assert float(refined_nozzle["separation_efficiency"]) <= 1.0
    assert "curvature_efficiency" in refined_nozzle
    assert "bell_quality" in refined_nozzle
    assert float(refined_nozzle["exit_pressure_kpa"]) > 0.0
    assert refined_result["heat_transfer"]["status"].startswith("calculated")
    assert refined_result["shock_analysis"]["status"] in {
        "normal-shock-candidate",
        "overexpanded-no-station-match",
        "not-triggered",
        "not-supersonic",
    }

    fast_summary = fast_result["summary"]
    refined_summary = refined_result["summary"]
    assert fast_summary["flow_model"] == "fast"
    assert refined_summary["flow_model"] == "refined"
    assert refined_summary["axial_profile_model"] == "characteristic-net area-Mach station field from solved nozzle contour"
    assert values["nozzle_throat_sizing_method"] == "Choked thrust coefficient"
    target_exit_pressure = float(values["nozzle_exit_pressure_target_kpa"])
    simulated_exit_pressure = float(refined_summary["exit_pressure_kpa"])
    assert abs(simulated_exit_pressure - target_exit_pressure) / target_exit_pressure < 0.25
    assert abs(float(fast_summary["chamber_pressure_kpa"]) - float(refined_summary["chamber_pressure_kpa"])) > 0.1
    assert float(refined_summary["exit_pressure_kpa"]) > 0.0
    assert float(refined_summary["predicted_isp_seconds"]) > 120.0
    assert float(refined_summary["chamber_pressure_kpa"]) > float(fast_summary["chamber_pressure_kpa"])

    refined_profile = refined_result["axial_profile"]
    assert len(refined_profile) == int(refined_result["metadata"]["station_count"])
    assert all("mach" in row and "area_ratio" in row for row in refined_profile)
    assert float(refined_profile[0]["mach"]) < 1.0
    assert max(float(row["mach"]) for row in refined_profile) > 1.0
    assert float(refined_profile[-1]["pressure_kpa"]) < float(refined_profile[0]["pressure_kpa"])


def test_architecture_cooling_and_flow_modes_share_solved_geometry():
    """Assert feed, cooling, nozzle, and flow-mode branches stay mutually usable."""
    base_assumptions = get_default_solver_assumptions()

    for use_pumps in (False, True):
        for regen_cooling, film_cooling in ((False, False), (True, False), (False, True), (True, True)):
            design = create_engine_design(
                {
                    "use_pumps": use_pumps,
                    "regen_cooling": regen_cooling,
                    "film_cooling": film_cooling,
                }
            )
            values = design.derived.engineering_values
            assert values["nozzle_contour_method"] == "moc_characteristic_net"
            assert float(values["nozzle_throat_diameter_mm"]) < float(values["nozzle_inner_diameter_mm"])
            assert float(values["minimum_structural_margin_ratio"]) > 0.0

            for flow_model in ("fast", "refined", "navier_stokes"):
                result = run_combustion_cfd_solver(
                    design,
                    replace(base_assumptions, flow_model=flow_model),
                    station_count=12,
                )
                assert result["metadata"]["flow_model"] == flow_model
                assert result["status"] in {"ok", "warning", "converged", "max-iterations"}
                assert float(result["summary"]["predicted_thrust_newtons"]) > 0.0
                assert float(result["physics"]["nozzle"]["overall_efficiency"]) > 0.0
                assert result["heat_transfer"]["status"].startswith("calculated")
                assert "regime" in result["shock_analysis"]
                if flow_model == "navier_stokes":
                    assert result["navier_stokes"]["status"] == "calculated"


def run_all_tests():
    tests = [
        test_flow_modes_emit_distinct_metadata_and_outputs,
        test_architecture_cooling_and_flow_modes_share_solved_geometry,
    ]
    passed = 0
    failed = 0
    for test_func in tests:
        try:
            test_func()
            passed += 1
            print(f"[ok] {test_func.__name__} passed")
        except AssertionError as exc:
            failed += 1
            print(f"[fail] {test_func.__name__} failed: {exc}")
        except Exception as exc:
            failed += 1
            print(f"[error] {test_func.__name__} error: {exc}")
    print(f"\nFlow Mode Tests: {passed} passed, {failed} failed out of {len(tests)} total.")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all_tests() else 1)
