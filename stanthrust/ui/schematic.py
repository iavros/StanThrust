"""System schematic scene: propellant stack, feed routing, and engine profile."""

from __future__ import annotations

from typing import Dict, Optional

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt5.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QGraphicsView,
)

from stanthrust.theme import PALETTE, ui_font_stack
from stanthrust.ui.formatting import format_number

SCENE_WIDTH = 1100.0
SCENE_HEIGHT = 620.0

_UI_FAMILY = ui_font_stack().split(",")[0].strip().strip('"')

#: Cutaway fills for the engine profile drawing.
SHELL_FILL = "#24161A"
GAS_FILL = "#9F2431"
GAS_EDGE = "#74202A"


class SchematicView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setScene(QGraphicsScene(self))
        self.setObjectName("schematicView")
        self.setMinimumHeight(420)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:  # type: ignore[override]
        return True

    def heightForWidth(self, width: int) -> int:  # type: ignore[override]
        return int(width * SCENE_HEIGHT / SCENE_WIDTH)

    def render_design(self, design) -> None:
        scene = self.scene()
        scene.clear()
        scene.setSceneRect(0, 0, SCENE_WIDTH, SCENE_HEIGHT)

        bg = QGraphicsRectItem(QRectF(0, 0, SCENE_WIDTH, SCENE_HEIGHT))
        bg.setBrush(QColor(PALETTE["bg"]))
        bg.setPen(QPen(QColor(PALETTE["bg"])))
        scene.addItem(bg)

        self._add_text(scene, 48, 28, "Propellant Stack", PALETTE["text"], 12, True)
        self._add_text(scene, 48, 50, "Tank lengths, diameters, and feed handoff", PALETTE["muted"], 9, max_width=260)
        self._add_text(scene, 316, 28, "Feed", PALETTE["text"], 12, True)
        self._add_text(scene, 316, 50, "Architecture and delivered mixture ratio", PALETTE["muted"], 9, max_width=184)
        self._add_text(scene, 544, 28, "Engine Geometry", PALETTE["text"], 12, True)
        self._add_text(scene, 544, 50, "Resolved chamber shell and nozzle contour", PALETTE["muted"], 9, max_width=420)

        self._add_round_rect(scene, 38, 82, 246, 352, PALETTE["card_alt"])
        self._add_round_rect(scene, 312, 126, 188, 196, PALETTE["card"])
        self._add_round_rect(scene, 526, 82, 534, 426, PALETTE["card"])

        self._add_tag(scene, 58, 100, "OXIDIZER")
        self._add_tag(scene, 58, 242, "FUEL")

        scene.addRect(58, 136, 14, 82, QPen(QColor(PALETTE["oxidizer"])), QBrush(QColor(PALETTE["oxidizer"])))
        scene.addRect(58, 278, 14, 82, QPen(QColor(PALETTE["fuel"])), QBrush(QColor(PALETTE["fuel"])))
        self._add_text(scene, 92, 138, design.inputs.oxidizer_name, PALETTE["text"], 14, True, max_width=166)
        self._add_text(scene, 92, 168, "Length {0} mm".format(format_number(float(design.derived.oxidizer_tank_length_mm), 2)), PALETTE["text"], 10, True, 166)
        self._add_text(scene, 92, 194, "Diameter {0} mm".format(format_number(float(design.inputs.tank_diameter_mm), 2)), PALETTE["muted"], 9, max_width=166)
        self._add_text(scene, 92, 280, design.inputs.fuel_name, PALETTE["text"], 14, True, max_width=166)
        self._add_text(scene, 92, 310, "Length {0} mm".format(format_number(float(design.derived.fuel_tank_length_mm), 2)), PALETTE["text"], 10, True, 166)
        self._add_text(scene, 92, 336, "Diameter {0} mm".format(format_number(float(design.inputs.tank_diameter_mm), 2)), PALETTE["muted"], 9, max_width=166)
        self._add_text(
            scene,
            58,
            390,
            "Total stack {0} mm".format(format_number(float(design.derived.total_stack_length_mm), 2)),
            PALETTE["muted"],
            9,
            True,
            max_width=204,
        )

        feed_title = "Pump-fed" if design.inputs.use_pumps else "Pressure-fed"
        self._add_text(scene, 334, 150, "Feed System", PALETTE["text"], 12, True)
        self._add_text(scene, 334, 178, feed_title, PALETTE["accent_hover"], 11, True, max_width=140)
        if design.inputs.use_pumps:
            self._add_text(
                scene,
                334,
                212,
                "Fuel impeller {0} mm".format(format_number(design.derived.engineering_values.get("fuel_impeller_diameter_mm", "--"), 2)),
                PALETTE["muted"],
                9,
                max_width=144,
            )
            self._add_text(
                scene,
                334,
                236,
                "Ox impeller {0} mm".format(format_number(design.derived.engineering_values.get("oxidizer_impeller_diameter_mm", "--"), 2)),
                PALETTE["muted"],
                9,
                max_width=144,
            )
            self._add_text(
                scene,
                334,
                260,
                "Pump head {0} kPa".format(format_number(design.derived.engineering_values.get("pump_differential_pressure_kpa", "--"), 2)),
                PALETTE["muted"],
                9,
                max_width=144,
            )
            self._add_text(
                scene,
                334,
                284,
                "Motor {0} kW".format(format_number(design.derived.engineering_values.get("electric_motor_power_kw", "--"), 3)),
                PALETTE["muted"],
                9,
                max_width=144,
            )
        else:
            self._add_text(scene, 334, 212, "Blowdown and regulated tank state", PALETTE["muted"], 9, max_width=150, wrap=True)
            self._add_text(
                scene,
                334,
                252,
                "Fuel tank {0} kPa".format(format_number(design.derived.engineering_values.get("fuel_tank_pressure_kpa", "--"), 2)),
                PALETTE["muted"],
                9,
                max_width=144,
            )
            self._add_text(
                scene,
                334,
                276,
                "Ox tank {0} kPa".format(format_number(design.derived.engineering_values.get("oxidizer_tank_pressure_kpa", "--"), 2)),
                PALETTE["muted"],
                9,
                max_width=144,
            )
        self._add_text(
            scene,
            334,
            302,
            "Mixture ratio {0}".format(format_number(float(design.inputs.mixture_ratio), 3)),
            PALETTE["text"],
            10,
            True,
            max_width=144,
        )

        throat_text = "Throat {0} mm".format(
            format_number(float(design.derived.engineering_values.get("nozzle_throat_diameter_mm", 0.0)), 2)
        )
        exit_text = "Exit {0} mm".format(
            format_number(
                float(design.derived.engineering_values.get("nozzle_inner_diameter_mm", design.inputs.nozzle_diameter_mm)),
                2,
            )
        )
        ratio_text = "Area ratio {0}".format(
            format_number(design.derived.engineering_values.get("nozzle_expansion_ratio", "--"), 3)
        )
        contour_text = str(
            design.derived.engineering_values.get("nozzle_contour_method_label", "Bell contour")
        ).replace(" contour", "")

        self._add_metric_pill(scene, 544, 96, throat_text, PALETTE["accent_hover"], 142)
        self._add_metric_pill(scene, 698, 96, exit_text, PALETTE["text"], 136)
        self._add_metric_pill(scene, 846, 96, ratio_text, PALETTE["text"], 154)
        self._add_text(scene, 548, 146, contour_text, PALETTE["text"], 10, True, max_width=196)
        self._add_text(
            scene,
            548,
            168,
            "Converging {0} mm   Diverging {1} mm".format(
                format_number(design.derived.engineering_values.get("nozzle_converging_length_mm", 0.0), 2),
                format_number(design.derived.engineering_values.get("nozzle_diverging_length_mm", 0.0), 2),
            ),
            PALETTE["muted"],
            9,
            max_width=250,
        )

        cooling_text = "Regen cooling active" if design.inputs.regen_cooling else "Film cooling active" if design.inputs.film_cooling else "No active cooling"
        cooling_color = PALETTE["cooling"] if design.inputs.regen_cooling else PALETTE["film"] if design.inputs.film_cooling else PALETTE["muted"]
        self._add_metric_pill(scene, 852, 146, cooling_text, cooling_color, 176)

        profile = self._draw_engine_profile(scene, design, 542, 236, 502, 176)

        ox_pen = QPen(QColor(PALETTE["oxidizer"]), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        fuel_pen = QPen(QColor(PALETTE["fuel"]), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
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

        scene.addEllipse(profile["injector_x"] - 6, profile["injector_top_y"] - 6, 12, 12, QPen(QColor(PALETTE["oxidizer"])), QBrush(QColor(PALETTE["oxidizer"])))
        scene.addEllipse(profile["injector_x"] - 6, profile["injector_bottom_y"] - 6, 12, 12, QPen(QColor(PALETTE["fuel"])), QBrush(QColor(PALETTE["fuel"])))

        if design.inputs.regen_cooling:
            cooling_pen = QPen(QColor(PALETTE["cooling"]), 2, Qt.DashLine, Qt.RoundCap)
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
            film_pen = QPen(QColor(PALETTE["film"]), 2, Qt.DashLine, Qt.RoundCap)
            scene.addLine(profile["injector_x"] + 8, profile["center_y"] - 22, profile["injector_x"] + 44, profile["center_y"] - 8, film_pen)

        self._add_metric_pill(
            scene,
            544,
            470,
            "Max dia {0} mm".format(format_number(float(design.derived.maximum_diameter_mm), 2)),
            PALETTE["text"],
            162,
        )
        self._add_metric_pill(
            scene,
            718,
            470,
            "Thrust {0} N".format(format_number(float(design.derived.engineering_values.get("calculated_thrust_newtons", 0.0)), 2)),
            PALETTE["text"],
            170,
        )
        self._add_metric_pill(
            scene,
            900,
            470,
            "Chamber {0} kPa".format(format_number(float(design.derived.engineering_values.get("chamber_pressure_kpa", 0.0)), 2)),
            PALETTE["text"],
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
        item.setPen(QPen(QColor(PALETTE["border"]), 2))
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
            font = QFont(_UI_FAMILY, fitted_size)
            font.setBold(bold)
            item = QGraphicsTextItem(text)
            item.setFont(font)
            item.setDefaultTextColor(QColor(color))
            item.setTextWidth(max_width)
            item.document().setDocumentMargin(0)
        else:
            fitted_size = self._fit_font_size(text, size, max_width, bold, minimum_size, wrap=False)
            font = QFont(_UI_FAMILY, fitted_size)
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
    def _text_width(text: str, size: int, bold: bool = False) -> float:
        font = QFont(_UI_FAMILY, size)
        font.setBold(bold)
        return float(QFontMetrics(font).horizontalAdvance(text))

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
            font = QFont(_UI_FAMILY, candidate)
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
        rect.setBrush(QColor(PALETTE["raised"]))
        rect.setPen(QPen(QColor(PALETTE["border_soft"]), 1))
        scene.addItem(rect)
        self._add_text(scene, x + 16, y + 6, text, PALETTE["muted"], 8, True)

    def _add_metric_pill(self, scene: QGraphicsScene, x: float, y: float, text: str, color: str, width: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, width, 34), 16, 16)
        item = QGraphicsPathItem(path)
        item.setBrush(QColor(PALETTE["input"]))
        item.setPen(QPen(QColor(PALETTE["border_soft"]), 1))
        scene.addItem(item)
        accent = scene.addRect(x + 8, y + 8, 8, 18, QPen(QColor(color)), QBrush(QColor(color)))
        accent.setOpacity(0.95)
        self._add_text(scene, x + 26, y + 9, text, PALETTE["text"], 9, True, max_width=width - 36)

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
        scale = min((width - 24.0) / total_length_mm, (height * 0.44) / max_radius_mm)
        scale_x = scale
        scale_y = scale
        drawn_width = total_length_mm * scale
        chamber_start_x = x + max(12.0, (width - drawn_width) * 0.5)
        chamber_end_x = chamber_start_x + chamber_length_mm * scale_x
        center_y = y + height * 0.50
        chamber_outer_radius_px = (chamber_outer_diameter_mm / 2.0) * scale_y
        chamber_inner_radius_px = (chamber_inner_diameter_mm / 2.0) * scale_y

        centerline_end_x = chamber_start_x + drawn_width + 14.0
        scene.addLine(chamber_start_x - 14.0, center_y, centerline_end_x, center_y, QPen(QColor(PALETTE["muted_soft"]), 1, Qt.DashLine))

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
        outer_item.setBrush(QColor(SHELL_FILL))
        outer_item.setPen(QPen(QColor(PALETTE["border"]), 2))
        scene.addItem(outer_item)

        inner_path = QPainterPath()
        inner_path.moveTo(*inner_top[0])
        for px, py in inner_top[1:]:
            inner_path.lineTo(px, py)
        for px, py in inner_bottom:
            inner_path.lineTo(px, py)
        inner_path.closeSubpath()
        inner_item = QGraphicsPathItem(inner_path)
        inner_item.setBrush(QColor(GAS_FILL))
        inner_item.setPen(QPen(QColor(GAS_EDGE), 1))
        inner_item.setOpacity(0.92)
        scene.addItem(inner_item)

        throat_pen = QPen(QColor(PALETTE["accent_hover"]), 2, Qt.DashLine)
        throat_radius_px = (throat_diameter_mm / 2.0) * scale_y
        scene.addLine(throat_x, center_y - throat_radius_px - 16, throat_x, center_y + throat_radius_px + 16, throat_pen)

        exit_x = chamber_end_x + nozzle_length_mm * scale_x
        self._add_dimension_line(scene, chamber_start_x, chamber_end_x, y + height + 10, "Chamber {0} mm".format(format_number(chamber_length_mm, 2)))
        self._add_dimension_line(scene, chamber_end_x, exit_x, y + height + 34, "Nozzle {0} mm".format(format_number(nozzle_length_mm, 2)))

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
        pen = QPen(QColor(PALETTE["muted_soft"]), 1)
        scene.addLine(start_x, y, end_x, y, pen)
        scene.addLine(start_x, y - 6, start_x, y + 6, pen)
        scene.addLine(end_x, y - 6, end_x, y + 6, pen)
        span = max(1.0, end_x - start_x)
        label_width = self._text_width(label, 8, bold=True)
        label_x = start_x + (span - label_width) * 0.5
        self._add_text(scene, label_x, y - 18, label, PALETTE["muted"], 8, True)
