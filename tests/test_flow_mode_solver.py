"""Regression tests for fast and refined combustion flow modes."""

import sys
from dataclasses import replace

sys.path.insert(0, r"E:/LIQUID_ENGINE")

from liquid_engine_studio.combustion_cfd_solver import run_combustion_cfd_proxy
from liquid_engine_studio.concept_model import create_concept_design
from liquid_engine_studio.solver_assumptions import get_default_solver_assumptions


def test_flow_modes_emit_distinct_metadata_and_outputs():
    """Assert the flow-mode switch changes metadata and produces the refined nozzle fields."""
    design = create_concept_design({})
    base_assumptions = get_default_solver_assumptions()

    try:
        fast_result = run_combustion_cfd_proxy(
            design,
            replace(base_assumptions, flow_model="fast"),
            station_count=18,
        )
        refined_result = run_combustion_cfd_proxy(
            design,
            replace(base_assumptions, flow_model="refined"),
            station_count=18,
        )
    except RuntimeError as exc:
        if "Cantera thermochemistry provider" in str(exc):
            print("[skip] test_flow_modes_emit_distinct_metadata_and_outputs skipped: Cantera unavailable")
            return
        raise

    assert fast_result["metadata"]["flow_model"] == "fast"
    assert refined_result["metadata"]["flow_model"] == "refined"
    assert fast_result["metadata"]["solver_mode"] == "quasi-1d-fast"
    assert refined_result["metadata"]["solver_mode"] == "quasi-1d-refined"

    refined_nozzle = refined_result["physics"]["nozzle"]
    assert refined_nozzle["flow_model_label"] == "Refined quasi-1D solve"
    assert float(refined_nozzle["separation_efficiency"]) <= 1.0
    assert "curvature_efficiency" in refined_nozzle
    assert "bell_quality" in refined_nozzle
    assert float(refined_nozzle["exit_pressure_kpa"]) > 0.0

    fast_summary = fast_result["summary"]
    refined_summary = refined_result["summary"]
    assert fast_summary["flow_model"] == "fast"
    assert refined_summary["flow_model"] == "refined"
    assert abs(float(fast_summary["predicted_thrust_newtons"]) - float(refined_summary["predicted_thrust_newtons"])) > 0.1
    assert float(refined_summary["exit_pressure_kpa"]) > 0.0
    assert float(refined_summary["predicted_isp_seconds"]) > 120.0
    assert float(refined_summary["chamber_pressure_kpa"]) > float(fast_summary["chamber_pressure_kpa"])


def test_architecture_cooling_and_flow_modes_share_solved_geometry():
    """Assert feed, cooling, nozzle, and flow-mode branches stay mutually usable."""
    base_assumptions = get_default_solver_assumptions()

    for use_pumps in (False, True):
        for regen_cooling, film_cooling in ((False, False), (True, False), (False, True), (True, True)):
            design = create_concept_design(
                {
                    "use_pumps": use_pumps,
                    "regen_cooling": regen_cooling,
                    "film_cooling": film_cooling,
                }
            )
            values = design.derived.engineering_values
            assert values["nozzle_contour_method"] == "moc_bell"
            assert float(values["nozzle_throat_diameter_mm"]) < float(values["nozzle_inner_diameter_mm"])
            assert float(values["minimum_structural_margin_ratio"]) > 0.0

            for flow_model in ("fast", "refined"):
                try:
                    result = run_combustion_cfd_proxy(
                        design,
                        replace(base_assumptions, flow_model=flow_model),
                        station_count=12,
                    )
                except RuntimeError as exc:
                    if "Cantera thermochemistry provider" in str(exc):
                        print("[skip] test_architecture_cooling_and_flow_modes_share_solved_geometry skipped: Cantera unavailable")
                        return
                    raise
                assert result["metadata"]["flow_model"] == flow_model
                assert result["status"] in {"ok", "warning"}
                assert float(result["summary"]["predicted_thrust_newtons"]) > 0.0
                assert float(result["physics"]["nozzle"]["overall_efficiency"]) > 0.0


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
