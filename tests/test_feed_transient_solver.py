"""Regression tests for the transient feed-system model."""

import sys
from dataclasses import asdict

sys.path.insert(0, r"E:/LIQUID_ENGINE")

from liquid_engine_studio.defaults import DEFAULT_STATE
from liquid_engine_studio.concept_model import create_concept_design
from liquid_engine_studio.exporter import build_cad_export_payload
from liquid_engine_studio.solver_interface import solve as solve_solver_interface


def test_pressure_fed_transient_history_has_blowdown_and_tailoff():
    state = asdict(DEFAULT_STATE)
    state["use_pumps"] = False

    solver_result = solve_solver_interface(state, upstream_context={"source": "test"})
    feed_result = solver_result["payload"]["feed_pressure_drop"]
    summary = feed_result["payload"]["summary"]
    rows = feed_result["payload"]["time_history_rows"]

    assert feed_result["metadata"]["solver_mode"] == "stage-2-transient-feed-v1"
    assert len(rows) == int(summary["history_step_count"])
    assert len(rows) >= 11
    assert abs(float(rows[0]["time_s"])) <= 1e-9
    assert abs(float(rows[-1]["time_s"]) - float(state["burn_time_seconds"])) < 1e-6
    assert all(float(rows[index]["time_s"]) <= float(rows[index + 1]["time_s"]) for index in range(len(rows) - 1))

    assert float(rows[-1]["fuel_tank_pressure_kpa"]) < float(rows[0]["fuel_tank_pressure_kpa"])
    assert float(rows[-1]["oxidizer_tank_pressure_kpa"]) < float(rows[0]["oxidizer_tank_pressure_kpa"])
    assert float(rows[-1]["chamber_pressure_kpa"]) <= float(rows[0]["chamber_pressure_kpa"])
    minimum_margin = min(
        min(float(row["fuel_margin_kpa"]), float(row["oxidizer_margin_kpa"])) for row in rows
    )
    assert abs(float(summary["minimum_feed_margin_kpa"]) - minimum_margin) < 0.01


def test_pump_fed_transient_history_tracks_pump_state():
    state = asdict(DEFAULT_STATE)
    state["use_pumps"] = True

    solver_result = solve_solver_interface(state, upstream_context={"source": "test"})
    feed_result = solver_result["payload"]["feed_pressure_drop"]
    summary = feed_result["payload"]["summary"]
    rows = feed_result["payload"]["time_history_rows"]

    assert len(rows) == int(summary["history_step_count"])
    assert max(float(row["pump_differential_pressure_kpa"]) for row in rows) > 0.0
    assert max(float(row["pump_speed_fraction"]) for row in rows) > 0.95
    assert float(summary["maximum_pump_speed_fraction"]) > 0.95
    assert float(rows[-1]["chamber_pressure_kpa"]) >= 0.9 * float(rows[0]["chamber_pressure_kpa"])


def test_export_payload_carries_feed_transient_history():
    state = asdict(DEFAULT_STATE)
    solver_result = solve_solver_interface(state, upstream_context={"source": "test"})
    design = create_concept_design(state)

    payload = build_cad_export_payload(
        design,
        objective_report={"total_score": 0.0},
        solver_interface_result=solver_result,
    )

    history = payload["solver"]["stage_2_feed_transient_history"]
    assert isinstance(history, list)
    assert len(history) == len(solver_result["payload"]["feed_pressure_drop"]["payload"]["time_history_rows"])
    assert payload["solver"]["stage_2_feed_pressure_drop"]["solver_mode"] == "stage-2-transient-feed-v1"


def run_all_tests():
    tests = [
        test_pressure_fed_transient_history_has_blowdown_and_tailoff,
        test_pump_fed_transient_history_tracks_pump_state,
        test_export_payload_carries_feed_transient_history,
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
    print(f"\nTransient Feed Tests: {passed} passed, {failed} failed out of {len(tests)} total.")
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all_tests() else 1)
