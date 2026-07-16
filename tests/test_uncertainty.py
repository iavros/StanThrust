"""Tests for uncertainty and provenance outputs."""

from stanthrust.uncertainty import (
    ProvenanceField,
    UncertaintyBand,
    build_uncertainty_summary,
    calculate_field_confidence,
    get_field_confidence_bands,
)


def test_uncertainty_band_creation():
    band = UncertaintyBand(5.0, 10.0, 0.75, "Test band")

    assert band.lower_percent == 5.0
    assert band.upper_percent == 10.0
    assert band.confidence == 0.75
    assert band.notes == "Test band"


def test_provenance_field_serialization():
    band = UncertaintyBand(5.0, 10.0, 0.75, "Test")
    field = ProvenanceField(1250.5, "kPa", "calculated", "Test Solver", 0.80, band)

    payload = field.as_dict()
    assert payload["value"] == 1250.5
    assert payload["unit"] == "kPa"
    assert payload["status"] == "calculated"
    assert payload["source_solver"] == "Test Solver"
    assert payload["confidence"] == 0.8
    assert payload["uncertainty"] == {
        "lower_percent": 5.0,
        "upper_percent": 10.0,
    }


def test_field_confidence_catalog_covers_critical_outputs():
    bands = get_field_confidence_bands()
    critical_fields = [
        "chamber_temperature_k",
        "chamber_pressure_kpa",
        "fuel_tank_pressure_kpa",
        "station_mass_flow_kg_s",
        "thermal_margin_index",
        "dry_mass_index",
    ]

    for field_name in critical_fields:
        assert field_name in bands
        assert isinstance(bands[field_name], UncertaintyBand)
        assert 0.0 <= bands[field_name].confidence <= 1.0


def test_calculate_field_confidence_handles_status_and_unknown_fields():
    calculated = calculate_field_confidence(
        "chamber_pressure_kpa", 1500.0, "Cantera Coupled Flow Solver"
    )
    unavailable = calculate_field_confidence(
        "chamber_pressure_kpa", 1500.0, "Cantera Coupled Flow Solver", "unavailable"
    )
    not_applicable = calculate_field_confidence(
        "chamber_pressure_kpa", 1500.0, "Cantera Coupled Flow Solver", "not-applicable"
    )

    assert calculated is not None
    assert unavailable is not None
    assert not_applicable is not None
    assert calculated.confidence > unavailable.confidence > not_applicable.confidence
    assert calculate_field_confidence("unknown_field", 1.0, "Solver") is None


def test_build_uncertainty_summary():
    fields = {
        "field_1": ProvenanceField(
            100.0, "unit", "calculated", "Solver A", 0.80,
            UncertaintyBand(5.0, 10.0, 0.80, "High confidence"),
        ),
        "field_2": ProvenanceField(
            200.0, "unit", "calculated", "Solver B", 0.60,
            UncertaintyBand(12.0, 18.0, 0.60, "Moderate confidence"),
        ),
        "field_3": ProvenanceField(
            300.0, "unit", "unavailable", "Solver C", 0.40,
            UncertaintyBand(15.0, 20.0, 0.40, "Low confidence"),
        ),
    }

    summary = build_uncertainty_summary(fields)
    assert summary["total_fields"] == 3
    assert summary["mean_confidence"] == 0.6
    assert summary["confidence_distribution"] == {
        "high": 1,
        "moderate": 1,
        "low": 1,
    }
    assert summary["high_uncertainty_fields"][0]["name"] == "field_3"


def test_build_uncertainty_summary_handles_empty_input():
    summary = build_uncertainty_summary({})

    assert summary["total_fields"] == 0
    assert summary["mean_confidence"] == 0.0
    assert summary["high_uncertainty_fields"] == []
