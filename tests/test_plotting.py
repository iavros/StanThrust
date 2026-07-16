"""Rendering checks for the Matplotlib-backed desktop plots."""

import numpy as np
from PyQt5.QtWidgets import QApplication

from stanthrust.plotting import EngineeringPlotCanvas, FlowFieldPlotCanvas


def _application() -> QApplication:
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    return application


def test_engineering_plot_preserves_series_without_filled_artifacts():
    _application()
    canvas = EngineeringPlotCanvas()
    canvas.resize(900, 320)
    canvas.set_plot_data(
        x_label="Axial position (mm)",
        primary_label="Pressure (kPa)",
        primary_series=[
            {"label": "Pressure", "color": "#37A493", "points": [(0, 1200), (10, 1180), (20, 800), (30, 250)]},
        ],
        secondary_label="Velocity (m/s)",
        secondary_series=[
            {"label": "Velocity", "color": "#E0A94B", "points": [(0, 20), (10, 40), (20, 900), (30, 2100)]},
        ],
    )
    canvas.draw()

    assert canvas.axes is not None
    assert canvas.secondary_axes is not None
    assert len(canvas.axes.lines) == 1
    assert len(canvas.secondary_axes.lines) == 1
    assert not canvas.axes.collections
    assert not canvas.secondary_axes.collections
    assert list(canvas.axes.lines[0].get_ydata()) == [1200, 1180, 800, 250]
    assert list(canvas.secondary_axes.lines[0].get_ydata()) == [20, 40, 900, 2100]


def test_flow_field_uses_exact_station_range_and_marks_sonic_state():
    _application()
    canvas = FlowFieldPlotCanvas()
    canvas.resize(1200, 380)
    profile = [
        {"x_mm": 0.0, "radius_mm": 18.0, "mach": 0.08},
        {"x_mm": 12.0, "radius_mm": 16.0, "mach": 0.18},
        {"x_mm": 24.0, "radius_mm": 10.0, "mach": 0.62},
        {"x_mm": 30.0, "radius_mm": 5.0, "mach": 1.0},
        {"x_mm": 40.0, "radius_mm": 8.0, "mach": 1.8},
        {"x_mm": 52.0, "radius_mm": 12.0, "mach": 2.7},
    ]
    canvas.set_flow_data(profile, variable="mach", variable_label="Mach field")
    canvas.draw()

    assert canvas.axes is not None
    assert canvas.colorbar is not None
    field_mesh = canvas.axes.collections[0]
    field_values = np.asarray(field_mesh.get_array(), dtype=float)
    assert float(np.nanmin(field_values)) == 0.08
    assert float(np.nanmax(field_values)) == 2.7
    assert 1.0 in list(canvas.colorbar.get_ticks())
    assert canvas.axes.get_xlabel() == "Axial position (mm)"
    assert canvas.axes.get_ylabel() == "Radius (mm)"
