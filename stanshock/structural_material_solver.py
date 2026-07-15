from typing import Any, Dict, List, Optional

from stanshock.design_model import (
    MATERIAL_ALLOWABLE_STRESS_MPA,
    MATERIAL_TEMPERATURE_LIMIT_K,
    clamp,
    create_engine_design,
)
from stanshock.heat_transfer_solver import MATERIAL_THERMAL_CONDUCTIVITY_W_M_K
from stanshock.materials import MATERIAL_OPTIONS


SOLVER_NAME = "Structural Material Solver"
SOLVER_VERSION = "0.3"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _field(unit: str, value: Optional[float], status: str) -> Dict[str, object]:
    return {
        "value": value,
        "unit": unit,
        "status": status,
        "source_solver": SOLVER_NAME,
    }


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    return numeric if numeric == numeric else fallback


def _temperature_derating(wall_temperature_k: float, temperature_limit_k: float) -> float:
    if wall_temperature_k <= 0.55 * temperature_limit_k:
        return 1.0
    if wall_temperature_k <= temperature_limit_k:
        progress = (wall_temperature_k - 0.55 * temperature_limit_k) / max(
            1e-6,
            0.45 * temperature_limit_k,
        )
        return clamp(1.0 - 0.55 * progress, 0.45, 1.0)
    over_limit_ratio = wall_temperature_k / max(1e-6, temperature_limit_k)
    return clamp(0.45 / max(1.0, over_limit_ratio), 0.12, 0.45)


def _hoop_stress_mpa(pressure_kpa: float, diameter_mm: float, wall_thickness_mm: float) -> float:
    radius_m = max(1e-6, diameter_mm / 2000.0)
    thickness_m = max(1e-6, wall_thickness_mm / 1000.0)
    return pressure_kpa * 1000.0 * radius_m / thickness_m / 1_000_000.0


def _required_wall_thickness_mm(
    pressure_kpa: float,
    diameter_mm: float,
    allowable_stress_mpa: float,
    factor_of_safety: float,
) -> float:
    radius_m = max(1e-6, diameter_mm / 2000.0)
    thickness_m = pressure_kpa * 1000.0 * radius_m * max(1.0, factor_of_safety) / max(
        1e-6,
        allowable_stress_mpa * 1_000_000.0,
    )
    return max(0.2, thickness_m * 1000.0)


def _heat_sections(combustion_result: Optional[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    if not isinstance(combustion_result, dict):
        return {}
    heat_transfer = _as_dict(combustion_result.get("heat_transfer"))
    sections = heat_transfer.get("sections", [])
    if not isinstance(sections, list):
        return {}
    return {str(section.get("name", "")).strip().lower(): section for section in sections if isinstance(section, dict)}


def _wall_state_from_heat_section(
    heat_section: Optional[Dict[str, object]],
    material_name: str,
    wall_thickness_mm: float,
    fallback_wall_temperature_k: float,
) -> Dict[str, float]:
    conductivity = MATERIAL_THERMAL_CONDUCTIVITY_W_M_K.get(material_name, 16.0)
    if not heat_section:
        return {
            "hot_wall_temperature_k": fallback_wall_temperature_k,
            "cold_wall_temperature_k": fallback_wall_temperature_k,
            "heat_flux_w_m2": 0.0,
            "heat_load_kw": 0.0,
            "conductivity_w_m_k": conductivity,
        }

    gas_h = max(1e-6, _safe_float(heat_section.get("gas_side_h_w_m2_k"), 0.0))
    coolant_h = max(1e-6, _safe_float(heat_section.get("coolant_side_h_w_m2_k"), 12.0))
    recovery_temperature_k = _safe_float(
        heat_section.get("effective_recovery_temperature_k"),
        fallback_wall_temperature_k,
    )
    sink_temperature_k = _safe_float(
        heat_section.get("coolant_inlet_temperature_k"),
        _safe_float(heat_section.get("cold_wall_temperature_k"), 293.0),
    )
    wall_resistance = max(1e-6, wall_thickness_mm / 1000.0) / max(1e-6, conductivity)
    total_resistance = 1.0 / gas_h + wall_resistance + 1.0 / coolant_h
    heat_flux_w_m2 = max(0.0, (recovery_temperature_k - sink_temperature_k) / max(1e-9, total_resistance))
    hot_wall_temperature_k = recovery_temperature_k - heat_flux_w_m2 / gas_h
    cold_wall_temperature_k = sink_temperature_k + heat_flux_w_m2 / coolant_h
    diameter_mm = max(0.1, _safe_float(heat_section.get("diameter_mm"), 0.0))
    length_mm = max(0.1, _safe_float(heat_section.get("length_mm"), 0.0))
    area_m2 = 3.141592653589793 * diameter_mm / 1000.0 * length_mm / 1000.0

    return {
        "hot_wall_temperature_k": hot_wall_temperature_k,
        "cold_wall_temperature_k": cold_wall_temperature_k,
        "heat_flux_w_m2": heat_flux_w_m2,
        "heat_load_kw": heat_flux_w_m2 * area_m2 / 1000.0,
        "conductivity_w_m_k": conductivity,
    }


def _material_eval(
    material_name: str,
    pressure_kpa: float,
    diameter_mm: float,
    wall_thickness_mm: float,
    factor_of_safety: float,
    fallback_wall_temperature_k: float,
    heat_section: Optional[Dict[str, object]],
) -> Dict[str, object]:
    allowable_stress_mpa = MATERIAL_ALLOWABLE_STRESS_MPA.get(material_name, 105.0)
    temperature_limit_k = MATERIAL_TEMPERATURE_LIMIT_K.get(material_name, 700.0)
    wall_state = _wall_state_from_heat_section(
        heat_section,
        material_name,
        wall_thickness_mm,
        fallback_wall_temperature_k,
    )
    hot_wall_temperature_k = wall_state["hot_wall_temperature_k"]
    derating = _temperature_derating(hot_wall_temperature_k, temperature_limit_k)
    derated_allowable_stress_mpa = allowable_stress_mpa * derating
    hoop_stress_mpa = _hoop_stress_mpa(pressure_kpa, diameter_mm, wall_thickness_mm)
    stress_margin_ratio = derated_allowable_stress_mpa / max(
        1e-6,
        hoop_stress_mpa * max(1.0, factor_of_safety),
    )
    thermal_margin_k = temperature_limit_k - hot_wall_temperature_k
    heat_transfer_margin_ratio = temperature_limit_k / max(1e-6, hot_wall_temperature_k)
    combined_margin_ratio = min(stress_margin_ratio, heat_transfer_margin_ratio)
    required_wall_thickness_mm = _required_wall_thickness_mm(
        pressure_kpa,
        diameter_mm,
        derated_allowable_stress_mpa,
        factor_of_safety,
    )
    status = "pass" if combined_margin_ratio >= 1.0 else "redesign-required"
    if stress_margin_ratio < 1.0 and heat_transfer_margin_ratio < 1.0:
        status = "stress-and-thermal-redesign-required"
    elif stress_margin_ratio < 1.0:
        status = "stress-redesign-required"
    elif heat_transfer_margin_ratio < 1.0:
        status = "thermal-redesign-required"

    return {
        "material": material_name,
        "catalog_status": "catalog" if material_name in MATERIAL_OPTIONS else "custom",
        "allowable_stress_mpa": round(allowable_stress_mpa, 4),
        "temperature_derating_factor": round(derating, 6),
        "temperature_derated_allowable_stress_mpa": round(derated_allowable_stress_mpa, 4),
        "thermal_conductivity_w_m_k": round(wall_state["conductivity_w_m_k"], 4),
        "temperature_limit_k": round(temperature_limit_k, 4),
        "hot_wall_temperature_k": round(hot_wall_temperature_k, 4),
        "cold_wall_temperature_k": round(wall_state["cold_wall_temperature_k"], 4),
        "heat_flux_w_m2": round(wall_state["heat_flux_w_m2"], 4),
        "heat_load_kw": round(wall_state["heat_load_kw"], 6),
        "hoop_stress_mpa": round(hoop_stress_mpa, 4),
        "stress_margin_ratio": round(stress_margin_ratio, 6),
        "heat_transfer_margin_ratio": round(heat_transfer_margin_ratio, 6),
        "thermal_margin_k": round(thermal_margin_k, 4),
        "combined_margin_ratio": round(combined_margin_ratio, 6),
        "required_wall_thickness_mm": round(required_wall_thickness_mm, 4),
        "recommended_wall_thickness_mm": round(max(wall_thickness_mm, required_wall_thickness_mm), 4),
        "status": status,
    }


def _rank_materials(
    current_material: str,
    pressure_kpa: float,
    diameter_mm: float,
    wall_thickness_mm: float,
    factor_of_safety: float,
    fallback_wall_temperature_k: float,
    heat_section: Optional[Dict[str, object]],
) -> List[Dict[str, object]]:
    materials = list(MATERIAL_OPTIONS)
    if current_material and current_material not in materials:
        materials.append(current_material)
    evaluations = [
        _material_eval(
            material,
            pressure_kpa,
            diameter_mm,
            wall_thickness_mm,
            factor_of_safety,
            fallback_wall_temperature_k,
            heat_section,
        )
        for material in materials
    ]
    return sorted(
        evaluations,
        key=lambda row: (
            _safe_float(row.get("combined_margin_ratio"), 0.0) >= 1.0,
            _safe_float(row.get("combined_margin_ratio"), 0.0),
            _safe_float(row.get("stress_margin_ratio"), 0.0),
        ),
        reverse=True,
    )


def _redesign_note(section_name: str, selected: Dict[str, object], recommended: Dict[str, object]) -> str:
    status = str(selected.get("status", "unknown"))
    if status == "pass":
        return "{0}: selected material and wall pass stress and heat-transfer checks.".format(section_name)
    actions = []
    if _safe_float(selected.get("stress_margin_ratio"), 0.0) < 1.0:
        actions.append(
            "increase wall to at least {0:.2f} mm for stress".format(
                _safe_float(selected.get("recommended_wall_thickness_mm"), 0.0)
            )
        )
    if _safe_float(selected.get("heat_transfer_margin_ratio"), 0.0) < 1.0:
        if _safe_float(recommended.get("combined_margin_ratio"), 0.0) >= 1.0:
            actions.append("switch to {0} for heat margin".format(recommended.get("material", "recommended material")))
        else:
            actions.append("increase cooling capacity or reduce hot-wall temperature")
    if not actions:
        actions.append("review selected material")
    return "{0}: {1}.".format(section_name, "; ".join(actions))


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
    design_envelope_result: Dict[str, object],
    material_assignment_result: Dict[str, object],
    combustion_result: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Build section-based structural and thermal outputs from the active design state."""
    state = _resolve_state(design_request)
    design = create_engine_design(state)
    values = dict(design.derived.engineering_values)
    section_materials = list(_as_dict(material_assignment_result.get("payload", {})).get("section_materials", []))
    heat_sections = _heat_sections(combustion_result)
    combustion_summary = _as_dict(combustion_result.get("summary")) if isinstance(combustion_result, dict) else {}

    section_definitions = [
        ("fuel_tank", "Fuel Feed Inlet", "fuel_tank_material"),
        ("oxidizer_tank", "Fuel Feed Inlet", "oxidizer_tank_material"),
        ("chamber", "Chamber Mid", "chamber_material"),
        ("throat", "Throat Region", "nozzle_material"),
        ("nozzle", "Nozzle Exit Plane", "nozzle_material"),
    ]

    material_lookup = {str(row.get("section", "")): str(row.get("material", "")) for row in section_materials}
    section_property_rows: List[Dict[str, object]] = []
    station_field_updates: Dict[str, Dict[str, object]] = {}
    redesign_recommendations: List[Dict[str, object]] = []

    for section_name, station_label, material_key in section_definitions:
        material_name = material_lookup.get(section_name, "") or str(state.get(material_key, ""))
        if section_name == "throat":
            material_name = material_name or material_lookup.get("nozzle", "") or str(state.get("nozzle_material", ""))
        heat_section = heat_sections.get(station_label.strip().lower())
        pressure_kpa = _safe_float(values.get(f"{section_name}_pressure_kpa"), 0.0)
        if section_name == "throat":
            pressure_kpa = _safe_float(
                combustion_summary.get("throat_pressure_kpa"),
                _safe_float(values.get("chamber_pressure_kpa"), 0.0) * 0.55,
            )
        elif section_name == "nozzle":
            pressure_kpa = _safe_float(
                combustion_summary.get("exit_pressure_kpa"),
                _safe_float(values.get("chamber_pressure_kpa"), 0.0) * 0.08,
            )
        diameter_k = f"{section_name}_diameter_mm"
        diameter_mm = _safe_float(values.get(diameter_k), 0.0)
        if section_name == "chamber":
            diameter_mm = _safe_float(values.get("chamber_inner_diameter_mm"), diameter_mm)
        elif section_name == "throat":
            diameter_mm = _safe_float(values.get("nozzle_throat_diameter_mm"), diameter_mm)
        elif section_name == "nozzle":
            diameter_mm = _safe_float(values.get("nozzle_inner_diameter_mm"), diameter_mm)
        wall_thickness_key = (
            "nozzle_structural_wall_thickness_mm"
            if section_name == "nozzle"
            else "throat_wall_thickness_mm"
            if section_name == "throat"
            else f"{section_name}_wall_thickness_mm"
        )
        wall_thickness_mm = _safe_float(values.get(wall_thickness_key), 1.0)
        fallback_wall_temperature_k = _safe_float(
            values.get(f"{section_name}_wall_temperature_k"),
            _safe_float(heat_section.get("hot_wall_temperature_k") if heat_section else None, 293.0),
        )
        factor_of_safety = _safe_float(values.get("factor_of_safety"), _safe_float(state.get("factor_of_safety"), 2.0))
        selected_eval = _material_eval(
            material_name,
            pressure_kpa,
            diameter_mm,
            wall_thickness_mm,
            factor_of_safety,
            fallback_wall_temperature_k,
            heat_section,
        )
        material_evaluations = _rank_materials(
            material_name,
            pressure_kpa,
            diameter_mm,
            wall_thickness_mm,
            factor_of_safety,
            fallback_wall_temperature_k,
            heat_section,
        )
        if selected_eval["status"] == "pass":
            recommended_eval = selected_eval
        else:
            recommended_eval = next(
                (
                    row
                    for row in material_evaluations
                    if _safe_float(row.get("combined_margin_ratio"), 0.0) >= 1.0
                ),
                material_evaluations[0] if material_evaluations else selected_eval,
            )
        redesign_note = _redesign_note(section_name, selected_eval, recommended_eval)
        if selected_eval["status"] != "pass":
            redesign_recommendations.append(
                {
                    "section": section_name,
                    "current_material": material_name,
                    "recommended_material": recommended_eval.get("material", material_name),
                    "current_wall_thickness_mm": round(wall_thickness_mm, 4),
                    "recommended_wall_thickness_mm": selected_eval["recommended_wall_thickness_mm"],
                    "current_combined_margin_ratio": selected_eval["combined_margin_ratio"],
                    "redesigned_combined_margin_ratio": recommended_eval.get("combined_margin_ratio", selected_eval["combined_margin_ratio"]),
                    "status": selected_eval["status"],
                    "note": redesign_note,
                }
            )
        section_fields = {
            "pressure_kpa": _field("kPa", round(pressure_kpa, 4), "calculated"),
            "diameter_mm": _field("mm", round(diameter_mm, 4), "calculated"),
            "wall_thickness_mm": _field("mm", round(wall_thickness_mm, 4), "calculated"),
            "allowable_stress_mpa": _field("MPa", _safe_float(selected_eval.get("allowable_stress_mpa")), "calculated"),
            "temperature_derated_allowable_stress_mpa": _field(
                "MPa",
                _safe_float(selected_eval.get("temperature_derated_allowable_stress_mpa")),
                "calculated",
            ),
            "hoop_stress_mpa": _field("MPa", _safe_float(selected_eval.get("hoop_stress_mpa")), "calculated"),
            "structural_margin_ratio": _field("x", _safe_float(selected_eval.get("stress_margin_ratio")), "calculated"),
            "wall_temperature_k": _field("K", _safe_float(selected_eval.get("hot_wall_temperature_k")), "calculated"),
            "cold_wall_temperature_k": _field("K", _safe_float(selected_eval.get("cold_wall_temperature_k")), "calculated"),
            "temperature_limit_k": _field("K", _safe_float(selected_eval.get("temperature_limit_k")), "calculated"),
            "thermal_margin_k": _field("K", _safe_float(selected_eval.get("thermal_margin_k")), "calculated"),
            "thermal_margin_index": _field(
                "index",
                round(clamp(_safe_float(selected_eval.get("heat_transfer_margin_ratio")) * 100.0, 0.0, 100.0), 4),
                "calculated",
            ),
            "heat_transfer_margin_ratio": _field("x", _safe_float(selected_eval.get("heat_transfer_margin_ratio")), "calculated"),
            "combined_margin_ratio": _field("x", _safe_float(selected_eval.get("combined_margin_ratio")), "calculated"),
            "required_wall_thickness_mm": _field("mm", _safe_float(selected_eval.get("required_wall_thickness_mm")), "calculated"),
            "recommended_wall_thickness_mm": _field("mm", _safe_float(selected_eval.get("recommended_wall_thickness_mm")), "calculated"),
            "recommended_material": _field("", None, "calculated"),
            "redesign_status": _field("", None, str(selected_eval.get("status", "unknown"))),
        }
        section_fields["recommended_material"]["value"] = str(recommended_eval.get("material", material_name))
        section_fields["redesign_note"] = _field("", None, "calculated")
        section_fields["redesign_note"]["value"] = redesign_note
        section_property_rows.append(
            {
                "section": section_name,
                "material": material_name,
                "fields": section_fields,
                "material_catalog_evaluation": material_evaluations,
                "selected_material_evaluation": selected_eval,
                "recommended_material_evaluation": recommended_eval,
            }
        )
        station_field_updates.setdefault(station_label, {})
        station_field_updates[station_label]["thermal_margin"] = {
            "value": section_fields["thermal_margin_index"]["value"],
            "unit": "index",
            "status": "calculated",
            "source_solver": SOLVER_NAME,
        }
        station_field_updates[station_label]["structural_margin"] = {
            "value": _safe_float(selected_eval.get("stress_margin_ratio")),
            "unit": "x",
            "status": "calculated",
            "source_solver": SOLVER_NAME,
        }
        station_field_updates[station_label]["combined_material_margin"] = {
            "value": _safe_float(selected_eval.get("combined_margin_ratio")),
            "unit": "x",
            "status": "calculated",
            "source_solver": SOLVER_NAME,
        }

    minimum_combined_margin = min(
        (
            _safe_float(_as_dict(row.get("fields")).get("combined_margin_ratio", {}).get("value"), 0.0)
            for row in section_property_rows
        ),
        default=0.0,
    )
    minimum_stress_margin = min(
        (
            _safe_float(_as_dict(row.get("fields")).get("structural_margin_ratio", {}).get("value"), 0.0)
            for row in section_property_rows
        ),
        default=0.0,
    )
    minimum_heat_margin = min(
        (
            _safe_float(_as_dict(row.get("fields")).get("heat_transfer_margin_ratio", {}).get("value"), 0.0)
            for row in section_property_rows
        ),
        default=0.0,
    )
    status = "ok" if minimum_combined_margin >= 1.0 else "warning"
    warnings = [str(item["note"]) for item in redesign_recommendations]
    return {
        "metadata": {
            "solver_name": SOLVER_NAME,
            "solver_version": SOLVER_VERSION,
            "solver_mode": "section-engineering-v1",
            "input_schema_version": "1.0",
            "output_schema_version": "1.0",
        },
        "status": status,
        "payload": {
            "section_property_rows": section_property_rows,
            "station_field_updates": station_field_updates,
            "summary": {
                "minimum_stress_margin_ratio": round(minimum_stress_margin, 6),
                "minimum_heat_transfer_margin_ratio": round(minimum_heat_margin, 6),
                "minimum_combined_margin_ratio": round(minimum_combined_margin, 6),
                "redesign_required": bool(redesign_recommendations),
                "redesign_recommendation_count": len(redesign_recommendations),
            },
            "redesign_recommendations": redesign_recommendations,
            "design_request": design_request,
            "geometry_reference": _as_dict(_as_dict(design_envelope_result.get("payload")).get("geometry_bundle", {})),
        },
        "warnings": warnings,
        "trace": [
            "Built section-based structural stress, heat-transfer, material ranking, and redesign outputs from the current design state."
        ],
    }
