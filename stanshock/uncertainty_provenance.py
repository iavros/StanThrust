"""Uncertainty and provenance tracking for Stage 4.2.

This module provides utilities for tracking confidence levels and source
attribution of key solver outputs. Each critical field carries:
- value: the computed or estimated quantity
- unit: physical unit
- status: calculated, placeholder, not-applicable, etc.
- source_solver: which solver produced this (or concept-solver)
- confidence: 0.0–1.0 confidence estimate (0 = low, 1 = high)
- uncertainty_percent: estimated ±% uncertainty

This supports end-to-end traceability in exports and helps users understand
which outputs are well-justified vs. concept-stage approximations.
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class UncertaintyBand:
    """Confidence and uncertainty for a field value."""
    lower_percent: float  # ±% below nominal value
    upper_percent: float  # ±% above nominal value
    confidence: float    # 0.0–1.0; higher means more trusted
    notes: str           # rationale for the uncertainty band


@dataclass(frozen=True)
class ProvenanceField:
    """A field with provenance, source, and uncertainty information."""
    value: float
    unit: str
    status: str            # "calculated", "placeholder", "not-applicable"
    source_solver: str     # e.g. "Combustion CFD Proxy Solver", "concept-solver"
    confidence: float      # 0.0–1.0
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
    """Return confidence and uncertainty bands for key solver fields.

    These are conservative, concept-stage estimates. Tighten as solver
    fidelity increases or replace with solver-generated uncertainty outputs.
    """
    return {
        # Thermochemistry fields (Stage 1)
        "chamber_temperature_k": UncertaintyBand(
            lower_percent=8.0,
            upper_percent=12.0,
            confidence=0.65,
            notes="Concept-stage assumption; depends on propellant pair and thermochem provider."
        ),

        # Feed pressure drop (Stage 2.1)
        "fuel_tank_pressure_kpa": UncertaintyBand(
            lower_percent=6.0,
            upper_percent=10.0,
            confidence=0.72,
            notes="Reduced-order branch-loss model; excludes manifold detail."
        ),
        "oxidizer_tank_pressure_kpa": UncertaintyBand(
            lower_percent=6.0,
            upper_percent=10.0,
            confidence=0.72,
            notes="Reduced-order branch-loss model; excludes manifold detail."
        ),

        # Combustion fields (Stage 2.2)
        "chamber_pressure_kpa": UncertaintyBand(
            lower_percent=5.0,
            upper_percent=8.0,
            confidence=0.78,
            notes="Proxy solver; concept-stage; actual engine requires testing."
        ),
        "exhaust_temperature_k": UncertaintyBand(
            lower_percent=10.0,
            upper_percent=15.0,
            confidence=0.62,
            notes="Concept estimate; depends on combustion model fidelity."
        ),
        "nozzle_exit_mach": UncertaintyBand(
            lower_percent=8.0,
            upper_percent=12.0,
            confidence=0.70,
            notes="Based on isentropic expansion assumption."
        ),

        # Station fields (Stage 2.4)
        "station_mass_flow_kg_s": UncertaintyBand(
            lower_percent=4.0,
            upper_percent=6.0,
            confidence=0.82,
            notes="From propellant budget; propagates burn-time uncertainty."
        ),
        "station_temperature_k": UncertaintyBand(
            lower_percent=10.0,
            upper_percent=15.0,
            confidence=0.65,
            notes="Interpolated or proxy; depends on combustion and cooling models."
        ),

        # Structural fields (Stage 3.2)
        "thermal_margin_index": UncertaintyBand(
            lower_percent=15.0,
            upper_percent=20.0,
            confidence=0.58,
            notes="Concept-stage heuristic; excludes stress and fatigue."
        ),

        # Packaging and indices (concept layer)
        "dry_mass_index": UncertaintyBand(
            lower_percent=12.0,
            upper_percent=18.0,
            confidence=0.60,
            notes="Scaling-law proxy; component-level mass not detailed."
        ),
        "packaging_efficiency_index": UncertaintyBand(
            lower_percent=8.0,
            upper_percent=12.0,
            confidence=0.68,
            notes="Geometric heuristic; does not account for routing complexity."
        ),
    }


def estimate_field_confidence(
    field_name: str,
    value: float,
    source_solver: str,
    status: str = "calculated",
) -> Optional[ProvenanceField]:
    """Create a provenance field with confidence estimate for a known field.

    If the field is not in the confidence band library, returns None.
    Status priority: calculated > placeholder > not-applicable.
    """
    bands = get_field_confidence_bands()
    if field_name not in bands:
        return None

    band = bands[field_name]

    # Adjust confidence based on status
    confidence = band.confidence
    if status == "placeholder":
        confidence *= 0.6  # Placeholder fields are less confident
    elif status == "not-applicable":
        confidence *= 0.3  # N/A fields are very low confidence

    return ProvenanceField(
        value=value,
        unit="",  # Empty; caller should fill in
        status=status,
        source_solver=source_solver,
        confidence=confidence,
        uncertainty=band,
    )


def build_uncertainty_summary(fields: Dict[str, ProvenanceField]) -> Dict[str, object]:
    """Build a summary of uncertainty across a set of provenance fields.

    Returns statistics on confidence distribution and high-uncertainty fields.
    """
    if not fields:
        return {
            "total_fields": 0,
            "mean_confidence": 0.0,
            "confidence_distribution": {},
            "high_uncertainty_fields": [],
        }

    confidences = [f.confidence for f in fields.values()]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0

    # Confidence distribution buckets
    dist = {
        "high": len([c for c in confidences if c >= 0.75]),
        "moderate": len([c for c in confidences if 0.5 <= c < 0.75]),
        "low": len([c for c in confidences if c < 0.5]),
    }

    # High-uncertainty fields (upper band > 10%)
    high_unc = [
        (name, field.uncertainty.upper_percent)
        for name, field in fields.items()
        if field.uncertainty.upper_percent > 10.0
    ]
    high_unc.sort(key=lambda x: x[1], reverse=True)

    return {
        "total_fields": len(fields),
        "mean_confidence": round(mean_conf, 3),
        "confidence_distribution": dist,
        "high_uncertainty_fields": [
            {"name": name, "upper_percent": pct}
            for name, pct in high_unc[:5]  # Top 5
        ],
    }

