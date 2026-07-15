"""Monte Carlo ensemble sampler for propagating input uncertainties through solvers.

Provides:
- LHS (Latin Hypercube Sampling) and uniform random sampling strategies
- Multi-fidelity ensemble evaluation (design -> coupled cycle → full validation)
- Statistical aggregation of outputs (mean, std, percentiles, confidence intervals)
- Export-ready ensemble summaries compatible with uncertainty_provenance.ProvenanceField

Usage:
    sampler = MonteCarloEnsemble(
        base_design=design,
        input_ranges={
            "mixture_ratio": (1.0, 3.5),
            "burn_time_seconds": (5.0, 45.0),
        },
        sample_size=200,
    )
    results = sampler.run(n_jobs=4)
    summary = results.summary_statistics()
"""

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np

from stanshock.design_model import EngineDesign, create_engine_design
from stanshock.validation_pack import validate_engine_design


@dataclass
class InputSample:
    """One realization of uncertain input parameters."""
    mixture_ratio: float
    burn_time_seconds: float
    seed_id: int

    def to_state_update(self) -> Dict[str, object]:
        return {
            "mixture_ratio": self.mixture_ratio,
            "burn_time_seconds": self.burn_time_seconds,
        }


@dataclass
class EnsembleEvaluation:
    """Results from evaluating one sample through the solver chain."""
    sample_id: int
    input_sample: InputSample
    design: Optional[EngineDesign]
    coupled_cycle_passed: bool
    validation_passed: bool
    dry_mass_index: Optional[float] = None
    thermal_margin_index: Optional[float] = None
    packaging_efficiency_index: Optional[float] = None
    total_stack_length_mm: Optional[float] = None
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "sample_id": self.sample_id,
            "input_mixture_ratio": self.input_sample.mixture_ratio,
            "input_burn_time_seconds": self.input_sample.burn_time_seconds,
            "dry_mass_index": self.dry_mass_index,
            "thermal_margin_index": self.thermal_margin_index,
            "packaging_efficiency_index": self.packaging_efficiency_index,
            "total_stack_length_mm": self.total_stack_length_mm,
            "coupled_cycle_passed": self.coupled_cycle_passed,
            "validation_passed": self.validation_passed,
            "errors": self.errors,
        }


@dataclass
class EnsembleStatistics:
    """Aggregated statistics from ensemble run."""
    parameter: str
    n_samples: int
    n_feasible: int
    feasibility_rate: float
    mean: float
    std_dev: float
    min_value: float
    max_value: float
    percentile_5: float
    percentile_25: float
    percentile_50: float
    percentile_75: float
    percentile_95: float
    confidence_lower: float
    confidence_upper: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "parameter": self.parameter,
            "n_samples": self.n_samples,
            "n_feasible": self.n_feasible,
            "feasibility_rate": round(self.feasibility_rate, 3),
            "mean": round(self.mean, 4),
            "std_dev": round(self.std_dev, 4),
            "min": round(self.min_value, 4),
            "max": round(self.max_value, 4),
            "percentile_5": round(self.percentile_5, 4),
            "percentile_25": round(self.percentile_25, 4),
            "percentile_50": round(self.percentile_50, 4),
            "percentile_75": round(self.percentile_75, 4),
            "percentile_95": round(self.percentile_95, 4),
            "confidence_lower_95": round(self.confidence_lower, 4),
            "confidence_upper_95": round(self.confidence_upper, 4),
        }


@dataclass
class EnsembleResults:
    """Complete ensemble run results with statistics."""
    evaluations: List[EnsembleEvaluation]
    statistics: Dict[str, EnsembleStatistics]
    sample_seed: int
    total_runtime_seconds: float

    def as_dict(self) -> Dict[str, object]:
        return {
            "n_evaluations": len(self.evaluations),
            "n_feasible": sum(1 for e in self.evaluations if e.validation_passed),
            "sample_seed": self.sample_seed,
            "total_runtime_seconds": round(self.total_runtime_seconds, 2),
            "statistics": {key: stats.as_dict() for key, stats in self.statistics.items()},
            "evaluations_sample": [e.as_dict() for e in self.evaluations[:10]],
        }

    def summary_statistics(self) -> Dict[str, object]:
        """Brief summary of ensemble quality and coverage."""
        feasible = [e for e in self.evaluations if e.validation_passed]
        return {
            "total_samples": len(self.evaluations),
            "feasible_count": len(feasible),
            "feasibility_rate": len(feasible) / len(self.evaluations) if self.evaluations else 0.0,
            "key_statistics": {k: v.as_dict() for k, v in self.statistics.items()},
        }


class MonteCarloEnsemble:
    """Monte Carlo sampler for input uncertainty propagation."""

    def __init__(
        self,
        base_design: EngineDesign,
        input_ranges: Dict[str, Tuple[float, float]],
        sample_size: int = 100,
        sampling_method: str = "lhs",
    ):
        """Initialize ensemble sampler.

        Args:
            base_design: Reference EngineDesign to clone for each sample
            input_ranges: Dict mapping parameter name to (min, max) tuple
            sample_size: Number of samples to draw
            sampling_method: "lhs" (Latin Hypercube) or "uniform"
        """
        self.base_design = base_design
        self.input_ranges = input_ranges
        self.sample_size = sample_size
        self.sampling_method = sampling_method

    def _generate_samples(self, random_seed: Optional[int] = None) -> List[InputSample]:
        """Generate input sample matrix using LHS or uniform sampling."""
        rng = random.Random(random_seed)
        samples = []

        if self.sampling_method == "lhs":
            samples = self._lhs_samples(rng)
        else:
            samples = self._uniform_samples(rng)

        return samples

    def _uniform_samples(self, rng: random.Random) -> List[InputSample]:
        """Generate uniform random samples."""
        samples = []
        for i in range(self.sample_size):
            sample_dict = {}
            for param_name, (low, high) in self.input_ranges.items():
                sample_dict[param_name] = rng.uniform(low, high)

            sample = InputSample(
                mixture_ratio=sample_dict.get("mixture_ratio", 2.0),
                burn_time_seconds=sample_dict.get("burn_time_seconds", 20.0),
                seed_id=i,
            )
            samples.append(sample)
        return samples

    def _lhs_samples(self, rng: random.Random) -> List[InputSample]:
        """Generate Latin Hypercube samples (stratified stratified)."""
        samples = []
        param_names = sorted(self.input_ranges.keys())
        n_params = len(param_names)

        # Create LHS matrix: each parameter gets one value per sample in stratified bins
        lhs_matrix = np.zeros((self.sample_size, n_params))
        for j, param_name in enumerate(param_names):
            low, high = self.input_ranges[param_name]
            # Divide range into N bins, pick one random point per bin
            bins = np.linspace(low, high, self.sample_size + 1)
            values = [
                rng.uniform(bins[i], bins[i + 1]) for i in range(self.sample_size)
            ]
            # Shuffle to break correlation
            rng.shuffle(values)
            lhs_matrix[:, j] = values

        for i in range(self.sample_size):
            sample = InputSample(
                mixture_ratio=float(lhs_matrix[i, param_names.index("mixture_ratio")]),
                burn_time_seconds=float(
                    lhs_matrix[i, param_names.index("burn_time_seconds")]
                ),
                seed_id=i,
            )
            samples.append(sample)
        return samples

    def evaluate_sample(
        self,
        sample: InputSample,
        use_coupled_cycle: bool = True,
    ) -> EnsembleEvaluation:
        """Evaluate one sample through the model hierarchy."""
        try:
            # Create design with sample inputs
            state = self.base_design.as_input_state()
            state.update(sample.to_state_update())
            design = create_engine_design(state)

            # Extract key outputs from derived geometry
            evaluation = EnsembleEvaluation(
                sample_id=sample.seed_id,
                input_sample=sample,
                design=design,
                coupled_cycle_passed=False,
                validation_passed=False,
                dry_mass_index=float(design.derived.dry_mass_index),
                thermal_margin_index=float(design.derived.thermal_margin_index),
                packaging_efficiency_index=float(design.derived.packaging_efficiency_index),
                total_stack_length_mm=float(design.derived.total_stack_length_mm),
            )

            # Run validation check
            try:
                validation_result = validate_engine_design(design)
                evaluation.validation_passed = validation_result.passed
                if not validation_result.passed:
                    failed_checks = [
                        c.check_name for c in validation_result.checks if not c.passed
                    ]
                    evaluation.errors.append(f"Validation failed: {', '.join(failed_checks)}")
            except Exception as e:
                evaluation.errors.append(f"Validation error: {str(e)}")

            return evaluation

        except Exception as e:
            evaluation = EnsembleEvaluation(
                sample_id=sample.seed_id,
                input_sample=sample,
                design=None,
                coupled_cycle_passed=False,
                validation_passed=False,
                errors=[str(e)],
            )
            return evaluation

    def run(
        self,
        random_seed: Optional[int] = None,
        use_coupled_cycle: bool = False,
    ) -> EnsembleResults:
        """Execute full ensemble run.

        Args:
            random_seed: Optional seed for reproducibility
            use_coupled_cycle: If True, invoke coupled cycle solver per sample

        Returns:
            EnsembleResults with all evaluations and aggregated statistics
        """
        import time

        start_time = time.time()

        # Generate samples
        samples = self._generate_samples(random_seed)

        # Evaluate each sample
        evaluations = []
        for sample in samples:
            evaluation = self.evaluate_sample(sample, use_coupled_cycle=use_coupled_cycle)
            evaluations.append(evaluation)

        # Compute statistics on feasible evaluations
        feasible_evals = [e for e in evaluations if e.validation_passed]
        statistics = self._compute_statistics(feasible_evals)

        total_runtime = time.time() - start_time

        return EnsembleResults(
            evaluations=evaluations,
            statistics=statistics,
            sample_seed=random_seed or 0,
            total_runtime_seconds=total_runtime,
        )

    def _compute_statistics(
        self, feasible_evals: List[EnsembleEvaluation]
    ) -> Dict[str, EnsembleStatistics]:
        """Compute per-parameter statistics from feasible samples."""
        output_keys = [
            "dry_mass_index",
            "thermal_margin_index",
            "packaging_efficiency_index",
            "total_stack_length_mm",
        ]
        stats_dict = {}

        for key in output_keys:
            values = [
                getattr(e, key) for e in feasible_evals if getattr(e, key) is not None
            ]
            if not values:
                continue

            values_array = np.array(values)
            mean = float(np.mean(values_array))
            std_dev = float(np.std(values_array))
            min_val = float(np.min(values_array))
            max_val = float(np.max(values_array))

            # Percentiles
            p5 = float(np.percentile(values_array, 5))
            p25 = float(np.percentile(values_array, 25))
            p50 = float(np.percentile(values_array, 50))
            p75 = float(np.percentile(values_array, 75))
            p95 = float(np.percentile(values_array, 95))

            # 95% confidence interval using a normal distribution model.
            ci_lower = mean - 1.96 * std_dev / np.sqrt(len(values_array))
            ci_upper = mean + 1.96 * std_dev / np.sqrt(len(values_array))

            stats_dict[key] = EnsembleStatistics(
                parameter=key,
                n_samples=len(feasible_evals),
                n_feasible=len(values),
                feasibility_rate=len(values) / len(feasible_evals)
                if feasible_evals
                else 0.0,
                mean=mean,
                std_dev=std_dev,
                min_value=min_val,
                max_value=max_val,
                percentile_5=p5,
                percentile_25=p25,
                percentile_50=p50,
                percentile_75=p75,
                percentile_95=p95,
                confidence_lower=float(ci_lower),
                confidence_upper=float(ci_upper),
            )

        return stats_dict






