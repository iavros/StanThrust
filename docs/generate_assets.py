from dataclasses import asdict, replace
from pathlib import Path
import csv
import site
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPORT_ROOT / "data"


def _add_user_site() -> None:
    try:
        user_site = site.getusersitepackages()
    except Exception:
        return
    if user_site and user_site not in sys.path:
        try:
            site.addsitedir(user_site)
        except Exception:
            sys.path.append(user_site)


_add_user_site()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stanthrust.combustion_cfd_solver import run_combustion_cfd_solver
from stanthrust.benchmark_cases import (
    build_internal_baseline_rows,
    build_public_benchmark_reference_rows,
    build_reconstructed_benchmark_rows,
)
from stanthrust.design_model import _solve_pressure_state, create_engine_design
from stanthrust.inputs import DEFAULT_STATE, get_default_solver_assumptions
from stanthrust.solver_interface import solve as solve_solver_interface
from stanthrust.thermochemistry_provider import CanteraThermochemistryProvider


def write_csv(path: Path, fieldnames, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def make_pgfplots_safe_rows(rows, text_fields):
    safe_rows = []
    for row in rows:
        safe_row = dict(row)
        for field in text_fields:
            if field in safe_row and safe_row[field] is not None:
                safe_row[field] = str(safe_row[field]).replace(",", ";")
        safe_rows.append(safe_row)
    return safe_rows


def write_macros(path: Path, macros) -> None:
    lines = []
    for name, value in macros.items():
        safe_value = str(value)
        replacements = {
            "\\": "\\textbackslash{}",
            "_": "\\_",
            "%": "\\%",
            "&": "\\&",
            "#": "\\#",
            "{": "\\{",
            "}": "\\}",
        }
        for old, new in replacements.items():
            safe_value = safe_value.replace(old, new)
        lines.append("\\newcommand{{\\{0}}}{{{1}}}".format(name, safe_value))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_default_case():
    state = asdict(DEFAULT_STATE)
    design = create_engine_design(state)
    assumptions = get_default_solver_assumptions()
    combustion = run_combustion_cfd_solver(
        design,
        assumptions,
        station_count=25,
        thermochemistry_mode="auto",
    )
    return state, design, assumptions, combustion


def build_case_for_flow_model(flow_model: str):
    state = asdict(DEFAULT_STATE)
    design = create_engine_design(state)
    assumptions = replace(get_default_solver_assumptions(), flow_model=flow_model)
    combustion = run_combustion_cfd_solver(
        design,
        assumptions,
        station_count=25,
        thermochemistry_mode="auto",
        thermochemistry_provider=CanteraThermochemistryProvider(),
    )
    return state, design, assumptions, combustion


def build_feed_transient_case_rows(use_pumps: bool):
    state = asdict(DEFAULT_STATE)
    state["use_pumps"] = use_pumps
    solver_result = solve_solver_interface(
        state,
        upstream_context={"source": "overleaf-report", "stage": "feed-transient"},
    )
    feed_result = solver_result["payload"]["feed_pressure_drop"]
    summary = feed_result["payload"]["summary"]
    rows = []
    for row in feed_result["payload"]["time_history_rows"]:
        rows.append(
            {
                "architecture": "pump-fed" if use_pumps else "pressure-fed",
                "time_s": row["time_s"],
                "burn_fraction": row["burn_fraction"],
                "chamber_pressure_kpa": row["chamber_pressure_kpa"],
                "required_feed_pressure_kpa": row["required_feed_pressure_kpa"],
                "fuel_tank_pressure_kpa": row["fuel_tank_pressure_kpa"],
                "oxidizer_tank_pressure_kpa": row["oxidizer_tank_pressure_kpa"],
                "fuel_margin_kpa": row["fuel_margin_kpa"],
                "oxidizer_margin_kpa": row["oxidizer_margin_kpa"],
                "pump_differential_pressure_kpa": row["pump_differential_pressure_kpa"],
                "pump_speed_fraction": row["pump_speed_fraction"],
            }
        )
    return rows, summary


def build_flow_mode_comparison_rows(design, fast_combustion, refined_combustion, navier_stokes_combustion):
    values = design.derived.engineering_values
    rows = []
    for mode_name, result in (
        ("fast", fast_combustion),
        ("refined", refined_combustion),
        ("navier_stokes", navier_stokes_combustion),
    ):
        summary = result["summary"]
        nozzle = result["physics"]["nozzle"]
        rows.append(
            {
                "flow_model": mode_name,
                "flow_model_label": summary.get("flow_model_label", mode_name.title()),
                "predicted_thrust_newtons": round(float(summary.get("predicted_thrust_newtons", 0.0)), 5),
                "predicted_isp_seconds": round(float(summary.get("predicted_isp_seconds", 0.0)), 5),
                "chamber_pressure_kpa": round(float(summary.get("chamber_pressure_kpa", 0.0)), 5),
                "mass_flow_kg_s": round(float(summary.get("mass_flow_kg_s", 0.0)), 5),
                "exit_pressure_kpa": round(float(summary.get("exit_pressure_kpa", 0.0)), 5),
                "thrust_coefficient": round(float(summary.get("thrust_coefficient", 0.0)), 5),
                "overall_efficiency": round(float(nozzle.get("overall_efficiency", 0.0)), 5),
                "separation_efficiency": round(float(nozzle.get("separation_efficiency", 1.0)), 5),
                "nozzle_half_angle_deg": round(float(nozzle.get("half_angle_deg", 0.0)), 5),
                "nozzle_expansion_ratio": round(float(values.get("nozzle_expansion_ratio", 0.0)), 5),
            }
        )
    return rows


def build_pressure_rows(state):
    rows = []
    for use_pumps, label in ((True, "pump-fed"), (False, "pressure-fed")):
        arch_state = dict(state)
        arch_state["use_pumps"] = use_pumps
        design = create_engine_design(arch_state)
        values = design.derived.engineering_values
        solution = _solve_pressure_state(
            use_pumps=use_pumps,
            target_chamber_pressure_kpa=float(values.get("chamber_pressure_target_kpa", values.get("chamber_pressure_kpa", 0.0))),
            propellant_mass_flow_kg_s=float(values.get("propellant_mass_flow_kg_s", 0.0)),
            feed_system_bay_length_mm=float(design.derived.feed_system_bay_length_mm),
        )
        rows.append(
            {
                "architecture": label,
                "chamber_pressure_kpa": round(solution.chamber_pressure_kpa, 3),
                "required_feed_pressure_kpa": round(solution.required_feed_pressure_kpa, 3),
                "fuel_tank_pressure_kpa": round(solution.fuel_tank_pressure_kpa, 3),
                "oxidizer_tank_pressure_kpa": round(solution.oxidizer_tank_pressure_kpa, 3),
                "pump_differential_pressure_kpa": round(solution.pump_differential_pressure_kpa, 3),
                "fuel_margin_kpa": round(solution.fuel_pressure_margin_kpa, 3),
                "oxidizer_margin_kpa": round(solution.oxidizer_pressure_margin_kpa, 3),
            }
        )
    return rows


def build_geometry_breakdown_rows(design):
    return [
        {"component": "Fuel tank", "length_mm": round(float(design.derived.fuel_tank_length_mm), 3)},
        {"component": "Ox tank", "length_mm": round(float(design.derived.oxidizer_tank_length_mm), 3)},
        {"component": "Feed bay", "length_mm": round(float(design.derived.feed_system_bay_length_mm), 3)},
        {"component": "Chamber", "length_mm": round(float(design.derived.chamber_length_mm), 3)},
        {"component": "Nozzle", "length_mm": round(float(design.derived.nozzle_length_mm), 3)},
    ]


def build_render_geometry_rows(values):
    fields = [
        ("Fuel feed tube", "Diameter", "fuel_feed_tube_diameter_mm"),
        ("Ox feed tube", "Diameter", "oxidizer_feed_tube_diameter_mm"),
        ("Fuel pump casing", "Diameter", "fuel_pump_casing_diameter_mm"),
        ("Ox pump casing", "Diameter", "oxidizer_pump_casing_diameter_mm"),
        ("Fuel impeller", "Diameter", "fuel_impeller_diameter_mm"),
        ("Ox impeller", "Diameter", "oxidizer_impeller_diameter_mm"),
        ("Motor envelope", "Length", "electric_motor_envelope_length_mm"),
        ("Injector face", "Diameter", "injector_face_diameter_mm"),
        ("Injector recess", "Diameter", "injector_recess_diameter_mm"),
        ("Injector element ring", "Diameter", "injector_element_ring_diameter_mm"),
    ]
    rows = []
    for component, dimension, key in fields:
        value = float(values.get(key, 0.0) or 0.0)
        if value > 0.0:
            rows.append(
                {
                    "component": component,
                    "dimension": dimension,
                    "field_name": key,
                    "value_mm": round(value, 4),
                }
            )
    return rows


def build_internal_regression_summary_rows(rows):
    def value_range(row, low_key, high_key, precision=1):
        low = float(row.get(low_key, 0.0) or 0.0)
        high = float(row.get(high_key, 0.0) or 0.0)
        return "{0:.{2}f} to {1:.{2}f}".format(low, high, precision)

    summary_rows = []
    for row in rows:
        summary_rows.append(
            {
                "label": row["label"],
                "thrust_n": round(float(row["calculated_thrust_newtons_observed"]), 1),
                "thrust_range_n": value_range(
                    row,
                    "calculated_thrust_newtons_min",
                    "calculated_thrust_newtons_max",
                    1,
                ),
                "chamber_pressure_kpa": round(float(row["chamber_pressure_kpa_observed"]), 1),
                "chamber_pressure_range_kpa": value_range(
                    row,
                    "chamber_pressure_kpa_min",
                    "chamber_pressure_kpa_max",
                    1,
                ),
                "expansion_ratio": round(float(row["nozzle_expansion_ratio_observed"]), 2),
                "expansion_ratio_range": value_range(
                    row,
                    "nozzle_expansion_ratio_min",
                    "nozzle_expansion_ratio_max",
                    1,
                ),
                "stack_length_mm": round(float(row["total_stack_length_mm_observed"]), 1),
                "stack_length_range_mm": value_range(
                    row,
                    "total_stack_length_mm_min",
                    "total_stack_length_mm_max",
                    1,
                ),
            }
        )
    return summary_rows


def build_individual_benchmark_run_rows(rows):
    comparable_fields = [
        "thrust_error_percent",
        "chamber_pressure_error_percent",
        "mass_flow_error_percent",
        "isp_error_percent",
    ]
    solved_field_count = 9
    summary_rows = []
    for row in rows:
        errors = []
        for field in comparable_fields:
            value = row.get(field, "")
            if value not in ("", None):
                errors.append(abs(float(value)))
        summary_rows.append(
            {
                "engine": row["engine"],
                "comparable_stats": len(errors),
                "mean_abs_error_percent": "" if not errors else round(sum(errors) / len(errors), 2),
                "max_abs_error_percent": "" if not errors else round(max(errors), 2),
                "solved_expansion_ratio": round(float(row["generated_expansion_ratio"]), 2),
                "solved_stack_length_mm": round(float(row["generated_total_length_mm"]), 1),
                "solved_field_count": solved_field_count,
            }
        )
    return summary_rows


def build_propellant_breakdown_rows(values):
    return [
        {
            "quantity": "Mass",
            "fuel_value": round(float(values.get("fuel_mass_kg", 0.0)), 4),
            "oxidizer_value": round(float(values.get("oxidizer_mass_kg", 0.0)), 4),
            "unit": "kg",
        },
        {
            "quantity": "Volume",
            "fuel_value": round(float(values.get("fuel_volume_l", 0.0)), 4),
            "oxidizer_value": round(float(values.get("oxidizer_volume_l", 0.0)), 4),
            "unit": "L",
        },
        {
            "quantity": "Volume flow",
            "fuel_value": round(float(values.get("fuel_volume_flow_l_min", 0.0)), 4),
            "oxidizer_value": round(float(values.get("oxidizer_volume_flow_l_min", 0.0)), 4),
            "unit": "L/min",
        },
    ]


def build_nozzle_loss_rows(combustion):
    coeffs = combustion["physics"]["coefficients"]
    nozzle = combustion["physics"]["nozzle"]
    return [
        {"term": "Divergence", "value": round(float(coeffs.get("divergence_efficiency", 0.0)), 5)},
        {"term": "Boundary layer", "value": round(float(coeffs.get("boundary_layer_efficiency", 0.0)), 5)},
        {"term": "Discharge", "value": round(float(coeffs.get("discharge_coefficient", 0.0)), 5)},
        {"term": "Geometry", "value": round(float(coeffs.get("nozzle_geometry_efficiency", 0.0)), 5)},
        {"term": "Overall", "value": round(float(nozzle.get("overall_efficiency", 0.0)), 5)},
    ]


def build_cooling_sweep_rows(state):
    rows = []
    modes = (
        ("Baseline", False, False),
        ("Film", False, True),
        ("Regen", True, False),
        ("Regen + film", True, True),
    )
    for label, regen, film in modes:
        variant_state = dict(state)
        variant_state["regen_cooling"] = regen
        variant_state["film_cooling"] = film
        design = create_engine_design(variant_state)
        values = design.derived.engineering_values
        rows.append(
            {
                "mode": label,
                "thermal_margin_index": round(float(design.derived.thermal_margin_index), 4),
                "calculated_thrust_newtons": round(float(values.get("calculated_thrust_newtons", 0.0)), 4),
                "regen_pressure_drop_kpa": round(float(values.get("regen_pressure_drop_kpa", 0.0)), 4),
                "regen_coolant_temperature_rise_k": round(float(values.get("regen_coolant_temperature_rise_k", 0.0)), 4),
                "film_mass_flow_kg_s": round(float(values.get("film_mass_flow_kg_s", 0.0)), 5),
            }
        )
    return rows


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state, design, _, fast_combustion = build_case_for_flow_model("fast")
    _, _, _, refined_combustion = build_case_for_flow_model("refined")
    _, _, assumptions, combustion = build_case_for_flow_model("navier_stokes")
    pressure_fed_transient_rows, pressure_fed_transient_summary = build_feed_transient_case_rows(False)
    pump_fed_transient_rows, pump_fed_transient_summary = build_feed_transient_case_rows(True)
    values = design.derived.engineering_values
    summary = combustion["summary"]
    thermo = combustion["physics"]["thermochemistry"]
    coeffs = combustion["physics"]["coefficients"]

    write_csv(
        DATA_DIR / "nozzle_contour.csv",
        ["x_mm", "radius_mm", "diameter_mm", "section", "normalized_x"],
        design.derived.nozzle_contour_points,
    )
    write_csv(
        DATA_DIR / "axial_profile.csv",
        ["x_mm", "pressure_kpa", "temperature_k", "velocity_m_s"],
        combustion["axial_profile"],
    )
    write_csv(
        DATA_DIR / "iteration_trace.csv",
        [
            "iteration",
            "chamber_pressure_kpa",
            "chamber_density_kg_m3",
            "chamber_velocity_m_s",
            "thrust_coefficient",
            "relative_error",
        ],
        combustion["iteration_trace"],
    )
    write_csv(
        DATA_DIR / "pressure_modes.csv",
        [
            "architecture",
            "chamber_pressure_kpa",
            "required_feed_pressure_kpa",
            "fuel_tank_pressure_kpa",
            "oxidizer_tank_pressure_kpa",
            "pump_differential_pressure_kpa",
            "fuel_margin_kpa",
            "oxidizer_margin_kpa",
        ],
        build_pressure_rows(state),
    )
    write_csv(
        DATA_DIR / "feed_transient_pressure_fed.csv",
        [
            "architecture",
            "time_s",
            "burn_fraction",
            "chamber_pressure_kpa",
            "required_feed_pressure_kpa",
            "fuel_tank_pressure_kpa",
            "oxidizer_tank_pressure_kpa",
            "fuel_margin_kpa",
            "oxidizer_margin_kpa",
            "pump_differential_pressure_kpa",
            "pump_speed_fraction",
        ],
        pressure_fed_transient_rows,
    )
    write_csv(
        DATA_DIR / "feed_transient_pump_fed.csv",
        [
            "architecture",
            "time_s",
            "burn_fraction",
            "chamber_pressure_kpa",
            "required_feed_pressure_kpa",
            "fuel_tank_pressure_kpa",
            "oxidizer_tank_pressure_kpa",
            "fuel_margin_kpa",
            "oxidizer_margin_kpa",
            "pump_differential_pressure_kpa",
            "pump_speed_fraction",
        ],
        pump_fed_transient_rows,
    )
    write_csv(
        DATA_DIR / "geometry_breakdown.csv",
        ["component", "length_mm"],
        build_geometry_breakdown_rows(design),
    )
    write_csv(
        DATA_DIR / "render_geometry.csv",
        ["component", "dimension", "field_name", "value_mm"],
        build_render_geometry_rows(values),
    )
    write_csv(
        DATA_DIR / "propellant_breakdown.csv",
        ["quantity", "fuel_value", "oxidizer_value", "unit"],
        build_propellant_breakdown_rows(values),
    )
    write_csv(
        DATA_DIR / "nozzle_loss_breakdown.csv",
        ["term", "value"],
        build_nozzle_loss_rows(combustion),
    )
    write_csv(
        DATA_DIR / "cooling_sweep.csv",
        [
            "mode",
            "thermal_margin_index",
            "calculated_thrust_newtons",
            "regen_pressure_drop_kpa",
            "regen_coolant_temperature_rise_k",
            "film_mass_flow_kg_s",
        ],
        build_cooling_sweep_rows(state),
    )
    write_csv(
        DATA_DIR / "flow_mode_comparison.csv",
        [
            "flow_model",
            "flow_model_label",
            "predicted_thrust_newtons",
            "predicted_isp_seconds",
            "chamber_pressure_kpa",
            "mass_flow_kg_s",
            "exit_pressure_kpa",
            "thrust_coefficient",
            "overall_efficiency",
            "separation_efficiency",
            "nozzle_half_angle_deg",
            "nozzle_expansion_ratio",
        ],
        build_flow_mode_comparison_rows(design, fast_combustion, refined_combustion, combustion),
    )
    benchmark_rows = [
        {
            "engine": "StanThrust sample",
            "team": "Model default case",
            "thrust_lbf": round(summary["predicted_thrust_newtons"] / 4.4482216153, 2),
            "thrust_n": round(summary["predicted_thrust_newtons"], 2),
            "chamber_pressure_psi": round(summary["chamber_pressure_kpa"] / 6.8947572932, 2),
            "chamber_pressure_kpa": round(summary["chamber_pressure_kpa"], 2),
        },
        {
            "engine": "Elysium",
            "team": "Texas A&M RED",
            "thrust_lbf": 300.0,
            "thrust_n": round(300.0 * 4.4482216153, 2),
            "chamber_pressure_psi": 150.0,
            "chamber_pressure_kpa": round(150.0 * 6.8947572932, 2),
        },
        {
            "engine": "Juno",
            "team": "ERPL",
            "thrust_lbf": 350.0,
            "thrust_n": round(350.0 * 4.4482216153, 2),
            "chamber_pressure_psi": 450.0,
            "chamber_pressure_kpa": round(450.0 * 6.8947572932, 2),
        },
        {
            "engine": "Iron Lotus",
            "team": "BURPG",
            "thrust_lbf": 2553.0,
            "thrust_n": round(2553.0 * 4.4482216153, 2),
            "chamber_pressure_psi": 500.0,
            "chamber_pressure_kpa": round(500.0 * 6.8947572932, 2),
        },
    ]
    write_csv(
        DATA_DIR / "benchmark_engines.csv",
        ["engine", "team", "thrust_lbf", "thrust_n", "chamber_pressure_psi", "chamber_pressure_kpa"],
        benchmark_rows,
    )
    public_reference_rows = build_public_benchmark_reference_rows()
    write_csv(
        DATA_DIR / "public_benchmark_reference_cases.csv",
        [
            "engine",
            "team",
            "fuel_name",
            "oxidizer_name",
            "reference_thrust_n",
            "reference_chamber_pressure_kpa",
            "reference_burn_time_seconds",
            "reference_mass_flow_kg_s",
            "reference_isp_seconds",
            "mixture_ratio_model",
            "feed_mode_model",
            "regen_cooling_model",
            "film_cooling_model",
            "source_label",
            "source_url",
            "assumptions_note",
        ],
        make_pgfplots_safe_rows(public_reference_rows, ["assumptions_note"]),
    )
    internal_baseline_rows = build_internal_baseline_rows()
    write_csv(
        DATA_DIR / "internal_regression_baselines.csv",
        [
            "case_id",
            "label",
            "note",
            "calculated_thrust_newtons_observed",
            "calculated_thrust_newtons_min",
            "calculated_thrust_newtons_max",
            "chamber_pressure_kpa_observed",
            "chamber_pressure_kpa_min",
            "chamber_pressure_kpa_max",
            "propellant_mass_flow_kg_s_observed",
            "propellant_mass_flow_kg_s_min",
            "propellant_mass_flow_kg_s_max",
            "calculated_impulse_newton_seconds_observed",
            "calculated_impulse_newton_seconds_min",
            "calculated_impulse_newton_seconds_max",
            "nozzle_expansion_ratio_observed",
            "nozzle_expansion_ratio_min",
            "nozzle_expansion_ratio_max",
            "total_stack_length_mm_observed",
            "total_stack_length_mm_min",
            "total_stack_length_mm_max",
            "thermal_margin_index_observed",
            "thermal_margin_index_min",
            "thermal_margin_index_max",
        ],
        internal_baseline_rows,
    )
    write_csv(
        DATA_DIR / "internal_regression_summary.csv",
        [
            "label",
            "thrust_n",
            "thrust_range_n",
            "chamber_pressure_kpa",
            "chamber_pressure_range_kpa",
            "expansion_ratio",
            "expansion_ratio_range",
            "stack_length_mm",
            "stack_length_range_mm",
        ],
        build_internal_regression_summary_rows(internal_baseline_rows),
    )
    reconstructed_rows = build_reconstructed_benchmark_rows(
        assumptions,
        thermochemistry_provider=CanteraThermochemistryProvider(),
    )
    write_csv(
        DATA_DIR / "reconstructed_benchmark_cases.csv",
        [
            "engine",
            "team",
            "fuel_name",
            "oxidizer_name",
            "reference_thrust_n",
            "reference_chamber_pressure_kpa",
            "reference_burn_time_seconds",
            "reference_mass_flow_kg_s",
            "reference_isp_seconds",
            "mixture_ratio_model",
            "feed_mode_model",
            "regen_cooling_model",
            "film_cooling_model",
            "simulated_thrust_n",
            "simulated_chamber_pressure_kpa",
            "simulated_mass_flow_kg_s",
            "simulated_isp_seconds",
            "thrust_error_percent",
            "chamber_pressure_error_percent",
            "mass_flow_error_percent",
            "isp_error_percent",
            "generated_total_length_mm",
            "generated_chamber_length_mm",
            "generated_nozzle_length_mm",
            "generated_throat_diameter_mm",
            "generated_exit_diameter_mm",
            "generated_expansion_ratio",
            "assumptions_note",
            "source_label",
            "source_url",
        ],
        make_pgfplots_safe_rows(reconstructed_rows, ["assumptions_note"]),
    )
    write_csv(
        DATA_DIR / "individual_benchmark_runs.csv",
        [
            "engine",
            "comparable_stats",
            "mean_abs_error_percent",
            "max_abs_error_percent",
            "solved_expansion_ratio",
            "solved_stack_length_mm",
            "solved_field_count",
        ],
        build_individual_benchmark_run_rows(reconstructed_rows),
    )

    macros = {
        "SampleFuel": state["fuel_name"],
        "SampleFlowMode": str(summary.get("flow_model_label", "Cantera plus Navier-Stokes design solve")),
        "SampleOxidizer": state["oxidizer_name"],
        "SampleMixtureRatio": "{0:.2f}".format(state["mixture_ratio"]),
        "SampleTargetThrust": "{0:.1f}".format(state["target_thrust_newtons"]),
        "SampleBurnTime": "{0:.2f}".format(state["burn_time_seconds"]),
        "SampleChamberLength": "{0:.1f}".format(design.derived.chamber_length_mm),
        "SampleNozzleLength": "{0:.1f}".format(design.derived.nozzle_length_mm),
        "SampleThroatDiameter": "{0:.1f}".format(values.get("nozzle_throat_diameter_mm", 0.0)),
        "SampleExitDiameter": "{0:.1f}".format(values.get("nozzle_inner_diameter_mm", 0.0)),
        "SampleExpansionRatio": "{0:.3f}".format(values.get("nozzle_expansion_ratio", 0.0)),
        "SampleContourMethod": str(values.get("nozzle_contour_method_label", "MOC characteristic-net bell contour")),
        "SampleReferenceConicalLength": "{0:.1f}".format(values.get("nozzle_reference_conical_length_mm", 0.0)),
        "SampleBellLengthFraction": "{0:.3f}".format(values.get("nozzle_bell_length_fraction", 0.0)),
        "SampleBellEntranceAngle": "{0:.2f}".format(values.get("nozzle_bell_entrance_angle_deg", 0.0)),
        "SampleBellExitAngle": "{0:.2f}".format(values.get("nozzle_bell_exit_angle_deg", 0.0)),
        "SampleThroatEntryBlendRadius": "{0:.1f}".format(values.get("nozzle_throat_entry_blend_radius_mm", 0.0)),
        "SampleThroatExitBlendRadius": "{0:.1f}".format(values.get("nozzle_throat_exit_blend_radius_mm", 0.0)),
        "SampleInjectorOrificeCount": "{0:.0f}".format(values.get("impinging_orifice_count", 0.0)),
        "SampleInjectorElementRing": "{0:.1f}".format(values.get("injector_element_ring_diameter_mm", 0.0)),
        "SampleChamberPressure": "{0:.1f}".format(summary["chamber_pressure_kpa"]),
        "SampleThrust": "{0:.1f}".format(summary["predicted_thrust_newtons"]),
        "SampleIsp": "{0:.1f}".format(summary["predicted_isp_seconds"]),
        "SampleCstar": "{0:.1f}".format(summary["cstar_m_s"]),
        "SampleExitMach": "{0:.2f}".format(summary["exit_mach"]),
        "SampleGamma": "{0:.4f}".format(thermo["gamma"]),
        "SampleGasConstant": "{0:.1f}".format(thermo["gas_constant_j_kgk"]),
        "SampleChamberTemperature": "{0:.1f}".format(thermo["chamber_temperature_k"]),
        "SampleThermoSource": combustion["metadata"]["thermochemistry"]["source"],
        "SampleNozzleEfficiency": "{0:.4f}".format(coeffs["nozzle_geometry_efficiency"]),
        "SampleOverallNozzleEfficiency": "{0:.4f}".format(combustion["physics"]["nozzle"]["overall_efficiency"]),
        "SampleIterations": str(combustion["iterations"]),
        "SamplePressureFedFinalTankPressure": "{0:.1f}".format(
            float(pressure_fed_transient_summary.get("final_oxidizer_tank_pressure_kpa", 0.0))
        ),
        "SamplePressureFedFeedDrift": "{0:.2f}".format(
            float(pressure_fed_transient_summary.get("chamber_pressure_drift_percent", 0.0))
        ),
        "SamplePumpFedFeedDrift": "{0:.2f}".format(
            float(pump_fed_transient_summary.get("chamber_pressure_drift_percent", 0.0))
        ),
    }
    write_macros(DATA_DIR / "report_macros.tex", macros)

    print("Wrote report assets to", DATA_DIR)


if __name__ == "__main__":
    main()
