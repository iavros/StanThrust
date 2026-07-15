from dataclasses import asdict, dataclass
from typing import Dict, List


DEFAULT_OBJECTIVE_WEIGHTS = {
    "thrust": 0.25,
    "mass": 0.25,
    "packaging": 0.25,
    "thermal": 0.25,
}

MATERIAL_OPTIONS = [
    "Aluminum 6061-T6",
    "Aluminum 7075-T6",
    "Stainless Steel 304",
    "Stainless Steel 316",
    "Carbon Steel 1018",
    "Titanium Grade 5",
    "Copper C110",
    "Inconel 625",
]


@dataclass(frozen=True)
class DefaultState:
    fuel_name: str = "Ethanol"
    oxidizer_name: str = "Liquid Oxygen"
    mixture_ratio: float = 1.4
    injector_type: str = "impinging"
    target_thrust_newtons: float = 250.0
    target_chamber_pressure_kpa: float = 0.0
    target_impulse_newton_seconds: float = 3000.0
    target_diameter_mm: float = 110.0
    burn_time_seconds: float = 12.0
    tank_diameter_mm: float = 110.0
    chamber_diameter_mm: float = 68.0
    nozzle_diameter_mm: float = 95.0
    nozzle_exit_mode: str = "auto"
    nozzle_expansion_bias: str = "pressure_matched"
    fuel_tank_material: str = "Aluminum 6061-T6"
    oxidizer_tank_material: str = "Aluminum 6061-T6"
    feed_system_material: str = "Stainless Steel 304"
    chamber_material: str = "Stainless Steel 304"
    nozzle_material: str = "Stainless Steel 304"
    factor_of_safety: float = 2.0
    packaging_bias: str = "balanced"
    use_pumps: bool = True
    regen_cooling: bool = False
    film_cooling: bool = False


DEFAULT_STATE = DefaultState()


@dataclass(frozen=True)
class PropellantOption:
    name: str
    role: str
    density_index: float
    cooling_affinity: float
    thermal_severity: float
    handling_complexity: float
    visualization_weight: float


FUEL_OPTIONS: List[PropellantOption] = [
    PropellantOption("Methane", "fuel", 0.42, 0.52, 0.38, 0.22, 0.9),
    PropellantOption("Ethanol", "fuel", 0.72, 0.58, 0.42, 0.26, 0.94),
    PropellantOption("Isopropyl Alcohol", "fuel", 0.79, 0.54, 0.45, 0.28, 0.92),
]

OXIDIZER_OPTIONS: List[PropellantOption] = [
    PropellantOption("Liquid Oxygen", "oxidizer", 0.94, 0.18, 0.82, 0.46, 1.0),
    PropellantOption("Nitrous Oxide", "oxidizer", 0.82, 0.2, 0.68, 0.38, 0.92),
    PropellantOption("Hydrogen Peroxide", "oxidizer", 0.88, 0.24, 0.6, 0.42, 0.9),
]

FUEL_NAMES = [option.name for option in FUEL_OPTIONS]
OXIDIZER_NAMES = [option.name for option in OXIDIZER_OPTIONS]

_FUEL_LOOKUP = {option.name.lower(): option for option in FUEL_OPTIONS}
_OXIDIZER_LOOKUP = {option.name.lower(): option for option in OXIDIZER_OPTIONS}


def lookup_propellant(name: str, role: str) -> PropellantOption:
    key = (name or "").strip().lower()
    table = _FUEL_LOOKUP if role == "fuel" else _OXIDIZER_LOOKUP
    if key in table:
        return table[key]

    if role == "fuel":
        return PropellantOption(
            name=(name or "Custom Fuel").strip() or "Custom Fuel",
            role="fuel",
            density_index=0.68,
            cooling_affinity=0.46,
            thermal_severity=0.48,
            handling_complexity=0.32,
            visualization_weight=0.9,
        )

    return PropellantOption(
        name=(name or "Custom Oxidizer").strip() or "Custom Oxidizer",
        role="oxidizer",
        density_index=0.86,
        cooling_affinity=0.18,
        thermal_severity=0.7,
        handling_complexity=0.42,
        visualization_weight=0.94,
    )


@dataclass(frozen=True)
class SolverAssumptions:
    flow_model: str = "navier_stokes"
    gravity_m_s2: float = 9.80665
    ambient_pressure_kpa: float = 101.3
    gamma: float = 1.22
    gas_constant_j_kgk: float = 355.0
    chamber_temperature_k: float = 3350.0
    combustion_efficiency: float = 1.0
    nozzle_efficiency: float = 1.0
    nozzle_discharge_coefficient: float = 0.96
    nozzle_divergence_half_angle_deg: float = 14.0
    nozzle_boundary_layer_loss_factor: float = 0.022
    injector_pressure_drop_ratio: float = 0.16
    line_loss_coefficient: float = 0.08
    tank_ullage_fraction_pump: float = 0.08
    tank_ullage_fraction_blowdown: float = 0.16
    convergence_tolerance: float = 0.005
    max_iterations: int = 40

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


def get_default_solver_assumptions() -> SolverAssumptions:
    return SolverAssumptions()

