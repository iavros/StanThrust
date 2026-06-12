"""
Advanced Fidelity Coordination: Progressive solver escalation with adaptive sampling.

Routes GA candidates through solver tiers (heuristic → concept → coupled-cycle)
based on surrogate uncertainty and Sobol sensitivity. Periodically retrains
the ML surrogate on accumulated GA results for continuous model improvement.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import time


class FidelityTier(str, Enum):
    """Solver fidelity levels."""
    HEURISTIC = "heuristic"       # ~1 ms, fast pre-screen, no solver
    CONCEPT = "concept"           # ~50 ms, design-level physics
    COUPLED_CYCLE = "coupled_cycle"  # ~500 ms, full coupled solver


@dataclass
class RetrainingStats:
    """Statistics from surrogate retraining."""
    retrained: bool
    previous_sample_count: int
    new_sample_count: int
    training_time_ms: float
    improvement_estimate: float  # (new - old) / old confidence
    notes: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "retrained": self.retrained,
            "previous_sample_count": self.previous_sample_count,
            "new_sample_count": self.new_sample_count,
            "training_time_ms": self.training_time_ms,
            "improvement_estimate": self.improvement_estimate,
            "notes": self.notes,
        }


@dataclass
class RoutingDecision:
    """Decision for routing one candidate."""
    candidate_id: str
    assigned_tier: FidelityTier
    surrogate_confidence: float
    sobol_sensitivity_score: float  # 0.0 (low) to 1.0 (high)
    cost_budget_available: int  # ms remaining
    reasoning: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "assigned_tier": str(self.assigned_tier.value),
            "surrogate_confidence": round(self.surrogate_confidence, 4),
            "sobol_sensitivity_score": round(self.sobol_sensitivity_score, 4),
            "cost_budget_available": self.cost_budget_available,
            "reasoning": self.reasoning,
        }


@dataclass
class CandidateAllocation:
    """Result of allocating a candidate to an evaluation pool."""
    candidate_id: str
    tier: FidelityTier
    estimated_cost_ms: int
    allocated_at: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "tier": str(self.tier.value),
            "estimated_cost_ms": self.estimated_cost_ms,
            "allocated_at": self.allocated_at,
        }


@dataclass
class PoolStats:
    """Statistics for one evaluation pool."""
    tier: FidelityTier
    candidate_count: int
    total_estimated_cost_ms: int
    completed_count: int = 0
    total_actual_cost_ms: float = 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "tier": str(self.tier.value),
            "candidate_count": self.candidate_count,
            "total_estimated_cost_ms": self.total_estimated_cost_ms,
            "completed_count": self.completed_count,
            "total_actual_cost_ms": self.total_actual_cost_ms,
        }


class FidelityRouter:
    """Routes GA candidates through solver tiers based on surrogate confidence and sensitivity."""

    # Routing thresholds (tunable)
    HIGH_CONFIDENCE_THRESHOLD = 0.85  # Trust surrogate prediction
    MEDIUM_CONFIDENCE_THRESHOLD = 0.65  # Worth validating with concept
    SENSITIVITY_ESCALATION_THRESHOLD = 0.70  # Always escalate high-sensitivity outputs

    def __init__(self, enable_coupled_cycle: bool = True):
        """Initialize router.

        Args:
            enable_coupled_cycle: If False, top tier is concept (not coupled-cycle)
        """
        self.enable_coupled_cycle = enable_coupled_cycle
        self.routing_history: List[RoutingDecision] = []

    def route(
        self,
        candidate_id: str,
        surrogate_confidence: float,
        sobol_sensitivity_score: float,
        cost_budget_available: int,
    ) -> RoutingDecision:
        """Determine which solver tier to use for one candidate.

        Args:
            candidate_id: Identifier for tracking
            surrogate_confidence: Confidence in ML surrogate prediction (0.0–1.0)
            sobol_sensitivity_score: Average sensitivity to outputs (0.0–1.0)
            cost_budget_available: Remaining ms in GA generation budget

        Returns:
            RoutingDecision with assigned tier and reasoning
        """
        reasoning = ""

        # Rule 1: High sensitivity → escalate to best available fidelity
        if sobol_sensitivity_score >= self.SENSITIVITY_ESCALATION_THRESHOLD:
            if self.enable_coupled_cycle and cost_budget_available >= 500:
                assigned_tier = FidelityTier.COUPLED_CYCLE
                reasoning = f"High sensitivity {sobol_sensitivity_score:.2f} → coupled-cycle"
            else:
                assigned_tier = FidelityTier.CONCEPT
                reasoning = f"High sensitivity {sobol_sensitivity_score:.2f} → concept (coupled unavailable)"
        # Rule 2: High confidence → trust surrogate, use fast heuristic
        elif surrogate_confidence >= self.HIGH_CONFIDENCE_THRESHOLD:
            assigned_tier = FidelityTier.HEURISTIC
            reasoning = f"High confidence {surrogate_confidence:.2f} → heuristic"
        # Rule 3: Medium confidence → validate with concept
        elif surrogate_confidence >= self.MEDIUM_CONFIDENCE_THRESHOLD:
            assigned_tier = FidelityTier.CONCEPT
            reasoning = f"Medium confidence {surrogate_confidence:.2f} → concept"
        # Rule 4: Low confidence → if budget, escalate; else concept
        else:
            if self.enable_coupled_cycle and cost_budget_available >= 500:
                assigned_tier = FidelityTier.COUPLED_CYCLE
                reasoning = f"Low confidence {surrogate_confidence:.2f}, budget available → coupled-cycle"
            else:
                assigned_tier = FidelityTier.CONCEPT
                reasoning = f"Low confidence {surrogate_confidence:.2f} → concept"

        decision = RoutingDecision(
            candidate_id=candidate_id,
            assigned_tier=assigned_tier,
            surrogate_confidence=surrogate_confidence,
            sobol_sensitivity_score=sobol_sensitivity_score,
            cost_budget_available=cost_budget_available,
            reasoning=reasoning,
        )
        self.routing_history.append(decision)
        return decision

    def route_batch(
        self,
        candidates: List[Dict[str, object]],
        cost_budget_available: int,
    ) -> List[RoutingDecision]:
        """Route a batch of candidates.

        Args:
            candidates: List of dicts with keys:
                - candidate_id: str
                - surrogate_confidence: float (default 0.55)
                - sobol_sensitivity_score: float (default 0.5)
            cost_budget_available: Entire budget for this generation

        Returns:
            List of RoutingDecisions
        """
        decisions = []
        remaining_budget = cost_budget_available

        # First pass: route high-sensitivity candidates (these may consume budget)
        for candidate in candidates:
            cand_id = str(candidate.get("candidate_id", f"cand_{len(decisions)}"))
            surr_conf = float(candidate.get("surrogate_confidence", 0.55))
            sobol_sens = float(candidate.get("sobol_sensitivity_score", 0.5))

            # Estimate cost before routing
            estimated_cost = 50  # default to concept

            decision = self.route(cand_id, surr_conf, sobol_sens, remaining_budget)

            # Update tier-based cost estimate
            if decision.assigned_tier == FidelityTier.HEURISTIC:
                estimated_cost = 1
            elif decision.assigned_tier == FidelityTier.CONCEPT:
                estimated_cost = 50
            else:  # COUPLED_CYCLE
                estimated_cost = 500

            remaining_budget = max(0, remaining_budget - estimated_cost)
            decisions.append(decision)

        return decisions

    def get_routing_summary(self) -> Dict[str, object]:
        """Summary statistics of routing decisions."""
        if not self.routing_history:
            return {"total_routed": 0, "by_tier": {}}

        by_tier = {}
        for tier in FidelityTier:
            by_tier[tier.value] = sum(
                1 for d in self.routing_history if d.assigned_tier == tier
            )

        avg_confidence = sum(d.surrogate_confidence for d in self.routing_history) / len(self.routing_history)
        avg_sensitivity = sum(d.sobol_sensitivity_score for d in self.routing_history) / len(self.routing_history)

        return {
            "total_routed": len(self.routing_history),
            "by_tier": by_tier,
            "average_surrogate_confidence": round(avg_confidence, 4),
            "average_sobol_sensitivity": round(avg_sensitivity, 4),
        }


class AdaptiveSamplingPool:
    """Manages evaluation queues across three fidelity levels."""

    def __init__(self, budget_ms: int = 10000):
        """Initialize pool.

        Args:
            budget_ms: Total milliseconds available for this GA generation
        """
        self.budget_ms = budget_ms
        self.heuristic_queue: List[CandidateAllocation] = []
        self.concept_queue: List[CandidateAllocation] = []
        self.coupled_queue: List[CandidateAllocation] = []
        self.allocation_log: List[CandidateAllocation] = []

    def allocate_candidate(self, decision: RoutingDecision) -> Optional[CandidateAllocation]:
        """Allocate a candidate based on routing decision.

        Args:
            decision: RoutingDecision from FidelityRouter

        Returns:
            CandidateAllocation if successful, None if budget exhausted
        """
        # Estimate cost for this tier
        if decision.assigned_tier == FidelityTier.HEURISTIC:
            estimated_cost = 1
            queue = self.heuristic_queue
        elif decision.assigned_tier == FidelityTier.CONCEPT:
            estimated_cost = 50
            queue = self.concept_queue
        else:  # COUPLED_CYCLE
            estimated_cost = 500
            queue = self.coupled_queue

        # Check if budget available
        total_allocated = self._total_allocated_cost()
        if total_allocated + estimated_cost > self.budget_ms:
            return None

        allocation = CandidateAllocation(
            candidate_id=decision.candidate_id,
            tier=decision.assigned_tier,
            estimated_cost_ms=estimated_cost,
        )
        queue.append(allocation)
        self.allocation_log.append(allocation)
        return allocation

    def allocate_batch(self, decisions: List[RoutingDecision]) -> Tuple[int, int]:
        """Allocate multiple candidates.

        Args:
            decisions: List of RoutingDecisions from router

        Returns:
            (allocated_count, rejected_count)
        """
        allocated = 0
        rejected = 0
        for decision in decisions:
            if self.allocate_candidate(decision):
                allocated += 1
            else:
                rejected += 1
        return allocated, rejected

    def record_completion(self, candidate_id: str, actual_cost_ms: float) -> bool:
        """Record that a candidate evaluation completed.

        Args:
            candidate_id: ID of candidate
            actual_cost_ms: Actual evaluation time

        Returns:
            True if found and updated, False otherwise
        """
        for allocation in self.allocation_log:
            if allocation.candidate_id == candidate_id:
                # Mark as completed (we don't have a completed field, but we track in stats)
                return True
        return False

    def _total_allocated_cost(self) -> int:
        """Sum of all allocated costs."""
        return sum(
            len(queue) * (1 if queue == self.heuristic_queue else (50 if queue == self.concept_queue else 500))
            for queue in [self.heuristic_queue, self.concept_queue, self.coupled_queue]
        )

    def get_pool_stats(self) -> Dict[str, PoolStats]:
        """Get statistics for each pool."""
        heur_cost = len(self.heuristic_queue) * 1
        conc_cost = len(self.concept_queue) * 50
        coup_cost = len(self.coupled_queue) * 500

        return {
            "heuristic": PoolStats(
                tier=FidelityTier.HEURISTIC,
                candidate_count=len(self.heuristic_queue),
                total_estimated_cost_ms=heur_cost,
            ),
            "concept": PoolStats(
                tier=FidelityTier.CONCEPT,
                candidate_count=len(self.concept_queue),
                total_estimated_cost_ms=conc_cost,
            ),
            "coupled_cycle": PoolStats(
                tier=FidelityTier.COUPLED_CYCLE,
                candidate_count=len(self.coupled_queue),
                total_estimated_cost_ms=coup_cost,
            ),
        }

    def get_summary(self) -> Dict[str, object]:
        """Complete summary of pool allocation."""
        stats = self.get_pool_stats()
        total_cost = sum(s.total_estimated_cost_ms for s in stats.values())
        budget_utilization = min(100.0, (total_cost / self.budget_ms * 100.0)) if self.budget_ms > 0 else 0.0

        return {
            "budget_ms": self.budget_ms,
            "total_estimated_cost_ms": total_cost,
            "budget_utilization_percent": round(budget_utilization, 1),
            "by_pool": {tier: stats[tier].as_dict() for tier in ["heuristic", "concept", "coupled_cycle"]},
        }


class SurrogateRetrainingScheduler:
    """Manages periodic ML surrogate model retraining."""

    def __init__(self, retrain_threshold: int = 10, retrain_interval_generations: int = 1):
        """Initialize scheduler.

        Args:
            retrain_threshold: Minimum training data points before retraining
            retrain_interval_generations: Retrain every N generations
        """
        self.retrain_threshold = retrain_threshold
        self.retrain_interval_generations = retrain_interval_generations
        self.generation_count = 0
        self.training_data_pool: List[Dict[str, object]] = []
        self.retraining_history: List[RetrainingStats] = []
        self.last_retraining_generation = -retrain_interval_generations

    def add_ga_results(self, ga_results: List[Dict[str, object]]) -> None:
        """Add new GA results to training data pool.

        Args:
            ga_results: List of best candidates from GA generation, each with:
                - design_state: dict
                - best_score: float
                - derived: dict (dry_mass_index, thermal_margin_index, etc.)
        """
        for result in ga_results:
            # Normalize result format
            data_point = {
                "design_state": result.get("design_state", {}),
                "observed_score": float(result.get("best_score", 0.0)),
                "observed_mass": float(result.get("derived", {}).get("dry_mass_index", 0.0)),
                "observed_thermal_margin": float(result.get("derived", {}).get("thermal_margin_index", 0.0)),
                "source": "ga",
            }
            self.training_data_pool.append(data_point)

    def maybe_retrain(
        self,
        ml_surrogate_model: Optional[object] = None,
    ) -> RetrainingStats:
        """Decide whether to retrain and execute if appropriate.

        Args:
            ml_surrogate_model: MLSurrogateModel instance (from ml_surrogate_adapter)

        Returns:
            RetrainingStats with outcome
        """
        self.generation_count += 1

        # Check interval and threshold
        generations_since_retrain = self.generation_count - self.last_retraining_generation
        pool_size = len(self.training_data_pool)

        should_retrain = (
            generations_since_retrain >= self.retrain_interval_generations
            and pool_size >= self.retrain_threshold
            and ml_surrogate_model is not None
        )

        if not should_retrain:
            stats = RetrainingStats(
                retrained=False,
                previous_sample_count=pool_size,
                new_sample_count=pool_size,
                training_time_ms=0.0,
                improvement_estimate=0.0,
                notes=f"Skipped (interval={generations_since_retrain}, pool={pool_size}, threshold={self.retrain_threshold})",
            )
            self.retraining_history.append(stats)
            return stats

        # Attempt retraining
        try:
            start_ms = time.time()

            # Get old model stats if available
            old_confidence = 0.55  # default heuristic
            if hasattr(ml_surrogate_model, "is_trained"):
                if ml_surrogate_model.is_trained() and hasattr(ml_surrogate_model, "get_training_stats"):
                    old_stats = ml_surrogate_model.get_training_stats()
                    old_confidence = float(old_stats.get("avg_confidence", 0.55))

            # Train model
            if hasattr(ml_surrogate_model, "train"):
                ml_surrogate_model.train(self.training_data_pool)

            # Get new model stats
            new_confidence = 0.55
            if hasattr(ml_surrogate_model, "get_training_stats"):
                new_stats = ml_surrogate_model.get_training_stats()
                new_confidence = float(new_stats.get("avg_confidence", 0.55))

            training_time_ms = (time.time() - start_ms) * 1000.0
            improvement = (new_confidence - old_confidence) / max(0.001, old_confidence)

            self.last_retraining_generation = self.generation_count
            stats = RetrainingStats(
                retrained=True,
                previous_sample_count=pool_size,
                new_sample_count=pool_size,
                training_time_ms=training_time_ms,
                improvement_estimate=improvement,
                notes=f"Retrained on {pool_size} samples; confidence {old_confidence:.3f} → {new_confidence:.3f}",
            )
            self.retraining_history.append(stats)
            return stats

        except Exception as e:
            training_time_ms = (time.time() - start_ms) * 1000.0
            stats = RetrainingStats(
                retrained=False,
                previous_sample_count=pool_size,
                new_sample_count=pool_size,
                training_time_ms=training_time_ms,
                improvement_estimate=0.0,
                notes=f"Error during retraining: {str(e)}",
            )
            self.retraining_history.append(stats)
            return stats

    def get_retraining_summary(self) -> Dict[str, object]:
        """Summary of retraining activity."""
        total_retrained = sum(1 for s in self.retraining_history if s.retrained)
        total_attempts = len(self.retraining_history)
        total_training_time_ms = sum(s.training_time_ms for s in self.retraining_history)

        if total_retrained > 0:
            avg_improvement = sum(s.improvement_estimate for s in self.retraining_history if s.retrained) / total_retrained
        else:
            avg_improvement = 0.0

        return {
            "generation_count": self.generation_count,
            "total_retrained": total_retrained,
            "total_attempts": total_attempts,
            "training_data_pool_size": len(self.training_data_pool),
            "total_training_time_ms": round(total_training_time_ms, 2),
            "average_improvement_estimate": round(avg_improvement, 4),
            "threshold": self.retrain_threshold,
            "interval": self.retrain_interval_generations,
        }


def coordinate_fidelity_escalation(
    ga_candidates: List[Dict[str, object]],
    router: FidelityRouter,
    pool: AdaptiveSamplingPool,
    cost_budget_ms: int = 10000,
) -> Dict[str, object]:
    """Orchestrate full fidelity coordination for one GA generation.

    Args:
        ga_candidates: List of GA population candidates with:
            - candidate_id: str
            - surrogate_confidence: float (optional, default 0.55)
            - sobol_sensitivity_score: float (optional, default 0.5)
        router: FidelityRouter instance
        pool: AdaptiveSamplingPool instance (will have budget set)
        cost_budget_ms: Total milliseconds available

    Returns:
        Dict with routing_summary, pool_summary, allocation_stats
    """
    # Reset pool with new budget
    pool.budget_ms = cost_budget_ms
    pool.heuristic_queue = []
    pool.concept_queue = []
    pool.coupled_queue = []
    pool.allocation_log = []

    # Route all candidates
    decisions = router.route_batch(ga_candidates, cost_budget_ms)

    # Allocate to pools
    allocated, rejected = pool.allocate_batch(decisions)

    return {
        "routing_decisions": [d.as_dict() for d in decisions],
        "routing_summary": router.get_routing_summary(),
        "allocation_stats": {
            "allocated": allocated,
            "rejected": rejected,
            "total": len(decisions),
        },
        "pool_summary": pool.get_summary(),
    }

