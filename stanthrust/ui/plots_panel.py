"""Engineering plot gallery: one selector list driving one full-size canvas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from stanthrust.theme import PALETTE, SPACE
from stanthrust.ui.formatting import format_number, safe_float
from stanthrust.ui.widgets import EngineeringPlotCard, FlowFieldPlotCard

PLACEHOLDER_SUBTITLE = "Run the coupled solve to generate this plot from the current engine state."
FLOW_FIELD_KEY = "flow_field"


@dataclass(frozen=True)
class PlotDefinition:
    """One entry in the plot selector."""

    key: str
    group: str
    title: str
    subtitle: str


PLOT_DEFINITIONS: Tuple[PlotDefinition, ...] = (
    PlotDefinition(
        "pressure_transient",
        "Burn transient",
        "Pressure history",
        "Chamber, required feed, and supply-side pressure across the burn.",
    ),
    PlotDefinition(
        "performance_transient",
        "Burn transient",
        "Thrust and flow",
        "Solved thrust trace and propellant flow over the burn.",
    ),
    PlotDefinition(
        "feed_margins",
        "Burn transient",
        "Feed margins",
        "Fuel and oxidizer pressure margin through the burn.",
    ),
    PlotDefinition(
        "axial_field",
        "Axial field",
        "Pressure and velocity",
        "Pressure and velocity progression along the engine axis.",
    ),
    PlotDefinition(
        "mach_area",
        "Axial field",
        "Mach and area ratio",
        "Mach number and area ratio from the solved nozzle contour.",
    ),
    PlotDefinition(
        "thermal_density",
        "Axial field",
        "Temperature and density",
        "Static temperature and density along the solved flow path.",
    ),
    PlotDefinition(
        FLOW_FIELD_KEY,
        "Axial field",
        "2D nozzle flow field",
        "Mach-coloured axisymmetric station field inside the calculated contour.",
    ),
    PlotDefinition(
        "wall_thermal",
        "Thermal",
        "Wall and coolant march",
        "Hot wall, cold wall, coolant temperature, and heat flux over the geometry.",
    ),
    PlotDefinition(
        "convergence",
        "Convergence",
        "Chamber pressure iteration",
        "Chamber-pressure iteration trace and relative thrust error.",
    ),
    PlotDefinition(
        "coupled_margins",
        "Convergence",
        "Coupled margins",
        "Feed margin, structural margin, and pressure residual by iteration.",
    ),
)


# --------------------------------------------------------------------------- #
# Series construction
# --------------------------------------------------------------------------- #

def _series(label: str, color: str, points: Sequence[Tuple[object, object]]) -> Dict[str, object]:
    return {"label": label, "color": color, "points": list(points)}


def _column(rows: Sequence[dict], x_key: str, y_key: str) -> List[Tuple[object, object]]:
    return [(row.get(x_key), row.get(y_key)) for row in rows]


def pressure_transient_series(time_history: Sequence[dict]) -> List[Dict[str, object]]:
    """Chamber, required feed, tank, and pump-discharge pressure over the burn."""
    if not time_history:
        return []
    series = [
        _series("Chamber", PALETTE["accent_hover"], _column(time_history, "time_s", "chamber_pressure_kpa")),
        _series("Required", PALETTE["warning"], _column(time_history, "time_s", "required_feed_pressure_kpa")),
        _series("Fuel tank", PALETTE["fuel"], _column(time_history, "time_s", "fuel_tank_pressure_kpa")),
        _series("Ox tank", PALETTE["oxidizer"], _column(time_history, "time_s", "oxidizer_tank_pressure_kpa")),
    ]
    if any(safe_float(row.get("pump_discharge_pressure_kpa"), 0.0) for row in time_history):
        series.append(
            _series(
                "Pump discharge",
                PALETTE["success"],
                _column(time_history, "time_s", "pump_discharge_pressure_kpa"),
            )
        )
    return series


def performance_transient_series(
    time_history: Sequence[dict], predicted_thrust: float
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Thrust scaled by the transient flow history, plus propellant mass flow."""
    if not time_history:
        return [], []
    thrust_points = [
        (row.get("time_s"), predicted_thrust * (safe_float(row.get("flow_scale"), 1.0) or 1.0))
        for row in time_history
    ]
    thrust = [_series("Solved thrust", PALETTE["accent_hover"], thrust_points)]
    mass_flow = [
        _series("Mass flow", PALETTE["fuel"], _column(time_history, "time_s", "propellant_mass_flow_kg_s"))
    ]
    return thrust, mass_flow


def feed_margin_series(time_history: Sequence[dict]) -> List[Dict[str, object]]:
    """Fuel and oxidizer pressure margin over the burn."""
    if not time_history:
        return []
    return [
        _series("Fuel margin", PALETTE["fuel"], _column(time_history, "time_s", "fuel_margin_kpa")),
        _series("Ox margin", PALETTE["oxidizer"], _column(time_history, "time_s", "oxidizer_margin_kpa")),
    ]


def axial_field_series(
    axial_profile: Sequence[dict],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Static pressure and velocity along the axis."""
    if not axial_profile:
        return [], []
    pressure = [_series("Pressure", PALETTE["accent_hover"], _column(axial_profile, "x_mm", "pressure_kpa"))]
    velocity = [_series("Velocity", PALETTE["fuel"], _column(axial_profile, "x_mm", "velocity_m_s"))]
    return pressure, velocity


def mach_area_series(
    axial_profile: Sequence[dict],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Mach number and local area ratio along the axis."""
    if not axial_profile:
        return [], []
    mach = [_series("Mach", PALETTE["accent_hover"], _column(axial_profile, "x_mm", "mach"))]
    area = [_series("Area ratio", PALETTE["warning"], _column(axial_profile, "x_mm", "area_ratio"))]
    return mach, area


def thermal_density_series(
    axial_profile: Sequence[dict],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Static temperature and density along the axis."""
    if not axial_profile:
        return [], []
    temperature = [_series("Temperature", PALETTE["danger"], _column(axial_profile, "x_mm", "temperature_k"))]
    density = [_series("Density", PALETTE["oxidizer"], _column(axial_profile, "x_mm", "density_kg_m3"))]
    return temperature, density


def wall_thermal_series(
    thermal_stations: Sequence[dict],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Wall and coolant temperatures with heat flux in MW/m2."""
    if not thermal_stations:
        return [], []
    temperature = [
        _series("Hot wall", PALETTE["danger"], _column(thermal_stations, "x_mm", "hot_wall_temperature_k")),
        _series("Cold wall", PALETTE["warning"], _column(thermal_stations, "x_mm", "cold_wall_temperature_k")),
        _series("Coolant", PALETTE["cooling"], _column(thermal_stations, "x_mm", "coolant_inlet_temperature_k")),
    ]
    heat_flux = [
        _series(
            "Heat flux",
            PALETTE["oxidizer"],
            [
                (row.get("x_mm"), (safe_float(row.get("heat_flux_w_m2"), 0.0) or 0.0) / 1_000_000.0)
                for row in thermal_stations
            ],
        )
    ]
    return temperature, heat_flux


def convergence_series(
    iteration_trace: Sequence[dict],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Chamber pressure and relative error by solver iteration."""
    if not iteration_trace:
        return [], []
    pressure = [_series("Pc", PALETTE["accent_hover"], _column(iteration_trace, "iteration", "chamber_pressure_kpa"))]
    error = [
        _series(
            "Error",
            PALETTE["warning"],
            [
                (
                    row.get("iteration"),
                    (safe_float(row.get("relative_error", row.get("thrust_error_fraction")), 0.0) or 0.0) * 100.0,
                )
                for row in iteration_trace
            ],
        )
    ]
    return pressure, error


def coupled_margin_series(
    iteration_trace: Sequence[dict],
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Coupled-cycle residual, feed margin, and structural margin by iteration."""
    if not iteration_trace:
        return [], []
    pressure = [
        _series("Residual", PALETTE["warning"], _column(iteration_trace, "iteration", "residual_kpa")),
        _series(
            "Feed margin",
            PALETTE["accent_hover"],
            _column(iteration_trace, "iteration", "minimum_feed_margin_kpa"),
        ),
    ]
    structural = [
        _series(
            "Structural margin",
            PALETTE["success"],
            _column(iteration_trace, "iteration", "minimum_structural_margin_ratio"),
        )
    ]
    return pressure, structural


# --------------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------------- #

class PlotsPanel(QWidget):
    """Plot selector on the left, the selected plot filling the rest."""

    def __init__(self) -> None:
        super().__init__()
        self.cards: Dict[str, EngineeringPlotCard] = {}
        self.flow_field_card: Optional[FlowFieldPlotCard] = None
        self._stack = QStackedWidget()
        self._selector = QListWidget()
        self._selector.setFixedWidth(212)
        self._selector.setUniformItemSizes(False)
        self._page_index: Dict[str, int] = {}
        self._build()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])

        current_group = ""
        for definition in PLOT_DEFINITIONS:
            if definition.group != current_group:
                current_group = definition.group
                header = QListWidgetItem(definition.group.upper())
                header.setFlags(Qt.NoItemFlags)
                self._selector.addItem(header)
            item = QListWidgetItem(definition.title)
            item.setData(Qt.UserRole, definition.key)
            item.setToolTip(definition.subtitle)
            self._selector.addItem(item)

            if definition.key == FLOW_FIELD_KEY:
                card: QWidget = FlowFieldPlotCard(definition.title, definition.subtitle)
                self.flow_field_card = card
            else:
                card = EngineeringPlotCard(definition.title, definition.subtitle)
                self.cards[definition.key] = card
            self._page_index[definition.key] = self._stack.addWidget(card)

        self._selector.currentItemChanged.connect(self._on_selection)

        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(SPACE["sm"])
        caption = QLabel("Views")
        caption.setObjectName("eyebrow")
        side.addWidget(caption)
        side.addWidget(self._selector, 1)
        layout.addLayout(side)
        layout.addWidget(self._stack, 1)
        self.select(PLOT_DEFINITIONS[0].key)

    def _on_selection(self, current: Optional[QListWidgetItem], _previous) -> None:
        if current is None:
            return
        key = current.data(Qt.UserRole)
        if key in self._page_index:
            self._stack.setCurrentIndex(self._page_index[key])

    def select(self, key: str) -> None:
        """Bring one plot to the front."""
        for row in range(self._selector.count()):
            item = self._selector.item(row)
            if item.data(Qt.UserRole) == key:
                self._selector.setCurrentItem(item)
                return

    def set_placeholder(self) -> None:
        """Reset every plot to its pre-solve placeholder state."""
        for definition in PLOT_DEFINITIONS:
            if definition.key == FLOW_FIELD_KEY:
                continue
            self.cards[definition.key].set_plot_data(
                subtitle=PLACEHOLDER_SUBTITLE,
                x_label="",
                primary_label="",
                primary_series=[],
            )
        if self.flow_field_card is not None:
            self.flow_field_card.set_flow_data(
                subtitle=PLACEHOLDER_SUBTITLE,
                axial_profile=[],
            )

    def render(
        self,
        *,
        design,
        combustion_result: dict,
        feed_summary: dict,
        time_history: Sequence[dict],
        axial_profile: Sequence[dict],
        thermal_stations: Sequence[dict],
        iteration_trace: Sequence[dict],
        predicted_thrust: float,
    ) -> None:
        """Populate every plot from one solved state."""
        summary = dict(combustion_result.get("summary", {}))
        metadata = dict(combustion_result.get("metadata", {}))
        flow_model_label = str(
            metadata.get("flow_model_label", summary.get("flow_model_label", "Current solve"))
        )
        architecture = "Pump-fed" if bool(design.inputs.use_pumps) else "Pressure-fed"
        drift_percent = format_number(feed_summary.get("chamber_pressure_drift_percent", "--"), 3)

        performance_primary, performance_secondary = performance_transient_series(
            time_history, predicted_thrust
        )
        axial_primary, axial_secondary = axial_field_series(axial_profile)
        mach_primary, mach_secondary = mach_area_series(axial_profile)
        thermal_primary, thermal_secondary = thermal_density_series(axial_profile)
        wall_primary, wall_secondary = wall_thermal_series(thermal_stations)
        convergence_primary, convergence_secondary = convergence_series(iteration_trace)
        coupled_primary, coupled_secondary = coupled_margin_series(iteration_trace)

        self.cards["pressure_transient"].set_plot_data(
            subtitle="{0} transient pressure history. Chamber-pressure drift over the burn: {1}%.".format(
                architecture, drift_percent
            ),
            x_label="Time (s)",
            primary_label="Pressure (kPa)",
            primary_series=pressure_transient_series(time_history),
            note="Supply curves come from the transient feed solver. Pump discharge appears only for pump-fed cases.",
        )
        self.cards["performance_transient"].set_plot_data(
            subtitle="Thrust curve scaled from the solved operating point using the transient feed state.",
            x_label="Time (s)",
            primary_label="Solved thrust (N)",
            primary_series=performance_primary,
            secondary_label="Mass flow (kg/s)",
            secondary_series=performance_secondary,
            note=(
                "Preliminary trace: it scales converged steady-state thrust by the transient flow-scale "
                "history rather than time-marching the combustion solve."
            ),
        )
        self.cards["feed_margins"].set_plot_data(
            subtitle="Pressure margin available to each propellant path across the burn.",
            x_label="Time (s)",
            primary_label="Margin (kPa)",
            primary_series=feed_margin_series(time_history),
            note="Positive margin means the feed path still supports the requested chamber and injector pressure.",
        )
        self.cards["axial_field"].set_plot_data(
            subtitle="{0} axial station field from the chamber and nozzle solve.".format(flow_model_label),
            x_label="Axial position (mm)",
            primary_label="Pressure (kPa)",
            primary_series=axial_primary,
            secondary_label="Velocity (m/s)",
            secondary_series=axial_secondary,
            note="These are the same stations reported in the data table and the log.",
        )
        self.cards["mach_area"].set_plot_data(
            subtitle="Mach solution and local area ratio along the solved chamber, throat, and nozzle contour.",
            x_label="Axial position (mm)",
            primary_label="Mach",
            primary_series=mach_primary,
            secondary_label="Area ratio",
            secondary_series=mach_secondary,
            note="Use this to confirm the flow chokes at the throat and expands through the bell section.",
        )
        self.cards["thermal_density"].set_plot_data(
            subtitle="Static temperature and density derived from the same area-Mach profile.",
            x_label="Axial position (mm)",
            primary_label="Temperature (K)",
            primary_series=thermal_primary,
            secondary_label="Density (kg/m3)",
            secondary_series=thermal_secondary,
            note="Calculated from the local area-Mach station state and Cantera thermochemistry.",
        )
        self.cards["wall_thermal"].set_plot_data(
            subtitle="Stationwise conjugate wall solution with counterflow coolant marching from exit to injector.",
            x_label="Engine axial position (mm)",
            primary_label="Temperature (K)",
            primary_series=wall_primary,
            secondary_label="Heat flux (MW/m2)",
            secondary_series=wall_secondary,
            note="Every point uses the local solved radius, wall thickness, channel pitch, and coolant state.",
        )
        self.cards["convergence"].set_plot_data(
            subtitle="Chamber-pressure iteration trace and relative thrust error.",
            x_label="Iteration",
            primary_label="Chamber pressure (kPa)",
            primary_series=convergence_primary,
            secondary_label="Relative error (%)",
            secondary_series=convergence_secondary,
            note="Shows whether the solve converged cleanly or stopped at the iteration limit.",
        )
        self.cards["coupled_margins"].set_plot_data(
            subtitle="Coupled-cycle residual, feed margin, and structural margin by iteration.",
            x_label="Iteration",
            primary_label="Pressure (kPa)",
            primary_series=coupled_primary,
            secondary_label="Structural margin (x)",
            secondary_series=coupled_secondary,
            note="Residual should trend downward while feed and structural margins stay acceptable.",
        )
        if self.flow_field_card is not None:
            self.flow_field_card.set_flow_data(
                subtitle="Mach-coloured axisymmetric station field from the calculated wall radius and flow solution.",
                axial_profile=list(axial_profile),
                variable="mach",
                variable_label="Mach field",
                note=(
                    "Colours use the exact calculated station values with linear interpolation between "
                    "stations. The throat and the station nearest M = 1 are marked explicitly."
                ),
            )
