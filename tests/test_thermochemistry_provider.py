from pathlib import Path

import pytest


def test_generated_equilibrium_mechanism_loads_and_solves():
    import cantera as ct

    from stanshock.design_model import create_engine_design
    from stanshock.propellants import lookup_propellant
    from stanshock.solver_assumptions import get_default_solver_assumptions
    from stanshock.thermochemistry_provider import (
        CanteraThermochemistryProvider,
        EQUILIBRIUM_MECHANISM_PATH,
    )

    assert Path(EQUILIBRIUM_MECHANISM_PATH).exists()

    gas = ct.Solution(str(EQUILIBRIUM_MECHANISM_PATH), "rocket_detailed")
    assert gas.n_species >= 20

    design = create_engine_design({"fuel_name": "Ethanol", "oxidizer_name": "Liquid Oxygen"})
    provider = CanteraThermochemistryProvider()
    thermo = provider.solve(
        design,
        get_default_solver_assumptions(),
        lookup_propellant("Ethanol", "fuel"),
        lookup_propellant("Liquid Oxygen", "oxidizer"),
    )

    assert thermo.provider_name == "cantera"
    assert thermo.status == "ok"
    assert "rocket_mech_equilibrium.yaml" in thermo.source


def test_public_benchmark_propellant_pairs_are_supported():
    import cantera  # noqa: F401

    from stanshock.benchmark_cases import get_public_benchmark_cases
    from stanshock.design_model import create_engine_design
    from stanshock.propellants import lookup_propellant
    from stanshock.solver_assumptions import get_default_solver_assumptions
    from stanshock.thermochemistry_provider import CanteraThermochemistryProvider

    provider = CanteraThermochemistryProvider()
    assumptions = get_default_solver_assumptions()

    for case in get_public_benchmark_cases():
        design = create_engine_design(case.as_state())
        thermo = provider.solve(
            design,
            assumptions,
            lookup_propellant(case.fuel_name, "fuel"),
            lookup_propellant(case.oxidizer_name, "oxidizer"),
        )
        assert thermo.provider_name == "cantera"
        assert thermo.status == "ok", f"{case.engine}: thermochemistry status {thermo.status}"


def test_thermochemistry_provider_requires_cantera_mode():
    from stanshock.thermochemistry_provider import (
        CanteraThermochemistryProvider,
        resolve_thermochemistry_provider,
    )

    assert isinstance(resolve_thermochemistry_provider("auto"), CanteraThermochemistryProvider)
    with pytest.raises(RuntimeError, match="Cantera is required"):
        resolve_thermochemistry_provider("fallback")
