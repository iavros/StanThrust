"""Text report and diagnostic snapshot construction for the desktop views."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from stanthrust.ui.formatting import (
    EMPTY,
    display_injector_name,
    display_solver_stage,
    format_number,
    safe_float,
)


def _as_dict(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _section(title: str) -> List[str]:
    return ["", title, "-" * len(title)]


def _dimensions_line(label: str, values: Dict[str, object], prefix: str, length_key: str) -> str:
    return "{0:<16}{1} / {2} / {3} / {4} mm".format(
        label,
        format_number(values.get("{0}_inner_diameter_mm".format(prefix), EMPTY), 2),
        format_number(values.get("{0}_outer_diameter_mm".format(prefix), EMPTY), 2),
        format_number(values.get("{0}_wall_thickness_mm".format(prefix), EMPTY), 2),
        format_number(values.get(length_key, EMPTY), 2),
    )


def build_report_text(
    *,
    design,
    objective_report: Optional[dict],
    combustion_result: Optional[dict],
    solver_interface_result: Optional[dict],
    optimizer_result: Optional[object] = None,
    uncertainty_bounds: Optional[Sequence[dict]] = None,
) -> str:
    """Build the plain-text engineering report shown on the Report tab."""
    values = dict(design.derived.engineering_values)
    combustion = _as_dict(combustion_result)
    combustion_summary = _as_dict(combustion.get("summary"))
    combustion_metadata = _as_dict(combustion.get("metadata"))
    combustion_warnings = list(combustion.get("warnings", []))

    solver = _as_dict(solver_interface_result)
    solver_payload = _as_dict(solver.get("payload"))
    feed_result = _as_dict(solver_payload.get("feed_pressure_drop"))
    feed_summary = _as_dict(_as_dict(feed_result.get("payload")).get("summary"))
    solver_warnings = list(solver.get("warnings", []))
    solver_trace = list(solver.get("trace", []))

    lines: List[str] = ["STANTHRUST DESIGN REPORT", "=" * 24]

    lines.extend(_section("Operating point"))
    lines.extend(
        [
            "{0:<24}{1} N".format(
                "Thrust",
                format_number(
                    combustion_summary.get(
                        "predicted_thrust_newtons", values.get("calculated_thrust_newtons", EMPTY)
                    ),
                    2,
                ),
            ),
            "{0:<24}{1} s".format(
                "Specific impulse",
                format_number(
                    combustion_summary.get("predicted_isp_seconds", values.get("predicted_isp_seconds", EMPTY)),
                    3,
                ),
            ),
            "{0:<24}{1} kPa".format(
                "Chamber pressure",
                format_number(
                    combustion_summary.get("chamber_pressure_kpa", values.get("chamber_pressure_kpa", EMPTY)),
                    3,
                ),
            ),
            "{0:<24}{1} kg/s".format(
                "Mass flow",
                format_number(
                    combustion_summary.get("mass_flow_kg_s", values.get("propellant_mass_flow_kg_s", EMPTY)),
                    5,
                ),
            ),
        ]
    )
    if objective_report is not None:
        lines.append(
            "{0:<24}{1}".format("Objective score", format_number(objective_report.get("total_score"), 4))
        )

    lines.extend(_section("Hardware (ID / OD / wall / length)"))
    lines.append(
        "{0:<24}{1}".format(
            "Injector",
            display_injector_name(values.get("injector_type", design.inputs.injector_type)),
        )
    )
    lines.append(
        "{0:<24}{1} kg".format("Propellant used", format_number(values.get("propellant_mass_used_kg", EMPTY), 3))
    )
    lines.extend(
        [
            _dimensions_line("Fuel tank", values, "fuel_tank", "fuel_tank_required_length_mm"),
            _dimensions_line("Oxidizer tank", values, "oxidizer_tank", "oxidizer_tank_required_length_mm"),
            _dimensions_line("Chamber", values, "chamber", "chamber_length_mm"),
            _dimensions_line("Nozzle", values, "nozzle", "nozzle_diverging_length_mm"),
        ]
    )

    lines.extend(_section("Envelope"))
    lines.extend(
        [
            "{0:<24}{1} mm".format(
                "Maximum outer diameter",
                format_number(values.get("maximum_diameter_mm", design.derived.maximum_diameter_mm), 3),
            ),
            "{0:<24}{1} mm".format(
                "Target diameter limit",
                format_number(
                    values.get("target_outer_diameter_limit_mm", design.inputs.target_diameter_mm), 3
                ),
            ),
            "{0:<24}{1} mm".format(
                "Uncapped requirement",
                format_number(
                    values.get("maximum_required_outer_diameter_mm", design.derived.maximum_diameter_mm), 3
                ),
            ),
            "{0:<24}{1}".format("Status", values.get("diameter_limit_status", EMPTY)),
        ]
    )

    if feed_summary:
        lines.extend(_section("Feed transient"))
        lines.extend(
            [
                "{0:<24}{1}".format("History steps", feed_summary.get("history_step_count", EMPTY)),
                "{0:<24}{1} -> {2} kPa".format(
                    "Chamber pressure drift",
                    format_number(feed_summary.get("initial_chamber_pressure_kpa", EMPTY), 3),
                    format_number(feed_summary.get("final_chamber_pressure_kpa", EMPTY), 3),
                ),
                "{0:<24}{1} kPa".format(
                    "Minimum feed margin",
                    format_number(feed_summary.get("minimum_feed_margin_kpa", EMPTY), 3),
                ),
                "{0:<24}fuel {1} kPa | ox {2} kPa".format(
                    "End-of-burn tanks",
                    format_number(feed_summary.get("final_fuel_tank_pressure_kpa", EMPTY), 3),
                    format_number(feed_summary.get("final_oxidizer_tank_pressure_kpa", EMPTY), 3),
                ),
            ]
        )

    if combustion:
        thermochemistry = _as_dict(combustion_metadata.get("thermochemistry"))
        stage_label = str(
            combustion_metadata.get(
                "solver_stage_label",
                display_solver_stage(
                    combustion_metadata.get("solver_stage", EMPTY),
                    combustion_metadata.get(
                        "flow_model_label", combustion_summary.get("flow_model_label", "")
                    ),
                ),
            )
        )
        lines.extend(_section("Solver"))
        lines.extend(
            [
                "{0:<24}{1}".format("Status", combustion.get("status", "unknown")),
                "{0:<24}{1}".format("Stage", stage_label),
                "{0:<24}{1} ({2})".format(
                    "Thermochemistry",
                    thermochemistry.get("provider", EMPTY),
                    thermochemistry.get("status", EMPTY),
                ),
                "{0:<24}{1}; max wall {2} K; coolant outlet {3} K".format(
                    "Heat transfer",
                    combustion_summary.get("heat_transfer_status", EMPTY),
                    format_number(combustion_summary.get("max_hot_wall_temperature_k", EMPTY), 2),
                    format_number(combustion_summary.get("coolant_outlet_temperature_k", EMPTY), 2),
                ),
                "{0:<24}{1}; regime {2}; station {3} mm".format(
                    "Shock diagnostics",
                    combustion_summary.get("shock_status", EMPTY),
                    combustion_summary.get("shock_regime", EMPTY),
                    format_number(combustion_summary.get("shock_station_x_mm", EMPTY), 2),
                ),
                "{0:<24}{1}".format("Axial stations", int(combustion_summary.get("station_count", 0))),
                "{0:<24}{1}".format("Iterations", combustion.get("iterations", 0)),
            ]
        )

    if uncertainty_bounds:
        lines.extend(_section("Uncertainty bounds"))
        for bound in uncertainty_bounds:
            if not isinstance(bound, dict):
                continue
            lines.append(
                "{0:<40}{1} [{2} .. {3}] {4}".format(
                    str(bound.get("name", EMPTY)),
                    format_number(bound.get("value"), 4),
                    format_number(bound.get("lower"), 4),
                    format_number(bound.get("upper"), 4),
                    str(bound.get("unit", "")),
                ).rstrip()
            )
            basis = str(bound.get("basis", "")).strip()
            if basis:
                lines.append("{0}{1}".format(" " * 4, basis))

    recent = [str(item) for item in solver_warnings[:2]]
    recent.extend(str(item) for item in combustion_warnings[:2])
    recent.extend(str(item) for item in solver_trace[-2:])
    if recent:
        lines.extend(_section("Recent solver messages"))
        lines.extend(recent[:4])

    if optimizer_result is not None:
        lines.extend(_section("Optimiser"))
        lines.extend(
            [
                "{0:<24}{1}".format("Generations", len(getattr(optimizer_result, "history", []) or [])),
                "{0:<24}{1}".format(
                    "Best score", format_number(getattr(optimizer_result, "best_score", EMPTY), 4)
                ),
            ]
        )

    return "\n".join(lines)


def build_diagnostic_lines(
    *,
    design=None,
    combustion_result: Optional[dict] = None,
    solver_interface_result: Optional[dict] = None,
    structural_result: Optional[dict] = None,
    validation_report=None,
) -> List[str]:
    """Build the keyed diagnostic snapshot appended below the solver log."""
    lines: List[str] = []

    if design is not None:
        values = dict(design.derived.engineering_values)
        lines.extend(
            [
                "geometry.maximum_diameter_mm = {0}".format(
                    format_number(values.get("maximum_diameter_mm", design.derived.maximum_diameter_mm), 4)
                ),
                "geometry.target_outer_diameter_limit_mm = {0}".format(
                    format_number(
                        values.get("target_outer_diameter_limit_mm", design.inputs.target_diameter_mm), 4
                    )
                ),
                "geometry.maximum_required_outer_diameter_mm = {0}".format(
                    format_number(
                        values.get(
                            "maximum_required_outer_diameter_mm", design.derived.maximum_diameter_mm
                        ),
                        4,
                    )
                ),
                "geometry.diameter_limit_status = {0}".format(values.get("diameter_limit_status", EMPTY)),
            ]
        )

    solver = _as_dict(solver_interface_result)
    if solver:
        feed_result = _as_dict(_as_dict(solver.get("payload")).get("feed_pressure_drop"))
        feed_metadata = _as_dict(feed_result.get("metadata"))
        feed_summary = _as_dict(_as_dict(feed_result.get("payload")).get("summary"))
        if feed_metadata:
            lines.extend(
                [
                    "feed.solver_mode = {0}".format(feed_metadata.get("solver_mode", EMPTY)),
                    "feed.status = {0}".format(feed_result.get("status", EMPTY)),
                    "feed.history_step_count = {0}".format(feed_summary.get("history_step_count", EMPTY)),
                    "feed.minimum_feed_margin_kpa = {0}".format(
                        format_number(feed_summary.get("minimum_feed_margin_kpa", EMPTY), 4)
                    ),
                    "feed.chamber_pressure_drift_percent = {0}".format(
                        format_number(feed_summary.get("chamber_pressure_drift_percent", EMPTY), 4)
                    ),
                    "feed.fuel_required_supply_pressure_kpa = {0}".format(
                        format_number(feed_summary.get("fuel_required_supply_pressure_kpa", EMPTY), 4)
                    ),
                    "feed.fuel_coolant_pressure_constraint_active = {0}".format(
                        feed_summary.get("fuel_coolant_pressure_constraint_active", EMPTY)
                    ),
                ]
            )
            for warning in list(feed_result.get("warnings", []))[:3]:
                lines.append("feed.warning = {0}".format(warning))
        for trace_line in list(solver.get("trace", []))[:5]:
            lines.append("solver.trace = {0}".format(trace_line))

    combustion = _as_dict(combustion_result)
    if combustion:
        metadata = _as_dict(combustion.get("metadata"))
        thermochemistry = _as_dict(metadata.get("thermochemistry"))
        summary = _as_dict(combustion.get("summary"))
        heat_summary = _as_dict(_as_dict(combustion.get("heat_transfer")).get("summary"))
        shock = _as_dict(combustion.get("shock_analysis"))
        lines.extend(
            [
                "thermochemistry.requested_mode = {0}".format(thermochemistry.get("requested_mode", EMPTY)),
                "thermochemistry.effective_mode = {0}".format(thermochemistry.get("effective_mode", EMPTY)),
                "thermochemistry.provider = {0}".format(thermochemistry.get("provider", EMPTY)),
                "thermochemistry.status = {0}".format(thermochemistry.get("status", EMPTY)),
                "nozzle_flow.status_detail = {0}".format(combustion.get("status_detail", EMPTY)),
                "heat_transfer.status = {0}".format(
                    heat_summary.get("status", summary.get("heat_transfer_status", EMPTY))
                ),
                "heat_transfer.total_heat_load_kw = {0}".format(
                    format_number(
                        heat_summary.get("total_heat_load_kw", summary.get("heat_load_kw", EMPTY)), 4
                    )
                ),
                "heat_transfer.max_hot_wall_temperature_k = {0}".format(
                    format_number(
                        heat_summary.get(
                            "max_hot_wall_temperature_k", summary.get("max_hot_wall_temperature_k", EMPTY)
                        ),
                        4,
                    )
                ),
                "heat_transfer.limiting_section = {0}".format(
                    heat_summary.get(
                        "limiting_section", summary.get("heat_transfer_limiting_section", EMPTY)
                    )
                ),
                "heat_transfer.coolant_phase_pressure_basis = {0}".format(
                    heat_summary.get("coolant_phase_pressure_basis", EMPTY)
                ),
                "heat_transfer.coolant_required_inlet_pressure_kpa = {0}".format(
                    format_number(heat_summary.get("coolant_required_inlet_pressure_kpa", EMPTY), 4)
                ),
                "heat_transfer.coolant_pressure_margin_kpa = {0}".format(
                    format_number(heat_summary.get("coolant_pressure_margin_kpa", EMPTY), 4)
                ),
                "heat_transfer.coolant_pressure_requirement_met = {0}".format(
                    heat_summary.get("coolant_pressure_requirement_met", EMPTY)
                ),
                "heat_transfer.wall_normal_node_count = {0}".format(
                    heat_summary.get("wall_normal_node_count", EMPTY)
                ),
                "heat_transfer.maximum_grid_refinement_error_percent = {0}".format(
                    format_number(heat_summary.get("maximum_thermal_grid_refinement_error_percent", EMPTY), 4)
                ),
                "heat_transfer.computational_complexity = {0}".format(
                    heat_summary.get("computational_complexity", {})
                ),
                "shock.status = {0}".format(shock.get("status", summary.get("shock_status", EMPTY))),
                "shock.regime = {0}".format(shock.get("regime", summary.get("shock_regime", EMPTY))),
                "shock.model = {0}".format(shock.get("model", "rankine-hugoniot-normal-shock")),
            ]
        )
        for warning in list(combustion.get("warnings", []))[:3]:
            lines.append("nozzle_flow.warning = {0}".format(warning))

    structural = _as_dict(structural_result)
    if structural:
        payload = _as_dict(structural.get("payload"))
        summary = _as_dict(payload.get("summary"))
        lines.extend(
            [
                "material.minimum_stress_margin_ratio = {0}".format(
                    format_number(summary.get("minimum_stress_margin_ratio", EMPTY), 4)
                ),
                "material.minimum_heat_transfer_margin_ratio = {0}".format(
                    format_number(summary.get("minimum_heat_transfer_margin_ratio", EMPTY), 4)
                ),
                "material.minimum_combined_margin_ratio = {0}".format(
                    format_number(summary.get("minimum_combined_margin_ratio", EMPTY), 4)
                ),
                "material.redesign_required = {0}".format(summary.get("redesign_required", EMPTY)),
            ]
        )
        for recommendation in list(payload.get("redesign_recommendations", []))[:5]:
            if isinstance(recommendation, dict):
                lines.append("material.recommendation = {0}".format(recommendation.get("note", EMPTY)))
    else:
        lines.append("mode = design-preview")

    if validation_report is not None:
        lines.append("validation.status = {0}".format("ok" if validation_report.passed else "warning"))
        lines.append("validation.summary = {0}".format(validation_report.summary))
        for check in [check for check in validation_report.checks if not check.passed][:5]:
            lines.append("validation.{0} = {1}".format(check.check_name, check.message))

    if design is not None:
        for stage in list(design.derived.calculation_stages)[:7]:
            lines.append("calculation_stage = {0}".format(stage))

    return lines


def convergence_summary(coupled_result: Optional[dict]) -> str:
    """One-line residual and margin summary for the solve status area."""
    payload = _as_dict(_as_dict(coupled_result).get("payload"))
    convergence = _as_dict(payload.get("convergence"))
    if not convergence:
        return "Residuals appear after a solve."
    return (
        "Pc residual {0} kPa   thrust error {1}%   feed {2} kPa   stress {3}x   material {4}x".format(
            format_number(convergence.get("final_residual_kpa", EMPTY), 3),
            format_number((safe_float(convergence.get("thrust_error_fraction"), 0.0) or 0.0) * 100.0, 3),
            format_number(convergence.get("minimum_feed_margin_kpa", EMPTY), 3),
            format_number(convergence.get("minimum_structural_margin_ratio", EMPTY), 3),
            format_number(convergence.get("minimum_combined_material_margin_ratio", EMPTY), 3),
        )
    )
