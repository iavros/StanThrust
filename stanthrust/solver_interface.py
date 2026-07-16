from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from stanthrust.design_model import DesignInputs, create_engine_design
from stanthrust.feed_pressure_drop_solver import solve as solve_feed_pressure_drop
from stanthrust.inputs import MATERIAL_OPTIONS, lookup_propellant


SOLVER_INTERFACE_VERSION = "1.0"


@dataclass
class SolverMetadata:
    solver_name: str
    solver_version: str
    solver_mode: str
    input_schema_version: str
    output_schema_version: str


@dataclass
class ValidationReport:
    is_valid: bool
    messages: List[str]
    normalized_request: Dict[str, object]

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class SolverResult:
    metadata: Dict[str, object]
    status: str
    payload: Dict[str, object]
    warnings: List[str]
    trace: List[str]

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class SummaryBlock:
    title: str
    key_values: Dict[str, object]
    notes: List[str]

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


def _build_design_request(raw_state: Dict[str, object]) -> Dict[str, object]:
    inputs = DesignInputs.from_mapping(raw_state)
    fuel = lookup_propellant(inputs.fuel_name, "fuel")
    oxidizer = lookup_propellant(inputs.oxidizer_name, "oxidizer")
    design_request = {
        "targets": {
            "target_thrust_newtons": inputs.target_thrust_newtons,
            "target_chamber_pressure_kpa": inputs.target_chamber_pressure_kpa,
            "target_impulse_newton_seconds": inputs.target_impulse_newton_seconds,
            "target_diameter_mm": inputs.target_diameter_mm,
            "burn_time_seconds": inputs.burn_time_seconds,
        },
        "propellants": {
            "fuel": inputs.fuel_name,
            "oxidizer": inputs.oxidizer_name,
            "mixture_ratio": inputs.mixture_ratio,
            "fuel_record": asdict(fuel),
            "oxidizer_record": asdict(oxidizer),
        },
        "geometry_limits": {
            "tank_diameter_mm": inputs.tank_diameter_mm,
            "chamber_diameter_mm": inputs.chamber_diameter_mm,
            "nozzle_diameter_mm": inputs.nozzle_diameter_mm,
            "nozzle_exit_mode": inputs.nozzle_exit_mode,
            "nozzle_expansion_bias": inputs.nozzle_expansion_bias,
        },
        "materials": {
            "fuel_tank_material": inputs.fuel_tank_material,
            "oxidizer_tank_material": inputs.oxidizer_tank_material,
            "feed_system_material": inputs.feed_system_material,
            "chamber_material": inputs.chamber_material,
            "nozzle_material": inputs.nozzle_material,
        },
        "analysis": {
            "factor_of_safety": inputs.factor_of_safety,
        },
        "architecture": {
            "injector_type": inputs.injector_type,
            "use_pumps": inputs.use_pumps,
            "regen_cooling": inputs.regen_cooling,
            "film_cooling": inputs.film_cooling,
            "packaging_bias": inputs.packaging_bias,
        },
    }
    design_request["feed_pressure_drop_request"] = {
        "status": "calculated",
        "model_family": "reduced-order",
        "time_model": "burn-transient",
        "history_steps": 31 if inputs.use_pumps else 41,
        "initial_fill_fraction": 0.72 if inputs.use_pumps else 0.58,
        "pressurant_polytropic_index": 1.08,
        "pump_inlet_pressure_decay_fraction": 0.09,
        "notes": [
            "Stage 2 transient feed-system model request.",
            "This reduced-order path tracks burn-time pressure drift and feed margin rather than a single static closure.",
        ],
    }
    return design_request


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _request_to_state(design_request: Dict[str, object]) -> Dict[str, object]:
    targets = _as_dict(design_request.get("targets"))
    propellants = _as_dict(design_request.get("propellants"))
    geometry_limits = _as_dict(design_request.get("geometry_limits"))
    materials = _as_dict(design_request.get("materials"))
    analysis = _as_dict(design_request.get("analysis"))
    architecture = _as_dict(design_request.get("architecture"))
    injector_type = str(architecture.get("injector_type", "impinging") or "impinging").strip().lower()
    if injector_type not in {"impinging", "pintle"}:
        injector_type = "impinging"
    return {
        "fuel_name": propellants.get("fuel", "Ethanol"),
        "oxidizer_name": propellants.get("oxidizer", "Liquid Oxygen"),
        "mixture_ratio": propellants.get("mixture_ratio", 1.4),
        "target_thrust_newtons": targets.get("target_thrust_newtons", 250.0),
        "target_chamber_pressure_kpa": targets.get("target_chamber_pressure_kpa", 0.0),
        "target_impulse_newton_seconds": targets.get("target_impulse_newton_seconds", 3000.0),
        "target_diameter_mm": targets.get("target_diameter_mm", 110.0),
        "burn_time_seconds": targets.get("burn_time_seconds", 12.0),
        "tank_diameter_mm": geometry_limits.get("tank_diameter_mm", 110.0),
        "chamber_diameter_mm": geometry_limits.get("chamber_diameter_mm", 68.0),
        "nozzle_diameter_mm": geometry_limits.get("nozzle_diameter_mm", 95.0),
        "nozzle_exit_mode": geometry_limits.get("nozzle_exit_mode", "auto"),
        "nozzle_expansion_bias": geometry_limits.get("nozzle_expansion_bias", "pressure_matched"),
        "factor_of_safety": analysis.get("factor_of_safety", 2.0),
        "fuel_tank_material": materials.get("fuel_tank_material", "Aluminum 6061-T6"),
        "oxidizer_tank_material": materials.get("oxidizer_tank_material", "Aluminum 6061-T6"),
        "feed_system_material": materials.get("feed_system_material", "Stainless Steel 304"),
        "chamber_material": materials.get("chamber_material", "Stainless Steel 304"),
        "nozzle_material": materials.get("nozzle_material", "Stainless Steel 304"),
        "packaging_bias": architecture.get("packaging_bias", "balanced"),
        "injector_type": injector_type,
        "use_pumps": bool(architecture.get("use_pumps", False)),
        "regen_cooling": bool(architecture.get("regen_cooling", False)),
        "film_cooling": bool(architecture.get("film_cooling", False)),
    }


def validate_inputs(design_request: Dict[str, object]) -> Dict[str, object]:
    """Validate and normalize a design request into stable top-level sections."""
    messages: List[str] = []
    request = design_request
    if "targets" not in request:
        request = _build_design_request(request)
        messages.append("Input mapped from raw UI-style state into design_request sections.")

    normalized_request = _build_design_request(_request_to_state(request))

    architecture = _as_dict(normalized_request.get("architecture"))
    packaging_bias = str(architecture.get("packaging_bias", "balanced"))
    if packaging_bias not in {"balanced", "compact", "serviceable"}:
        messages.append("Unknown packaging_bias; normalized to balanced.")
        architecture["packaging_bias"] = "balanced"
    normalized_request["architecture"] = architecture

    injector_type = str(architecture.get("injector_type", "impinging") or "impinging").strip().lower()
    if injector_type not in {"impinging", "pintle"}:
        messages.append("Unknown injector_type; normalized to impinging.")
        injector_type = "impinging"
    architecture["injector_type"] = injector_type

    for key, value in _as_dict(normalized_request.get("materials")).items():
        if value not in MATERIAL_OPTIONS:
            messages.append("Custom material accepted for {0}: {1}".format(key, value))

    return ValidationReport(
        is_valid=True,
        messages=messages,
        normalized_request=normalized_request,
    ).as_dict()


def solve(design_request: Dict[str, object], upstream_context: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Common interface solve handle; individual solver modules own detailed solving."""
    validation = validate_inputs(design_request)
    status = "ok" if validation["is_valid"] else "invalid"
    trace = ["Validated request through common solver interface v{0}.".format(SOLVER_INTERFACE_VERSION)]
    if upstream_context:
        trace.append("Upstream context keys: {0}".format(", ".join(sorted(upstream_context.keys()))))

    feed_context = dict(upstream_context or {})
    try:
        design_for_feed = create_engine_design(_request_to_state(validation["normalized_request"]))
        design_values = design_for_feed.derived.engineering_values
        feed_context.setdefault("chamber_pressure_kpa", design_values.get("chamber_pressure_kpa"))
        feed_context.setdefault("propellant_mass_flow_kg_s", design_values.get("propellant_mass_flow_kg_s"))
    except Exception as exc:
        trace.append("Design-layer feed context unavailable: {0}".format(exc))

    feed_solver_result = solve_feed_pressure_drop(
        validation["normalized_request"], upstream_context=feed_context
    )
    trace.append("Stage 2 transient feed model executed.")
    return SolverResult(
        metadata={"solver_name": "Common Solver Interface", "solver_version": SOLVER_INTERFACE_VERSION},
        status=status,
        payload={
            "normalized_request": validation["normalized_request"],
            "feed_pressure_drop": feed_solver_result,
        },
        warnings=list(validation.get("messages", []))
        + list(feed_solver_result.get("warnings", [])),
        trace=trace,
    ).as_dict()


def summarize(solver_result: Dict[str, object]) -> Dict[str, object]:
    payload = _as_dict(solver_result.get("payload"))
    normalized_request = _as_dict(payload.get("normalized_request"))
    targets = _as_dict(normalized_request.get("targets"))
    return SummaryBlock(
        title="Design Request Validation",
        key_values={
            "status": solver_result.get("status", "unknown"),
            "target_thrust_newtons": targets.get("target_thrust_newtons"),
            "target_diameter_mm": targets.get("target_diameter_mm"),
            "injector_type": _as_dict(normalized_request.get("architecture")).get("injector_type"),
            "feed_pressure_drop_status": _as_dict(payload.get("feed_pressure_drop")).get("status"),
        },
        notes=list(solver_result.get("warnings", [])) if isinstance(solver_result.get("warnings"), list) else [],
    ).as_dict()


