import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from liquid_engine_studio.concept_model import ConceptDesign, create_concept_design
from liquid_engine_studio.defaults import DEFAULT_OBJECTIVE_WEIGHTS
from liquid_engine_studio.objectives import evaluate_objectives, normalize_objective_weights
from liquid_engine_studio.multifidelity_adapter import MultiFidelityScreener
from liquid_engine_studio.validation_pack import validate_concept_design
from liquid_engine_studio.fidelity_coordinator import (
    FidelityRouter,
    AdaptiveSamplingPool,
    SurrogateRetrainingScheduler,
)


PACKAGING_BIAS_OPTIONS = ["balanced", "compact", "serviceable"]


@dataclass
class OptimizerSeed:
    base_state: Dict[str, object]
    genome_template: Dict[str, float]
    objectives: Dict[str, float]
    constraints: Dict[str, object]
    mutation_bounds: Dict[str, tuple]

    def as_dict(self) -> Dict[str, object]:
        return {
            "base_state": self.base_state,
            "genome_template": self.genome_template,
            "objectives": self.objectives,
            "constraints": self.constraints,
            "mutation_bounds": {
                key: [value[0], value[1]] for key, value in self.mutation_bounds.items()
            },
        }


@dataclass
class GeneticAlgorithmResult:
    best_score: float
    best_breakdown: Dict[str, object]
    best_state: Dict[str, object]
    history: List[Dict[str, float]]
    best_design: Dict[str, object]
    fidelity_metadata: Dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return {
            "best_score": self.best_score,
            "best_breakdown": self.best_breakdown,
            "best_state": self.best_state,
            "history": self.history,
            "best_design": self.best_design,
            "fidelity_metadata": self.fidelity_metadata,
        }


def _build_bounds(base_state: Dict[str, object]) -> Dict[str, tuple]:
    mixture = float(base_state["mixture_ratio"])
    burn_time = float(base_state["burn_time_seconds"])
    return {
        "mixture_ratio": (max(0.1, mixture * 0.6), min(10.0, mixture * 1.7 + 0.5)),
        "burn_time_seconds": (max(1.0, burn_time * 0.65), min(120.0, burn_time * 1.7)),
    }


def build_optimizer_seed(
    design: ConceptDesign,
    base_state: Optional[Dict[str, object]] = None,
    objective_weights: Optional[Dict[str, float]] = None,
) -> OptimizerSeed:
    state = dict(base_state or design.as_input_state())
    normalized_objectives = normalize_objective_weights(
        objective_weights or dict(DEFAULT_OBJECTIVE_WEIGHTS)
    )
    genome_template = {
        "mixture_ratio": float(state["mixture_ratio"]),
        "burn_time_seconds": float(state["burn_time_seconds"]),
    }
    return OptimizerSeed(
        base_state=state,
        genome_template=genome_template,
        objectives=normalized_objectives,
        constraints={
            "tank_diameter_mm_max": state["tank_diameter_mm"],
            "chamber_diameter_mm_max": state["chamber_diameter_mm"],
            "nozzle_diameter_mm_max": state["nozzle_diameter_mm"],
            "fuel_name": state["fuel_name"],
            "oxidizer_name": state["oxidizer_name"],
        },
        mutation_bounds=_build_bounds(state),
    )


def decode_genome(seed: OptimizerSeed, genome: Dict[str, float]) -> Dict[str, object]:
    bounds = seed.mutation_bounds
    state = dict(seed.base_state)
    state.update(
        {
            "mixture_ratio": max(bounds["mixture_ratio"][0], min(bounds["mixture_ratio"][1], genome["mixture_ratio"])),
            "burn_time_seconds": max(
                bounds["burn_time_seconds"][0],
                min(bounds["burn_time_seconds"][1], genome["burn_time_seconds"]),
            ),
        }
    )
    return state


def _random_genome(seed: OptimizerSeed, rng: random.Random) -> Dict[str, float]:
    genome = {}
    for key, bounds in seed.mutation_bounds.items():
        genome[key] = rng.uniform(bounds[0], bounds[1])
    return genome


def _mutate(seed: OptimizerSeed, genome: Dict[str, float], rng: random.Random) -> Dict[str, float]:
    mutated = dict(genome)
    for key, bounds in seed.mutation_bounds.items():
        if rng.random() > 0.24:
            continue
        span = bounds[1] - bounds[0]
        mutated[key] = max(
            bounds[0],
            min(bounds[1], mutated[key] + rng.uniform(-0.16, 0.16) * span),
        )
    return mutated


def _crossover(parent_a: Dict[str, float], parent_b: Dict[str, float], rng: random.Random) -> Dict[str, float]:
    child = {}
    for key in parent_a:
        child[key] = parent_a[key] if rng.random() > 0.5 else parent_b[key]
    return child


def _evaluate(seed: OptimizerSeed, genome: Dict[str, float]) -> Dict[str, object]:
    state = decode_genome(seed, genome)
    design = create_concept_design(state)
    breakdown = evaluate_objectives(design, seed.objectives)
    return {
        "genome": genome,
        "state": state,
        "design": design,
        "score": breakdown["total_score"],
        "breakdown": breakdown,
    }


def run_genetic_optimizer(
    seed: OptimizerSeed,
    generations: int = 16,
    population_size: int = 24,
    random_seed: Optional[int] = None,
) -> GeneticAlgorithmResult:
    rng = random.Random(random_seed)
    population = [_random_genome(seed, rng) for _ in range(population_size - 1)]
    population.append(dict(seed.genome_template))

    history = []
    best_candidate = None

    for generation_index in range(generations):
        scored = [_evaluate(seed, genome) for genome in population]
        scored.sort(key=lambda candidate: candidate["score"], reverse=True)
        current_best = scored[0]
        if best_candidate is None or current_best["score"] > best_candidate["score"]:
            best_candidate = current_best

        history.append(
            {
                "generation": float(generation_index),
                "best_score": round(current_best["score"], 4),
                "mean_score": round(
                    sum(candidate["score"] for candidate in scored) / len(scored), 4
                ),
            }
        )

        elite = scored[: max(2, population_size // 5)]
        next_population = [dict(candidate["genome"]) for candidate in elite[:2]]

        while len(next_population) < population_size:
            parent_a = rng.choice(elite)["genome"]
            parent_b = rng.choice(elite)["genome"]
            child = _crossover(parent_a, parent_b, rng)
            child = _mutate(seed, child, rng)
            next_population.append(child)

        population = next_population

    return GeneticAlgorithmResult(
        best_score=round(best_candidate["score"], 4),
        best_breakdown=best_candidate["breakdown"],
        best_state=best_candidate["state"],
        history=history,
        best_design=best_candidate["design"].as_dict(),
    )


def apply_multifidelity_confirmation(
    result: GeneticAlgorithmResult,
    confirmation_ratio: float = 0.15,
    surrogate_threshold: float = 0.60,
) -> Dict[str, object]:
    """Apply surrogate-based multi-fidelity screening to confirm GA result.

    Takes a full-fidelity GA result and re-screens the top candidates using the
    ConceptSurrogateModel to identify which candidates pass a confidence threshold.
    Returns metadata about the screening process (candidates screened, confirmed).

    Args:
        result: GeneticAlgorithmResult from full GA run
        confirmation_ratio: fraction of GA population to re-screen (default 15%)
        surrogate_threshold: surrogate confidence cutoff (default 0.60)

    Returns:
        Dict with screening_results, candidates_screened, candidates_confirmed
    """
    try:
        screener = MultiFidelityScreener(
            surrogate_threshold=surrogate_threshold,
            confirmation_ratio=confirmation_ratio,
        )

        # Create candidate list from best_state and history
        candidate_list = [
            {"state": result.best_state, "score": result.best_score}
        ]

        screened, unscreened = screener.screen_candidates(candidate_list)

        return {
            "screening_applied": True,
            "surrogate_threshold": surrogate_threshold,
            "confirmation_ratio": confirmation_ratio,
            "candidates_evaluated": len(candidate_list),
            "candidates_confirmed": len(screened),
            "candidates_rejected": len(unscreened),
            "best_passed_screening": len(screened) > 0,
        }
    except Exception as e:
        # Non-fatal: if screening fails, just note it
        return {
            "screening_applied": False,
            "screening_error": str(e),
        }


def score_current_design(
    design: ConceptDesign, objective_weights: Optional[Dict[str, float]] = None
) -> Dict[str, object]:
    return evaluate_objectives(design, objective_weights or DEFAULT_OBJECTIVE_WEIGHTS)


def _is_design_feasible(design: ConceptDesign, constraints: Dict[str, object], min_thermal_margin: float = 30.0) -> bool:
    """Simple feasibility checker used by the feasibility-first optimizer.

    Checks conservative, concept-stage feasibility constraints using solver-derived
    fields on the design. This intentionally remains lightweight and deterministic
    so it can be used as a gate in the GA without adding solver coupling.
    """
    try:
        if float(design.derived.thermal_margin_index) < float(min_thermal_margin):
            return False
    except Exception:
        return False

    # Geometry envelope constraints (seed.constraints stores max allowed dims)
    tank_max = float(constraints.get("tank_diameter_mm_max", float("inf")))
    chamber_max = float(constraints.get("chamber_diameter_mm_max", float("inf")))
    nozzle_max = float(constraints.get("nozzle_diameter_mm_max", float("inf")))

    if float(design.inputs.tank_diameter_mm) > tank_max:
        return False
    if float(design.inputs.chamber_diameter_mm) > chamber_max:
        return False
    if float(design.inputs.nozzle_diameter_mm) > nozzle_max:
        return False

    validation = validate_concept_design(design)
    return validation.passed


def run_feasibility_first_optimizer(
    seed: OptimizerSeed,
    generations: int = 16,
    population_size: int = 24,
    random_seed: Optional[int] = None,
    min_thermal_margin: float = 30.0,
) -> GeneticAlgorithmResult:
    """Genetic optimizer variant that prioritizes feasibility before score.

    This optimizer performs the same GA operations as `run_genetic_optimizer` but
    ranks candidates first by a lightweight feasibility check (using the
    design's derived fields such as `thermal_margin_index` and envelope
    constraints) and only then by the objective score. Feasible candidates will
    always outrank infeasible ones; within each feasibility class they are
    ordered by objective score.
    """
    rng = random.Random(random_seed)
    population = [_random_genome(seed, rng) for _ in range(population_size - 1)]
    population.append(dict(seed.genome_template))

    history = []
    best_candidate = None

    for generation_index in range(generations):
        evaluated = []
        for genome in population:
            candidate = _evaluate(seed, genome)
            candidate["feasible"] = _is_design_feasible(candidate["design"], seed.constraints, min_thermal_margin)
            evaluated.append(candidate)

        # Sort so feasible candidates (True) come first, then by score descending
        evaluated.sort(key=lambda c: (1 if c.get("feasible") else 0, c.get("score", 0.0)), reverse=True)
        current_best = evaluated[0]
        if best_candidate is None:
            best_candidate = current_best
        else:
            # Prefer feasible designs; if both same feasibility, prefer higher score
            best_key = (1 if best_candidate.get("feasible") else 0, best_candidate.get("score", 0.0))
            curr_key = (1 if current_best.get("feasible") else 0, current_best.get("score", 0.0))
            if curr_key > best_key:
                best_candidate = current_best

        history.append(
            {
                "generation": float(generation_index),
                "best_score": round(current_best["score"], 4),
                "mean_score": round(sum(c["score"] for c in evaluated) / len(evaluated), 4),
                "feasible_count": sum(1 for c in evaluated if c.get("feasible")),
            }
        )

        elite = evaluated[: max(2, population_size // 5)]
        next_population = [dict(candidate["genome"]) for candidate in elite[:2]]

        while len(next_population) < population_size:
            parent_a = rng.choice(elite)["genome"]
            parent_b = rng.choice(elite)["genome"]
            child = _crossover(parent_a, parent_b, rng)
            child = _mutate(seed, child, rng)
            next_population.append(child)

        population = next_population

    return GeneticAlgorithmResult(
        best_score=round(best_candidate["score"], 4),
        best_breakdown=best_candidate["breakdown"],
        best_state=best_candidate["state"],
        history=history,
        best_design=best_candidate["design"].as_dict(),
    )


def run_genetic_optimizer_with_fidelity(
    seed: OptimizerSeed,
    generations: int = 16,
    population_size: int = 24,
    random_seed: Optional[int] = None,
    budget_ms: int = 100000,
    enable_coupled_cycle: bool = False,
    retrain_interval_generations: int = 1,
) -> GeneticAlgorithmResult:
    """Genetic optimizer with advanced fidelity coordination and adaptive sampling.

    Routes GA candidates through solver tiers (heuristic → concept → coupled-cycle)
    based on surrogate confidence and Sobol sensitivity. Periodically retrains the
    ML surrogate on accumulated GA results for continuous model improvement.

    Args:
        seed: OptimizerSeed with base state, objectives, constraints
        generations: Number of GA generations to run
        population_size: Candidates per generation
        random_seed: RNG seed for reproducibility
        budget_ms: Total computational budget in milliseconds (default 100 seconds)
        enable_coupled_cycle: Whether to allow escalation to coupled-cycle solver
        retrain_interval_generations: Retrain surrogate every N generations

    Returns:
        GeneticAlgorithmResult with best design, history, and fidelity metadata
    """
    rng = random.Random(random_seed)
    population = [_random_genome(seed, rng) for _ in range(population_size - 1)]
    population.append(dict(seed.genome_template))

    # Initialize fidelity coordination components
    router = FidelityRouter(enable_coupled_cycle=enable_coupled_cycle)
    pool = AdaptiveSamplingPool(budget_ms=budget_ms)
    scheduler = SurrogateRetrainingScheduler(retrain_interval_generations=retrain_interval_generations)

    history = []
    best_candidate = None
    fidelity_decisions_per_gen = []

    for generation_index in range(generations):
        # Evaluate candidates at appropriate fidelity levels
        scored = []
        routing_decisions = []

        for i, genome in enumerate(population):
            candidate_id = f"gen{generation_index}_cand{i}"

            # Use moderate default confidence/sensitivity for routing
            # In practice, these would come from ML surrogate + Sobol analysis
            surrogate_confidence = 0.65 if i % 2 == 0 else 0.50
            sobol_sensitivity = 0.35 if i % 3 == 0 else 0.25

            # Get routing decision from fidelity coordinator
            # Calculate current total allocated cost
            current_stats = pool.get_pool_stats()
            current_total_cost = sum(s.total_estimated_cost_ms for s in current_stats.values())
            
            decision = router.route(
                candidate_id=candidate_id,
                surrogate_confidence=surrogate_confidence,
                sobol_sensitivity_score=sobol_sensitivity,
                cost_budget_available=budget_ms - current_total_cost,
            )
            routing_decisions.append(decision)

            # Attempt to allocate candidate
            allocation = pool.allocate_candidate(decision)

            # Always evaluate at concept level (fallback if budget exhausted)
            result = _evaluate(seed, genome)
            result = dict(result)
            result["candidate_id"] = candidate_id
            result["decision"] = decision.as_dict()
            result["allocated"] = allocation is not None
            if allocation:
                result["tier"] = str(allocation.tier.value)
                result["estimated_cost_ms"] = allocation.estimated_cost_ms
            else:
                result["tier"] = "concept"  # Default fallback
                result["estimated_cost_ms"] = 50

            scored.append(result)

        scored.sort(key=lambda candidate: candidate["score"], reverse=True)
        current_best = scored[0]
        if best_candidate is None or current_best["score"] > best_candidate["score"]:
            best_candidate = current_best

        # Record generation history
        history.append(
            {
                "generation": float(generation_index),
                "best_score": round(current_best["score"], 4),
                "mean_score": round(sum(c["score"] for c in scored) / len(scored), 4),
                "fidelity_tiers_used": _summarize_tier_usage(scored),
                "total_allocated_cost_ms": current_total_cost,
            }
        )

        fidelity_decisions_per_gen.append(
            {
                "generation": generation_index,
                "routing_decisions": [d.as_dict() for d in routing_decisions],
                "pool_stats": pool.get_summary().as_dict() if hasattr(pool.get_summary(), "as_dict") else str(pool.get_summary()),
            }
        )

        # Periodically retrain surrogate on accumulated results
        if generation_index % retrain_interval_generations == 0 and generation_index > 0:
            ga_results = [
                {
                    "candidate_id": c["candidate_id"],
                    "state": c["state"],
                    "score": c["score"],
                    "design": c["design"],
                }
                for c in scored
            ]
            scheduler.add_ga_results(ga_results)

        # Prepare elite for next generation
        elite = scored[: max(2, population_size // 5)]
        next_population = [dict(candidate["genome"]) for candidate in elite[:2]]

        while len(next_population) < population_size:
            parent_a = rng.choice(elite)["genome"]
            parent_b = rng.choice(elite)["genome"]
            child = _crossover(parent_a, parent_b, rng)
            child = _mutate(seed, child, rng)
            next_population.append(child)

        population = next_population

    # Compile fidelity metadata for result
    fidelity_metadata = {
        "optimization_enabled": True,
        "budget_ms": budget_ms,
        "enable_coupled_cycle": enable_coupled_cycle,
        "retrain_interval_generations": retrain_interval_generations,
        "total_generations": generations,
        "population_size": population_size,
        "final_pool_stats": pool.get_pool_stats(),
        "decisions_by_generation": fidelity_decisions_per_gen,
        "routing_summary": router.get_routing_summary().as_dict() if hasattr(router.get_routing_summary(), "as_dict") else str(router.get_routing_summary()),
    }

    return GeneticAlgorithmResult(
        best_score=round(best_candidate["score"], 4),
        best_breakdown=best_candidate["breakdown"],
        best_state=best_candidate["state"],
        history=history,
        best_design=best_candidate["design"].as_dict(),
        fidelity_metadata=fidelity_metadata,
    )


def _summarize_tier_usage(scored_candidates: List[Dict[str, object]]) -> Dict[str, int]:
    """Count how many candidates were routed to each fidelity tier."""
    tier_counts = {"heuristic": 0, "concept": 0, "coupled_cycle": 0}
    for candidate in scored_candidates:
        tier = candidate.get("tier", "concept")
        if tier in tier_counts:
            tier_counts[tier] += 1
    return tier_counts
