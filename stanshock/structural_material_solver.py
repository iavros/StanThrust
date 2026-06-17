from typing import Any, Dict, List, Optional

from stanshock.concept_model import create_concept_design


SOLVER_NAME = "Structural Material Solver"
SOLVER_VERSION = "0.2"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _field(unit: str, value: Optional[float], status: str) -> Dict[str, object]:
    return {
        "value": value,
        "unit": unit,
        "status": status,
        "source_solver": SOLVER_NAME,
    }


def _resolve_state(design_request: Dict[str, object]) -> Dict[str, object]:
    state = {}
    nested_state = _as_dict(design_request.get("state"))
    if nested_state:
        state.update(nested_state)
    materials = _as_dict(design_request.get("materials"))
    if materials:
        state.update(materials)
    if not state:
        state.update(design_request)
    return state


def build_structural_materials_output(
    design_request: Dict[str, object],
    concept_envelope_result: Dict[str, object],
    material_assignment_result: Dict[str, object],
    combustion_result: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Build section-based structural and thermal outputs from the active design state."""
    state = _resolve_state(design_request)
    design = create_concept_design(state)
    values = dict(design.derived.engineering_values)
    section_materials = list(_as_dict(material_assignment_result.get("payload", {})).get("section_materials", []))

    section_definitions = [
        ("fuel_tank", "Fuel Feed Inlet"),
        ("oxidizer_tank", "Fuel Feed Inlet"),
        ("chamber", "Chamber Mid"),
        ("throat", "Throat Region"),
        ("nozzle", "Nozzle Exit Plane"),
    ]

    material_lookup = {str(row.get("section", "")): str(row.get("material", "")) for row in section_materials}
    section_property_rows: List[Dict[str, object]] = []
    station_field_updates: Dict[str, Dict[str, object]] = {}

    for section_name, station_label in section_definitions:
        material_name = material_lookup.get(section_name, "")
        thermal_margin_index = float(values.get(f"{section_name}_thermal_margin_index", 0.0))
        thermal_margin_k = float(values.get(f"{section_name}_thermal_margin_k", 0.0))
        structural_margin_ratio = float(values.get(f"{section_name}_structural_margin_ratio", 0.0))
        section_fields = {
            "pressure_kpa": _field("kPa", float(values.get(f"{section_name}_pressure_kpa", 0.0)), "calculated"),
            "diameter_mm": _field("mm", float(values.get(f"{section_name}_diameter_mm", 0.0)), "calculated"),
            "wall_thickness_mm": _field(
                "mm",
                float(
                    values.get(
                        "nozzle_structural_wall_thickness_mm"
                        if section_name == "nozzle"
                        else f"{section_name}_wall_thickness_mm",
                        0.0,
                    )
                ),
                "calculated",
            ),
            "allowable_stress_mpa": _field("MPa", float(values.get(f"{section_name}_allowable_stress_mpa", 0.0)), "calculated"),
            "hoop_stress_mpa": _field("MPa", float(values.get(f"{section_name}_hoop_stress_mpa", 0.0)), "calculated"),
            "structural_margin_ratio": _field("x", structural_margin_ratio, "calculated"),
            "wall_temperature_k": _field("K", float(values.get(f"{section_name}_estimated_wall_temperature_k", 0.0)), "calculated"),
            "temperature_limit_k": _field("K", float(values.get(f"{section_name}_temperature_limit_k", 0.0)), "calculated"),
            "thermal_margin_k": _field("K", thermal_margin_k, "calculated"),
            "thermal_margin_index": _field("index", thermal_margin_index, "calculated"),
        }
        section_property_rows.append(
            {
                "section": section_name,
                "material": material_name,
                "fields": section_fields,
            }
        )
        station_field_updates.setdefault(station_label, {})
        station_field_updates[station_label]["thermal_margin"] = {
            "value": thermal_margin_index,
            "unit": "index",
            "status": "calculated",
            "source_solver": SOLVER_NAME,
        }
        station_field_updates[station_label]["structural_margin"] = {
            "value": structural_margin_ratio,
            "unit": "x",
            "status": "calculated",
            "source_solver": SOLVER_NAME,
        }

    return {
        "metadata": {
            "solver_name": SOLVER_NAME,
            "solver_version": SOLVER_VERSION,
            "solver_mode": "section-engineering-v1",
            "input_schema_version": "1.0",
            "output_schema_version": "1.0",
        },
        "status": "ok",
        "payload": {
            "section_property_rows": section_property_rows,
            "station_field_updates": station_field_updates,
            "design_request": design_request,
            "geometry_reference": _as_dict(_as_dict(concept_envelope_result.get("payload")).get("geometry_bundle", {})),
        },
        "warnings": [],
        "trace": [
            "Built section-based structural stress and thermal margin outputs from the current design state."
        ],
    }
