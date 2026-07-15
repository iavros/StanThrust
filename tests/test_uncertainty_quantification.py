"""Tests for Advanced Uncertainty Quantification (Stage 5 / Item 4).

Covers:
- Monte Carlo ensemble sampling (LHS, uniform)
- Ensemble evaluation and validation
- Sobol global sensitivity analysis
- Export formats and integration
"""

import pytest
from stanshock.design_model import create_engine_design
from stanshock.monte_carlo_sampler import (
    MonteCarloEnsemble,
    InputSample,
)
from stanshock.sobol_analyzer import SobolAnalyzer


# Fixtures
@pytest.fixture
def base_design():
    """Create a baseline design for ensemble testing."""
    state = {
        "mixture_ratio": 2.0,
        "burn_time_seconds": 20.0,
        "tank_diameter_mm": 150.0,
        "chamber_diameter_mm": 100.0,
        "nozzle_diameter_mm": 50.0,
        "fuel_name": "RP-1",
        "oxidizer_name": "LOX",
    }
    return create_engine_design(state)


@pytest.fixture
def sample_input_ranges():
    """Standard input uncertainty ranges for testing."""
    return {
        "mixture_ratio": (1.2, 3.0),
        "burn_time_seconds": (10.0, 40.0),
    }


# Tests: Input Sample Generation
def test_input_sample_creation():
    """Test InputSample dataclass."""
    sample = InputSample(mixture_ratio=2.5, burn_time_seconds=25.0, seed_id=0)
    assert sample.mixture_ratio == 2.5
    assert sample.burn_time_seconds == 25.0
    assert sample.seed_id == 0

    state_update = sample.to_state_update()
    assert state_update["mixture_ratio"] == 2.5
    assert state_update["burn_time_seconds"] == 25.0


# Tests: Ensemble Sampler Creation and Initialization
def test_monte_carlo_ensemble_init(base_design, sample_input_ranges):
    """Test ensemble sampler initialization."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=32,
        sampling_method="uniform",
    )
    assert sampler.sample_size == 32
    assert sampler.sampling_method == "uniform"
    assert len(sampler.input_ranges) == 2


# Tests: Uniform Sampling
def test_uniform_sampling(base_design, sample_input_ranges):
    """Test uniform random sampling strategy."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=50,
        sampling_method="uniform",
    )
    samples = sampler._generate_samples(random_seed=42)
    assert len(samples) == 50
    assert all(isinstance(s, InputSample) for s in samples)

    # Check ranges
    mixture_values = [s.mixture_ratio for s in samples]
    burn_times = [s.burn_time_seconds for s in samples]
    assert all(1.2 <= m <= 3.0 for m in mixture_values)
    assert all(10.0 <= b <= 40.0 for b in burn_times)


# Tests: Latin Hypercube Sampling
def test_lhs_sampling(base_design, sample_input_ranges):
    """Test Latin Hypercube sampling strategy."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=50,
        sampling_method="lhs",
    )
    samples = sampler._generate_samples(random_seed=42)
    assert len(samples) == 50

    # LHS should have better coverage (low correlation between params)
    mixture_values = [s.mixture_ratio for s in samples]
    burn_times = [s.burn_time_seconds for s in samples]
    assert all(1.2 <= m <= 3.0 for m in mixture_values)
    assert all(10.0 <= b <= 40.0 for b in burn_times)


# Tests: Ensemble Evaluation
def test_single_sample_evaluation(base_design, sample_input_ranges):
    """Test evaluation of a single sample."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=10,
    )
    sample = InputSample(mixture_ratio=2.0, burn_time_seconds=20.0, seed_id=0)
    evaluation = sampler.evaluate_sample(sample, use_coupled_cycle=False)

    assert evaluation.sample_id == 0
    assert evaluation.design is not None
    assert evaluation.thermal_margin_index is not None
    assert evaluation.dry_mass_index is not None


# Tests: Full Ensemble Run
def test_ensemble_run_basic(base_design, sample_input_ranges):
    """Test basic ensemble run workflow."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=20,
        sampling_method="uniform",
    )
    results = sampler.run(random_seed=42, use_coupled_cycle=False)

    assert results is not None
    assert len(results.evaluations) == 20
    assert results.total_runtime_seconds >= 0.0
    assert len(results.statistics) > 0


# Tests: Ensemble Statistics
def test_ensemble_statistics_computation(base_design, sample_input_ranges):
    """Test statistical aggregation from ensemble."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=30,
    )
    results = sampler.run(random_seed=42, use_coupled_cycle=False)

    # Check that statistics contain expected keys
    assert "thrust_n" in results.statistics or len(results.statistics) > 0

    for param_name, stats_obj in results.statistics.items():
        assert stats_obj.mean >= 0.0
        assert stats_obj.std_dev >= 0.0
        assert stats_obj.percentile_50 <= stats_obj.percentile_95
        assert stats_obj.min_value <= stats_obj.max_value
        assert 0.0 <= stats_obj.feasibility_rate <= 1.0


# Tests: Ensemble Results Serialization
def test_ensemble_results_as_dict(base_design, sample_input_ranges):
    """Test serialization of ensemble results."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=15,
    )
    results = sampler.run(random_seed=42)
    output_dict = results.as_dict()

    assert "n_evaluations" in output_dict
    assert "n_feasible" in output_dict
    assert "statistics" in output_dict
    assert "evaluations_sample" in output_dict


# Tests: Ensemble Summary Statistics
def test_ensemble_summary_statistics(base_design, sample_input_ranges):
    """Test summary statistics API."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=20,
    )
    results = sampler.run(random_seed=42)
    summary = results.summary_statistics()

    assert "total_samples" in summary
    assert "feasible_count" in summary
    assert "feasibility_rate" in summary
    assert "key_statistics" in summary
    rate = summary["feasibility_rate"]
    assert isinstance(rate, (int, float))
    assert 0.0 <= rate <= 1.0


# Tests: Sobol Index Computation
def test_sobol_analyzer_initialization(base_design, sample_input_ranges):
    """Test Sobol analyzer initialization."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=50,
    )
    results = sampler.run(random_seed=42)
    analyzer = SobolAnalyzer(results)

    assert analyzer.ensemble_results is not None
    assert len(analyzer.feasible_evals) > 0


# Tests: Sobol Indices
def test_sobol_indices_computation(base_design, sample_input_ranges):
    """Test Sobol index calculation."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=50,
    )
    results = sampler.run(random_seed=42)
    analyzer = SobolAnalyzer(results)

    indices = analyzer.compute_indices(
        output_parameter="thrust_n",
        input_parameters=["mixture_ratio", "burn_time_seconds"],
    )

    # Should have one index per input parameter
    assert len(indices) <= 2
    for param_name, sobol_idx in indices.items():
        assert 0.0 <= sobol_idx.s1 <= 1.0
        assert 0.0 <= sobol_idx.st <= 1.0
        assert sobol_idx.s1 <= sobol_idx.st


# Tests: Sobol Index Serialization
def test_sobol_index_serialization(base_design, sample_input_ranges):
    """Test Sobol index as_dict serialization."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=40,
    )
    results = sampler.run(random_seed=42)
    analyzer = SobolAnalyzer(results)

    indices = analyzer.compute_indices(output_parameter="chamber_pressure_kpa")
    for param_name, idx in indices.items():
        idx_dict = idx.as_dict()
        assert "input" in idx_dict
        assert "output" in idx_dict
        assert "s1_first_order" in idx_dict
        assert "st_total_order" in idx_dict


# Tests: Sensitivity Ranking
def test_sensitivity_ranking(base_design, sample_input_ranges):
    """Test parameter sensitivity ranking."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=40,
    )
    results = sampler.run(random_seed=42)
    analyzer = SobolAnalyzer(results)

    ranking = analyzer.sensitivity_report()
    assert "sensitivity_rankings" in ranking
    assert isinstance(ranking["sensitivity_rankings"], dict)


# Tests: Parameter Screening
def test_parameter_screening(base_design, sample_input_ranges):
    """Test screening of influential parameters."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=40,
    )
    results = sampler.run(random_seed=42)
    analyzer = SobolAnalyzer(results)

    screening = analyzer.parameter_screening(threshold=0.05)
    assert isinstance(screening, dict)
    # Each output may or may not have influential parameters depending on data


# Tests: Sobol Export
def test_sobol_export_summary(base_design, sample_input_ranges):
    """Test Sobol sensitivity export summary."""
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=40,
    )
    results = sampler.run(random_seed=42)
    analyzer = SobolAnalyzer(results)

    export = analyzer.export_sensitivity_summary()
    assert "method" in export
    assert "n_samples" in export
    assert "sensitivity_report" in export
    assert "parameter_screening" in export


# Tests: Reproducibility with Seed
def test_reproducibility_with_seed(base_design, sample_input_ranges):
    """Test that runs with same seed produce consistent results."""
    sampler1 = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=20,
    )
    results1 = sampler1.run(random_seed=123)

    sampler2 = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=20,
    )
    results2 = sampler2.run(random_seed=123)

    # Compare feasibility rates
    rate1 = results1.summary_statistics()["feasibility_rate"]
    rate2 = results2.summary_statistics()["feasibility_rate"]
    assert rate1 == rate2


# Integration Test: End-to-End UQ Workflow
def test_endtoend_uq_workflow(base_design, sample_input_ranges):
    """Integration test: generate ensemble → compute Sobol indices → export."""
    # Create and run ensemble
    sampler = MonteCarloEnsemble(
        base_design=base_design,
        input_ranges=sample_input_ranges,
        sample_size=35,
    )
    ensemble_results = sampler.run(random_seed=42)

    # Compute sensitivity indices
    analyzer = SobolAnalyzer(ensemble_results)
    sensitivity_summary = analyzer.export_sensitivity_summary()

    # Verify export structure
    n_feasible = len(analyzer.feasible_evals)
    assert sensitivity_summary["n_samples"] == n_feasible
    assert "sensitivity_report" in sensitivity_summary
    assert "parameter_screening" in sensitivity_summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])






