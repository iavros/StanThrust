"""Tests for direct solver fidelity coordination."""

from stanthrust.fidelity_coordinator import (
    AdaptiveSamplingPool,
    CandidateAllocation,
    FidelityRouter,
    FidelityTier,
    PoolStats,
    RoutingDecision,
    coordinate_fidelity_escalation,
)


def test_router_creation():
    router = FidelityRouter()
    assert router.enable_coupled_cycle is True
    assert router.routing_history == []


def test_route_high_accuracy_to_coupled_cycle():
    router = FidelityRouter()
    decision = router.route(
        candidate_id="cand_1",
        requested_accuracy=0.95,
        sobol_sensitivity_score=0.2,
        cost_budget_available=10000,
    )
    assert decision.assigned_tier == FidelityTier.COUPLED_CYCLE
    assert "coupled-cycle" in decision.reasoning


def test_route_high_sensitivity_to_best_available_tier():
    router = FidelityRouter(enable_coupled_cycle=False)
    decision = router.route(
        candidate_id="cand_1",
        requested_accuracy=0.2,
        sobol_sensitivity_score=0.85,
        cost_budget_available=10000,
    )
    assert decision.assigned_tier == FidelityTier.DESIGN


def test_route_budget_limited_to_fast():
    router = FidelityRouter()
    decision = router.route(
        candidate_id="cand_1",
        requested_accuracy=0.95,
        sobol_sensitivity_score=0.8,
        cost_budget_available=5,
    )
    assert decision.assigned_tier == FidelityTier.FAST


def test_route_batch_tracks_remaining_budget():
    router = FidelityRouter()
    candidates = [
        {"candidate_id": "cand_1", "requested_accuracy": 0.95, "sobol_sensitivity_score": 0.1},
        {"candidate_id": "cand_2", "requested_accuracy": 0.55, "sobol_sensitivity_score": 0.1},
        {"candidate_id": "cand_3", "requested_accuracy": 0.10, "sobol_sensitivity_score": 0.1},
    ]
    decisions = router.route_batch(candidates, cost_budget_available=560)
    assert [d.assigned_tier for d in decisions] == [
        FidelityTier.COUPLED_CYCLE,
        FidelityTier.DESIGN,
        FidelityTier.FAST,
    ]


def test_routing_summary_uses_direct_inputs():
    router = FidelityRouter()
    router.route_batch(
        [
            {"candidate_id": "cand_1", "requested_accuracy": 0.95, "sobol_sensitivity_score": 0.1},
            {"candidate_id": "cand_2", "requested_accuracy": 0.55, "sobol_sensitivity_score": 0.9},
        ],
        cost_budget_available=10000,
    )
    summary = router.get_routing_summary()
    assert summary["total_routed"] == 2
    assert "average_requested_accuracy" in summary
    assert "average_sobol_sensitivity" in summary


def test_pool_allocation_and_stats():
    pool = AdaptiveSamplingPool(budget_ms=10000)
    decisions = [
        RoutingDecision("fast", FidelityTier.FAST, 0.1, 0.1, 10000, "test"),
        RoutingDecision("design", FidelityTier.DESIGN, 0.6, 0.1, 10000, "test"),
        RoutingDecision("coupled", FidelityTier.COUPLED_CYCLE, 0.9, 0.8, 10000, "test"),
    ]
    allocated, rejected = pool.allocate_batch(decisions)
    assert allocated == 3
    assert rejected == 0
    stats = pool.get_pool_stats()
    assert stats["fast"].candidate_count == 1
    assert stats["design"].candidate_count == 1
    assert stats["coupled_cycle"].candidate_count == 1


def test_pool_respects_budget():
    pool = AdaptiveSamplingPool(budget_ms=100)
    decisions = [
        RoutingDecision("a", FidelityTier.DESIGN, 0.6, 0.1, 100, "test"),
        RoutingDecision("b", FidelityTier.DESIGN, 0.6, 0.1, 100, "test"),
        RoutingDecision("c", FidelityTier.DESIGN, 0.6, 0.1, 100, "test"),
    ]
    allocated, rejected = pool.allocate_batch(decisions)
    assert allocated == 2
    assert rejected == 1


def test_coordinate_fidelity_escalation():
    router = FidelityRouter()
    pool = AdaptiveSamplingPool(budget_ms=10000)
    result = coordinate_fidelity_escalation(
        ga_candidates=[
            {"candidate_id": "cand_1", "requested_accuracy": 0.9, "sobol_sensitivity_score": 0.1},
            {"candidate_id": "cand_2", "requested_accuracy": 0.5, "sobol_sensitivity_score": 0.1},
        ],
        router=router,
        pool=pool,
        cost_budget_ms=10000,
    )
    assert result["allocation_stats"]["allocated"] == 2
    assert "routing_summary" in result
    assert "pool_summary" in result


def test_dataclass_serialization():
    decision = RoutingDecision(
        candidate_id="cand_1",
        assigned_tier=FidelityTier.DESIGN,
        requested_accuracy=0.75,
        sobol_sensitivity_score=0.5,
        cost_budget_available=5000,
        reasoning="test",
    )
    allocation = CandidateAllocation("cand_1", FidelityTier.DESIGN, 50)
    stats = PoolStats(FidelityTier.DESIGN, candidate_count=1, total_allocated_cost_ms=50)

    assert decision.as_dict()["assigned_tier"] == "design"
    assert decision.as_dict()["requested_accuracy"] == 0.75
    assert allocation.as_dict()["tier"] == "design"
    assert stats.as_dict()["tier"] == "design"
