from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from liquid_engine_studio.combustion_cfd_solver import run_combustion_cfd_proxy
from liquid_engine_studio.concept_model import create_concept_design
from liquid_engine_studio.defaults import DEFAULT_STATE
from liquid_engine_studio.solver_assumptions import SolverAssumptions


@dataclass(frozen=True)
class PublicBenchmarkCase:
    engine: str
    team: str
    fuel_name: str
    oxidizer_name: str
    target_thrust_newtons: float
    reference_chamber_pressure_kpa: float
    reference_burn_time_seconds: float
    mixture_ratio: float
    injector_type: str
    use_pumps: bool
    regen_cooling: bool
    film_cooling: bool
    target_diameter_mm: float
    tank_diameter_mm: float
    chamber_diameter_mm: float
    nozzle_diameter_mm: float
    reference_mass_flow_kg_s: Optional[float]
    reference_isp_seconds: Optional[float]
    source_label: str
    source_url: str
    assumptions_note: str

    def as_state(self) -> Dict[str, object]:
        state = asdict(DEFAULT_STATE)
        state.update(
            {
                "fuel_name": self.fuel_name,
                "oxidizer_name": self.oxidizer_name,
                "target_thrust_newtons": self.target_thrust_newtons,
                "target_impulse_newton_seconds": self.target_thrust_newtons * self.reference_burn_time_seconds,
                "burn_time_seconds": self.reference_burn_time_seconds,
                "mixture_ratio": self.mixture_ratio,
                "injector_type": self.injector_type,
                "use_pumps": self.use_pumps,
                "regen_cooling": self.regen_cooling,
                "film_cooling": self.film_cooling,
                "target_diameter_mm": self.target_diameter_mm,
                "tank_diameter_mm": self.tank_diameter_mm,
                "chamber_diameter_mm": self.chamber_diameter_mm,
                "nozzle_diameter_mm": self.nozzle_diameter_mm,
            }
        )
        return state


@dataclass(frozen=True)
class InternalBaselineCase:
    case_id: str
    label: str
    state: Dict[str, object]
    expected_ranges: Dict[str, List[float]]
    note: str


def get_public_benchmark_cases() -> List[PublicBenchmarkCase]:
    return [
        PublicBenchmarkCase(
            engine="Elysium",
            team="Texas A&M RED",
            fuel_name="ethanol",
            oxidizer_name="nitrous oxide",
            target_thrust_newtons=300.0 * 4.4482216153,
            reference_chamber_pressure_kpa=150.0 * 6.8947572932,
            reference_burn_time_seconds=3.5,
            mixture_ratio=4.0,
            injector_type="impinging",
            use_pumps=False,
            regen_cooling=False,
            film_cooling=False,
            target_diameter_mm=115.0,
            tank_diameter_mm=110.0,
            chamber_diameter_mm=68.0,
            nozzle_diameter_mm=95.0,
            reference_mass_flow_kg_s=None,
            reference_isp_seconds=None,
            source_label="[A]",
            source_url="https://www.tamured.space/projects/elysium",
            assumptions_note="Published thrust, chamber pressure, and burn time were used directly. Pressure-fed architecture, impinging injector family, and O/F = 4.0 were assumed for reconstruction.",
        ),
        PublicBenchmarkCase(
            engine="Juno",
            team="ERPL",
            fuel_name="ethanol",
            oxidizer_name="liquid oxygen",
            target_thrust_newtons=350.0 * 4.4482216153,
            reference_chamber_pressure_kpa=450.0 * 6.8947572932,
            reference_burn_time_seconds=3.0,
            mixture_ratio=1.3,
            injector_type="impinging",
            use_pumps=False,
            regen_cooling=False,
            film_cooling=False,
            target_diameter_mm=128.0,
            tank_diameter_mm=122.0,
            chamber_diameter_mm=74.0,
            nozzle_diameter_mm=108.0,
            reference_mass_flow_kg_s=0.66,
            reference_isp_seconds=None,
            source_label="[B]",
            source_url="https://erpl.space/juno/",
            assumptions_note="Published thrust, chamber pressure, mass flow, and O/F were used directly. A 3 s short-duration fire and pressure-fed architecture were assumed because the public page does not list burn time or feed mode.",
        ),
        PublicBenchmarkCase(
            engine="Iron Lotus",
            team="BURPG",
            fuel_name="isopropyl alcohol",
            oxidizer_name="nitrous oxide",
            target_thrust_newtons=2553.0 * 4.4482216153,
            reference_chamber_pressure_kpa=500.0 * 6.8947572932,
            reference_burn_time_seconds=3.0,
            mixture_ratio=4.0,
            injector_type="impinging",
            use_pumps=False,
            regen_cooling=False,
            film_cooling=True,
            target_diameter_mm=220.0,
            tank_diameter_mm=190.0,
            chamber_diameter_mm=136.0,
            nozzle_diameter_mm=188.0,
            reference_mass_flow_kg_s=None,
            reference_isp_seconds=211.0,
            source_label="[C]",
            source_url="https://burpg.org/iron-lotus",
            assumptions_note="Published thrust, chamber pressure, 3 s firing time, and 211 s Isp were used directly. O/F = 4.0 and pressure-fed architecture were inferred from the closely related Lotus Dev 2 program, and film cooling was enabled because the public Iron Lotus page mentions supplemental film cooling.",
        ),
    ]


def get_internal_baseline_cases() -> List[InternalBaselineCase]:
    return [
        InternalBaselineCase(
            case_id="default_pressure_fed",
            label="Default pressure-fed sample",
            state={},
            expected_ranges={
                "calculated_thrust_newtons": [190.0, 230.0],
                "chamber_pressure_kpa": [1450.0, 1800.0],
                "propellant_mass_flow_kg_s": [0.08, 0.12],
                "calculated_impulse_newton_seconds": [2850.0, 3150.0],
                "nozzle_expansion_ratio": [10.8, 12.2],
                "total_stack_length_mm": [920.0, 1025.0],
                "thermal_margin_index": [95.0, 100.0],
            },
            note="Baseline pressure-fed sample used for ordinary StanThrust regression checks.",
        ),
        InternalBaselineCase(
            case_id="pump_regen_midscale",
            label="Pump-fed regen midscale case",
            state={
                "use_pumps": True,
                "regen_cooling": True,
                "fuel_name": "Ethanol",
                "oxidizer_name": "Liquid Oxygen",
                "mixture_ratio": 1.4,
                "target_thrust_newtons": 900.0,
                "burn_time_seconds": 14.0,
                "target_impulse_newton_seconds": 12600.0,
                "target_diameter_mm": 130.0,
                "tank_diameter_mm": 126.0,
                "chamber_diameter_mm": 78.0,
                "nozzle_diameter_mm": 112.0,
            },
            expected_ranges={
                "calculated_thrust_newtons": [780.0, 905.0],
                "chamber_pressure_kpa": [1950.0, 2350.0],
                "propellant_mass_flow_kg_s": [0.34, 0.46],
                "calculated_impulse_newton_seconds": [11900.0, 13250.0],
                "nozzle_expansion_ratio": [10.9, 12.4],
                "total_stack_length_mm": [1090.0, 1215.0],
                "thermal_margin_index": [95.0, 100.0],
            },
            note="Midscale pump-fed case used to detect drift in pressure closure and cooled geometry sizing.",
        ),
        InternalBaselineCase(
            case_id="film_pressure_fed_large",
            label="Film-cooled pressure-fed large case",
            state={
                "use_pumps": False,
                "regen_cooling": False,
                "film_cooling": True,
                "fuel_name": "Isopropyl Alcohol",
                "oxidizer_name": "Nitrous Oxide",
                "mixture_ratio": 3.8,
                "target_thrust_newtons": 1800.0,
                "burn_time_seconds": 8.0,
                "target_impulse_newton_seconds": 14400.0,
                "target_diameter_mm": 180.0,
                "tank_diameter_mm": 170.0,
                "chamber_diameter_mm": 108.0,
                "nozzle_diameter_mm": 150.0,
            },
            expected_ranges={
                "calculated_thrust_newtons": [1450.0, 1685.0],
                "chamber_pressure_kpa": [1900.0, 2350.0],
                "propellant_mass_flow_kg_s": [0.70, 0.90],
                "calculated_impulse_newton_seconds": [13600.0, 15150.0],
                "nozzle_expansion_ratio": [9.8, 11.4],
                "total_stack_length_mm": [1625.0, 1815.0],
                "thermal_margin_index": [95.0, 100.0],
            },
            note="Large pressure-fed case used to guard film-cooling and packaging behavior at larger scales.",
        ),
    ]


def _pct_error(predicted: Optional[float], reference: Optional[float]) -> Optional[float]:
    if predicted is None or reference in (None, 0.0):
        return None
    return 100.0 * (float(predicted) - float(reference)) / float(reference)


def build_public_benchmark_reference_rows() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for case in get_public_benchmark_cases():
        rows.append(
            {
                "engine": case.engine,
                "team": case.team,
                "fuel_name": case.fuel_name,
                "oxidizer_name": case.oxidizer_name,
                "reference_thrust_n": round(case.target_thrust_newtons, 3),
                "reference_chamber_pressure_kpa": round(case.reference_chamber_pressure_kpa, 3),
                "reference_burn_time_seconds": round(case.reference_burn_time_seconds, 3),
                "reference_mass_flow_kg_s": "" if case.reference_mass_flow_kg_s is None else round(case.reference_mass_flow_kg_s, 5),
                "reference_isp_seconds": "" if case.reference_isp_seconds is None else round(case.reference_isp_seconds, 5),
                "mixture_ratio_model": round(case.mixture_ratio, 4),
                "feed_mode_model": "pump-fed" if case.use_pumps else "pressure-fed",
                "regen_cooling_model": "yes" if case.regen_cooling else "no",
                "film_cooling_model": "yes" if case.film_cooling else "no",
                "source_label": case.source_label,
                "source_url": case.source_url,
                "assumptions_note": case.assumptions_note,
            }
        )
    return rows


def build_reconstructed_benchmark_rows(
    assumptions: SolverAssumptions,
    thermochemistry_provider: Optional[object] = None,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    gravity_m_s2 = float(assumptions.gravity_m_s2)

    for case in get_public_benchmark_cases():
        design = create_concept_design(case.as_state())
        combustion = run_combustion_cfd_proxy(
            design,
            assumptions,
            station_count=25,
            thermochemistry_mode="auto",
            thermochemistry_provider=thermochemistry_provider,
        )
        summary = combustion["summary"]
        values = design.derived.engineering_values
        reference_mass_flow = case.reference_mass_flow_kg_s
        reference_isp = case.reference_isp_seconds

        if reference_mass_flow is None and reference_isp is not None:
            reference_mass_flow = case.target_thrust_newtons / (reference_isp * gravity_m_s2)
        if reference_isp is None and reference_mass_flow is not None:
            reference_isp = case.target_thrust_newtons / (reference_mass_flow * gravity_m_s2)

        rows.append(
            {
                "engine": case.engine,
                "team": case.team,
                "fuel_name": case.fuel_name,
                "oxidizer_name": case.oxidizer_name,
                "reference_thrust_n": round(case.target_thrust_newtons, 3),
                "reference_chamber_pressure_kpa": round(case.reference_chamber_pressure_kpa, 3),
                "reference_burn_time_seconds": round(case.reference_burn_time_seconds, 3),
                "reference_mass_flow_kg_s": "" if reference_mass_flow is None else round(reference_mass_flow, 5),
                "reference_isp_seconds": "" if reference_isp is None else round(reference_isp, 5),
                "mixture_ratio_model": round(case.mixture_ratio, 4),
                "feed_mode_model": "pump-fed" if case.use_pumps else "pressure-fed",
                "regen_cooling_model": "yes" if case.regen_cooling else "no",
                "film_cooling_model": "yes" if case.film_cooling else "no",
                "simulated_thrust_n": round(float(summary["predicted_thrust_newtons"]), 5),
                "simulated_chamber_pressure_kpa": round(float(summary["chamber_pressure_kpa"]), 5),
                "simulated_mass_flow_kg_s": round(float(summary["mass_flow_kg_s"]), 5),
                "simulated_isp_seconds": round(float(summary["predicted_isp_seconds"]), 5),
                "thrust_error_percent": "" if _pct_error(summary["predicted_thrust_newtons"], case.target_thrust_newtons) is None else round(_pct_error(summary["predicted_thrust_newtons"], case.target_thrust_newtons), 5),
                "chamber_pressure_error_percent": "" if _pct_error(summary["chamber_pressure_kpa"], case.reference_chamber_pressure_kpa) is None else round(_pct_error(summary["chamber_pressure_kpa"], case.reference_chamber_pressure_kpa), 5),
                "mass_flow_error_percent": "" if _pct_error(summary["mass_flow_kg_s"], reference_mass_flow) is None else round(_pct_error(summary["mass_flow_kg_s"], reference_mass_flow), 5),
                "isp_error_percent": "" if _pct_error(summary["predicted_isp_seconds"], reference_isp) is None else round(_pct_error(summary["predicted_isp_seconds"], reference_isp), 5),
                "generated_total_length_mm": round(float(design.derived.total_stack_length_mm), 5),
                "generated_chamber_length_mm": round(float(design.derived.chamber_length_mm), 5),
                "generated_nozzle_length_mm": round(float(design.derived.nozzle_length_mm), 5),
                "generated_throat_diameter_mm": round(float(values.get("nozzle_throat_diameter_mm", 0.0)), 5),
                "generated_exit_diameter_mm": round(float(values.get("nozzle_inner_diameter_mm", 0.0)), 5),
                "generated_expansion_ratio": round(float(values.get("nozzle_expansion_ratio", 0.0)), 5),
                "assumptions_note": case.assumptions_note,
                "source_label": case.source_label,
                "source_url": case.source_url,
            }
        )
    return rows


def build_internal_baseline_rows() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []

    for case in get_internal_baseline_cases():
        design = create_concept_design(case.state)
        values = design.derived.engineering_values
        observed = {
            "calculated_thrust_newtons": float(values.get("calculated_thrust_newtons", 0.0)),
            "chamber_pressure_kpa": float(values.get("chamber_pressure_kpa", 0.0)),
            "propellant_mass_flow_kg_s": float(values.get("propellant_mass_flow_kg_s", 0.0)),
            "calculated_impulse_newton_seconds": float(values.get("calculated_impulse_newton_seconds", 0.0)),
            "nozzle_expansion_ratio": float(values.get("nozzle_expansion_ratio", 0.0)),
            "total_stack_length_mm": float(design.derived.total_stack_length_mm),
            "thermal_margin_index": float(design.derived.thermal_margin_index),
        }

        row: Dict[str, object] = {
            "case_id": case.case_id,
            "label": case.label,
            "note": case.note,
        }
        for metric_name, observed_value in observed.items():
            lower_bound, upper_bound = case.expected_ranges[metric_name]
            row[f"{metric_name}_observed"] = round(observed_value, 5)
            row[f"{metric_name}_min"] = round(float(lower_bound), 5)
            row[f"{metric_name}_max"] = round(float(upper_bound), 5)
        rows.append(row)
    return rows
