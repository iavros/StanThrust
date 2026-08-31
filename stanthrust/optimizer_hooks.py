"""Genetic and feasibility-first optimiser drivers over the design model."""

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from stanthrust.design_model import EngineDesign, create_engine_design
from stanthrust.fidelity_coordinator import AdaptiveSamplingPool, FidelityRouter
from stanthrust.inputs import DEFAULT_OBJECTIVE_WEIGHTS
from stanthrust.objectives import evaluate_objectives, normalize_objective_weights
from stanthrust.validation_pack import validate_engine_design

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
    design: EngineDesign,
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
            "mixture_ratio": max(
                bounds["mixture_ratio"][0],
                min(bounds["mixture_ratio"][1], genome["mixture_ratio"]),
            ),
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
    design = create_engine_design(state)
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
    score_threshold: float = 0.0,
) -> Dict[str, object]:
    """Confirm the GA result with the direct design validator."""

    try:
        confirmed_design = create_engine_design(result.best_state)
        validation = validate_engine_design(confirmed_design)
        score_ok = float(result.best_score) >= float(score_threshold)
        passed = bool(validation.passed and score_ok)
        return {
            "screening_applied": True,
            "confirmation_ratio": confirmation_ratio,
            "score_threshold": score_threshold,
            "candidates_evaluated": 1,
            "candidates_confirmed": 1 if passed else 0,
            "candidates_rejected": 0 if passed else 1,
            "best_passed_screening": passed,
            "validation_summary": validation.summary,
        }
    except Exception as exc:
        return {
            "screening_applied": False,
            "screening_error": str(exc),
        }


def _is_design_feasible(
    design: EngineDesign,
    constraints: Dict[str, object],
    min_thermal_margin: float = 30.0,
) -> bool:
    """Simple feasibility checker used by the feasibility-first optimizer."""

    try:
        if float(design.derived.thermal_margin_index) < float(min_thermal_margin):
            return False
    except Exception:
        return False

    tank_max = float(constraints.get("tank_diameter_mm_max", float("inf")))
    chamber_max = float(constraints.get("chamber_diameter_mm_max", float("inf")))
    nozzle_max = float(constraints.get("nozzle_diameter_mm_max", float("inf")))

    if float(design.inputs.tank_diameter_mm) > tank_max:
        return False
    if float(design.inputs.chamber_diameter_mm) > chamber_max:
        return False
    if float(design.inputs.nozzle_diameter_mm) > nozzle_max:
        return False

    validation = validate_engine_design(design)
    return validation.passed


def run_feasibility_first_optimizer(
    seed: OptimizerSeed,
    generations: int = 16,
    population_size: int = 24,
    random_seed: Optional[int] = None,
    min_thermal_margin: float = 30.0,
) -> GeneticAlgorithmResult:
    """Genetic optimizer variant that prioritizes feasibility before score."""

    rng = random.Random(random_seed)
    population = [_random_genome(seed, rng) for _ in range(population_size - 1)]
    population.append(dict(seed.genome_template))

    history = []
    best_candidate = None

    for generation_index in range(generations):
        evaluated = []
        for genome in population:
            candidate = _evaluate(seed, genome)
            candidate["feasible"] = _is_design_feasible(
                candidate["design"],
                seed.constraints,
                min_thermal_margin,
            )
            evaluated.append(candidate)

        evaluated.sort(
            key=lambda row: (1 if row.get("feasible") else 0, row.get("score", 0.0)),
            reverse=True,
        )
        current_best = evaluated[0]
        if best_candidate is None:
            best_candidate = current_best
        else:
            best_key = (
                1 if best_candidate.get("feasible") else 0,
                best_candidate.get("score", 0.0),
            )
            current_key = (
                1 if current_best.get("feasible") else 0,
                current_best.get("score", 0.0),
            )
            if current_key > best_key:
                best_candidate = current_best

        history.append(
            {
                "generation": float(generation_index),
                "best_score": round(current_best["score"], 4),
                "mean_score": round(sum(row["score"] for row in evaluated) / len(evaluated), 4),
                "feasible_count": sum(1 for row in evaluated if row.get("feasible")),
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
) -> GeneticAlgorithmResult:
    """Genetic optimizer with direct solver fidelity routing."""

    rng = random.Random(random_seed)
    population = [_random_genome(seed, rng) for _ in range(population_size - 1)]
    population.append(dict(seed.genome_template))

    router = FidelityRouter(enable_coupled_cycle=enable_coupled_cycle)
    pool = AdaptiveSamplingPool(budget_ms=budget_ms)

    history = []
    best_candidate = None
    fidelity_decisions_per_gen = []

    for generation_index in range(generations):
        scored = []
        routing_decisions = []

        for i, genome in enumerate(population):
            candidate_id = f"gen{generation_index}_cand{i}"
            requested_accuracy = 0.55 + 0.25 * (i % 2)
            sobol_sensitivity = 0.35 if i % 3 == 0 else 0.25
            current_stats = pool.get_pool_stats()
            current_total_cost = sum(row.total_allocated_cost_ms for row in current_stats.values())

            decision = router.route(
                candidate_id=candidate_id,
                requested_accuracy=requested_accuracy,
                sobol_sensitivity_score=sobol_sensitivity,
                cost_budget_available=budget_ms - current_total_cost,
            )
            routing_decisions.append(decision)
            allocation = pool.allocate_candidate(decision)

            result = dict(_evaluate(seed, genome))
            result["candidate_id"] = candidate_id
            result["decision"] = decision.as_dict()
            result["allocated"] = allocation is not None
            if allocation:
                result["tier"] = allocation.tier.value
                result["allocated_cost_ms"] = allocation.allocated_cost_ms
            else:
                result["tier"] = "design"
                result["allocated_cost_ms"] = 50
            scored.append(result)

        scored.sort(key=lambda candidate: candidate["score"], reverse=True)
        current_best = scored[0]
        if best_candidate is None or current_best["score"] > best_candidate["score"]:
            best_candidate = current_best

        history.append(
            {
                "generation": float(generation_index),
                "best_score": round(current_best["score"], 4),
                "mean_score": round(sum(row["score"] for row in scored) / len(scored), 4),
                "fidelity_tiers_used": _summarize_tier_usage(scored),
                "total_allocated_cost_ms": current_total_cost,
            }
        )
        fidelity_decisions_per_gen.append(
            {
                "generation": generation_index,
                "routing_decisions": [decision.as_dict() for decision in routing_decisions],
                "pool_stats": pool.get_summary(),
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

    fidelity_metadata = {
        "optimization_enabled": True,
        "budget_ms": budget_ms,
        "enable_coupled_cycle": enable_coupled_cycle,
        "total_generations": generations,
        "population_size": population_size,
        "final_pool_stats": pool.get_pool_stats(),
        "decisions_by_generation": fidelity_decisions_per_gen,
        "routing_summary": router.get_routing_summary(),
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

    tier_counts = {"fast": 0, "design": 0, "coupled_cycle": 0}
    for candidate in scored_candidates:
        tier = candidate.get("tier", "design")
        if tier in tier_counts:
            tier_counts[tier] += 1
    return tier_counts
