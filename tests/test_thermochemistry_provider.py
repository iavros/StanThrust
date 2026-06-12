from pathlib import Path


def test_generated_equilibrium_mechanism_loads_and_estimates():
    try:
        import cantera as ct
    except Exception:
        return

    from liquid_engine_studio.concept_model import create_concept_design
    from liquid_engine_studio.propellants import lookup_propellant
    from liquid_engine_studio.solver_assumptions import get_default_solver_assumptions
    from liquid_engine_studio.thermochemistry_provider import (
        CanteraThermochemistryProvider,
        EQUILIBRIUM_MECHANISM_PATH,
    )

    assert Path(EQUILIBRIUM_MECHANISM_PATH).exists()

    gas = ct.Solution(str(EQUILIBRIUM_MECHANISM_PATH), "rocket_detailed")
    assert gas.n_species >= 20

    design = create_concept_design({"fuel_name": "Ethanol", "oxidizer_name": "Liquid Oxygen"})
    provider = CanteraThermochemistryProvider()
    thermo = provider.estimate(
        design,
        get_default_solver_assumptions(),
        lookup_propellant("Ethanol", "fuel"),
        lookup_propellant("Liquid Oxygen", "oxidizer"),
    )

    assert thermo.provider_name == "cantera"
    assert thermo.status == "ok"
    assert "rocket_mech_equilibrium.yaml" in thermo.source


def test_public_benchmark_propellant_pairs_are_supported():
    try:
        import cantera  # noqa: F401
    except Exception:
        return

    from liquid_engine_studio.benchmark_cases import get_public_benchmark_cases
    from liquid_engine_studio.concept_model import create_concept_design
    from liquid_engine_studio.propellants import lookup_propellant
    from liquid_engine_studio.solver_assumptions import get_default_solver_assumptions
    from liquid_engine_studio.thermochemistry_provider import CanteraThermochemistryProvider

    provider = CanteraThermochemistryProvider()
    assumptions = get_default_solver_assumptions()

    for case in get_public_benchmark_cases():
        design = create_concept_design(case.as_state())
        thermo = provider.estimate(
            design,
            assumptions,
            lookup_propellant(case.fuel_name, "fuel"),
            lookup_propellant(case.oxidizer_name, "oxidizer"),
        )
        assert thermo.provider_name == "cantera"
        assert thermo.status == "ok", f"{case.engine}: thermochemistry status {thermo.status}"
