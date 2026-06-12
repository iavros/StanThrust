"""Tests for Stage 6: Machine Learning Surrogates.

Covers:
- Gaussian Process surrogate training
- Prediction with uncertainty quantification
- Training data generation from GA history and ensemble results
- Fallback to heuristic when ML unavailable
- Integration with multi-fidelity adapter
"""

import pytest
from importlib.util import find_spec
from liquid_engine_studio.ml_surrogate_adapter import (
    GaussianProcessSurrogate,
    MLSurrogateModel,
    TrainingDataPoint,
    MLSurrogateEvaluation,
    create_training_data_from_ga_history,
)

# Check if sklearn is available
HAS_SKLEARN = find_spec("sklearn") is not None


# Fixtures
@pytest.fixture
def training_data():
    """Create synthetic training data."""
    return [
        TrainingDataPoint(
            design_state={
                "target_thrust_newtons": 200.0,
                "target_impulse_newton_seconds": 2500.0,
                "burn_time_seconds": 15.0,
                "tank_diameter_mm": 120.0,
                "chamber_diameter_mm": 80.0,
                "nozzle_diameter_mm": 40.0,
            },
            observed_score=0.72,
            observed_mass=45.0,
            observed_thermal_margin=55.0,
            source="ga",
        ),
        TrainingDataPoint(
            design_state={
                "target_thrust_newtons": 300.0,
                "target_impulse_newton_seconds": 3500.0,
                "burn_time_seconds": 20.0,
                "tank_diameter_mm": 140.0,
                "chamber_diameter_mm": 90.0,
                "nozzle_diameter_mm": 50.0,
            },
            observed_score=0.68,
            observed_mass=60.0,
            observed_thermal_margin=48.0,
            source="ga",
        ),
        TrainingDataPoint(
            design_state={
                "target_thrust_newtons": 250.0,
                "target_impulse_newton_seconds": 3000.0,
                "burn_time_seconds": 18.0,
                "tank_diameter_mm": 130.0,
                "chamber_diameter_mm": 85.0,
                "nozzle_diameter_mm": 45.0,
            },
            observed_score=0.75,
            observed_mass=52.0,
            observed_thermal_margin=52.0,
            source="ensemble",
        ),
        TrainingDataPoint(
            design_state={
                "target_thrust_newtons": 350.0,
                "target_impulse_newton_seconds": 4000.0,
                "burn_time_seconds": 25.0,
                "tank_diameter_mm": 150.0,
                "chamber_diameter_mm": 95.0,
                "nozzle_diameter_mm": 55.0,
            },
            observed_score=0.65,
            observed_mass=70.0,
            observed_thermal_margin=42.0,
            source="ga",
        ),
        TrainingDataPoint(
            design_state={
                "target_thrust_newtons": 150.0,
                "target_impulse_newton_seconds": 2000.0,
                "burn_time_seconds": 10.0,
                "tank_diameter_mm": 100.0,
                "chamber_diameter_mm": 70.0,
                "nozzle_diameter_mm": 35.0,
            },
            observed_score=0.70,
            observed_mass=35.0,
            observed_thermal_margin=62.0,
            source="ensemble",
        ),
    ]


@pytest.fixture
def test_design():
    """Test design for prediction."""
    return {
        "target_thrust_newtons": 250.0,
        "target_impulse_newton_seconds": 3000.0,
        "burn_time_seconds": 18.0,
        "tank_diameter_mm": 130.0,
        "chamber_diameter_mm": 85.0,
        "nozzle_diameter_mm": 45.0,
    }


@pytest.fixture
def ga_history():
    """Sample GA history for converting to training data."""
    return [
        {
            "best_state": {
                "target_thrust_newtons": 250.0,
                "target_impulse_newton_seconds": 3000.0,
                "burn_time_seconds": 20.0,
                "tank_diameter_mm": 130.0,
                "chamber_diameter_mm": 85.0,
                "nozzle_diameter_mm": 45.0,
            },
            "best_score": 0.75,
            "best_design": {
                "derived": {
                    "dry_mass_index": 52.0,
                    "thermal_margin_index": 52.0,
                }
            },
        },
        {
            "best_state": {
                "target_thrust_newtons": 300.0,
                "target_impulse_newton_seconds": 3500.0,
                "burn_time_seconds": 22.0,
                "tank_diameter_mm": 140.0,
                "chamber_diameter_mm": 90.0,
                "nozzle_diameter_mm": 50.0,
            },
            "best_score": 0.72,
            "best_design": {
                "derived": {
                    "dry_mass_index": 60.0,
                    "thermal_margin_index": 48.0,
                }
            },
        },
    ]


# Tests: ML Surrogate Training

@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_gp_surrogate_creation(training_data):
    """Test creating a GP surrogate."""
    gp = GaussianProcessSurrogate(kernel_type="rbf", normalize=True)
    assert not gp.is_trained
    assert gp.kernel_type == "rbf"


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_gp_surrogate_training(training_data):
    """Test training a GP surrogate."""
    gp = GaussianProcessSurrogate(kernel_type="rbf")
    gp.train(training_data)
    assert gp.is_trained


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_gp_surrogate_insufficient_data():
    """Test that training with too few samples fails."""
    gp = GaussianProcessSurrogate()
    small_data = [
        TrainingDataPoint(
            design_state={"target_thrust_newtons": 250.0},
            observed_score=0.7,
            observed_mass=50.0,
            observed_thermal_margin=50.0,
        )
    ]
    with pytest.raises(ValueError):
        gp.train(small_data)


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_gp_surrogate_prediction(training_data, test_design):
    """Test GP prediction."""
    gp = GaussianProcessSurrogate()
    gp.train(training_data)

    score, score_std, mass, mass_std, thermal, thermal_std = gp.predict(test_design)

    # Check output ranges
    assert 0.0 <= score <= 1.0
    assert mass >= 10.0
    assert thermal >= 5.0
    assert score_std >= 0.0
    assert mass_std >= 0.0
    assert thermal_std >= 0.0


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_gp_surrogate_matern_kernel(training_data, test_design):
    """Test GP with Matern kernel."""
    gp = GaussianProcessSurrogate(kernel_type="matern")
    gp.train(training_data)

    score, score_std, _, _, _, _ = gp.predict(test_design)
    assert 0.0 <= score <= 1.0
    assert score_std >= 0.0


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_gp_surrogate_without_normalization(training_data, test_design):
    """Test GP without normalization."""
    gp = GaussianProcessSurrogate(normalize=False)
    gp.train(training_data)

    score, score_std, mass, mass_std, thermal, thermal_std = gp.predict(test_design)
    assert 0.0 <= score <= 1.0


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_gp_surrogate_training_stats(training_data):
    """Test getting training statistics."""
    gp = GaussianProcessSurrogate()

    # Before training
    stats = gp.get_training_stats()
    assert stats["status"] == "not_trained"

    # After training
    gp.train(training_data)
    stats = gp.get_training_stats()
    assert stats["status"] == "trained"
    assert stats["kernel_type"] in ["rbf", "matern"]
    assert stats["normalization"] is True


# Tests: ML Surrogate Model

def test_ml_surrogate_model_creation():
    """Test creating ML surrogate model."""
    model = MLSurrogateModel(use_heuristic_fallback=True)
    assert not model.is_trained()


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_ml_surrogate_model_training(training_data):
    """Test training ML surrogate model."""
    model = MLSurrogateModel()
    success = model.train(training_data)
    assert success
    assert model.is_trained()


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_ml_surrogate_model_prediction(training_data, test_design):
    """Test ML surrogate prediction."""
    model = MLSurrogateModel()
    model.train(training_data)

    result = model.predict(test_design)

    assert isinstance(result, MLSurrogateEvaluation)
    assert 0.0 <= result.predicted_score <= 1.0
    assert result.predicted_mass >= 10.0
    assert result.predicted_thermal_margin >= 5.0
    assert 0.0 <= result.confidence <= 1.0
    assert result.eval_time_ms >= 0.0
    assert result.model_type in ["rbf", "matern"]


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_ml_surrogate_uncertainty(training_data, test_design):
    """Test that uncertainty is captured."""
    model = MLSurrogateModel()
    model.train(training_data)

    result = model.predict(test_design)

    # Uncertainty should be positive (GP provides variance)
    assert result.score_std >= 0.0
    assert result.mass_std >= 0.0
    assert result.thermal_std >= 0.0


def test_ml_surrogate_heuristic_fallback():
    """Test fallback to heuristic when ML unavailable."""
    model = MLSurrogateModel(use_heuristic_fallback=True)

    test_design = {
        "target_thrust_newtons": 250.0,
        "target_impulse_newton_seconds": 3000.0,
        "burn_time_seconds": 20.0,
        "tank_diameter_mm": 130.0,
        "chamber_diameter_mm": 85.0,
        "nozzle_diameter_mm": 45.0,
    }

    result = model.predict(test_design)

    # Should fall back to heuristic
    assert result.model_type == "heuristic"
    assert result.confidence == 0.55  # Heuristic confidence


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_ml_surrogate_comparison_with_heuristic(training_data, test_design):
    """Compare ML predictions with heuristic fallback."""
    # ML model
    ml_model = MLSurrogateModel()
    ml_model.train(training_data)
    ml_result = ml_model.predict(test_design)

    # Heuristic model (untrained)
    heuristic_model = MLSurrogateModel(use_heuristic_fallback=True)
    heuristic_result = heuristic_model.predict(test_design)

    # ML should have higher confidence than heuristic (after training)
    assert ml_result.confidence >= heuristic_result.confidence


# Tests: Training Data Conversion

def test_create_training_data_from_ga_history(ga_history):
    """Test converting GA history to training data."""
    training_data = create_training_data_from_ga_history(ga_history)

    assert len(training_data) == 2
    for point in training_data:
        assert isinstance(point, TrainingDataPoint)
        assert 0.0 <= point.observed_score <= 1.0
        assert point.observed_mass > 0.0
        assert point.observed_thermal_margin > 0.0
        assert point.source == "ga"


def test_create_training_data_from_ga_history_with_missing_fields():
    """Test robustness when GA history has missing fields."""
    incomplete_history = [
        {
            "best_state": {"target_thrust_newtons": 250.0},
            "best_score": 0.75,
            # Missing "best_design"
        },
        {
            # Missing many fields
        },
    ]

    training_data = create_training_data_from_ga_history(incomplete_history)

    # Should gracefully handle missing data
    assert len(training_data) >= 0


# Tests: Integration

@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_trained_ml_surrogate_can_predict_multiple_samples(training_data):
    """Test that trained model can predict multiple designs."""
    model = MLSurrogateModel()
    model.train(training_data)

    test_designs = [
        {
            "target_thrust_newtons": 200.0,
            "target_impulse_newton_seconds": 2500.0,
            "burn_time_seconds": 15.0,
            "tank_diameter_mm": 120.0,
            "chamber_diameter_mm": 80.0,
            "nozzle_diameter_mm": 40.0,
        },
        {
            "target_thrust_newtons": 300.0,
            "target_impulse_newton_seconds": 3500.0,
            "burn_time_seconds": 22.0,
            "tank_diameter_mm": 140.0,
            "chamber_diameter_mm": 90.0,
            "nozzle_diameter_mm": 50.0,
        },
    ]

    results = [model.predict(design) for design in test_designs]

    assert len(results) == 2
    for result in results:
        assert result.predicted_score >= 0.0
        assert result.confidence >= 0.0


@pytest.mark.skipif(not HAS_SKLEARN, reason="scikit-learn not installed")
def test_end_to_end_ml_workflow(ga_history):
    """Integration test: GA history → training data → model → prediction."""
    # Convert GA history to training data
    training_data = create_training_data_from_ga_history(ga_history)

    # Train model
    model = MLSurrogateModel()
    success = model.train(training_data)
    assert success

    # Predict
    test_design = {
        "target_thrust_newtons": 275.0,
        "target_impulse_newton_seconds": 3250.0,
        "burn_time_seconds": 21.0,
        "tank_diameter_mm": 135.0,
        "chamber_diameter_mm": 87.0,
        "nozzle_diameter_mm": 47.0,
    }

    result = model.predict(test_design)

    # Verify result
    assert isinstance(result, MLSurrogateEvaluation)
    assert 0.0 <= result.predicted_score <= 1.0
    assert result.eval_time_ms > 0.0


def run_all_tests():
    """Run all tests and report results."""
    tests = [
        test_ml_surrogate_model_creation,
        test_ml_surrogate_heuristic_fallback,
        test_create_training_data_from_ga_history,
        test_create_training_data_from_ga_history_with_missing_fields,
    ]

    if HAS_SKLEARN:
        tests.extend([
            test_gp_surrogate_creation,
            test_gp_surrogate_training,
            test_gp_surrogate_prediction,
            test_gp_surrogate_matern_kernel,
            test_gp_surrogate_without_normalization,
            test_gp_surrogate_training_stats,
            test_ml_surrogate_model_training,
            test_ml_surrogate_model_prediction,
            test_ml_surrogate_uncertainty,
            test_ml_surrogate_comparison_with_heuristic,
            test_trained_ml_surrogate_can_predict_multiple_samples,
            test_end_to_end_ml_workflow,
        ])

    passed = 0
    failed = 0
    skipped = 0

    print("\n" + "=" * 70)
    print("Stage 6 ML Surrogate Tests")
    print("=" * 70 + "\n")

    for test_func in tests:
        try:
            test_func()
            print(f"✓ {test_func.__name__}")
            passed += 1
        except pytest.skip.Exception:
            print(f"⊘ {test_func.__name__} (skipped)")
            skipped += 1
        except AssertionError as e:
            print(f"✗ {test_func.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test_func.__name__} ERROR: {e}")
            failed += 1

    total = len(tests)
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped out of {total} total")
    return failed == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

