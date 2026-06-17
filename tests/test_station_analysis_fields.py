"""Comprehensive tests for Stage 2.4 station field promotion feature."""
from pathlib import Path
from tempfile import TemporaryDirectory
import csv


def test_station_numeric_promotion():
    """Assert all station rows include machine-friendly numeric fields."""
    from liquid_engine_studio.concept_model import create_concept_design

    design = create_concept_design({})
    for row in design.derived.station_rows:
        assert hasattr(row, "temperature_k"), f"{row.label}: missing temperature_k"
        assert hasattr(row, "pressure_kpa"), f"{row.label}: missing pressure_kpa"
        assert hasattr(row, "mass_flow_kg_s"), f"{row.label}: missing mass_flow_kg_s"
        assert hasattr(row, "mach_number"), f"{row.label}: missing mach_number"
        assert row.temperature_k is not None, f"{row.label}: temperature_k is None"
        assert row.pressure_kpa is not None, f"{row.label}: pressure_kpa is None"
        assert row.mass_flow_kg_s is not None, f"{row.label}: mass_flow_kg_s is None"
        assert row.mach_number is not None, f"{row.label}: mach_number is None"


def test_numeric_value_ranges():
    """Validate that numeric station fields have physically reasonable values."""
    from liquid_engine_studio.concept_model import create_concept_design

    design = create_concept_design({})
    for row in design.derived.station_rows:
        # Temperature should be in reasonable range (200 K - 4000 K for any engine)
        assert 200 <= row.temperature_k <= 4000, \
            f"{row.label}: temp {row.temperature_k} K out of range"
        # Pressure should be positive and within typical engine range (0.1 - 100 MPa)
        assert 0 < row.pressure_kpa < 100000, \
            f"{row.label}: pressure {row.pressure_kpa} kPa out of range"
        # Mass flow should be positive
        assert row.mass_flow_kg_s > 0, \
            f"{row.label}: mass flow {row.mass_flow_kg_s} kg/s is not positive"
        # Mach should be in sensible range (0 to ~5 for typical nozzles)
        assert 0 <= row.mach_number <= 5, \
            f"{row.label}: mach {row.mach_number} out of range"


def test_json_export_contains_analysis_fields():
    """Assert JSON export contains analysis_fields with provenance for each station."""
    from liquid_engine_studio.concept_model import create_concept_design
    from liquid_engine_studio.exporter import build_cad_export_payload

    design = create_concept_design({})
    payload = build_cad_export_payload(design, {"total_score": 1.0})
    assert "concept_station_rows" in payload
    rows = payload["concept_station_rows"]
    assert isinstance(rows, list) and len(rows) > 0
    
    for r in rows:
        # Check analysis_fields structure
        assert "analysis_fields" in r, f"Missing analysis_fields in {r.get('label')}"
        af = r["analysis_fields"]
        
        # Each field should have value, unit, status, source_solver
        for field_name in ("temperature", "pressure", "mass_flow", "mach"):
            assert field_name in af, f"Missing {field_name} in analysis_fields"
            field = af[field_name]
            assert "value" in field, f"Missing value for {field_name}"
            assert "status" in field, f"Missing status for {field_name}"
            assert "unit" in field, f"Missing unit for {field_name}"
            assert "source_solver" in field, f"Missing source_solver for {field_name}"
            # Status should be "calculated" or "placeholder"
            assert field["status"] in ("calculated", "placeholder"), \
                f"Invalid status {field['status']} for {field_name}"


def test_json_export_numeric_fields():
    """Assert JSON export includes machine-friendly numeric station fields."""
    from liquid_engine_studio.concept_model import create_concept_design
    from liquid_engine_studio.exporter import build_cad_export_payload

    design = create_concept_design({})
    payload = build_cad_export_payload(design, {"total_score": 1.0})
    rows = payload["concept_station_rows"]
    
    for r in rows:
        assert "temperature_k" in r, f"Missing temperature_k in {r.get('label')}"
        assert "pressure_kpa" in r, f"Missing pressure_kpa in {r.get('label')}"
        assert "mass_flow_kg_s" in r, f"Missing mass_flow_kg_s in {r.get('label')}"
        assert "mach_number" in r, f"Missing mach_number in {r.get('label')}"


def test_backward_compatibility_notes():
    """Assert human-readable note fields are still present (backward compatibility)."""
    from liquid_engine_studio.concept_model import create_concept_design

    design = create_concept_design({})
    for row in design.derived.station_rows:
        assert hasattr(row, "temperature_note"), f"{row.label}: missing temperature_note"
        assert hasattr(row, "pressure_note"), f"{row.label}: missing pressure_note"
        assert hasattr(row, "mass_flow_note"), f"{row.label}: missing mass_flow_note"
        assert hasattr(row, "mach_note"), f"{row.label}: missing mach_note"
        assert isinstance(row.temperature_note, str), f"{row.label}: temperature_note not a string"
        assert isinstance(row.pressure_note, str), f"{row.label}: pressure_note not a string"


def test_regen_cooling_scenario():
    """Test station promotion with regen cooling enabled."""
    from liquid_engine_studio.concept_model import create_concept_design

    state = {
        "regen_cooling": True,
        "fuel_name": "Ethanol",
        "oxidizer_name": "Liquid Oxygen",
        "mixture_ratio": 1.4,
        "target_thrust_newtons": 250.0,
        "target_impulse_newton_seconds": 3000.0,
        "target_diameter_mm": 110.0,
        "burn_time_seconds": 12.0,
        "chamber_diameter_mm": 68.0,
        "nozzle_diameter_mm": 95.0,
        "tank_diameter_mm": 110.0,
    }
    design = create_concept_design(state)
    
    # Verify all stations have numeric fields
    for row in design.derived.station_rows:
        assert row.temperature_k is not None
        assert row.pressure_kpa > 0
        assert row.mass_flow_kg_s > 0
        assert row.mach_number >= 0
    
    # Thermal margins should be calculated with regen on
    assert design.derived.engineering_values.get("regen_thermal_model_status") == "calculated"
    assert design.derived.engineering_values.get("regen_min_thermal_margin_index") > 0


def test_no_regen_cooling_scenario():
    """Test station promotion with regen cooling disabled."""
    from liquid_engine_studio.concept_model import create_concept_design

    state = {
        "regen_cooling": False,
        "fuel_name": "Ethanol",
        "oxidizer_name": "Liquid Oxygen",
        "mixture_ratio": 1.4,
        "target_thrust_newtons": 250.0,
        "target_impulse_newton_seconds": 3000.0,
        "target_diameter_mm": 110.0,
        "burn_time_seconds": 12.0,
        "chamber_diameter_mm": 68.0,
        "nozzle_diameter_mm": 95.0,
        "tank_diameter_mm": 110.0,
    }
    design = create_concept_design(state)
    
    # Verify all stations have numeric fields
    for row in design.derived.station_rows:
        assert row.temperature_k is not None
        assert row.pressure_kpa >= 0
        assert row.mass_flow_kg_s > 0
        assert row.mach_number >= 0
    
    # Thermal margins should NOT be calculated without regen
    assert design.derived.engineering_values.get("regen_thermal_model_status") == "not-active"


def test_csv_export_numeric_columns():
    """Assert CSV station export includes numeric columns at the end."""
    from liquid_engine_studio.concept_model import create_concept_design
    from liquid_engine_studio.exporter import export_station_csv

    design = create_concept_design({})
    with TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "stations.csv"
        export_station_csv(csv_path, design)
        
        # Read and check header
        rows = list(csv.reader(csv_path.open("r")))
        header = rows[0]
        
        # Check for numeric column headers
        assert "temperature_k" in header, "Missing temperature_k column"
        assert "pressure_kpa" in header, "Missing pressure_kpa column"
        assert "mass_flow_kg_s" in header, "Missing mass_flow_kg_s column"
        assert "mach_number_calculated" in header, "Missing mach_number_calculated column"
        
        # Check that numeric columns are at the end
        expected_numeric_columns = ["temperature_k", "pressure_kpa", "mass_flow_kg_s", "mach_number_calculated"]
        actual_last_columns = header[-len(expected_numeric_columns):]
        assert actual_last_columns == expected_numeric_columns, \
            f"Numeric columns not at end. Last columns: {actual_last_columns}"
        
        # Verify data rows have numeric values in those columns
        if len(rows) > 1:
            data_row = rows[1]
            temp_col_idx = header.index("temperature_k")
            press_col_idx = header.index("pressure_kpa")
            mass_col_idx = header.index("mass_flow_kg_s")
            mach_col_idx = header.index("mach_number_calculated")
            
            # Temperature should parse as float
            if data_row[temp_col_idx]:
                temp_val = float(data_row[temp_col_idx])
                assert 200 <= temp_val <= 4000, f"Temp {temp_val} out of range"
            
            # Pressure should parse as float and be positive
            if data_row[press_col_idx]:
                press_val = float(data_row[press_col_idx])
                assert press_val > 0, f"Pressure {press_val} should be positive"
            
            # Mass flow should parse as float and be positive
            if data_row[mass_col_idx]:
                mass_val = float(data_row[mass_col_idx])
                assert mass_val > 0, f"Mass flow {mass_val} should be positive"
            
            # Mach should parse as float and be in reasonable range
            if data_row[mach_col_idx]:
                mach_val = float(data_row[mach_col_idx])
                assert 0 <= mach_val <= 5, f"Mach {mach_val} out of range"


def test_solver_provenance_tagging():
    """Assert that solver provenance is correctly tagged in analysis_fields."""
    from liquid_engine_studio.concept_model import create_concept_design
    from liquid_engine_studio.exporter import build_cad_export_payload

    design = create_concept_design({})
    payload = build_cad_export_payload(design, {"total_score": 1.0})
    rows = payload["concept_station_rows"]
    
    # Check that source_solver field exists and has reasonable values
    for r in rows:
        af = r["analysis_fields"]
        for field_name, field_data in af.items():
            assert "source_solver" in field_data, \
                f"Missing source_solver for {field_name} in {r.get('label')}"
            # source_solver should identify the solver that produced the field.
            valid_solvers = [
                "concept-solver",
                "Feed Pressure-Drop Solver",
                "Combustion CFD Proxy Solver",
            ]
            assert field_data["source_solver"] in valid_solvers, \
                f"Unknown source_solver {field_data['source_solver']} for {field_name}"


def test_nozzle_contour_metadata_and_shape():
    """Assert the nozzle contour uses bell metadata and produces a usable monotonic profile."""
    from liquid_engine_studio.concept_model import create_concept_design

    design = create_concept_design({})
    values = design.derived.engineering_values
    contour_points = list(design.derived.nozzle_contour_points)

    assert values.get("nozzle_contour_method") == "moc_bell"
    assert values.get("nozzle_contour_method_label") == "MOC-informed bell contour"
    assert float(values.get("nozzle_moc_exit_mach", 0.0)) > 1.0
    assert float(values.get("nozzle_moc_turn_angle_deg", 0.0)) > float(values.get("nozzle_bell_exit_angle_deg", 0.0))
    assert float(values.get("nozzle_bell_length_fraction", 0.0)) > 0.0
    assert float(values.get("nozzle_bell_entrance_angle_deg", 0.0)) > float(values.get("nozzle_bell_exit_angle_deg", 0.0))
    assert len(contour_points) >= 12

    throat_index = next(
        index for index, point in enumerate(contour_points) if str(point.get("section", "")) == "throat"
    )
    throat_radius_mm = float(contour_points[throat_index]["radius_mm"])

    axial_values = [float(point["x_mm"]) for point in contour_points]
    assert axial_values == sorted(axial_values), "Nozzle contour x-coordinates must be monotonic"

    converging_radii = [float(point["radius_mm"]) for point in contour_points[: throat_index + 1]]
    diverging_radii = [float(point["radius_mm"]) for point in contour_points[throat_index:]]
    assert all(a >= b for a, b in zip(converging_radii, converging_radii[1:])), \
        "Converging branch should contract toward the throat"
    assert all(a <= b for a, b in zip(diverging_radii, diverging_radii[1:])), \
        "Diverging branch should expand away from the throat"
    assert abs(throat_radius_mm * 2.0 - float(values.get("nozzle_throat_diameter_mm", 0.0))) < 0.2


def test_export_includes_nozzle_geometry_metadata():
    """Assert export payload carries the nozzle contour metadata for downstream CAD/report use."""
    from liquid_engine_studio.concept_model import create_concept_design
    from liquid_engine_studio.exporter import build_cad_export_payload

    design = create_concept_design({})
    payload = build_cad_export_payload(design, {"total_score": 1.0})
    geometry_meta = payload["solver"]["stage_0_geometry"]

    assert geometry_meta["status"] == "calculated"
    assert geometry_meta["nozzle_contour_method"] == "moc_bell"
    assert float(geometry_meta["moc_exit_mach"]) > 1.0
    assert float(geometry_meta["bell_entrance_angle_deg"]) > float(geometry_meta["bell_exit_angle_deg"])
    assert float(geometry_meta["throat_entry_blend_radius_mm"]) > 0.0
    assert float(geometry_meta["throat_exit_blend_radius_mm"]) > 0.0


if __name__ == "__main__":
    # Run tests manually if pytest is not available
    import traceback
    
    test_functions = [
        test_station_numeric_promotion,
        test_numeric_value_ranges,
        test_json_export_contains_analysis_fields,
        test_json_export_numeric_fields,
        test_backward_compatibility_notes,
        test_regen_cooling_scenario,
        test_no_regen_cooling_scenario,
        test_csv_export_numeric_columns,
        test_solver_provenance_tagging,
        test_nozzle_contour_metadata_and_shape,
        test_export_includes_nozzle_geometry_metadata,
    ]
    
    for test_func in test_functions:
        try:
            test_func()
            print(f"✓ {test_func.__name__}")
        except Exception as e:
            print(f"✗ {test_func.__name__}: {e}")
            traceback.print_exc()




