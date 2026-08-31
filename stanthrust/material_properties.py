"""Temperature-dependent material properties used by thermal and stress solvers."""

from bisect import bisect_right
from typing import Dict, List, Sequence, Tuple

MATERIAL_ALLOWABLE_STRESS_MPA = {
    "Aluminum 6061-T6": 95.0,
    "Aluminum 7075-T6": 145.0,
    "Stainless Steel 304": 120.0,
    "Stainless Steel 316": 125.0,
    "Carbon Steel 1018": 110.0,
    "Titanium Grade 5": 240.0,
    "Copper C110": 70.0,
    "Inconel 625": 230.0,
}

MATERIAL_TEMPERATURE_LIMIT_K = {
    "Aluminum 6061-T6": 425.0,
    "Aluminum 7075-T6": 410.0,
    "Stainless Steel 304": 1080.0,
    "Stainless Steel 316": 1110.0,
    "Carbon Steel 1018": 880.0,
    "Titanium Grade 5": 720.0,
    "Copper C110": 640.0,
    "Inconel 625": 1250.0,
}


def _curve(*rows: Tuple[float, float]) -> List[Tuple[float, float]]:
    return sorted((float(temperature), float(value)) for temperature, value in rows)


# Conductivity values are temperature tables. Stress values are conservative design
# allowables, not ultimate strengths. Manufacturer-backed tables are identified separately
# from the conservative screening curves retained for materials without a single public
# product-form data set.
MATERIAL_PROPERTY_TABLES: Dict[str, Dict[str, object]] = {
    "Aluminum 6061-T6": {
        "conductivity": _curve((293.15, 167.0), (373.15, 177.0), (425.0, 180.0)),
        "allowable": _curve((293.15, 95.0), (350.0, 88.0), (400.0, 65.0), (425.0, 48.0)),
        "source": "Aluminum Association and producer property envelopes",
        "source_url": "https://www.aluminum.org/aluminum-standards-data-2024",
        "provenance": "conservative-screening-curve",
        "uncertainty_fraction": 0.20,
    },
    "Aluminum 7075-T6": {
        "conductivity": _curve((293.15, 130.0), (350.0, 137.0), (410.0, 145.0)),
        "allowable": _curve((293.15, 145.0), (350.0, 128.0), (390.0, 88.0), (410.0, 58.0)),
        "source": "Aluminum Association and producer property envelopes",
        "source_url": "https://www.aluminum.org/aluminum-standards-data-2024",
        "provenance": "conservative-screening-curve",
        "uncertainty_fraction": 0.20,
    },
    "Stainless Steel 304": {
        "conductivity": _curve((293.15, 16.2), (773.15, 21.0), (1080.0, 24.2)),
        "allowable": _curve((293.15, 120.0), (573.15, 103.0), (773.15, 82.0), (973.15, 58.0), (1080.0, 42.0)),
        "source": "Outokumpu Therma 304H/4948 elevated-temperature data",
        "source_url": "https://www.outokumpu.com/en/products/product-ranges/therma",
        "provenance": "manufacturer-table-with-conservative-allowable",
        "uncertainty_fraction": 0.15,
    },
    "Stainless Steel 316": {
        "conductivity": _curve((293.15, 14.6), (573.15, 18.0), (773.15, 21.0), (1110.0, 24.5)),
        "allowable": _curve((293.15, 125.0), (573.15, 108.0), (773.15, 86.0), (973.15, 61.0), (1110.0, 43.0)),
        "source": "Outokumpu austenitic stainless elevated-temperature data",
        "source_url": "https://www.outokumpu.com/en/products/product-ranges/therma",
        "provenance": "manufacturer-table-with-conservative-allowable",
        "uncertainty_fraction": 0.15,
    },
    "Carbon Steel 1018": {
        "conductivity": _curve((293.15, 51.0), (473.15, 47.0), (673.15, 40.0), (880.0, 32.0)),
        "allowable": _curve((293.15, 110.0), (473.15, 96.0), (673.15, 72.0), (880.0, 38.0)),
        "source": "AISI low-carbon steel engineering property envelope",
        "source_url": "https://www.steel.org/steel-technology/steel-production/steel-grades/",
        "provenance": "conservative-screening-curve",
        "uncertainty_fraction": 0.25,
    },
    "Titanium Grade 5": {
        "conductivity": _curve((293.15, 6.7), (473.15, 8.7), (623.15, 11.0), (720.0, 12.5)),
        "allowable": _curve((293.15, 240.0), (473.15, 218.0), (623.15, 178.0), (720.0, 130.0)),
        "source": "TIMET TIMETAL 6-4 elevated-temperature property data",
        "source_url": "https://www.timet.com/documents/datasheets/alpha-and-beta-alloys/timetal-6-4.pdf",
        "provenance": "manufacturer-curve-with-conservative-allowable",
        "uncertainty_fraction": 0.15,
    },
    "Copper C110": {
        "conductivity": _curve((293.15, 391.0), (373.15, 384.0), (473.15, 374.0), (640.0, 355.0)),
        "allowable": _curve((293.15, 70.0), (373.15, 58.0), (473.15, 42.0), (640.0, 24.0)),
        "source": "Copper Development Association C11000 property data",
        "source_url": "https://alloys.copper.org/alloy/C11000",
        "provenance": "manufacturer-table-with-conservative-allowable",
        "uncertainty_fraction": 0.20,
    },
    "Inconel 625": {
        "conductivity": _curve(
            (294.15, 9.8), (366.15, 10.8), (477.15, 12.5), (589.15, 14.1),
            (700.15, 15.7), (811.15, 17.5), (922.15, 19.0), (1033.15, 20.8),
            (1144.15, 22.8), (1250.0, 24.8),
        ),
        "allowable": _curve((293.15, 230.0), (649.0, 230.0), (922.15, 205.0), (1033.15, 165.0), (1144.15, 92.0), (1250.0, 55.0)),
        "source": "Special Metals INCONEL alloy 625 technical bulletin",
        "source_url": "https://www.specialmetals.com/documents/technical-bulletins/inconel/inconel-alloy-625.pdf",
        "provenance": "manufacturer-table-with-conservative-allowable",
        "uncertainty_fraction": 0.12,
    },
}

def _interpolate(points: Sequence[Tuple[float, float]], temperature_k: float) -> Tuple[float, bool]:
    temperature = float(temperature_k)
    if temperature <= points[0][0]:
        return float(points[0][1]), temperature >= points[0][0]
    if temperature >= points[-1][0]:
        return float(points[-1][1]), temperature <= points[-1][0]
    index = bisect_right([point[0] for point in points], temperature)
    left_t, left_value = points[index - 1]
    right_t, right_value = points[index]
    fraction = (temperature - left_t) / (right_t - left_t)
    return left_value + fraction * (right_value - left_value), True


def material_property_state(material: str, temperature_k: float) -> Dict[str, object]:
    table = MATERIAL_PROPERTY_TABLES.get(material)
    if table is None:
        raise ValueError("No temperature-dependent property table is available for {0}.".format(material))
    conductivity, conductivity_in_range = _interpolate(table["conductivity"], temperature_k)
    allowable, allowable_in_range = _interpolate(table["allowable"], temperature_k)
    uncertainty = float(table["uncertainty_fraction"])
    return {
        "material": material,
        "temperature_k": float(temperature_k),
        "thermal_conductivity_w_m_k": conductivity,
        "allowable_stress_mpa": allowable,
        "allowable_stress_lower_mpa": allowable * (1.0 - uncertainty),
        "allowable_stress_upper_mpa": allowable * (1.0 + uncertainty),
        "conductivity_lower_w_m_k": conductivity * (1.0 - uncertainty),
        "conductivity_upper_w_m_k": conductivity * (1.0 + uncertainty),
        "temperature_limit_k": MATERIAL_TEMPERATURE_LIMIT_K[material],
        "in_property_range": conductivity_in_range and allowable_in_range,
        "source": table["source"],
        "source_url": table["source_url"],
        "provenance": table["provenance"],
        "uncertainty_fraction": uncertainty,
    }


def material_thermal_conductivity(material: str, temperature_k: float) -> float:
    return float(material_property_state(material, temperature_k)["thermal_conductivity_w_m_k"])

