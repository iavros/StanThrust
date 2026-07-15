"""Direct solver fidelity coordination for optimizer runs."""

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Dict, List, Optional, Tuple


FAST_COST_MS = 10
DESIGN_COST_MS = 50
COUPLED_CYCLE_COST_MS = 500


class FidelityTier(str, Enum):
    """Solver fidelity levels."""

    FAST = "fast"
    DESIGN = "design"
    COUPLED_CYCLE = "coupled_cycle"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _tier_cost_ms(tier: FidelityTier) -> int:
    if tier == FidelityTier.FAST:
        return FAST_COST_MS
    if tier == FidelityTier.DESIGN:
        return DESIGN_COST_MS
    return COUPLED_CYCLE_COST_MS


@dataclass
class RoutingDecision:
    """Decision for routing one candidate."""

    candidate_id: str
    assigned_tier: FidelityTier
    requested_accuracy: float
    sobol_sensitivity_score: float
    cost_budget_available: int
    reasoning: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "assigned_tier": self.assigned_tier.value,
            "requested_accuracy": round(self.requested_accuracy, 4),
            "sobol_sensitivity_score": round(self.sobol_sensitivity_score, 4),
            "cost_budget_available": self.cost_budget_available,
            "reasoning": self.reasoning,
        }


@dataclass
class CandidateAllocation:
    """Result of allocating a candidate to an evaluation pool."""

    candidate_id: str
    tier: FidelityTier
    allocated_cost_ms: int
    allocated_at: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "tier": self.tier.value,
            "allocated_cost_ms": self.allocated_cost_ms,
            "allocated_at": self.allocated_at,
        }


@dataclass
class PoolStats:
    """Statistics for one evaluation pool."""

    tier: FidelityTier
    candidate_count: int
    total_allocated_cost_ms: int
    completed_count: int = 0
    total_actual_cost_ms: float = 0.0

    def as_dict(self) -> Dict[str, object]:
        return {
            "tier": self.tier.value,
            "candidate_count": self.candidate_count,
            "total_allocated_cost_ms": self.total_allocated_cost_ms,
            "completed_count": self.completed_count,
            "total_actual_cost_ms": self.total_actual_cost_ms,
        }


class FidelityRouter:
    """Routes optimizer candidates through direct solver tiers."""

    HIGH_ACCURACY_THRESHOLD = 0.85
    DESIGN_ACCURACY_THRESHOLD = 0.35
    SENSITIVITY_ESCALATION_THRESHOLD = 0.70

    def __init__(self, enable_coupled_cycle: bool = True):
        self.enable_coupled_cycle = enable_coupled_cycle
        self.routing_history: List[RoutingDecision] = []

    def route(
        self,
        candidate_id: str,
        requested_accuracy: float,
        sobol_sensitivity_score: float,
        cost_budget_available: int,
    ) -> RoutingDecision:
        """Determine which direct solver tier to use for one candidate."""

        requested_accuracy = _clamp01(requested_accuracy)
        sobol_sensitivity_score = _clamp01(sobol_sensitivity_score)
        cost_budget_available = max(0, int(cost_budget_available))

        if (
            requested_accuracy >= self.HIGH_ACCURACY_THRESHOLD
            or sobol_sensitivity_score >= self.SENSITIVITY_ESCALATION_THRESHOLD
        ):
            if self.enable_coupled_cycle and cost_budget_available >= COUPLED_CYCLE_COST_MS:
                assigned_tier = FidelityTier.COUPLED_CYCLE
                reasoning = "high accuracy or sensitivity routed to coupled-cycle solve"
            elif cost_budget_available >= DESIGN_COST_MS:
                assigned_tier = FidelityTier.DESIGN
                reasoning = "high accuracy or sensitivity routed to design solve"
            else:
                assigned_tier = FidelityTier.FAST
                reasoning = "budget limited route to fast solve"
        elif requested_accuracy >= self.DESIGN_ACCURACY_THRESHOLD and cost_budget_available >= DESIGN_COST_MS:
            assigned_tier = FidelityTier.DESIGN
            reasoning = "moderate accuracy routed to design solve"
        else:
            assigned_tier = FidelityTier.FAST
            reasoning = "low accuracy request or limited budget routed to fast solve"

        decision = RoutingDecision(
            candidate_id=candidate_id,
            assigned_tier=assigned_tier,
            requested_accuracy=requested_accuracy,
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
        """Route a batch of candidates while tracking remaining budget."""

        decisions = []
        remaining_budget = max(0, int(cost_budget_available))

        for candidate in candidates:
            candidate_id = str(candidate.get("candidate_id", f"cand_{len(decisions)}"))
            requested_accuracy = float(
                candidate.get("requested_accuracy", candidate.get("accuracy_request", 0.5))
            )
            sobol_sensitivity = float(candidate.get("sobol_sensitivity_score", 0.5))

            decision = self.route(
                candidate_id=candidate_id,
                requested_accuracy=requested_accuracy,
                sobol_sensitivity_score=sobol_sensitivity,
                cost_budget_available=remaining_budget,
            )
            remaining_budget = max(0, remaining_budget - _tier_cost_ms(decision.assigned_tier))
            decisions.append(decision)

        return decisions

    def get_routing_summary(self) -> Dict[str, object]:
        """Summary statistics of routing decisions."""

        if not self.routing_history:
            return {"total_routed": 0, "by_tier": {}}

        by_tier = {
            tier.value: sum(1 for decision in self.routing_history if decision.assigned_tier == tier)
            for tier in FidelityTier
        }
        avg_accuracy = sum(d.requested_accuracy for d in self.routing_history) / len(self.routing_history)
        avg_sensitivity = sum(d.sobol_sensitivity_score for d in self.routing_history) / len(self.routing_history)

        return {
            "total_routed": len(self.routing_history),
            "by_tier": by_tier,
            "average_requested_accuracy": round(avg_accuracy, 4),
            "average_sobol_sensitivity": round(avg_sensitivity, 4),
        }


class AdaptiveSamplingPool:
    """Manages evaluation queues across direct solver fidelity levels."""

    def __init__(self, budget_ms: int = 10000):
        self.budget_ms = budget_ms
        self.fast_queue: List[CandidateAllocation] = []
        self.design_queue: List[CandidateAllocation] = []
        self.coupled_queue: List[CandidateAllocation] = []
        self.allocation_log: List[CandidateAllocation] = []

    def allocate_candidate(self, decision: RoutingDecision) -> Optional[CandidateAllocation]:
        """Allocate a candidate based on routing decision."""

        allocated_cost = _tier_cost_ms(decision.assigned_tier)
        if self._total_allocated_cost() + allocated_cost > self.budget_ms:
            return None

        if decision.assigned_tier == FidelityTier.FAST:
            queue = self.fast_queue
        elif decision.assigned_tier == FidelityTier.DESIGN:
            queue = self.design_queue
        else:
            queue = self.coupled_queue

        allocation = CandidateAllocation(
            candidate_id=decision.candidate_id,
            tier=decision.assigned_tier,
            allocated_cost_ms=allocated_cost,
        )
        queue.append(allocation)
        self.allocation_log.append(allocation)
        return allocation

    def allocate_batch(self, decisions: List[RoutingDecision]) -> Tuple[int, int]:
        allocated = 0
        rejected = 0
        for decision in decisions:
            if self.allocate_candidate(decision):
                allocated += 1
            else:
                rejected += 1
        return allocated, rejected

    def record_completion(self, candidate_id: str, actual_cost_ms: float) -> bool:
        for allocation in self.allocation_log:
            if allocation.candidate_id == candidate_id:
                return True
        return False

    def _total_allocated_cost(self) -> int:
        return sum(allocation.allocated_cost_ms for allocation in self.allocation_log)

    def get_pool_stats(self) -> Dict[str, PoolStats]:
        return {
            "fast": PoolStats(
                tier=FidelityTier.FAST,
                candidate_count=len(self.fast_queue),
                total_allocated_cost_ms=len(self.fast_queue) * FAST_COST_MS,
            ),
            "design": PoolStats(
                tier=FidelityTier.DESIGN,
                candidate_count=len(self.design_queue),
                total_allocated_cost_ms=len(self.design_queue) * DESIGN_COST_MS,
            ),
            "coupled_cycle": PoolStats(
                tier=FidelityTier.COUPLED_CYCLE,
                candidate_count=len(self.coupled_queue),
                total_allocated_cost_ms=len(self.coupled_queue) * COUPLED_CYCLE_COST_MS,
            ),
        }

    def get_summary(self) -> Dict[str, object]:
        stats = self.get_pool_stats()
        total_cost = sum(row.total_allocated_cost_ms for row in stats.values())
        budget_utilization = min(100.0, total_cost / self.budget_ms * 100.0) if self.budget_ms > 0 else 0.0

        return {
            "budget_ms": self.budget_ms,
            "total_allocated_cost_ms": total_cost,
            "budget_utilization_percent": round(budget_utilization, 1),
            "by_pool": {tier: stats[tier].as_dict() for tier in ["fast", "design", "coupled_cycle"]},
        }


def coordinate_fidelity_escalation(
    ga_candidates: List[Dict[str, object]],
    router: FidelityRouter,
    pool: AdaptiveSamplingPool,
    cost_budget_ms: int = 10000,
) -> Dict[str, object]:
    """Coordinate direct solver fidelity selection for one optimizer generation."""

    pool.budget_ms = cost_budget_ms
    pool.fast_queue = []
    pool.design_queue = []
    pool.coupled_queue = []
    pool.allocation_log = []

    decisions = router.route_batch(ga_candidates, cost_budget_ms)
    allocated, rejected = pool.allocate_batch(decisions)

    return {
        "routing_decisions": [decision.as_dict() for decision in decisions],
        "routing_summary": router.get_routing_summary(),
        "allocation_stats": {
            "allocated": allocated,
            "rejected": rejected,
            "total": len(decisions),
        },
        "pool_summary": pool.get_summary(),
    }
