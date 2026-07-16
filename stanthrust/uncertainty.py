"""Uncertainty and provenance records attached to exported solver fields."""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class UncertaintyBand:
    lower_percent: float
    upper_percent: float
    confidence: float
    notes: str


@dataclass(frozen=True)
class ProvenanceField:
    value: float
    unit: str
    status: str
    source_solver: str
    confidence: float
    uncertainty: UncertaintyBand

    def as_dict(self) -> Dict[str, object]:
        return {
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "source_solver": self.source_solver,
            "confidence": round(self.confidence, 3),
            "uncertainty": {
                "lower_percent": round(self.uncertainty.lower_percent, 2),
                "upper_percent": round(self.uncertainty.upper_percent, 2),
            },
            "uncertainty_notes": self.uncertainty.notes,
        }


def get_field_confidence_bands() -> Dict[str, UncertaintyBand]:
    """Return documented design-stage confidence bands for exported fields."""
    return {
        "chamber_temperature_k": UncertaintyBand(
            lower_percent=8.0,
            upper_percent=12.0,
            confidence=0.65,
            notes="Bound reflects propellant-pair and thermochemistry-model sensitivity.",
        ),
        "fuel_tank_pressure_kpa": UncertaintyBand(
            lower_percent=6.0,
            upper_percent=10.0,
            confidence=0.72,
            notes="Bound covers unresolved manifold and branch-loss geometry.",
        ),
        "oxidizer_tank_pressure_kpa": UncertaintyBand(
            lower_percent=6.0,
            upper_percent=10.0,
            confidence=0.72,
            notes="Bound covers unresolved manifold and branch-loss geometry.",
        ),
        "chamber_pressure_kpa": UncertaintyBand(
            lower_percent=5.0,
            upper_percent=8.0,
            confidence=0.78,
            notes="Hardware correlation is required to narrow this solver-model bound.",
        ),
        "exhaust_temperature_k": UncertaintyBand(
            lower_percent=10.0,
            upper_percent=15.0,
            confidence=0.62,
            notes="Bound reflects combustion-state and equilibrium-model sensitivity.",
        ),
        "nozzle_exit_mach": UncertaintyBand(
            lower_percent=8.0,
            upper_percent=12.0,
            confidence=0.70,
            notes="Bound reflects viscous, shock, and boundary-layer model sensitivity.",
        ),
        "station_mass_flow_kg_s": UncertaintyBand(
            lower_percent=4.0,
            upper_percent=6.0,
            confidence=0.82,
            notes="Bound propagates burn-duration and propellant-budget uncertainty.",
        ),
        "station_temperature_k": UncertaintyBand(
            lower_percent=10.0,
            upper_percent=15.0,
            confidence=0.65,
            notes="Bound reflects combustion, interpolation, and cooling-model sensitivity.",
        ),
        "thermal_margin_index": UncertaintyBand(
            lower_percent=15.0,
            upper_percent=20.0,
            confidence=0.58,
            notes="Bound covers unresolved fatigue, contact, and manufacturing effects.",
        ),
        "dry_mass_index": UncertaintyBand(
            lower_percent=12.0,
            upper_percent=18.0,
            confidence=0.60,
            notes="Bound covers component details not represented by the sizing model.",
        ),
        "packaging_efficiency_index": UncertaintyBand(
            lower_percent=8.0,
            upper_percent=12.0,
            confidence=0.68,
            notes="Bound covers routing and integration geometry outside the solved envelope.",
        ),
    }


def calculate_field_confidence(
    field_name: str,
    value: float,
    source_solver: str,
    status: str = "calculated",
) -> Optional[ProvenanceField]:
    band = get_field_confidence_bands().get(field_name)
    if band is None:
        return None

    confidence = band.confidence
    if status == "unavailable":
        confidence *= 0.6
    elif status == "not-applicable":
        confidence *= 0.3

    return ProvenanceField(
        value=value,
        unit="",
        status=status,
        source_solver=source_solver,
        confidence=confidence,
        uncertainty=band,
    )


def build_uncertainty_summary(fields: Dict[str, ProvenanceField]) -> Dict[str, object]:
    if not fields:
        return {
            "total_fields": 0,
            "mean_confidence": 0.0,
            "confidence_distribution": {},
            "high_uncertainty_fields": [],
        }

    confidences = [field.confidence for field in fields.values()]
    mean_confidence = sum(confidences) / len(confidences)
    distribution = {
        "high": sum(confidence >= 0.75 for confidence in confidences),
        "moderate": sum(0.5 <= confidence < 0.75 for confidence in confidences),
        "low": sum(confidence < 0.5 for confidence in confidences),
    }
    high_uncertainty = sorted(
        (
            (name, field.uncertainty.upper_percent)
            for name, field in fields.items()
            if field.uncertainty.upper_percent > 10.0
        ),
        key=lambda item: item[1],
        reverse=True,
    )

    return {
        "total_fields": len(fields),
        "mean_confidence": round(mean_confidence, 3),
        "confidence_distribution": distribution,
        "high_uncertainty_fields": [
            {"name": name, "upper_percent": percent}
            for name, percent in high_uncertainty[:5]
        ],
    }
