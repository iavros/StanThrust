"""Project persistence and CAD/CSV/DXF export of the solved design state."""

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from stanthrust.benchmark_cases import (
    get_internal_baseline_cases,
    get_public_benchmark_cases,
)
from stanthrust.design_model import EngineDesign
from stanthrust.uncertainty import (
    ProvenanceField,
    UncertaintyBand,
    build_uncertainty_summary,
    calculate_field_confidence,
)

PROJECT_SCHEMA_VERSION = 1


@dataclass
class ProjectDocument:
    version: int
    generated_at: str
    state: Dict[str, object]
    objective_weights: Dict[str, float]
    ga_result: Optional[Dict[str, object]] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "state": self.state,
            "objective_weights": self.objective_weights,
            "ga_result": self.ga_result,
        }


def build_project_document(
    state: Dict[str, object],
    objective_weights: Dict[str, float],
    ga_result: Optional[Dict[str, object]] = None,
) -> ProjectDocument:
    generated_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return ProjectDocument(
        version=PROJECT_SCHEMA_VERSION,
        generated_at=generated_at,
        state=state,
        objective_weights=objective_weights,
        ga_result=ga_result,
    )


def save_project(
    path: Path,
    state: Dict[str, object],
    objective_weights: Dict[str, float],
    ga_result: Optional[Dict[str, object]] = None,
) -> None:
    document = build_project_document(state, objective_weights, ga_result)
    path.write_text(json.dumps(document.as_dict(), indent=2), encoding="utf-8")


def load_project(path: Path) -> ProjectDocument:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ProjectDocument(
        version=int(raw.get("version", PROJECT_SCHEMA_VERSION)),
        generated_at=str(raw.get("generated_at", "")),
        state=dict(raw.get("state", {})),
        objective_weights=dict(raw.get("objective_weights", {})),
        ga_result=raw.get("ga_result"),
    )


def _as_dict(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_float(value: object, fallback: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback


def _build_analysis_field(
    field_name: str,
    value: Optional[float],
    unit: str,
    status: str,
    source_solver: str,
    station_field_updates: Dict[str, Dict[str, object]],
    row_label: str,
) -> Dict[str, object]:
    """Build an analysis field with provenance and confidence data.

    Merges the value and status from ``station_field_updates`` when available and
    adds the confidence and uncertainty bands from the uncertainty module.
    """
    # Get value and status from station updates or row-level data.
    row_updates = _as_dict(station_field_updates.get(row_label, {}))
    field_updates = _as_dict(row_updates.get(field_name, {}))
    final_value = field_updates.get("value") if field_updates.get("value") is not None else value
    final_status = str(field_updates.get("status", status))
    final_source = str(field_updates.get("source_solver", source_solver))
    confidence_key = _analysis_confidence_key(field_name)

    # Build base field
    base_field = {
        "value": final_value,
        "unit": field_updates.get("unit", unit),
        "status": final_status,
        "source_solver": final_source,
    }

    # Attempt to add confidence and uncertainty
    if final_value is not None and isinstance(final_value, (int, float)):
        confidence_field = calculate_field_confidence(
            confidence_key,
            final_value,
            final_source,
            status=final_status,
        )

        if confidence_field is not None:
            base_field["confidence"] = round(confidence_field.confidence, 3)
            base_field["uncertainty"] = {
                "lower_percent": round(confidence_field.uncertainty.lower_percent, 2),
                "upper_percent": round(confidence_field.uncertainty.upper_percent, 2),
            }

    return base_field


def _analysis_confidence_key(field_name: str) -> str:
    return {
        "temperature_k": "station_temperature_k",
        "pressure_kpa": "station_pressure_kpa",
        "mass_flow_kg_s": "station_mass_flow_kg_s",
        "mach_number": "station_mach_number",
        "thermal_margin_index": "thermal_margin_index",
    }.get(field_name, field_name)


def _extract_station_field_updates(
    combustion_result: Optional[Dict[str, object]] = None,
    solver_interface_result: Optional[Dict[str, object]] = None,
    structural_result: Optional[Dict[str, object]] = None,
) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Extract station_field_updates from solvers and merge with provenance tags."""
    updates = {}

    # Extract from combustion solver
    if isinstance(combustion_result, dict):
        comb_updates = combustion_result.get("station_field_updates")
        if isinstance(comb_updates, dict):
            for station_label, fields in comb_updates.items():
                if station_label not in updates:
                    updates[station_label] = {}
                if isinstance(fields, dict):
                    updates[station_label].update(fields)

    # Extract from solver interface (feed solver wrapped)
    if isinstance(solver_interface_result, dict):
        payload = solver_interface_result.get("payload", {})
        if isinstance(payload, dict):
            feed_updates = payload.get("station_field_updates")
            if isinstance(feed_updates, dict):
                for station_label, fields in feed_updates.items():
                    if station_label not in updates:
                        updates[station_label] = {}
                    if isinstance(fields, dict):
                        updates[station_label].update(fields)

    # Extract from structural solver station updates
    if isinstance(structural_result, dict):
        struct_updates = _as_dict(structural_result.get("payload", {})).get("station_field_updates")
        if isinstance(struct_updates, dict):
            for station_label, fields in struct_updates.items():
                if station_label not in updates:
                    updates[station_label] = {}
                if isinstance(fields, dict):
                    updates[station_label].update(fields)

    return updates


def _build_uncertainty_summary_from_rows(rows: list) -> Dict[str, object]:
    provenance_fields = {}
    for row in rows:
        row_label = row.get("label", "")
        analysis_fields = _as_dict(row.get("analysis_fields", {}))
        for field_name, field_data in analysis_fields.items():
            field_data = _as_dict(field_data)
            value = field_data.get("value")
            confidence = field_data.get("confidence")
            uncertainty = _as_dict(field_data.get("uncertainty"))
            if not isinstance(value, (int, float)):
                continue
            if confidence is None or not uncertainty:
                continue
            provenance_fields[f"{row_label}.{field_name}"] = ProvenanceField(
                value=float(value),
                unit=str(field_data.get("unit", "")),
                status=str(field_data.get("status", "unknown")),
                source_solver=str(field_data.get("source_solver", "unknown")),
                confidence=_as_float(confidence),
                uncertainty=UncertaintyBand(
                    lower_percent=_as_float(uncertainty.get("lower_percent", 0.0)),
                    upper_percent=_as_float(uncertainty.get("upper_percent", 0.0)),
                    confidence=_as_float(confidence),
                    notes=str(field_data.get("uncertainty_notes", "")),
                ),
            )

    summary = build_uncertainty_summary(provenance_fields)
    summary["status"] = "calculated" if provenance_fields else "not-available"
    if not provenance_fields:
        summary["note"] = "No confidence-tagged station analysis fields were available for this export."
    return summary


def _build_stage_1_thermochemistry_metadata(
    combustion_result: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    metadata = _as_dict(combustion_result.get("metadata", {})) if isinstance(combustion_result, dict) else {}
    thermochemistry = _as_dict(metadata.get("thermochemistry", {}))
    if not thermochemistry:
        return {
            "status": "not-available",
            "note": "Combustion solver output was not provided for this export.",
        }
    return {
        "requested_mode": thermochemistry.get("requested_mode", thermochemistry.get("mode", "auto")),
        "effective_mode": thermochemistry.get("effective_mode", "auto"),
        "provider": thermochemistry.get("provider", "unknown"),
        "source": thermochemistry.get("source", "unknown"),
        "status": thermochemistry.get("status", "unknown"),
        "cantera_solved": bool(thermochemistry.get("cantera_solved", False)),
        "note": thermochemistry.get("note", ""),
    }


def _build_stage_2_feed_metadata(
    solver_interface_result: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    payload = _as_dict(solver_interface_result.get("payload", {})) if isinstance(solver_interface_result, dict) else {}
    feed_result = _as_dict(payload.get("feed_pressure_drop", {}))
    if not feed_result:
        return {
            "status": "not-available",
            "note": "Feed stage output was not provided for this export.",
        }
    metadata = _as_dict(feed_result.get("metadata", {}))
    feed_payload = _as_dict(feed_result.get("payload", {}))
    summary = _as_dict(feed_payload.get("summary", {}))
    return {
        "solver_mode": metadata.get("solver_mode", "unknown"),
        "status": feed_result.get("status", "unknown"),
        "model_status": summary.get("model_status", "unknown"),
        "quality_flag": summary.get("quality_flag", "unknown"),
        "total_pressure_drop_kpa": summary.get("total_pressure_drop_kpa"),
        "history_step_count": summary.get("history_step_count"),
        "initial_chamber_pressure_kpa": summary.get("initial_chamber_pressure_kpa"),
        "final_chamber_pressure_kpa": summary.get("final_chamber_pressure_kpa"),
        "minimum_feed_margin_kpa": summary.get("minimum_feed_margin_kpa"),
        "final_fuel_tank_pressure_kpa": summary.get("final_fuel_tank_pressure_kpa"),
        "final_oxidizer_tank_pressure_kpa": summary.get("final_oxidizer_tank_pressure_kpa"),
        "chamber_pressure_drift_percent": summary.get("chamber_pressure_drift_percent"),
        "maximum_pump_speed_fraction": summary.get("maximum_pump_speed_fraction"),
        "note": "Reduced-order transient feed model; not yet a calibrated hydraulic network or valve-level simulation.",
    }


def _build_stage_2_feed_history(
    solver_interface_result: Optional[Dict[str, object]] = None,
) -> List[Dict[str, object]]:
    payload = _as_dict(solver_interface_result.get("payload", {})) if isinstance(solver_interface_result, dict) else {}
    feed_result = _as_dict(payload.get("feed_pressure_drop", {}))
    feed_payload = _as_dict(feed_result.get("payload", {}))
    history_rows = feed_payload.get("time_history_rows", [])
    return list(history_rows) if isinstance(history_rows, list) else []


def _build_stage_2_nozzle_metadata(
    combustion_result: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    result = dict(combustion_result) if isinstance(combustion_result, dict) else {}
    metadata = _as_dict(result.get("metadata", {}))
    physics = _as_dict(result.get("physics", {}))
    coefficients = _as_dict(physics.get("coefficients", {}))
    nozzle = _as_dict(physics.get("nozzle", {}))
    if not nozzle:
        return {
            "status": "not-available",
            "note": "Combustion solver nozzle output was not provided for this export.",
        }
    return {
        "solver_stage": metadata.get("solver_stage", "unknown"),
        "solver_mode": metadata.get("solver_mode", "unknown"),
        "flow_model": metadata.get("flow_model", "unknown"),
        "flow_model_label": metadata.get("flow_model_label", "unknown"),
        "status": result.get("status", "unknown"),
        "status_detail": result.get("status_detail", "unknown"),
        "half_angle_deg": nozzle.get("half_angle_deg", coefficients.get("nozzle_half_angle_deg")),
        "reference_half_angle_deg": nozzle.get("reference_half_angle_deg", ""),
        "divergence_efficiency": nozzle.get("divergence_efficiency", coefficients.get("divergence_efficiency")),
        "boundary_layer_efficiency": nozzle.get(
            "boundary_layer_efficiency", coefficients.get("boundary_layer_efficiency")
        ),
        "geometry_efficiency": nozzle.get("geometry_efficiency", coefficients.get("nozzle_geometry_efficiency")),
        "overall_efficiency": nozzle.get("overall_efficiency", ""),
        "loss_fraction": nozzle.get("loss_fraction", coefficients.get("nozzle_loss_fraction")),
        "separation_efficiency": nozzle.get("separation_efficiency", coefficients.get("separation_efficiency")),
        "ambient_correction": nozzle.get("ambient_correction", coefficients.get("ambient_correction")),
        "exit_pressure_kpa": nozzle.get("exit_pressure_kpa", _as_dict(physics.get("results", {})).get("exit_pressure_kpa")),
        "note": (
            "Viscous mode uses Cantera thermochemistry, a characteristic-net nozzle contour, shock feedback, and quasi-1D station corrections; external CFD validation is still recommended."
            if metadata.get("flow_model") == "viscous"
            else "Refined mode uses the characteristic-net nozzle contour with pressure-root solving and shock feedback."
            if metadata.get("flow_model") == "refined"
            else "Fast preview mode uses geometry-aware nozzle losses and is intended for quick iteration."
        ),
    }


def _build_stage_0_geometry_metadata(design: EngineDesign) -> Dict[str, object]:
    values = dict(design.derived.engineering_values)
    contour_method = str(values.get("nozzle_contour_method", "")).strip()
    if not contour_method:
        return {
            "status": "not-available",
            "note": "No nozzle contour metadata was available for this export.",
        }
    return {
        "status": "calculated",
        "nozzle_contour_method": contour_method,
        "nozzle_contour_method_label": values.get("nozzle_contour_method_label", contour_method),
        "reference_conical_half_angle_deg": values.get("nozzle_reference_conical_half_angle_deg"),
        "reference_conical_length_mm": values.get("nozzle_reference_conical_length_mm"),
        "bell_length_fraction": values.get("nozzle_bell_length_fraction"),
        "bell_entrance_angle_deg": values.get("nozzle_bell_entrance_angle_deg"),
        "bell_exit_angle_deg": values.get("nozzle_bell_exit_angle_deg"),
        "moc_gamma": values.get("nozzle_moc_gamma"),
        "moc_exit_mach": values.get("nozzle_moc_exit_mach"),
        "moc_prandtl_meyer_exit_deg": values.get("nozzle_moc_prandtl_meyer_exit_deg"),
        "moc_turn_angle_deg": values.get("nozzle_moc_turn_angle_deg"),
        "throat_entry_blend_radius_mm": values.get("nozzle_throat_entry_blend_radius_mm"),
        "throat_exit_blend_radius_mm": values.get("nozzle_throat_exit_blend_radius_mm"),
        "note": "The exported nozzle contour uses a Prandtl-Meyer method-of-characteristics angle closure with explicit throat blend radii and bell-angle metadata.",
    }


def _build_benchmark_reference_metadata() -> Dict[str, object]:
    return {
        "public_reference_cases": [
            {
                "engine": case.engine,
                "team": case.team,
                "fuel_name": case.fuel_name,
                "oxidizer_name": case.oxidizer_name,
                "reference_thrust_n": round(case.target_thrust_newtons, 3),
                "reference_chamber_pressure_kpa": round(case.reference_chamber_pressure_kpa, 3),
                "reference_burn_time_seconds": round(case.reference_burn_time_seconds, 3),
                "reference_mass_flow_kg_s": case.reference_mass_flow_kg_s,
                "reference_isp_seconds": case.reference_isp_seconds,
                "mixture_ratio_model": round(case.mixture_ratio, 4),
                "feed_mode_model": "pump-fed" if case.use_pumps else "pressure-fed",
                "regen_cooling_model": case.regen_cooling,
                "film_cooling_model": case.film_cooling,
                "source_label": case.source_label,
                "source_url": case.source_url,
                "assumptions_note": case.assumptions_note,
            }
            for case in get_public_benchmark_cases()
        ],
        "internal_regression_cases": [
            {
                "case_id": case.case_id,
                "label": case.label,
                "state": dict(case.state),
                "expected_ranges": {metric: list(bounds) for metric, bounds in case.expected_ranges.items()},
                "note": case.note,
            }
            for case in get_internal_baseline_cases()
        ],
        "note": "These benchmark definitions are the canonical machine-readable regression cases used by the report generator and automated tests.",
    }


def build_cad_export_payload(
    design: EngineDesign,
    objective_report: Dict[str, object],
    ga_result: Optional[Dict[str, object]] = None,
    combustion_result: Optional[Dict[str, object]] = None,
    solver_interface_result: Optional[Dict[str, object]] = None,
    structural_result: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    # Extract solver field updates for provenance tagging (combustion, feed, structural)
    station_field_updates = _extract_station_field_updates(
        combustion_result, solver_interface_result, structural_result
    )
    design_station_rows = []

    for row in design.derived.station_rows:
        design_station_rows.append(
            {
                "label": row.label,
                "axial_position_mm": row.axial_position_mm,
                "envelope_diameter_mm": row.envelope_diameter_mm,
                "area_index": row.area_index,
                "shell_complexity_index": row.shell_complexity_index,
                "temperature_note": row.temperature_note,
                "pressure_note": row.pressure_note,
                "mass_flow_note": row.mass_flow_note,
                "mach_note": row.mach_note,
                "thermal_margin_index": row.thermal_margin_index,
                "thermal_margin_note": row.thermal_margin_note,
                # Machine-readable numeric fields.
                "temperature_k": row.temperature_k,
                "pressure_kpa": row.pressure_kpa,
                "mass_flow_kg_s": row.mass_flow_kg_s,
                "mach_number": row.mach_number,
                "density_kg_m3": row.density_kg_m3,
                "velocity_m_s": row.velocity_m_s,
                # Per-field provenance and status (merged from solvers)
                "analysis_fields": {
                    "temperature": _build_analysis_field(
                        "temperature_k",
                        row.temperature_k,
                        "K",
                        "calculated" if row.temperature_k is not None else "unavailable",
                        "design-solver",
                        station_field_updates,
                        row.label,
                    ),
                    "pressure": _build_analysis_field(
                        "pressure_kpa",
                        row.pressure_kpa,
                        "kPa",
                        "calculated" if row.pressure_kpa is not None else "unavailable",
                        "design-solver",
                        station_field_updates,
                        row.label,
                    ),
                    "mass_flow": _build_analysis_field(
                        "mass_flow_kg_s",
                        row.mass_flow_kg_s,
                        "kg/s",
                        "calculated" if row.mass_flow_kg_s is not None else "unavailable",
                        "design-solver",
                        station_field_updates,
                        row.label,
                    ),
                    "mach": _build_analysis_field(
                        "mach_number",
                        row.mach_number,
                        "",
                        "calculated" if row.mach_number is not None else "unavailable",
                        "design-solver",
                        station_field_updates,
                        row.label,
                    ),
                    "thermal_margin": _build_analysis_field(
                        "thermal_margin_index",
                        row.thermal_margin_index if row.thermal_margin_index is not None else None,
                        "unitless",
                        "calculated" if row.thermal_margin_index is not None else "unavailable",
                        "design-solver",
                        station_field_updates,
                        row.label,
                    ),
                },
            }
        )
    uncertainty_summary = _build_uncertainty_summary_from_rows(design_station_rows)

    return {
        "metadata": {
            "app": "StanThrust",
            "mode": "design-stage",
            "generated_at": datetime.now(UTC).isoformat(),
            "safety_boundary": "This project intentionally stays at the level of preliminary visualization and software architecture. The measurements and scores shown are solver outputs for analysis review only. They are not manufacturing dimensions, propulsion calculations for hardware release, test parameters, or build instructions.",
        },
        "solver": {
            "name": design.derived.solver_meta.solver_name,
            "version": design.derived.solver_meta.solver_version,
            "mode": design.derived.solver_meta.solver_mode,
            "stage_0_geometry": _build_stage_0_geometry_metadata(design),
            "stage_1_thermochemistry": _build_stage_1_thermochemistry_metadata(combustion_result),
            "stage_2_nozzle_loss": _build_stage_2_nozzle_metadata(combustion_result),
            "stage_2_feed_pressure_drop": _build_stage_2_feed_metadata(solver_interface_result),
            "stage_2_feed_transient_history": _build_stage_2_feed_history(solver_interface_result),
            "stage_3_structural_materials": _as_dict(structural_result.get("metadata", {})) if isinstance(structural_result, dict) else {},
        },
        "inputs": design.inputs.as_state(),
        "measurements": [
            {
                "label": row.label,
                "value": row.value,
                "numeric_value": row.numeric_value,
                "unit": row.unit,
            }
            for row in design.derived.measurement_rows
        ],
        "cad_stations_mm": design.derived.cad_stations_mm,
        "nozzle_contour_points": [
            {
                "x_mm": point["x_mm"],
                "radius_mm": point["radius_mm"],
                "diameter_mm": point["diameter_mm"],
                "section": point["section"],
                "normalized_x": point["normalized_x"],
            }
            for point in design.derived.nozzle_contour_points
        ],
        "design_station_rows": design_station_rows,
        "section_property_rows": _as_dict(structural_result.get("payload", {})).get("section_property_rows", [])
        if isinstance(structural_result, dict)
        else [],
        "uncertainty_summary": uncertainty_summary,
        "subsystem_design_notes": [
            {"label": item.label, "status": item.status, "note": item.note}
            for item in design.derived.subsystem_design_notes
        ],
        "summary": [{"label": item.label, "value": item.value} for item in design.derived.summary],
        "calculation_stages": list(design.derived.calculation_stages),
        "engineering_values": dict(design.derived.engineering_values),
        "objective_report": objective_report,
        "visualization_hints": design.derived.visualization_hints,
        "ga_result": ga_result,
        "benchmark_reference_cases": _build_benchmark_reference_metadata(),
    }


def export_cad_json(
    path: Path,
    design: EngineDesign,
    objective_report: Dict[str, object],
    ga_result: Optional[Dict[str, object]] = None,
    combustion_result: Optional[Dict[str, object]] = None,
    solver_interface_result: Optional[Dict[str, object]] = None,
    structural_result: Optional[Dict[str, object]] = None,
) -> None:
    """Write the full CAD payload, including provenance and uncertainty, as JSON."""
    payload = build_cad_export_payload(
        design,
        objective_report,
        ga_result,
        combustion_result,
        solver_interface_result,
        structural_result,
    )
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def export_measurements_csv(
    path: Path,
    design: EngineDesign,
    combustion_result: Optional[Dict[str, object]] = None,
    solver_interface_result: Optional[Dict[str, object]] = None,
    structural_result: Optional[Dict[str, object]] = None,
) -> None:
    thermochemistry = _build_stage_1_thermochemistry_metadata(combustion_result)
    nozzle_stage = _build_stage_2_nozzle_metadata(combustion_result)
    feed_stage = _build_stage_2_feed_metadata(solver_interface_result)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "value", "numeric_value", "unit"])
        for row in design.derived.measurement_rows:
            writer.writerow([row.label, row.value, row.numeric_value, row.unit])
        writer.writerow(["stage_1_thermochemistry.requested_mode", thermochemistry.get("requested_mode", ""), "", ""])
        writer.writerow(["stage_1_thermochemistry.effective_mode", thermochemistry.get("effective_mode", ""), "", ""])
        writer.writerow(["stage_1_thermochemistry.provider", thermochemistry.get("provider", ""), "", ""])
        writer.writerow(["stage_1_thermochemistry.source", thermochemistry.get("source", ""), "", ""])
        writer.writerow(["stage_1_thermochemistry.status", thermochemistry.get("status", ""), "", ""])
        writer.writerow(["stage_1_thermochemistry.cantera_solved", thermochemistry.get("cantera_solved", ""), "", ""])
        writer.writerow(["stage_1_thermochemistry.note", thermochemistry.get("note", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.solver_stage", nozzle_stage.get("solver_stage", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.solver_mode", nozzle_stage.get("solver_mode", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.flow_model", nozzle_stage.get("flow_model", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.flow_model_label", nozzle_stage.get("flow_model_label", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.status", nozzle_stage.get("status", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.status_detail", nozzle_stage.get("status_detail", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.half_angle_deg", nozzle_stage.get("half_angle_deg", ""), "", "deg"])
        writer.writerow([
            "stage_2_nozzle_loss.divergence_efficiency",
            nozzle_stage.get("divergence_efficiency", ""),
            "",
            "",
        ])
        writer.writerow([
            "stage_2_nozzle_loss.boundary_layer_efficiency",
            nozzle_stage.get("boundary_layer_efficiency", ""),
            "",
            "",
        ])
        writer.writerow(["stage_2_nozzle_loss.geometry_efficiency", nozzle_stage.get("geometry_efficiency", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.overall_efficiency", nozzle_stage.get("overall_efficiency", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.loss_fraction", nozzle_stage.get("loss_fraction", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.separation_efficiency", nozzle_stage.get("separation_efficiency", ""), "", ""])
        writer.writerow(["stage_2_nozzle_loss.exit_pressure_kpa", nozzle_stage.get("exit_pressure_kpa", ""), "", "kPa"])
        writer.writerow(["stage_2_nozzle_loss.note", nozzle_stage.get("note", ""), "", ""])
        writer.writerow(["stage_2_feed_pressure_drop.solver_mode", feed_stage.get("solver_mode", ""), "", ""])
        writer.writerow(["stage_2_feed_pressure_drop.status", feed_stage.get("status", ""), "", ""])
        writer.writerow(["stage_2_feed_pressure_drop.model_status", feed_stage.get("model_status", ""), "", ""])
        writer.writerow(["stage_2_feed_pressure_drop.quality_flag", feed_stage.get("quality_flag", ""), "", ""])
        writer.writerow(["stage_2_feed_pressure_drop.total_pressure_drop_kpa", feed_stage.get("total_pressure_drop_kpa", ""), "", "kPa"])
        writer.writerow(["stage_2_feed_pressure_drop.history_step_count", feed_stage.get("history_step_count", ""), "", ""])
        writer.writerow(["stage_2_feed_pressure_drop.initial_chamber_pressure_kpa", feed_stage.get("initial_chamber_pressure_kpa", ""), "", "kPa"])
        writer.writerow(["stage_2_feed_pressure_drop.final_chamber_pressure_kpa", feed_stage.get("final_chamber_pressure_kpa", ""), "", "kPa"])
        writer.writerow(["stage_2_feed_pressure_drop.minimum_feed_margin_kpa", feed_stage.get("minimum_feed_margin_kpa", ""), "", "kPa"])
        writer.writerow(["stage_2_feed_pressure_drop.final_fuel_tank_pressure_kpa", feed_stage.get("final_fuel_tank_pressure_kpa", ""), "", "kPa"])
        writer.writerow(["stage_2_feed_pressure_drop.final_oxidizer_tank_pressure_kpa", feed_stage.get("final_oxidizer_tank_pressure_kpa", ""), "", "kPa"])
        writer.writerow(["stage_2_feed_pressure_drop.chamber_pressure_drift_percent", feed_stage.get("chamber_pressure_drift_percent", ""), "", "%"])
        writer.writerow(["stage_2_feed_pressure_drop.maximum_pump_speed_fraction", feed_stage.get("maximum_pump_speed_fraction", ""), "", ""])
        writer.writerow(["stage_2_feed_pressure_drop.note", feed_stage.get("note", ""), "", ""])
        # structural metadata summary (if provided)
        if isinstance(structural_result, dict):
            struct_meta = _as_dict(structural_result.get("metadata", {}))
            writer.writerow(["stage_3_structural_materials.solver_name", struct_meta.get("solver_name", ""), "", ""])
            writer.writerow(["stage_3_structural_materials.solver_version", struct_meta.get("solver_version", ""), "", ""])
            writer.writerow(["stage_3_structural_materials.solver_mode", struct_meta.get("solver_mode", ""), "", ""])


def export_station_csv(
    path: Path,
    design: EngineDesign,
    combustion_result: Optional[Dict[str, object]] = None,
    solver_interface_result: Optional[Dict[str, object]] = None,
    structural_result: Optional[Dict[str, object]] = None,
) -> None:
    thermochemistry = _build_stage_1_thermochemistry_metadata(combustion_result)
    nozzle_stage = _build_stage_2_nozzle_metadata(combustion_result)
    feed_stage = _build_stage_2_feed_metadata(solver_interface_result)
    station_field_updates = _extract_station_field_updates(
        combustion_result,
        solver_interface_result,
        structural_result,
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "station_label",
                "axial_position_mm",
                "envelope_diameter_mm",
                "area_index",
                "shell_complexity_index",
                "temperature",
                "pressure",
                "mass_flow",
                "mach_number",
                "status",
                "thermochemistry_provider",
                "thermochemistry_status",
                "thermochemistry_source",
                "thermochemistry_effective_mode",
                "thermal_margin_index",
                "thermal_margin_note",
                "nozzle_stage_solver_mode",
                "nozzle_stage_flow_model",
                "nozzle_stage_status",
                "nozzle_stage_loss_fraction",
                "nozzle_stage_overall_efficiency",
                "feed_stage_solver_mode",
                "feed_stage_status",
                "feed_stage_quality_flag",
                "feed_stage_total_pressure_drop_kpa",
                # Structural numeric fields.
                "thermal_margin_index",
                "thermal_margin_source",
                # Numeric station fields.
                "temperature_k",
                "pressure_kpa",
                "mass_flow_kg_s",
                "mach_number_calculated",
            ]
        )
        for row in design.derived.station_rows:
            station_updates = station_field_updates.get(row.label, {})
            thermal_margin_update = station_updates.get("thermal_margin", {})
            thermal_margin_value = thermal_margin_update.get("value", row.thermal_margin_index)
            thermal_margin_source = thermal_margin_update.get("source_solver", "")
            writer.writerow(
                [
                    row.label,
                    row.axial_position_mm,
                    row.envelope_diameter_mm,
                    row.area_index,
                    row.shell_complexity_index,
                    row.temperature_note,
                    row.pressure_note,
                    row.mass_flow_note,
                    row.mach_note,
                    "Design-stage export",
                    thermochemistry.get("provider", ""),
                    thermochemistry.get("status", ""),
                    thermochemistry.get("source", ""),
                    thermochemistry.get("effective_mode", ""),
                    row.thermal_margin_index,
                    row.thermal_margin_note,
                    nozzle_stage.get("solver_mode", ""),
                    nozzle_stage.get("flow_model", ""),
                    nozzle_stage.get("status", ""),
                    nozzle_stage.get("loss_fraction", ""),
                    nozzle_stage.get("overall_efficiency", ""),
                    feed_stage.get("solver_mode", ""),
                    feed_stage.get("status", ""),
                    feed_stage.get("quality_flag", ""),
                    feed_stage.get("total_pressure_drop_kpa", ""),
                    thermal_margin_value,
                    thermal_margin_source,
                    row.temperature_k,
                    row.pressure_kpa,
                    row.mass_flow_kg_s,
                    row.mach_number,
                ]
            )


def _dedupe_profile_points(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    cleaned: List[Tuple[float, float]] = []
    for x_mm, radius_mm in points:
        point = (round(float(x_mm), 6), round(max(0.0, float(radius_mm)), 6))
        if cleaned and abs(cleaned[-1][0] - point[0]) < 1e-6 and abs(cleaned[-1][1] - point[1]) < 1e-6:
            continue
        cleaned.append(point)
    return cleaned


def build_revolved_profile_points(design: EngineDesign) -> List[Tuple[float, float]]:
    values = dict(design.derived.engineering_values)
    feed_length_mm = float(design.derived.feed_system_bay_length_mm)
    chamber_length_mm = float(design.derived.chamber_length_mm)
    chamber_outer_diameter_mm = float(values.get("chamber_outer_diameter_mm", design.inputs.chamber_diameter_mm))
    chamber_outer_radius_mm = max(0.5, chamber_outer_diameter_mm * 0.5)
    injector_face_diameter_mm = float(values.get("injector_face_diameter_mm", chamber_outer_diameter_mm))
    feed_outer_radius_mm = max(chamber_outer_radius_mm, injector_face_diameter_mm * 0.52)
    nozzle_wall_thickness_mm = max(0.0, float(values.get("nozzle_wall_thickness_mm", 0.0)))

    top_profile: List[Tuple[float, float]] = [
        (0.0, feed_outer_radius_mm),
        (feed_length_mm, feed_outer_radius_mm),
        (feed_length_mm, chamber_outer_radius_mm),
        (feed_length_mm + chamber_length_mm, chamber_outer_radius_mm),
    ]

    contour_points = list(design.derived.nozzle_contour_points)
    chamber_end_x_mm = feed_length_mm + chamber_length_mm
    for index, point in enumerate(contour_points):
        axial_mm = float(point.get("x_mm", 0.0))
        inner_radius_mm = max(0.0, float(point.get("radius_mm", 0.0)))
        outer_radius_mm = inner_radius_mm + nozzle_wall_thickness_mm
        if index == 0:
            outer_radius_mm = max(outer_radius_mm, chamber_outer_radius_mm)
        top_profile.append((chamber_end_x_mm + axial_mm, outer_radius_mm))

    return _dedupe_profile_points(top_profile)


def export_profile_dxf(path: Path, design: EngineDesign) -> None:
    top_profile = build_revolved_profile_points(design)
    if len(top_profile) < 2:
        raise ValueError("Not enough profile points to export a DXF sketch.")

    start_x_mm = top_profile[0][0]
    end_x_mm = top_profile[-1][0]
    closed_profile = [(start_x_mm, 0.0)] + top_profile + [(end_x_mm, 0.0), (start_x_mm, 0.0)]

    def _line_entity(start: Tuple[float, float], end: Tuple[float, float], layer: str) -> List[str]:
        return [
            "0", "LINE",
            "8", layer,
            "10", "{0:.6f}".format(start[0]),
            "20", "{0:.6f}".format(start[1]),
            "30", "0.0",
            "11", "{0:.6f}".format(end[0]),
            "21", "{0:.6f}".format(end[1]),
            "31", "0.0",
        ]

    content: List[str] = [
        "0", "SECTION",
        "2", "HEADER",
        "9", "$INSUNITS",
        "70", "4",
        "0", "ENDSEC",
        "0", "SECTION",
        "2", "ENTITIES",
    ]

    for index in range(len(closed_profile) - 1):
        content.extend(_line_entity(closed_profile[index], closed_profile[index + 1], "PROFILE"))

    content.extend(
        _line_entity((start_x_mm, 0.0), (end_x_mm, 0.0), "CENTERLINE")
    )
    content.extend(["0", "ENDSEC", "0", "EOF"])
    path.write_text("\n".join(content) + "\n", encoding="utf-8")

