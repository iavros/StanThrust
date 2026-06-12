"""Diagnostic helper for the active thermochemistry provider and mechanism files."""

import pprint
from pathlib import Path


print("Workspace:", Path(__file__).resolve().parent.parent)

try:
    from liquid_engine_studio.thermochemistry_provider import (
        CanteraThermochemistryProvider,
        DEFAULT_MECHANISM_PATH,
    )
    from liquid_engine_studio.propellants import FUEL_NAMES, OXIDIZER_NAMES
except Exception as exc:
    print("Error importing modules:", type(exc).__name__, str(exc))
    raise

print("\nProvider fuel mapping:")
for key, value in CanteraThermochemistryProvider._fuel_species.items():
    print(f"  '{key}' -> '{value}'")

print("\nProvider oxidizer mapping:")
for key, value in CanteraThermochemistryProvider._oxidizer_species.items():
    print(f"  '{key}' -> '{value}'")

print("\nAvailable UI fuel options:", FUEL_NAMES)
print("Available UI oxidizer options:", OXIDIZER_NAMES)

print("\nDEFAULT_MECHANISM_PATH:", DEFAULT_MECHANISM_PATH)

try:
    import cantera as ct

    print("\nCantera version:", ct.__version__)
    mech_path = str(DEFAULT_MECHANISM_PATH)
    print("Attempting to load mechanism file:", mech_path)
    gas = None
    for phase_name in ("rocket_detailed", "rocket"):
        try:
            gas = ct.Solution(mech_path, phase_name)
            print("Loaded phase", phase_name)
            break
        except Exception as exc:
            print("Could not load phase", phase_name, "->", type(exc).__name__, str(exc)[:200])
    if gas is None:
        try:
            gas = ct.Solution(mech_path)
            print("Loaded default solution")
        except Exception as exc:
            print("Could not load default solution ->", type(exc).__name__, str(exc)[:200])
            gas = None
    if gas is not None:
        print("Mechanism species count:", len(gas.species_names))
        print("Sample species:", gas.species_names[:20])
        missing = []
        for key, value in CanteraThermochemistryProvider._fuel_species.items():
            if value not in gas.species_names:
                missing.append((key, value))
        for key, value in CanteraThermochemistryProvider._oxidizer_species.items():
            if value not in gas.species_names:
                missing.append((key, value))
        if missing:
            print("\nMappings missing in mechanism:")
            for key, value in missing:
                print("  mapping", key, "->", value, "NOT FOUND")
        else:
            print("\nAll provider-mapped species are present in mechanism")
except Exception as exc:
    print("\nCantera not available or failed to load mechanism:", type(exc).__name__, exc)

try:
    provider = CanteraThermochemistryProvider()

    class Dummy:
        pass

    design = Dummy()

    class Inputs:
        mixture_ratio = 6.0

    design.inputs = Inputs()
    design.derived = type("D", (), {"engineering_values": {"chamber_pressure_kpa": 1000.0}})()
    assumptions = type("A", (), {"chamber_temperature_k": 3200.0})()

    fuel = type("F", (), {"name": "Ethanol"})()
    oxidizer = type("O", (), {"name": "Liquid Oxygen"})()
    print("\nRunning sample estimate for Ethanol / Liquid Oxygen ...")
    pprint.pprint(provider.estimate(design, assumptions, fuel, oxidizer))

    fuel2 = type("F", (), {"name": "Methane"})()
    print("\nRunning sample estimate for Methane / Liquid Oxygen ...")
    pprint.pprint(provider.estimate(design, assumptions, fuel2, oxidizer))

except Exception as exc:
    print("Sample estimate failed:", type(exc).__name__, str(exc))

print("\nDiagnostic finished")
