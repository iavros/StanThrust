"""Matplotlib-backed engineering plots embedded in the desktop interface."""

from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Tuple

os.environ.setdefault("QT_API", "PyQt5")

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.colors import LinearSegmentedColormap, Normalize, TwoSlopeNorm
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator
from PyQt5.QtWidgets import QSizePolicy

from stanthrust.theme import PLOT_COLORS

MACH_CMAP = LinearSegmentedColormap.from_list(
    "stanthrust_mach",
    [
        (0.00, "#173B68"),
        (0.36, "#246B87"),
        (0.50, "#2B9A83"),
        (0.66, "#9CBC65"),
        (0.82, "#E0A94B"),
        (1.00, "#E76F51"),
    ],
)


def _finite_float(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_tick(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1000.0:
        return f"{value:.0f}"
    if magnitude >= 100.0:
        return f"{value:.1f}"
    if magnitude >= 10.0:
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _normalize_series(raw_series: List[Dict[str, object]]) -> List[Dict[str, object]]:
    normalized: List[Dict[str, object]] = []
    for series in raw_series:
        points = []
        for point in list(series.get("points", [])):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                continue
            x_value = _finite_float(point[0])
            y_value = _finite_float(point[1])
            if x_value is not None and y_value is not None:
                points.append((x_value, y_value))
        points.sort(key=lambda item: item[0])
        if points:
            normalized.append(
                {
                    "label": str(series.get("label", "Series")),
                    "color": str(series.get("color", PLOT_COLORS["text"])),
                    "points": points,
                }
            )
    return normalized


#: Margins reserved around the axes, in pixels. Expressing them in pixels
#: rather than as a fraction of the figure keeps the axis labels and the legend
#: legible whether a canvas is 260 px tall in a grid or 760 px tall on its own.
MARGINS_PX = {
    "left": 68,
    "right": 20,
    "right_secondary": 68,
    "bottom": 46,
    "top": 16,
    "legend_row": 17,
}

#: Legend entries per row before it wraps.
LEGEND_COLUMNS = 4

#: Rows reserved above the flow field for its inset colour bar and label.
COLORBAR_ROWS = 3


def legend_rows(entry_count: int) -> int:
    """Return how many rows a legend of ``entry_count`` entries needs."""
    if entry_count <= 0:
        return 0
    return -(-entry_count // LEGEND_COLUMNS)


def _apply_margins(
    figure, *, secondary: bool = False, rows: int = 0
) -> Tuple[float, float, float]:
    """Reserve a fixed pixel border around the axes of ``figure``.

    ``rows`` is the legend row count; the space above the axes grows with it so
    that a wrapped legend is never clipped. Returns the left, right, and top
    fractions that were applied, for anchoring overlays in figure coordinates.
    """
    width_px = max(1.0, figure.get_size_inches()[0] * figure.dpi)
    height_px = max(1.0, figure.get_size_inches()[1] * figure.dpi)
    right_px = MARGINS_PX["right_secondary"] if secondary else MARGINS_PX["right"]
    top_px = MARGINS_PX["top"] + rows * MARGINS_PX["legend_row"]
    left = min(0.45, MARGINS_PX["left"] / width_px)
    right = max(0.55, 1.0 - right_px / width_px)
    top = max(0.55, 1.0 - top_px / height_px)
    figure.subplots_adjust(
        left=left,
        right=right,
        bottom=min(0.45, MARGINS_PX["bottom"] / height_px),
        top=top,
    )
    return left, right, top


def _style_axis(axis) -> None:
    axis.set_facecolor(PLOT_COLORS["axes"])
    axis.tick_params(axis="both", colors=PLOT_COLORS["muted"], labelsize=8, length=3)
    axis.xaxis.label.set_color(PLOT_COLORS["muted"])
    axis.yaxis.label.set_color(PLOT_COLORS["text"])
    axis.xaxis.label.set_size(8)
    axis.yaxis.label.set_size(8)
    for spine in axis.spines.values():
        spine.set_color(PLOT_COLORS["border"])
        spine.set_linewidth(0.8)
    axis.grid(True, color=PLOT_COLORS["grid"], linewidth=0.7, alpha=0.85)
    axis.set_axisbelow(True)
    axis.xaxis.set_major_locator(MaxNLocator(nbins=5, min_n_ticks=3))
    axis.yaxis.set_major_locator(MaxNLocator(nbins=5, min_n_ticks=3))


class EngineeringPlotCanvas(FigureCanvasQTAgg):
    """Stable dual-axis line plot embedded directly in Qt."""

    def __init__(self) -> None:
        self.figure = Figure(figsize=(7.0, 3.0), dpi=100, facecolor=PLOT_COLORS["background"])
        super().__init__(self.figure)
        self.setMinimumHeight(270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(f"background: {PLOT_COLORS['background']};")
        self.axes = None
        self.secondary_axes = None
        self._legend_rows = 0
        self._last_request: Optional[Dict[str, object]] = None
        self._empty_message = "Run Solve to populate this plot."
        self._draw_empty(self._empty_message)

    def set_plot_data(
        self,
        *,
        x_label: str,
        primary_label: str,
        primary_series: List[Dict[str, object]],
        secondary_label: str = "",
        secondary_series: Optional[List[Dict[str, object]]] = None,
        empty_message: str = "Run Solve to populate this plot.",
    ) -> None:
        self._last_request = {
            "x_label": x_label,
            "primary_label": primary_label,
            "primary_series": primary_series,
            "secondary_label": secondary_label,
            "secondary_series": secondary_series,
            "empty_message": empty_message,
        }
        primary = _normalize_series(primary_series)
        secondary = _normalize_series(secondary_series or [])
        self._empty_message = empty_message
        if not primary and not secondary:
            self._draw_empty(empty_message)
            return

        self.figure.clear()
        self._legend_rows = legend_rows(len(primary) + len(secondary))
        legend_left, _legend_right, legend_bottom = _apply_margins(
            self.figure, secondary=bool(secondary), rows=self._legend_rows
        )
        axis = self.figure.add_subplot(111)
        self.axes = axis
        self.secondary_axes = axis.twinx() if secondary else None
        _style_axis(axis)
        axis.set_xlabel(x_label, labelpad=7)
        axis.set_ylabel(primary_label, labelpad=8)

        handles = []
        labels = []
        for series in primary:
            points = list(series["points"])
            line, = axis.plot(
                [point[0] for point in points],
                [point[1] for point in points],
                color=str(series["color"]),
                linewidth=1.9,
                solid_capstyle="round",
                solid_joinstyle="round",
                antialiased=True,
                zorder=3,
            )
            handles.append(line)
            labels.append(str(series["label"]))

        if self.secondary_axes is not None:
            secondary_axis = self.secondary_axes
            _style_axis(secondary_axis)
            secondary_axis.grid(False)
            secondary_axis.set_ylabel(secondary_label, labelpad=9)
            secondary_axis.spines["left"].set_visible(False)
            secondary_axis.spines["top"].set_visible(False)
            for series in secondary:
                points = list(series["points"])
                line, = secondary_axis.plot(
                    [point[0] for point in points],
                    [point[1] for point in points],
                    color=str(series["color"]),
                    linewidth=1.8,
                    linestyle=(0, (5, 2.5)),
                    dash_capstyle="round",
                    dash_joinstyle="round",
                    antialiased=True,
                    zorder=4,
                )
                handles.append(line)
                labels.append(str(series["label"]))

        all_points = [point for series in primary + secondary for point in list(series["points"])]
        x_values = [point[0] for point in all_points]
        if x_values:
            x_min = min(x_values)
            x_max = max(x_values)
            padding = max(1e-9, (x_max - x_min) * 0.005)
            axis.set_xlim(x_min - padding, x_max + padding)
        axis.margins(y=0.12)
        if self.secondary_axes is not None:
            self.secondary_axes.margins(y=0.12)

        columns = min(LEGEND_COLUMNS, max(1, len(handles)))
        legend = axis.legend(
            handles,
            labels,
            loc="lower left",
            bbox_to_anchor=(legend_left, legend_bottom + 0.006),
            bbox_transform=self.figure.transFigure,
            ncol=columns,
            frameon=False,
            fontsize=8,
            handlelength=2.0,
            columnspacing=1.5,
            borderaxespad=0.0,
        )
        for text in legend.get_texts():
            text.set_color(PLOT_COLORS["muted"])
        self.draw_idle()


    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._last_request is not None:
            # Margins and the legend anchor are pixel based, so the plot is
            # rebuilt from the stored request rather than rescaled.
            self.set_plot_data(**self._last_request)

    def _draw_empty(self, message: str) -> None:
        self.figure.clear()
        self.figure.subplots_adjust(left=0.04, right=0.98, bottom=0.08, top=0.96)
        axis = self.figure.add_subplot(111)
        self.axes = axis
        self.secondary_axes = None
        axis.set_facecolor(PLOT_COLORS["background"])
        axis.axis("off")
        axis.text(
            0.5,
            0.5,
            message,
            transform=axis.transAxes,
            ha="center",
            va="center",
            color=PLOT_COLORS["muted"],
            fontsize=10,
        )
        self.draw_idle()


class FlowFieldPlotCanvas(FigureCanvasQTAgg):
    """Axisymmetric station-field view using the calculated wall contour."""

    def __init__(self) -> None:
        self.figure = Figure(figsize=(12.0, 3.5), dpi=100, facecolor=PLOT_COLORS["background"])
        super().__init__(self.figure)
        self.setMinimumHeight(360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(f"background: {PLOT_COLORS['background']};")
        self.axes = None
        self.colorbar = None
        self._last_request: Optional[Dict[str, object]] = None
        self._empty_message = "Run Solve to populate the 2D flow field."
        self._draw_empty(self._empty_message)

    def set_flow_data(
        self,
        axial_profile: List[Dict[str, object]],
        *,
        variable: str = "mach",
        variable_label: str = "Mach",
        empty_message: str = "Run Solve to populate the 2D flow field.",
    ) -> None:
        self._last_request = {
            "axial_profile": axial_profile,
            "variable": variable,
            "variable_label": variable_label,
            "empty_message": empty_message,
        }
        rows_by_x: Dict[float, Dict[str, float]] = {}
        for row in axial_profile:
            x_value = _finite_float(row.get("x_mm"))
            radius = _finite_float(row.get("radius_mm"))
            field = _finite_float(row.get(variable))
            if x_value is None or radius is None or field is None:
                continue
            rows_by_x[x_value] = {
                "x": x_value,
                "radius": max(0.0, radius),
                "field": field,
            }
        profile = [rows_by_x[key] for key in sorted(rows_by_x)]
        self._empty_message = empty_message
        if len(profile) < 2:
            self._draw_empty(empty_message)
            return
        self._draw_field(profile, variable=variable, variable_label=variable_label)

    def _draw_field(self, profile: List[Dict[str, float]], *, variable: str, variable_label: str) -> None:
        x_values = np.asarray([row["x"] for row in profile], dtype=float)
        radii = np.maximum(0.05, np.asarray([row["radius"] for row in profile], dtype=float))
        field_values = np.asarray([row["field"] for row in profile], dtype=float)
        field_min = float(np.min(field_values))
        field_max = float(np.max(field_values))
        if math.isclose(field_min, field_max, rel_tol=0.0, abs_tol=1e-12):
            field_min -= 0.5
            field_max += 0.5

        is_mach = variable.lower() == "mach"
        if is_mach and field_min < 1.0 < field_max:
            norm = TwoSlopeNorm(vmin=field_min, vcenter=1.0, vmax=field_max)
        else:
            norm = Normalize(vmin=field_min, vmax=field_max)

        self.figure.clear()
        _bar_left, bar_right, bar_top = _apply_margins(self.figure, rows=COLORBAR_ROWS)
        axis = self.figure.add_subplot(111)
        self.axes = axis
        _style_axis(axis)
        axis.set_xlabel("Axial position (mm)", labelpad=7)
        axis.set_ylabel("Radius (mm)", labelpad=8)

        radial_fraction = np.linspace(-1.0, 1.0, 65)
        mesh_x = np.broadcast_to(x_values, (radial_fraction.size, x_values.size))
        mesh_y = radial_fraction[:, None] * radii[None, :]
        mesh_values = np.broadcast_to(field_values, mesh_x.shape)
        field_mesh = axis.pcolormesh(
            mesh_x,
            mesh_y,
            mesh_values,
            shading="gouraud",
            cmap=MACH_CMAP,
            norm=norm,
            antialiased=True,
            zorder=1,
        )

        station_step = max(1, len(x_values) // 9)
        for index in range(0, len(x_values), station_step):
            axis.plot(
                [x_values[index], x_values[index]],
                [-radii[index], radii[index]],
                color="#FFFFFF",
                linewidth=0.45,
                alpha=0.16,
                zorder=2,
            )
        axis.plot(x_values, radii, color=PLOT_COLORS["text"], linewidth=1.5, zorder=4)
        axis.plot(x_values, -radii, color=PLOT_COLORS["text"], linewidth=1.5, zorder=4)
        axis.axhline(0.0, color=PLOT_COLORS["centerline"], linewidth=0.8, linestyle=(0, (4, 3)), zorder=3)

        throat_index = int(np.argmin(radii))
        throat_x = float(x_values[throat_index])
        throat_state = f"\nM = {field_values[throat_index]:.2f}" if is_mach else ""
        axis.axvline(throat_x, color=PLOT_COLORS["throat"], linewidth=1.2, linestyle=(0, (4, 2)), zorder=5)
        axis.annotate(
            f"Throat  {throat_x:.2f} mm{throat_state}",
            xy=(throat_x, float(radii[throat_index])),
            xytext=(7, 18),
            textcoords="offset points",
            color=PLOT_COLORS["throat"],
            fontsize=8,
            ha="left",
            va="bottom",
            arrowprops={"arrowstyle": "-", "color": PLOT_COLORS["throat"], "linewidth": 0.8},
            zorder=6,
        )

        if is_mach:
            sonic_index = int(np.argmin(np.abs(field_values - 1.0)))
            sonic_x = float(x_values[sonic_index])
            station_spacing = float(np.median(np.diff(x_values)))
            sonic_is_separate = (
                sonic_index != throat_index
                and abs(sonic_x - throat_x) > max(1e-9, station_spacing * 1.5)
                and abs(float(field_values[throat_index]) - 1.0) > 0.10
            )
            if sonic_is_separate:
                axis.axvline(sonic_x, color=PLOT_COLORS["sonic"], linewidth=0.9, linestyle=(0, (2, 3)), alpha=0.75, zorder=5)
                axis.text(
                    sonic_x,
                    -float(radii[sonic_index]) - max(radii) * 0.11,
                    f"M = {field_values[sonic_index]:.2f}",
                    color=PLOT_COLORS["text"],
                    fontsize=8,
                    ha="center",
                    va="top",
                    zorder=6,
                )

        maximum_radius = float(np.max(radii))
        x_span = max(1e-9, float(x_values[-1] - x_values[0]))
        axis.set_xlim(float(x_values[0] - x_span * 0.015), float(x_values[-1] + x_span * 0.015))
        axis.set_ylim(-maximum_radius * 1.28, maximum_radius * 1.28)
        # A fixed display aspect keeps long nozzles legible without implying a
        # different calculated contour. Both axes remain labeled in millimeters.
        axis.set_aspect(0.48, adjustable="box")
        quantity_label = "Mach" if is_mach else variable_label.replace(" field", "")
        axis.text(
            0.01,
            0.04,
            f"Inlet  {quantity_label} {_format_tick(field_values[0])}",
            transform=axis.transAxes,
            color=PLOT_COLORS["muted"],
            fontsize=8,
            ha="left",
            va="bottom",
        )
        axis.text(
            0.99,
            0.04,
            f"Exit  {quantity_label} {_format_tick(field_values[-1])}",
            transform=axis.transAxes,
            color=PLOT_COLORS["muted"],
            fontsize=8,
            ha="right",
            va="bottom",
        )

        # A figure-level axes keeps the colour bar in the reserved strip above
        # the plot regardless of how tall the canvas is.
        height_px = max(1.0, self.figure.get_size_inches()[1] * self.figure.dpi)
        bar_height = 9.0 / height_px
        bar_width = 0.22
        color_axis = self.figure.add_axes(
            (bar_right - bar_width, bar_top + 20.0 / height_px, bar_width, bar_height)
        )
        self.colorbar = self.figure.colorbar(field_mesh, cax=color_axis, orientation="horizontal")
        self.colorbar.ax.tick_params(colors=PLOT_COLORS["muted"], labelsize=7, length=2)
        self.colorbar.outline.set_edgecolor(PLOT_COLORS["border"])
        self.colorbar.outline.set_linewidth(0.7)
        self.colorbar.set_label(variable_label, color=PLOT_COLORS["text"], fontsize=8, labelpad=4)
        self.colorbar.ax.xaxis.set_label_position("top")
        if is_mach and field_min < 1.0 < field_max:
            ticks = sorted({field_min, 1.0, field_max})
            self.colorbar.set_ticks(ticks)
            self.colorbar.set_ticklabels([_format_tick(value) for value in ticks])
        self.draw_idle()


    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._last_request is not None:
            # The margins and the colour bar are positioned in pixels, so the
            # field is rebuilt rather than rescaled.
            request = dict(self._last_request)
            profile = request.pop("axial_profile")
            self.set_flow_data(profile, **request)

    def _draw_empty(self, message: str) -> None:
        self.figure.clear()
        self.figure.subplots_adjust(left=0.04, right=0.98, bottom=0.08, top=0.96)
        axis = self.figure.add_subplot(111)
        self.axes = axis
        self.colorbar = None
        axis.set_facecolor(PLOT_COLORS["background"])
        axis.axis("off")
        axis.text(
            0.5,
            0.5,
            message,
            transform=axis.transAxes,
            ha="center",
            va="center",
            color=PLOT_COLORS["muted"],
            fontsize=10,
        )
        self.draw_idle()
