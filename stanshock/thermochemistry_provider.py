from dataclasses import dataclass
import importlib
from pathlib import Path
import site
import sys
from typing import Optional, Tuple


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


DATA_DIR = Path(__file__).resolve().parent / "data"
EQUILIBRIUM_MECHANISM_PATH = DATA_DIR / "rocket_mech_equilibrium.yaml"


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
            raise RuntimeError(
                "Cantera is required for StanThrust thermochemistry. "
                "Install project dependencies with 'python -m pip install -r requirements.txt'."
            ) from second_error


@dataclass(frozen=True)
class ThermochemistryResult:
    gamma: float
    gas_constant_j_kgk: float
    chamber_temperature_k: float
    cstar_efficiency_factor: float
    provider_name: str
    source: str
    status: str
    note: str = ""


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

