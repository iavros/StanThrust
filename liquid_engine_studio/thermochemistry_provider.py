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
DETAILED_MECHANISM_PATH = DATA_DIR / "rocket_mech.yaml"
MINIMAL_MECHANISM_PATH = DATA_DIR / "rocket_mech_minimal.yaml"
MECHANISM_SEARCH_ORDER = (
    EQUILIBRIUM_MECHANISM_PATH,
    DETAILED_MECHANISM_PATH,
    MINIMAL_MECHANISM_PATH,
)


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
class ThermochemistryEstimate:
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

    def estimate(self, design, assumptions, fuel, oxidizer) -> ThermochemistryEstimate:
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
        for mechanism_path in MECHANISM_SEARCH_ORDER:
            if not mechanism_path.exists():
                continue
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

    def estimate(self, design, assumptions, fuel, oxidizer) -> ThermochemistryEstimate:
        ct = _import_cantera()

        fuel_key = fuel.name.strip().lower()
        oxidizer_key = oxidizer.name.strip().lower()
        fuel_species = self._fuel_species.get(fuel_key)
        oxidizer_species = self._oxidizer_species.get(oxidizer_key)
        phi = self._build_phi(design, fuel_key, oxidizer_key)
        # If mappings are missing or we could not build a phi, return an approximate
        # estimate but include a helpful diagnostic note for the UI to display.
        if fuel_species is None or oxidizer_species is None or phi is None:
            missing = []
            if fuel_species is None:
                missing.append(f"fuel mapping '{fuel_key}'")
            if oxidizer_species is None:
                missing.append(f"oxidizer mapping '{oxidizer_key}'")
            if phi is None:
                missing.append("stoichiometry/phi")
            reason = "; ".join(missing) if missing else "unknown"
            return self._approximate_estimate(design, assumptions, fuel_key, oxidizer_key, phi or 1.0, reason=reason)

        try:
            gas, mechanism_path, phase_name = self._load_mechanism(ct)
        except Exception as exc:
            raise RuntimeError("Cantera mechanism load failed: {0}".format(str(exc))) from exc

        # If the requested species aren't in the bundled mechanism, return a
        # approximate estimate instead of failing the app.
        missing_species = []
        if fuel_species not in gas.species_names:
            missing_species.append(fuel_species)
        if oxidizer_species not in gas.species_names:
            missing_species.append(oxidizer_species)
        if missing_species:
            reason = f"missing species in mechanism: {', '.join(missing_species)}"
            return self._approximate_estimate(design, assumptions, fuel_key, oxidizer_key, phi, reason=reason)

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
            return ThermochemistryEstimate(
                gamma=float(_clamp(gamma, 1.05, 1.40)),
                gas_constant_j_kgk=float(_clamp(r_gas, 180.0, 600.0)),
                chamber_temperature_k=float(_clamp(chamber_temperature_k, 1200.0, 4500.0)),
                cstar_efficiency_factor=float(cstar_efficiency_factor),
                provider_name=self.name,
                source="cantera-equilibrium-{0}:{1}".format(mechanism_path.name, phase_name),
                status="ok",
                note="Cantera equilibrium estimate using bundled mechanism {0} (phase {1}).".format(
                    mechanism_path.name, phase_name
                ),
            )
        except Exception as exc:
            # Provide diagnostic information when equilibrium fails so the UI can
            # distinguish an approximate estimate from a full equilibrium result.
            reason = f"equilibrium failure: {type(exc).__name__}: {str(exc)[:200]}"
            return self._approximate_estimate(design, assumptions, fuel_key, oxidizer_key, phi, reason=reason)

    def _approximate_estimate(self, design, assumptions, fuel_key: str, oxidizer_key: str, phi: float, reason: Optional[str] = None) -> ThermochemistryEstimate:
        """Return a simple approximate thermochemistry estimate when a full
        mechanism is not available for the requested species.

        This intentionally provides conservative, plausible values for early
        design work. It is not a substitute for a validated chemical mechanism.
        """
        # Default mapping based on the bundled mechanism species; tuned to be
        # plausible for preliminary design work.
        approx_table = {
            "methane": {"gamma": 1.23, "r": 300.0, "t_chamber": 3450.0},
            "ethanol": {"gamma": 1.20, "r": 300.0, "t_chamber": 3300.0},
            "isopropyl alcohol": {"gamma": 1.18, "r": 288.0, "t_chamber": 3380.0},
            "isopropanol": {"gamma": 1.18, "r": 288.0, "t_chamber": 3380.0},
        }

        defaults = approx_table.get(fuel_key, {"gamma": 1.24, "r": 300.0, "t_chamber": 3200.0})
        gamma = _clamp(defaults["gamma"] * (1.0 + (0.02 * (1.0 - phi))), 1.05, 1.40)
        r_gas = _clamp(defaults["r"], 180.0, 600.0)
        chamber_temperature_k = _clamp(defaults["t_chamber"] * (1.0 + 0.02 * (phi - 1.0)), 1200.0, 4500.0)
        cstar_efficiency_factor = _clamp(chamber_temperature_k / max(1.0, float(assumptions.chamber_temperature_k)), 0.9, 1.06)

        note = (
            "Requested species are not present in the bundled mechanism; "
            "returning an approximate estimate. For rigorous "
            "thermochemistry, use a fuel/oxidizer pair that is present in the "
            "bundled mechanism."
        )
        if reason:
            note = note + " Diagnostic: " + str(reason)

        return ThermochemistryEstimate(
            gamma=float(gamma),
            gas_constant_j_kgk=float(r_gas),
            chamber_temperature_k=float(chamber_temperature_k),
            cstar_efficiency_factor=float(cstar_efficiency_factor),
            provider_name=self.name,
            source="cantera-approximate-lookup",
            status="approximate",
            note=note,
        )


def resolve_thermochemistry_provider(mode: str) -> ThermochemistryProvider:
    m = (mode or "auto").strip().lower()
    if m == "fallback":
        raise RuntimeError("Fallback thermochemistry mode has been removed; Cantera is required.")

    _import_cantera()
    return CanteraThermochemistryProvider()

