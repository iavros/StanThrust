"""Multi-fidelity optimization adapter for Stage 4.3.

This module provides a lightweight surrogate model for fast concept-stage
prescreen, followed by higher-fidelity solver confirmation for promising
candidates. Enables efficient exploration of large design spaces.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class SurrogateEvaluation:
    """Result from lightweight surrogate evaluation."""
    design_state: Dict[str, object]
    predicted_score: float
    predicted_mass: float
    predicted_thermal_margin: float
    confidence: float  # 0.0–1.0; how reliable is this prediction
    eval_time_ms: float


@dataclass(frozen=True)
class ConfirmedEvaluation:
    """Result from higher-fidelity confirmation."""
    design_state: Dict[str, object]
    confirmed_score: float
    confirmed_mass: float
    confirmed_thermal_margin: float
    score_change_percent: float  # (confirmed - surrogate) / surrogate
    fidelity_level: str  # "low", "medium", "high"
    eval_time_ms: float


@dataclass(frozen=True)
class MultiFidelityResult:
    """Combined multi-fidelity optimization result."""
    best_score: float
    best_state: Dict[str, object]
    surrogate_evaluations: int
    confirmed_evaluations: int
    total_time_ms: float
    efficiency_factor: float  # ratio of work avoided vs full fidelity
    screening_history: List[Dict[str, object]]

    def as_dict(self) -> Dict[str, object]:
        return {
            "best_score": round(self.best_score, 4),
            "best_state": self.best_state,
            "surrogate_evaluations": self.surrogate_evaluations,
            "confirmed_evaluations": self.confirmed_evaluations,
            "total_time_ms": round(self.total_time_ms, 1),
            "efficiency_factor": round(self.efficiency_factor, 3),
            "screening_history_count": len(self.screening_history),
        }


class ConceptSurrogateModel:
    """Lightweight surrogate model for concept-stage design screening.

    Uses simple scaling laws and heuristics to predict key metrics
    (thrust, mass, thermal margin) from input parameters without
    invoking heavy solvers. ~1–2 ms per evaluation.
    """

    @staticmethod
    def predict(design_state: Dict[str, object]) -> SurrogateEvaluation:
        """Predict key metrics using concept-stage heuristics.

        Args:
            design_state: Design input parameters (thrust, impulse, geometry, etc.)

        Returns:
            SurrogateEvaluation with predicted scores and low-fidelity confidence.
        """
        import time
        start_ms = time.perf_counter() * 1000

        try:
            # Extract key parameters
            target_thrust = float(design_state.get("target_thrust_newtons", 250.0))
            target_impulse = float(design_state.get("target_impulse_newton_seconds", 3000.0))
            tank_diam = float(design_state.get("tank_diameter_mm", 110.0))
            chamber_diam = float(design_state.get("chamber_diameter_mm", 68.0))
            nozzle_diam = float(design_state.get("nozzle_diameter_mm", 95.0))
            burn_time = float(design_state.get("burn_time_seconds", 12.0))
            use_pumps = bool(design_state.get("use_pumps", False))
            regen = bool(design_state.get("regen_cooling", False))

            # Simple scaling laws (concept-only)
            thrust_scale = (target_thrust / 250.0) ** 0.22
            impulse_scale = (target_impulse / 3000.0) ** 0.16

            # Predict dry mass index (concept proxy)
            predicted_mass = (
                20.0 +
                (tank_diam * impulse_scale / 100.0) +
                (chamber_diam * thrust_scale / 50.0) +
                (nozzle_diam / 100.0) * 5.0 +
                (12.0 if use_pumps else 0.0) +
                (8.0 if regen else 0.0)
            )
            predicted_mass = max(10.0, min(100.0, predicted_mass))

            # Predict thermal margin index (concept proxy)
            predicted_thermal = (
                50.0 +
                (10.0 if regen else -5.0) +
                (5.0 if use_pumps else 0.0) -
                (thrust_scale * 5.0) -
                (burn_time / 24.0) * 3.0
            )
            predicted_thermal = max(5.0, min(95.0, predicted_thermal))

            # Predict thrust delivery (concept envelope)
            thrust_delivery = 0.85 + (0.05 if use_pumps else 0.0) + (0.04 if regen else 0.0)
            thrust_delivery = max(0.75, min(1.05, thrust_delivery))

            # Composite score (simple objective)
            # Higher thermal margin + lower mass + better thrust delivery
            cost_mass = predicted_mass / 100.0  # 0–1
            cost_thermal = 1.0 - (predicted_thermal / 100.0)  # 0–1 (lower is better)
            cost_thrust = abs(thrust_delivery - 1.0)  # 0–1 (lower is better)

            predicted_score = 1.0 - (cost_mass * 0.4 + cost_thermal * 0.35 + cost_thrust * 0.25)
            predicted_score = max(0.0, min(1.0, predicted_score))

            # Surrogate confidence (low fidelity, but quick)
            confidence = 0.55

            elapsed_ms = time.perf_counter() * 1000 - start_ms

            return SurrogateEvaluation(
                design_state=design_state,
                predicted_score=predicted_score,
                predicted_mass=predicted_mass,
                predicted_thermal_margin=predicted_thermal,
                confidence=confidence,
                eval_time_ms=elapsed_ms,
            )

        except Exception:
            # Fallback: return neutral prediction
            return SurrogateEvaluation(
                design_state=design_state,
                predicted_score=0.5,
                predicted_mass=50.0,
                predicted_thermal_margin=50.0,
                confidence=0.3,
                eval_time_ms=time.perf_counter() * 1000 - start_ms,
            )


class MultiFidelityScreener:
    """Orchestrates multi-fidelity optimization workflow.

    1. Use fast surrogate to pre-screen candidates
    2. Confirm promising candidates with higher-fidelity (full solver)
    3. Track efficiency gains vs full-fidelity-only approach
    """

    def __init__(
        self,
        surrogate_threshold: float = 0.60,
        confirmation_ratio: float = 0.10,
    ):
        """Initialize multi-fidelity screener.

        Args:
            surrogate_threshold: Min predicted score to warrant confirmation
            confirmation_ratio: Fraction of candidates to confirm (0.0–1.0)
        """
        self.surrogate_threshold = surrogate_threshold
        self.confirmation_ratio = max(0.01, min(1.0, confirmation_ratio))

    def screen_candidates(
        self,
        candidates: List[Dict[str, object]],
    ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        """Screen candidates using surrogate; return promising + unpromising.

        Args:
            candidates: List of design states to evaluate

        Returns:
            (promising_states, unpromising_states)
        """
        evaluations = [ConceptSurrogateModel.predict(c) for c in candidates]
        evaluations.sort(key=lambda e: e.predicted_score, reverse=True)

        threshold_idx = max(
            1,
            int(len(evaluations) * self.confirmation_ratio),
        )

        promising = [
            e.design_state for e in evaluations[:threshold_idx]
            if e.predicted_score >= self.surrogate_threshold
        ]
        unpromising = [e.design_state for e in evaluations[threshold_idx:]]

        return promising, unpromising

    def build_screening_summary(
        self,
        total_candidates: int,
        screened_promising: int,
        confirmed_count: int,
        total_time_ms: float,
    ) -> Dict[str, object]:
        """Build summary of multi-fidelity screening."""
        full_fidelity_time_ms = total_candidates * 50.0  # Rough estimate
        efficiency = (
            (total_candidates - confirmed_count) * 50.0 / max(1.0, total_time_ms)
        ) if total_time_ms > 0 else 0.0

        return {
            "total_candidates": total_candidates,
            "screened_promising": screened_promising,
            "confirmed_count": confirmed_count,
            "efficiency_factor": round(efficiency, 2),
            "estimated_full_fidelity_time_ms": round(full_fidelity_time_ms, 1),
            "actual_time_ms": round(total_time_ms, 1),
            "time_saved_percent": round(
                100.0 * (1.0 - total_time_ms / full_fidelity_time_ms),
                1
            ) if full_fidelity_time_ms > 0 else 0.0,
        }

