"""Design defaults, selectable input catalogues, and solver assumptions."""

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
    target_chamber_pressure_kpa: float = 1500.0
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
    regen_coolant_inlet_temperature_k: float = 0.0
    regen_coolant_inlet_pressure_kpa: float = 0.0
    pressure_solve_mode: str = "design"
    combustion_efficiency: float = 0.95
    design_injector_dp_ratio: float = 0.20
    fuel_injector_discharge_coefficient: float = 0.72
    oxidizer_injector_discharge_coefficient: float = 0.72
    fuel_injector_area_mm2: float = 0.0
    oxidizer_injector_area_mm2: float = 0.0
    fuel_supply_pressure_kpa: float = 0.0
    oxidizer_supply_pressure_kpa: float = 0.0
    fuel_tank_inlet_pressure_kpa: float = 300.0
    oxidizer_tank_inlet_pressure_kpa: float = 330.0
    design_supply_margin_ratio: float = 0.08
    analysis_throat_diameter_mm: float = 0.0
    line_diameter_fuel_m: float = 0.012
    line_diameter_oxidizer_m: float = 0.0114
    line_length_fuel_m: float = 1.55
    line_length_oxidizer_m: float = 1.75
    minor_loss_fuel_k: float = 8.0
    minor_loss_oxidizer_k: float = 9.2
    line_roughness_fuel_m: float = 1.5e-6
    line_roughness_oxidizer_m: float = 1.5e-6
    uncertainty_sample_count: int = 128


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
    supported = ", ".join(option.name for option in table.values())
    raise ValueError("Unsupported {0} '{1}'. Supported options: {2}.".format(role, name, supported))


@dataclass(frozen=True)
class SolverAssumptions:
    flow_model: str = "viscous"
    gravity_m_s2: float = 9.80665
    ambient_pressure_kpa: float = 101.3
    gamma: float = 1.22
    gas_constant_j_kgk: float = 355.0
    chamber_temperature_k: float = 3350.0
    combustion_efficiency: float = 0.95
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
