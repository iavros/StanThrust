"""Thermophysical property access for regenerative coolants."""

from typing import Dict

try:
    from CoolProp import __version__ as COOLPROP_VERSION
    from CoolProp.CoolProp import PhaseSI, PropsSI
except ImportError as exc:  # pragma: no cover - exercised by packaged startup checks
    from stanthrust import dependency_error_message

    raise RuntimeError(dependency_error_message("CoolProp", exc)) from exc


COOLANT_BACKENDS = {
    "ethanol": "Ethanol",
    "methane": "Methane",
}

COOLANT_DEFAULT_INLET_TEMPERATURE_K = {
    "ethanol": 293.15,
    "methane": 110.0,
}

PROPERTY_SOURCE = {
    "name": "CoolProp Helmholtz equation-of-state and transport-property backend",
    "version": COOLPROP_VERSION,
    "url": "https://coolprop.org/fluid_properties/",
    "reference": "Fluid-specific equations and transport correlations listed by CoolProp",
}


def coolant_default_inlet_temperature_k(fuel_name: str) -> float:
    key = (fuel_name or "").strip().lower()
    if key not in COOLANT_DEFAULT_INLET_TEMPERATURE_K:
        raise ValueError(
            "Regenerative cooling property data are unavailable for {0}. "
            "Supported coolants are Ethanol and Methane.".format(fuel_name or "the selected fuel")
        )
    return COOLANT_DEFAULT_INLET_TEMPERATURE_K[key]


def coolant_phase_envelope(fuel_name: str) -> Dict[str, float]:
    """Return critical and triple-point properties used by phase checks."""

    key = (fuel_name or "").strip().lower()
    fluid = COOLANT_BACKENDS.get(key)
    if fluid is None:
        raise ValueError(
            "Regenerative cooling property data are unavailable for {0}. "
            "Supported coolants are Ethanol and Methane.".format(fuel_name or "the selected fuel")
        )
    return {
        "critical_temperature_k": float(PropsSI("Tcrit", fluid)),
        "critical_pressure_kpa": float(PropsSI("pcrit", fluid)) / 1000.0,
        "triple_temperature_k": float(PropsSI("Ttriple", fluid)),
    }


def coolant_single_phase_pressure_requirement_kpa(
    fuel_name: str,
    maximum_bulk_temperature_k: float,
    pressure_margin_ratio: float = 0.08,
) -> Dict[str, object]:
    """Calculate the minimum pressure for a liquid or supercritical coolant path."""

    key = (fuel_name or "").strip().lower()
    fluid = COOLANT_BACKENDS.get(key)
    if fluid is None:
        raise ValueError(
            "Regenerative cooling property data are unavailable for {0}. "
            "Supported coolants are Ethanol and Methane.".format(fuel_name or "the selected fuel")
        )
    envelope = coolant_phase_envelope(fuel_name)
    critical_temperature = envelope["critical_temperature_k"]
    critical_pressure = envelope["critical_pressure_kpa"]
    temperature = max(float(maximum_bulk_temperature_k), envelope["triple_temperature_k"] + 0.01)
    margin = min(0.50, max(0.0, float(pressure_margin_ratio)))
    if temperature >= critical_temperature:
        boundary_pressure_kpa = critical_pressure
        basis = "critical-pressure"
    else:
        boundary_pressure_kpa = float(PropsSI("P", "T", temperature, "Q", 0.0, fluid)) / 1000.0
        basis = "saturation-pressure"
    return {
        **envelope,
        "maximum_bulk_temperature_k": temperature,
        "phase_boundary_pressure_kpa": boundary_pressure_kpa,
        "pressure_margin_ratio": margin,
        "minimum_single_phase_pressure_kpa": boundary_pressure_kpa * (1.0 + margin),
        "basis": basis,
        "backend": PROPERTY_SOURCE["name"],
        "backend_version": COOLPROP_VERSION,
    }


def coolant_property_state(fuel_name: str, temperature_k: float, pressure_kpa: float) -> Dict[str, object]:
    """Return a single-phase coolant state at the requested temperature and pressure."""

    key = (fuel_name or "").strip().lower()
    fluid = COOLANT_BACKENDS.get(key)
    if fluid is None:
        raise ValueError(
            "Regenerative cooling property data are unavailable for {0}. "
            "Supported coolants are Ethanol and Methane.".format(fuel_name or "the selected fuel")
        )
    temperature = float(temperature_k)
    pressure_pa = float(pressure_kpa) * 1000.0
    if temperature <= 0.0 or pressure_pa <= 0.0:
        raise ValueError("Coolant temperature and pressure must both be positive.")
    try:
        phase = str(PhaseSI("T", temperature, "P", pressure_pa, fluid))
        density = float(PropsSI("Dmass", "T", temperature, "P", pressure_pa, fluid))
        heat_capacity = float(PropsSI("Cpmass", "T", temperature, "P", pressure_pa, fluid))
        viscosity = float(PropsSI("VISCOSITY", "T", temperature, "P", pressure_pa, fluid))
        conductivity = float(PropsSI("CONDUCTIVITY", "T", temperature, "P", pressure_pa, fluid))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Coolant property solve failed for {0} at {1:.2f} K and {2:.2f} kPa: {3}".format(
                fuel_name,
                temperature,
                pressure_kpa,
                exc,
            )
        ) from exc
    if "two_phase" in phase.lower() or "twophase" in phase.lower():
        raise ValueError(
            "The {0} coolant state entered the two-phase region at {1:.2f} K and {2:.2f} kPa."
            .format(fuel_name, temperature, pressure_kpa)
        )
    prandtl = heat_capacity * viscosity / conductivity
    return {
        "fluid": fluid,
        "phase": phase,
        "temperature_k": temperature,
        "pressure_kpa": float(pressure_kpa),
        "density_kg_m3": density,
        "cp_j_kg_k": heat_capacity,
        "viscosity_pa_s": viscosity,
        "conductivity_w_m_k": conductivity,
        "prandtl": prandtl,
        "backend": PROPERTY_SOURCE["name"],
        "backend_version": COOLPROP_VERSION,
    }
