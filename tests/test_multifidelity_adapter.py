"""Tests for multi-fidelity screening and confirmation workflows."""

from stanshock.multifidelity_adapter import (
    ConceptSurrogateModel,
    MultiFidelityScreener,
)


def test_surrogate_model_evaluation():
    """Test that surrogate model can evaluate a design."""
    design_state = {
        "target_thrust_newtons": 500.0,
        "target_impulse_newton_seconds": 5000.0,
        "tank_diameter_mm": 125.0,
        "chamber_diameter_mm": 75.0,
        "nozzle_diameter_mm": 100.0,
        "burn_time_seconds": 15.0,
        "use_pumps": True,
        "regen_cooling": True,
    }

    result = ConceptSurrogateModel.predict(design_state)

    assert result.predicted_score is not None
    assert 0.0 <= result.predicted_score <= 1.0
    assert result.predicted_mass is not None
    assert 10.0 <= result.predicted_mass <= 100.0
    assert result.predicted_thermal_margin is not None
    assert 5.0 <= result.predicted_thermal_margin <= 95.0
    assert result.confidence is not None
    assert 0.0 <= result.confidence <= 1.0
    assert result.eval_time_ms >= 0.0

    print("✓ test_surrogate_model_evaluation passed")
    print(f"  - Score: {result.predicted_score:.3f}, Mass: {result.predicted_mass:.1f}, Thermal: {result.predicted_thermal_margin:.1f}")


def test_surrogate_respects_design_parameters():
    """Test that surrogate predictions vary with design inputs."""
    base_state = {
        "target_thrust_newtons": 250.0,
        "target_impulse_newton_seconds": 3000.0,
        "tank_diameter_mm": 110.0,
        "chamber_diameter_mm": 68.0,
        "nozzle_diameter_mm": 95.0,
        "burn_time_seconds": 12.0,
        "use_pumps": False,
        "regen_cooling": False,
    }

    # Baseline
    base_eval = ConceptSurrogateModel.predict(base_state)

    # High thrust (should affect mass and thermal)
    high_thrust_state = dict(base_state)
    high_thrust_state["target_thrust_newtons"] = 2000.0
    high_thrust_eval = ConceptSurrogateModel.predict(high_thrust_state)

    # High thrust should increase mass
    assert high_thrust_eval.predicted_mass > base_eval.predicted_mass

    # With regen should improve thermal margin
    regen_state = dict(base_state)
    regen_state["regen_cooling"] = True
    regen_eval = ConceptSurrogateModel.predict(regen_state)
    assert regen_eval.predicted_thermal_margin > base_eval.predicted_thermal_margin

    print("✓ test_surrogate_respects_design_parameters passed")


def test_multifidelity_screener_creation():
    """Test that MultiFidelityScreener can be instantiated."""
    screener = MultiFidelityScreener(
        surrogate_threshold=0.60,
        confirmation_ratio=0.15,
    )

    assert screener.surrogate_threshold == 0.60
    assert screener.confirmation_ratio == 0.15

    print("✓ test_multifidelity_screener_creation passed")


def test_multifidelity_screen_candidates():
    """Test candidate screening workflow."""
    candidates = [
        {"target_thrust_newtons": 100.0, "burn_time_seconds": 5.0},
        {"target_thrust_newtons": 500.0, "burn_time_seconds": 10.0},
        {"target_thrust_newtons": 1000.0, "burn_time_seconds": 20.0},
        {"target_thrust_newtons": 250.0, "burn_time_seconds": 12.0},
        {"target_thrust_newtons": 750.0, "burn_time_seconds": 15.0},
    ]

    screener = MultiFidelityScreener(
        surrogate_threshold=0.50,
        confirmation_ratio=0.30,  # Top 30%
    )

    promising, unpromising = screener.screen_candidates(candidates)

    # Should have some promising and some unpromising
    assert len(promising) > 0
    assert len(promising) + len(unpromising) <= len(candidates)

    # Promising fraction should be roughly confirmation_ratio
    expected_promising = max(1, int(len(candidates) * screener.confirmation_ratio))
    assert len(promising) <= expected_promising + 1  # Allow small margin

    print("✓ test_multifidelity_screen_candidates passed")
    print(f"  - Total: {len(candidates)}, Promising: {len(promising)}, Unpromising: {len(unpromising)}")


def test_screening_summary():
    """Test that screening summary is generated correctly."""
    screener = MultiFidelityScreener()

    summary = screener.build_screening_summary(
        total_candidates=100,
        screened_promising=15,
        confirmed_count=10,
        total_time_ms=500.0,
    )

    assert summary["total_candidates"] == 100
    assert summary["screened_promising"] == 15
    assert summary["confirmed_count"] == 10
    assert summary["efficiency_factor"] > 0.0
    assert summary["time_saved_percent"] > 0.0

    print("✓ test_screening_summary passed")
    print(f"  - Efficiency: {summary['efficiency_factor']:.2f}×, Time saved: {summary['time_saved_percent']:.1f}%")


def test_surrogate_evaluation_robustness():
    """Test that surrogate handles missing/invalid inputs gracefully."""
    # Empty state
    empty_eval = ConceptSurrogateModel.predict({})
    assert empty_eval.predicted_score is not None
    assert 0.0 <= empty_eval.predicted_score <= 1.0

    # Negative thrust
    bad_state = {"target_thrust_newtons": -100.0}
    bad_eval = ConceptSurrogateModel.predict(bad_state)
    assert bad_eval.predicted_score is not None

    # Very large values
    huge_state = {"target_thrust_newtons": 1e6, "tank_diameter_mm": 1e6}
    huge_eval = ConceptSurrogateModel.predict(huge_state)
    assert huge_eval.predicted_score is not None
    assert 0.0 <= huge_eval.predicted_score <= 1.0

    print("✓ test_surrogate_evaluation_robustness passed")


def run_all_tests():
    """Run all Stage 4.3 multi-fidelity tests."""
    tests = [
        test_surrogate_model_evaluation,
        test_surrogate_respects_design_parameters,
        test_multifidelity_screener_creation,
        test_multifidelity_screen_candidates,
        test_screening_summary,
        test_surrogate_evaluation_robustness,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test_func.__name__} failed: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test_func.__name__} error: {e}")
            failed += 1

    print(f"\nStage 4.3 Tests: {passed} passed, {failed} failed out of {len(tests)} total.")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)

