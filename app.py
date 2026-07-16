import site
import os
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


if not getattr(sys, "frozen", False):
    _append_user_site()


def _self_test_cantera() -> int:
    from stanthrust.thermochemistry_provider import CanteraThermochemistryProvider, _import_cantera

    ct = _import_cantera()
    gas, mechanism_path, phase_name = CanteraThermochemistryProvider()._load_mechanism(ct)
    print(f"Cantera import: ok ({ct.__version__})")
    print(f"Mechanism load: ok ({mechanism_path.name}:{phase_name}, species={len(gas.species_names)})")
    return 0


def _self_test_desktop() -> int:
    trace_path = os.environ.get("STANTHRUST_SELF_TEST_LOG", "")

    def trace(message: str) -> None:
        if not trace_path:
            return
        with open(trace_path, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    trace("start")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy
    trace(f"numpy-imported-{numpy.__version__}")
    import matplotlib
    trace(f"matplotlib-core-imported-{matplotlib.__version__}")
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
    trace(f"qt5agg-imported-{FigureCanvasQTAgg.__name__}")
    from PyQt5.QtWidgets import QApplication
    trace("pyqt-imported")
    from stanthrust.plotting import EngineeringPlotCanvas, FlowFieldPlotCanvas
    trace("matplotlib-imported")

    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    trace("application-created")
    line_plot = EngineeringPlotCanvas()
    trace("line-canvas-created")
    line_plot.set_plot_data(
        x_label="Station",
        primary_label="Primary",
        primary_series=[{"label": "Primary", "color": "#37A493", "points": [(0.0, 1.0), (1.0, 2.0)]}],
    )
    line_plot.draw()
    trace("line-rendered")
    flow_field = FlowFieldPlotCanvas()
    trace("field-canvas-created")
    flow_field.set_flow_data(
        [
            {"x_mm": 0.0, "radius_mm": 10.0, "mach": 0.2},
            {"x_mm": 10.0, "radius_mm": 5.0, "mach": 1.0},
            {"x_mm": 20.0, "radius_mm": 8.0, "mach": 2.0},
        ],
        variable="mach",
        variable_label="Mach field",
    )
    flow_field.draw()
    trace("field-rendered")
    print("Desktop plotting: ok (Matplotlib QtAgg)")
    sys.stdout.flush()
    line_plot.close()
    flow_field.close()
    application.processEvents()
    application.quit()
    trace("shutdown-complete")
    return 0


def main() -> int:
    if "--self-test-cantera" in sys.argv:
        return _self_test_cantera()
    if "--self-test-desktop" in sys.argv:
        return _self_test_desktop()

    from stanthrust.qt_desktop import run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
