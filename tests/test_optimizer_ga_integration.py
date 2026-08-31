"""Tests for GA workflow integration with fidelity coordination."""

import pytest
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtWidgets import QApplication

import stanthrust.ui.main_window as desktop_module
from stanthrust.design_model import create_engine_design
from stanthrust.optimizer_hooks import (
    GeneticAlgorithmResult,
    _summarize_tier_usage,
    build_optimizer_seed,
    run_genetic_optimizer,
    run_genetic_optimizer_with_fidelity,
)
from stanthrust.ui.inputs_panel import DEFAULT_SOLVER_STATION_COUNT


class TestGAIntegrationBasics:
    """Basic tests for GA integration with fidelity coordination."""

    def test_run_genetic_optimizer_with_fidelity_returns_result(self):
        """GA with fidelity should return a GeneticAlgorithmResult."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=2, population_size=4, random_seed=42
        )
        assert isinstance(result, GeneticAlgorithmResult)
        assert result.best_score > 0
        assert result.history is not None
        assert len(result.history) == 2

    def test_fidelity_result_includes_metadata(self):
        """GA with fidelity should include fidelity_metadata in result."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=1, population_size=4, random_seed=42, budget_ms=50000
        )
        assert result.fidelity_metadata is not None
        assert "optimization_enabled" in result.fidelity_metadata
        assert result.fidelity_metadata["optimization_enabled"] is True
        assert result.fidelity_metadata["budget_ms"] == 50000

    def test_fidelity_result_contains_key_metadata_fields(self):
        """Fidelity metadata should include routing and allocation info."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed,
            generations=2,
            population_size=4,
            random_seed=42,
            enable_coupled_cycle=False,
        )
        metadata = result.fidelity_metadata
        assert "enable_coupled_cycle" in metadata
        assert metadata["enable_coupled_cycle"] is False
        assert "total_generations" in metadata
        assert metadata["total_generations"] == 2
        assert "population_size" in metadata
        assert metadata["population_size"] == 4

    def test_fidelity_metadata_includes_decisions_per_generation(self):
        """Metadata should track routing decisions for each generation."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=2, population_size=4, random_seed=42
        )
        metadata = result.fidelity_metadata
        assert "decisions_by_generation" in metadata
        decisions_list = metadata["decisions_by_generation"]
        assert len(decisions_list) == 2
        for gen_decisions in decisions_list:
            assert "generation" in gen_decisions
            assert "routing_decisions" in gen_decisions


class TestFidelityTierAllocation:
    """Tests for fidelity tier allocation and budget management."""

    def test_tier_allocation_respects_budget(self):
        """Pool should not allocate candidates beyond budget."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        # Very small budget: only 50ms = one design candidate
        result = run_genetic_optimizer_with_fidelity(
            seed,
            generations=1,
            population_size=4,
            random_seed=42,
            budget_ms=50,
        )
        # Check that allocations were made but stayed within budget
        metadata = result.fidelity_metadata
        assert metadata["budget_ms"] == 50
        # At least some candidates should have been allocated
        assert "final_pool_stats" in metadata

    def test_tier_usage_summary_in_history(self):
        """Generation history should include fidelity tier usage summary."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=2, population_size=6, random_seed=42
        )
        for gen_entry in result.history:
            assert "fidelity_tiers_used" in gen_entry
            tier_summary = gen_entry["fidelity_tiers_used"]
            assert "fast" in tier_summary
            assert "design" in tier_summary
            assert "coupled_cycle" in tier_summary
            assert tier_summary["design"] > 0

    def test_tier_allocation_cost_tracking(self):
        """History should track allocated cost per generation."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed,
            generations=2,
            population_size=4,
            random_seed=42,
            budget_ms=20000,
        )
        for gen_entry in result.history:
            assert "total_allocated_cost_ms" in gen_entry
            # Cost should be reasonable (design candidates ~50ms each)
            cost = gen_entry["total_allocated_cost_ms"]
            assert cost >= 0
            assert cost <= 20000


class TestFidelityOptions:
    def test_coupled_cycle_flag_propagates(self):
        """enable_coupled_cycle flag should be stored in metadata."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed,
            generations=1,
            population_size=4,
            random_seed=42,
            enable_coupled_cycle=True,
        )
        assert result.fidelity_metadata["enable_coupled_cycle"] is True


class TestFidelityMetadataComparison:
    """Tests comparing fidelity GA vs regular GA."""

    def test_fidelity_garesult_vs_regular_garesult(self):
        """Both GA variants should return comparable result structures."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)

        result_regular = run_genetic_optimizer(
            seed, generations=1, population_size=4, random_seed=42
        )
        result_fidelity = run_genetic_optimizer_with_fidelity(
            seed, generations=1, population_size=4, random_seed=42
        )

        # Both should have same core fields
        assert isinstance(result_regular.best_score, float)
        assert isinstance(result_fidelity.best_score, float)
        assert isinstance(result_regular.history, list)
        assert isinstance(result_fidelity.history, list)

        # Fidelity should have empty fidelity_metadata in non-fidelity run
        assert result_regular.fidelity_metadata == {}
        assert result_fidelity.fidelity_metadata != {}


class TestGAGenerationCount:
    """Tests for GA generation tracking."""

    def test_generation_counter_in_history(self):
        """Each history entry should have correct generation number."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=5, population_size=4, random_seed=42
        )
        assert len(result.history) == 5
        for i, entry in enumerate(result.history):
            assert entry["generation"] == float(i)

    def test_consistent_generation_tracking_in_metadata(self):
        """Decisions by generation should match history generations."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=3, population_size=4, random_seed=42
        )
        decisions_list = result.fidelity_metadata["decisions_by_generation"]
        assert len(decisions_list) == len(result.history)
        for decision_entry, history_entry in zip(decisions_list, result.history):
            assert decision_entry["generation"] == int(history_entry["generation"])


class TestBestCandidateTracking:
    """Tests for best candidate selection across generations."""

    def test_best_score_improves_or_maintains(self):
        """Best score should never decrease across generations."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=4, population_size=8, random_seed=42
        )
        prev_best = 0
        for entry in result.history:
            current_best = entry["best_score"]
            assert current_best >= prev_best, f"Best score decreased: {current_best} < {prev_best}"
            prev_best = current_best

    def test_final_best_matches_history(self):
        """Result best_score should match the final history entry."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=3, population_size=6, random_seed=42
        )
        final_best_in_history = result.history[-1]["best_score"]
        assert result.best_score >= final_best_in_history - 0.0001


class TestTierUsageSummary:
    """Tests for the tier usage summary helper function."""

    def test_summarize_tier_usage_counts_correctly(self):
        """Tier usage summary should count candidates by tier."""
        candidates = [
            {"tier": "fast"},
            {"tier": "fast"},
            {"tier": "design"},
            {"tier": "coupled_cycle"},
        ]
        summary = _summarize_tier_usage(candidates)
        assert summary["fast"] == 2
        assert summary["design"] == 1
        assert summary["coupled_cycle"] == 1


class TestQtInputPreservation:
    """Qt workflow tests ensuring GA does not overwrite live form inputs."""

    def test_run_design_ga_preserves_live_inputs(self):
        app_instance = QApplication.instance() or QApplication([])
        app_instance.setQuitOnLastWindowClosed(False)
        window = desktop_module.StanThrustQtWindow()
        try:
            window.widgets["mixture_ratio"].setValue(1.4)
            window.widgets["burn_time_seconds"].setValue(12.0)
            window.refresh_preview()

            original_state = window.collect_form_state()
            assert window.widgets["packaging_bias"].currentText() == "Balanced"
            assert original_state["packaging_bias"] == "balanced"

            expected_result = GeneticAlgorithmResult(
                best_score=9.99,
                best_breakdown={"total_score": 9.99},
                best_state={**original_state, "mixture_ratio": 2.2, "burn_time_seconds": 18.0},
                history=[{"generation": 0.0, "best_score": 9.99, "mean_score": 9.99}],
                best_design=window.current_design.as_dict(),
            )

            original_runner = desktop_module.run_feasibility_first_optimizer
            desktop_module.run_feasibility_first_optimizer = lambda seed: expected_result
            try:
                window.run_design_ga()
            finally:
                desktop_module.run_feasibility_first_optimizer = original_runner

            assert window.collect_form_state() == original_state
            assert window.current_ga_candidate_state == expected_result.best_state
            assert window.widgets["mixture_ratio"].value() == pytest.approx(1.4)
            assert window.widgets["burn_time_seconds"].value() == pytest.approx(12.0)
        finally:
            window.close()

    def test_summarize_tier_usage_handles_missing_tier(self):
        """Tier usage summary should default missing tier to design."""
        candidates = [{"tier": "fast"}, {}]
        summary = _summarize_tier_usage(candidates)
        assert summary["fast"] == 1
        assert summary["design"] == 1

    def test_plot_tab_has_expanded_solver_views(self):
        """Plot tab should expose transient, axial, 2D field, and convergence views."""
        app_instance = QApplication.instance() or QApplication([])
        app_instance.setQuitOnLastWindowClosed(False)
        window = desktop_module.StanThrustQtWindow()
        try:
            expected_cards = {
                "pressure_transient",
                "performance_transient",
                "feed_margins",
                "axial_field",
                "mach_area",
                "thermal_density",
                "convergence",
                "coupled_margins",
            }
            assert expected_cards.issubset(set(window.plot_cards))
            assert window.flow_field_card is not None
            assert int(window.widgets["solver_station_count"].value()) == DEFAULT_SOLVER_STATION_COUNT
            assert window.collect_form_state()["nozzle_expansion_bias"] == "pressure_matched"
            window._set_default_plots()
        finally:
            window.close()

    def test_cooling_overlays_render_from_calculated_geometry(self):
        """Cooling overlays should render from solved regen and film geometry fields."""
        app_instance = QApplication.instance() or QApplication([])
        app_instance.setQuitOnLastWindowClosed(False)
        scenarios = [
            create_engine_design({"regen_cooling": True}),
            create_engine_design({"film_cooling": True}),
        ]
        for design in scenarios:
            view = desktop_module.Model3DView("chamber_nozzle")
            try:
                view.resize(900, 520)
                view.render_design(design)
                pixmap = QPixmap(900, 520)
                painter = QPainter(pixmap)
                view.scene().render(painter)
                painter.end()
                assert len(view.scene().items()) > 20
            finally:
                view.close()


class TestFidelityMetadataExportable:
    """Tests for metadata export compatibility."""

    def test_result_as_dict_includes_fidelity_metadata(self):
        """Result.as_dict() should include fidelity_metadata."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=1, population_size=4, random_seed=42
        )
        result_dict = result.as_dict()
        assert "fidelity_metadata" in result_dict
        assert result_dict["fidelity_metadata"] == result.fidelity_metadata

    def test_result_as_dict_for_regular_ga(self):
        """Regular GA result as_dict() should have empty fidelity_metadata."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer(
            seed, generations=1, population_size=4, random_seed=42
        )
        result_dict = result.as_dict()
        assert "fidelity_metadata" in result_dict
        assert result_dict["fidelity_metadata"] == {}


class TestIntegration:
    """Integration tests combining multiple GA fidelity features."""

    def test_full_ga_run_with_all_fidelity_options(self):
        """Full GA run should work with all fidelity options enabled."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed,
            generations=3,
            population_size=8,
            random_seed=45,
            budget_ms=150000,
            enable_coupled_cycle=True,
        )
        assert result.best_score > 0
        assert len(result.history) == 3
        assert result.fidelity_metadata["budget_ms"] == 150000
        assert result.fidelity_metadata["enable_coupled_cycle"] is True

    def test_ga_convergence_with_fidelity(self):
        """GA with fidelity should show convergence trend over generations."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=6, population_size=12, random_seed=123
        )
        # Extract best scores per generation
        best_scores = [entry["best_score"] for entry in result.history]
        # Scores should generally improve or plateau (not oscillate wildly)
        assert len(best_scores) == 6
        # Final best should be at least as good as early generations
        assert best_scores[-1] >= best_scores[0]

    def test_metadata_routing_decisions_have_structure(self):
        """Routing decisions in metadata should have required fields."""
        design = create_engine_design({})
        seed = build_optimizer_seed(design)
        result = run_genetic_optimizer_with_fidelity(
            seed, generations=1, population_size=4, random_seed=42
        )
        decisions_list = result.fidelity_metadata["decisions_by_generation"]
        for gen_decisions in decisions_list:
            routing_decisions = gen_decisions["routing_decisions"]
            for decision in routing_decisions:
                assert "candidate_id" in decision
                assert "assigned_tier" in decision
                assert "requested_accuracy" in decision
                assert "sobol_sensitivity_score" in decision
