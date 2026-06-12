"""Machine Learning surrogate model for Stage 6 multi-fidelity optimization.

This module provides Gaussian Process and neural network surrogates trained
on historical GA results or ensemble data. Replaces the heuristic surrogate
with learned predictive models for higher accuracy and uncertainty quantification.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple
import numpy as np
import warnings

# Try to import ML libraries; graceful degradation if unavailable
try:
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, Matern, ConstantKernel as C
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    warnings.warn("scikit-learn not available; ML surrogate will use fallback heuristic")


@dataclass(frozen=True)
class TrainingDataPoint:
    """One training sample: design state → observed outputs."""
    design_state: Dict[str, object]
    observed_score: float
    observed_mass: float
    observed_thermal_margin: float
    source: str = "ga"  # "ga", "ensemble", "user"


@dataclass(frozen=True)
class MLSurrogateEvaluation:
    """Result from ML surrogate prediction with uncertainty."""
    design_state: Dict[str, object]
    predicted_score: float
    predicted_mass: float
    predicted_thermal_margin: float
    score_std: float  # Standard deviation (uncertainty)
    mass_std: float
    thermal_std: float
    confidence: float  # 0.0–1.0; based on GP confidence
    eval_time_ms: float
    model_type: str  # "gp_rbf", "gp_matern", "heuristic"


class GaussianProcessSurrogate:
    """Gaussian Process surrogate trained on historical design evaluations."""

    def __init__(self, kernel_type: str = "rbf", normalize: bool = True):
        """Initialize GP surrogate.

        Args:
            kernel_type: "rbf" (Radial Basis Function) or "matern"
            normalize: Whether to normalize input/output scales
        """
        if not HAS_SKLEARN:
            raise RuntimeError("scikit-learn required for ML surrogate")

        self.kernel_type = kernel_type
        self.normalize = normalize
        self.is_trained = False

        # Feature keys to extract from design state
        self.feature_keys = [
            "target_thrust_newtons",
            "target_impulse_newton_seconds",
            "burn_time_seconds",
            "tank_diameter_mm",
            "chamber_diameter_mm",
            "nozzle_diameter_mm",
        ]

        # Three separate regressors: one for each output
        self.regressor_score = None
        self.regressor_mass = None
        self.regressor_thermal = None

        self.feature_scaler = None
        self.output_scalers = {}

    def train(self, data_points: List[TrainingDataPoint]) -> None:
        """Train the GP surrogate on historical data.

        Args:
            data_points: List of (design_state, observed_outputs) pairs

        Raises:
            ValueError: If insufficient training data
        """
        if len(data_points) < 2:
            raise ValueError(f"Need at least 2 training samples; got {len(data_points)}")

        # Extract training features and targets
        X_list = []
        y_score = []
        y_mass = []
        y_thermal = []

        for point in data_points:
            features = [
                float(point.design_state.get(key, 0.0)) for key in self.feature_keys
            ]
            X_list.append(features)
            y_score.append(point.observed_score)
            y_mass.append(point.observed_mass)
            y_thermal.append(point.observed_thermal_margin)

        X = np.array(X_list, dtype=np.float64)
        y_score_arr = np.array(y_score, dtype=np.float64)
        y_mass_arr = np.array(y_mass, dtype=np.float64)
        y_thermal_arr = np.array(y_thermal, dtype=np.float64)

        # Normalize inputs
        if self.normalize:
            self.feature_scaler = StandardScaler()
            X_scaled = self.feature_scaler.fit_transform(X)
        else:
            X_scaled = X

        # Normalize outputs
        if self.normalize:
            for name, y in [
                ("score", y_score_arr),
                ("mass", y_mass_arr),
                ("thermal", y_thermal_arr),
            ]:
                scaler = StandardScaler()
                scaler.fit(y.reshape(-1, 1))
                self.output_scalers[name] = scaler

            y_score_scaled = self.output_scalers["score"].transform(
                y_score_arr.reshape(-1, 1)
            ).ravel()
            y_mass_scaled = self.output_scalers["mass"].transform(
                y_mass_arr.reshape(-1, 1)
            ).ravel()
            y_thermal_scaled = self.output_scalers["thermal"].transform(
                y_thermal_arr.reshape(-1, 1)
            ).ravel()
        else:
            y_score_scaled = y_score_arr
            y_mass_scaled = y_mass_arr
            y_thermal_scaled = y_thermal_arr

        # Create and train regressors
        if self.kernel_type == "matern":
            kernel = C(1.0) * Matern(nu=2.5, length_scale=1.0)
        else:  # rbf (default)
            kernel = C(1.0) * RBF(length_scale=1.0)

        # Train separate models for each output
        self.regressor_score = GaussianProcessRegressor(
            kernel=kernel,
            n_restarts_optimizer=10,
            random_state=42,
            normalize_y=True,
            alpha=1e-6,
        )
        self.regressor_score.fit(X_scaled, y_score_scaled)

        self.regressor_mass = GaussianProcessRegressor(
            kernel=kernel,
            n_restarts_optimizer=10,
            random_state=42,
            normalize_y=True,
            alpha=1e-6,
        )
        self.regressor_mass.fit(X_scaled, y_mass_scaled)

        self.regressor_thermal = GaussianProcessRegressor(
            kernel=kernel,
            n_restarts_optimizer=10,
            random_state=42,
            normalize_y=True,
            alpha=1e-6,
        )
        self.regressor_thermal.fit(X_scaled, y_thermal_scaled)

        self.is_trained = True

    def predict(self, design_state: Dict[str, object]) -> Tuple[float, float, float, float, float, float]:
        """Predict outputs with uncertainty for a design.

        Returns:
            (score, score_std, mass, mass_std, thermal, thermal_std)
        """
        if not self.is_trained:
            raise RuntimeError("Must call train() before predict()")

        # Extract features
        x = np.array(
            [float(design_state.get(key, 0.0)) for key in self.feature_keys],
            dtype=np.float64,
        ).reshape(1, -1)

        # Scale features
        if self.normalize and self.feature_scaler:
            x_scaled = self.feature_scaler.transform(x)
        else:
            x_scaled = x

        # Get predictions with uncertainty
        score_pred, score_std = self.regressor_score.predict(x_scaled, return_std=True)
        mass_pred, mass_std = self.regressor_mass.predict(x_scaled, return_std=True)
        thermal_pred, thermal_std = self.regressor_thermal.predict(x_scaled, return_std=True)

        # Unscale predictions
        if self.normalize:
            score_pred = self.output_scalers["score"].inverse_transform(
                score_pred.reshape(-1, 1)
            )[0, 0]
            score_std = score_std * self.output_scalers["score"].scale_[0]

            mass_pred = self.output_scalers["mass"].inverse_transform(
                mass_pred.reshape(-1, 1)
            )[0, 0]
            mass_std = mass_std * self.output_scalers["mass"].scale_[0]

            thermal_pred = self.output_scalers["thermal"].inverse_transform(
                thermal_pred.reshape(-1, 1)
            )[0, 0]
            thermal_std = thermal_std * self.output_scalers["thermal"].scale_[0]

        def scalar(value) -> float:
            return float(np.asarray(value).reshape(-1)[0])

        return (
            scalar(score_pred),
            scalar(score_std),
            scalar(mass_pred),
            scalar(mass_std),
            scalar(thermal_pred),
            scalar(thermal_std),
        )

    def get_training_stats(self) -> Dict[str, object]:
        """Return statistics about the trained model."""
        if not self.is_trained:
            return {"status": "not_trained"}

        return {
            "status": "trained",
            "kernel_type": self.kernel_type,
            "normalization": self.normalize,
            "n_features": len(self.feature_keys),
            "feature_names": self.feature_keys,
        }


class MLSurrogateModel:
    """High-level ML surrogate model matching ConceptSurrogateModel API.

    Wraps a trained Gaussian Process or falls back to heuristic if not trained.
    """

    def __init__(self, kernel_type: str = "rbf", use_heuristic_fallback: bool = True):
        """Initialize ML surrogate.

        Args:
            kernel_type: "rbf" or "matern"
            use_heuristic_fallback: Fall back to heuristic if ML fails
        """
        self.use_heuristic_fallback = use_heuristic_fallback
        self.gp_surrogate = None

        if HAS_SKLEARN:
            try:
                self.gp_surrogate = GaussianProcessSurrogate(kernel_type=kernel_type)
            except Exception as e:
                warnings.warn(f"Could not initialize GP surrogate: {e}")

    def train(self, training_data: List[TrainingDataPoint]) -> bool:
        """Train the ML surrogate.

        Args:
            training_data: Historical design evaluations

        Returns:
            True if training succeeded, False otherwise
        """
        if not self.gp_surrogate:
            return False

        try:
            self.gp_surrogate.train(training_data)
            return True
        except Exception as exc:
            warnings.warn(f"ML surrogate training failed: {exc}")
            return False

    def is_trained(self) -> bool:
        """Check if the model is trained."""
        return self.gp_surrogate is not None and self.gp_surrogate.is_trained

    def predict(self, design_state: Dict[str, object]) -> MLSurrogateEvaluation:
        """Predict key metrics using ML or fallback heuristic.

        Args:
            design_state: Design input parameters

        Returns:
            MLSurrogateEvaluation with predictions and uncertainty
        """
        import time

        start_ms = time.perf_counter() * 1000

        try:
            # Try ML if trained
            if self.is_trained():
                score, score_std, mass, mass_std, thermal, thermal_std = self.gp_surrogate.predict(
                    design_state
                )

                # Clamp to reasonable ranges
                score = max(0.0, min(1.0, score))
                mass = max(10.0, min(100.0, mass))
                thermal = max(5.0, min(95.0, thermal))

                # Confidence inversely related to uncertainty
                avg_std = (score_std + mass_std / 100.0 + thermal_std / 100.0) / 3.0
                confidence = max(0.5, min(0.95, 1.0 - avg_std))

                elapsed_ms = time.perf_counter() * 1000 - start_ms

                return MLSurrogateEvaluation(
                    design_state=design_state,
                    predicted_score=score,
                    predicted_mass=mass,
                    predicted_thermal_margin=thermal,
                    score_std=score_std,
                    mass_std=mass_std,
                    thermal_std=thermal_std,
                    confidence=confidence,
                    eval_time_ms=elapsed_ms,
                    model_type=self.gp_surrogate.kernel_type,
                )

            # Fall back to heuristic if not trained
            if self.use_heuristic_fallback:
                return self._predict_heuristic(design_state, start_ms)
            else:
                raise RuntimeError("ML surrogate not trained and fallback disabled")

        except Exception:
            # Final fallback to heuristic
            if self.use_heuristic_fallback:
                return self._predict_heuristic(design_state, start_ms)
            else:
                raise

    def _predict_heuristic(
        self, design_state: Dict[str, object], start_ms: float
    ) -> MLSurrogateEvaluation:
        """Fallback heuristic prediction (same as ConceptSurrogateModel)."""
        import time

        try:
            target_thrust = float(design_state.get("target_thrust_newtons", 250.0))
            target_impulse = float(design_state.get("target_impulse_newton_seconds", 3000.0))
            tank_diam = float(design_state.get("tank_diameter_mm", 110.0))
            chamber_diam = float(design_state.get("chamber_diameter_mm", 68.0))
            nozzle_diam = float(design_state.get("nozzle_diameter_mm", 95.0))
            burn_time = float(design_state.get("burn_time_seconds", 12.0))
            use_pumps = bool(design_state.get("use_pumps", False))
            regen = bool(design_state.get("regen_cooling", False))

            thrust_scale = (target_thrust / 250.0) ** 0.22
            impulse_scale = (target_impulse / 3000.0) ** 0.16

            # Heuristic predictions
            mass = (
                20.0
                + (tank_diam * impulse_scale / 100.0)
                + (chamber_diam * thrust_scale / 50.0)
                + (nozzle_diam / 100.0) * 5.0
                + (12.0 if use_pumps else 0.0)
                + (8.0 if regen else 0.0)
            )
            mass = max(10.0, min(100.0, mass))

            thermal = (
                50.0
                + (10.0 if regen else -5.0)
                + (5.0 if use_pumps else 0.0)
                - (thrust_scale * 5.0)
                - (burn_time / 24.0) * 3.0
            )
            thermal = max(5.0, min(95.0, thermal))

            thrust_delivery = 0.85 + (0.05 if use_pumps else 0.0) + (0.04 if regen else 0.0)
            thrust_delivery = max(0.75, min(1.05, thrust_delivery))

            cost_mass = mass / 100.0
            cost_thermal = 1.0 - (thermal / 100.0)
            cost_thrust = abs(thrust_delivery - 1.0)
            score = 1.0 - (cost_mass * 0.4 + cost_thermal * 0.35 + cost_thrust * 0.25)
            score = max(0.0, min(1.0, score))

            elapsed_ms = time.perf_counter() * 1000 - start_ms

            return MLSurrogateEvaluation(
                design_state=design_state,
                predicted_score=score,
                predicted_mass=mass,
                predicted_thermal_margin=thermal,
                score_std=0.15,  # Heuristic uncertainty estimate
                mass_std=5.0,
                thermal_std=8.0,
                confidence=0.55,  # Low confidence for heuristic
                eval_time_ms=elapsed_ms,
                model_type="heuristic",
            )

        except Exception:
            elapsed_ms = time.perf_counter() * 1000 - start_ms
            return MLSurrogateEvaluation(
                design_state=design_state,
                predicted_score=0.5,
                predicted_mass=50.0,
                predicted_thermal_margin=50.0,
                score_std=0.25,
                mass_std=10.0,
                thermal_std=15.0,
                confidence=0.3,
                eval_time_ms=elapsed_ms,
                model_type="error_fallback",
            )


def create_training_data_from_ga_history(
    ga_history: List[Dict[str, object]],
) -> List[TrainingDataPoint]:
    """Convert GA history into training data.

    Expects each GA result dict to contain:
    - "best_state": design state
    - "best_score": composite objective score
    - "best_design": design dict with derived metrics

    Args:
        ga_history: GA results with scores and states

    Returns:
        List of TrainingDataPoint objects
    """
    training_data = []

    for result in ga_history:
        try:
            state = result.get("best_state", {})
            score = float(result.get("best_score", 0.5))
            design_dict = result.get("best_design", {})

            # Extract derived metrics from design
            if isinstance(design_dict, dict):
                derived = design_dict.get("derived", {})
                if isinstance(derived, dict):
                    mass = float(derived.get("dry_mass_index", 50.0))
                    thermal = float(derived.get("thermal_margin_index", 50.0))
                else:
                    mass = 50.0
                    thermal = 50.0
            else:
                mass = 50.0
                thermal = 50.0

            training_data.append(
                TrainingDataPoint(
                    design_state=state,
                    observed_score=score,
                    observed_mass=mass,
                    observed_thermal_margin=thermal,
                    source="ga",
                )
            )
        except (KeyError, ValueError, TypeError):
            continue

    return training_data


def create_training_data_from_ensemble(
    ensemble_results,
) -> List[TrainingDataPoint]:
    """Convert ensemble results into training data.

    Args:
        ensemble_results: EnsembleResults object from Stage 5

    Returns:
        List of TrainingDataPoint objects
    """
    training_data = []

    for evaluation in ensemble_results.evaluations:
        if not evaluation.validation_passed:
            continue  # Skip infeasible samples

        try:
            state = evaluation.input_sample.to_state_update()
            # Use derived metrics as targets
            score = 0.5  # Ensemble doesn't have composite score; use neutral
            mass = float(evaluation.dry_mass_index or 50.0)
            thermal = float(evaluation.thermal_margin_index or 50.0)

            training_data.append(
                TrainingDataPoint(
                    design_state=state,
                    observed_score=score,
                    observed_mass=mass,
                    observed_thermal_margin=thermal,
                    source="ensemble",
                )
            )
        except (AttributeError, ValueError, TypeError):
            continue

    return training_data

