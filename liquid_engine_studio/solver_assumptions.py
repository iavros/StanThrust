from dataclasses import asdict, dataclass
from typing import Dict, List


@dataclass(frozen=True)
class SolverAssumptions:
    flow_model: str = "fast"
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


def assumptions_to_lines(assumptions: SolverAssumptions) -> List[str]:
    return [
        "- flow model: {0}".format(assumptions.flow_model),
        "- ambient pressure: {0} kPa".format(assumptions.ambient_pressure_kpa),
        "- gamma: {0}".format(assumptions.gamma),
        "- gas constant: {0} J/kg-K".format(assumptions.gas_constant_j_kgk),
        "- chamber temperature: {0} K".format(assumptions.chamber_temperature_k),
        "- combustion efficiency hook: {0}".format(assumptions.combustion_efficiency),
        "- nozzle efficiency hook: {0}".format(assumptions.nozzle_efficiency),
        "- nozzle Cd: {0}".format(assumptions.nozzle_discharge_coefficient),
        "- nozzle divergence half-angle: {0} deg".format(assumptions.nozzle_divergence_half_angle_deg),
        "- nozzle boundary-layer loss factor: {0}".format(assumptions.nozzle_boundary_layer_loss_factor),
        "- injector dP/Pc: {0}".format(assumptions.injector_pressure_drop_ratio),
        "- line loss coefficient: {0}".format(assumptions.line_loss_coefficient),
        "- ullage (pump mode): {0}".format(assumptions.tank_ullage_fraction_pump),
        "- ullage (blowdown mode): {0}".format(assumptions.tank_ullage_fraction_blowdown),
        "- convergence tolerance: {0}".format(assumptions.convergence_tolerance),
        "- max iterations: {0}".format(assumptions.max_iterations),
    ]

