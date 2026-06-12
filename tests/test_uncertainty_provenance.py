"""Tests for uncertainty and provenance outputs."""

from liquid_engine_studio.uncertainty_provenance import (
    UncertaintyBand,
    ProvenanceField,
    get_field_confidence_bands,
    estimate_field_confidence,
    build_uncertainty_summary,
)


def test_uncertainty_band_creation():
    """Test that UncertaintyBand dataclass works."""
    band = UncertaintyBand(
        lower_percent=5.0,
        upper_percent=10.0,
        confidence=0.75,
        notes="Test band"
    )

    assert band.lower_percent == 5.0
    assert band.upper_percent == 10.0
    assert band.confidence == 0.75
    assert band.notes == "Test band"
    print("✓ test_uncertainty_band_creation passed")


def test_provenance_field_creation():
    """Test that ProvenanceField dataclass and as_dict() work."""
    band = UncertaintyBand(
        lower_percent=5.0,
        upper_percent=10.0,
        confidence=0.75,
        notes="Test"
    )
    field = ProvenanceField(
        value=1250.5,
        unit="kPa",
        status="calculated",
        source_solver="Test Solver",
        confidence=0.80,
        uncertainty=band,
    )

    assert field.value == 1250.5
    assert field.unit == "kPa"

    export = field.as_dict()
    assert export["value"] == 1250.5
    assert export["unit"] == "kPa"
    assert export["status"] == "calculated"
    assert export["source_solver"] == "Test Solver"
    assert export["confidence"] == 0.8
    assert "uncertainty" in export
    assert export["uncertainty"]["lower_percent"] == 5.0
    assert export["uncertainty"]["upper_percent"] == 10.0
    print("✓ test_provenance_field_creation passed")


def test_field_confidence_bands_loaded():
    """Test that confidence bands can be loaded."""
    bands = get_field_confidence_bands()

    assert isinstance(bands, dict)
    assert len(bands) > 0

    # Check for key fields from different stages
    critical_fields = [
        "chamber_temperature_k",
        "chamber_pressure_kpa",
        "fuel_tank_pressure_kpa",
        "station_mass_flow_kg_s",
        "thermal_margin_index",
        "dry_mass_index",
    ]

    for field in critical_fields:
        assert field in bands, f"{field} should be in confidence bands"
        band = bands[field]
        assert isinstance(band, UncertaintyBand)
        assert 0.0 <= band.confidence <= 1.0

    print(f"✓ test_field_confidence_bands_loaded passed: {len(bands)} fields")


def test_estimate_field_confidence():
    """Test confidence estimation for known fields."""
    # Test calculated status
    field_calc = estimate_field_confidence(
        "chamber_pressure_kpa",
        1500.0,
        "Combustion CFD Proxy Solver",
        status="calculated"
    )

    assert field_calc is not None
    assert field_calc.value == 1500.0
    assert field_calc.source_solver == "Combustion CFD Proxy Solver"
    assert field_calc.status == "calculated"
    assert field_calc.confidence > 0.5

    # Test placeholder status degrades confidence
    field_placeholder = estimate_field_confidence(
        "chamber_pressure_kpa",
        1500.0,
        "concept-solver",
        status="placeholder"
    )

    assert field_placeholder is not None
    assert field_placeholder.confidence < field_calc.confidence

    # Test unknown field returns None
    field_unknown = estimate_field_confidence(
        "unknown_field_xyz",
        999.0,
        "Test Solver",
    )

    assert field_unknown is None

    print("✓ test_estimate_field_confidence passed")


def test_build_uncertainty_summary():
    """Test that uncertainty summaries are built correctly."""
    band1 = UncertaintyBand(lower_percent=5.0, upper_percent=10.0, confidence=0.80, notes="High conf")
    band2 = UncertaintyBand(lower_percent=12.0, upper_percent=18.0, confidence=0.60, notes="Mod conf")
    band3 = UncertaintyBand(lower_percent=15.0, upper_percent=20.0, confidence=0.40, notes="Low conf")

    fields = {
        "field_1": ProvenanceField(100.0, "unit", "calculated", "Solver A", 0.80, band1),
        "field_2": ProvenanceField(200.0, "unit", "calculated", "Solver B", 0.60, band2),
        "field_3": ProvenanceField(300.0, "unit", "placeholder", "concept-solver", 0.40, band3),
    }

    summary = build_uncertainty_summary(fields)

    assert summary["total_fields"] == 3
    assert summary["mean_confidence"] > 0.5  # Average of 0.80, 0.60, 0.40
    assert "confidence_distribution" in summary
    assert summary["confidence_distribution"]["high"] == 1
    assert summary["confidence_distribution"]["moderate"] == 1
    assert summary["confidence_distribution"]["low"] == 1
    assert "high_uncertainty_fields" in summary

    print("✓ test_build_uncertainty_summary passed")


def test_uncertainty_summary_empty():
    """Test that uncertainty summary handles empty field set."""
    summary = build_uncertainty_summary({})

    assert summary["total_fields"] == 0
    assert summary["mean_confidence"] == 0.0
    assert len(summary["high_uncertainty_fields"]) == 0

    print("✓ test_uncertainty_summary_empty passed")


def test_confidence_degradation_by_status():
    """Test that confidence properly degrades based on field status."""
    calculated_conf = estimate_field_confidence(
        "chamber_pressure_kpa", 1500.0, "Solver", status="calculated"
    ).confidence

    placeholder_conf = estimate_field_confidence(
        "chamber_pressure_kpa", 1500.0, "Solver", status="placeholder"
    ).confidence

    not_applicable_conf = estimate_field_confidence(
        "chamber_pressure_kpa", 1500.0, "Solver", status="not-applicable"
    ).confidence

    assert calculated_conf > placeholder_conf
    assert placeholder_conf > not_applicable_conf

    print("✓ test_confidence_degradation_by_status passed")


def run_all_tests():
    """Run all Stage 4.2 uncertainty/provenance tests."""
    tests = [
        test_uncertainty_band_creation,
        test_provenance_field_creation,
        test_field_confidence_bands_loaded,
        test_estimate_field_confidence,
        test_build_uncertainty_summary,
        test_uncertainty_summary_empty,
        test_confidence_degradation_by_status,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test_func.__name__} failed: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test_func.__name__} error: {e}")
            failed += 1

    print(f"\nStage 4.2 Tests: {passed} passed, {failed} failed out of {len(tests)} total.")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)

