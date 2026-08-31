"""Objective definitions and weighted design scoring."""

from dataclasses import dataclass
from typing import Callable, Dict

from stanthrust.design_model import EngineDesign, clamp
from stanthrust.inputs import DEFAULT_OBJECTIVE_WEIGHTS

ObjectiveEvaluator = Callable[[EngineDesign], float]


@dataclass(frozen=True)
class ObjectiveDefinition:
    key: str
    label: str
    description: str
    evaluator: ObjectiveEvaluator


def _mass_efficiency(design: EngineDesign) -> float:
    return clamp(1.0 - design.derived.dry_mass_index / 100.0, 0.0, 1.0)


def _thrust_alignment(design: EngineDesign) -> float:
    target = max(1.0, float(design.inputs.target_thrust_newtons))
    predicted = float(design.derived.engineering_values.get("calculated_thrust_newtons", target))
    return clamp(1.0 - abs(predicted - target) / target, 0.0, 1.0)


def _packaging_efficiency(design: EngineDesign) -> float:
    return clamp(design.derived.packaging_efficiency_index / 100.0, 0.0, 1.0)


def _thermal_margin(design: EngineDesign) -> float:
    return clamp(design.derived.thermal_margin_index / 100.0, 0.0, 1.0)


OBJECTIVES: Dict[str, ObjectiveDefinition] = {
    "thrust": ObjectiveDefinition(
        key="thrust",
        label="Thrust",
        description="Favors design states that match the target thrust more closely.",
        evaluator=_thrust_alignment,
    ),
    "mass": ObjectiveDefinition(
        key="mass",
        label="Weight",
        description="Favors lower preliminary dry-mass index and reduced system burden.",
        evaluator=_mass_efficiency,
    ),
    "packaging": ObjectiveDefinition(
        key="packaging",
        label="Packaging",
        description="Favors shorter, better-packaged engine layouts within the envelope.",
        evaluator=_packaging_efficiency,
    ),
    "thermal": ObjectiveDefinition(
        key="thermal",
        label="Thermal Margin",
        description="Favors more robust preliminary thermal margin in the cooling architecture.",
        evaluator=_thermal_margin,
    ),
}


def normalize_objective_weights(weights: Dict[str, float]) -> Dict[str, float]:
    merged = {}
    for key in OBJECTIVES:
        merged[key] = max(0.0, float(weights.get(key, DEFAULT_OBJECTIVE_WEIGHTS.get(key, 0.0))))
    total = sum(merged.values())
    if total <= 0:
        return dict(DEFAULT_OBJECTIVE_WEIGHTS)
    return {key: value / total for key, value in merged.items()}


def evaluate_objectives(design: EngineDesign, weights: Dict[str, float]) -> Dict[str, object]:
    normalized = normalize_objective_weights(weights)
    raw_scores = {
        key: round(definition.evaluator(design), 4) for key, definition in OBJECTIVES.items()
    }
    total_score = 0.0
    for key, score in raw_scores.items():
        total_score += normalized[key] * score

    return {
        "total_score": round(total_score, 4),
        "weights": {key: round(value, 4) for key, value in normalized.items()},
        "scores": raw_scores,
    }
