from dataclasses import dataclass


DEFAULT_OBJECTIVE_WEIGHTS = {
    "thrust": 0.25,
    "mass": 0.25,
    "packaging": 0.25,
    "thermal": 0.25,
}


@dataclass(frozen=True)
class DefaultState:
    fuel_name: str = "Ethanol"
    oxidizer_name: str = "Liquid Oxygen"
    mixture_ratio: float = 1.4
    injector_type: str = "impinging"
    target_thrust_newtons: float = 250.0
    target_impulse_newton_seconds: float = 3000.0
    target_diameter_mm: float = 110.0
    burn_time_seconds: float = 12.0
    tank_diameter_mm: float = 110.0
    chamber_diameter_mm: float = 68.0
    nozzle_diameter_mm: float = 95.0
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
