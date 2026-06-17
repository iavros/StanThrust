from typing import Any, Dict, List

from stanshock.materials import MATERIAL_OPTIONS


SOLVER_NAME = "Material Assignment Solver"
SOLVER_VERSION = "1.0"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


SECTION_KEYS = [
    ("fuel_tank", "fuel_tank_material"),
    ("oxidizer_tank", "oxidizer_tank_material"),
    ("feed_system", "feed_system_material"),
    ("chamber", "chamber_material"),
    ("nozzle", "nozzle_material"),
]


def assign_materials(
    design_request: Dict[str, object], concept_envelope_result: Dict[str, object]
) -> Dict[str, object]:
    materials = _as_dict(design_request.get("materials"))
    section_materials: List[Dict[str, object]] = []
    material_notes: List[str] = []
    validation_messages: List[str] = []
    compatibility_flags: Dict[str, bool] = {}

    for section_name, key in SECTION_KEYS:
        selected = str(materials.get(key, ""))
        in_catalog = selected in MATERIAL_OPTIONS
        compatibility_flags[section_name] = in_catalog
        section_materials.append(
            {
                "section": section_name,
                "material": selected,
                "catalog_status": "catalog" if in_catalog else "custom",
            }
        )
        if in_catalog:
            material_notes.append("{0}: using catalog material {1}".format(section_name, selected))
        else:
            validation_messages.append(
                "{0}: custom material accepted ({1}); downstream solver should validate later.".format(
                    section_name, selected
                )
            )

    return {
        "metadata": {
            "solver_name": SOLVER_NAME,
            "solver_version": SOLVER_VERSION,
            "solver_mode": "concept-only",
            "input_schema_version": "1.0",
            "output_schema_version": "1.0",
        },
        "status": "ok",
        "payload": {
            "section_materials": section_materials,
            "material_notes": material_notes,
            "validation_messages": validation_messages,
            "compatibility_flags": compatibility_flags,
            "geometry_reference": _as_dict(concept_envelope_result.get("payload")).get("geometry_bundle", {}),
        },
        "warnings": validation_messages,
        "trace": ["Section materials assigned against catalog references."],
    }


def describe_section_materials(material_assignment_result: Dict[str, object]) -> List[Dict[str, str]]:
    payload = _as_dict(material_assignment_result.get("payload"))
    rows = []
    for item in payload.get("section_materials", []):
        rows.append(
            {
                "section": item.get("section", ""),
                "material": item.get("material", ""),
                "catalog_status": item.get("catalog_status", "unknown"),
                "note": "Concept-only assignment",
            }
        )
    return rows


