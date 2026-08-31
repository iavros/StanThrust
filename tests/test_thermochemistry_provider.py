"""Tests for Cantera equilibrium and frozen-composition transport coverage."""

from pathlib import Path

import pytest


def test_generated_equilibrium_mechanism_loads_and_solves():
    import cantera as ct

    from stanthrust.design_model import create_engine_design
    from stanthrust.inputs import get_default_solver_assumptions, lookup_propellant
    from stanthrust.thermochemistry_provider import (
        EQUILIBRIUM_MECHANISM_PATH,
        MINIMUM_TRANSPORT_MASS_FRACTION_COVERAGE,
        TRANSPORT_MECHANISM_NAME,
        CanteraThermochemistryProvider,
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
    assert thermo.transport_mechanism == TRANSPORT_MECHANISM_NAME
    assert thermo.transport_mass_fraction_coverage >= MINIMUM_TRANSPORT_MASS_FRACTION_COVERAGE
    assert sum(value for _, value in thermo.transport_mass_fractions) == pytest.approx(1.0)


def test_every_supported_propellant_pair_has_complete_transport_coverage():
    from stanthrust.design_model import create_engine_design
    from stanthrust.inputs import (
        FUEL_NAMES,
        OXIDIZER_NAMES,
        get_default_solver_assumptions,
        lookup_propellant,
    )
    from stanthrust.thermochemistry_provider import (
        MINIMUM_TRANSPORT_MASS_FRACTION_COVERAGE,
        CanteraThermochemistryProvider,
    )

    provider = CanteraThermochemistryProvider()
    assumptions = get_default_solver_assumptions()
    for fuel_name in FUEL_NAMES:
        for oxidizer_name in OXIDIZER_NAMES:
            design = create_engine_design(
                {"fuel_name": fuel_name, "oxidizer_name": oxidizer_name}
            )
            result = provider.solve(
                design,
                assumptions,
                lookup_propellant(fuel_name, "fuel"),
                lookup_propellant(oxidizer_name, "oxidizer"),
            )
            assert result.transport_mass_fraction_coverage >= (
                MINIMUM_TRANSPORT_MASS_FRACTION_COVERAGE
            ), f"{fuel_name} / {oxidizer_name}"
            assert sum(value for _, value in result.transport_mass_fractions) == pytest.approx(1.0)


def test_frozen_transport_is_evaluated_at_each_station_state():
    from stanthrust.design_model import create_engine_design
    from stanthrust.inputs import get_default_solver_assumptions, lookup_propellant
    from stanthrust.thermochemistry_provider import (
        TRANSPORT_MECHANISM_NAME,
        CanteraThermochemistryProvider,
        apply_frozen_transport_to_profile,
    )

    design = create_engine_design({})
    result = CanteraThermochemistryProvider().solve(
        design,
        get_default_solver_assumptions(),
        lookup_propellant(design.inputs.fuel_name, "fuel"),
        lookup_propellant(design.inputs.oxidizer_name, "oxidizer"),
    )
    profile = apply_frozen_transport_to_profile(
        [
            {"temperature_k": 3000.0, "pressure_kpa": 1000.0},
            {"temperature_k": 1800.0, "pressure_kpa": 100.0},
        ],
        result.transport_mass_fractions,
    )

    assert len(profile) == 2
    for row in profile:
        assert float(row["gas_viscosity_pa_s"]) > 0.0
        assert float(row["gas_conductivity_w_m_k"]) > 0.0
        assert float(row["gas_cp_j_kg_k"]) > 0.0
        assert float(row["gas_prandtl"]) > 0.0
        assert row["gas_transport_source"] == (
            f"cantera-frozen-composition:{TRANSPORT_MECHANISM_NAME}"
        )
    assert profile[0]["gas_viscosity_pa_s"] != profile[1]["gas_viscosity_pa_s"]
    assert profile[0]["gas_conductivity_w_m_k"] != profile[1]["gas_conductivity_w_m_k"]


def test_frozen_transport_rejects_an_empty_composition():
    from stanthrust.thermochemistry_provider import apply_frozen_transport_to_profile

    with pytest.raises(RuntimeError, match="composition is empty"):
        apply_frozen_transport_to_profile([], [])


def test_public_benchmark_propellant_pairs_are_supported():
    import cantera  # noqa: F401

    from stanthrust.benchmark_cases import get_public_benchmark_cases
    from stanthrust.design_model import create_engine_design
    from stanthrust.inputs import get_default_solver_assumptions, lookup_propellant
    from stanthrust.thermochemistry_provider import CanteraThermochemistryProvider

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
    from stanthrust.thermochemistry_provider import (
        CanteraThermochemistryProvider,
        resolve_thermochemistry_provider,
    )

    assert isinstance(resolve_thermochemistry_provider("auto"), CanteraThermochemistryProvider)
    with pytest.raises(RuntimeError, match="Cantera is required"):
        resolve_thermochemistry_provider("fallback")
