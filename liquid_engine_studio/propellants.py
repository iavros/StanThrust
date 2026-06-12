from dataclasses import dataclass
from typing import Dict, List


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

_FUEL_LOOKUP: Dict[str, PropellantOption] = {option.name.lower(): option for option in FUEL_OPTIONS}
_OXIDIZER_LOOKUP: Dict[str, PropellantOption] = {
    option.name.lower(): option for option in OXIDIZER_OPTIONS
}


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
