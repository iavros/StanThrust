import site
import sys


def _append_user_site() -> None:
    try:
        user_site = site.getusersitepackages()
    except Exception:
        return
    if user_site and user_site not in sys.path:
        try:
            site.addsitedir(user_site)
        except Exception:
            sys.path.append(user_site)


_append_user_site()


def _self_test_cantera() -> int:
    from liquid_engine_studio.thermochemistry_provider import CanteraThermochemistryProvider, _import_cantera

    ct = _import_cantera()
    gas, mechanism_path, phase_name = CanteraThermochemistryProvider()._load_mechanism(ct)
    print(f"Cantera import: ok ({ct.__version__})")
    print(f"Mechanism load: ok ({mechanism_path.name}:{phase_name}, species={len(gas.species_names)})")
    return 0


def main() -> int:
    if "--self-test-cantera" in sys.argv:
        return _self_test_cantera()

    from liquid_engine_studio.qt_desktop import run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
