"""Tests for Stage 7: Advanced Fidelity Coordination."""

import pytest
from liquid_engine_studio.fidelity_coordinator import (
    FidelityTier,
    FidelityRouter,
    AdaptiveSamplingPool,
    SurrogateRetrainingScheduler,
    RetrainingStats,
    RoutingDecision,
    CandidateAllocation,
    coordinate_fidelity_escalation,
)


class TestFidelityRouter:
    """Test FidelityRouter routing logic."""

    def test_router_creation(self):
        """Test router initialization."""
        router = FidelityRouter()
        assert router is not None
        assert router.enable_coupled_cycle is True
        assert len(router.routing_history) == 0

    def test_router_creation_disabled_coupled_cycle(self):
        """Test router with coupled-cycle disabled."""
        router = FidelityRouter(enable_coupled_cycle=False)
        assert router.enable_coupled_cycle is False

    def test_route_high_confidence(self):
        """High confidence → heuristic tier."""
        router = FidelityRouter()
        decision = router.route(
            candidate_id="cand_1",
            surrogate_confidence=0.90,
            sobol_sensitivity_score=0.3,
            cost_budget_available=10000,
        )
        assert decision.assigned_tier == FidelityTier.HEURISTIC
        assert "High confidence" in decision.reasoning

    def test_route_medium_confidence(self):
        """Medium confidence → concept tier."""
        router = FidelityRouter()
        decision = router.route(
            candidate_id="cand_1",
            surrogate_confidence=0.70,
            sobol_sensitivity_score=0.3,
            cost_budget_available=10000,
        )
        assert decision.assigned_tier == FidelityTier.CONCEPT
        assert "Medium confidence" in decision.reasoning

    def test_route_low_confidence_with_budget(self):
        """Low confidence + budget → coupled-cycle tier."""
        router = FidelityRouter()
        decision = router.route(
            candidate_id="cand_1",
            surrogate_confidence=0.50,
            sobol_sensitivity_score=0.3,
            cost_budget_available=10000,
        )
        assert decision.assigned_tier == FidelityTier.COUPLED_CYCLE
        assert "Low confidence" in decision.reasoning

    def test_route_low_confidence_no_budget(self):
        """Low confidence + no budget → concept tier (fallback)."""
        router = FidelityRouter()
        decision = router.route(
            candidate_id="cand_1",
            surrogate_confidence=0.50,
            sobol_sensitivity_score=0.3,
            cost_budget_available=10,
        )
        assert decision.assigned_tier == FidelityTier.CONCEPT

    def test_route_high_sensitivity(self):
        """High sensitivity → escalate to coupled-cycle."""
        router = FidelityRouter()
        decision = router.route(
            candidate_id="cand_1",
            surrogate_confidence=0.90,  # even high confidence
            sobol_sensitivity_score=0.80,  # high sensitivity overrides
            cost_budget_available=10000,
        )
        assert decision.assigned_tier == FidelityTier.COUPLED_CYCLE
        assert "High sensitivity" in decision.reasoning

    def test_route_high_sensitivity_no_budget(self):
        """High sensitivity but no budget → concept (fallback)."""
        router = FidelityRouter()
        decision = router.route(
            candidate_id="cand_1",
            surrogate_confidence=0.90,
            sobol_sensitivity_score=0.80,
            cost_budget_available=10,
        )
        assert decision.assigned_tier == FidelityTier.CONCEPT

    def test_route_disabled_coupled_cycle(self):
        """With coupled-cycle disabled, max tier is concept."""
        router = FidelityRouter(enable_coupled_cycle=False)
        decision = router.route(
            candidate_id="cand_1",
            surrogate_confidence=0.50,
            sobol_sensitivity_score=0.3,
            cost_budget_available=10000,
        )
        assert decision.assigned_tier == FidelityTier.CONCEPT

    def test_routing_history_tracking(self):
        """Routing history accumulates decisions."""
        router = FidelityRouter()
        router.route("cand_1", 0.90, 0.3, 10000)
        router.route("cand_2", 0.70, 0.3, 10000)
        router.route("cand_3", 0.50, 0.3, 10000)
        assert len(router.routing_history) == 3

    def test_route_batch(self):
        """Batch routing of multiple candidates."""
        router = FidelityRouter()
        candidates = [
            {"candidate_id": "cand_1", "surrogate_confidence": 0.90, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_2", "surrogate_confidence": 0.70, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_3", "surrogate_confidence": 0.50, "sobol_sensitivity_score": 0.3},
        ]
        decisions = router.route_batch(candidates, cost_budget_available=10000)
        assert len(decisions) == 3
        assert decisions[0].assigned_tier == FidelityTier.HEURISTIC
        assert decisions[1].assigned_tier == FidelityTier.CONCEPT
        assert decisions[2].assigned_tier == FidelityTier.COUPLED_CYCLE

    def test_route_batch_budget_exhaustion(self):
        """Budget exhaustion during batch routing."""
        router = FidelityRouter()
        # 24 candidates × 50ms (concept) = 1200ms, but budget is 500ms
        candidates = [
            {"candidate_id": f"cand_{i}", "surrogate_confidence": 0.70, "sobol_sensitivity_score": 0.3}
            for i in range(24)
        ]
        decisions = router.route_batch(candidates, cost_budget_available=500)
        assert len(decisions) == 24
        # Early decisions should be concept, later may be heuristic (due to budget tracking)

    def test_get_routing_summary(self):
        """Routing summary statistics."""
        router = FidelityRouter()
        candidates = [
            {"candidate_id": "cand_1", "surrogate_confidence": 0.90, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_2", "surrogate_confidence": 0.70, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_3", "surrogate_confidence": 0.50, "sobol_sensitivity_score": 0.8},
        ]
        router.route_batch(candidates, cost_budget_available=10000)
        summary = router.get_routing_summary()

        assert summary["total_routed"] == 3
        assert "by_tier" in summary
        assert "average_surrogate_confidence" in summary
        assert "average_sobol_sensitivity" in summary


class TestAdaptiveSamplingPool:
    """Test AdaptiveSamplingPool allocation logic."""

    def test_pool_creation(self):
        """Test pool initialization."""
        pool = AdaptiveSamplingPool(budget_ms=10000)
        assert pool.budget_ms == 10000
        assert len(pool.heuristic_queue) == 0
        assert len(pool.concept_queue) == 0
        assert len(pool.coupled_queue) == 0

    def test_allocate_heuristic_candidate(self):
        """Allocate candidate to heuristic pool."""
        pool = AdaptiveSamplingPool(budget_ms=10000)
        router = FidelityRouter()
        decision = router.route("cand_1", 0.90, 0.3, 10000)

        allocation = pool.allocate_candidate(decision)
        assert allocation is not None
        assert allocation.tier == FidelityTier.HEURISTIC
        assert allocation.estimated_cost_ms == 1
        assert len(pool.heuristic_queue) == 1

    def test_allocate_concept_candidate(self):
        """Allocate candidate to concept pool."""
        pool = AdaptiveSamplingPool(budget_ms=10000)
        router = FidelityRouter()
        decision = router.route("cand_1", 0.70, 0.3, 10000)

        allocation = pool.allocate_candidate(decision)
        assert allocation is not None
        assert allocation.tier == FidelityTier.CONCEPT
        assert allocation.estimated_cost_ms == 50
        assert len(pool.concept_queue) == 1

    def test_allocate_coupled_candidate(self):
        """Allocate candidate to coupled-cycle pool."""
        pool = AdaptiveSamplingPool(budget_ms=10000)
        router = FidelityRouter()
        decision = router.route("cand_1", 0.50, 0.3, 10000)

        allocation = pool.allocate_candidate(decision)
        assert allocation is not None
        assert allocation.tier == FidelityTier.COUPLED_CYCLE
        assert allocation.estimated_cost_ms == 500
        assert len(pool.coupled_queue) == 1

    def test_allocate_respects_budget(self):
        """Allocation respects total budget constraint."""
        pool = AdaptiveSamplingPool(budget_ms=100)  # Small budget
        router = FidelityRouter()

        # Try to allocate 3 concept candidates (3 × 50 = 150 ms > 100 ms budget)
        # With 0.50 confidence, router routes to concept (50ms per candidate)
        decisions = [
            router.route("cand_1", 0.50, 0.3, 100),
            router.route("cand_2", 0.50, 0.3, 100),
            router.route("cand_3", 0.50, 0.3, 100),
        ]

        allocated_count = 0
        for decision in decisions:
            if pool.allocate_candidate(decision):
                allocated_count += 1

        # Only 2 should fit: 2 × 50 = 100 ms exactly fits budget
        assert allocated_count == 2

    def test_allocate_batch(self):
        """Batch allocation of multiple candidates."""
        pool = AdaptiveSamplingPool(budget_ms=10000)
        router = FidelityRouter()

        candidates = [
            {"candidate_id": "cand_1", "surrogate_confidence": 0.90, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_2", "surrogate_confidence": 0.70, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_3", "surrogate_confidence": 0.50, "sobol_sensitivity_score": 0.3},
        ]
        decisions = router.route_batch(candidates, cost_budget_available=10000)
        allocated, rejected = pool.allocate_batch(decisions)

        assert allocated == 3
        assert rejected == 0
        assert len(pool.allocation_log) == 3

    def test_get_pool_stats(self):
        """Pool statistics computation."""
        pool = AdaptiveSamplingPool(budget_ms=10000)
        router = FidelityRouter()

        candidates = [
            {"candidate_id": "cand_1", "surrogate_confidence": 0.90, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_2", "surrogate_confidence": 0.70, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_3", "surrogate_confidence": 0.50, "sobol_sensitivity_score": 0.3},
        ]
        decisions = router.route_batch(candidates, cost_budget_available=10000)
        pool.allocate_batch(decisions)

        stats = pool.get_pool_stats()
        assert "heuristic" in stats
        assert "concept" in stats
        assert "coupled_cycle" in stats

        assert stats["heuristic"].candidate_count == 1
        assert stats["concept"].candidate_count == 1
        assert stats["coupled_cycle"].candidate_count == 1

    def test_get_summary(self):
        """Complete pool summary."""
        pool = AdaptiveSamplingPool(budget_ms=10000)
        router = FidelityRouter()

        candidates = [
            {"candidate_id": "cand_1", "surrogate_confidence": 0.90, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_2", "surrogate_confidence": 0.70, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_3", "surrogate_confidence": 0.50, "sobol_sensitivity_score": 0.3},
        ]
        decisions = router.route_batch(candidates, cost_budget_available=10000)
        pool.allocate_batch(decisions)

        summary = pool.get_summary()
        assert summary["budget_ms"] == 10000
        assert "total_estimated_cost_ms" in summary
        assert "budget_utilization_percent" in summary
        assert "by_pool" in summary


class TestSurrogateRetrainingScheduler:
    """Test SurrogateRetrainingScheduler logic."""

    def test_scheduler_creation(self):
        """Test scheduler initialization."""
        scheduler = SurrogateRetrainingScheduler()
        assert scheduler.retrain_threshold == 10
        assert scheduler.retrain_interval_generations == 1
        assert scheduler.generation_count == 0
        assert len(scheduler.training_data_pool) == 0

    def test_add_ga_results(self):
        """Add GA results to training pool."""
        scheduler = SurrogateRetrainingScheduler()
        results = [
            {
                "design_state": {"mixture_ratio": 2.0, "burn_time_seconds": 20.0},
                "best_score": 0.75,
                "derived": {"dry_mass_index": 50.0, "thermal_margin_index": 60.0},
            },
            {
                "design_state": {"mixture_ratio": 2.1, "burn_time_seconds": 21.0},
                "best_score": 0.78,
                "derived": {"dry_mass_index": 51.0, "thermal_margin_index": 61.0},
            },
        ]
        scheduler.add_ga_results(results)

        assert len(scheduler.training_data_pool) == 2
        assert scheduler.training_data_pool[0]["observed_score"] == 0.75
        assert scheduler.training_data_pool[1]["observed_mass"] == 51.0

    def test_maybe_retrain_below_threshold(self):
        """Retraining skipped when below threshold."""
        scheduler = SurrogateRetrainingScheduler(retrain_threshold=10)
        results = [
            {
                "design_state": {"mixture_ratio": 2.0, "burn_time_seconds": 20.0},
                "best_score": 0.75,
                "derived": {"dry_mass_index": 50.0, "thermal_margin_index": 60.0},
            },
        ]
        scheduler.add_ga_results(results)

        stats = scheduler.maybe_retrain(ml_surrogate_model=None)
        assert stats.retrained is False
        assert "pool=1" in stats.notes

    def test_maybe_retrain_above_threshold_no_model(self):
        """Retraining skipped when no model provided."""
        scheduler = SurrogateRetrainingScheduler(retrain_threshold=5)
        results = [
            {
                "design_state": {"mixture_ratio": f"2.{i}", "burn_time_seconds": f"20.{i}"},
                "best_score": float(0.75 + i * 0.01),
                "derived": {"dry_mass_index": 50.0 + i, "thermal_margin_index": 60.0 + i},
            }
            for i in range(10)
        ]
        scheduler.add_ga_results(results)

        stats = scheduler.maybe_retrain(ml_surrogate_model=None)
        assert stats.retrained is False
        assert "pool=10" not in stats.notes or "threshold=5" in stats.notes

    def test_get_retraining_summary(self):
        """Retraining summary statistics."""
        scheduler = SurrogateRetrainingScheduler()
        results = [
            {
                "design_state": {"mixture_ratio": 2.0, "burn_time_seconds": 20.0},
                "best_score": 0.75,
                "derived": {"dry_mass_index": 50.0, "thermal_margin_index": 60.0},
            },
        ]
        scheduler.add_ga_results(results)
        scheduler.maybe_retrain(ml_surrogate_model=None)

        summary = scheduler.get_retraining_summary()
        assert summary["generation_count"] >= 1
        assert "total_retrained" in summary
        assert "training_data_pool_size" in summary


class TestFidelityCoordinationOrchestration:
    """Test full fidelity coordination orchestration."""

    def test_coordinate_fidelity_escalation(self):
        """Full end-to-end coordination."""
        router = FidelityRouter()
        pool = AdaptiveSamplingPool(budget_ms=10000)

        candidates = [
            {"candidate_id": "cand_1", "surrogate_confidence": 0.90, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_2", "surrogate_confidence": 0.70, "sobol_sensitivity_score": 0.3},
            {"candidate_id": "cand_3", "surrogate_confidence": 0.50, "sobol_sensitivity_score": 0.3},
        ]

        result = coordinate_fidelity_escalation(
            ga_candidates=candidates,
            router=router,
            pool=pool,
            cost_budget_ms=10000,
        )

        assert "routing_decisions" in result
        assert "routing_summary" in result
        assert "allocation_stats" in result
        assert "pool_summary" in result

        assert result["allocation_stats"]["allocated"] == 3
        assert result["allocation_stats"]["rejected"] == 0

    def test_coordinate_with_budget_exhaustion(self):
        """Coordination with limited budget."""
        router = FidelityRouter()
        pool = AdaptiveSamplingPool(budget_ms=500)  # Very tight budget

        candidates = [
            {"candidate_id": f"cand_{i}", "surrogate_confidence": 0.70, "sobol_sensitivity_score": 0.3}
            for i in range(24)
        ]

        result = coordinate_fidelity_escalation(
            ga_candidates=candidates,
            router=router,
            pool=pool,
            cost_budget_ms=500,
        )

        assert result["allocation_stats"]["rejected"] > 0
        assert result["pool_summary"]["budget_utilization_percent"] <= 100.0


class TestDataClassSerialization:
    """Test serialization of dataclasses."""

    def test_routing_decision_as_dict(self):
        """RoutingDecision serializes to dict."""
        decision = RoutingDecision(
            candidate_id="cand_1",
            assigned_tier=FidelityTier.CONCEPT,
            surrogate_confidence=0.75,
            sobol_sensitivity_score=0.5,
            cost_budget_available=5000,
            reasoning="test",
        )
        d = decision.as_dict()
        assert d["candidate_id"] == "cand_1"
        assert d["assigned_tier"] == "concept"
        assert d["surrogate_confidence"] == 0.75

    def test_candidate_allocation_as_dict(self):
        """CandidateAllocation serializes to dict."""
        allocation = CandidateAllocation(
            candidate_id="cand_1",
            tier=FidelityTier.CONCEPT,
            estimated_cost_ms=50,
        )
        d = allocation.as_dict()
        assert d["candidate_id"] == "cand_1"
        assert d["tier"] == "concept"
        assert d["estimated_cost_ms"] == 50

    def test_retraining_stats_as_dict(self):
        """RetrainingStats serializes to dict."""
        stats = RetrainingStats(
            retrained=True,
            previous_sample_count=10,
            new_sample_count=15,
            training_time_ms=100.5,
            improvement_estimate=0.1,
            notes="test",
        )
        d = stats.as_dict()
        assert d["retrained"] is True
        assert d["previous_sample_count"] == 10
        assert d["training_time_ms"] == 100.5


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_full_ga_generation_workflow(self):
        """Simulate one full GA generation with fidelity coordination."""
        router = FidelityRouter()
        pool = AdaptiveSamplingPool(budget_ms=5000)
        scheduler = SurrogateRetrainingScheduler(retrain_threshold=5)

        # Simulate GA generation with 24 candidates
        candidates = [
            {
                "candidate_id": f"cand_{i}",
                "surrogate_confidence": 0.55 + (i / 24) * 0.35,
                "sobol_sensitivity_score": 0.3 + (i % 3) * 0.2,
            }
            for i in range(24)
        ]

        # Coordinate fidelity for all candidates
        result = coordinate_fidelity_escalation(
            ga_candidates=candidates,
            router=router,
            pool=pool,
            cost_budget_ms=5000,
        )

        # Add synthetic GA results
        ga_results = [
            {
                "design_state": {"mixture_ratio": 2.0 + i * 0.05, "burn_time_seconds": 20.0},
                "best_score": 0.70 + (i / 24) * 0.20,
                "derived": {"dry_mass_index": 50.0 + i, "thermal_margin_index": 60.0 + i * 0.5},
            }
            for i in range(10)
        ]
        scheduler.add_ga_results(ga_results)

        # Check retraining decision
        scheduler.maybe_retrain(ml_surrogate_model=None)

        assert result["allocation_stats"]["total"] == 24
        assert result["pool_summary"]["budget_ms"] == 5000
        assert len(scheduler.training_data_pool) == 10

    def test_multiple_generations_retraining(self):
        """Test retraining across multiple GA generations."""
        scheduler = SurrogateRetrainingScheduler(retrain_threshold=5, retrain_interval_generations=2)

        # Generation 1: Add 5 results, shouldn't retrain yet
        results_gen1 = [
            {
                "design_state": {"mixture_ratio": 2.0, "burn_time_seconds": 20.0},
                "best_score": 0.75,
                "derived": {"dry_mass_index": 50.0, "thermal_margin_index": 60.0},
            }
            for _ in range(5)
        ]
        scheduler.add_ga_results(results_gen1)
        stats1 = scheduler.maybe_retrain()
        assert stats1.retrained is False  # Threshold met but interval not

        # Generation 2: Add more results, still shouldn't retrain (interval not elapsed)
        results_gen2 = [
            {
                "design_state": {"mixture_ratio": 2.1, "burn_time_seconds": 21.0},
                "best_score": 0.77,
                "derived": {"dry_mass_index": 51.0, "thermal_margin_index": 61.0},
            }
            for _ in range(5)
        ]
        scheduler.add_ga_results(results_gen2)
        stats2 = scheduler.maybe_retrain()
        assert stats2.retrained is False

        # Generation 3: Now interval has elapsed, should be ready to retrain (but no model)
        results_gen3 = [
            {
                "design_state": {"mixture_ratio": 2.2, "burn_time_seconds": 22.0},
                "best_score": 0.79,
                "derived": {"dry_mass_index": 52.0, "thermal_margin_index": 62.0},
            }
            for _ in range(5)
        ]
        scheduler.add_ga_results(results_gen3)
        scheduler.maybe_retrain()
        # Would retrain if model provided, but skips due to None

        assert len(scheduler.training_data_pool) == 15
        assert scheduler.generation_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


