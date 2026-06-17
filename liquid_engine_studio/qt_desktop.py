import json
import math
import os
import shutil
import sys
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from liquid_engine_studio import __version__ as APP_VERSION
from liquid_engine_studio.concept_model import INJECTOR_TYPES, create_concept_design
from liquid_engine_studio.coupled_cycle_solver import solve as solve_coupled_cycle
from liquid_engine_studio.defaults import DEFAULT_OBJECTIVE_WEIGHTS, DEFAULT_STATE
from liquid_engine_studio.exporter import (
    build_revolved_profile_points,
    export_measurements_csv,
    export_profile_dxf,
    export_station_csv,
)
from liquid_engine_studio.materials import MATERIAL_OPTIONS
from liquid_engine_studio.material_assignment_solver import assign_materials
from liquid_engine_studio.objectives import evaluate_objectives, normalize_objective_weights
from liquid_engine_studio.optimizer_hooks import (
    build_optimizer_seed,
    run_feasibility_first_optimizer,
    run_genetic_optimizer,
    apply_multifidelity_confirmation,
)
from liquid_engine_studio.project_io import load_project, save_project
from liquid_engine_studio.propellants import FUEL_NAMES, OXIDIZER_NAMES
from liquid_engine_studio.solver_assumptions import get_default_solver_assumptions
from liquid_engine_studio.structural_material_solver import build_structural_materials_output
from liquid_engine_studio.validation_pack import validate_concept_design

try:
    from PyQt5.QtCore import Qt, QLineF, QPointF, QRectF, QTimer
    from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
    from PyQt5.QtWidgets import (
        QApplication,
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QFrame,
        QGraphicsPathItem,
        QGraphicsRectItem,
        QGraphicsScene,
        QGraphicsSimpleTextItem,
        QGraphicsTextItem,
        QGraphicsView,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSizePolicy,
        QSplitter,
        QStatusBar,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
        QHeaderView,
    )
except ImportError as exc:  # pragma: no cover - exercised only when Qt is absent
    raise ImportError("PyQt5 is required for the Qt desktop UI.") from exc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGO_PNG_PATH = PROJECT_ROOT / "assets" / "Logo.png"
UPDATE_RELEASE_API = "https://api.github.com/repos/iavros/StanThrust/releases/latest"

QT_PALETTE = {
    "bg": "#0D0F12",
    "panel": "#15181D",
    "card": "#1B2026",
    "card_alt": "#20272E",
    "input": "#11151A",
    "border": "#343B44",
    "border_soft": "#252B33",
    "text": "#F2F5F3",
    "muted": "#A9B2AD",
    "muted_soft": "#747E7A",
    "accent": "#2B8C7E",
    "accent_hover": "#37A493",
    "accent_dark": "#17685D",
    "success": "#6FCF97",
    "warning": "#E0A94B",
    "danger": "#E76F51",
    "fuel": "#E1A955",
    "oxidizer": "#6DB4F2",
    "cooling": "#55C2A2",
}


FIELD_HELPERS = {
    "fuel_name": "Primary fuel selection.",
    "oxidizer_name": "Primary oxidizer selection.",
    "injector_type": "Injector pattern used for the engine.",
    "target_thrust_newtons": "How much thrust the engine should produce.",
    "target_impulse_newton_seconds": "Total push delivered over the full burn.",
    "burn_time_seconds": "Planned burn duration.",
    "target_diameter_mm": "Maximum allowed outside diameter.",
    "tank_diameter_mm": "Maximum tank diameter constraint.",
    "chamber_diameter_mm": "Maximum combustion chamber diameter.",
    "nozzle_diameter_mm": "Maximum nozzle exit diameter.",
    "mixture_ratio": "Oxidizer to fuel mass ratio.",
    "packaging_bias": "How aggressively to prioritize compact packaging.",
    "factor_of_safety": "Extra structural margin for the engine.",
    "solver_station_count": "Axial station count used by the proxy solver.",
    "solver_convergence_tolerance": "Lower values require tighter convergence.",
    "solver_iteration_limit": "Maximum solver iterations before stopping.",
    "solver_flow_model": "Fast mode is the default preview-oriented quasi-1D path. Refined mode adds contour-aware throat sizing and ambient-pressure correction for explicit solves.",
}


MEASUREMENT_TABS = (
    "All",
    "Tanks",
    "Feed/Pumps",
    "Chamber",
    "Injector",
    "Nozzle",
    "Cooling",
    "Overall",
    "CFD",
)

INJECTOR_DISPLAY_NAMES = {
    "impinging": "Impinging",
    "pintle": "Pintle",
}

FLOW_MODEL_DISPLAY_NAMES = {
    "fast": "Fast Preview",
    "refined": "Refined Solve",
}


def _display_injector_name(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return INJECTOR_DISPLAY_NAMES.get(normalized, str(value or ""))


def _display_option_name(value: object) -> str:
    text = str(value or "")
    if text and text == text.lower():
        return text.replace("_", " ").replace("-", " ").title()
    return text


def _as_float(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _format_number(value: object, decimals: int = 1) -> str:
    if value in (None, "", "--"):
        return "--"
    try:
        return "{0:.{1}f}".format(float(value), decimals)
    except (TypeError, ValueError):
        return str(value)


def _safe_float(value: object, fallback: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _version_parts(version: object) -> Tuple[int, ...]:
    cleaned = str(version or "").strip().lstrip("vV")
    parts: List[int] = []
    for token in cleaned.replace("-", ".").replace("_", ".").split("."):
        digits = "".join(char for char in token if char.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts or [0])


def _is_newer_version(candidate: object, current: object) -> bool:
    candidate_parts = list(_version_parts(candidate))
    current_parts = list(_version_parts(current))
    width = max(len(candidate_parts), len(current_parts))
    candidate_parts.extend([0] * (width - len(candidate_parts)))
    current_parts.extend([0] * (width - len(current_parts)))
    return tuple(candidate_parts) > tuple(current_parts)


class MetricCard(QFrame):
    def __init__(self, title: str, unit: str = "") -> None:
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("metricTitle")
        layout.addWidget(label)
        value_row = QHBoxLayout()
        value_row.setSpacing(6)
        self.value_label = QLabel("--")
        self.value_label.setObjectName("metricValue")
        value_row.addWidget(self.value_label)
        if unit:
            unit_label = QLabel(unit)
            unit_label.setObjectName("metricUnit")
            value_row.addWidget(unit_label)
        value_row.addStretch(1)
        layout.addLayout(value_row)
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("metricDetail")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

    def set_metric(self, value: str, detail: str = "") -> None:
        self.value_label.setText(value)
        self.detail_label.setText(detail)


class StatusBanner(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("statusBanner")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(4)
        self.chip = QLabel("Ready")
        self.chip.setObjectName("statusChip")
        layout.addWidget(self.chip, 0, Qt.AlignLeft)
        self.title = QLabel("Ready to solve")
        self.title.setObjectName("statusTitle")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.message = QLabel("Set inputs, review the geometry, then run the solver.")
        self.message.setObjectName("statusMessage")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

    def update_banner(self, title: str, message: str, tone: str) -> None:
        self.chip.setText(tone.title())
        self.chip.setProperty("tone", tone)
        self.chip.style().unpolish(self.chip)
        self.chip.style().polish(self.chip)
        self.title.setText(title)
        self.message.setText(message)


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()


class EngineeringPlotCanvas(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(208)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._x_label = "X"
        self._primary_label = ""
        self._secondary_label = ""
        self._primary_series: List[Dict[str, object]] = []
        self._secondary_series: List[Dict[str, object]] = []
        self._empty_message = "Run Solve to populate this plot."

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
        self._x_label = x_label
        self._primary_label = primary_label
        self._secondary_label = secondary_label
        self._primary_series = self._normalize_series(primary_series)
        self._secondary_series = self._normalize_series(secondary_series or [])
        self._empty_message = empty_message
        self.update()

    @staticmethod
    def _normalize_series(raw_series: List[Dict[str, object]]) -> List[Dict[str, object]]:
        normalized: List[Dict[str, object]] = []
        for series in raw_series:
            points = []
            for point in list(series.get("points", [])):
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    continue
                x_value = _safe_float(point[0])
                y_value = _safe_float(point[1])
                if x_value is None or y_value is None:
                    continue
                points.append((x_value, y_value))
            if points:
                normalized.append(
                    {
                        "label": str(series.get("label", "Series")),
                        "color": str(series.get("color", QT_PALETTE["text"])),
                        "points": points,
                    }
                )
        return normalized

    @staticmethod
    def _value_range(series_list: List[Dict[str, object]]) -> Optional[List[float]]:
        values = [point[1] for series in series_list for point in list(series.get("points", []))]
        if not values:
            return None
        minimum = min(values)
        maximum = max(values)
        if abs(maximum - minimum) < 1e-9:
            padding = abs(maximum) * 0.12 if abs(maximum) > 1e-6 else 1.0
            return [minimum - padding, maximum + padding]
        padding = (maximum - minimum) * 0.10
        return [minimum - padding, maximum + padding]

    @staticmethod
    def _format_axis_value(value: float) -> str:
        magnitude = abs(value)
        if magnitude >= 1000.0:
            return "{0:.0f}".format(value)
        if magnitude >= 100.0:
            return "{0:.1f}".format(value)
        if magnitude >= 10.0:
            return "{0:.2f}".format(value)
        return "{0:.3f}".format(value)

    def paintEvent(self, _event) -> None:  # pragma: no cover - GUI paint path
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        outer = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(outer, QColor("#11151A"))

        left_margin = 54
        right_margin = 58 if self._secondary_series else 18
        top_margin = 38
        bottom_margin = 34
        plot_rect = QRectF(
            left_margin,
            top_margin,
            max(40.0, outer.width() - left_margin - right_margin),
            max(60.0, outer.height() - top_margin - bottom_margin),
        )

        painter.setPen(QPen(QColor(QT_PALETTE["border_soft"]), 1))
        painter.drawRoundedRect(plot_rect.adjusted(-8, -10, 8, 10), 14, 14)

        all_series = list(self._primary_series) + list(self._secondary_series)
        if not all_series:
            painter.setPen(QColor(QT_PALETTE["muted"]))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(outer, Qt.AlignCenter, self._empty_message)
            return

        x_values = [point[0] for series in all_series for point in list(series.get("points", []))]
        x_min = min(x_values)
        x_max = max(x_values)
        if abs(x_max - x_min) < 1e-9:
            x_max = x_min + 1.0

        primary_range = self._value_range(self._primary_series)
        secondary_range = self._value_range(self._secondary_series) if self._secondary_series else None
        if primary_range is None:
            painter.setPen(QColor(QT_PALETTE["muted"]))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(outer, Qt.AlignCenter, self._empty_message)
            return

        primary_min, primary_max = primary_range
        secondary_min, secondary_max = secondary_range if secondary_range else (0.0, 1.0)

        grid_pen = QPen(QColor(QT_PALETTE["border_soft"]), 1)
        grid_pen.setCosmetic(True)
        for step in range(5):
            ratio = step / 4.0
            y_pos = plot_rect.bottom() - ratio * plot_rect.height()
            painter.setPen(grid_pen)
            painter.drawLine(QLineF(plot_rect.left(), y_pos, plot_rect.right(), y_pos))
            value = primary_min + ratio * (primary_max - primary_min)
            painter.setPen(QColor(QT_PALETTE["muted"]))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(4, int(y_pos + 4), left_margin - 10, 14, Qt.AlignRight, self._format_axis_value(value))
            if self._secondary_series:
                secondary_value = secondary_min + ratio * (secondary_max - secondary_min)
                painter.drawText(
                    int(plot_rect.right()) + 10,
                    int(y_pos + 4),
                    right_margin - 14,
                    14,
                    Qt.AlignLeft,
                    self._format_axis_value(secondary_value),
                )

        for step in range(5):
            ratio = step / 4.0
            x_pos = plot_rect.left() + ratio * plot_rect.width()
            painter.setPen(grid_pen)
            painter.drawLine(QLineF(x_pos, plot_rect.top(), x_pos, plot_rect.bottom()))
            tick_value = x_min + ratio * (x_max - x_min)
            painter.setPen(QColor(QT_PALETTE["muted"]))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(
                int(x_pos - 28),
                int(plot_rect.bottom()) + 8,
                56,
                14,
                Qt.AlignHCenter,
                self._format_axis_value(tick_value),
            )

        painter.setPen(QColor(QT_PALETTE["text"]))
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.drawText(int(plot_rect.left()), 18, self._primary_label)
        if self._secondary_series:
            painter.drawText(int(plot_rect.right()) - 90, 18, 90, 14, Qt.AlignRight, self._secondary_label)
        painter.setPen(QColor(QT_PALETTE["muted"]))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(int(plot_rect.center().x()) - 40, int(outer.bottom()) - 2, 80, 14, Qt.AlignHCenter, self._x_label)

        legend_x = int(plot_rect.left())
        legend_y = 22
        for series in all_series:
            painter.setPen(QPen(QColor(series["color"]), 3))
            painter.drawLine(legend_x, legend_y, legend_x + 12, legend_y)
            painter.setPen(QColor(QT_PALETTE["muted"]))
            painter.setFont(QFont("Segoe UI", 8))
            label_width = min(92, QFontMetrics(QFont("Segoe UI", 8)).horizontalAdvance(series["label"]) + 8)
            painter.drawText(legend_x + 18, legend_y - 8, label_width, 14, Qt.AlignLeft, series["label"])
            legend_x += label_width + 34

        def map_x(value: float) -> float:
            return plot_rect.left() + (value - x_min) / max(1e-9, x_max - x_min) * plot_rect.width()

        def map_primary(value: float) -> float:
            return plot_rect.bottom() - (value - primary_min) / max(1e-9, primary_max - primary_min) * plot_rect.height()

        def map_secondary(value: float) -> float:
            return plot_rect.bottom() - (value - secondary_min) / max(1e-9, secondary_max - secondary_min) * plot_rect.height()

        painter.setClipRect(plot_rect.adjusted(-2, -2, 2, 2))
        for series in self._primary_series:
            pen = QPen(QColor(series["color"]), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            points = list(series["points"])
            for index in range(len(points) - 1):
                painter.drawLine(
                    QLineF(
                        map_x(points[index][0]),
                        map_primary(points[index][1]),
                        map_x(points[index + 1][0]),
                        map_primary(points[index + 1][1]),
                    )
                )
        for series in self._secondary_series:
            pen = QPen(QColor(series["color"]), 2.0, Qt.DashLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            points = list(series["points"])
            for index in range(len(points) - 1):
                painter.drawLine(
                    QLineF(
                        map_x(points[index][0]),
                        map_secondary(points[index][1]),
                        map_x(points[index + 1][0]),
                        map_secondary(points[index + 1][1]),
                    )
                )
        painter.setClipping(False)


class EngineeringPlotCard(QFrame):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        layout.addWidget(title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("sectionBody")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)
        self.canvas = EngineeringPlotCanvas()
        layout.addWidget(self.canvas, 1)
        self.note_label = QLabel("")
        self.note_label.setObjectName("sectionBody")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)

    def set_plot_data(
        self,
        *,
        subtitle: str,
        x_label: str,
        primary_label: str,
        primary_series: List[Dict[str, object]],
        secondary_label: str = "",
        secondary_series: Optional[List[Dict[str, object]]] = None,
        note: str = "",
        empty_message: str = "Run Solve to populate this plot.",
    ) -> None:
        self.subtitle_label.setText(subtitle)
        self.note_label.setText(note)
        self.canvas.set_plot_data(
            x_label=x_label,
            primary_label=primary_label,
            primary_series=primary_series,
            secondary_label=secondary_label,
            secondary_series=secondary_series or [],
            empty_message=empty_message,
        )


class SchematicView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setScene(QGraphicsScene(self))
        self.setObjectName("schematicView")
        self.setMinimumHeight(460)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def render_design(self, design) -> None:
        scene = self.scene()
        scene.clear()
        scene.setSceneRect(0, 0, 1100, 620)

        bg = QGraphicsRectItem(QRectF(0, 0, 1100, 620))
        bg.setBrush(QColor("#0D0F12"))
        bg.setPen(QPen(QColor("#0D0F12")))
        scene.addItem(bg)

        self._add_text(scene, 48, 28, "Propellant Stack", QT_PALETTE["text"], 12, True)
        self._add_text(scene, 48, 50, "Tank lengths, diameters, and feed handoff", QT_PALETTE["muted"], 9, max_width=260)
        self._add_text(scene, 344, 28, "Feed", QT_PALETTE["text"], 12, True)
        self._add_text(scene, 544, 28, "Engine Geometry", QT_PALETTE["text"], 12, True)
        self._add_text(scene, 544, 50, "Resolved chamber shell and nozzle contour", QT_PALETTE["muted"], 9, max_width=420)

        self._add_round_rect(scene, 38, 82, 246, 352, QT_PALETTE["card_alt"])
        self._add_round_rect(scene, 312, 126, 188, 196, QT_PALETTE["card"])
        self._add_round_rect(scene, 526, 82, 534, 426, QT_PALETTE["card"])

        self._add_tag(scene, 58, 100, "OXIDIZER")
        self._add_tag(scene, 58, 242, "FUEL")

        scene.addRect(58, 136, 14, 82, QPen(QColor(QT_PALETTE["oxidizer"])), QBrush(QColor(QT_PALETTE["oxidizer"])))
        scene.addRect(58, 278, 14, 82, QPen(QColor(QT_PALETTE["fuel"])), QBrush(QColor(QT_PALETTE["fuel"])))
        self._add_text(scene, 92, 138, design.inputs.oxidizer_name, QT_PALETTE["text"], 14, True, max_width=166)
        self._add_text(scene, 92, 168, "Length {0} mm".format(_format_number(float(design.derived.oxidizer_tank_length_mm), 2)), QT_PALETTE["text"], 10, True, 166)
        self._add_text(scene, 92, 194, "Diameter {0} mm".format(_format_number(float(design.inputs.tank_diameter_mm), 2)), QT_PALETTE["muted"], 9, max_width=166)
        self._add_text(scene, 92, 280, design.inputs.fuel_name, QT_PALETTE["text"], 14, True, max_width=166)
        self._add_text(scene, 92, 310, "Length {0} mm".format(_format_number(float(design.derived.fuel_tank_length_mm), 2)), QT_PALETTE["text"], 10, True, 166)
        self._add_text(scene, 92, 336, "Diameter {0} mm".format(_format_number(float(design.inputs.tank_diameter_mm), 2)), QT_PALETTE["muted"], 9, max_width=166)
        self._add_text(
            scene,
            58,
            390,
            "Total stack {0} mm".format(_format_number(float(design.derived.total_stack_length_mm), 2)),
            QT_PALETTE["muted"],
            9,
            True,
            max_width=204,
        )

        feed_title = "Pump-fed" if design.inputs.use_pumps else "Pressure-fed"
        self._add_text(scene, 334, 150, "Feed System", QT_PALETTE["text"], 12, True)
        self._add_text(scene, 334, 178, feed_title, QT_PALETTE["accent_hover"], 11, True, max_width=140)
        if design.inputs.use_pumps:
            self._add_text(
                scene,
                334,
                212,
                "Fuel impeller {0} mm".format(_format_number(design.derived.engineering_values.get("fuel_impeller_diameter_mm", "--"), 2)),
                QT_PALETTE["muted"],
                9,
                max_width=144,
            )
            self._add_text(
                scene,
                334,
                236,
                "Ox impeller {0} mm".format(_format_number(design.derived.engineering_values.get("oxidizer_impeller_diameter_mm", "--"), 2)),
                QT_PALETTE["muted"],
                9,
                max_width=144,
            )
            self._add_text(
                scene,
                334,
                260,
                "Pump head {0} kPa".format(_format_number(design.derived.engineering_values.get("pump_differential_pressure_kpa", "--"), 2)),
                QT_PALETTE["muted"],
                9,
                max_width=144,
            )
            self._add_text(
                scene,
                334,
                284,
                "Motor {0} kW".format(_format_number(design.derived.engineering_values.get("electric_motor_power_kw", "--"), 3)),
                QT_PALETTE["muted"],
                9,
                max_width=144,
            )
        else:
            self._add_text(scene, 334, 212, "Blowdown and regulated tank state", QT_PALETTE["muted"], 9, max_width=150, wrap=True)
            self._add_text(
                scene,
                334,
                252,
                "Fuel tank {0} kPa".format(_format_number(design.derived.engineering_values.get("fuel_tank_pressure_kpa", "--"), 2)),
                QT_PALETTE["muted"],
                9,
                max_width=144,
            )
            self._add_text(
                scene,
                334,
                276,
                "Ox tank {0} kPa".format(_format_number(design.derived.engineering_values.get("oxidizer_tank_pressure_kpa", "--"), 2)),
                QT_PALETTE["muted"],
                9,
                max_width=144,
            )
        self._add_text(
            scene,
            334,
            302,
            "Mixture ratio {0}".format(_format_number(float(design.inputs.mixture_ratio), 3)),
            QT_PALETTE["text"],
            10,
            True,
            max_width=144,
        )

        throat_text = "Throat {0} mm".format(
            _format_number(float(design.derived.engineering_values.get("nozzle_throat_diameter_mm", 0.0)), 2)
        )
        exit_text = "Exit {0} mm".format(
            _format_number(
                float(design.derived.engineering_values.get("nozzle_inner_diameter_mm", design.inputs.nozzle_diameter_mm)),
                2,
            )
        )
        ratio_text = "Area ratio {0}".format(
            _format_number(design.derived.engineering_values.get("nozzle_expansion_ratio", "--"), 3)
        )
        contour_text = str(
            design.derived.engineering_values.get("nozzle_contour_method_label", "Bell contour")
        ).replace(" contour", "")

        self._add_metric_pill(scene, 544, 96, throat_text, QT_PALETTE["accent_hover"], 142)
        self._add_metric_pill(scene, 698, 96, exit_text, QT_PALETTE["text"], 136)
        self._add_metric_pill(scene, 846, 96, ratio_text, QT_PALETTE["text"], 154)
        self._add_text(scene, 548, 146, contour_text, QT_PALETTE["text"], 10, True, max_width=196)
        self._add_text(
            scene,
            548,
            168,
            "Converging {0} mm   Diverging {1} mm".format(
                _format_number(design.derived.engineering_values.get("nozzle_converging_length_mm", 0.0), 2),
                _format_number(design.derived.engineering_values.get("nozzle_diverging_length_mm", 0.0), 2),
            ),
            QT_PALETTE["muted"],
            9,
            max_width=250,
        )

        cooling_text = "Regen cooling active" if design.inputs.regen_cooling else "Film cooling active" if design.inputs.film_cooling else "No active cooling"
        cooling_color = QT_PALETTE["cooling"] if design.inputs.regen_cooling else QT_PALETTE["accent_hover"] if design.inputs.film_cooling else QT_PALETTE["muted"]
        self._add_metric_pill(scene, 852, 146, cooling_text, cooling_color, 176)

        profile = self._draw_engine_profile(scene, design, 560, 244, 408, 150)

        ox_pen = QPen(QColor(QT_PALETTE["oxidizer"]), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        fuel_pen = QPen(QColor(QT_PALETTE["fuel"]), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        feed_bus_x = 516
        ox_route_y = 112
        fuel_route_y = 452
        ox_path = QPainterPath()
        ox_path.moveTo(284, 178)
        ox_path.cubicTo(306, 178, 300, ox_route_y, 336, ox_route_y)
        ox_path.lineTo(feed_bus_x, ox_route_y)
        ox_path.cubicTo(
            feed_bus_x + 14,
            ox_route_y,
            feed_bus_x + 16,
            profile["injector_top_y"],
            profile["injector_x"] + 18,
            profile["injector_top_y"],
        )
        fuel_path = QPainterPath()
        fuel_path.moveTo(284, 320)
        fuel_path.cubicTo(306, 320, 300, fuel_route_y, 336, fuel_route_y)
        fuel_path.lineTo(feed_bus_x, fuel_route_y)
        fuel_path.cubicTo(
            feed_bus_x + 14,
            fuel_route_y,
            feed_bus_x + 16,
            profile["injector_bottom_y"],
            profile["injector_x"] + 18,
            profile["injector_bottom_y"],
        )
        scene.addPath(ox_path, ox_pen)
        scene.addPath(fuel_path, fuel_pen)

        scene.addEllipse(profile["injector_x"] - 6, profile["injector_top_y"] - 6, 12, 12, QPen(QColor(QT_PALETTE["oxidizer"])), QBrush(QColor(QT_PALETTE["oxidizer"])))
        scene.addEllipse(profile["injector_x"] - 6, profile["injector_bottom_y"] - 6, 12, 12, QPen(QColor(QT_PALETTE["fuel"])), QBrush(QColor(QT_PALETTE["fuel"])))

        if design.inputs.regen_cooling:
            cooling_pen = QPen(QColor(QT_PALETTE["cooling"]), 2, Qt.DashLine, Qt.RoundCap)
            cooling_path = QPainterPath()
            cooling_path.moveTo(profile["chamber_end_x"] - 24, profile["center_y"] + 42)
            cooling_path.cubicTo(
                profile["throat_x"] + 20,
                profile["center_y"] + 70,
                profile["exit_x"] - 24,
                profile["center_y"] + 64,
                profile["exit_x"] - 8,
                profile["center_y"] + 8,
            )
            scene.addPath(cooling_path, cooling_pen)
        elif design.inputs.film_cooling:
            film_pen = QPen(QColor(QT_PALETTE["accent_hover"]), 2, Qt.DashLine, Qt.RoundCap)
            scene.addLine(profile["injector_x"] + 8, profile["center_y"] - 22, profile["injector_x"] + 44, profile["center_y"] - 8, film_pen)

        self._add_metric_pill(
            scene,
            544,
            470,
            "Max dia {0} mm".format(_format_number(float(design.derived.maximum_diameter_mm), 2)),
            QT_PALETTE["text"],
            162,
        )
        self._add_metric_pill(
            scene,
            718,
            470,
            "Thrust {0} N".format(_format_number(float(design.derived.engineering_values.get("calculated_thrust_newtons", 0.0)), 2)),
            QT_PALETTE["text"],
            170,
        )
        self._add_metric_pill(
            scene,
            900,
            470,
            "Chamber {0} kPa".format(_format_number(float(design.derived.engineering_values.get("chamber_pressure_kpa", 0.0)), 2)),
            QT_PALETTE["text"],
            144,
        )
        self.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self.scene() is not None:
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def _add_round_rect(self, scene: QGraphicsScene, x: float, y: float, w: float, h: float, fill: str):
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, w, h), 20, 20)
        item = QGraphicsPathItem(path)
        item.setBrush(QColor(fill))
        item.setPen(QPen(QColor(QT_PALETTE["border"]), 2))
        scene.addItem(item)
        return item

    def _add_text(
        self,
        scene: QGraphicsScene,
        x: float,
        y: float,
        text: str,
        color: str,
        size: int,
        bold: bool = False,
        max_width: Optional[float] = None,
        wrap: bool = False,
        minimum_size: int = 7,
    ) -> None:
        if max_width and wrap:
            fitted_size = self._fit_font_size(text, size, max_width, bold, minimum_size, wrap=True)
            font = QFont("Segoe UI", fitted_size)
            font.setBold(bold)
            item = QGraphicsTextItem(text)
            item.setFont(font)
            item.setDefaultTextColor(QColor(color))
            item.setTextWidth(max_width)
            item.document().setDocumentMargin(0)
        else:
            fitted_size = self._fit_font_size(text, size, max_width, bold, minimum_size, wrap=False)
            font = QFont("Segoe UI", fitted_size)
            font.setBold(bold)
            display_text = text
            if max_width:
                display_text = QFontMetrics(font).elidedText(text, Qt.ElideRight, max(8, int(max_width)))
            item = QGraphicsSimpleTextItem(display_text)
            item.setFont(font)
            item.setBrush(QColor(color))
        item.setPos(x, y)
        scene.addItem(item)

    @staticmethod
    def _fit_font_size(
        text: str,
        preferred_size: int,
        max_width: Optional[float],
        bold: bool,
        minimum_size: int,
        wrap: bool,
    ) -> int:
        if not max_width:
            return preferred_size
        for candidate in range(preferred_size, minimum_size - 1, -1):
            font = QFont("Segoe UI", candidate)
            font.setBold(bold)
            metrics = QFontMetrics(font)
            if wrap:
                segments = [segment for segment in str(text).replace("\n", " ").split(" ") if segment]
                required_width = max((metrics.horizontalAdvance(segment) for segment in segments), default=0)
            else:
                required_width = metrics.horizontalAdvance(text)
            if required_width <= max_width:
                return candidate
        return minimum_size

    def _add_tag(self, scene: QGraphicsScene, x: float, y: float, text: str) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, 102, 28), 12, 12)
        rect = QGraphicsPathItem(path)
        rect.setBrush(QColor("#20272E"))
        rect.setPen(QPen(QColor(QT_PALETTE["border_soft"]), 1))
        scene.addItem(rect)
        self._add_text(scene, x + 16, y + 6, text, QT_PALETTE["muted"], 8, True)

    def _draw_background_arc(
        self,
        scene: QGraphicsScene,
        x: float,
        y: float,
        width: float,
        height: float,
        color: str,
        alpha: float,
        thickness: int,
    ) -> None:
        path = QPainterPath()
        path.moveTo(x, y + height * 0.6)
        path.cubicTo(
            x + width * 0.2,
            y - height * 0.12,
            x + width * 0.78,
            y + height * 0.04,
            x + width,
            y + height * 0.8,
        )
        pen_color = QColor(color)
        pen_color.setAlphaF(alpha)
        scene.addPath(path, QPen(pen_color, thickness, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def _add_metric_pill(self, scene: QGraphicsScene, x: float, y: float, text: str, color: str, width: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, width, 34), 16, 16)
        item = QGraphicsPathItem(path)
        item.setBrush(QColor("#11151A"))
        item.setPen(QPen(QColor(QT_PALETTE["border_soft"]), 1))
        scene.addItem(item)
        accent = scene.addRect(x + 8, y + 8, 8, 18, QPen(QColor(color)), QBrush(QColor(color)))
        accent.setOpacity(0.95)
        self._add_text(scene, x + 26, y + 9, text, QT_PALETTE["text"], 9, True, max_width=width - 36)

    def _draw_engine_profile(self, scene: QGraphicsScene, design, x: float, y: float, width: float, height: float) -> Dict[str, float]:
        values = dict(design.derived.engineering_values)
        chamber_length_mm = max(1.0, float(design.derived.chamber_length_mm))
        nozzle_length_mm = max(1.0, float(design.derived.nozzle_length_mm))
        chamber_inner_diameter_mm = max(1.0, float(values.get("chamber_inner_diameter_mm", design.inputs.chamber_diameter_mm)))
        chamber_outer_diameter_mm = max(
            chamber_inner_diameter_mm,
            float(values.get("chamber_outer_diameter_mm", design.inputs.chamber_diameter_mm)),
        )
        nozzle_exit_inner_diameter_mm = max(1.0, float(values.get("nozzle_inner_diameter_mm", design.inputs.nozzle_diameter_mm)))
        nozzle_exit_outer_diameter_mm = max(
            nozzle_exit_inner_diameter_mm,
            float(values.get("nozzle_outer_diameter_mm", nozzle_exit_inner_diameter_mm)),
        )
        nozzle_wall_thickness_mm = max(0.8, float(values.get("nozzle_wall_thickness_mm", 1.8)))
        throat_diameter_mm = max(1.0, float(values.get("nozzle_throat_diameter_mm", nozzle_exit_inner_diameter_mm * 0.72)))
        contour_points = list(design.derived.nozzle_contour_points)
        if not contour_points:
            contour_points = [
                {"x_mm": 0.0, "radius_mm": chamber_inner_diameter_mm / 2.0},
                {"x_mm": float(values.get("nozzle_converging_length_mm", nozzle_length_mm * 0.28)), "radius_mm": throat_diameter_mm / 2.0},
                {"x_mm": nozzle_length_mm, "radius_mm": nozzle_exit_inner_diameter_mm / 2.0},
            ]

        total_length_mm = chamber_length_mm + nozzle_length_mm
        max_radius_mm = max(chamber_outer_diameter_mm / 2.0, nozzle_exit_outer_diameter_mm / 2.0, max(float(point.get("radius_mm", 0.0)) + nozzle_wall_thickness_mm for point in contour_points))
        scale_x = min(width / total_length_mm, 0.94)
        scale_y = min((height * 0.38) / max_radius_mm, 1.62)
        chamber_start_x = x + 12
        chamber_end_x = chamber_start_x + chamber_length_mm * scale_x
        center_y = y + height * 0.50
        chamber_outer_radius_px = (chamber_outer_diameter_mm / 2.0) * scale_y
        chamber_inner_radius_px = (chamber_inner_diameter_mm / 2.0) * scale_y

        scene.addLine(chamber_start_x - 4, center_y, x + width - 8, center_y, QPen(QColor(QT_PALETTE["muted_soft"]), 1, Qt.DashLine))

        outer_top = [(chamber_start_x, center_y - chamber_outer_radius_px), (chamber_end_x, center_y - chamber_outer_radius_px)]
        outer_bottom = [(chamber_end_x, center_y + chamber_outer_radius_px), (chamber_start_x, center_y + chamber_outer_radius_px)]
        inner_top = [(chamber_start_x, center_y - chamber_inner_radius_px), (chamber_end_x, center_y - chamber_inner_radius_px)]
        inner_bottom = [(chamber_end_x, center_y + chamber_inner_radius_px), (chamber_start_x, center_y + chamber_inner_radius_px)]
        throat_x = chamber_end_x

        for point in contour_points:
            axial_mm = float(point.get("x_mm", 0.0))
            radius_mm = float(point.get("radius_mm", 0.0))
            px = chamber_end_x + axial_mm * scale_x
            inner_radius_px = radius_mm * scale_y
            outer_radius_px = (radius_mm + nozzle_wall_thickness_mm) * scale_y
            outer_top.append((px, center_y - outer_radius_px))
            outer_bottom.insert(0, (px, center_y + outer_radius_px))
            inner_top.append((px, center_y - inner_radius_px))
            inner_bottom.insert(0, (px, center_y + inner_radius_px))
            if str(point.get("section", "")) == "throat":
                throat_x = px

        outer_path = QPainterPath()
        outer_path.moveTo(*outer_top[0])
        for px, py in outer_top[1:]:
            outer_path.lineTo(px, py)
        for px, py in outer_bottom:
            outer_path.lineTo(px, py)
        outer_path.closeSubpath()
        outer_item = QGraphicsPathItem(outer_path)
        outer_item.setBrush(QColor("#24161A"))
        outer_item.setPen(QPen(QColor(QT_PALETTE["border"]), 2))
        scene.addItem(outer_item)

        inner_path = QPainterPath()
        inner_path.moveTo(*inner_top[0])
        for px, py in inner_top[1:]:
            inner_path.lineTo(px, py)
        for px, py in inner_bottom:
            inner_path.lineTo(px, py)
        inner_path.closeSubpath()
        inner_item = QGraphicsPathItem(inner_path)
        inner_item.setBrush(QColor("#9F2431"))
        inner_item.setPen(QPen(QColor("#74202A"), 1))
        inner_item.setOpacity(0.92)
        scene.addItem(inner_item)

        throat_pen = QPen(QColor(QT_PALETTE["accent_hover"]), 2, Qt.DashLine)
        throat_radius_px = (throat_diameter_mm / 2.0) * scale_y
        scene.addLine(throat_x, center_y - throat_radius_px - 16, throat_x, center_y + throat_radius_px + 16, throat_pen)

        exit_x = chamber_end_x + nozzle_length_mm * scale_x
        self._add_dimension_line(scene, chamber_start_x, chamber_end_x, y + height + 10, "Chamber {0} mm".format(_format_number(chamber_length_mm, 2)))
        self._add_dimension_line(scene, chamber_end_x, exit_x, y + height + 34, "Nozzle {0} mm".format(_format_number(nozzle_length_mm, 2)))

        return {
            "center_y": center_y,
            "chamber_end_x": chamber_end_x,
            "throat_x": throat_x,
            "exit_x": exit_x,
            "injector_x": chamber_start_x + 8,
            "injector_top_y": center_y - chamber_inner_radius_px * 0.35,
            "injector_bottom_y": center_y + chamber_inner_radius_px * 0.35,
        }

    def _add_dimension_line(self, scene: QGraphicsScene, start_x: float, end_x: float, y: float, label: str) -> None:
        pen = QPen(QColor(QT_PALETTE["muted_soft"]), 1)
        scene.addLine(start_x, y, end_x, y, pen)
        scene.addLine(start_x, y - 6, start_x, y + 6, pen)
        scene.addLine(end_x, y - 6, end_x, y + 6, pen)
        available_width = max(42.0, end_x - start_x - 12.0)
        label_x = start_x + max(6.0, (end_x - start_x - available_width) * 0.5)
        self._add_text(scene, label_x, y - 18, label, QT_PALETTE["muted"], 8, True, max_width=available_width)


class Model3DView(QGraphicsView):
    def __init__(self, view_mode: str = "chamber_nozzle") -> None:
        super().__init__()
        self.view_mode = view_mode
        self._design = None
        self._angle_deg = -28.0
        self._dragging = False
        self._last_drag_x = 0
        self._callout_rects: List[QRectF] = []
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setScene(QGraphicsScene(self))
        self.setObjectName("schematicView")
        self.setMinimumHeight(420)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setMouseTracking(True)
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(45)
        self._spin_timer.timeout.connect(self._tick_spin)
        self._spin_timer.start()

    def render_design(self, design) -> None:
        self._design = design
        self._redraw_model()

    def _tick_spin(self) -> None:
        if self._design is None or self._dragging or not self.isVisible():
            return
        self._angle_deg = (self._angle_deg + 0.9) % 360.0
        self._redraw_model()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_drag_x = event.x()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging and self._design is not None:
            delta_x = event.x() - self._last_drag_x
            self._last_drag_x = event.x()
            self._angle_deg = (self._angle_deg + delta_x * 0.55) % 360.0
            self._redraw_model()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._dragging = False
        super().mouseReleaseEvent(event)

    def _redraw_model(self) -> None:
        design = self._design
        if design is None:
            return
        scene = self.scene()
        scene.clear()
        scene.setSceneRect(0, 0, 1100, 590)
        self._callout_rects = []
        bg = QGraphicsRectItem(QRectF(0, 0, 1100, 590))
        bg.setBrush(QColor("#0D0F12"))
        bg.setPen(QPen(QColor("#0D0F12")))
        scene.addItem(bg)

        if self.view_mode == "pumps":
            self._draw_pumps(scene, design)
        elif self.view_mode == "injector":
            self._draw_injector(scene, design)
        elif self.view_mode == "tanks":
            self._draw_tanks(scene, design)
        else:
            self._draw_chamber_nozzle(scene, design)
        self.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self.scene() is not None:
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def _draw_chamber_nozzle(self, scene: QGraphicsScene, design) -> None:
        self._component_title(
            scene,
            "Chamber And Nozzle",
            "Calculated chamber shell, throat, and bell nozzle.",
        )
        values = dict(design.derived.engineering_values)
        chamber_length = float(design.derived.chamber_length_mm)
        nozzle_length = float(design.derived.nozzle_length_mm)
        chamber_outer_diameter = _safe_float(values.get("chamber_outer_diameter_mm"), design.inputs.chamber_diameter_mm) or design.inputs.chamber_diameter_mm
        chamber_outer_radius = max(0.5, chamber_outer_diameter * 0.5)
        nozzle_wall = max(0.0, _safe_float(values.get("nozzle_wall_thickness_mm"), 0.0) or 0.0)
        profile: List[Tuple[float, float]] = [
            (0.0, chamber_outer_radius),
            (chamber_length, chamber_outer_radius),
        ]
        for index, point in enumerate(design.derived.nozzle_contour_points):
            axial_mm = float(point.get("x_mm", 0.0))
            inner_radius = max(0.0, float(point.get("radius_mm", 0.0)))
            outer_radius = inner_radius + nozzle_wall
            if index == 0:
                outer_radius = max(outer_radius, chamber_outer_radius)
            profile.append((chamber_length + axial_mm, outer_radius))
        if len(profile) < 2:
            self._add_text(scene, 48, 110, "No model profile is available for the current design.", QT_PALETTE["warning"], 10, 420)
            return

        max_x = max(point[0] for point in profile)
        max_radius = max(point[1] for point in profile)
        scale = min(552.0 / max(1.0, max_x), 150.0 / max(1.0, max_radius))
        origin = (414.0, 316.0)
        segments = 28
        phase = self._spin_phase()
        rings: List[List[Tuple[float, float, float]]] = []
        for x_mm, radius_mm in profile:
            ring: List[Tuple[float, float, float]] = []
            for index in range(segments):
                theta = phase + 2.0 * math.pi * index / segments
                ring.append((x_mm - max_x * 0.5, radius_mm * math.cos(theta), radius_mm * math.sin(theta)))
            rings.append(ring)
        self._draw_ring_mesh(scene, rings, origin, scale, "#AA2735", "#E16A76", cap_ends=True)

        throat_diameter = _format_number(values.get("nozzle_throat_diameter_mm", "--"), 2)
        exit_diameter = _format_number(values.get("nozzle_inner_diameter_mm", design.inputs.nozzle_diameter_mm), 2)
        length = _format_number(chamber_length + nozzle_length, 2)
        contour = str(values.get("nozzle_contour_method_label", "Nozzle contour"))
        converging_length = _safe_float(values.get("nozzle_converging_length_mm"), nozzle_length * 0.35) or nozzle_length * 0.35
        throat_x = chamber_length + converging_length
        throat_radius = max(0.5, (_safe_float(values.get("nozzle_throat_diameter_mm"), 1.0) or 1.0) * 0.5)
        exit_radius = max(0.5, (_safe_float(values.get("nozzle_inner_diameter_mm"), design.inputs.nozzle_diameter_mm) or design.inputs.nozzle_diameter_mm) * 0.5)
        chamber_mid = self._project(chamber_length * 0.42 - max_x * 0.5, -chamber_outer_radius * 1.08, 0.0, origin, scale)
        throat_point = self._project(throat_x - max_x * 0.5, throat_radius, 0.0, origin, scale)
        exit_point = self._project(max_x * 0.5, exit_radius, 0.0, origin, scale)
        self._draw_callout(scene, (chamber_mid[0], chamber_mid[1]), (176.0, 454.0), "Chamber length {0} mm".format(_format_number(chamber_length, 2)), QT_PALETTE["text"])
        self._draw_callout(scene, (throat_point[0], throat_point[1]), (520.0, 166.0), "Throat dia {0} mm".format(throat_diameter), QT_PALETTE["accent_hover"])
        self._draw_callout(scene, (exit_point[0], exit_point[1]), (638.0, 500.0), "Exit dia {0} mm".format(exit_diameter), QT_PALETTE["text"])
        self._draw_callout(scene, (origin[0] + 8.0, origin[1] + max_radius * scale * 0.86), (436.0, 526.0), "Nozzle length {0} mm".format(_format_number(nozzle_length, 2)), QT_PALETTE["muted"])
        self._metric(scene, 790, 104, "Length", f"{length} mm")
        self._metric(scene, 790, 166, "Throat", f"{throat_diameter} mm")
        self._metric(scene, 790, 228, "Exit", f"{exit_diameter} mm")
        self._metric(scene, 790, 290, "Contour", contour)
        cooling = "Regen" if design.inputs.regen_cooling else "Film" if design.inputs.film_cooling else "Passive"
        self._metric(scene, 790, 352, "Cooling", cooling)

    def _draw_tanks(self, scene: QGraphicsScene, design) -> None:
        self._component_title(
            scene,
            "Propellant Tanks",
            "Calculated oxidizer and fuel tank envelopes.",
        )
        values = dict(design.derived.engineering_values)
        ox_diameter = _safe_float(values.get("oxidizer_tank_outer_diameter_mm"), design.inputs.tank_diameter_mm) or design.inputs.tank_diameter_mm
        fuel_diameter = _safe_float(values.get("fuel_tank_outer_diameter_mm"), design.inputs.tank_diameter_mm) or design.inputs.tank_diameter_mm
        ox_length = float(design.derived.oxidizer_tank_length_mm)
        fuel_length = float(design.derived.fuel_tank_length_mm)
        ox_feed_length = _safe_float(values.get("oxidizer_feed_tube_length_mm"), design.derived.feed_system_bay_length_mm * 0.72) or design.derived.feed_system_bay_length_mm * 0.72
        fuel_feed_length = _safe_float(values.get("fuel_feed_tube_length_mm"), design.derived.feed_system_bay_length_mm * 0.72) or design.derived.feed_system_bay_length_mm * 0.72
        ox_feed_diameter = _safe_float(values.get("oxidizer_feed_tube_diameter_mm"), max(3.0, ox_diameter * 0.04)) or max(3.0, ox_diameter * 0.04)
        fuel_feed_diameter = _safe_float(values.get("fuel_feed_tube_diameter_mm"), max(3.0, fuel_diameter * 0.04)) or max(3.0, fuel_diameter * 0.04)
        max_length = max(1.0, ox_length, fuel_length)
        max_feed_length = max(1.0, ox_feed_length, fuel_feed_length)
        max_diameter = max(1.0, ox_diameter, fuel_diameter)
        scale = min(520.0 / (max_length + max_feed_length), 102.0 / max_diameter)
        ox_origin = (356.0, 210.0)
        fuel_origin = (356.0, 420.0)
        self._draw_axial_cylinder_mesh(scene, ox_length, ox_diameter * 0.5, ox_origin, scale, QT_PALETTE["oxidizer"], "#9FD2F7")
        self._draw_axial_cylinder_mesh(scene, fuel_length, fuel_diameter * 0.5, fuel_origin, scale, QT_PALETTE["fuel"], "#F3C26E")
        self._add_text(scene, 74, 154, "Oxidizer Tank", QT_PALETTE["text"], 11, 180)
        self._add_text(scene, 74, 364, "Fuel Tank", QT_PALETTE["text"], 11, 180)
        ox_front = self._project(ox_length * 0.5, ox_diameter * 0.18, 0.0, ox_origin, scale)
        ox_body = self._project(0.0, -ox_diameter * 0.52, 0.0, ox_origin, scale)
        ox_tube_start = self._project(ox_length * 0.5, 0.0, 0.0, ox_origin, scale)
        fuel_front = self._project(fuel_length * 0.5, fuel_diameter * 0.18, 0.0, fuel_origin, scale)
        fuel_body = self._project(0.0, -fuel_diameter * 0.52, 0.0, fuel_origin, scale)
        fuel_tube_start = self._project(fuel_length * 0.5, 0.0, 0.0, fuel_origin, scale)
        ox_outlet = (ox_tube_start[0] + ox_feed_length * scale, ox_tube_start[1])
        fuel_outlet = (fuel_tube_start[0] + fuel_feed_length * scale, fuel_tube_start[1])
        ox_tube_width = max(1.6, ox_feed_diameter * scale)
        fuel_tube_width = max(1.6, fuel_feed_diameter * scale)
        self._draw_feed_connector(scene, (ox_tube_start[0], ox_tube_start[1]), ox_outlet, QT_PALETTE["oxidizer"], ox_tube_width)
        self._draw_feed_connector(scene, (fuel_tube_start[0], fuel_tube_start[1]), fuel_outlet, QT_PALETTE["fuel"], fuel_tube_width)
        self._draw_small_nozzle(scene, ox_outlet, QT_PALETTE["oxidizer"], ox_tube_width)
        self._draw_small_nozzle(scene, fuel_outlet, QT_PALETTE["fuel"], fuel_tube_width)
        self._draw_callout(scene, (ox_front[0], ox_front[1]), (552.0, 128.0), "Ox length {0} mm".format(_format_number(ox_length, 2)), QT_PALETTE["oxidizer"])
        self._draw_callout(scene, (ox_body[0], ox_body[1]), (154.0, 286.0), "Ox dia {0} mm".format(_format_number(ox_diameter, 2)), QT_PALETTE["text"])
        self._draw_callout(scene, (fuel_front[0], fuel_front[1]), (552.0, 344.0), "Fuel length {0} mm".format(_format_number(fuel_length, 2)), QT_PALETTE["fuel"])
        self._draw_callout(scene, (fuel_body[0], fuel_body[1]), (154.0, 500.0), "Fuel dia {0} mm".format(_format_number(fuel_diameter, 2)), QT_PALETTE["text"])
        self._metric(scene, 790, 104, "Ox Length", "{0} mm".format(_format_number(ox_length, 2)))
        self._metric(scene, 790, 166, "Fuel Length", "{0} mm".format(_format_number(fuel_length, 2)))
        self._metric(scene, 790, 228, "Feed Tube", "{0} / {1} mm".format(_format_number(ox_feed_diameter, 2), _format_number(fuel_feed_diameter, 2)))
        self._metric(scene, 790, 290, "Fuel Wall", "{0} mm".format(_format_number(values.get("fuel_tank_wall_thickness_mm", "--"), 2)))
        self._metric(scene, 790, 352, "Ox Wall", "{0} mm".format(_format_number(values.get("oxidizer_tank_wall_thickness_mm", "--"), 2)))

    def _draw_pumps(self, scene: QGraphicsScene, design) -> None:
        self._component_title(
            scene,
            "Feed Hardware",
            "Calculated impeller geometry with feed hardware context.",
        )
        values = dict(design.derived.engineering_values)
        if not design.inputs.use_pumps:
            self._add_text(scene, 86, 176, "Pressure-fed architecture selected.", QT_PALETTE["text"], 15, 420)
            self._draw_regulator_3d(scene, (324.0, 302.0), 1.0)
            self._metric(scene, 790, 104, "Fuel Tank", "{0} kPa".format(_format_number(values.get("fuel_tank_pressure_kpa", "--"), 2)))
            self._metric(scene, 790, 166, "Ox Tank", "{0} kPa".format(_format_number(values.get("oxidizer_tank_pressure_kpa", "--"), 2)))
            self._metric(scene, 790, 228, "Mode", "Blowdown")
            return

        fuel_geometry = self._pump_geometry(values, "fuel", 42.0)
        ox_geometry = self._pump_geometry(values, "oxidizer", 45.0)
        max_casing_diameter = max(1.0, fuel_geometry["casing_diameter_mm"], ox_geometry["casing_diameter_mm"])
        motor_length_mm = max(1.0, _safe_float(values.get("electric_motor_envelope_length_mm"), 80.0) or 80.0)
        motor_height_mm = max(1.0, _safe_float(values.get("electric_motor_envelope_height_mm"), 44.0) or 44.0)
        motor_depth_mm = max(1.0, _safe_float(values.get("electric_motor_envelope_depth_mm"), 48.0) or 48.0)
        display_scale = min(2.85, 188.0 / max_casing_diameter, 300.0 / motor_length_mm)
        fuel_geometry["display_scale"] = display_scale
        ox_geometry["display_scale"] = display_scale

        fuel_radius = self._pump_outer_radius(fuel_geometry)
        ox_radius = self._pump_outer_radius(ox_geometry)
        motor_width = motor_length_mm * display_scale
        motor_origin = (390.0, 412.0)
        self._draw_box_mesh(
            scene,
            motor_origin,
            motor_length_mm,
            motor_height_mm,
            motor_depth_mm,
            display_scale,
            "#26313A",
            QT_PALETTE["muted"],
        )
        self._add_text(scene, motor_origin[0] + 22.0, motor_origin[1] + 8.0, "Electric Motor", QT_PALETTE["text"], 10, 160)
        fuel_origin = (236.0, 256.0)
        ox_origin = (542.0, 256.0)
        self._draw_shaft(
            scene,
            (fuel_origin[0] + fuel_radius * 0.74, fuel_origin[1] + fuel_radius * 0.54),
            (motor_origin[0] - motor_width * 0.46, motor_origin[1] + 6.0),
            QT_PALETTE["muted"],
        )
        self._draw_shaft(
            scene,
            (ox_origin[0] - ox_radius * 0.66, ox_origin[1] + ox_radius * 0.54),
            (motor_origin[0] + motor_width * 0.46, motor_origin[1] + 6.0),
            QT_PALETTE["muted"],
        )
        self._draw_pump_assembly(scene, fuel_origin, fuel_geometry, QT_PALETTE["fuel"], "Fuel Pump")
        self._draw_pump_assembly(scene, ox_origin, ox_geometry, QT_PALETTE["oxidizer"], "Ox Pump")
        self._draw_pump_callouts(scene, fuel_origin, fuel_geometry, QT_PALETTE["fuel"], left_side=True)
        self._draw_pump_callouts(scene, ox_origin, ox_geometry, QT_PALETTE["oxidizer"], left_side=False)
        self._metric(scene, 790, 104, "Fuel Dia", "{0} mm".format(_format_number(fuel_geometry["diameter_mm"], 2)))
        self._metric(scene, 790, 166, "Ox Dia", "{0} mm".format(_format_number(ox_geometry["diameter_mm"], 2)))
        self._metric(
            scene,
            790,
            228,
            "Width",
            "{0} / {1} mm".format(
                _format_number(fuel_geometry["width_mm"], 2),
                _format_number(ox_geometry["width_mm"], 2),
            ),
        )
        self._metric(
            scene,
            790,
            290,
            "Blades",
            "Fuel {0} / Ox {1}".format(fuel_geometry["blade_count"], ox_geometry["blade_count"]),
        )
        self._metric(
            scene,
            790,
            352,
            "Blade Angle",
            "{0} / {1} deg".format(
                _format_number(fuel_geometry["blade_angle_deg"], 1),
                _format_number(ox_geometry["blade_angle_deg"], 1),
            ),
        )
        self._metric(
            scene,
            790,
            414,
            "Casing Dia",
            "{0} / {1} mm".format(
                _format_number(fuel_geometry["casing_diameter_mm"], 2),
                _format_number(ox_geometry["casing_diameter_mm"], 2),
            ),
        )
        self._metric(scene, 790, 476, "Head / Motor", "{0} kPa / {1} kW".format(
            _format_number(values.get("pump_differential_pressure_kpa", "--"), 1),
            _format_number(values.get("electric_motor_power_kw", "--"), 2),
        ))

    def _draw_injector(self, scene: QGraphicsScene, design) -> None:
        self._component_title(
            scene,
            "Injector",
            "Calculated injector face and element geometry.",
        )
        values = dict(design.derived.engineering_values)
        injector_type = str(design.inputs.injector_type).strip().lower()
        face_diameter = _safe_float(values.get("injector_face_diameter_mm"), design.inputs.chamber_diameter_mm) or design.inputs.chamber_diameter_mm
        face_thickness = _safe_float(values.get("injector_face_thickness_mm"), face_diameter * 0.075) or face_diameter * 0.075
        recess_diameter = _safe_float(values.get("injector_recess_diameter_mm"), face_diameter * 0.72) or face_diameter * 0.72
        face_radius = max(0.5, face_diameter * 0.5)
        display_scale = min(3.7, 260.0 / max(1.0, face_diameter))
        origin = (360.0, 308.0)
        face_radius_px = face_radius * display_scale
        recess_radius_px = max(1.0, recess_diameter * 0.5 * display_scale)
        face_thickness_px = max(9.0, face_thickness * display_scale)
        self._draw_disc_mesh(scene, face_radius_px, face_thickness_px, origin, 1.0, "#26313A", QT_PALETTE["muted"], center=(0.0, 0.0, 0.0))
        self._draw_disc_mesh(scene, recess_radius_px, face_thickness_px * 1.12, origin, 1.0, "#11151A", QT_PALETTE["border"], center=(0.0, 0.0, face_thickness_px * 0.18))
        if injector_type == "pintle":
            tip_diameter = _safe_float(values.get("pintle_tip_diameter_mm"), face_diameter * 0.24) or face_diameter * 0.24
            stem_diameter = _safe_float(values.get("pintle_stem_diameter_mm"), tip_diameter * 0.62) or tip_diameter * 0.62
            annulus_gap = _safe_float(values.get("pintle_annulus_gap_mm"), 1.0) or 1.0
            projection_length = _safe_float(values.get("pintle_projection_length_mm"), face_diameter * 0.55) or face_diameter * 0.55
            tip_radius_px = tip_diameter * 0.5 * display_scale
            stem_radius_px = stem_diameter * 0.5 * display_scale
            annulus_radius_px = (tip_diameter * 0.5 + annulus_gap) * display_scale
            projection_px = max(face_thickness_px * 1.1, projection_length * display_scale)
            self._draw_disc_mesh(scene, annulus_radius_px, face_thickness_px * 1.24, origin, 1.0, "#0D0F12", QT_PALETTE["oxidizer"], center=(0.0, 0.0, face_thickness_px * 0.60))
            self._draw_disc_mesh(scene, stem_radius_px, projection_px, origin, 1.0, "#D6D3C7", "#FFFFFF", center=(0.0, 0.0, projection_px * 0.42))
            self._draw_disc_mesh(scene, tip_radius_px, max(10.0, face_thickness_px * 1.35), origin, 1.0, QT_PALETTE["fuel"], "#F3C26E", center=(0.0, 0.0, projection_px * 0.94))
            tip_point = self._project(0.0, -tip_radius_px, projection_px * 0.94, origin, 1.0)
            gap_point = self._project(annulus_radius_px, 0.0, face_thickness_px * 0.60, origin, 1.0)
            self._draw_callout(scene, (tip_point[0], tip_point[1]), (120.0, 472.0), "Pintle tip {0} mm".format(_format_number(tip_diameter, 2)), QT_PALETTE["fuel"])
            self._draw_callout(scene, (gap_point[0], gap_point[1]), (552.0, 172.0), "Annulus gap {0} mm".format(_format_number(annulus_gap, 2)), QT_PALETTE["oxidizer"])
            self._metric(scene, 790, 290, "Tip / Gap", "{0} / {1} mm".format(_format_number(tip_diameter, 2), _format_number(annulus_gap, 2)))
            self._metric(scene, 790, 414, "Projection", "{0} mm".format(_format_number(projection_length, 2)))
        else:
            orifice_diameter = _safe_float(values.get("impinging_orifice_diameter_mm"), 1.0) or 1.0
            impinging_angle = _safe_float(values.get("impinging_angle_deg"), 30.0) or 30.0
            pair_spacing = _safe_float(values.get("impinging_pair_spacing_mm"), face_diameter * 0.085) or face_diameter * 0.085
            ring_diameter = _safe_float(values.get("injector_element_ring_diameter_mm"), face_diameter * 0.72) or face_diameter * 0.72
            convergence_height = _safe_float(values.get("impinging_convergence_height_mm"), face_diameter * 0.12) or face_diameter * 0.12
            element_count = max(2, int(round(_safe_float(values.get("impinging_element_count"), 2.0) or 2.0)))
            orifice_count = max(element_count * 2, int(round(_safe_float(values.get("impinging_orifice_count"), element_count * 2) or element_count * 2)))
            port_radius_px = max(2.6, orifice_diameter * 0.5 * display_scale)
            spacing_px = pair_spacing * display_scale
            ring_radius_px = max(1.0, ring_diameter * 0.5 * display_scale)
            jet_pen = QPen(QColor(QT_PALETTE["oxidizer"]), 1.0, Qt.SolidLine, Qt.RoundCap)
            jet_pen.setCosmetic(True)
            first_port_point = None
            orifice_positions: List[Tuple[float, float, float]] = []
            convergence_lift = max(1.0, convergence_height * display_scale)
            for pair_index in range(element_count):
                base_angle = self._spin_phase() + 2.0 * math.pi * pair_index / element_count
                center_x = ring_radius_px * math.cos(base_angle)
                center_y = ring_radius_px * math.sin(base_angle)
                tangent_x = -math.sin(base_angle)
                tangent_y = math.cos(base_angle)
                convergence = self._project(center_x, center_y, face_thickness_px + convergence_lift, origin, 1.0)
                for side in (-1.0, 1.0):
                    port_x = center_x + tangent_x * spacing_px * 0.5 * side
                    port_y = center_y + tangent_y * spacing_px * 0.5 * side
                    port_z = face_thickness_px * 1.04
                    port = self._project(port_x, port_y, port_z, origin, 1.0)
                    orifice_positions.append((port_x, port_y, port_z))
                    scene.addLine(port[0], port[1], convergence[0], convergence[1], jet_pen)
                    if first_port_point is None:
                        first_port_point = port
            for port_x, port_y, port_z in orifice_positions:
                self._draw_projected_orifice(scene, origin, port_x, port_y, port_z, port_radius_px, QT_PALETTE["oxidizer"])
            port_point = first_port_point or self._project(ring_radius_px, 0.0, face_thickness_px, origin, 1.0)
            self._draw_callout(scene, (port_point[0], port_point[1]), (552.0, 172.0), "Orifice dia {0} mm".format(_format_number(orifice_diameter, 2)), QT_PALETTE["oxidizer"])
            ring_point = self._project(-ring_radius_px * 0.65, -ring_radius_px * 0.76, face_thickness_px * 0.88, origin, 1.0)
            self._draw_callout(scene, (ring_point[0], ring_point[1]), (132.0, 438.0), "Ring dia {0} mm".format(_format_number(ring_diameter, 2)), QT_PALETTE["muted"])
            self._metric(scene, 790, 290, "Orifice", "{0} mm".format(_format_number(orifice_diameter, 2)))
            self._metric(
                scene,
                790,
                414,
                "Angle / Spacing",
                "{0} deg / {1} mm".format(_format_number(impinging_angle, 1), _format_number(pair_spacing, 2)),
            )
            self._metric(scene, 790, 476, "Holes", str(orifice_count))
            self._metric(scene, 790, 538, "Ring Dia", "{0} mm".format(_format_number(ring_diameter, 2)))
        face_point = self._project(face_radius_px, 0.0, 0.0, origin, 1.0)
        thickness_point = self._project(0.0, face_radius_px * 0.70, face_thickness_px * 0.56, origin, 1.0)
        self._draw_callout(scene, (face_point[0], face_point[1]), (552.0, 392.0), "Face dia {0} mm".format(_format_number(face_diameter, 2)), QT_PALETTE["text"])
        self._draw_callout(scene, (thickness_point[0], thickness_point[1]), (126.0, 150.0), "Face thick {0} mm".format(_format_number(face_thickness, 2)), QT_PALETTE["muted"])
        self._add_text(scene, 252, 520, _display_injector_name(injector_type), QT_PALETTE["text"], 11, 220)
        self._metric(scene, 790, 104, "Injector", _display_injector_name(injector_type))
        self._metric(scene, 790, 166, "Face Dia", "{0} mm".format(_format_number(face_diameter, 2)))
        self._metric(scene, 790, 228, "Face Thick", "{0} mm".format(_format_number(face_thickness, 2)))
        self._metric(scene, 790, 352, "Drop", "{0} kPa".format(_format_number(values.get("injector_pressure_drop_kpa", "--"), 2)))

    def _component_title(self, scene: QGraphicsScene, title: str, subtitle: str) -> None:
        title_item = scene.addText(title, QFont("Segoe UI", 13, QFont.Bold))
        title_item.setDefaultTextColor(QColor(QT_PALETTE["text"]))
        title_item.setPos(48, 28)
        self._add_text(scene, 48, 62, subtitle, QT_PALETTE["muted"], 9, 640)

    def _add_round_rect(self, scene: QGraphicsScene, rect: QRectF, radius: float, pen: QPen, brush: QBrush) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        item.setBrush(brush)
        scene.addItem(item)

    def _project(self, x: float, y: float, z: float, origin: Tuple[float, float], scale: float) -> Tuple[float, float, float]:
        yaw = math.radians(-18.0)
        tilt = math.radians(18.0)
        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        x1 = x * cos_yaw + z * sin_yaw
        z1 = -x * sin_yaw + z * cos_yaw
        y2 = y * math.cos(tilt) - z1 * math.sin(tilt)
        z2 = y * math.sin(tilt) + z1 * math.cos(tilt)
        return (
            origin[0] + x1 * scale + z2 * scale * 0.10,
            origin[1] - y2 * scale + z2 * scale * 0.05,
            z2,
        )

    def _spin_phase(self) -> float:
        return math.radians(self._angle_deg)

    def _draw_ring_mesh(
        self,
        scene: QGraphicsScene,
        rings: List[List[Tuple[float, float, float]]],
        origin: Tuple[float, float],
        scale: float,
        color: str,
        wire_color: str,
        cap_ends: bool = False,
    ) -> None:
        if len(rings) < 2:
            return
        projected = [[self._project(x, y, z, origin, scale) for x, y, z in ring] for ring in rings]
        surfaces = []
        for ring_index in range(len(projected) - 1):
            for segment_index in range(len(projected[ring_index])):
                p0 = projected[ring_index][segment_index]
                p1 = projected[ring_index][(segment_index + 1) % len(projected[ring_index])]
                p2 = projected[ring_index + 1][(segment_index + 1) % len(projected[ring_index + 1])]
                p3 = projected[ring_index + 1][segment_index]
                depth = (p0[2] + p1[2] + p2[2] + p3[2]) * 0.25
                surfaces.append((depth, [p0, p1, p2, p3]))
        if cap_ends:
            for ring in (projected[0], projected[-1]):
                surfaces.append((sum(point[2] for point in ring) / max(1, len(ring)), ring))
        depths = [item[0] for item in surfaces]
        min_depth = min(depths)
        span = max(1e-6, max(depths) - min_depth)
        base = QColor(color)
        for depth, points in sorted(surfaces, key=lambda item: item[0]):
            ratio = (depth - min_depth) / span
            shade = self._shade(base, ratio)
            polygon = QPolygonF([QPointF(point[0], point[1]) for point in points])
            scene.addPolygon(polygon, QPen(QColor(wire_color), 0.35), QBrush(shade))
        wire_pen = QPen(QColor(wire_color), 0.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        wire_pen.setCosmetic(True)
        for ring_index, ring in enumerate(projected):
            if ring_index % 3 != 0 and ring_index not in (0, len(projected) - 1):
                continue
            for segment_index in range(len(ring)):
                p0 = ring[segment_index]
                p1 = ring[(segment_index + 1) % len(ring)]
                scene.addLine(p0[0], p0[1], p1[0], p1[1], wire_pen)

    def _shade(self, base: QColor, ratio: float) -> QColor:
        factor = 0.56 + 0.52 * max(0.0, min(1.0, ratio))
        return QColor(
            min(255, int(base.red() * factor)),
            min(255, int(base.green() * factor)),
            min(255, int(base.blue() * factor)),
        )

    def _axial_cylinder_rings(
        self,
        length: float,
        radius: float,
        slices: int = 10,
        segments: int = 30,
        phase: float = 0.0,
    ) -> List[List[Tuple[float, float, float]]]:
        rings = []
        for slice_index in range(slices + 1):
            x = -length * 0.5 + length * slice_index / max(1, slices)
            ring = []
            for segment_index in range(segments):
                theta = phase + 2.0 * math.pi * segment_index / segments
                ring.append((x, radius * math.cos(theta), radius * math.sin(theta)))
            rings.append(ring)
        return rings

    def _draw_axial_cylinder_mesh(
        self,
        scene: QGraphicsScene,
        length: float,
        radius: float,
        origin: Tuple[float, float],
        scale: float,
        color: str,
        wire_color: str,
    ) -> None:
        self._draw_ring_mesh(
            scene,
            self._axial_cylinder_rings(length, radius, phase=self._spin_phase()),
            origin,
            scale,
            color,
            wire_color,
            cap_ends=True,
        )

    def _disc_rings(
        self,
        radius_mm: float,
        depth_mm: float,
        center: Tuple[float, float, float],
        segments: int = 32,
    ) -> List[List[Tuple[float, float, float]]]:
        rings = []
        phase = self._spin_phase()
        for z in (-depth_mm * 0.5, depth_mm * 0.5):
            ring = []
            for index in range(segments):
                theta = phase + 2.0 * math.pi * index / segments
                ring.append((center[0] + radius_mm * math.cos(theta), center[1] + radius_mm * math.sin(theta), center[2] + z))
            rings.append(ring)
        return rings

    def _draw_disc_mesh(
        self,
        scene: QGraphicsScene,
        radius_mm: float,
        depth_mm: float,
        origin: Tuple[float, float],
        scale: float,
        color: str,
        wire_color: str,
        center: Tuple[float, float, float],
    ) -> None:
        self._draw_ring_mesh(scene, self._disc_rings(radius_mm, depth_mm, center), origin, scale, color, wire_color, cap_ends=True)

    def _draw_box_mesh(
        self,
        scene: QGraphicsScene,
        origin: Tuple[float, float],
        width: float,
        height: float,
        depth: float,
        scale: float,
        color: str,
        wire_color: str,
    ) -> None:
        hw, hh, hd = width * 0.5, height * 0.5, depth * 0.5
        vertices = {
            "lbf": (-hw, -hh, -hd),
            "rbf": (hw, -hh, -hd),
            "rtf": (hw, hh, -hd),
            "ltf": (-hw, hh, -hd),
            "lbb": (-hw, -hh, hd),
            "rbb": (hw, -hh, hd),
            "rtb": (hw, hh, hd),
            "ltb": (-hw, hh, hd),
        }
        faces = [
            ("front", ("lbf", "rbf", "rtf", "ltf")),
            ("back", ("lbb", "rbb", "rtb", "ltb")),
            ("top", ("ltf", "rtf", "rtb", "ltb")),
            ("bottom", ("lbf", "rbf", "rbb", "lbb")),
            ("left", ("lbf", "ltf", "ltb", "lbb")),
            ("right", ("rbf", "rtf", "rtb", "rbb")),
        ]
        projected = {key: self._project(*point, origin, scale) for key, point in vertices.items()}
        depths = []
        surfaces = []
        for _name, keys in faces:
            points = [projected[key] for key in keys]
            depth_value = sum(point[2] for point in points) / len(points)
            depths.append(depth_value)
            surfaces.append((depth_value, points))
        base = QColor(color)
        span = max(1e-6, max(depths) - min(depths))
        for depth_value, points in sorted(surfaces, key=lambda item: item[0]):
            shade = self._shade(base, (depth_value - min(depths)) / span)
            scene.addPolygon(QPolygonF([QPointF(point[0], point[1]) for point in points]), QPen(QColor(wire_color), 0.6), QBrush(shade))

    def _pump_geometry(self, values: Dict[str, object], prefix: str, fallback_diameter: float) -> Dict[str, float]:
        diameter = max(1.0, _safe_float(values.get(f"{prefix}_impeller_diameter_mm"), fallback_diameter) or fallback_diameter)
        width = max(0.5, _safe_float(values.get(f"{prefix}_impeller_width_mm"), diameter * 0.18) or diameter * 0.18)
        hub_diameter = max(0.5, _safe_float(values.get(f"{prefix}_impeller_hub_diameter_mm"), diameter * 0.42) or diameter * 0.42)
        eye_diameter = max(0.5, _safe_float(values.get(f"{prefix}_impeller_eye_diameter_mm"), diameter * 0.48) or diameter * 0.48)
        blade_count = int(round(_safe_float(values.get(f"{prefix}_impeller_blade_count"), 6.0) or 6.0))
        blade_angle = _safe_float(values.get(f"{prefix}_impeller_blade_angle_deg"), 22.0) or 22.0
        blade_thickness = max(0.1, _safe_float(values.get(f"{prefix}_impeller_blade_thickness_mm"), width * 0.18) or width * 0.18)
        tip_clearance = max(0.0, _safe_float(values.get(f"{prefix}_impeller_tip_clearance_mm"), diameter * 0.0075) or 0.0)
        casing_diameter = max(
            diameter + 2.0 * tip_clearance,
            _safe_float(
                values.get(f"{prefix}_pump_casing_diameter_mm"),
                diameter + 2.0 * tip_clearance + max(4.0, width * 0.35),
            )
            or diameter + 2.0 * tip_clearance,
        )
        casing_depth = max(
            width,
            _safe_float(
                values.get(f"{prefix}_pump_casing_depth_mm"),
                width + 2.0 * max(tip_clearance, blade_thickness),
            )
            or width,
        )
        return {
            "diameter_mm": diameter,
            "width_mm": width,
            "hub_diameter_mm": hub_diameter,
            "eye_diameter_mm": eye_diameter,
            "blade_count": max(1, blade_count),
            "blade_angle_deg": blade_angle,
            "blade_thickness_mm": blade_thickness,
            "tip_clearance_mm": tip_clearance,
            "casing_diameter_mm": casing_diameter,
            "casing_depth_mm": casing_depth,
            "display_scale": 1.0,
        }

    def _pump_outer_radius(self, geometry: Dict[str, float]) -> float:
        scale = geometry.get("display_scale", 1.0)
        return geometry["casing_diameter_mm"] * 0.5 * scale

    def _draw_pump_assembly(
        self,
        scene: QGraphicsScene,
        origin: Tuple[float, float],
        geometry: Dict[str, float],
        color: str,
        label: str,
    ) -> None:
        scale = geometry["display_scale"]
        casing_radius = self._pump_outer_radius(geometry)
        casing_depth = max(4.0, geometry["casing_depth_mm"] * scale)
        self._draw_disc_mesh(
            scene,
            casing_radius,
            casing_depth,
            origin,
            1.0,
            "#26313A",
            QT_PALETTE["border"],
            center=(0.0, 0.0, -casing_depth * 0.20),
        )
        self._draw_projected_ring(scene, origin, casing_radius, casing_depth * 0.12, QColor(QT_PALETTE["border"]), 96, 1.2)
        self._draw_impeller_mesh(scene, origin, geometry, color)
        self._add_text(scene, origin[0] - 68, 540, label, QT_PALETTE["text"], 10, 150)

    def _draw_pump_callouts(
        self,
        scene: QGraphicsScene,
        origin: Tuple[float, float],
        geometry: Dict[str, float],
        color: str,
        left_side: bool,
    ) -> None:
        scale = geometry["display_scale"]
        radius = geometry["diameter_mm"] * 0.5 * scale
        casing_radius = geometry["casing_diameter_mm"] * 0.5 * scale
        width = geometry["width_mm"] * scale
        eye_radius = geometry["eye_diameter_mm"] * 0.5 * scale
        casing_point = self._project(casing_radius, 0.0, max(8.0, geometry["casing_depth_mm"] * scale * 0.24), origin, 1.0)
        dia_point = self._project(radius, 0.0, max(8.0, width * 0.55), origin, 1.0)
        eye_point = self._project(eye_radius, 0.0, max(12.0, width * 1.1), origin, 1.0)
        if left_side:
            casing_label = (28.0, 118.0)
            dia_label = (28.0, 352.0)
            eye_label = (28.0, 414.0)
        else:
            casing_label = (628.0, 118.0)
            dia_label = (628.0, 352.0)
            eye_label = (628.0, 414.0)
        self._draw_callout(
            scene,
            (casing_point[0], casing_point[1]),
            casing_label,
            "Casing {0} mm".format(_format_number(geometry["casing_diameter_mm"], 2)),
            color,
        )
        self._draw_callout(
            scene,
            (dia_point[0], dia_point[1]),
            dia_label,
            "Impeller {0} mm".format(_format_number(geometry["diameter_mm"], 2)),
            color,
        )
        self._draw_callout(
            scene,
            (eye_point[0], eye_point[1]),
            eye_label,
            "Eye {0} mm".format(_format_number(geometry["eye_diameter_mm"], 2)),
            QT_PALETTE["text"],
        )
        width_label_y = 508.0
        self._draw_callout(
            scene,
            (origin[0], origin[1] + radius * 0.72),
            (dia_label[0], width_label_y),
            "Width {0} mm / {1}".format(_format_number(geometry["width_mm"], 2), geometry["blade_count"]),
            QT_PALETTE["muted"],
        )

    def _draw_impeller_mesh(
        self,
        scene: QGraphicsScene,
        origin: Tuple[float, float],
        geometry: Dict[str, float],
        accent_color: str,
    ) -> None:
        scale = geometry["display_scale"]
        radius = geometry["diameter_mm"] * 0.5 * scale
        width = max(8.0, geometry["width_mm"] * scale)
        hub_radius = geometry["hub_diameter_mm"] * 0.5 * scale
        eye_radius = geometry["eye_diameter_mm"] * 0.5 * scale
        blade_count = int(geometry["blade_count"])
        blade_angle_rad = math.radians(geometry["blade_angle_deg"])
        blade_thickness = max(1.8, geometry["blade_thickness_mm"] * scale)
        tip_clearance = max(0.8, geometry["tip_clearance_mm"] * scale)
        base_depth = max(16.0, width * 0.72)
        front_z = base_depth * 0.92

        self._draw_disc_mesh(scene, radius, base_depth, origin, 1.0, "#7A8079", "#D8D8D0", center=(0.0, 0.0, front_z * 0.30))
        groove_start = max(eye_radius * 1.08, hub_radius * 1.04, radius * 0.34)
        groove_span = max(1.0, radius * 0.94 - groove_start)
        for groove_index in range(7):
            groove_radius = groove_start + groove_span * groove_index / 6.0
            alpha = 34 + groove_index * 6
            self._draw_projected_ring(scene, origin, groove_radius, front_z * 0.98, QColor(235, 236, 224, alpha), 96)

        surfaces = []
        blade_spin = math.radians(self._angle_deg * 2.0)
        inner_radius = min(radius * 0.84, max(hub_radius * 1.04, eye_radius * 1.02))
        outer_radius = max(inner_radius + blade_thickness * 2.0, radius - tip_clearance)
        pitch = 2.0 * math.pi / max(1, blade_count)
        angular_width = min(pitch * 0.42, max(pitch * 0.18, blade_thickness / max(1.0, inner_radius)))
        blade_lift = max(7.0, width * 0.34)
        for blade_index in range(blade_count):
            base_angle = blade_spin + 2.0 * math.pi * blade_index / blade_count
            surfaces.extend(
                self._impeller_blade_surfaces(
                    origin,
                    inner_radius,
                    outer_radius,
                    base_angle,
                    angular_width=angular_width,
                    twist=-blade_angle_rad,
                    z_front=front_z,
                    blade_lift=blade_lift,
                    blade_thickness=blade_thickness,
                    metal="#C8C5B9",
                )
            )

        if surfaces:
            depths = [depth for depth, _points, _color in surfaces]
            min_depth = min(depths)
            span = max(1e-6, max(depths) - min_depth)
            for depth, points, color in sorted(surfaces, key=lambda item: item[0]):
                shade = self._shade(QColor(color), (depth - min_depth) / span)
                scene.addPolygon(
                    QPolygonF([QPointF(point[0], point[1]) for point in points]),
                    QPen(QColor("#F1F0E8"), 0.45),
                    QBrush(shade),
                )

        recess = QColor("#08090B")
        recess.setAlpha(170)
        self._draw_projected_ring(scene, origin, eye_radius, front_z + blade_lift * 0.60, recess, 96, max(2.2, blade_thickness * 0.42))
        self._draw_disc_mesh(
            scene,
            hub_radius,
            max(10.0, width * 0.84),
            origin,
            1.0,
            "#D6D3C7",
            "#FFFFFF",
            center=(0.0, 0.0, front_z + blade_lift * 0.78),
        )
        self._draw_projected_ring(scene, origin, eye_radius, front_z + blade_lift * 1.12, QColor("#F6F4EA"), 96, 1.8)
        bore = self._project(0.0, 0.0, front_z + blade_lift * 1.18, origin, 1.0)
        bore_radius = max(5.0, min(hub_radius * 0.42, eye_radius * 0.34))
        scene.addEllipse(
            bore[0] - bore_radius,
            bore[1] - bore_radius * 0.70,
            bore_radius * 2.0,
            bore_radius * 1.40,
            QPen(QColor("#050608"), 2),
            QBrush(QColor("#050608")),
        )
        accent = QColor(accent_color)
        accent.setAlpha(150)
        self._draw_projected_ring(scene, origin, radius, front_z * 1.02, accent, 96, 2.0)

    def _impeller_blade_surfaces(
        self,
        origin: Tuple[float, float],
        inner_radius: float,
        outer_radius: float,
        base_angle: float,
        angular_width: float,
        twist: float,
        z_front: float,
        blade_lift: float,
        blade_thickness: float,
        metal: str,
    ) -> List[Tuple[float, List[Tuple[float, float, float]], str]]:
        surfaces = []
        segments = 6
        for segment_index in range(segments):
            t0 = segment_index / segments
            t1 = (segment_index + 1) / segments
            r0 = inner_radius + (outer_radius - inner_radius) * t0
            r1 = inner_radius + (outer_radius - inner_radius) * t1
            a0 = base_angle + twist * t0
            a1 = base_angle + twist * t1
            w0 = angular_width * (0.66 + 0.28 * t0)
            w1 = angular_width * (0.66 + 0.28 * t1)
            blade_lift_0 = z_front + blade_lift * t0
            blade_lift_1 = z_front + blade_lift * t1
            raw_points = [
                (r0 * math.cos(a0), r0 * math.sin(a0), blade_lift_0),
                (r1 * math.cos(a1), r1 * math.sin(a1), blade_lift_1),
                (r1 * math.cos(a1 + w1), r1 * math.sin(a1 + w1), blade_lift_1 + blade_thickness),
                (r0 * math.cos(a0 + w0), r0 * math.sin(a0 + w0), blade_lift_0 + blade_thickness * 0.72),
            ]
            points = [self._project(x, y, z, origin, 1.0) for x, y, z in raw_points]
            depth = sum(point[2] for point in points) / len(points)
            surfaces.append((depth, points, metal))
        return surfaces

    def _draw_projected_ring(
        self,
        scene: QGraphicsScene,
        origin: Tuple[float, float],
        radius: float,
        z: float,
        color: QColor,
        segments: int,
        width: float = 0.8,
    ) -> None:
        path = QPainterPath()
        first = self._project(radius, 0.0, z, origin, 1.0)
        path.moveTo(first[0], first[1])
        for index in range(1, segments + 1):
            theta = 2.0 * math.pi * index / segments
            point = self._project(radius * math.cos(theta), radius * math.sin(theta), z, origin, 1.0)
            path.lineTo(point[0], point[1])
        scene.addPath(path, QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def _draw_projected_orifice(
        self,
        scene: QGraphicsScene,
        origin: Tuple[float, float],
        x: float,
        y: float,
        z: float,
        radius: float,
        color: str,
    ) -> Tuple[float, float, float]:
        projected = self._project(x, y, z, origin, 1.0)
        apparent = max(3.2, radius * 1.18)
        rim = QColor(color)
        rim.setAlpha(235)
        shadow = QColor("#030507")
        scene.addEllipse(
            projected[0] - apparent - 1.1,
            projected[1] - apparent - 1.1,
            (apparent + 1.1) * 2.0,
            (apparent + 1.1) * 2.0,
            QPen(QColor("#030507"), 0.8),
            QBrush(QColor("#030507")),
        )
        scene.addEllipse(
            projected[0] - apparent,
            projected[1] - apparent,
            apparent * 2.0,
            apparent * 2.0,
            QPen(rim, 1.4),
            QBrush(QColor("#071018")),
        )
        scene.addEllipse(
            projected[0] - apparent * 0.35,
            projected[1] - apparent * 0.35,
            apparent * 0.70,
            apparent * 0.70,
            QPen(QColor(color), 0.7),
            QBrush(rim),
        )
        return projected

    def _draw_projected_port(self, scene: QGraphicsScene, origin: Tuple[float, float], ring_radius: float, angle: float, port_radius: float, color: str) -> None:
        projected = self._project(ring_radius * math.cos(angle), ring_radius * math.sin(angle), 42.0, origin, 1.0)
        apparent = max(4.0, port_radius * (0.78 + 0.18 * math.cos(math.radians(self._angle_deg))))
        scene.addEllipse(projected[0] - apparent, projected[1] - apparent, apparent * 2.0, apparent * 2.0, QPen(QColor(color)), QBrush(QColor(color)))

    def _draw_shaft(self, scene: QGraphicsScene, start: Tuple[float, float], end: Tuple[float, float], color: str) -> None:
        pen = QPen(QColor(color), 5, Qt.SolidLine, Qt.RoundCap)
        scene.addLine(start[0], start[1], end[0], end[1], pen)
        shine = QColor("#FFFFFF")
        shine.setAlpha(65)
        scene.addLine(start[0] + 2, start[1] - 2, end[0] + 2, end[1] - 2, QPen(shine, 1.4, Qt.SolidLine, Qt.RoundCap))

    def _draw_regulator_3d(self, scene: QGraphicsScene, origin: Tuple[float, float], scale: float) -> None:
        self._draw_box_mesh(scene, origin, 250.0, 88.0, 74.0, scale, "#26313A", QT_PALETTE["muted"])
        self._draw_disc_mesh(scene, 36.0, 24.0, (origin[0] - 76.0, origin[1] - 2.0), scale, QT_PALETTE["accent"], QT_PALETTE["accent_hover"], center=(0.0, 0.0, 36.0))
        self._add_text(scene, origin[0] - 40, origin[1] - 14, "Regulated Feed", QT_PALETTE["text"], 11, 160)

    def _draw_small_nozzle(self, scene: QGraphicsScene, origin: Tuple[float, float], color: str, line_width: float = 4.0) -> None:
        width = max(1.2, line_width)
        stem = max(20.0, width * 7.0)
        branch = max(10.0, width * 3.0)
        pen = QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap)
        scene.addLine(origin[0] - stem * 0.45, origin[1], origin[0] + stem * 0.55, origin[1], pen)
        scene.addLine(origin[0] + stem * 0.55, origin[1], origin[0] + stem * 0.55 + branch, origin[1] - branch * 0.70, pen)
        scene.addLine(origin[0] + stem * 0.55, origin[1], origin[0] + stem * 0.55 + branch, origin[1] + branch * 0.70, pen)

    def _draw_feed_connector(
        self,
        scene: QGraphicsScene,
        start: Tuple[float, float],
        end: Tuple[float, float],
        color: str,
        line_width: float = 4.0,
    ) -> None:
        pen = QPen(QColor(color), max(1.2, line_width), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        path = QPainterPath()
        path.moveTo(start[0], start[1])
        bend_x = start[0] + (end[0] - start[0]) * 0.54
        path.lineTo(bend_x, start[1])
        path.lineTo(bend_x, end[1])
        path.lineTo(end[0], end[1])
        scene.addPath(path, pen)
        highlight = QColor("#FFFFFF")
        highlight.setAlpha(62)
        scene.addPath(path, QPen(highlight, max(0.6, line_width * 0.24), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def _draw_callout(
        self,
        scene: QGraphicsScene,
        start: Tuple[float, float],
        label_pos: Tuple[float, float],
        label: str,
        color: str,
    ) -> None:
        line_color = QColor(color)
        line_color.setAlpha(210)
        pen = QPen(line_color, 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        font = QFont("Segoe UI", 8)
        metrics = QFontMetrics(font)
        width = max(90.0, float(metrics.horizontalAdvance(label)) + 22.0)
        label_x = min(max(24.0, label_pos[0]), 770.0 - width)
        label_y = max(96.0, min(526.0, label_pos[1]))
        height = 25.0
        label_rect = self._place_callout_rect(QRectF(label_x - 2.0, label_y - height * 0.5, width, height))
        label_x = label_rect.x() + 2.0
        label_y = label_rect.center().y()
        elbow_x = start[0] + (label_x - start[0]) * 0.55
        elbow_y = label_y
        path = QPainterPath()
        path.moveTo(start[0], start[1])
        path.lineTo(elbow_x, start[1])
        path.lineTo(elbow_x, elbow_y)
        path.lineTo(label_x, label_y)
        scene.addPath(path, pen)
        scene.addEllipse(start[0] - 3.0, start[1] - 3.0, 6.0, 6.0, QPen(line_color, 1.0), QBrush(line_color))
        box_path = QPainterPath()
        box_path.addRoundedRect(label_rect, 6.0, 6.0)
        box_item = QGraphicsPathItem(box_path)
        box_item.setBrush(QColor("#11151A"))
        box_item.setPen(QPen(QColor(QT_PALETTE["border_soft"]), 1.0))
        scene.addItem(box_item)
        text_item = scene.addSimpleText(label, font)
        text_item.setBrush(QColor(QT_PALETTE["text"]))
        text_item.setPos(label_x + 8.0, label_y - 8.0)

    def _place_callout_rect(self, requested: QRectF) -> QRectF:
        rect = QRectF(requested)
        min_y = 92.0
        max_y = 566.0 - rect.height()
        padded_existing = [
            QRectF(existing.x() - 8.0, existing.y() - 8.0, existing.width() + 16.0, existing.height() + 16.0)
            for existing in self._callout_rects
        ]
        for _attempt in range(28):
            if not any(rect.intersects(existing) for existing in padded_existing):
                self._callout_rects.append(QRectF(rect))
                return rect
            rect.moveTop(min(max_y, rect.y() + rect.height() + 8.0))
        rect = QRectF(requested)
        for _attempt in range(28):
            if not any(rect.intersects(existing) for existing in padded_existing):
                self._callout_rects.append(QRectF(rect))
                return rect
            rect.moveTop(max(min_y, rect.y() - rect.height() - 8.0))
        self._callout_rects.append(QRectF(rect))
        return rect

    def _add_text(self, scene: QGraphicsScene, x: float, y: float, text: str, color: str, size: int, width: float) -> None:
        item = QGraphicsTextItem(text)
        item.setFont(QFont("Segoe UI", size))
        item.setDefaultTextColor(QColor(color))
        item.setTextWidth(width)
        item.document().setDocumentMargin(0)
        item.setPos(x, y)
        scene.addItem(item)

    def _fit_font_size(self, text: str, max_width: float, preferred: int, minimum: int = 7) -> int:
        for size in range(preferred, minimum - 1, -1):
            if QFontMetrics(QFont("Segoe UI", size)).horizontalAdvance(text) <= max_width:
                return size
        return minimum

    def _metric(self, scene: QGraphicsScene, x: float, y: float, label: str, value: str) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, 244, 46), 8, 8)
        item = QGraphicsPathItem(path)
        item.setBrush(QColor("#11151A"))
        item.setPen(QPen(QColor(QT_PALETTE["border_soft"]), 1))
        scene.addItem(item)
        content_width = 216.0
        label_size = self._fit_font_size(label, content_width, 8, 7)
        value_size = self._fit_font_size(value, content_width, 9, 7)
        self._add_text(scene, x + 14, y + 6, label, QT_PALETTE["muted"], label_size, content_width)
        self._add_text(scene, x + 14, y + 22, value, QT_PALETTE["text"], value_size, content_width)


class StanThrustQtWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("StanThrust")
        self.resize(1560, 980)
        self.setMinimumSize(1280, 820)

        self.current_design = None
        self.current_objective_report = None
        self.current_validation_report = None
        self.current_combustion_result = None
        self.current_solver_interface_result = None
        self.current_coupled_cycle_result = None
        self.current_structural_result = None
        self.current_ga_result = None
        self.current_ga_candidate_state = None
        self.current_input_state = {}
        self.combustion_progress_feed = []
        self.solver_assumptions = get_default_solver_assumptions()

        self.widgets: Dict[str, object] = {}
        self.measurement_tables: Dict[str, QTableWidget] = {}
        self.control_scroll_area: Optional[QScrollArea] = None
        self.control_sections: Dict[str, QWidget] = {}
        self.control_nav_buttons: Dict[str, QPushButton] = {}
        self.plot_cards: Dict[str, EngineeringPlotCard] = {}
        self.output_tabs: Optional[QTabWidget] = None
        self.summary_text: Optional[QPlainTextEdit] = None
        self.metadata_text: Optional[QPlainTextEdit] = None
        self.status_banner: Optional[StatusBanner] = None
        self.schematic_view: Optional[SchematicView] = None
        self.model_3d_view: Optional[Model3DView] = None
        self.model_3d_views: Dict[str, Model3DView] = {}
        self.ga_status_label: Optional[QLabel] = None
        self.cfd_status_label: Optional[QLabel] = None
        self.solver_stage_label: Optional[QLabel] = None
        self.solver_residual_label: Optional[QLabel] = None
        self.export_status_label: Optional[QLabel] = None
        self.progress_bar: Optional[QProgressBar] = None
        self.step_progress_bars: Dict[str, QProgressBar] = {}
        self.step_status_labels: Dict[str, QLabel] = {}
        self.solver_log_lines: List[str] = []
        self.diagnostic_snapshot_lines: List[str] = []
        self.solver_error_counter = 0
        self.result_cards: Dict[str, MetricCard] = {}
        self.cfd_cards: Dict[str, MetricCard] = {}
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._apply_preview_refresh)
        self._suspend_preview = False

        self._apply_styles()
        self._build_ui()
        self.reset_form()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #0D0F12;
                color: #F2F5F3;
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            QLabel, QCheckBox, QRadioButton {
                background: transparent;
            }
            QFrame#panel, QFrame#card, QGroupBox {
                background: #15181D;
                border: 1px solid #252B33;
                border-radius: 8px;
            }
            QFrame#card {
                background: #1B2026;
                border: 1px solid #343B44;
            }
            QFrame#heroBrand {
                background: #1B2026;
                border: 1px solid #343B44;
                border-radius: 8px;
            }
            QLabel#logoTitle {
                color: #F2F5F3;
                font-size: 24pt;
                font-weight: 700;
            }
            QLabel#logoSubtitle {
                color: #A9B2AD;
                font-size: 9pt;
                font-weight: 700;
            }
            QLabel#sectionKicker {
                color: #A9B2AD;
                font-size: 8.5pt;
                font-weight: 700;
            }
            QLabel#eyebrow {
                color: #55C2A2;
                font-size: 9pt;
                font-weight: 700;
            }
            QLabel#heroTitle {
                font-size: 28pt;
                font-weight: 700;
            }
            QLabel#heroBody {
                color: #A9B2AD;
                font-size: 10pt;
            }
            QLabel#sectionTitle {
                font-size: 12.5pt;
                font-weight: 700;
            }
            QLabel#sectionBody, QLabel#helperLabel {
                color: #A9B2AD;
                font-size: 9pt;
            }
            QLabel#fieldLabel {
                color: #F2F5F3;
                font-size: 9pt;
                font-weight: 700;
                padding-bottom: 2px;
            }
            QLabel#statusTitle {
                font-size: 15pt;
                font-weight: 700;
            }
            QLabel#statusMessage {
                color: #A9B2AD;
                font-size: 10pt;
            }
            QLabel#statusChip[tone="ready"] {
                background: #20272E;
                color: #A9B2AD;
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QLabel#statusChip[tone="feasible"] {
                background: rgba(111, 207, 151, 0.16);
                color: #6FCF97;
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QLabel#statusChip[tone="warning"] {
                background: rgba(224, 169, 75, 0.16);
                color: #E0A94B;
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QLabel#statusChip[tone="needs-work"] {
                background: rgba(231, 111, 81, 0.16);
                color: #E76F51;
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QFrame#metricCard {
                background: #1B2026;
                border: 1px solid #343B44;
                border-radius: 8px;
            }
            QLabel#metricTitle {
                color: #A9B2AD;
                font-size: 9pt;
                font-weight: 700;
            }
            QLabel#metricValue {
                font-size: 22pt;
                font-weight: 700;
            }
            QLabel#metricUnit {
                color: #A9B2AD;
                font-size: 10pt;
                padding-top: 10px;
            }
            QLabel#metricDetail {
                color: #A9B2AD;
                font-size: 9pt;
            }
            QPushButton {
                background: #1B2026;
                border: 1px solid #343B44;
                border-radius: 6px;
                padding: 10px 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                border-color: #37A493;
            }
            QPushButton#primary {
                background: #2B8C7E;
                border-color: #2B8C7E;
                color: #FFFFFF;
            }
            QPushButton#primary:hover {
                background: #37A493;
                border-color: #37A493;
            }
            QPushButton#modeButton {
                min-height: 34px;
            }
            QPushButton#modeButton:checked {
                background: #2B8C7E;
                border-color: #2B8C7E;
                color: #FFFFFF;
            }
            QPushButton#navButton {
                text-align: left;
                min-height: 34px;
                padding: 10px 12px;
            }
            QPushButton#navButton:hover {
                background: #20272E;
            }
            QTabWidget::pane {
                border: 1px solid #252B33;
                border-radius: 8px;
                background: #15181D;
                top: -1px;
            }
            QTabBar::tab {
                background: #11151A;
                color: #A9B2AD;
                padding: 10px 16px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                min-width: 90px;
                font-weight: 700;
            }
            QTabBar::tab:selected {
                background: #1B2026;
                color: #F2F5F3;
                border-bottom: 2px solid #37A493;
            }
            QTabWidget#componentTabs QTabBar::tab {
                min-width: 126px;
                padding: 10px 18px;
            }
            QLineEdit, QComboBox, QDoubleSpinBox, QPlainTextEdit, QTableWidget, QGraphicsView {
                background: #11151A;
                border: 1px solid #343B44;
                border-radius: 6px;
                padding: 8px 10px;
                selection-background-color: #2B8C7E;
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
                border: 1px solid #37A493;
            }
            QPlainTextEdit#terminalText {
                background: #080A0D;
                color: #DCE7E1;
                border: 1px solid #343B44;
                font-family: "Cascadia Mono", "Consolas";
                font-size: 9.5pt;
            }
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 18px;
                border: none;
                background: transparent;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QRadioButton, QCheckBox {
                spacing: 8px;
                font-weight: 700;
            }
            QProgressBar {
                background: #11151A;
                border: 1px solid #343B44;
                border-radius: 5px;
                text-align: center;
                min-height: 16px;
            }
            QProgressBar::chunk {
                background: #2B8C7E;
                border-radius: 4px;
            }
            QHeaderView::section {
                background: #1B2026;
                color: #F2F5F3;
                border: none;
                border-bottom: 1px solid #343B44;
                padding: 8px;
                font-weight: 700;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 12px;
                margin: 4px;
            }
            QScrollBar::handle:vertical {
                background: #343B44;
                border-radius: 6px;
                min-height: 36px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QStatusBar {
                background: #15181D;
                color: #A9B2AD;
            }
            """
        )

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(14)
        root_layout.addWidget(self._build_header())

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._build_controls_panel())
        split.addWidget(self._build_results_panel())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([440, 1100])
        root_layout.addWidget(split, 1)

        self.setCentralWidget(root)
        self.setStatusBar(self._build_status_bar())

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(18)

        brand = QFrame()
        brand.setObjectName("heroBrand")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(16, 14, 18, 14)
        brand_layout.setSpacing(14)
        logo_label = QLabel()
        logo_label.setFixedSize(72, 72)
        if LOGO_PNG_PATH.exists():
            pixmap = QPixmap(str(LOGO_PNG_PATH)).scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_label.setPixmap(pixmap)
            logo_label.setAlignment(Qt.AlignCenter)
        brand_layout.addWidget(logo_label, 0, Qt.AlignVCenter)
        copy = QVBoxLayout()
        kicker = QLabel("Engine Workspace")
        kicker.setObjectName("logoSubtitle")
        copy.addWidget(kicker)
        title = QLabel("StanThrust")
        title.setObjectName("logoTitle")
        copy.addWidget(title)
        copy.addStretch(1)
        brand_layout.addLayout(copy, 1)
        layout.addWidget(brand, 1)

        mode_card = QFrame()
        mode_card.setObjectName("card")
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(16, 14, 16, 14)
        mode_layout.setSpacing(10)
        label = QLabel("Workspace")
        label.setObjectName("eyebrow")
        mode_layout.addWidget(label)
        row = QHBoxLayout()
        self.mode_buttons = {}
        for key, title in (("explorer", "Explorer"), ("expert", "Expert")):
            button = QPushButton(title)
            button.setObjectName("modeButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, value=key: self._set_mode(value))
            self.mode_buttons[key] = button
            row.addWidget(button)
        mode_layout.addLayout(row)
        self.mode_status_label = QLabel("")
        self.mode_status_label.setObjectName("heroBody")
        self.mode_status_label.setWordWrap(True)
        mode_layout.addWidget(self.mode_status_label)
        layout.addWidget(mode_card)

        return frame

    def _build_controls_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        frame.setMinimumWidth(456)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QFrame()
        intro.setObjectName("card")
        intro_layout = QVBoxLayout(intro)
        intro_layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel("Inputs")
        title.setObjectName("sectionTitle")
        intro_layout.addWidget(title)
        kicker = QLabel("Jump to a section, then scroll naturally through the whole design workspace.")
        kicker.setObjectName("sectionBody")
        kicker.setWordWrap(True)
        intro_layout.addWidget(kicker)
        nav = QGridLayout()
        nav.setHorizontalSpacing(8)
        nav.setVerticalSpacing(8)
        sections = (
            ("Mission", "mission", False),
            ("Envelope", "geometry", False),
            ("Architecture", "layout", False),
            ("Materials", "materials", True),
            ("Optimize", "optimize", True),
            ("Solver", "solver", True),
        )
        for index, (label, key, expert_only) in enumerate(sections):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.clicked.connect(lambda _checked=False, value=key: self._scroll_to_control_section(value))
            self.control_nav_buttons[key] = button
            button.setProperty("expertOnly", expert_only)
            nav.addWidget(button, index // 3, index % 3)
        intro_layout.addLayout(nav)
        layout.addWidget(intro)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        for key, widget, expert_only in (
            ("mission", self._build_mission_tab(), False),
            ("geometry", self._build_geometry_tab(), False),
            ("layout", self._build_layout_tab(), False),
            ("materials", self._build_materials_tab(), True),
            ("optimize", self._build_optimize_tab(), True),
            ("solver", self._build_solver_tab(), True),
        ):
            self.control_sections[key] = widget
            widget.setProperty("expertOnly", expert_only)
            content_layout.addWidget(widget)
        content_layout.addStretch(1)

        self.control_scroll_area = QScrollArea()
        self.control_scroll_area.setWidgetResizable(True)
        self.control_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.control_scroll_area.setWidget(content)
        self.control_scroll_area.verticalScrollBar().setSingleStep(24)
        layout.addWidget(self.control_scroll_area, 1)

        actions = QFrame()
        actions.setObjectName("card")
        actions_layout = QGridLayout(actions)
        actions_layout.setContentsMargins(16, 14, 16, 14)
        actions_layout.setHorizontalSpacing(8)
        actions_layout.setVerticalSpacing(8)
        action_title = QLabel("Actions")
        action_title.setObjectName("sectionTitle")
        actions_layout.addWidget(action_title, 0, 0, 1, 3)
        self.solve_button = QPushButton("Solve")
        self.solve_button.setObjectName("primary")
        self.solve_button.clicked.connect(self.run_solver)
        actions_layout.addWidget(self.solve_button, 1, 0, 1, 3)
        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self.reset_form)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_project_dialog)
        load_button = QPushButton("Load")
        load_button.clicked.connect(self.load_project_dialog)
        export_dxf = QPushButton("Profile DXF")
        export_dxf.clicked.connect(self.export_profile_dxf_dialog)
        export_csv = QPushButton("Export CSV")
        export_csv.clicked.connect(self.export_csv_dialog)
        export_stations = QPushButton("Stations CSV")
        export_stations.clicked.connect(self.export_station_csv_dialog)
        update_button = QPushButton("Check For Update")
        update_button.clicked.connect(self.check_for_update)
        actions_layout.addWidget(reset_button, 2, 0)
        actions_layout.addWidget(save_button, 2, 1)
        actions_layout.addWidget(load_button, 2, 2)
        actions_layout.addWidget(export_dxf, 3, 0)
        actions_layout.addWidget(export_csv, 3, 1)
        actions_layout.addWidget(export_stations, 3, 2)
        actions_layout.addWidget(update_button, 4, 0, 1, 3)
        layout.addWidget(actions)
        return frame

    def _build_results_panel(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.output_tabs = QTabWidget()
        self.output_tabs.tabBar().setUsesScrollButtons(True)
        self.output_tabs.tabBar().setElideMode(Qt.ElideRight)
        self.output_tabs.tabBar().setExpanding(False)
        self.output_tabs.addTab(self._build_overview_tab(), "Overview")
        self.output_tabs.addTab(self._build_schematic_tab(), "Schematic")
        self.output_tabs.addTab(self._build_model_tab(), "3D Model")
        self.output_tabs.addTab(self._build_plots_tab(), "Plots")
        self.output_tabs.addTab(self._build_measurements_tab(), "Measurements")
        self.output_tabs.addTab(self._build_summary_tab(), "Summary")
        self.output_tabs.addTab(self._build_diagnostics_tab(), "Diagnostics")
        layout.addWidget(self.output_tabs, 1)
        return frame

    def _build_overview_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        self.status_banner = StatusBanner()
        layout.addWidget(self.status_banner)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)
        for key, title, unit in (
            ("thrust", "Thrust", "N"),
            ("impulse", "Impulse", "N*s"),
            ("diameter", "Max Diameter", "mm"),
            ("length", "Total Length", "mm"),
        ):
            card = MetricCard(title, unit)
            cards_row.addWidget(card, 1)
            self.result_cards[key] = card
        layout.addLayout(cards_row)

        snapshot = QFrame()
        snapshot.setObjectName("card")
        snapshot_layout = QVBoxLayout(snapshot)
        snapshot_layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel("Solver Snapshot")
        title.setObjectName("sectionTitle")
        snapshot_layout.addWidget(title)
        row = QHBoxLayout()
        row.setSpacing(10)
        for key, title_text, unit in (
            ("cfd_thrust", "Thrust", "N"),
            ("cfd_isp", "Isp", "s"),
            ("cfd_pc", "Pc", "kPa"),
            ("cfd_mdot", "Mass Flow", "kg/s"),
        ):
            card = MetricCard(title_text, unit)
            row.addWidget(card, 1)
            self.cfd_cards[key] = card
        snapshot_layout.addLayout(row)
        layout.addWidget(snapshot)

        progress_card = QFrame()
        progress_card.setObjectName("card")
        progress_layout = QVBoxLayout(progress_card)
        progress_layout.setContentsMargins(16, 14, 16, 14)
        progress_layout.setSpacing(10)
        title = QLabel("Solver Run")
        title.setObjectName("sectionTitle")
        progress_layout.addWidget(title)
        self.solver_stage_label = QLabel("Coupled solver idle.")
        self.solver_stage_label.setObjectName("sectionBody")
        self.solver_stage_label.setWordWrap(True)
        progress_layout.addWidget(self.solver_stage_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        self.solver_residual_label = QLabel("Residuals will appear after Solve.")
        self.solver_residual_label.setObjectName("sectionBody")
        self.solver_residual_label.setWordWrap(True)
        progress_layout.addWidget(self.solver_residual_label)

        steps_grid = QGridLayout()
        steps_grid.setHorizontalSpacing(12)
        steps_grid.setVerticalSpacing(8)
        for row_index, (key, label_text) in enumerate(
            (
                ("preview", "Preview"),
                ("optimization", "Optimization"),
                ("feed", "Feed Transient"),
                ("chamber_nozzle", "Chamber/Nozzle"),
                ("structure", "Structure"),
                ("convergence", "Convergence"),
            )
        ):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            status = QLabel("Waiting")
            status.setObjectName("helperLabel")
            self.step_progress_bars[key] = bar
            self.step_status_labels[key] = status
            steps_grid.addWidget(label, row_index, 0)
            steps_grid.addWidget(bar, row_index, 1)
            steps_grid.addWidget(status, row_index, 2)
        steps_grid.setColumnStretch(1, 1)
        progress_layout.addLayout(steps_grid)
        layout.addWidget(progress_card)
        layout.addStretch(1)
        return tab

    def _build_schematic_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        title = QLabel("System Schematic")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.schematic_view = SchematicView()
        layout.addWidget(self.schematic_view, 1)
        return tab

    def _build_model_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        title = QLabel("Live 3D Model")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        component_tabs = QTabWidget()
        component_tabs.setObjectName("componentTabs")
        component_tabs.tabBar().setUsesScrollButtons(False)
        component_tabs.tabBar().setElideMode(Qt.ElideNone)
        component_tabs.tabBar().setExpanding(True)
        for key, label in (
            ("chamber_nozzle", "Chamber/Nozzle"),
            ("pumps", "Pumps"),
            ("injector", "Injector"),
            ("tanks", "Tanks"),
        ):
            view = Model3DView(key)
            self.model_3d_views[key] = view
            if key == "chamber_nozzle":
                self.model_3d_view = view
            component_tabs.addTab(view, label)
        layout.addWidget(component_tabs, 1)
        return tab

    def _build_measurements_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        self.measurement_tabs = QTabWidget()
        self.measurement_tabs.tabBar().setUsesScrollButtons(True)
        self.measurement_tabs.tabBar().setElideMode(Qt.ElideRight)
        self.measurement_tabs.tabBar().setExpanding(False)
        for name in MEASUREMENT_TABS:
            table = self._make_measurement_table()
            self.measurement_tables[name] = table
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addWidget(table)
            self.measurement_tabs.addTab(page, name)
        layout.addWidget(self.measurement_tabs)
        return tab

    def _build_plots_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        title = QLabel("Engineering Plots")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        body = QLabel("Transient, axial, and convergence views generated from the current reduced-order solve.")
        body.setObjectName("sectionBody")
        body.setWordWrap(True)
        layout.addWidget(body)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        definitions = (
            ("pressure_transient", "Burn Transient Pressure", "Chamber, required feed, and supply-side pressure history."),
            ("performance_transient", "Burn Performance", "Estimated thrust trace and propellant flow over the burn."),
            ("axial_field", "Axial Flow Field", "Pressure and velocity progression along the engine axis."),
            ("convergence", "Solver Convergence", "Chamber iteration pressure and relative error."),
        )
        for index, (key, label, subtitle) in enumerate(definitions):
            card = EngineeringPlotCard(label, subtitle)
            self.plot_cards[key] = card
            grid.addWidget(card, index // 2, index % 2)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(grid_host)
        layout.addWidget(scroll, 1)
        return tab

    def _build_summary_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        self.summary_text = QPlainTextEdit()
        self.summary_text.setReadOnly(True)
        layout.addWidget(self.summary_text)
        return tab

    def _build_diagnostics_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        top = QFrame()
        top.setObjectName("card")
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel("Diagnostics Terminal")
        title.setObjectName("sectionTitle")
        top_layout.addWidget(title)
        self.metadata_text = QPlainTextEdit()
        self.metadata_text.setObjectName("terminalText")
        self.metadata_text.setReadOnly(True)
        self.metadata_text.setMinimumHeight(520)
        top_layout.addWidget(self.metadata_text)
        layout.addWidget(top, 1)

        self.ga_status_label = QLabel("GA idle.")
        self.ga_status_label.setVisible(False)
        self.cfd_status_label = QLabel("Preview ready.")
        self.cfd_status_label.setVisible(False)
        self.export_status_label = QLabel("")
        self.export_status_label.setVisible(False)
        return tab

    def _build_status_bar(self) -> QStatusBar:
        bar = QStatusBar()
        bar.showMessage("Ready")
        return bar

    def _make_scroll_tab(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        return scroll

    def _make_measurement_table(self) -> QTableWidget:
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Measurement", "Value"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setShowGrid(False)
        table.setWordWrap(True)
        return table

    @staticmethod
    def _configure_form_layout(form: QFormLayout) -> None:
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(0)
        form.setVerticalSpacing(14)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

    def _build_section(self, title: str, body: str) -> QGroupBox:
        group = QGroupBox()
        group.setObjectName("card")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(16, 14, 16, 14)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        if body:
            subtitle = QLabel(body)
            subtitle.setObjectName("sectionBody")
            subtitle.setWordWrap(True)
            layout.addWidget(subtitle)
        return group

    def _build_mission_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        group = self._build_section("Mission", "")
        form = QFormLayout()
        self._configure_form_layout(form)
        self.widgets["fuel_name"] = self._combo(FUEL_NAMES)
        self.widgets["oxidizer_name"] = self._combo(OXIDIZER_NAMES)
        self.widgets["injector_type"] = self._combo(INJECTOR_TYPES, display_map=INJECTOR_DISPLAY_NAMES)
        self.widgets["target_thrust_newtons"] = self._spin(0.0, 100000.0, 1.0, 1, " N")
        self.widgets["target_impulse_newton_seconds"] = self._spin(0.0, 1000000.0, 10.0, 1, " N*s")
        self.widgets["burn_time_seconds"] = self._spin(0.1, 1000.0, 0.5, 2, " s")
        self._add_form_row(form, "Fuel", self.widgets["fuel_name"], FIELD_HELPERS["fuel_name"])
        self._add_form_row(form, "Oxidizer", self.widgets["oxidizer_name"], FIELD_HELPERS["oxidizer_name"])
        self._add_form_row(form, "Injector Family", self.widgets["injector_type"], FIELD_HELPERS["injector_type"])
        self._add_form_row(form, "Target Thrust (N)", self.widgets["target_thrust_newtons"], FIELD_HELPERS["target_thrust_newtons"])
        self._add_form_row(form, "Target Impulse (N*s)", self.widgets["target_impulse_newton_seconds"], FIELD_HELPERS["target_impulse_newton_seconds"])
        self._add_form_row(form, "Burn Time (s)", self.widgets["burn_time_seconds"], FIELD_HELPERS["burn_time_seconds"])
        group.layout().addLayout(form)
        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _build_geometry_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        group = self._build_section("Geometry", "")
        form = QFormLayout()
        self._configure_form_layout(form)
        self.widgets["target_diameter_mm"] = self._spin(10.0, 5000.0, 1.0, 1, " mm")
        self.widgets["tank_diameter_mm"] = self._spin(10.0, 5000.0, 1.0, 1, " mm")
        self.widgets["chamber_diameter_mm"] = self._spin(10.0, 5000.0, 1.0, 1, " mm")
        self.widgets["nozzle_diameter_mm"] = self._spin(10.0, 5000.0, 1.0, 1, " mm")
        self._add_form_row(form, "Target Diameter (mm)", self.widgets["target_diameter_mm"], FIELD_HELPERS["target_diameter_mm"])
        self._add_form_row(form, "Tank Diameter (mm)", self.widgets["tank_diameter_mm"], FIELD_HELPERS["tank_diameter_mm"])
        self._add_form_row(form, "Chamber Diameter (mm)", self.widgets["chamber_diameter_mm"], FIELD_HELPERS["chamber_diameter_mm"])
        self._add_form_row(form, "Nozzle Exit Diameter (mm)", self.widgets["nozzle_diameter_mm"], FIELD_HELPERS["nozzle_diameter_mm"])
        group.layout().addLayout(form)
        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _build_layout_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        assumption_group = self._build_section("Architecture", "")
        form = QFormLayout()
        self._configure_form_layout(form)
        self.widgets["mixture_ratio"] = self._spin(0.1, 20.0, 0.1, 3)
        self.widgets["packaging_bias"] = self._combo(("balanced", "compact", "serviceable"))
        self._add_form_row(form, "Mixture Ratio (O/F)", self.widgets["mixture_ratio"], FIELD_HELPERS["mixture_ratio"])
        self._add_form_row(form, "Packaging Bias", self.widgets["packaging_bias"], FIELD_HELPERS["packaging_bias"])
        assumption_group.layout().addLayout(form)

        feed_box = QGroupBox()
        feed_layout = QVBoxLayout(feed_box)
        feed_layout.setContentsMargins(0, 8, 0, 0)
        label = QLabel("Feed Mode")
        label.setObjectName("fieldLabel")
        feed_layout.addWidget(label)
        helper = QLabel("Choose the propellant feed architecture.")
        helper.setObjectName("helperLabel")
        helper.setWordWrap(True)
        feed_layout.addWidget(helper)
        row = QHBoxLayout()
        self.feed_group = QButtonGroup(self)
        self.widgets["feed_mode_pump"] = QRadioButton("Pump-fed")
        self.widgets["feed_mode_blowdown"] = QRadioButton("Blowdown")
        self.feed_group.addButton(self.widgets["feed_mode_pump"])
        self.feed_group.addButton(self.widgets["feed_mode_blowdown"])
        self.widgets["feed_mode_pump"].toggled.connect(self._on_preview_change)
        self.widgets["feed_mode_blowdown"].toggled.connect(self._on_preview_change)
        row.addWidget(self.widgets["feed_mode_pump"])
        row.addWidget(self.widgets["feed_mode_blowdown"])
        row.addStretch(1)
        feed_layout.addLayout(row)
        assumption_group.layout().addWidget(feed_box)

        self.widgets["regen_cooling"] = QCheckBox("Regenerative Cooling")
        self.widgets["film_cooling"] = QCheckBox("Film Cooling")
        self.widgets["regen_cooling"].toggled.connect(self._on_preview_change)
        self.widgets["film_cooling"].toggled.connect(self._on_preview_change)
        assumption_group.layout().addWidget(self.widgets["regen_cooling"])
        assumption_group.layout().addWidget(self._helper_label("Recirculate propellant through cooling passages."))
        assumption_group.layout().addWidget(self.widgets["film_cooling"])
        assumption_group.layout().addWidget(self._helper_label("Use a thin protective propellant film at the chamber wall."))

        layout.addWidget(assumption_group)
        layout.addStretch(1)
        return page

    def _build_materials_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        group = self._build_section("Materials", "")
        form = QFormLayout()
        self._configure_form_layout(form)
        self.widgets["fuel_tank_material"] = self._combo(MATERIAL_OPTIONS)
        self.widgets["oxidizer_tank_material"] = self._combo(MATERIAL_OPTIONS)
        self.widgets["feed_system_material"] = self._combo(MATERIAL_OPTIONS)
        self.widgets["chamber_material"] = self._combo(MATERIAL_OPTIONS)
        self.widgets["nozzle_material"] = self._combo(MATERIAL_OPTIONS)
        self.widgets["factor_of_safety"] = self._spin(1.0, 10.0, 0.1, 2)
        self._add_form_row(form, "Fuel Tank Material", self.widgets["fuel_tank_material"], "Tank wall material.")
        self._add_form_row(form, "Oxidizer Tank Material", self.widgets["oxidizer_tank_material"], "Tank wall material.")
        self._add_form_row(form, "Feed System Material", self.widgets["feed_system_material"], "Material placeholder for feed hardware.")
        self._add_form_row(form, "Chamber Material", self.widgets["chamber_material"], "Chamber material.")
        self._add_form_row(form, "Nozzle Material", self.widgets["nozzle_material"], "Nozzle material.")
        self._add_form_row(form, "Safety Factor", self.widgets["factor_of_safety"], FIELD_HELPERS["factor_of_safety"])
        group.layout().addLayout(form)
        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _build_optimize_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        group = self._build_section("Optimization", "")
        form = QFormLayout()
        self._configure_form_layout(form)
        self.widgets["objective_thrust"] = self._spin(0.0, 1.0, 0.05, 3)
        self.widgets["objective_mass"] = self._spin(0.0, 1.0, 0.05, 3)
        self.widgets["objective_packaging"] = self._spin(0.0, 1.0, 0.05, 3)
        self.widgets["objective_thermal"] = self._spin(0.0, 1.0, 0.05, 3)
        self._add_form_row(form, "Thrust Goal", self.widgets["objective_thrust"], "Higher values favor delivered thrust.")
        self._add_form_row(form, "Mass Goal", self.widgets["objective_mass"], "Higher values favor lighter engines.")
        self._add_form_row(form, "Packaging Weight", self.widgets["objective_packaging"], "Higher values favor tighter packaging.")
        self._add_form_row(form, "Thermal Weight", self.widgets["objective_thermal"], "Higher values favor thermal margin.")
        group.layout().addLayout(form)
        self.widgets["ga_enabled"] = QCheckBox("Optimize on Solve")
        self.widgets["feasibility_first"] = QCheckBox("Feasibility-first GA")
        self.widgets["use_multi_fidelity"] = QCheckBox("Multi-Fidelity Screening")
        self.widgets["show_uncertainty"] = QCheckBox("Show Uncertainty Summary")
        for key in ("ga_enabled", "feasibility_first", "use_multi_fidelity", "show_uncertainty"):
            checkbox = self.widgets[key]
            checkbox.toggled.connect(self._on_preview_change)
            group.layout().addWidget(checkbox)
        preview_button = QPushButton("Surrogate Preview")
        preview_button.clicked.connect(self.on_surrogate_preview)
        group.layout().addWidget(preview_button)
        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _build_solver_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        group = self._build_section("Solver", "")
        form = QFormLayout()
        self._configure_form_layout(form)
        self.widgets["solver_flow_model"] = self._combo(("fast", "refined"), FLOW_MODEL_DISPLAY_NAMES)
        self.widgets["solver_station_count"] = self._spin(6, 120, 1.0, 0)
        self.widgets["solver_convergence_tolerance"] = self._spin(0.0001, 0.1, 0.001, 4)
        self.widgets["solver_iteration_limit"] = self._spin(1, 500, 1.0, 0)
        self._add_form_row(form, "Flow Model", self.widgets["solver_flow_model"], FIELD_HELPERS["solver_flow_model"])
        self._add_form_row(form, "CFD Station Count", self.widgets["solver_station_count"], FIELD_HELPERS["solver_station_count"])
        self._add_form_row(form, "CFD Convergence Tolerance", self.widgets["solver_convergence_tolerance"], FIELD_HELPERS["solver_convergence_tolerance"])
        self._add_form_row(form, "Iteration Limit", self.widgets["solver_iteration_limit"], FIELD_HELPERS["solver_iteration_limit"])
        group.layout().addLayout(form)
        layout.addWidget(group)
        layout.addStretch(1)
        return page

    def _combo(self, values, display_map: Optional[Dict[str, str]] = None) -> QComboBox:
        combo = NoWheelComboBox()
        for value in values:
            text_value = str(value)
            if display_map is None:
                combo.addItem(_display_option_name(text_value), text_value)
            else:
                combo.addItem(display_map.get(text_value, text_value), text_value)
        combo.setMaxVisibleItems(14)
        combo.currentIndexChanged.connect(self._on_preview_change)
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return combo

    @staticmethod
    def _combo_value(combo: QComboBox) -> str:
        data = combo.currentData()
        if data is None:
            return combo.currentText()
        return str(data)

    @staticmethod
    def _set_combo_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
            return
        combo.setCurrentText(str(value))

    def _spin(self, minimum: float, maximum: float, step: float, decimals: int, suffix: str = "") -> QDoubleSpinBox:
        spin = NoWheelDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setKeyboardTracking(False)
        spin.setAccelerated(True)
        spin.valueChanged.connect(self._on_preview_change)
        spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return spin

    def _add_form_row(self, form: QFormLayout, label_text: str, widget: QWidget, helper_text: str) -> None:
        row = QWidget()
        row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(5)
        label = QLabel(label_text)
        label.setObjectName("fieldLabel")
        label.setWordWrap(True)
        row_layout.addWidget(label)
        if helper_text:
            row_layout.addWidget(self._helper_label(helper_text))
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row_layout.addWidget(widget)
        form.addRow(row)

    def _helper_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("helperLabel")
        label.setWordWrap(True)
        return label

    def _scroll_to_control_section(self, section_key: str) -> None:
        if self.control_scroll_area is None:
            return
        section = self.control_sections.get(section_key)
        if section is None or not section.isVisible():
            return
        self.control_scroll_area.verticalScrollBar().setValue(section.pos().y())

    def _set_mode(self, mode_name: str) -> None:
        if mode_name == self.mode():
            return
        self.widgets["ui_mode"] = mode_name
        self.is_expert_mode = mode_name == "expert"
        self._refresh_mode_controls()
        self.refresh_preview()

    def mode(self) -> str:
        return "expert" if getattr(self, "is_expert_mode", False) else "explorer"

    def _refresh_mode_controls(self) -> None:
        is_expert = getattr(self, "is_expert_mode", False)
        if self.mode_buttons:
            self.mode_buttons["explorer"].setChecked(not is_expert)
            self.mode_buttons["expert"].setChecked(is_expert)
        if self.mode_status_label is not None:
            self.mode_status_label.setText("Extended controls enabled" if is_expert else "Core design controls")
        for key, widget in self.control_sections.items():
            expert_only = bool(widget.property("expertOnly"))
            widget.setVisible(is_expert or not expert_only)
        for key, button in self.control_nav_buttons.items():
            expert_only = bool(button.property("expertOnly"))
            button.setVisible(is_expert or not expert_only)

    def reset_form(self) -> None:
        self._suspend_preview = True
        self.preview_timer.stop()
        self.is_expert_mode = False
        self._refresh_mode_controls()
        self._set_combo_value(self.widgets["fuel_name"], DEFAULT_STATE.fuel_name)
        self._set_combo_value(self.widgets["oxidizer_name"], DEFAULT_STATE.oxidizer_name)
        self._set_combo_value(self.widgets["injector_type"], DEFAULT_STATE.injector_type)
        self.widgets["target_thrust_newtons"].setValue(DEFAULT_STATE.target_thrust_newtons)
        self.widgets["target_impulse_newton_seconds"].setValue(DEFAULT_STATE.target_impulse_newton_seconds)
        self.widgets["burn_time_seconds"].setValue(DEFAULT_STATE.burn_time_seconds)
        self.widgets["target_diameter_mm"].setValue(DEFAULT_STATE.target_diameter_mm)
        self.widgets["tank_diameter_mm"].setValue(DEFAULT_STATE.tank_diameter_mm)
        self.widgets["chamber_diameter_mm"].setValue(DEFAULT_STATE.chamber_diameter_mm)
        self.widgets["nozzle_diameter_mm"].setValue(DEFAULT_STATE.nozzle_diameter_mm)
        self.widgets["mixture_ratio"].setValue(DEFAULT_STATE.mixture_ratio)
        self._set_combo_value(self.widgets["packaging_bias"], DEFAULT_STATE.packaging_bias)
        self.widgets["feed_mode_pump"].setChecked(DEFAULT_STATE.use_pumps)
        self.widgets["feed_mode_blowdown"].setChecked(not DEFAULT_STATE.use_pumps)
        self.widgets["regen_cooling"].setChecked(DEFAULT_STATE.regen_cooling)
        self.widgets["film_cooling"].setChecked(DEFAULT_STATE.film_cooling)
        self._set_combo_value(self.widgets["fuel_tank_material"], DEFAULT_STATE.fuel_tank_material)
        self._set_combo_value(self.widgets["oxidizer_tank_material"], DEFAULT_STATE.oxidizer_tank_material)
        self._set_combo_value(self.widgets["feed_system_material"], DEFAULT_STATE.feed_system_material)
        self._set_combo_value(self.widgets["chamber_material"], DEFAULT_STATE.chamber_material)
        self._set_combo_value(self.widgets["nozzle_material"], DEFAULT_STATE.nozzle_material)
        self.widgets["factor_of_safety"].setValue(DEFAULT_STATE.factor_of_safety)
        self.widgets["objective_thrust"].setValue(DEFAULT_OBJECTIVE_WEIGHTS["thrust"])
        self.widgets["objective_mass"].setValue(DEFAULT_OBJECTIVE_WEIGHTS["mass"])
        self.widgets["objective_packaging"].setValue(DEFAULT_OBJECTIVE_WEIGHTS["packaging"])
        self.widgets["objective_thermal"].setValue(DEFAULT_OBJECTIVE_WEIGHTS["thermal"])
        self.widgets["ga_enabled"].setChecked(True)
        self.widgets["feasibility_first"].setChecked(True)
        self.widgets["use_multi_fidelity"].setChecked(False)
        self.widgets["show_uncertainty"].setChecked(False)
        self.widgets["solver_flow_model"].setCurrentIndex(0)
        self.widgets["solver_station_count"].setValue(25)
        self.widgets["solver_convergence_tolerance"].setValue(0.005)
        self.widgets["solver_iteration_limit"].setValue(50)
        self.current_ga_result = None
        self.current_ga_candidate_state = None
        self.current_combustion_result = None
        self.current_solver_interface_result = None
        self.current_coupled_cycle_result = None
        self._suspend_preview = False
        self.refresh_preview()

    def collect_form_state(self) -> dict:
        return {
            "ui_mode": self.mode(),
            "fuel_name": self._combo_value(self.widgets["fuel_name"]),
            "oxidizer_name": self._combo_value(self.widgets["oxidizer_name"]),
            "target_thrust_newtons": float(self.widgets["target_thrust_newtons"].value()),
            "target_impulse_newton_seconds": float(self.widgets["target_impulse_newton_seconds"].value()),
            "target_diameter_mm": float(self.widgets["target_diameter_mm"].value()),
            "mixture_ratio": float(self.widgets["mixture_ratio"].value()),
            "injector_type": self._combo_value(self.widgets["injector_type"]),
            "burn_time_seconds": float(self.widgets["burn_time_seconds"].value()),
            "tank_diameter_mm": float(self.widgets["tank_diameter_mm"].value()),
            "chamber_diameter_mm": float(self.widgets["chamber_diameter_mm"].value()),
            "nozzle_diameter_mm": float(self.widgets["nozzle_diameter_mm"].value()),
            "factor_of_safety": float(self.widgets["factor_of_safety"].value()),
            "fuel_tank_material": self._combo_value(self.widgets["fuel_tank_material"]),
            "oxidizer_tank_material": self._combo_value(self.widgets["oxidizer_tank_material"]),
            "feed_system_material": self._combo_value(self.widgets["feed_system_material"]),
            "chamber_material": self._combo_value(self.widgets["chamber_material"]),
            "nozzle_material": self._combo_value(self.widgets["nozzle_material"]),
            "packaging_bias": self._combo_value(self.widgets["packaging_bias"]),
            "use_pumps": self.widgets["feed_mode_pump"].isChecked(),
            "regen_cooling": self.widgets["regen_cooling"].isChecked(),
            "film_cooling": self.widgets["film_cooling"].isChecked(),
            "ga_enabled": self.widgets["ga_enabled"].isChecked(),
            "solver_flow_model": self._combo_value(self.widgets["solver_flow_model"]),
            "solver_station_count": float(self.widgets["solver_station_count"].value()),
            "solver_convergence_tolerance": float(self.widgets["solver_convergence_tolerance"].value()),
            "solver_iteration_limit": float(self.widgets["solver_iteration_limit"].value()),
        }

    def collect_objective_weights(self) -> dict:
        return normalize_objective_weights(
            {
                "thrust": float(self.widgets["objective_thrust"].value()),
                "mass": float(self.widgets["objective_mass"].value()),
                "packaging": float(self.widgets["objective_packaging"].value()),
                "thermal": float(self.widgets["objective_thermal"].value()),
            }
        )

    def _on_preview_change(self, *_args) -> None:
        if self._suspend_preview:
            return
        self.current_combustion_result = None
        self.current_solver_interface_result = None
        self.current_coupled_cycle_result = None
        self.current_structural_result = None
        self.current_ga_result = None
        self.current_ga_candidate_state = None
        if self.statusBar() is not None:
            self.statusBar().showMessage("Updating preview...")
        self.preview_timer.start(130)

    def _apply_preview_refresh(self) -> None:
        self.refresh_preview(status="Preview ready.")

    def refresh_preview(self, status: str = "Preview ready.") -> None:
        state = self.collect_form_state()
        objective_weights = self.collect_objective_weights()
        self.current_input_state = dict(state)
        self.current_design = create_concept_design(state)
        self.current_objective_report = evaluate_objectives(self.current_design, objective_weights)
        self.current_validation_report = validate_concept_design(self.current_design)
        self.current_structural_result = self._build_structural_result()
        self._render_status_banner()
        self._render_metric_cards()
        self._render_measurements()
        self._render_summary_text()
        self._render_metadata_text()
        self._render_plots()
        if self.schematic_view is not None:
            self.schematic_view.render_design(self.current_design)
        self._render_model_views()
        self._set_solver_snapshot_defaults()
        self._render_coupled_convergence_status()
        self._set_status(status, "GA idle.", "")

    def _render_model_views(self) -> None:
        if self.current_design is None:
            return
        for view in self.model_3d_views.values():
            view.render_design(self.current_design)

    def _set_status(self, cfd_text: str, ga_text: str, export_text: str) -> None:
        if self.cfd_status_label is not None:
            self.cfd_status_label.setText(cfd_text)
        if self.ga_status_label is not None:
            self.ga_status_label.setText(ga_text)
        if self.export_status_label is not None:
            self.export_status_label.setText(export_text)
        if self.solver_stage_label is not None and cfd_text.lower().startswith(("preview", "project", "updating")):
            self.solver_stage_label.setText("Coupled solver idle.")
        if self.statusBar() is not None:
            self.statusBar().showMessage(cfd_text)

    def _reset_solver_progress(self, clear_log: bool = False) -> None:
        if clear_log:
            self.solver_log_lines = []
        if self.progress_bar is not None:
            self.progress_bar.setValue(0)
        if self.solver_stage_label is not None:
            self.solver_stage_label.setText("Coupled solver idle.")
        if self.solver_residual_label is not None:
            self.solver_residual_label.setText("Residuals will appear after Solve.")
        for key in self.step_progress_bars:
            self._set_step_progress(key, 0, "Waiting")
        self._refresh_diagnostics_terminal()

    def _set_step_progress(self, key: str, progress: float, status: str) -> None:
        bar = self.step_progress_bars.get(key)
        if bar is not None:
            bar.setValue(int(max(0.0, min(100.0, progress))))
        label = self.step_status_labels.get(key)
        if label is not None:
            label.setText(status)

    def _append_solver_log(self, message: str, code: str = "I-SOLVE-000", level: str = "INFO") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.solver_log_lines.append("{0} [{1}] {2}: {3}".format(timestamp, code, level, message))
        self.solver_log_lines = self.solver_log_lines[-400:]
        self._refresh_diagnostics_terminal()

    def _append_solver_warning_logs(self, warnings: List[object]) -> None:
        for index, warning in enumerate(warnings[:12], start=1):
            self._append_solver_log(str(warning), "W-SOLVE-{0:03d}".format(index), "WARN")

    def _refresh_diagnostics_terminal(self) -> None:
        if self.metadata_text is None:
            return
        lines = list(self.solver_log_lines)
        if not lines:
            lines = ["--:--:-- [I-PREVIEW-000] INFO: Preview model ready. Press Solve to run the coupled numerical solver."]
        if self.diagnostic_snapshot_lines:
            lines.extend(["", "# Current State"])
            lines.extend(self.diagnostic_snapshot_lines)
        self.metadata_text.setPlainText("\n".join(lines))
        scrollbar = self.metadata_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _update_step_from_solver_message(self, progress: float, message: str) -> None:
        lower = message.lower()
        if "preparing design" in lower:
            self._set_step_progress("preview", 100, "Ready")
            self._set_step_progress("convergence", max(8, progress * 0.2), "Iterating")
        elif "solving feed transient" in lower:
            self._set_step_progress("feed", 55, "Solving")
        elif "solving chamber and nozzle" in lower:
            self._set_step_progress("feed", 100, "Solved")
            self._set_step_progress("chamber_nozzle", 55, "Solving")
        elif "checking structural margins" in lower:
            self._set_step_progress("chamber_nozzle", 100, "Solved")
            self._set_step_progress("structure", 70, "Checking")
        elif "residual" in lower:
            self._set_step_progress("structure", 100, "Checked")
            self._set_step_progress("convergence", min(96, max(20, progress)), "Checking residual")
        elif "assembling final" in lower:
            for key in ("feed", "chamber_nozzle", "structure"):
                self._set_step_progress(key, 100, "Solved")
            self._set_step_progress("convergence", 96, "Assembling")

    def _set_solver_progress(self, progress: float, message: str) -> None:
        if self.progress_bar is not None:
            self.progress_bar.setValue(int(max(0.0, min(100.0, progress))))
        if self.solver_stage_label is not None:
            self.solver_stage_label.setText(message)
        if self.cfd_status_label is not None:
            self.cfd_status_label.setText(message)
        if self.statusBar() is not None:
            self.statusBar().showMessage(message)
        self._update_step_from_solver_message(progress, message)
        self._append_solver_log(message, "I-SOLVE-STEP", "INFO")
        QApplication.processEvents()

    def _render_coupled_convergence_status(self) -> None:
        if self.solver_residual_label is None:
            return
        if not isinstance(self.current_coupled_cycle_result, dict):
            self.solver_residual_label.setText("Residuals will appear after Solve.")
            return
        payload = dict(self.current_coupled_cycle_result.get("payload", {}))
        convergence = dict(payload.get("convergence", {}))
        self.solver_residual_label.setText(
            "Pc residual {0} kPa | thrust error {1}% | feed margin {2} kPa | structural margin {3}x".format(
                _format_number(convergence.get("final_residual_kpa", "--"), 3),
                _format_number((_safe_float(convergence.get("thrust_error_fraction"), 0.0) or 0.0) * 100.0, 3),
                _format_number(convergence.get("minimum_feed_margin_kpa", "--"), 3),
                _format_number(convergence.get("minimum_structural_margin_ratio", "--"), 3),
            )
        )

    def _render_status_banner(self) -> None:
        if self.status_banner is None:
            return
        validation = self.current_validation_report
        title = "Ready to solve"
        message = "Set inputs, review the geometry, then run the solver."
        tone = "ready"
        if validation is not None:
            if validation.passed:
                title = "Current design is feasible"
                message = validation.summary
                tone = "feasible"
            else:
                title = "Design needs adjustment"
                message = validation.summary
                tone = "needs-work"
        self.status_banner.update_banner(title, message, tone)

    def _render_metric_cards(self) -> None:
        if self.current_design is None:
            return
        values = dict(self.current_design.derived.engineering_values)
        thrust_value = float(values.get("calculated_thrust_newtons") or 0.0)
        impulse_value = float(values.get("calculated_impulse_newton_seconds") or 0.0)
        diameter_value = float(self.current_design.derived.maximum_diameter_mm)
        length_value = float(self.current_design.derived.total_stack_length_mm)
        required_diameter_value = float(values.get("maximum_required_outer_diameter_mm") or diameter_value)
        thrust_target = float(values.get("target_thrust_newtons") or self.current_design.inputs.target_thrust_newtons)
        impulse_target = float(values.get("target_impulse_newton_seconds") or self.current_design.inputs.target_impulse_newton_seconds)

        self.result_cards["thrust"].set_metric(_format_number(thrust_value, 1), "{0:.0f}% of target".format((thrust_value / thrust_target * 100.0) if thrust_target else 0.0))
        self.result_cards["impulse"].set_metric(_format_number(impulse_value, 1), "{0:.0f}% of target".format((impulse_value / impulse_target * 100.0) if impulse_target else 0.0))
        self.result_cards["diameter"].set_metric(
            _format_number(diameter_value, 2),
            "Limit {0} mm | Required {1} mm".format(
                _format_number(self.current_design.inputs.target_diameter_mm, 2),
                _format_number(required_diameter_value, 2),
            ),
        )
        self.result_cards["length"].set_metric(_format_number(length_value, 2), "Stacked engine length")

    def _set_solver_snapshot_defaults(self) -> None:
        for card in self.cfd_cards.values():
            card.set_metric("--", "Run Solve to populate")

    def _render_solver_snapshot(self) -> None:
        if not self.current_combustion_result:
            self._set_solver_snapshot_defaults()
            return
        summary = dict(self.current_combustion_result.get("summary", {}))
        self.cfd_cards["cfd_thrust"].set_metric(_format_number(summary.get("predicted_thrust_newtons", 0.0), 1), "Predicted thrust")
        self.cfd_cards["cfd_isp"].set_metric(_format_number(summary.get("predicted_isp_seconds", 0.0), 2), "Specific impulse")
        self.cfd_cards["cfd_pc"].set_metric(_format_number(summary.get("chamber_pressure_kpa", 0.0), 2), "Chamber pressure")
        self.cfd_cards["cfd_mdot"].set_metric(_format_number(summary.get("mass_flow_kg_s", 0.0), 5), "Mass flow")

    def _set_default_plots(self) -> None:
        for card in self.plot_cards.values():
            card.set_plot_data(
                subtitle="Run Solve to generate this plot from the current engine state.",
                x_label="Solve progress",
                primary_label="No data",
                primary_series=[],
                note="The plots tab uses transient feed history, axial station fields, and convergence traces from the solver.",
            )

    def _pressure_plot_series(self, time_history: list) -> list:
        if not time_history:
            return []

        pressure_series = [
            {
                "label": "Chamber",
                "color": QT_PALETTE["accent_hover"],
                "points": [(row.get("time_s"), row.get("chamber_pressure_kpa")) for row in time_history],
            },
            {
                "label": "Required",
                "color": QT_PALETTE["warning"],
                "points": [(row.get("time_s"), row.get("required_feed_pressure_kpa")) for row in time_history],
            },
            {
                "label": "Fuel tank",
                "color": QT_PALETTE["fuel"],
                "points": [(row.get("time_s"), row.get("fuel_tank_pressure_kpa")) for row in time_history],
            },
            {
                "label": "Ox tank",
                "color": QT_PALETTE["oxidizer"],
                "points": [(row.get("time_s"), row.get("oxidizer_tank_pressure_kpa")) for row in time_history],
            },
        ]
        has_pump_discharge = any(_safe_float(row.get("pump_discharge_pressure_kpa"), 0.0) for row in time_history)
        if has_pump_discharge:
            pressure_series.append(
                {
                    "label": "Pump discharge",
                    "color": QT_PALETTE["success"],
                    "points": [(row.get("time_s"), row.get("pump_discharge_pressure_kpa")) for row in time_history],
                }
            )
        return pressure_series

    def _performance_plot_series(self, time_history: list, predicted_thrust: float) -> Tuple[list, list]:
        if not time_history:
            return [], []

        thrust_series = [
            {
                "label": "Estimated thrust",
                "color": QT_PALETTE["accent_hover"],
                "points": [
                    (
                        row.get("time_s"),
                        predicted_thrust * (_safe_float(row.get("flow_scale"), 1.0) or 1.0),
                    )
                    for row in time_history
                ],
            }
        ]
        mass_flow_series = [
            {
                "label": "Mass flow",
                "color": QT_PALETTE["fuel"],
                "points": [(row.get("time_s"), row.get("propellant_mass_flow_kg_s")) for row in time_history],
            }
        ]
        return thrust_series, mass_flow_series

    def _axial_plot_series(self, axial_profile: list) -> Tuple[list, list]:
        if not axial_profile:
            return [], []

        pressure_series = [
            {
                "label": "Pressure",
                "color": QT_PALETTE["accent_hover"],
                "points": [(row.get("x_mm"), row.get("pressure_kpa")) for row in axial_profile],
            }
        ]
        velocity_series = [
            {
                "label": "Velocity",
                "color": QT_PALETTE["fuel"],
                "points": [(row.get("x_mm"), row.get("velocity_m_s")) for row in axial_profile],
            }
        ]
        return pressure_series, velocity_series

    def _convergence_plot_series(self, iteration_trace: list) -> Tuple[list, list]:
        if not iteration_trace:
            return [], []

        pressure_series = [
            {
                "label": "Pc",
                "color": QT_PALETTE["accent_hover"],
                "points": [(row.get("iteration"), row.get("chamber_pressure_kpa")) for row in iteration_trace],
            }
        ]
        error_series = [
            {
                "label": "Error",
                "color": QT_PALETTE["warning"],
                "points": [
                    (
                        row.get("iteration"),
                        (
                            _safe_float(
                                row.get("relative_error", row.get("thrust_error_fraction")),
                                0.0,
                            )
                            or 0.0
                        )
                        * 100.0,
                    )
                    for row in iteration_trace
                ],
            }
        ]
        return pressure_series, error_series

    def _render_plots(self) -> None:
        if not self.plot_cards:
            return
        if not isinstance(self.current_combustion_result, dict) or not isinstance(self.current_solver_interface_result, dict):
            self._set_default_plots()
            return

        combustion_summary = dict(self.current_combustion_result.get("summary", {}))
        combustion_metadata = dict(self.current_combustion_result.get("metadata", {}))
        solver_payload = dict(self.current_solver_interface_result.get("payload", {}))
        feed_result = dict(solver_payload.get("feed_pressure_drop", {}))
        feed_payload = dict(feed_result.get("payload", {}))
        feed_summary = dict(feed_payload.get("summary", {}))
        time_history = list(feed_payload.get("time_history_rows", []))
        axial_profile = list(self.current_combustion_result.get("axial_profile", []))
        coupled_payload = (
            dict(self.current_coupled_cycle_result.get("payload", {}))
            if isinstance(self.current_coupled_cycle_result, dict)
            else {}
        )
        iteration_trace = list(coupled_payload.get("iteration_trace", [])) or list(
            self.current_combustion_result.get("iteration_trace", [])
        )

        predicted_thrust = _safe_float(
            combustion_summary.get("predicted_thrust_newtons"),
            _safe_float(dict(self.current_design.derived.engineering_values).get("calculated_thrust_newtons"), 0.0)
            if self.current_design is not None
            else 0.0,
        ) or 0.0

        pressure_primary = self._pressure_plot_series(time_history)
        performance_primary, performance_secondary = self._performance_plot_series(time_history, predicted_thrust)
        axial_primary, axial_secondary = self._axial_plot_series(axial_profile)
        convergence_primary, convergence_secondary = self._convergence_plot_series(iteration_trace)

        flow_model_label = str(combustion_metadata.get("flow_model_label", combustion_summary.get("flow_model_label", "Current solve")))
        architecture_label = "Pump-fed" if bool(self.current_design.inputs.use_pumps) else "Pressure-fed"
        drift_percent = _format_number(feed_summary.get("chamber_pressure_drift_percent", "--"), 3)

        self.plot_cards["pressure_transient"].set_plot_data(
            subtitle="{0} transient pressure history. Drift over burn: {1}%.".format(architecture_label, drift_percent),
            x_label="Time (s)",
            primary_label="Pressure (kPa)",
            primary_series=pressure_primary,
            note="Fuel and oxidizer supply curves come directly from the transient feed solver. Pump discharge appears only for pump-fed cases.",
        )
        self.plot_cards["performance_transient"].set_plot_data(
            subtitle="Preliminary thrust curve scaled from the solved operating point using transient feed state.",
            x_label="Time (s)",
            primary_label="Estimated thrust (N)",
            primary_series=performance_primary,
            secondary_label="Mass flow (kg/s)",
            secondary_series=performance_secondary,
            note="This thrust trace is preliminary: it scales the converged steady-state thrust by the transient flow-scale history rather than running a full time-marching combustion solve.",
        )
        self.plot_cards["axial_field"].set_plot_data(
            subtitle="{0} axial station field from the reduced-order combustion path.".format(flow_model_label),
            x_label="Axial position (mm)",
            primary_label="Pressure (kPa)",
            primary_series=axial_primary,
            secondary_label="Velocity (m/s)",
            secondary_series=axial_secondary,
            note="Axial station fields come from the same quasi-1D solver used for the measurements and diagnostics tabs.",
        )
        self.plot_cards["convergence"].set_plot_data(
            subtitle="Chamber-pressure iteration trace and relative thrust error.",
            x_label="Iteration",
            primary_label="Chamber pressure (kPa)",
            primary_series=convergence_primary,
            secondary_label="Relative error (%)",
            secondary_series=convergence_secondary,
            note="Use this view to see whether the current solver resolution converged cleanly or simply stopped at the iteration limit.",
        )

    def _render_measurements(self) -> None:
        if self.current_design is None:
            return
        for table in self.measurement_tables.values():
            table.setRowCount(0)

        for row in self.current_design.derived.measurement_rows:
            category = self._measurement_tab_for_label(row.label)
            for table_name in ("All", category):
                self._append_measurement_row(self.measurement_tables[table_name], row.label, row.value)

        if self.current_combustion_result:
            summary = dict(self.current_combustion_result.get("summary", {}))
            metadata = dict(self.current_combustion_result.get("metadata", {}))
            thermo = dict(metadata.get("thermochemistry", {}))
            cfd_rows = (
                ("CFD Status", self.current_combustion_result.get("status", "unknown")),
                ("CFD Stage", metadata.get("solver_stage", "--")),
                ("Thermochemistry", "{0} ({1})".format(thermo.get("provider", "--"), thermo.get("status", "--"))),
                ("CFD Thrust", "{0} N".format(_format_number(summary.get("predicted_thrust_newtons", "--"), 2))),
                ("CFD Impulse", "{0} N*s".format(_format_number(summary.get("predicted_impulse_newton_seconds", "--"), 2))),
                ("CFD Isp", "{0} s".format(_format_number(summary.get("predicted_isp_seconds", "--"), 3))),
                ("CFD Chamber Pressure", "{0} kPa".format(_format_number(summary.get("chamber_pressure_kpa", "--"), 3))),
            )
            for label, value in cfd_rows:
                for table_name in ("All", "CFD", "Overall"):
                    self._append_measurement_row(self.measurement_tables[table_name], label, value)

        for table in self.measurement_tables.values():
            table.resizeRowsToContents()

    @staticmethod
    def _append_measurement_row(table: QTableWidget, label: str, value: str) -> None:
        row_index = table.rowCount()
        table.insertRow(row_index)
        table.setItem(row_index, 0, QTableWidgetItem(str(label)))
        table.setItem(row_index, 1, QTableWidgetItem(str(value)))

    @staticmethod
    def _measurement_tab_for_label(label: str) -> str:
        text = label.lower()
        if "tank" in text:
            return "Tanks"
        if "impeller" in text or "motor" in text or "feed" in text or "pressurization bay" in text:
            return "Feed/Pumps"
        if "chamber" in text:
            return "Chamber"
        if "throat" in text:
            return "Nozzle"
        if "injector" in text or "pintle" in text or "impinging" in text:
            return "Injector"
        if "nozzle" in text:
            return "Nozzle"
        if "regen" in text or "film" in text or "cooling" in text:
            return "Cooling"
        return "Overall"

    def _build_structural_result(self) -> Optional[Dict[str, object]]:
        if self.current_design is None:
            return None
        state = self.collect_form_state()
        material_result = assign_materials({"materials": state}, {})
        return build_structural_materials_output(
            state,
            {"payload": {}},
            material_result,
            self.current_combustion_result,
        )

    def _render_summary_text(self) -> None:
        if self.summary_text is None or self.current_design is None or self.current_objective_report is None:
            return
        values = dict(self.current_design.derived.engineering_values)
        combustion_summary = {}
        combustion_metadata = {}
        combustion_warnings = []
        if isinstance(self.current_combustion_result, dict):
            combustion_summary = dict(self.current_combustion_result.get("summary", {}))
            combustion_metadata = dict(self.current_combustion_result.get("metadata", {}))
            combustion_warnings = list(self.current_combustion_result.get("warnings", []))
        feed_summary = {}
        solver_warnings = []
        solver_trace = []
        if isinstance(self.current_solver_interface_result, dict):
            solver_payload = dict(self.current_solver_interface_result.get("payload", {}))
            feed_result = dict(solver_payload.get("feed_pressure_drop", {}))
            feed_summary = dict(dict(feed_result.get("payload", {})).get("summary", {}))
            solver_warnings = list(self.current_solver_interface_result.get("warnings", []))
            solver_trace = list(self.current_solver_interface_result.get("trace", []))
        thrust_display = combustion_summary.get("predicted_thrust_newtons", values.get("calculated_thrust_newtons", "--"))
        isp_display = combustion_summary.get("predicted_isp_seconds", values.get("predicted_isp_seconds", "--"))
        chamber_pressure_display = combustion_summary.get("chamber_pressure_kpa", values.get("chamber_pressure_kpa", "--"))
        mass_flow_display = combustion_summary.get("mass_flow_kg_s", values.get("propellant_mass_flow_kg_s", "--"))
        lines = [
            "Solve",
            "Thrust: {0} N".format(_format_number(thrust_display, 2)),
            "Specific impulse: {0} s".format(_format_number(isp_display, 3)),
            "Chamber pressure: {0} kPa".format(_format_number(chamber_pressure_display, 3)),
            "Mass flow: {0} kg/s".format(_format_number(mass_flow_display, 5)),
            "Score: {0}".format(_format_number(self.current_objective_report["total_score"], 4)),
            "",
            "Hardware",
            "Injector: {0}".format(_display_injector_name(values.get("injector_type", self.current_design.inputs.injector_type))),
            "Propellant used: {0} kg".format(_format_number(values.get("propellant_mass_used_kg", "--"), 3)),
            "Fuel tank ID/OD/wall/length: {0}/{1}/{2}/{3} mm".format(
                _format_number(values.get("fuel_tank_inner_diameter_mm", "--"), 2),
                _format_number(values.get("fuel_tank_outer_diameter_mm", self.current_design.inputs.tank_diameter_mm), 2),
                _format_number(values.get("fuel_tank_wall_thickness_mm", "--"), 2),
                _format_number(values.get("fuel_tank_required_length_mm", "--"), 2),
            ),
            "Ox tank ID/OD/wall/length: {0}/{1}/{2}/{3} mm".format(
                _format_number(values.get("oxidizer_tank_inner_diameter_mm", "--"), 2),
                _format_number(values.get("oxidizer_tank_outer_diameter_mm", self.current_design.inputs.tank_diameter_mm), 2),
                _format_number(values.get("oxidizer_tank_wall_thickness_mm", "--"), 2),
                _format_number(values.get("oxidizer_tank_required_length_mm", "--"), 2),
            ),
            "Chamber ID/OD/wall/length: {0}/{1}/{2}/{3} mm".format(
                _format_number(values.get("chamber_inner_diameter_mm", self.current_design.inputs.chamber_diameter_mm), 2),
                _format_number(values.get("chamber_outer_diameter_mm", self.current_design.inputs.chamber_diameter_mm), 2),
                _format_number(values.get("chamber_wall_thickness_mm", "--"), 2),
                _format_number(values.get("chamber_length_mm", self.current_design.derived.chamber_length_mm), 2),
            ),
            "Nozzle ID/OD/wall/length: {0}/{1}/{2}/{3} mm".format(
                _format_number(values.get("nozzle_inner_diameter_mm", self.current_design.inputs.nozzle_diameter_mm), 2),
                _format_number(values.get("nozzle_outer_diameter_mm", self.current_design.inputs.nozzle_diameter_mm), 2),
                _format_number(values.get("nozzle_wall_thickness_mm", "--"), 2),
                _format_number(values.get("nozzle_diverging_length_mm", "--"), 2),
            ),
            "",
            "Geometry limit",
            "Maximum outer diameter: {0} mm".format(_format_number(values.get("maximum_diameter_mm", self.current_design.derived.maximum_diameter_mm), 3)),
            "Target diameter limit: {0} mm".format(_format_number(values.get("target_outer_diameter_limit_mm", self.current_design.inputs.target_diameter_mm), 3)),
            "Uncapped requirement: {0} mm".format(_format_number(values.get("maximum_required_outer_diameter_mm", self.current_design.derived.maximum_diameter_mm), 3)),
            "Diameter status: {0}".format(values.get("diameter_limit_status", "--")),
        ]
        if feed_summary:
            lines.extend(
                [
                    "",
                    "Feed transient",
                    "History steps: {0}".format(feed_summary.get("history_step_count", "--")),
                    "Chamber pressure drift: {0} -> {1} kPa".format(
                        _format_number(feed_summary.get("initial_chamber_pressure_kpa", "--"), 3),
                        _format_number(feed_summary.get("final_chamber_pressure_kpa", "--"), 3),
                    ),
                    "Minimum feed margin: {0} kPa".format(_format_number(feed_summary.get("minimum_feed_margin_kpa", "--"), 3)),
                    "End-of-burn tank pressure: fuel {0} kPa | ox {1} kPa".format(
                        _format_number(feed_summary.get("final_fuel_tank_pressure_kpa", "--"), 3),
                        _format_number(feed_summary.get("final_oxidizer_tank_pressure_kpa", "--"), 3),
                    ),
                ]
            )
        if self.current_combustion_result:
            thermochemistry = dict(combustion_metadata.get("thermochemistry", {}))
            lines.extend(
                [
                    "",
                    "Solver",
                    "Status: {0}".format(self.current_combustion_result.get("status", "unknown")),
                    "Stage: {0}".format(combustion_metadata.get("solver_stage", "--")),
                    "Thermochemistry: {0} ({1})".format(
                        thermochemistry.get("provider", "--"),
                        thermochemistry.get("status", "--"),
                    ),
                    "Stations: {0}".format(int(combustion_summary.get("station_count", 0))),
                    "Iterations: {0}".format(self.current_combustion_result.get("iterations", 0)),
                ]
            )
        recent_logs = []
        recent_logs.extend(str(item) for item in solver_warnings[:2])
        recent_logs.extend(str(item) for item in combustion_warnings[:2])
        recent_logs.extend(str(item) for item in solver_trace[-2:])
        if recent_logs:
            lines.extend(["", "Recent logs"])
            lines.extend(recent_logs[:4])
        if self.current_ga_result:
            lines.extend(
                [
                    "",
                    "GA",
                    "Iterations: {0}".format(len(self.current_ga_result.history)),
                    "Best score: {0}".format(self.current_ga_result.best_score),
                ]
            )
        self.summary_text.setPlainText("\n".join(lines))

    def _render_metadata_text(self) -> None:
        if self.metadata_text is None:
            return
        lines = []
        if self.current_design is not None:
            values = dict(self.current_design.derived.engineering_values)
            lines.extend(
                [
                    "geometry.maximum_diameter_mm = {0}".format(_format_number(values.get("maximum_diameter_mm", self.current_design.derived.maximum_diameter_mm), 4)),
                    "geometry.target_outer_diameter_limit_mm = {0}".format(_format_number(values.get("target_outer_diameter_limit_mm", self.current_design.inputs.target_diameter_mm), 4)),
                    "geometry.maximum_required_outer_diameter_mm = {0}".format(_format_number(values.get("maximum_required_outer_diameter_mm", self.current_design.derived.maximum_diameter_mm), 4)),
                    "geometry.diameter_limit_status = {0}".format(values.get("diameter_limit_status", "--")),
                ]
            )
        if isinstance(self.current_solver_interface_result, dict):
            solver_payload = dict(self.current_solver_interface_result.get("payload", {}))
            feed_result = dict(solver_payload.get("feed_pressure_drop", {}))
            feed_metadata = dict(feed_result.get("metadata", {}))
            feed_summary = dict(dict(feed_result.get("payload", {})).get("summary", {}))
            if feed_metadata:
                lines.extend(
                    [
                        "stage_2_feed_pressure_drop.solver_mode = {0}".format(feed_metadata.get("solver_mode", "--")),
                        "stage_2_feed_pressure_drop.status = {0}".format(feed_result.get("status", "--")),
                        "stage_2_feed_pressure_drop.history_step_count = {0}".format(feed_summary.get("history_step_count", "--")),
                        "stage_2_feed_pressure_drop.minimum_feed_margin_kpa = {0}".format(_format_number(feed_summary.get("minimum_feed_margin_kpa", "--"), 4)),
                        "stage_2_feed_pressure_drop.chamber_pressure_drift_percent = {0}".format(_format_number(feed_summary.get("chamber_pressure_drift_percent", "--"), 4)),
                    ]
                )
                for warning in list(feed_result.get("warnings", []))[:3]:
                    lines.append("stage_2_feed_pressure_drop.warning = {0}".format(warning))
            for trace_line in list(self.current_solver_interface_result.get("trace", []))[:5]:
                lines.append("solver_interface.trace = {0}".format(trace_line))
        if self.current_combustion_result:
            metadata = dict(self.current_combustion_result.get("metadata", {}))
            thermochemistry = dict(metadata.get("thermochemistry", {}))
            lines.extend(
                [
                    "stage_1_thermochemistry.requested_mode = {0}".format(thermochemistry.get("requested_mode", "--")),
                    "stage_1_thermochemistry.effective_mode = {0}".format(thermochemistry.get("effective_mode", "--")),
                    "stage_1_thermochemistry.provider = {0}".format(thermochemistry.get("provider", "--")),
                    "stage_1_thermochemistry.status = {0}".format(thermochemistry.get("status", "--")),
                    "stage_2_nozzle_flow.status_detail = {0}".format(self.current_combustion_result.get("status_detail", "--")),
                ]
            )
            for warning in list(self.current_combustion_result.get("warnings", []))[:3]:
                lines.append("stage_2_nozzle_flow.warning = {0}".format(warning))
        else:
            lines.append("preview.mode = design-preview")
        if self.current_validation_report is not None:
            lines.append("validation.status = {0}".format("ok" if self.current_validation_report.passed else "warning"))
            lines.append("validation.summary = {0}".format(self.current_validation_report.summary))
            failed_checks = [check for check in self.current_validation_report.checks if not check.passed]
            for check in failed_checks[:5]:
                lines.append("validation.{0} = {1}".format(check.check_name, check.message))
        if self.current_design is not None:
            for stage in list(self.current_design.derived.calculation_stages)[:7]:
                lines.append("calculation_stage = {0}".format(stage))
        self.diagnostic_snapshot_lines = lines
        self._refresh_diagnostics_terminal()

    def check_for_update(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            release = self._fetch_latest_release()
            latest_tag = str(release.get("tag_name") or release.get("name") or "").strip()
            latest_version = latest_tag.lstrip("vV")
            if not latest_version:
                QMessageBox.information(self, "Update Check", "No release version was found.")
                return
            if not _is_newer_version(latest_version, APP_VERSION):
                QMessageBox.information(
                    self,
                    "StanThrust Is Up To Date",
                    "Installed version: {0}\nLatest release: {1}".format(APP_VERSION, latest_tag),
                )
                return
            asset = self._select_release_asset(release)
            release_url = str(release.get("html_url") or "https://github.com/iavros/StanThrust/releases")
            if not asset:
                answer = QMessageBox.question(
                    self,
                    "Update Available",
                    "StanThrust {0} is available, but no installer asset matches this platform.\n\nOpen the release page?".format(latest_tag),
                    QMessageBox.Yes | QMessageBox.No,
                )
                if answer == QMessageBox.Yes:
                    webbrowser.open(release_url)
                return
            asset_name = str(asset.get("name") or "StanThrust update")
            answer = QMessageBox.question(
                self,
                "Update Available",
                "StanThrust {0} is available.\n\nDownload {1} to your Downloads folder?".format(latest_tag, asset_name),
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            downloaded_path = self._download_release_asset(asset)
            self._append_solver_log("Downloaded update installer: {0}".format(downloaded_path), "I-UPDATE-001", "INFO")
            QMessageBox.information(
                self,
                "Update Downloaded",
                "Downloaded:\n{0}\n\nRun the installer to update StanThrust.".format(downloaded_path),
            )
            self._open_file_location(downloaded_path)
        except (urllib.error.URLError, TimeoutError) as exc:
            QMessageBox.warning(self, "Update Check Failed", "Could not reach GitHub Releases.\n\n{0}".format(exc))
        except Exception as exc:
            QMessageBox.warning(self, "Update Check Failed", "StanThrust could not complete the update check.\n\n{0}".format(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def _fetch_latest_release(self) -> Dict[str, object]:
        request = urllib.request.Request(
            UPDATE_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "StanThrust/{0}".format(APP_VERSION),
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return dict(json.loads(response.read().decode("utf-8")))

    def _select_release_asset(self, release: Dict[str, object]) -> Optional[Dict[str, object]]:
        raw_assets = release.get("assets", [])
        assets = [dict(asset) for asset in raw_assets if isinstance(asset, dict)]
        if sys.platform == "win32":
            preferred = ("installer.exe", ".msi", "windows-portable.zip", ".zip")
        elif sys.platform == "darwin":
            preferred = (".dmg", ".pkg", ".zip")
        else:
            preferred = (".zip", ".tar.gz", ".tgz")
        for marker in preferred:
            for asset in assets:
                name = str(asset.get("name") or "").lower()
                if marker in name and asset.get("browser_download_url"):
                    return asset
        return None

    def _download_release_asset(self, asset: Dict[str, object]) -> Path:
        url = str(asset.get("browser_download_url") or "")
        if not url:
            raise ValueError("Release asset is missing a download URL.")
        name = Path(str(asset.get("name") or "StanThrust-update")).name
        downloads_dir = Path.home() / "Downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        target = downloads_dir / name
        stem = target.stem
        suffix = "".join(target.suffixes)
        counter = 1
        while target.exists():
            target = downloads_dir / "{0}-{1}{2}".format(stem, counter, suffix)
            counter += 1
        request = urllib.request.Request(url, headers={"User-Agent": "StanThrust/{0}".format(APP_VERSION)})
        with urllib.request.urlopen(request, timeout=90) as response, target.open("wb") as output:
            shutil.copyfileobj(response, output)
        return target

    def _open_file_location(self, path: Path) -> None:
        folder = path.parent
        try:
            if sys.platform == "win32":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                webbrowser.open(folder.as_uri())
            else:
                webbrowser.open(folder.as_uri())
        except Exception:
            webbrowser.open(folder.as_uri())

    def run_solver(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.solve_button.setEnabled(False)
        try:
            self._reset_solver_progress(clear_log=True)
            self._append_solver_log("Solve requested from desktop UI.", "I-SOLVE-001", "INFO")
            self._set_step_progress("preview", 25, "Building")
            self.refresh_preview(status="Solving design...")
            self._set_step_progress("preview", 100, "Ready")
            if self.progress_bar is not None:
                self.progress_bar.setValue(5)
            if self.widgets["ga_enabled"].isChecked() or self.current_validation_report is None or not self.current_validation_report.passed:
                self._set_step_progress("optimization", 20, "Running")
                self.run_concept_ga()
                self._set_step_progress("optimization", 100, "Solved")
            else:
                self.current_ga_result = None
                self.current_ga_candidate_state = None
                self._set_step_progress("optimization", 100, "Skipped")
                self._append_solver_log("Optimization skipped; current preview passed validation and Optimize on Solve is disabled.", "I-SOLVE-002", "INFO")
            self.run_combustion_solver()
        except Exception as exc:
            self.solver_error_counter += 1
            error_code = "E-SOLVE-{0:03d}".format(self.solver_error_counter)
            self._append_solver_log("{0}: {1}".format(error_code, exc), error_code, "ERROR")
            QMessageBox.critical(self, "Solve failed", "StanThrust could not complete the solve.\n\n{0}".format(exc))
        finally:
            self.solve_button.setEnabled(True)
            QApplication.restoreOverrideCursor()

    def run_concept_ga(self) -> None:
        if not self.current_design:
            return
        self._append_solver_log("Starting feasibility and objective optimization pass.", "I-GA-001", "INFO")
        self._set_status(self.cfd_status_label.text() if self.cfd_status_label else "Solving...", "Running design GA...", self.export_status_label.text() if self.export_status_label else "")
        QApplication.processEvents()
        seed = build_optimizer_seed(
            self.current_design,
            base_state=self.collect_form_state(),
            objective_weights=self.collect_objective_weights(),
        )
        if self.widgets["feasibility_first"].isChecked():
            self.current_ga_result = run_feasibility_first_optimizer(seed)
        else:
            self.current_ga_result = run_genetic_optimizer(seed)
        mf_results = {}
        if self.widgets["use_multi_fidelity"].isChecked() and self.current_ga_result is not None:
            mf_results = apply_multifidelity_confirmation(self.current_ga_result)
        self.current_ga_candidate_state = (
            dict(self.current_ga_result.best_state) if self.current_ga_result is not None else None
        )
        if self.widgets["use_multi_fidelity"].isChecked() and mf_results.get("screening_applied"):
            ga_text = "GA complete. Screened {0}, confirmed {1}.".format(
                mf_results.get("candidates_evaluated", 0),
                mf_results.get("candidates_confirmed", 0),
            )
        else:
            ga_text = "GA complete. Best score: {0:.4f}.".format(self.current_ga_result.best_score)
        if self.ga_status_label is not None:
            self.ga_status_label.setText(ga_text)
        self._append_solver_log(ga_text, "I-GA-002", "INFO")

    def run_combustion_solver(self) -> None:
        if not self.current_design:
            return
        self._set_solver_progress(12, "Coupled solve: initializing feed, chamber/nozzle, and structural loop")
        if self.cfd_status_label is not None:
            self.cfd_status_label.setText("Running coupled numerical solve...")
        QApplication.processEvents()
        resolution = self._collect_solver_resolution()
        state = self.collect_form_state()
        state["solver_flow_model"] = resolution["flow_model"]
        initial_pressure_kpa = _safe_float(
            dict(self.current_design.derived.engineering_values).get("chamber_pressure_kpa"),
            1500.0,
        )
        self.current_coupled_cycle_result = solve_coupled_cycle(
            state,
            upstream_context={"source": "qt-ui", "stage": "coupled-solve"},
            initial_chamber_pressure_kpa=initial_pressure_kpa,
            initial_design=self.current_design,
            convergence_tolerance_kpa=max(0.5, resolution["convergence_tolerance"] * 1000.0),
            max_iterations=resolution["iteration_limit"],
            progress_callback=self._set_solver_progress,
        )
        if self.current_coupled_cycle_result.get("status") == "error":
            self.solver_error_counter += 1
            self._append_solver_log(
                str(dict(self.current_coupled_cycle_result.get("payload", {})).get("error", "Coupled solver returned an error.")),
                "E-SOLVE-{0:03d}".format(self.solver_error_counter),
                "ERROR",
            )
        coupled_payload = dict(self.current_coupled_cycle_result.get("payload", {}))
        final_feed_result = dict(coupled_payload.get("feed_solver_result", {}))
        final_combustion_result = dict(coupled_payload.get("combustion_solver_result", {}))
        final_structural_result = dict(coupled_payload.get("structural_solver_result", {}))
        self.current_solver_interface_result = {
            "metadata": {
                "solver_name": "Common Solver Interface",
                "solver_version": "1.0",
                "solver_mode": "coupled-cycle",
            },
            "status": self.current_coupled_cycle_result.get("status", "unknown"),
            "payload": {
                "normalized_request": state,
                "feed_pressure_drop": final_feed_result,
                "coupled_cycle": self.current_coupled_cycle_result,
            },
            "warnings": list(self.current_coupled_cycle_result.get("warnings", [])),
            "trace": list(self.current_coupled_cycle_result.get("trace", [])),
        }
        self.current_combustion_result = final_combustion_result or {}
        self.current_structural_result = (
            final_structural_result if final_structural_result else self._build_structural_result()
        )
        self._render_status_banner()
        self._render_measurements()
        self._render_summary_text()
        self._render_metadata_text()
        self._render_plots()
        self._render_solver_snapshot()
        self._render_coupled_convergence_status()
        self._render_model_views()
        thermo = {}
        if isinstance(self.current_combustion_result, dict):
            thermo = dict(dict(self.current_combustion_result.get("metadata", {})).get("thermochemistry", {}))
        coupled_convergence = dict(coupled_payload.get("convergence", {}))
        self._set_status(
            "Done. {0}: {1} | coupled: {2}, residual {3} kPa | thermo: {4} ({5})".format(
                FLOW_MODEL_DISPLAY_NAMES.get(resolution["flow_model"], resolution["flow_model"]),
                self.current_combustion_result.get("status", "unknown"),
                self.current_coupled_cycle_result.get("status", "unknown"),
                _format_number(coupled_convergence.get("final_residual_kpa", "--"), 3),
                thermo.get("provider", "--"),
                thermo.get("status", "--"),
            ),
            self.ga_status_label.text() if self.ga_status_label else "",
            self.export_status_label.text() if self.export_status_label else "",
        )
        if self.progress_bar is not None:
            self.progress_bar.setValue(100)
        for key in self.step_progress_bars:
            self._set_step_progress(key, 100, "Complete")
        self._append_solver_warning_logs(list(self.current_coupled_cycle_result.get("warnings", [])))
        self._append_solver_log("Solve finished with status {0}.".format(self.current_coupled_cycle_result.get("status", "unknown")), "I-SOLVE-999", "INFO")

    def _collect_solver_resolution(self) -> dict:
        station_count = int(self.widgets["solver_station_count"].value())
        convergence_tolerance = float(self.widgets["solver_convergence_tolerance"].value())
        iteration_limit = int(self.widgets["solver_iteration_limit"].value())
        flow_model = self._combo_value(self.widgets["solver_flow_model"]).strip().lower()
        return {
            "flow_model": "refined" if flow_model == "refined" else "fast",
            "station_count": max(6, min(120, station_count)),
            "convergence_tolerance": max(0.0001, min(0.1, convergence_tolerance)),
            "iteration_limit": max(3, min(500, iteration_limit)),
        }

    def _on_combustion_progress(self, progress: float, message: str) -> None:
        if self.progress_bar is not None:
            self.progress_bar.setValue(int(progress))
        if self.cfd_status_label is not None:
            self.cfd_status_label.setText("{0:.0f}% - {1}".format(progress, message))
        QApplication.processEvents()

    def save_project_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            "stanth-project.stanth.json",
            "StanThrust Project (*.stanth.json);;JSON Files (*.json)",
        )
        if not path:
            return
        ga_payload = self.current_ga_result.as_dict() if self.current_ga_result else None
        save_project(Path(path), self.collect_form_state(), self.collect_objective_weights(), ga_payload)
        self._set_status(
            self.cfd_status_label.text() if self.cfd_status_label else "",
            self.ga_status_label.text() if self.ga_status_label else "",
            "Saved project document.",
        )

    def load_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Project",
            "",
            "StanThrust Project (*.stanth.json);;Liquid Engine Project (*.liquid.json);;JSON Files (*.json)",
        )
        if not path:
            return
        document = load_project(Path(path))
        self._apply_loaded_state(document.state, document.objective_weights)
        self.current_ga_result = None
        self.current_ga_candidate_state = None
        self.refresh_preview(status="Project loaded.")
        ga_text = "Loaded project with stored GA result snapshot." if document.ga_result else "Loaded project."
        if self.ga_status_label is not None:
            self.ga_status_label.setText(ga_text)

    def _apply_loaded_state(self, state: dict, objective_weights: dict) -> None:
        self._suspend_preview = True
        self.preview_timer.stop()
        self.is_expert_mode = str(state.get("ui_mode", "explorer")).lower() == "expert"
        self._refresh_mode_controls()
        self._set_combo_value(self.widgets["fuel_name"], str(state.get("fuel_name", DEFAULT_STATE.fuel_name)))
        self._set_combo_value(self.widgets["oxidizer_name"], str(state.get("oxidizer_name", DEFAULT_STATE.oxidizer_name)))
        self._set_combo_value(self.widgets["injector_type"], str(state.get("injector_type", DEFAULT_STATE.injector_type)))
        self.widgets["target_thrust_newtons"].setValue(float(state.get("target_thrust_newtons", DEFAULT_STATE.target_thrust_newtons)))
        self.widgets["target_impulse_newton_seconds"].setValue(float(state.get("target_impulse_newton_seconds", DEFAULT_STATE.target_impulse_newton_seconds)))
        self.widgets["target_diameter_mm"].setValue(float(state.get("target_diameter_mm", DEFAULT_STATE.target_diameter_mm)))
        self.widgets["mixture_ratio"].setValue(float(state.get("mixture_ratio", DEFAULT_STATE.mixture_ratio)))
        self.widgets["burn_time_seconds"].setValue(float(state.get("burn_time_seconds", DEFAULT_STATE.burn_time_seconds)))
        self.widgets["tank_diameter_mm"].setValue(float(state.get("tank_diameter_mm", DEFAULT_STATE.tank_diameter_mm)))
        self.widgets["chamber_diameter_mm"].setValue(float(state.get("chamber_diameter_mm", DEFAULT_STATE.chamber_diameter_mm)))
        self.widgets["nozzle_diameter_mm"].setValue(float(state.get("nozzle_diameter_mm", DEFAULT_STATE.nozzle_diameter_mm)))
        self.widgets["factor_of_safety"].setValue(float(state.get("factor_of_safety", DEFAULT_STATE.factor_of_safety)))
        self._set_combo_value(self.widgets["fuel_tank_material"], str(state.get("fuel_tank_material", DEFAULT_STATE.fuel_tank_material)))
        self._set_combo_value(self.widgets["oxidizer_tank_material"], str(state.get("oxidizer_tank_material", DEFAULT_STATE.oxidizer_tank_material)))
        self._set_combo_value(self.widgets["feed_system_material"], str(state.get("feed_system_material", DEFAULT_STATE.feed_system_material)))
        self._set_combo_value(self.widgets["chamber_material"], str(state.get("chamber_material", DEFAULT_STATE.chamber_material)))
        self._set_combo_value(self.widgets["nozzle_material"], str(state.get("nozzle_material", DEFAULT_STATE.nozzle_material)))
        self._set_combo_value(self.widgets["packaging_bias"], str(state.get("packaging_bias", DEFAULT_STATE.packaging_bias)))
        use_pumps = bool(state.get("use_pumps", DEFAULT_STATE.use_pumps))
        self.widgets["feed_mode_pump"].setChecked(use_pumps)
        self.widgets["feed_mode_blowdown"].setChecked(not use_pumps)
        self.widgets["regen_cooling"].setChecked(bool(state.get("regen_cooling", DEFAULT_STATE.regen_cooling)))
        self.widgets["film_cooling"].setChecked(bool(state.get("film_cooling", DEFAULT_STATE.film_cooling)))
        self.widgets["ga_enabled"].setChecked(bool(state.get("ga_enabled", True)))
        self.widgets["objective_thrust"].setValue(float(objective_weights.get("thrust", DEFAULT_OBJECTIVE_WEIGHTS["thrust"])))
        self.widgets["objective_mass"].setValue(float(objective_weights.get("mass", DEFAULT_OBJECTIVE_WEIGHTS["mass"])))
        self.widgets["objective_packaging"].setValue(float(objective_weights.get("packaging", DEFAULT_OBJECTIVE_WEIGHTS["packaging"])))
        self.widgets["objective_thermal"].setValue(float(objective_weights.get("thermal", DEFAULT_OBJECTIVE_WEIGHTS["thermal"])))
        self._set_combo_value(self.widgets["solver_flow_model"], str(state.get("solver_flow_model", "fast")).strip().lower())
        self.widgets["solver_station_count"].setValue(float(state.get("solver_station_count", 25)))
        self.widgets["solver_convergence_tolerance"].setValue(float(state.get("solver_convergence_tolerance", 0.005)))
        self.widgets["solver_iteration_limit"].setValue(float(state.get("solver_iteration_limit", 50)))
        self._suspend_preview = False

    def export_profile_dxf_dialog(self) -> None:
        if not self.current_design:
            QMessageBox.warning(self, "Nothing to export", "No design is available yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export CAD Profile DXF",
            "stanth-profile.dxf",
            "DXF Files (*.dxf);;All Files (*)",
        )
        if not path:
            return
        export_profile_dxf(Path(path), self.current_design)
        self._set_status(
            self.cfd_status_label.text() if self.cfd_status_label else "",
            self.ga_status_label.text() if self.ga_status_label else "",
            "Exported DXF profile for CAD revolve/import.",
        )

    def export_csv_dialog(self) -> None:
        if not self.current_design:
            QMessageBox.warning(self, "Nothing to export", "No design is available yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Measurements CSV",
            "stanth-measurements.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        export_measurements_csv(
            Path(path),
            self.current_design,
            self.current_combustion_result,
            self.current_solver_interface_result,
            self.current_structural_result,
        )
        self._set_status(
            self.cfd_status_label.text() if self.cfd_status_label else "",
            self.ga_status_label.text() if self.ga_status_label else "",
            "Exported CAD measurement CSV.",
        )

    def export_station_csv_dialog(self) -> None:
        if not self.current_design:
            QMessageBox.warning(self, "Nothing to export", "No design is available yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Station CSV",
            "stanth-stations.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        export_station_csv(
            Path(path),
            self.current_design,
            self.current_combustion_result,
            self.current_solver_interface_result,
            self.current_structural_result,
        )
        self._set_status(
            self.cfd_status_label.text() if self.cfd_status_label else "",
            self.ga_status_label.text() if self.ga_status_label else "",
            "Exported station CSV.",
        )

    def on_surrogate_preview(self) -> None:
        if self.summary_text is None:
            return
        state = self.collect_form_state()
        preview_lines = [
            "Surrogate preview",
            "",
            "Fuel: {0}".format(state["fuel_name"]),
            "Oxidizer: {0}".format(state["oxidizer_name"]),
            "Target thrust: {0:.0f} N".format(state["target_thrust_newtons"]),
            "Burn time: {0:.2f} s".format(state["burn_time_seconds"]),
            "",
            "This Qt shell preserves the current solver stack and can be extended with a richer surrogate preview next.",
        ]
        self.summary_text.setPlainText("\n".join(preview_lines))
        if self.output_tabs is not None:
            self.output_tabs.setCurrentIndex(4)


def run() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = StanThrustQtWindow()
    window.show()
    if QApplication.instance() is app:
        app.exec_()
