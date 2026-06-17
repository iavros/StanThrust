"""Tests for section-based structural and thermal material outputs."""
import sys

sys.path.insert(0, r"E:/LIQUID_ENGINE")

from liquid_engine_studio.concept_model import create_concept_design
from liquid_engine_studio.combustion_cfd_solver import run_combustion_cfd_proxy
from liquid_engine_studio.solver_assumptions import get_default_solver_assumptions
from liquid_engine_studio.material_assignment_solver import assign_materials
from liquid_engine_studio.structural_material_solver import build_structural_materials_output
from liquid_engine_studio.exporter import build_cad_export_payload


def test_structural_materials_output_contains_sections():
	design = create_concept_design({})
	assumptions = get_default_solver_assumptions()
	comb = run_combustion_cfd_proxy(design, assumptions)
	# prepare a material assignment using inputs as 'materials' mapping
	design_request = {"materials": design.inputs.as_state()}
	mat = assign_materials(design_request, {})
	struct = build_structural_materials_output(design_request, {}, mat, comb)

	assert isinstance(struct, dict)
	payload = struct.get("payload", {})
	assert "section_property_rows" in payload
	assert isinstance(payload["section_property_rows"], list)
	assert len(payload["section_property_rows"]) >= 5
	sections = {row.get("section") for row in payload["section_property_rows"]}
	for expected in ("fuel_tank", "oxidizer_tank", "chamber", "throat", "nozzle"):
		assert expected in sections
	for row in payload["section_property_rows"]:
		fields = row.get("fields", {})
		assert "hoop_stress_mpa" in fields
		assert "allowable_stress_mpa" in fields
		assert "structural_margin_ratio" in fields
		assert "wall_temperature_k" in fields
		assert "thermal_margin_k" in fields
	# Ensure station_field_updates exist and have thermal_margin entries
	s_updates = payload.get("station_field_updates", {})
	assert isinstance(s_updates, dict)
	# Look for representative station labels - chamber and nozzle exit should always be present
	for label in ("Chamber Mid", "Nozzle Exit Plane"):
		assert label in s_updates
		tm = s_updates[label].get("thermal_margin")
		assert tm is None or isinstance(tm.get("value"), (float, type(None)))


def test_exporter_includes_thermal_margin_in_analysis_fields():
	design = create_concept_design({})
	assumptions = get_default_solver_assumptions()
	comb = run_combustion_cfd_proxy(design, assumptions)
	design_request = {"materials": design.inputs.as_state()}
	mat = assign_materials(design_request, {})
	struct = build_structural_materials_output(design_request, {}, mat, comb)

	payload = build_cad_export_payload(design, {"total_score": 1.0}, None, comb, None, struct)
	rows = payload.get("concept_station_rows", [])
	assert isinstance(rows, list)
	# each analysis_fields should include thermal_margin with source_solver
	for r in rows:
		af = r.get("analysis_fields", {})
		assert "thermal_margin" in af
		tm = af["thermal_margin"]
		assert "source_solver" in tm
		assert tm["source_solver"] in ("concept-solver", "Combustion CFD Proxy Solver", "Structural Material Solver")


def test_section_margins_are_numeric_and_positive_for_default_design():
	design = create_concept_design({})
	assumptions = get_default_solver_assumptions()
	comb = run_combustion_cfd_proxy(design, assumptions)
	design_request = {"materials": design.inputs.as_state()}
	mat = assign_materials(design_request, {})
	struct = build_structural_materials_output(design_request, {}, mat, comb)
	rows = struct.get("payload", {}).get("section_property_rows", [])

	for row in rows:
		fields = row.get("fields", {})
		assert fields["structural_margin_ratio"]["value"] >= 1.0
		assert fields["thermal_margin_index"]["value"] >= 0.0



