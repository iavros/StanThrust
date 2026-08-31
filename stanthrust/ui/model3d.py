"""Live 3D component views built from the solved engine geometry.

Every mesh, callout, and dimension in these scenes is derived from the values
the solver produced, so the views change only when the design changes.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PyQt5.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)

from stanthrust.theme import PALETTE, ui_font_stack
from stanthrust.ui.formatting import display_injector_name, format_number, safe_float
from stanthrust.visualization_geometry import build_chamber_nozzle_geometry

_UI_FAMILY = ui_font_stack().split(",")[0].strip().strip('"')

#: Surface colors for the rendered hardware, as (base, highlight) pairs.
SURFACE = {
    "hot_wall": ("#A92839", "#E16A76"),
    "jacket": ("#67747D", "#B6C0C5"),
    "housing": ("#26313A", "#48555F"),
    "impeller": ("#7A8079", "#D8D8D0"),
    "shaft": ("#D6D3C7", "#FFFFFF"),
    "oxidizer": (PALETTE["oxidizer"], "#9FD2F7"),
    "fuel": (PALETTE["fuel"], "#F3C26E"),
}


# --------------------------------------------------------------------------- #
# Camera
# --------------------------------------------------------------------------- #

_YAW_RAD = math.radians(-18.0)
_TILT_RAD = math.radians(18.0)
_COS_YAW = math.cos(_YAW_RAD)
_SIN_YAW = math.sin(_YAW_RAD)
_COS_TILT = math.cos(_TILT_RAD)
_SIN_TILT = math.sin(_TILT_RAD)

#: Eye distance in scene pixels. Large enough that the perspective taper reads
#: as depth without visibly distorting the measured proportions.
FOCAL_LENGTH_PX = 2600.0

#: Oblique shear retained from the original axonometric view, so that a model
#: seen end-on still shows its length.
_SHEAR_X = 0.10
_SHEAR_Y = 0.05


def to_view(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Rotate a model-space vector into view space: +x right, +y up, +z at the eye.

    This is a pure rotation, so it can transform surface normals as well as
    positions.
    """
    x1 = x * _COS_YAW + z * _SIN_YAW
    z1 = -x * _SIN_YAW + z * _COS_YAW
    return (
        x1,
        y * _COS_TILT - z1 * _SIN_TILT,
        y * _SIN_TILT + z1 * _COS_TILT,
    )


# --------------------------------------------------------------------------- #
# Lighting
# --------------------------------------------------------------------------- #

def _normalize(vector: Tuple[float, float, float]) -> Tuple[float, float, float]:
    length = math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (vector[0] / length, vector[1] / length, vector[2] / length)


#: Key light, fixed in view space so the lit side stays put while a model spins.
LIGHT_DIRECTION = _normalize((-0.46, 0.70, 0.55))
#: Halfway between the light and the eye, for the Blinn specular term.
_HALF_DIRECTION = _normalize((LIGHT_DIRECTION[0], LIGHT_DIRECTION[1], LIGHT_DIRECTION[2] + 1.0))
#: Weak fill light from the opposite side, so unlit faces keep some form.
_FILL_DIRECTION = _normalize((0.55, -0.30, 0.35))

AMBIENT = 0.26
DIFFUSE_GAIN = 0.74
FILL_GAIN = 0.16
SPECULAR_GAIN = 0.38
SPECULAR_POWER = 24.0
RIM_GAIN = 0.20
#: Back faces are overdrawn by the front of a closed solid; keeping them dark
#: stops them from brightening the silhouette where the overdraw is imperfect.
BACK_FACE_DIM = 0.42

#: Circumferential station rings traced over each mesh.
STATION_RING_COUNT = 6

#: Render mesh density. The solved contour carries far more axial stations than
#: a shaded surface needs, and every extra quad is a scene item rebuilt on each
#: frame of the idle rotation, so the mesh is resampled for display only.
MESH_SEGMENTS = 40
MESH_RING_LIMIT = 24

#: Idle showcase rotation. The scene is rebuilt on every tick, so the cadence is
#: kept low; it stops for good once the user orbits the model themselves.
IDLE_SPIN_INTERVAL_MS = 100
IDLE_SPIN_STEP_DEG = 1.8


def _channel(value: float) -> int:
    return max(0, min(255, int(value)))


def _dot(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def shade_surface(base: QColor, view_normal: Tuple[float, float, float]) -> QColor:
    """Light one face from its view-space normal."""
    normal = _normalize(view_normal)
    if normal[2] <= 0.0:
        intensity = AMBIENT * BACK_FACE_DIM
        highlight = 0.0
    else:
        diffuse = max(0.0, _dot(normal, LIGHT_DIRECTION))
        fill = max(0.0, _dot(normal, _FILL_DIRECTION))
        intensity = AMBIENT + DIFFUSE_GAIN * diffuse + FILL_GAIN * fill
        specular = max(0.0, _dot(normal, _HALF_DIRECTION)) ** SPECULAR_POWER
        rim = (1.0 - normal[2]) ** 3
        highlight = SPECULAR_GAIN * specular + RIM_GAIN * rim
    boost = 255.0 * highlight
    shaded = QColor(
        _channel(base.red() * intensity + boost),
        _channel(base.green() * intensity + boost),
        _channel(base.blue() * intensity + boost),
    )
    shaded.setAlpha(base.alpha())
    return shaded


def polygon_center(points: Sequence[Tuple[float, float, float]]) -> Tuple[float, float, float]:
    """Return the average of a polygon's vertices."""
    count = max(1, len(points))
    return (
        sum(point[0] for point in points) / count,
        sum(point[1] for point in points) / count,
        sum(point[2] for point in points) / count,
    )


def blend_colors(first: QColor, second: QColor) -> QColor:
    """Return the midpoint of two colors, keeping the first one's alpha."""
    blended = QColor(
        (first.red() + second.red()) // 2,
        (first.green() + second.green()) // 2,
        (first.blue() + second.blue()) // 2,
    )
    blended.setAlpha(first.alpha())
    return blended


def revolve_profile(
    profile: Sequence[Tuple[float, float]],
    max_x: float,
    phase: float,
    segments: int,
) -> List[List[Tuple[float, float, float]]]:
    """Revolve an ``(x, radius)`` profile into rings centred on the origin."""
    rings: List[List[Tuple[float, float, float]]] = []
    angles = [phase + 2.0 * math.pi * index / segments for index in range(segments)]
    for x_mm, radius_mm in profile:
        x_centered = x_mm - max_x * 0.5
        rings.append(
            [
                (x_centered, radius_mm * math.cos(theta), radius_mm * math.sin(theta))
                for theta in angles
            ]
        )
    return rings


def resample_profile(
    profile: Sequence[Tuple[float, float]], limit: int = MESH_RING_LIMIT
) -> List[Tuple[float, float]]:
    """Thin an axial profile for display, keeping its ends and its throat.

    The solved contour is unchanged; this only limits how many rings the shaded
    surface is built from.
    """
    if len(profile) <= limit:
        return list(profile)
    throat_index = min(range(len(profile)), key=lambda index: profile[index][1])
    keep = {0, len(profile) - 1, throat_index}
    for step in range(limit):
        keep.add(round(step * (len(profile) - 1) / (limit - 1)))
    return [profile[index] for index in sorted(keep)]


def inflate_polygon(
    points: Sequence[Tuple[float, float]], amount: float = 2.0
) -> List[Tuple[float, float]]:
    """Push a screen polygon's vertices out from its centre by ``amount`` pixels.

    Neighbouring quads then overlap by a fraction of a pixel, which hides the
    antialiasing seam between them. Outlining the quads instead would tint the
    seam with a single flat colour and reintroduce visible faceting.
    """
    count = max(1, len(points))
    center_x = sum(point[0] for point in points) / count
    center_y = sum(point[1] for point in points) / count
    inflated: List[Tuple[float, float]] = []
    for x, y in points:
        dx = x - center_x
        dy = y - center_y
        distance = math.sqrt(dx * dx + dy * dy)
        if distance < 1e-9:
            inflated.append((x, y))
            continue
        stretch = (distance + amount) / distance
        inflated.append((center_x + dx * stretch, center_y + dy * stretch))
    return inflated


def outward_normal(
    points: Sequence[Tuple[float, float, float]],
    solid_center: Tuple[float, float, float],
) -> Tuple[float, float, float]:
    """Return a polygon's unit normal, oriented away from ``solid_center``.

    Orienting against the solid's centre makes the result independent of vertex
    winding, which the mesh builders do not guarantee.
    """
    origin = points[0]
    edge_a = (points[1][0] - origin[0], points[1][1] - origin[1], points[1][2] - origin[2])
    edge_b = (points[2][0] - origin[0], points[2][1] - origin[1], points[2][2] - origin[2])
    normal = (
        edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
        edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
        edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
    )
    center = polygon_center(points)
    outward = (
        center[0] - solid_center[0],
        center[1] - solid_center[1],
        center[2] - solid_center[2],
    )
    if _dot(normal, normal) < 1e-18:
        # A degenerate quad, such as the collapsed ring at a cone apex.
        return _normalize(outward)
    if _dot(normal, outward) < 0.0:
        normal = (-normal[0], -normal[1], -normal[2])
    return _normalize(normal)


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
        self._spin_timer.setInterval(IDLE_SPIN_INTERVAL_MS)
        self._spin_timer.timeout.connect(self._tick_spin)
        self._spin_timer.start()

    def render_design(self, design) -> None:
        self._design = design
        self._redraw_model()

    def _tick_spin(self) -> None:
        if self._design is None or self._dragging or not self.isVisible():
            return
        self._angle_deg = (self._angle_deg + IDLE_SPIN_STEP_DEG) % 360.0
        self._redraw_model()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_drag_x = event.x()
            self._spin_timer.stop()
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
        bg.setBrush(QColor(PALETTE["bg"]))
        bg.setPen(QPen(QColor(PALETTE["bg"])))
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
        geometry = build_chamber_nozzle_geometry(design)
        geometry_profile = list(geometry["profile"])
        chamber_length = float(geometry["chamber_length_mm"])
        nozzle_length = float(geometry["nozzle_length_mm"])
        profile = [
            (float(row["x_mm"]), float(row["jacket_outer_radius_mm"]))
            for row in geometry_profile
        ]
        hot_wall_profile = [
            (float(row["x_mm"]), float(row["hot_wall_outer_radius_mm"]))
            for row in geometry_profile
        ]
        chamber_outer_radius = float(geometry_profile[0]["jacket_outer_radius_mm"])
        if len(profile) < 2:
            self._add_text(scene, 48, 110, "No model profile is available for the current design.", PALETTE["warning"], 10, 420)
            return

        max_x = max(point[0] for point in profile)
        max_radius = max(point[1] for point in profile)
        scale = min(552.0 / max(1.0, max_x), 150.0 / max(1.0, max_radius))
        origin = (414.0, 316.0)
        segments = MESH_SEGMENTS
        phase = self._spin_phase()
        hot_wall_rings = revolve_profile(
            resample_profile(hot_wall_profile), max_x, phase, segments
        )
        # The jacket is a soft translucent shell over the hot wall. Half the
        # axial rings are enough through the transparency, and back-face culling
        # keeps the quad count down at full circumferential density.
        jacket_rings = revolve_profile(
            resample_profile(profile, MESH_RING_LIMIT // 2), max_x, phase, segments
        )
        self._draw_ground_shadow(
            scene,
            (origin[0], origin[1] + max_radius * scale * 1.16),
            max_x * scale * 0.52,
            max_radius * scale * 0.30,
        )
        self._draw_ring_mesh(
            scene,
            hot_wall_rings,
            origin,
            scale,
            *SURFACE["hot_wall"],
            cap_start=True,
            cap_end=False,
        )
        if design.inputs.regen_cooling:
            self._draw_regen_rib_mesh(scene, hot_wall_profile, origin, scale, max_x, phase, values)
            self._draw_ring_mesh(
                scene,
                jacket_rings,
                origin,
                scale,
                *SURFACE["jacket"],
                cap_ends=False,
                opacity=54,
                mesh_edges=False,
            )
        if design.inputs.film_cooling:
            self._draw_film_cooling_mesh(scene, hot_wall_profile, origin, scale, max_x, phase, values, chamber_length)

        throat_diameter = format_number(geometry["throat_diameter_mm"], 2)
        exit_diameter = format_number(geometry["exit_inner_diameter_mm"], 2)
        chamber_diameter = format_number(geometry["chamber_inner_diameter_mm"], 2)
        length = format_number(geometry["total_length_mm"], 2)
        contour = str(values.get("nozzle_contour_method_label", "Nozzle contour"))
        throat_x = float(geometry["throat_x_mm"])
        throat_radius = max(0.5, float(geometry["throat_diameter_mm"]) * 0.5)
        exit_radius = max(0.5, (safe_float(values.get("nozzle_inner_diameter_mm"), design.inputs.nozzle_diameter_mm) or design.inputs.nozzle_diameter_mm) * 0.5)
        chamber_mid = self._project(chamber_length * 0.42 - max_x * 0.5, -chamber_outer_radius * 1.08, 0.0, origin, scale)
        throat_point = self._project(throat_x - max_x * 0.5, throat_radius, 0.0, origin, scale)
        exit_point = self._project(max_x * 0.5, exit_radius, 0.0, origin, scale)
        self._draw_callout(scene, (chamber_mid[0], chamber_mid[1]), (176.0, 454.0), "Chamber length {0} mm".format(format_number(chamber_length, 2)), PALETTE["text"])
        self._draw_callout(scene, (throat_point[0], throat_point[1]), (520.0, 166.0), "Throat dia {0} mm".format(throat_diameter), PALETTE["accent_hover"])
        self._draw_callout(scene, (exit_point[0], exit_point[1]), (638.0, 500.0), "Exit dia {0} mm".format(exit_diameter), PALETTE["text"])
        self._draw_callout(scene, (origin[0] + 8.0, origin[1] + max_radius * scale * 0.86), (436.0, 526.0), "Nozzle length {0} mm".format(format_number(nozzle_length, 2)), PALETTE["muted"])
        self._metric(scene, 790, 90, "Overall Length", f"{length} mm")
        self._metric(scene, 790, 152, "Chamber Inner Diameter", f"{chamber_diameter} mm")
        self._metric(scene, 790, 214, "Throat Inner Diameter", f"{throat_diameter} mm")
        self._metric(scene, 790, 276, "Exit Inner Diameter", f"{exit_diameter} mm")
        self._metric(scene, 790, 338, "Wall Thickness, Chamber / Nozzle", "{0} / {1} mm".format(
            format_number(values.get("chamber_wall_thickness_mm", "--"), 2),
            format_number(values.get("nozzle_structural_wall_thickness_mm", "--"), 2),
        ))
        cooling = (
            "Regen + Film"
            if design.inputs.regen_cooling and design.inputs.film_cooling
            else "Regen"
            if design.inputs.regen_cooling
            else "Film"
            if design.inputs.film_cooling
            else "Passive"
        )
        self._metric(scene, 790, 400, "Cooling / Contour", "{0} / {1}".format(cooling, contour))
        if design.inputs.regen_cooling:
            self._metric(
                scene,
                790,
                462,
                "Channels / Rib / Depth",
                "{0} / {1} / {2} mm".format(
                    int(round(safe_float(values.get("regen_channel_count"), 0.0) or 0.0)),
                    format_number(values.get("regen_rib_thickness_mm", "--"), 2),
                    format_number(values.get("regen_channel_depth_mm", "--"), 2),
                ),
            )
            if design.inputs.film_cooling:
                self._metric(
                    scene,
                    790,
                    524,
                    "Film Slots / Height",
                    "{0} / {1} mm".format(
                        int(round(safe_float(values.get("film_slot_count"), 0.0) or 0.0)),
                        format_number(values.get("film_slot_height_mm", "--"), 2),
                    ),
                )
        elif design.inputs.film_cooling:
            self._metric(
                scene,
                790,
                462,
                "Film Slots / Height",
                "{0} / {1} mm".format(
                    int(round(safe_float(values.get("film_slot_count"), 0.0) or 0.0)),
                    format_number(values.get("film_slot_height_mm", "--"), 2),
                ),
            )

    def _draw_regen_rib_mesh(
        self,
        scene: QGraphicsScene,
        profile: List[Tuple[float, float]],
        origin: Tuple[float, float],
        scale: float,
        max_x: float,
        phase: float,
        values: Dict[str, object],
    ) -> None:
        channel_count = max(0, int(round(safe_float(values.get("regen_channel_count"), 0.0) or 0.0)))
        if channel_count <= 0 or len(profile) < 2:
            return
        display_count = max(1, min(96, channel_count))
        rib_thickness = max(0.4, safe_float(values.get("regen_rib_thickness_mm"), 1.0) or 1.0)
        channel_depth = max(0.2, safe_float(values.get("regen_channel_depth_mm"), 1.0) or 1.0)
        rib_paths: List[Tuple[float, QPainterPath]] = []
        for rib_index in range(display_count):
            theta = phase + 2.0 * math.pi * rib_index / max(1, display_count)
            path = QPainterPath()
            first_point = True
            depth_values: List[float] = []
            for x_mm, radius_mm in profile:
                # The line is centered in the solid rib between the hot-wall
                # channel floor and the calculated outer jacket.
                display_radius = radius_mm + channel_depth * 0.5
                projected = self._project(
                    x_mm - max_x * 0.5,
                    display_radius * math.cos(theta),
                    display_radius * math.sin(theta),
                    origin,
                    scale,
                )
                if first_point:
                    path.moveTo(projected[0], projected[1])
                    first_point = False
                else:
                    path.lineTo(projected[0], projected[1])
                depth_values.append(projected[2])

            mean_depth = sum(depth_values) / max(1, len(depth_values))
            rib_paths.append((mean_depth, path))

        for mean_depth, path in sorted(rib_paths, key=lambda item: item[0]):
            alpha = 235 if mean_depth > 0.0 else 56
            rib_color = QColor(PALETTE["cooling"])
            rib_color.setAlpha(alpha)
            pen = QPen(rib_color, max(1.2, rib_thickness * scale * 0.55), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            pen.setCosmetic(True)
            scene.addPath(path, pen)

        label_point = self._project(
            profile[min(len(profile) - 1, max(1, len(profile) // 3))][0] - max_x * 0.5,
            (profile[min(len(profile) - 1, max(1, len(profile) // 3))][1] + channel_depth) * 0.76,
            (profile[min(len(profile) - 1, max(1, len(profile) // 3))][1] + channel_depth) * 0.65,
            origin,
            scale,
        )
        self._draw_callout(
            scene,
            (label_point[0], label_point[1]),
            (120.0, 144.0),
            "Regen ribs {0}x".format(channel_count),
            PALETTE["cooling"],
        )

    def _draw_film_cooling_mesh(
        self,
        scene: QGraphicsScene,
        profile: List[Tuple[float, float]],
        origin: Tuple[float, float],
        scale: float,
        max_x: float,
        phase: float,
        values: Dict[str, object],
        chamber_length: float,
    ) -> None:
        slot_count = max(0, int(round(safe_float(values.get("film_slot_count"), 0.0) or 0.0)))
        if slot_count <= 0 or len(profile) < 2:
            return

        slot_height = max(0.1, safe_float(values.get("film_slot_height_mm"), 0.5) or 0.5)
        chamber_wall = max(0.0, safe_float(values.get("chamber_wall_thickness_mm"), 0.0) or 0.0)
        chamber_inner_radius = max(
            0.35,
            (safe_float(values.get("chamber_inner_diameter_mm"), 0.0) or max(profile[0][1] - chamber_wall, 0.35))
            * 0.5,
        )

        inlet_x = profile[0][0] - max_x * 0.5
        inlet_radius = chamber_inner_radius
        slot_total_width = max(0.1, safe_float(values.get("film_slot_width_mm"), 1.0) or 1.0)
        displayed_slot_count = max(1, min(96, slot_count))
        slot_arc_length = slot_total_width / max(1, slot_count)
        slot_angle = min(
            2.0 * math.pi / max(1, displayed_slot_count) * 0.44,
            max(math.radians(0.65), slot_arc_length / max(0.1, inlet_radius)),
        )
        slot_radius = max(0.25, inlet_radius - slot_height * 0.42)
        slot_width_px = max(1.2, slot_height * scale * 0.95)

        for slot_index in range(displayed_slot_count):
            theta_center = phase + 2.0 * math.pi * slot_index / max(1, displayed_slot_count)
            if math.sin(theta_center) < -0.22:
                continue
            slot_path = QPainterPath()
            depth_values: List[float] = []
            for sample_index in range(4):
                sample = sample_index / 3.0
                theta = theta_center - slot_angle * 0.5 + slot_angle * sample
                point = self._project(
                    inlet_x,
                    slot_radius * math.cos(theta),
                    slot_radius * math.sin(theta),
                    origin,
                    scale,
                )
                if sample_index == 0:
                    slot_path.moveTo(point[0], point[1])
                else:
                    slot_path.lineTo(point[0], point[1])
                depth_values.append(point[2])
            mean_depth = sum(depth_values) / max(1, len(depth_values))
            slot_color = QColor(PALETTE["film"])
            slot_color.setAlpha(245 if mean_depth > 0.0 else 55)
            slot_pen = QPen(slot_color, slot_width_px, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            slot_pen.setCosmetic(True)
            scene.addPath(slot_path, slot_pen)

        ring_color = QColor(PALETTE["film"])
        ring_color.setAlpha(115)
        ring_pen = QPen(
            ring_color,
            max(0.8, slot_height * scale * 0.28),
            Qt.DashLine,
            Qt.RoundCap,
            Qt.RoundJoin,
        )
        ring_pen.setCosmetic(True)
        for radius_offset in (slot_height * 0.15, slot_height * 1.05):
            ring_radius = max(0.2, inlet_radius - radius_offset)
            ring_path = QPainterPath()
            for sample_index in range(49):
                theta = phase + 2.0 * math.pi * sample_index / 48.0
                point = self._project(
                    inlet_x,
                    ring_radius * math.cos(theta),
                    ring_radius * math.sin(theta),
                    origin,
                    scale,
                )
                if sample_index == 0:
                    ring_path.moveTo(point[0], point[1])
                else:
                    ring_path.lineTo(point[0], point[1])
            scene.addPath(ring_path, ring_pen)

        tick_color = QColor(PALETTE["film"])
        tick_color.setAlpha(170)
        tick_pen = QPen(
            tick_color,
            max(0.9, slot_height * scale * 0.32),
            Qt.SolidLine,
            Qt.RoundCap,
            Qt.RoundJoin,
        )
        tick_pen.setCosmetic(True)
        visible_ticks = min(10, displayed_slot_count)
        tick_length = min(max(4.0, chamber_length * 0.035), max(4.0, slot_height * 8.0))
        for tick_index in range(visible_ticks):
            theta = phase + 2.0 * math.pi * tick_index / max(1, visible_ticks)
            if math.sin(theta) < -0.2:
                continue
            start_point = self._project(
                inlet_x,
                slot_radius * math.cos(theta),
                slot_radius * math.sin(theta),
                origin,
                scale,
            )
            end_radius = max(0.2, slot_radius - slot_height * 0.45)
            end_point = self._project(
                inlet_x + tick_length,
                end_radius * math.cos(theta),
                end_radius * math.sin(theta),
                origin,
                scale,
            )
            path = QPainterPath()
            path.moveTo(start_point[0], start_point[1])
            path.lineTo(end_point[0], end_point[1])
            scene.addPath(path, tick_pen)

    def _draw_tanks(self, scene: QGraphicsScene, design) -> None:
        self._component_title(
            scene,
            "Propellant Tanks",
            "Calculated oxidizer and fuel tank envelopes.",
        )
        values = dict(design.derived.engineering_values)
        ox_diameter = safe_float(values.get("oxidizer_tank_outer_diameter_mm"), design.inputs.tank_diameter_mm) or design.inputs.tank_diameter_mm
        fuel_diameter = safe_float(values.get("fuel_tank_outer_diameter_mm"), design.inputs.tank_diameter_mm) or design.inputs.tank_diameter_mm
        ox_length = float(design.derived.oxidizer_tank_length_mm)
        fuel_length = float(design.derived.fuel_tank_length_mm)
        ox_feed_length = safe_float(values.get("oxidizer_feed_tube_length_mm"), design.derived.feed_system_bay_length_mm * 0.72) or design.derived.feed_system_bay_length_mm * 0.72
        fuel_feed_length = safe_float(values.get("fuel_feed_tube_length_mm"), design.derived.feed_system_bay_length_mm * 0.72) or design.derived.feed_system_bay_length_mm * 0.72
        ox_feed_diameter = safe_float(values.get("oxidizer_feed_tube_diameter_mm"), max(3.0, ox_diameter * 0.04)) or max(3.0, ox_diameter * 0.04)
        fuel_feed_diameter = safe_float(values.get("fuel_feed_tube_diameter_mm"), max(3.0, fuel_diameter * 0.04)) or max(3.0, fuel_diameter * 0.04)
        max_length = max(1.0, ox_length, fuel_length)
        max_feed_length = max(1.0, ox_feed_length, fuel_feed_length)
        max_diameter = max(1.0, ox_diameter, fuel_diameter)
        scale = min(520.0 / (max_length + max_feed_length), 102.0 / max_diameter)
        ox_origin = (356.0, 210.0)
        fuel_origin = (356.0, 420.0)
        for origin, length, diameter in (
            (ox_origin, ox_length, ox_diameter),
            (fuel_origin, fuel_length, fuel_diameter),
        ):
            self._draw_ground_shadow(
                scene,
                (origin[0], origin[1] + diameter * 0.5 * scale * 1.22),
                length * scale * 0.50,
                diameter * 0.5 * scale * 0.30,
                strength=0.5,
            )
        self._draw_axial_cylinder_mesh(scene, ox_length, ox_diameter * 0.5, ox_origin, scale, *SURFACE["oxidizer"])
        self._draw_axial_cylinder_mesh(scene, fuel_length, fuel_diameter * 0.5, fuel_origin, scale, *SURFACE["fuel"])
        self._add_text(scene, 74, 154, "Oxidizer Tank", PALETTE["text"], 11, 180)
        self._add_text(scene, 74, 364, "Fuel Tank", PALETTE["text"], 11, 180)
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
        self._draw_feed_connector(scene, (ox_tube_start[0], ox_tube_start[1]), ox_outlet, PALETTE["oxidizer"], ox_tube_width)
        self._draw_feed_connector(scene, (fuel_tube_start[0], fuel_tube_start[1]), fuel_outlet, PALETTE["fuel"], fuel_tube_width)
        self._draw_small_nozzle(scene, ox_outlet, PALETTE["oxidizer"], ox_tube_width)
        self._draw_small_nozzle(scene, fuel_outlet, PALETTE["fuel"], fuel_tube_width)
        self._draw_callout(scene, (ox_front[0], ox_front[1]), (552.0, 128.0), "Ox length {0} mm".format(format_number(ox_length, 2)), PALETTE["oxidizer"])
        self._draw_callout(scene, (ox_body[0], ox_body[1]), (154.0, 286.0), "Ox dia {0} mm".format(format_number(ox_diameter, 2)), PALETTE["text"])
        self._draw_callout(scene, (fuel_front[0], fuel_front[1]), (552.0, 344.0), "Fuel length {0} mm".format(format_number(fuel_length, 2)), PALETTE["fuel"])
        self._draw_callout(scene, (fuel_body[0], fuel_body[1]), (154.0, 500.0), "Fuel dia {0} mm".format(format_number(fuel_diameter, 2)), PALETTE["text"])
        self._metric(scene, 790, 104, "Ox Length", "{0} mm".format(format_number(ox_length, 2)))
        self._metric(scene, 790, 166, "Fuel Length", "{0} mm".format(format_number(fuel_length, 2)))
        self._metric(scene, 790, 228, "Feed Tube", "{0} / {1} mm".format(format_number(ox_feed_diameter, 2), format_number(fuel_feed_diameter, 2)))
        self._metric(scene, 790, 290, "Fuel Wall", "{0} mm".format(format_number(values.get("fuel_tank_wall_thickness_mm", "--"), 2)))
        self._metric(scene, 790, 352, "Ox Wall", "{0} mm".format(format_number(values.get("oxidizer_tank_wall_thickness_mm", "--"), 2)))

    def _draw_pumps(self, scene: QGraphicsScene, design) -> None:
        self._component_title(
            scene,
            "Feed Hardware",
            "Calculated impeller geometry with feed hardware context.",
        )
        values = dict(design.derived.engineering_values)
        if not design.inputs.use_pumps:
            self._add_text(scene, 86, 176, "Pressure-fed architecture selected.", PALETTE["text"], 15, 420)
            self._draw_regulator_3d(scene, (324.0, 302.0), 1.0)
            self._metric(scene, 790, 104, "Fuel Tank", "{0} kPa".format(format_number(values.get("fuel_tank_pressure_kpa", "--"), 2)))
            self._metric(scene, 790, 166, "Ox Tank", "{0} kPa".format(format_number(values.get("oxidizer_tank_pressure_kpa", "--"), 2)))
            self._metric(scene, 790, 228, "Mode", "Blowdown")
            return

        fuel_geometry = self._pump_geometry(values, "fuel", 42.0)
        ox_geometry = self._pump_geometry(values, "oxidizer", 45.0)
        max_casing_diameter = max(1.0, fuel_geometry["casing_diameter_mm"], ox_geometry["casing_diameter_mm"])
        motor_length_mm = max(1.0, safe_float(values.get("electric_motor_envelope_length_mm"), 80.0) or 80.0)
        motor_height_mm = max(1.0, safe_float(values.get("electric_motor_envelope_height_mm"), 44.0) or 44.0)
        motor_depth_mm = max(1.0, safe_float(values.get("electric_motor_envelope_depth_mm"), 48.0) or 48.0)
        display_scale = min(2.85, 188.0 / max_casing_diameter, 300.0 / motor_length_mm)
        fuel_geometry["display_scale"] = display_scale
        ox_geometry["display_scale"] = display_scale

        fuel_radius = self._pump_outer_radius(fuel_geometry)
        ox_radius = self._pump_outer_radius(ox_geometry)
        motor_width = motor_length_mm * display_scale
        motor_origin = (390.0, 412.0)
        self._draw_ground_shadow(
            scene,
            (motor_origin[0], motor_origin[1] + motor_height_mm * display_scale * 0.72),
            motor_length_mm * display_scale * 0.62,
            motor_height_mm * display_scale * 0.34,
            strength=0.5,
        )
        self._draw_box_mesh(
            scene,
            motor_origin,
            motor_length_mm,
            motor_height_mm,
            motor_depth_mm,
            display_scale,
            SURFACE["housing"][0],
            PALETTE["muted"],
        )
        self._add_text(scene, motor_origin[0] + 22.0, motor_origin[1] + 8.0, "Electric Motor", PALETTE["text"], 10, 160)
        fuel_origin = (236.0, 256.0)
        ox_origin = (542.0, 256.0)
        self._draw_shaft(
            scene,
            (fuel_origin[0] + fuel_radius * 0.74, fuel_origin[1] + fuel_radius * 0.54),
            (motor_origin[0] - motor_width * 0.46, motor_origin[1] + 6.0),
            PALETTE["muted"],
        )
        self._draw_shaft(
            scene,
            (ox_origin[0] - ox_radius * 0.66, ox_origin[1] + ox_radius * 0.54),
            (motor_origin[0] + motor_width * 0.46, motor_origin[1] + 6.0),
            PALETTE["muted"],
        )
        self._draw_pump_assembly(scene, fuel_origin, fuel_geometry, PALETTE["fuel"], "Fuel Pump")
        self._draw_pump_assembly(scene, ox_origin, ox_geometry, PALETTE["oxidizer"], "Ox Pump")
        self._draw_pump_callouts(scene, fuel_origin, fuel_geometry, PALETTE["fuel"], left_side=True)
        self._draw_pump_callouts(scene, ox_origin, ox_geometry, PALETTE["oxidizer"], left_side=False)
        self._metric(scene, 790, 104, "Fuel Dia", "{0} mm".format(format_number(fuel_geometry["diameter_mm"], 2)))
        self._metric(scene, 790, 166, "Ox Dia", "{0} mm".format(format_number(ox_geometry["diameter_mm"], 2)))
        self._metric(
            scene,
            790,
            228,
            "Width",
            "{0} / {1} mm".format(
                format_number(fuel_geometry["width_mm"], 2),
                format_number(ox_geometry["width_mm"], 2),
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
                format_number(fuel_geometry["blade_angle_deg"], 1),
                format_number(ox_geometry["blade_angle_deg"], 1),
            ),
        )
        self._metric(
            scene,
            790,
            414,
            "Casing Dia",
            "{0} / {1} mm".format(
                format_number(fuel_geometry["casing_diameter_mm"], 2),
                format_number(ox_geometry["casing_diameter_mm"], 2),
            ),
        )
        self._metric(scene, 790, 476, "Head / Motor", "{0} kPa / {1} kW".format(
            format_number(values.get("pump_differential_pressure_kpa", "--"), 1),
            format_number(values.get("electric_motor_power_kw", "--"), 2),
        ))

    def _draw_injector(self, scene: QGraphicsScene, design) -> None:
        self._component_title(
            scene,
            "Injector",
            "Calculated injector face and element geometry.",
        )
        values = dict(design.derived.engineering_values)
        injector_type = str(design.inputs.injector_type).strip().lower()
        face_diameter = safe_float(values.get("injector_face_diameter_mm"), design.inputs.chamber_diameter_mm) or design.inputs.chamber_diameter_mm
        face_thickness = safe_float(values.get("injector_face_thickness_mm"), face_diameter * 0.075) or face_diameter * 0.075
        recess_diameter = safe_float(values.get("injector_recess_diameter_mm"), face_diameter * 0.72) or face_diameter * 0.72
        face_radius = max(0.5, face_diameter * 0.5)
        display_scale = min(3.7, 260.0 / max(1.0, face_diameter))
        origin = (360.0, 308.0)
        face_radius_px = face_radius * display_scale
        recess_radius_px = max(1.0, recess_diameter * 0.5 * display_scale)
        face_thickness_px = max(9.0, face_thickness * display_scale)
        self._draw_disc_mesh(scene, face_radius_px, face_thickness_px, origin, 1.0, SURFACE["housing"][0], PALETTE["muted"], center=(0.0, 0.0, 0.0))
        self._draw_disc_mesh(scene, recess_radius_px, face_thickness_px * 1.12, origin, 1.0, PALETTE["input"], PALETTE["border"], center=(0.0, 0.0, face_thickness_px * 0.18))
        if injector_type == "pintle":
            tip_diameter = safe_float(values.get("pintle_tip_diameter_mm"), face_diameter * 0.24) or face_diameter * 0.24
            stem_diameter = safe_float(values.get("pintle_stem_diameter_mm"), tip_diameter * 0.62) or tip_diameter * 0.62
            annulus_gap = safe_float(values.get("pintle_annulus_gap_mm"), 1.0) or 1.0
            projection_length = safe_float(values.get("pintle_projection_length_mm"), face_diameter * 0.55) or face_diameter * 0.55
            tip_radius_px = tip_diameter * 0.5 * display_scale
            stem_radius_px = stem_diameter * 0.5 * display_scale
            annulus_radius_px = (tip_diameter * 0.5 + annulus_gap) * display_scale
            projection_px = max(face_thickness_px * 1.1, projection_length * display_scale)
            self._draw_disc_mesh(scene, annulus_radius_px, face_thickness_px * 1.24, origin, 1.0, PALETTE["bg"], PALETTE["oxidizer"], center=(0.0, 0.0, face_thickness_px * 0.60))
            self._draw_disc_mesh(scene, stem_radius_px, projection_px, origin, 1.0, *SURFACE["shaft"], center=(0.0, 0.0, projection_px * 0.42))
            self._draw_disc_mesh(scene, tip_radius_px, max(10.0, face_thickness_px * 1.35), origin, 1.0, *SURFACE["fuel"], center=(0.0, 0.0, projection_px * 0.94))
            tip_point = self._project(0.0, -tip_radius_px, projection_px * 0.94, origin, 1.0)
            gap_point = self._project(annulus_radius_px, 0.0, face_thickness_px * 0.60, origin, 1.0)
            self._draw_callout(scene, (tip_point[0], tip_point[1]), (120.0, 472.0), "Pintle tip {0} mm".format(format_number(tip_diameter, 2)), PALETTE["fuel"])
            self._draw_callout(scene, (gap_point[0], gap_point[1]), (552.0, 172.0), "Annulus gap {0} mm".format(format_number(annulus_gap, 2)), PALETTE["oxidizer"])
            self._metric(scene, 790, 290, "Tip / Gap", "{0} / {1} mm".format(format_number(tip_diameter, 2), format_number(annulus_gap, 2)))
            self._metric(scene, 790, 414, "Projection", "{0} mm".format(format_number(projection_length, 2)))
        else:
            orifice_diameter = safe_float(values.get("impinging_orifice_diameter_mm"), 1.0) or 1.0
            impinging_angle = safe_float(values.get("impinging_angle_deg"), 30.0) or 30.0
            pair_spacing = safe_float(values.get("impinging_pair_spacing_mm"), face_diameter * 0.085) or face_diameter * 0.085
            ring_diameter = safe_float(values.get("injector_element_ring_diameter_mm"), face_diameter * 0.72) or face_diameter * 0.72
            convergence_height = safe_float(values.get("impinging_convergence_height_mm"), face_diameter * 0.12) or face_diameter * 0.12
            element_count = max(2, int(round(safe_float(values.get("impinging_element_count"), 2.0) or 2.0)))
            orifice_count = max(element_count * 2, int(round(safe_float(values.get("impinging_orifice_count"), element_count * 2) or element_count * 2)))
            port_radius_px = max(2.6, orifice_diameter * 0.5 * display_scale)
            spacing_px = pair_spacing * display_scale
            ring_radius_px = max(1.0, ring_diameter * 0.5 * display_scale)
            jet_pen = QPen(QColor(PALETTE["oxidizer"]), 1.0, Qt.SolidLine, Qt.RoundCap)
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
                self._draw_projected_orifice(scene, origin, port_x, port_y, port_z, port_radius_px, PALETTE["oxidizer"])
            port_point = first_port_point or self._project(ring_radius_px, 0.0, face_thickness_px, origin, 1.0)
            self._draw_callout(scene, (port_point[0], port_point[1]), (552.0, 172.0), "Orifice dia {0} mm".format(format_number(orifice_diameter, 2)), PALETTE["oxidizer"])
            ring_point = self._project(-ring_radius_px * 0.65, -ring_radius_px * 0.76, face_thickness_px * 0.88, origin, 1.0)
            self._draw_callout(scene, (ring_point[0], ring_point[1]), (132.0, 438.0), "Ring dia {0} mm".format(format_number(ring_diameter, 2)), PALETTE["muted"])
            self._metric(scene, 790, 290, "Orifice", "{0} mm".format(format_number(orifice_diameter, 2)))
            self._metric(
                scene,
                790,
                414,
                "Angle / Spacing",
                "{0} deg / {1} mm".format(format_number(impinging_angle, 1), format_number(pair_spacing, 2)),
            )
            self._metric(scene, 790, 476, "Holes", str(orifice_count))
            self._metric(scene, 790, 538, "Ring Dia", "{0} mm".format(format_number(ring_diameter, 2)))
        face_point = self._project(face_radius_px, 0.0, 0.0, origin, 1.0)
        thickness_point = self._project(0.0, face_radius_px * 0.70, face_thickness_px * 0.56, origin, 1.0)
        self._draw_callout(scene, (face_point[0], face_point[1]), (552.0, 392.0), "Face dia {0} mm".format(format_number(face_diameter, 2)), PALETTE["text"])
        self._draw_callout(scene, (thickness_point[0], thickness_point[1]), (126.0, 150.0), "Face thick {0} mm".format(format_number(face_thickness, 2)), PALETTE["muted"])
        self._add_text(scene, 252, 520, display_injector_name(injector_type), PALETTE["text"], 11, 220)
        self._metric(scene, 790, 104, "Injector", display_injector_name(injector_type))
        self._metric(scene, 790, 166, "Face Dia", "{0} mm".format(format_number(face_diameter, 2)))
        self._metric(scene, 790, 228, "Face Thick", "{0} mm".format(format_number(face_thickness, 2)))
        self._metric(scene, 790, 352, "Drop", "{0} kPa".format(format_number(values.get("injector_pressure_drop_kpa", "--"), 2)))

    def _component_title(self, scene: QGraphicsScene, title: str, subtitle: str) -> None:
        title_item = scene.addText(title, QFont(_UI_FAMILY, 13, QFont.Bold))
        title_item.setDefaultTextColor(QColor(PALETTE["text"]))
        title_item.setPos(48, 28)
        self._add_text(scene, 48, 62, subtitle, PALETTE["muted"], 9, 640)

    def _add_round_rect(self, scene: QGraphicsScene, rect: QRectF, radius: float, pen: QPen, brush: QBrush) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        item.setBrush(brush)
        scene.addItem(item)

    def _project(
        self, x: float, y: float, z: float, origin: Tuple[float, float], scale: float
    ) -> Tuple[float, float, float]:
        """Project a model-space point to scene coordinates plus a depth key."""
        x1, y2, z2 = to_view(x, y, z)
        depth_px = z2 * scale
        perspective = FOCAL_LENGTH_PX / max(FOCAL_LENGTH_PX * 0.4, FOCAL_LENGTH_PX - depth_px)
        return (
            origin[0] + (x1 * scale + depth_px * _SHEAR_X) * perspective,
            origin[1] + (-y2 * scale + depth_px * _SHEAR_Y) * perspective,
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
        opacity: int = 255,
        mesh_edges: bool = True,
        cap_start: Optional[bool] = None,
        cap_end: Optional[bool] = None,
    ) -> None:
        if len(rings) < 2:
            return
        projected = [[self._project(x, y, z, origin, scale) for x, y, z in ring] for ring in rings]
        solid_center = polygon_center([point for ring in rings for point in ring])
        base = QColor(color)
        base.setAlpha(max(0, min(255, opacity)))

        # Face normals first, accumulated per vertex so that shading can be
        # interpolated across each quad rather than stepping between facets.
        vertex_normals = [[(0.0, 0.0, 0.0)] * len(ring) for ring in rings]
        quads = []
        for ring_index in range(len(rings) - 1):
            near_world = rings[ring_index]
            far_world = rings[ring_index + 1]
            count = len(near_world)
            for segment_index in range(count):
                next_index = (segment_index + 1) % count
                normal = outward_normal(
                    (
                        near_world[segment_index],
                        near_world[next_index],
                        far_world[next_index],
                        far_world[segment_index],
                    ),
                    solid_center,
                )
                quads.append((ring_index, segment_index, next_index, normal))
                for row, column in (
                    (ring_index, segment_index),
                    (ring_index, next_index),
                    (ring_index + 1, next_index),
                    (ring_index + 1, segment_index),
                ):
                    total = vertex_normals[row][column]
                    vertex_normals[row][column] = (
                        total[0] + normal[0],
                        total[1] + normal[1],
                        total[2] + normal[2],
                    )

        shade_cache: Dict[Tuple[int, int], QColor] = {}

        def vertex_shade(row: int, column: int) -> QColor:
            key = (row, column)
            cached = shade_cache.get(key)
            if cached is None:
                cached = shade_surface(base, to_view(*_normalize(vertex_normals[row][column])))
                shade_cache[key] = cached
            return cached

        # A translucent shell should show what is behind it, not its own far
        # side, so its back faces are dropped instead of being overdrawn.
        cull_back = opacity < 255

        surfaces: List[Tuple[float, List[Tuple[float, float, float]], QColor, QColor]] = []
        for ring_index, segment_index, next_index, normal in quads:
            if cull_back and to_view(*normal)[2] <= 0.0:
                continue
            near_screen = projected[ring_index]
            far_screen = projected[ring_index + 1]
            quad_screen = [
                near_screen[segment_index],
                near_screen[next_index],
                far_screen[next_index],
                far_screen[segment_index],
            ]
            depth = sum(point[2] for point in quad_screen) * 0.25
            near_shade = blend_colors(
                vertex_shade(ring_index, segment_index),
                vertex_shade(ring_index + 1, segment_index),
            )
            far_shade = blend_colors(
                vertex_shade(ring_index, next_index),
                vertex_shade(ring_index + 1, next_index),
            )
            surfaces.append((depth, quad_screen, near_shade, far_shade))

        start_is_capped = cap_ends if cap_start is None else cap_start
        end_is_capped = cap_ends if cap_end is None else cap_end
        for is_capped, ring_index in ((start_is_capped, 0), (end_is_capped, len(rings) - 1)):
            if not is_capped:
                continue
            ring_screen = projected[ring_index]
            depth = sum(point[2] for point in ring_screen) / max(1, len(ring_screen))
            cap_shade = shade_surface(base, to_view(*outward_normal(rings[ring_index], solid_center)))
            surfaces.append((depth, list(ring_screen), cap_shade, cap_shade))

        no_pen = QPen(Qt.NoPen)
        for _depth, points, near_shade, far_shade in sorted(surfaces, key=lambda item: item[0]):
            outline = inflate_polygon([(point[0], point[1]) for point in points])
            polygon = QPolygonF([QPointF(x, y) for x, y in outline])
            if near_shade == far_shade:
                brush = QBrush(near_shade)
            else:
                gradient = QLinearGradient(
                    QPointF((points[0][0] + points[3][0]) * 0.5, (points[0][1] + points[3][1]) * 0.5),
                    QPointF((points[1][0] + points[2][0]) * 0.5, (points[1][1] + points[2][1]) * 0.5),
                )
                gradient.setColorAt(0.0, near_shade)
                gradient.setColorAt(1.0, far_shade)
                brush = QBrush(gradient)
            scene.addPolygon(polygon, no_pen, brush)

        if mesh_edges:
            self._draw_mesh_wireframe(scene, projected, wire_color, opacity)

    def _draw_mesh_wireframe(
        self,
        scene: QGraphicsScene,
        projected: List[List[Tuple[float, float, float]]],
        wire_color: str,
        opacity: int,
    ) -> None:
        """Trace a few station rings across the near side of a mesh.

        Circumferential rings read as cross-sections of the solved contour.
        Longitudinal lines are deliberately omitted: at this segment count they
        add noise rather than shape information.
        """
        wire = QColor(wire_color)
        wire.setAlpha(max(0, min(90, int(opacity * 0.30))))
        pen = QPen(wire, 0.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        pen.setCosmetic(True)

        ring_count = len(projected)
        ring_step = max(1, -(-(ring_count - 1) // STATION_RING_COUNT))
        for ring_index in range(0, ring_count, ring_step):
            ring = projected[ring_index]
            axis_depth = sum(point[2] for point in ring) / max(1, len(ring))
            for segment_index in range(len(ring)):
                p0 = ring[segment_index]
                p1 = ring[(segment_index + 1) % len(ring)]
                if (p0[2] + p1[2]) * 0.5 <= axis_depth:
                    continue
                scene.addLine(p0[0], p0[1], p1[0], p1[1], pen)

    def _draw_ground_shadow(
        self,
        scene: QGraphicsScene,
        center: Tuple[float, float],
        radius_x: float,
        radius_y: float,
        strength: float = 0.55,
    ) -> None:
        """Lay a soft contact shadow under an assembly so it does not float."""
        gradient = QRadialGradient(QPointF(center[0], center[1]), max(4.0, radius_x))
        core = QColor(PALETTE["overlay"])
        core.setAlphaF(max(0.0, min(1.0, strength)))
        edge = QColor(core)
        edge.setAlpha(0)
        gradient.setColorAt(0.0, core)
        gradient.setColorAt(0.55, QColor(core.red(), core.green(), core.blue(), int(core.alpha() * 0.45)))
        gradient.setColorAt(1.0, edge)
        rect = QRectF(center[0] - radius_x, center[1] - radius_y, radius_x * 2.0, radius_y * 2.0)
        scene.addEllipse(rect, QPen(Qt.NoPen), QBrush(gradient))

    def _axial_cylinder_rings(
        self,
        length: float,
        radius: float,
        slices: int = 10,
        segments: int = MESH_SEGMENTS,
        phase: float = 0.0,
    ) -> List[List[Tuple[float, float, float]]]:
        profile = [
            (length * slice_index / max(1, slices), radius) for slice_index in range(slices + 1)
        ]
        return revolve_profile(profile, length, phase, segments)

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
            ("lbf", "rbf", "rtf", "ltf"),
            ("lbb", "rbb", "rtb", "ltb"),
            ("ltf", "rtf", "rtb", "ltb"),
            ("lbf", "rbf", "rbb", "lbb"),
            ("lbf", "ltf", "ltb", "lbb"),
            ("rbf", "rtf", "rtb", "rbb"),
        ]
        projected = {key: self._project(*point, origin, scale) for key, point in vertices.items()}
        base = QColor(color)
        edge_pen = QPen(QColor(wire_color), 0.6)
        surfaces = []
        for keys in faces:
            world = [vertices[key] for key in keys]
            points = [projected[key] for key in keys]
            depth_value = sum(point[2] for point in points) / len(points)
            normal = to_view(*outward_normal(world, (0.0, 0.0, 0.0)))
            surfaces.append((depth_value, points, shade_surface(base, normal)))
        for _depth_value, points, shade in sorted(surfaces, key=lambda item: item[0]):
            polygon = QPolygonF([QPointF(point[0], point[1]) for point in points])
            scene.addPolygon(polygon, edge_pen, QBrush(shade))

    def _pump_geometry(self, values: Dict[str, object], prefix: str, fallback_diameter: float) -> Dict[str, float]:
        diameter = max(1.0, safe_float(values.get(f"{prefix}_impeller_diameter_mm"), fallback_diameter) or fallback_diameter)
        width = max(0.5, safe_float(values.get(f"{prefix}_impeller_width_mm"), diameter * 0.18) or diameter * 0.18)
        hub_diameter = max(0.5, safe_float(values.get(f"{prefix}_impeller_hub_diameter_mm"), diameter * 0.42) or diameter * 0.42)
        eye_diameter = max(0.5, safe_float(values.get(f"{prefix}_impeller_eye_diameter_mm"), diameter * 0.48) or diameter * 0.48)
        blade_count = int(round(safe_float(values.get(f"{prefix}_impeller_blade_count"), 6.0) or 6.0))
        blade_angle = safe_float(values.get(f"{prefix}_impeller_blade_angle_deg"), 22.0) or 22.0
        blade_thickness = max(0.1, safe_float(values.get(f"{prefix}_impeller_blade_thickness_mm"), width * 0.18) or width * 0.18)
        tip_clearance = max(0.0, safe_float(values.get(f"{prefix}_impeller_tip_clearance_mm"), diameter * 0.0075) or 0.0)
        casing_diameter = max(
            diameter + 2.0 * tip_clearance,
            safe_float(
                values.get(f"{prefix}_pump_casing_diameter_mm"),
                diameter + 2.0 * tip_clearance + max(4.0, width * 0.35),
            )
            or diameter + 2.0 * tip_clearance,
        )
        casing_depth = max(
            width,
            safe_float(
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
            SURFACE["housing"][0],
            PALETTE["border"],
            center=(0.0, 0.0, -casing_depth * 0.20),
        )
        self._draw_projected_ring(scene, origin, casing_radius, casing_depth * 0.12, QColor(PALETTE["border"]), 96, 1.2)
        self._draw_impeller_mesh(scene, origin, geometry, color)
        self._add_text(scene, origin[0] - 68, 540, label, PALETTE["text"], 10, 150)

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
            "Casing {0} mm".format(format_number(geometry["casing_diameter_mm"], 2)),
            color,
        )
        self._draw_callout(
            scene,
            (dia_point[0], dia_point[1]),
            dia_label,
            "Impeller {0} mm".format(format_number(geometry["diameter_mm"], 2)),
            color,
        )
        self._draw_callout(
            scene,
            (eye_point[0], eye_point[1]),
            eye_label,
            "Eye {0} mm".format(format_number(geometry["eye_diameter_mm"], 2)),
            PALETTE["text"],
        )
        width_label_y = 508.0
        self._draw_callout(
            scene,
            (origin[0], origin[1] + radius * 0.72),
            (dia_label[0], width_label_y),
            "Width {0} mm / {1}".format(format_number(geometry["width_mm"], 2), geometry["blade_count"]),
            PALETTE["muted"],
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

        self._draw_disc_mesh(scene, radius, base_depth, origin, 1.0, *SURFACE["impeller"], center=(0.0, 0.0, front_z * 0.30))
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

        blade_pen = QPen(QColor("#F1F0E8"), 0.45)
        blade_hub = (0.0, 0.0, front_z)
        for _depth, points, world, color in sorted(surfaces, key=lambda item: item[0]):
            normal = to_view(*outward_normal(world, blade_hub))
            scene.addPolygon(
                QPolygonF([QPointF(point[0], point[1]) for point in points]),
                blade_pen,
                QBrush(shade_surface(QColor(color), normal)),
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
            *SURFACE["shaft"],
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
    ) -> List[Tuple[float, List[Tuple[float, float, float]], List[Tuple[float, float, float]], str]]:
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
            surfaces.append((depth, points, raw_points, metal))
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

    def _draw_shaft(self, scene: QGraphicsScene, start: Tuple[float, float], end: Tuple[float, float], color: str) -> None:
        pen = QPen(QColor(color), 5, Qt.SolidLine, Qt.RoundCap)
        scene.addLine(start[0], start[1], end[0], end[1], pen)
        shine = QColor("#FFFFFF")
        shine.setAlpha(65)
        scene.addLine(start[0] + 2, start[1] - 2, end[0] + 2, end[1] - 2, QPen(shine, 1.4, Qt.SolidLine, Qt.RoundCap))

    def _draw_regulator_3d(self, scene: QGraphicsScene, origin: Tuple[float, float], scale: float) -> None:
        self._draw_box_mesh(scene, origin, 250.0, 88.0, 74.0, scale, SURFACE["housing"][0], PALETTE["muted"])
        self._draw_disc_mesh(scene, 36.0, 24.0, (origin[0] - 76.0, origin[1] - 2.0), scale, PALETTE["accent"], PALETTE["accent_hover"], center=(0.0, 0.0, 36.0))
        self._add_text(scene, origin[0] - 40, origin[1] - 14, "Regulated Feed", PALETTE["text"], 11, 160)

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
        font = QFont(_UI_FAMILY, 8)
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
        box_item.setBrush(QColor(PALETTE["input"]))
        box_item.setPen(QPen(QColor(PALETTE["border_soft"]), 1.0))
        scene.addItem(box_item)
        text_item = scene.addSimpleText(label, font)
        text_item.setBrush(QColor(PALETTE["text"]))
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
        item.setFont(QFont(_UI_FAMILY, size))
        item.setDefaultTextColor(QColor(color))
        item.setTextWidth(width)
        item.document().setDocumentMargin(0)
        item.setPos(x, y)
        scene.addItem(item)

    def _fit_font_size(self, text: str, max_width: float, preferred: int, minimum: int = 7) -> int:
        for size in range(preferred, minimum - 1, -1):
            if QFontMetrics(QFont(_UI_FAMILY, size)).horizontalAdvance(text) <= max_width:
                return size
        return minimum

    def _metric(self, scene: QGraphicsScene, x: float, y: float, label: str, value: str) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, 244, 46), 8, 8)
        item = QGraphicsPathItem(path)
        item.setBrush(QColor(PALETTE["input"]))
        item.setPen(QPen(QColor(PALETTE["border_soft"]), 1))
        scene.addItem(item)
        content_width = 216.0
        label_size = self._fit_font_size(label, content_width, 8, 7)
        value_size = self._fit_font_size(value, content_width, 9, 7)
        self._add_text(scene, x + 14, y + 6, label, PALETTE["muted"], label_size, content_width)
        self._add_text(scene, x + 14, y + 22, value, PALETTE["text"], value_size, content_width)
