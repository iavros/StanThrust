"""Validation pack for Stage 4.1: analytical checks and regression gates.

This module provides lightweight analytical validators for concept-stage designs
to ensure outputs remain within expected physical bounds and match design intent.
Validators are deterministic, require no external solvers, and operate on a
ConceptDesign object or raw solver outputs.
"""

from typing import Dict, List, Tuple
from dataclasses import dataclass

from stanshock.benchmark_cases import get_internal_baseline_cases
from stanshock.concept_model import ConceptDesign


@dataclass(frozen=True)
class ValidationCheck:
    """Result of a single validation check."""
    check_name: str
    passed: bool
    message: str
    severity: str  # "error", "warning", "info"


@dataclass(frozen=True)
class ValidationReport:
    """Report from a full validation pass."""
    passed: bool
    checks: List[ValidationCheck]
    summary: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "passed": self.passed,
            "checks": [
                {
                    "check_name": c.check_name,
                    "passed": c.passed,
                    "message": c.message,
                    "severity": c.severity,
                }
                for c in self.checks
            ],
            "summary": self.summary,
        }


def _check_thrust_delivery(design: ConceptDesign) -> ValidationCheck:
    """Check that calculated thrust is within reasonable bounds of target."""
    target = float(design.inputs.target_thrust_newtons)
    calculated = float(design.derived.engineering_values.get("calculated_thrust_newtons", target))

    # Expect delivery within bounds: 0.7x to 1.3x target (concept-stage tolerance)
    if calculated <= 0.0:
        return ValidationCheck(
            check_name="thrust_delivery",
            passed=False,
            message=f"Calculated thrust is {calculated:.1f} N; expected > 0 N.",
            severity="error",
        )

    ratio = calculated / max(1.0, target)
    if ratio < 0.7 or ratio > 1.3:
        return ValidationCheck(
            check_name="thrust_delivery",
            passed=False,
            message=f"Calculated thrust {calculated:.1f} N is {ratio:.2f}x target {target:.1f} N; expected 0.7–1.3x.",
            severity="warning",
        )

    return ValidationCheck(
        check_name="thrust_delivery",
        passed=True,
        message=f"Calculated thrust {calculated:.1f} N is {ratio:.2f}x target.",
        severity="info",
    )


def _check_mass_flow_balance(design: ConceptDesign) -> ValidationCheck:
    """Check fuel + oxidizer mass flow ≈ total mass flow."""
    eng_vals = design.derived.engineering_values
    fuel_flow = float(eng_vals.get("fuel_mass_flow_kg_s", 0.0))
    oxidizer_flow = float(eng_vals.get("oxidizer_mass_flow_kg_s", 0.0))
    total_flow = float(eng_vals.get("propellant_mass_flow_kg_s", 0.0))

    if total_flow <= 0.0:
        return ValidationCheck(
            check_name="mass_flow_balance",
            passed=False,
            message="Total propellant mass flow is 0 or negative; expected > 0.",
            severity="error",
        )

    sum_flows = fuel_flow + oxidizer_flow
    if sum_flows <= 0.0:
        return ValidationCheck(
            check_name="mass_flow_balance",
            passed=False,
            message="Sum of fuel and oxidizer flows is 0 or negative; expected > 0.",
            severity="error",
        )

    # Allow small tolerance (concept-stage rounding)
    ratio = sum_flows / total_flow
    if abs(ratio - 1.0) > 0.02:
        return ValidationCheck(
            check_name="mass_flow_balance",
            passed=False,
            message=f"Fuel+Oxidizer flow {sum_flows:.3f} kg/s != Total {total_flow:.3f} kg/s (ratio {ratio:.3f}).",
            severity="warning",
        )

    return ValidationCheck(
        check_name="mass_flow_balance",
        passed=True,
        message=f"Mass flow balanced: fuel {fuel_flow:.3f} + oxidizer {oxidizer_flow:.3f} = {sum_flows:.3f} kg/s.",
        severity="info",
    )


def _check_impulse_calc(design: ConceptDesign) -> ValidationCheck:
    """Check that calculated impulse ≈ thrust × burn_time."""
    eng_vals = design.derived.engineering_values
    thrust = float(eng_vals.get("calculated_thrust_newtons", 1.0))
    burn_time = float(eng_vals.get("calculated_burn_time_seconds", 1.0))
    calculated_impulse = float(eng_vals.get("calculated_impulse_newton_seconds", 1.0))

    expected_impulse = thrust * burn_time

    if expected_impulse <= 0.0:
        return ValidationCheck(
            check_name="impulse_calc",
            passed=False,
            message="Expected impulse (thrust × burn_time) is 0 or negative.",
            severity="error",
        )

    ratio = calculated_impulse / expected_impulse
    if abs(ratio - 1.0) > 0.05:
        return ValidationCheck(
            check_name="impulse_calc",
            passed=False,
            message=f"Calculated impulse {calculated_impulse:.0f} N·s ≠ thrust·time {expected_impulse:.0f} N·s (ratio {ratio:.3f}).",
            severity="warning",
        )

    return ValidationCheck(
        check_name="impulse_calc",
        passed=True,
        message=f"Impulse consistent: {calculated_impulse:.0f} N·s ≈ {thrust:.1f} N × {burn_time:.2f} s.",
        severity="info",
    )


def _check_pressure_hierarchy(design: ConceptDesign) -> ValidationCheck:
    """Check architecture-appropriate feed pressure hierarchy."""
    eng_vals = design.derived.engineering_values
    fuel_tank_p = float(eng_vals.get("fuel_tank_pressure_kpa", 0.0))
    oxidizer_tank_p = float(eng_vals.get("oxidizer_tank_pressure_kpa", 0.0))
    chamber_p = float(eng_vals.get("chamber_pressure_kpa", 0.0))
    required_feed_p = float(eng_vals.get("required_feed_pressure_kpa", 0.0))
    fuel_margin_p = float(eng_vals.get("fuel_pressure_margin_kpa", -1.0))
    oxidizer_margin_p = float(eng_vals.get("oxidizer_pressure_margin_kpa", -1.0))
    pump_discharge_p = float(eng_vals.get("pump_discharge_pressure_kpa", 0.0))
    ambient_p = 101.3

    if chamber_p <= ambient_p:
        return ValidationCheck(
            check_name="pressure_hierarchy",
            passed=False,
            message=f"Chamber pressure {chamber_p:.0f} kPa must exceed ambient {ambient_p:.1f} kPa.",
            severity="error",
        )

    if design.inputs.use_pumps:
        if fuel_tank_p <= ambient_p or oxidizer_tank_p <= ambient_p:
            return ValidationCheck(
                check_name="pressure_hierarchy",
                passed=False,
                message=f"Pump inlet tank pressures ({fuel_tank_p:.0f}, {oxidizer_tank_p:.0f} kPa) must exceed ambient {ambient_p:.1f} kPa.",
                severity="error",
            )
        if pump_discharge_p <= required_feed_p or pump_discharge_p <= chamber_p:
            return ValidationCheck(
                check_name="pressure_hierarchy",
                passed=False,
                message=(
                    f"Pump discharge {pump_discharge_p:.0f} kPa must exceed required feed "
                    f"{required_feed_p:.0f} kPa and chamber {chamber_p:.0f} kPa."
                ),
                severity="error",
            )
        if fuel_margin_p < 0.0 or oxidizer_margin_p < 0.0:
            return ValidationCheck(
                check_name="pressure_hierarchy",
                passed=False,
                message=(
                    f"Pump-fed pressure margins must stay non-negative; got fuel {fuel_margin_p:.1f} "
                    f"kPa and oxidizer {oxidizer_margin_p:.1f} kPa."
                ),
                severity="error",
            )
        return ValidationCheck(
            check_name="pressure_hierarchy",
            passed=True,
            message=(
                f"Pump-fed hierarchy OK: tanks ({fuel_tank_p:.0f}, {oxidizer_tank_p:.0f}) kPa "
                f"feed pumps to {pump_discharge_p:.0f} kPa, above chamber {chamber_p:.0f} kPa."
            ),
            severity="info",
        )

    if fuel_tank_p <= required_feed_p or oxidizer_tank_p <= required_feed_p:
        return ValidationCheck(
            check_name="pressure_hierarchy",
            passed=False,
            message=(
                f"Tank pressures ({fuel_tank_p:.0f}, {oxidizer_tank_p:.0f} kPa) must exceed required "
                f"feed pressure {required_feed_p:.0f} kPa."
            ),
            severity="error",
        )
    if fuel_tank_p <= chamber_p or oxidizer_tank_p <= chamber_p:
        return ValidationCheck(
            check_name="pressure_hierarchy",
            passed=False,
            message=f"Tank pressures ({fuel_tank_p:.0f}, {oxidizer_tank_p:.0f} kPa) must exceed chamber ({chamber_p:.0f} kPa).",
            severity="error",
        )
    if fuel_margin_p < 0.0 or oxidizer_margin_p < 0.0:
        return ValidationCheck(
            check_name="pressure_hierarchy",
            passed=False,
            message=(
                f"Pressure-fed tank margins must stay non-negative; got fuel {fuel_margin_p:.1f} "
                f"kPa and oxidizer {oxidizer_margin_p:.1f} kPa."
            ),
            severity="error",
        )

    return ValidationCheck(
        check_name="pressure_hierarchy",
        passed=True,
        message=(
            f"Pressure-fed hierarchy OK: tanks ({fuel_tank_p:.0f}, {oxidizer_tank_p:.0f}) > "
            f"required feed {required_feed_p:.0f} > chamber {chamber_p:.0f} > ambient {ambient_p:.1f} kPa."
        ),
        severity="info",
    )


def _check_index_bounds(design: ConceptDesign) -> ValidationCheck:
    """Check that concept indices (thermal, packaging, mass) are within 0–100."""
    thermal = float(design.derived.thermal_margin_index)
    packaging = float(design.derived.packaging_efficiency_index)
    mass = float(design.derived.dry_mass_index)

    msgs = []
    if not (0.0 <= thermal <= 100.0):
        msgs.append(f"thermal_margin_index {thermal:.1f} out of range [0–100]")
    if not (0.0 <= packaging <= 100.0):
        msgs.append(f"packaging_efficiency_index {packaging:.1f} out of range [0–100]")
    if not (0.0 <= mass <= 100.0):
        msgs.append(f"dry_mass_index {mass:.1f} out of range [0–100]")

    if msgs:
        return ValidationCheck(
            check_name="index_bounds",
            passed=False,
            message="; ".join(msgs),
            severity="error",
        )

    return ValidationCheck(
        check_name="index_bounds",
        passed=True,
        message=f"All indices in range [0–100]: thermal {thermal:.1f}, packaging {packaging:.1f}, mass {mass:.1f}.",
        severity="info",
    )


def _check_geometry_consistency(design: ConceptDesign) -> ValidationCheck:
    """Check that geometry envelope is self-consistent."""
    inputs = design.inputs
    derived = design.derived

    # Chamber should fit inside tank
    if inputs.chamber_diameter_mm > inputs.tank_diameter_mm:
        return ValidationCheck(
            check_name="geometry_consistency",
            passed=False,
            message=f"Chamber diameter {inputs.chamber_diameter_mm} mm > tank diameter {inputs.tank_diameter_mm} mm.",
            severity="error",
        )

    # Nozzle exit should be reasonable
    if inputs.nozzle_diameter_mm <= 0.0:
        return ValidationCheck(
            check_name="geometry_consistency",
            passed=False,
            message=f"Nozzle diameter {inputs.nozzle_diameter_mm} mm must be > 0.",
            severity="error",
        )

    # Total stack length should be positive
    if derived.total_stack_length_mm <= 0.0:
        return ValidationCheck(
            check_name="geometry_consistency",
            passed=False,
            message=f"Total stack length {derived.total_stack_length_mm} mm must be > 0.",
            severity="error",
        )

    # Check that max diameter is at least as large as chamber and nozzle input
    max_diam = derived.maximum_diameter_mm
    if max_diam < inputs.chamber_diameter_mm or max_diam < inputs.nozzle_diameter_mm:
        return ValidationCheck(
            check_name="geometry_consistency",
            passed=False,
            message=f"Maximum diameter {max_diam} mm is less than chamber or nozzle diameter.",
            severity="error",
        )

    return ValidationCheck(
        check_name="geometry_consistency",
        passed=True,
        message=f"Geometry consistent: stack {derived.total_stack_length_mm:.0f} mm, max diam {max_diam:.0f} mm.",
        severity="info",
    )


def _check_propellant_quantities(design: ConceptDesign) -> ValidationCheck:
    """Check that fuel and oxidizer masses are positive and sum to total."""
    eng_vals = design.derived.engineering_values
    fuel_mass = float(eng_vals.get("fuel_mass_kg", 0.0))
    oxidizer_mass = float(eng_vals.get("oxidizer_mass_kg", 0.0))
    total_mass = float(eng_vals.get("propellant_mass_used_kg", 0.0))

    if fuel_mass <= 0.0 or oxidizer_mass <= 0.0:
        return ValidationCheck(
            check_name="propellant_quantities",
            passed=False,
            message=f"Fuel {fuel_mass:.2f} kg or oxidizer {oxidizer_mass:.2f} kg is not positive.",
            severity="error",
        )

    sum_mass = fuel_mass + oxidizer_mass
    ratio = sum_mass / max(0.01, total_mass)
    if abs(ratio - 1.0) > 0.02:
        return ValidationCheck(
            check_name="propellant_quantities",
            passed=False,
            message=f"Fuel+Oxidizer {sum_mass:.2f} kg ≠ Total {total_mass:.2f} kg (ratio {ratio:.3f}).",
            severity="warning",
        )

    return ValidationCheck(
        check_name="propellant_quantities",
        passed=True,
        message=f"Propellant mass OK: fuel {fuel_mass:.2f} kg + oxidizer {oxidizer_mass:.2f} kg = {sum_mass:.2f} kg.",
        severity="info",
    )


def _check_section_margins(design: ConceptDesign) -> ValidationCheck:
    """Check that section-based structural and thermal margins remain non-negative."""
    eng_vals = design.derived.engineering_values
    sections = (
        ("fuel_tank", "fuel tank"),
        ("oxidizer_tank", "oxidizer tank"),
        ("chamber", "chamber"),
        ("throat", "throat"),
        ("nozzle", "nozzle"),
    )
    failures = []
    for key, label in sections:
        structural_margin = float(eng_vals.get(f"{key}_structural_margin_ratio", 0.0))
        thermal_margin_k = float(eng_vals.get(f"{key}_thermal_margin_k", 0.0))
        if structural_margin < 1.0:
            failures.append(
                f"{label} structural margin {structural_margin:.2f}x is below 1.00x"
            )
        if thermal_margin_k < 0.0:
            failures.append(
                f"{label} thermal margin {thermal_margin_k:.1f} K is below 0 K"
            )

    if failures:
        return ValidationCheck(
            check_name="section_margins",
            passed=False,
            message="; ".join(failures),
            severity="error",
        )

    minimum_structural = min(
        float(eng_vals.get(f"{key}_structural_margin_ratio", 0.0)) for key, _ in sections
    )
    minimum_thermal = min(
        float(eng_vals.get(f"{key}_thermal_margin_k", 0.0)) for key, _ in sections
    )
    return ValidationCheck(
        check_name="section_margins",
        passed=True,
        message=(
            f"Section margins OK: minimum structural margin {minimum_structural:.2f}x, "
            f"minimum thermal margin {minimum_thermal:.1f} K."
        ),
        severity="info",
    )


def validate_concept_design(design: ConceptDesign) -> ValidationReport:
    """Validate a design against analytical checks.

    Runs a suite of design-stage analytical validators to ensure design
    outputs fall within expected physical bounds and maintain consistency.
    Returns a report with pass/fail status and detailed messages per check.
    """
    checks = [
        _check_thrust_delivery(design),
        _check_mass_flow_balance(design),
        _check_impulse_calc(design),
        _check_pressure_hierarchy(design),
        _check_section_margins(design),
        _check_index_bounds(design),
        _check_geometry_consistency(design),
        _check_propellant_quantities(design),
    ]

    errors = [c for c in checks if c.severity == "error" and not c.passed]
    passed = len(errors) == 0

    error_count = len(errors)
    warning_count = len([c for c in checks if c.severity == "warning" and not c.passed])
    info_count = len([c for c in checks if c.passed])

    summary = f"Validation {'PASSED' if passed else 'FAILED'}: {info_count} OK, {warning_count} warnings, {error_count} errors."

    return ValidationReport(
        passed=passed,
        checks=checks,
        summary=summary,
    )


def get_regression_baselines() -> Dict[str, Tuple[float, float]]:
    """Return expected ranges for key indicators from regression tests.

    Each baseline is (min, max) for reference designs to compare outputs.
    These represent expected behavior from the concept-stage solvers.
    """
    return {
        "thrust_multiplier": (0.75, 1.25),  # calculated vs target
        "impulse_ratio": (0.95, 1.05),  # calculated vs expected
        "chamber_pressure_kpa": (600.0, 6500.0),
        "tank_pressure_ratio": (0.5, 10.0),  # tank pressure / chamber pressure
        "dry_mass_index": (10.0, 100.0),
        "thermal_margin_index": (5.0, 100.0),
        "packaging_efficiency_index": (5.0, 100.0),
        "nozzle_expansion_ratio": (1.0, 8.0),
    }


def get_regression_baseline_cases() -> Dict[str, Dict[str, object]]:
    """Return canonical internal regression cases used to guard solver drift."""
    return {
        case.case_id: {
            "label": case.label,
            "state": dict(case.state),
            "expected_ranges": {
                metric_name: (float(bounds[0]), float(bounds[1]))
                for metric_name, bounds in case.expected_ranges.items()
            },
            "note": case.note,
        }
        for case in get_internal_baseline_cases()
    }

