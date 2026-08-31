"""Cantera-backed equilibrium thermochemistry and frozen-composition transport."""

import importlib
import site
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from stanthrust import dependency_error_message


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


DATA_DIR = Path(__file__).resolve().parent / "data"
EQUILIBRIUM_MECHANISM_PATH = DATA_DIR / "rocket_mech_equilibrium.yaml"
TRANSPORT_MECHANISM_NAME = "gri30.yaml"
MINIMUM_TRANSPORT_MASS_FRACTION_COVERAGE = 0.999999


def _import_cantera():
    """Import Cantera, adding the user site-packages directory when needed."""
    try:
        return importlib.import_module("cantera")
    except Exception:
        user_site = site.getusersitepackages()
        if user_site and user_site not in sys.path:
            try:
                site.addsitedir(user_site)
            except Exception:
                pass
        try:
            return importlib.import_module("cantera")
        except Exception as second_error:
            raise RuntimeError(dependency_error_message("Cantera", second_error)) from second_error


@dataclass(frozen=True)
class ThermochemistryResult:
    gamma: float
    gas_constant_j_kgk: float
    chamber_temperature_k: float
    cstar_efficiency_factor: float
    provider_name: str
    source: str
    status: str
    transport_mass_fractions: Tuple[Tuple[str, float], ...]
    transport_mechanism: str
    transport_mass_fraction_coverage: float
    note: str = ""


def apply_frozen_transport_to_profile(
    axial_profile: Sequence[Mapping[str, object]],
    mass_fractions: Sequence[Tuple[str, float]],
) -> List[Dict[str, object]]:
    """Evaluate mixture transport at each solved station with frozen composition."""

    if not mass_fractions:
        raise RuntimeError("Cantera transport composition is empty.")
    ct = _import_cantera()
    try:
        gas = ct.Solution(TRANSPORT_MECHANISM_NAME)
        composition = {str(name): float(value) for name, value in mass_fractions if value > 0.0}
        gas.TPY = 300.0, 101325.0, composition
    except Exception as exc:
        raise RuntimeError("Cantera transport mechanism initialization failed: {0}".format(exc)) from exc

    solved_profile: List[Dict[str, object]] = []
    for index, source_row in enumerate(axial_profile):
        row = dict(source_row)
        try:
            temperature_k = max(200.0, float(row["temperature_k"]))
            pressure_pa = max(1000.0, float(row["pressure_kpa"]) * 1000.0)
            gas.TP = temperature_k, pressure_pa
            viscosity_pa_s = float(gas.viscosity)
            conductivity_w_m_k = float(gas.thermal_conductivity)
            cp_j_kg_k = float(gas.cp_mass)
            prandtl = cp_j_kg_k * viscosity_pa_s / conductivity_w_m_k
        except Exception as exc:
            raise RuntimeError(
                "Cantera transport solve failed at axial station {0}: {1}".format(index, exc)
            ) from exc
        row.update(
            {
                "gas_viscosity_pa_s": round(viscosity_pa_s, 10),
                "gas_conductivity_w_m_k": round(conductivity_w_m_k, 8),
                "gas_cp_j_kg_k": round(cp_j_kg_k, 6),
                "gas_prandtl": round(prandtl, 8),
                "gas_transport_source": "cantera-frozen-composition:{0}".format(
                    TRANSPORT_MECHANISM_NAME
                ),
            }
        )
        solved_profile.append(row)
    return solved_profile


class ThermochemistryProvider:
    name = "provider"

    def solve(self, design, assumptions, fuel, oxidizer) -> ThermochemistryResult:
        raise NotImplementedError


class CanteraThermochemistryProvider(ThermochemistryProvider):
    name = "cantera"

    _fuel_species = {
        "methane": "CH4",
        "ethanol": "C2H5OH",
        "isopropyl alcohol": "IC3H7OH",
        "isopropanol": "IC3H7OH",
    }
    _oxidizer_species = {
        "liquid oxygen": "O2",
        "nitrous oxide": "N2O",
        "hydrogen peroxide": "H2O2",
    }
    _fuel_stoich_of_o2 = {
        "methane": 4.0,
        "ethanol": 96.0 / 46.0,
        "isopropyl alcohol": 144.0 / 60.0,
        "isopropanol": 144.0 / 60.0,
    }
    _oxidizer_oxygen_mass_fraction = {
        "liquid oxygen": 1.0,
        "nitrous oxide": 16.0 / 44.0,
        "hydrogen peroxide": 32.0 / 34.0,
    }

    def _load_mechanism(self, ct) -> Tuple[object, Path, str]:
        """Load the highest-fidelity bundled mechanism available."""
        phase_names = ("rocket_detailed", "rocket_minimal", "rocket")
        mechanism_path = EQUILIBRIUM_MECHANISM_PATH
        if not mechanism_path.exists():
            raise RuntimeError(f"Bundled Cantera mechanism is missing: {mechanism_path.name}")
        for phase_name in phase_names:
            try:
                gas = ct.Solution(str(mechanism_path), phase_name)
                return gas, mechanism_path, phase_name
            except Exception:
                pass
        try:
            gas = ct.Solution(str(mechanism_path))
            return gas, mechanism_path, "default"
        except Exception:
            pass
        raise RuntimeError("No bundled Cantera mechanism could be loaded.")

    def _build_phi(self, design, fuel_name: str, oxidizer_name: str) -> Optional[float]:
        stoich_of_o2 = self._fuel_stoich_of_o2.get(fuel_name)
        oxidizer_oxygen_fraction = self._oxidizer_oxygen_mass_fraction.get(oxidizer_name)
        if stoich_of_o2 is None or oxidizer_oxygen_fraction is None:
            return None
        stoich_of = stoich_of_o2 / max(1e-6, oxidizer_oxygen_fraction)
        actual_of = max(0.1, float(design.inputs.mixture_ratio))
        return _clamp(stoich_of / actual_of, 0.45, 2.4)

    def solve(self, design, assumptions, fuel, oxidizer) -> ThermochemistryResult:
        ct = _import_cantera()

        fuel_key = fuel.name.strip().lower()
        oxidizer_key = oxidizer.name.strip().lower()
        fuel_species = self._fuel_species.get(fuel_key)
        oxidizer_species = self._oxidizer_species.get(oxidizer_key)
        phi = self._build_phi(design, fuel_key, oxidizer_key)
        if fuel_species is None or oxidizer_species is None or phi is None:
            missing = []
            if fuel_species is None:
                missing.append(f"fuel mapping '{fuel_key}'")
            if oxidizer_species is None:
                missing.append(f"oxidizer mapping '{oxidizer_key}'")
            if phi is None:
                missing.append("stoichiometry/phi")
            reason = "; ".join(missing) if missing else "unknown"
            raise RuntimeError(f"Cantera thermochemistry cannot be solved for this propellant setup: {reason}.")

        try:
            gas, mechanism_path, phase_name = self._load_mechanism(ct)
        except Exception as exc:
            raise RuntimeError("Cantera mechanism load failed: {0}".format(str(exc))) from exc

        missing_species = []
        if fuel_species not in gas.species_names:
            missing_species.append(fuel_species)
        if oxidizer_species not in gas.species_names:
            missing_species.append(oxidizer_species)
        if missing_species:
            raise RuntimeError(
                "Cantera thermochemistry cannot be solved because the bundled mechanism is missing species: {0}.".format(
                    ", ".join(missing_species)
                )
            )

        try:
            chamber_pressure_pa = max(
                100000.0,
                float(design.derived.engineering_values.get("chamber_pressure_kpa", 1000.0)) * 1000.0,
            )
            gas.TP = 298.15, chamber_pressure_pa
            gas.set_equivalence_ratio(phi, fuel=fuel_species, oxidizer=oxidizer_species)
            gas.equilibrate("HP")
            cp_mass = max(1.0, float(gas.cp_mass))
            cv_mass = max(1.0, float(gas.cv_mass))
            gamma = cp_mass / cv_mass
            r_gas = cp_mass - cv_mass
            chamber_temperature_k = float(gas.T)
            transport_gas = ct.Solution(TRANSPORT_MECHANISM_NAME)
            transport_species = set(transport_gas.species_names)
            mapped_mass_fractions = tuple(
                (name, float(fraction))
                for name, fraction in zip(gas.species_names, gas.Y)
                if float(fraction) > 1e-14 and name in transport_species
            )
            transport_coverage = sum(value for _, value in mapped_mass_fractions)
            if transport_coverage < MINIMUM_TRANSPORT_MASS_FRACTION_COVERAGE:
                missing_species = [
                    name
                    for name, fraction in zip(gas.species_names, gas.Y)
                    if float(fraction) > 1e-10 and name not in transport_species
                ]
                raise RuntimeError(
                    "Transport mechanism covers only {0:.8f} of equilibrium product mass; missing species: {1}."
                    .format(transport_coverage, ", ".join(missing_species) or "unknown")
                )
            normalized_transport_mass_fractions = tuple(
                (name, value / transport_coverage) for name, value in mapped_mass_fractions
            )
            cstar_efficiency_factor = _clamp(
                chamber_temperature_k / max(1.0, float(assumptions.chamber_temperature_k)),
                0.92,
                1.08,
            )
            return ThermochemistryResult(
                gamma=float(_clamp(gamma, 1.05, 1.40)),
                gas_constant_j_kgk=float(_clamp(r_gas, 180.0, 600.0)),
                chamber_temperature_k=float(_clamp(chamber_temperature_k, 1200.0, 4500.0)),
                cstar_efficiency_factor=float(cstar_efficiency_factor),
                provider_name=self.name,
                source="cantera-equilibrium-{0}:{1}".format(mechanism_path.name, phase_name),
                status="ok",
                transport_mass_fractions=normalized_transport_mass_fractions,
                transport_mechanism=TRANSPORT_MECHANISM_NAME,
                transport_mass_fraction_coverage=float(transport_coverage),
                note="Cantera equilibrium solution using bundled mechanism {0} (phase {1}).".format(
                    mechanism_path.name, phase_name
                ),
            )
        except Exception as exc:
            raise RuntimeError(
                "Cantera equilibrium solve failed: {0}: {1}".format(type(exc).__name__, str(exc)[:200])
            ) from exc


def resolve_thermochemistry_provider(mode: str) -> ThermochemistryProvider:
    m = (mode or "auto").strip().lower()
    if m == "fallback":
        raise RuntimeError("Fallback thermochemistry mode has been removed; Cantera is required.")

    _import_cantera()
    return CanteraThermochemistryProvider()

