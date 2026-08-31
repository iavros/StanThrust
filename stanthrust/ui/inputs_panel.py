"""The design input panel: field definitions, layout, and state conversion.

Fields are declared as data so that the panel layout, the reset defaults, the
project-state round trip, and the unit conversions all read from one table.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Dict, List, Optional, Sequence, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from stanthrust.design_model import INJECTOR_TYPES
from stanthrust.inputs import (
    DEFAULT_OBJECTIVE_WEIGHTS,
    DEFAULT_STATE,
    FUEL_NAMES,
    MATERIAL_OPTIONS,
    OXIDIZER_NAMES,
)
from stanthrust.theme import SPACE
from stanthrust.ui.formatting import (
    FLOW_MODEL_DISPLAY_NAMES,
    INJECTOR_DISPLAY_NAMES,
    NOZZLE_EXPANSION_DISPLAY_NAMES,
    PRESSURE_MODE_DISPLAY_NAMES,
    display_option_name,
)
from stanthrust.ui.widgets import (
    Card,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    divider,
)

#: Axial station counts. The interactive default stays lower than the final
#: pass so that editing inputs remains responsive.
DEFAULT_SOLVER_STATION_COUNT = 160
FINAL_SOLVER_STATION_COUNT = 180

DEFAULT_CONVERGENCE_TOLERANCE = 0.005
DEFAULT_ITERATION_LIMIT = 50


# --------------------------------------------------------------------------- #
# Field specifications
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Combo:
    """A drop-down field over a fixed set of option values."""

    values: Sequence[str]
    display: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class Spin:
    """A numeric field with an explicit range, step, and unit suffix."""

    minimum: float
    maximum: float
    step: float
    decimals: int
    suffix: str = ""


@dataclass(frozen=True)
class Check:
    """A single check box field."""

    text: str


@dataclass(frozen=True)
class Field:
    """One input control, its label, and how it maps into solver state."""

    key: str
    label: str
    control: object
    helper: str = ""
    #: Solver-state key, when it differs from the widget key.
    state_key: Optional[str] = None
    #: Multiplier applied when writing the widget value into solver state.
    to_state: float = 1.0
    #: Set for fields that are read at solve time rather than sent as state.
    local_only: bool = False
    #: Reset value for keys that ``DefaultState`` does not carry.
    default: Optional[object] = None

    @property
    def target(self) -> str:
        return self.state_key or self.key


@dataclass(frozen=True)
class Row:
    """One or two fields laid out side by side."""

    fields: Tuple[Field, ...]

    def __init__(self, *fields: Field) -> None:
        object.__setattr__(self, "fields", tuple(fields))


@dataclass(frozen=True)
class Group:
    """A titled block of rows inside a page."""

    title: str
    rows: Tuple[Row, ...]
    subtitle: str = ""


@dataclass(frozen=True)
class Page:
    """One entry in the input panel's category rail."""

    key: str
    title: str
    subtitle: str
    groups: Tuple[Group, ...]
    #: Advanced pages are hidden unless the full workspace is selected.
    advanced: bool = False
    extras: Tuple[str, ...] = dataclass_field(default_factory=tuple)


_MM = " mm"
_KPA = " kPa"

MISSION_PAGE = Page(
    key="mission",
    title="Mission",
    subtitle="Propellants, thrust target, and burn duration.",
    groups=(
        Group(
            "Propellants",
            (
                Row(
                    Field("fuel_name", "Fuel", Combo(FUEL_NAMES), "Primary fuel selection."),
                    Field("oxidizer_name", "Oxidizer", Combo(OXIDIZER_NAMES), "Primary oxidizer selection."),
                ),
                Row(
                    Field(
                        "injector_type",
                        "Injector family",
                        Combo(INJECTOR_TYPES, INJECTOR_DISPLAY_NAMES),
                        "Injector pattern used for the engine.",
                    ),
                    Field(
                        "mixture_ratio",
                        "Mixture ratio (O/F)",
                        Spin(0.1, 20.0, 0.1, 3),
                        "Oxidizer to fuel mass ratio.",
                    ),
                ),
            ),
        ),
        Group(
            "Performance targets",
            (
                Row(
                    Field(
                        "target_thrust_newtons",
                        "Thrust",
                        Spin(0.0, 100000.0, 1.0, 1, " N"),
                        "Thrust the engine should produce.",
                    ),
                    Field(
                        "target_chamber_pressure_kpa",
                        "Chamber pressure",
                        Spin(110.0, 50000.0, 10.0, 1, _KPA),
                        "Design chamber pressure. Predicted instead in hardware analysis mode.",
                    ),
                ),
                Row(
                    Field(
                        "target_impulse_newton_seconds",
                        "Total impulse",
                        Spin(0.0, 1000000.0, 10.0, 1, " N*s"),
                        "Total impulse delivered over the full burn.",
                    ),
                    Field(
                        "burn_time_seconds",
                        "Burn time",
                        Spin(0.1, 1000.0, 0.5, 2, " s"),
                        "Planned burn duration.",
                    ),
                ),
            ),
        ),
    ),
)

ENVELOPE_PAGE = Page(
    key="envelope",
    title="Envelope",
    subtitle="Packaging limits and nozzle exit sizing.",
    groups=(
        Group(
            "Diameter limits",
            (
                Row(
                    Field(
                        "target_diameter_mm",
                        "Overall diameter",
                        Spin(10.0, 5000.0, 1.0, 1, _MM),
                        "Maximum allowed outside diameter.",
                    ),
                    Field(
                        "packaging_bias",
                        "Packaging bias",
                        Combo(("balanced", "compact", "serviceable")),
                        "How aggressively to prioritise compact packaging.",
                    ),
                ),
                Row(
                    Field(
                        "tank_diameter_mm",
                        "Tank diameter",
                        Spin(10.0, 5000.0, 1.0, 1, _MM),
                        "Maximum tank diameter constraint.",
                    ),
                    Field(
                        "chamber_diameter_mm",
                        "Chamber diameter",
                        Spin(10.0, 5000.0, 1.0, 1, _MM),
                        "Maximum combustion chamber diameter.",
                    ),
                ),
            ),
        ),
        Group(
            "Nozzle exit",
            (
                Row(
                    Field(
                        "nozzle_exit_auto",
                        "",
                        Check("Size the nozzle exit automatically"),
                        "Sizes the exit from the pressure-compatible MOC expansion target and separation margin.",
                        state_key="nozzle_exit_mode",
                        local_only=True,
                    ),
                ),
                Row(
                    Field(
                        "nozzle_expansion_bias",
                        "Expansion target",
                        Combo(
                            ("pressure_matched", "underexpanded", "overexpanded"),
                            NOZZLE_EXPANSION_DISPLAY_NAMES,
                        ),
                        "Automatic exit pressure target: pressure matched, underexpanded, or overexpanded.",
                    ),
                    Field(
                        "nozzle_diameter_mm",
                        "Manual exit diameter",
                        Spin(10.0, 5000.0, 1.0, 1, _MM),
                        "Only used when automatic exit sizing is switched off.",
                    ),
                ),
            ),
        ),
    ),
)

ARCHITECTURE_PAGE = Page(
    key="architecture",
    title="Architecture",
    subtitle="Feed system and chamber cooling.",
    groups=(
        Group(
            "Cooling",
            (
                Row(
                    Field(
                        "regen_cooling",
                        "",
                        Check("Regenerative cooling"),
                        "Recirculate propellant through cooling passages in the chamber wall.",
                    ),
                ),
                Row(
                    Field(
                        "film_cooling",
                        "",
                        Check("Film cooling"),
                        "Maintain a thin protective propellant film at the chamber wall.",
                    ),
                ),
            ),
        ),
    ),
    extras=("feed_mode",),
)

MATERIALS_PAGE = Page(
    key="materials",
    title="Materials",
    subtitle="Section materials and structural margin.",
    advanced=True,
    groups=(
        Group(
            "Tanks and feed",
            (
                Row(
                    Field("fuel_tank_material", "Fuel tank", Combo(MATERIAL_OPTIONS), "Fuel tank wall material."),
                    Field("oxidizer_tank_material", "Oxidizer tank", Combo(MATERIAL_OPTIONS), "Oxidizer tank wall material."),
                ),
                Row(
                    Field(
                        "feed_system_material",
                        "Feed system",
                        Combo(MATERIAL_OPTIONS),
                        "Material used for feed hardware evaluation.",
                    ),
                    Field(
                        "factor_of_safety",
                        "Safety factor",
                        Spin(1.0, 10.0, 0.1, 2),
                        "Structural margin applied to every section.",
                    ),
                ),
            ),
        ),
        Group(
            "Hot section",
            (
                Row(
                    Field("chamber_material", "Chamber", Combo(MATERIAL_OPTIONS), "Chamber material."),
                    Field("nozzle_material", "Nozzle", Combo(MATERIAL_OPTIONS), "Nozzle material."),
                ),
            ),
        ),
    ),
)

OBJECTIVES_PAGE = Page(
    key="objectives",
    title="Objectives",
    subtitle="Optimiser weights and search options.",
    advanced=True,
    groups=(
        Group(
            "Objective weights",
            (
                Row(
                    Field("objective_thrust", "Thrust", Spin(0.0, 1.0, 0.05, 3), "Higher values favour delivered thrust."),
                    Field("objective_mass", "Mass", Spin(0.0, 1.0, 0.05, 3), "Higher values favour lighter engines."),
                ),
                Row(
                    Field("objective_packaging", "Packaging", Spin(0.0, 1.0, 0.05, 3), "Higher values favour tighter packaging."),
                    Field("objective_thermal", "Thermal", Spin(0.0, 1.0, 0.05, 3), "Higher values favour thermal margin."),
                ),
            ),
            subtitle="Weights are normalised before scoring.",
        ),
        Group(
            "Search",
            (
                Row(
                    Field(
                        "ga_enabled",
                        "",
                        Check("Optimise on solve"),
                        "Run the genetic optimiser as part of every solve.",
                        default=True,
                    ),
                ),
                Row(
                    Field(
                        "feasibility_first",
                        "",
                        Check("Feasibility-first search"),
                        "Search for a feasible design before optimising the objective score.",
                        local_only=True,
                        default=True,
                    ),
                ),
                Row(
                    Field(
                        "use_multi_fidelity",
                        "",
                        Check("Solver fidelity routing"),
                        "Screen candidates at low fidelity and confirm the survivors at full fidelity.",
                        local_only=True,
                    ),
                ),
                Row(
                    Field(
                        "show_uncertainty",
                        "",
                        Check("Report uncertainty bounds"),
                        "Add the solved P05/P50/P95 bounds to the report and the data table.",
                        local_only=True,
                    ),
                ),
            ),
        ),
    ),
)

SOLVER_PAGE = Page(
    key="solver",
    title="Solver",
    subtitle="Flow model, resolution, and convergence.",
    advanced=True,
    groups=(
        Group(
            "Flow solve",
            (
                Row(
                    Field(
                        "solver_flow_model",
                        "Flow model",
                        Combo(("viscous", "refined", "fast"), FLOW_MODEL_DISPLAY_NAMES),
                        "Viscous quasi-1D uses the Cantera state, MOC contour, shock feedback, and station corrections.",
                        default="viscous",
                    ),
                    Field(
                        "solver_station_count",
                        "Axial stations",
                        Spin(6, 240, 1.0, 0),
                        "Axial station count used by the interactive flow solve.",
                        default=DEFAULT_SOLVER_STATION_COUNT,
                    ),
                ),
                Row(
                    Field(
                        "solver_convergence_tolerance",
                        "Convergence tolerance",
                        Spin(0.0001, 0.1, 0.001, 4),
                        "Lower values require tighter convergence.",
                        default=DEFAULT_CONVERGENCE_TOLERANCE,
                    ),
                    Field(
                        "solver_iteration_limit",
                        "Iteration limit",
                        Spin(1, 500, 1.0, 0),
                        "Maximum solver iterations before stopping.",
                        default=DEFAULT_ITERATION_LIMIT,
                    ),
                ),
            ),
            subtitle=(
                "The final saved solve always escalates to the viscous path with at least "
                "{0} stations.".format(FINAL_SOLVER_STATION_COUNT)
            ),
        ),
    ),
)

HYDRAULICS_PAGE = Page(
    key="hydraulics",
    title="Hydraulics",
    subtitle="Injector, line loss, and pressure closure.",
    advanced=True,
    groups=(
        Group(
            "Pressure closure",
            (
                Row(
                    Field(
                        "pressure_solve_mode",
                        "Closure mode",
                        Combo(("design", "analysis"), PRESSURE_MODE_DISPLAY_NAMES),
                        "Design sizing solves injector area and supply pressure. Hardware analysis predicts chamber pressure from measured hardware.",
                    ),
                    Field(
                        "combustion_efficiency",
                        "Combustion efficiency",
                        Spin(0.50, 1.00, 0.005, 3),
                        "Delivered c-star efficiency applied to the Cantera ideal state.",
                    ),
                ),
                Row(
                    Field(
                        "design_injector_dp_ratio",
                        "Injector drop / Pc",
                        Spin(0.05, 0.50, 0.01, 3),
                        "Injector pressure-drop ratio used to size design-mode flow area.",
                    ),
                    Field(
                        "design_supply_margin_ratio",
                        "Supply margin",
                        Spin(0.0, 0.50, 0.01, 3),
                        "Pressure margin applied to the sized supply boundary.",
                    ),
                ),
                Row(
                    Field(
                        "uncertainty_sample_count",
                        "Uncertainty samples",
                        Spin(24, 1024, 8, 0),
                        "Latin-hypercube sample count for the final P05/P50/P95 intervals.",
                    ),
                ),
            ),
        ),
        Group(
            "Injector hardware",
            (
                Row(
                    Field(
                        "fuel_injector_discharge_coefficient",
                        "Fuel Cd",
                        Spin(0.20, 1.00, 0.01, 3),
                        "Aggregate fuel injector discharge coefficient.",
                    ),
                    Field(
                        "oxidizer_injector_discharge_coefficient",
                        "Oxidizer Cd",
                        Spin(0.20, 1.00, 0.01, 3),
                        "Aggregate oxidizer injector discharge coefficient.",
                    ),
                ),
                Row(
                    Field(
                        "fuel_injector_area_mm2",
                        "Fuel flow area",
                        Spin(0.0, 10000.0, 0.1, 4, " mm2"),
                        "Measured aggregate fuel flow area. Hardware analysis only.",
                    ),
                    Field(
                        "oxidizer_injector_area_mm2",
                        "Oxidizer flow area",
                        Spin(0.0, 10000.0, 0.1, 4, " mm2"),
                        "Measured aggregate oxidizer flow area. Hardware analysis only.",
                    ),
                ),
                Row(
                    Field(
                        "analysis_throat_diameter_mm",
                        "Measured throat diameter",
                        Spin(0.0, 1000.0, 0.1, 3, _MM),
                        "Measured throat diameter. Required in hardware analysis mode.",
                    ),
                ),
            ),
        ),
        Group(
            "Supply boundaries",
            (
                Row(
                    Field(
                        "fuel_supply_pressure_kpa",
                        "Fuel line inlet",
                        Spin(0.0, 100000.0, 10.0, 2, _KPA),
                        "Measured pressure at the fuel line inlet. Hardware analysis only.",
                    ),
                    Field(
                        "oxidizer_supply_pressure_kpa",
                        "Oxidizer line inlet",
                        Spin(0.0, 100000.0, 10.0, 2, _KPA),
                        "Measured pressure at the oxidizer line inlet. Hardware analysis only.",
                    ),
                ),
                Row(
                    Field(
                        "fuel_tank_inlet_pressure_kpa",
                        "Fuel tank / pump inlet",
                        Spin(101.325, 100000.0, 10.0, 2, _KPA),
                        "Tank-side pressure used to report required pump head.",
                    ),
                    Field(
                        "oxidizer_tank_inlet_pressure_kpa",
                        "Oxidizer tank / pump inlet",
                        Spin(101.325, 100000.0, 10.0, 2, _KPA),
                        "Tank-side pressure used to report required pump head.",
                    ),
                ),
            ),
        ),
        Group(
            "Feed lines",
            (
                Row(
                    Field(
                        "line_diameter_fuel_mm",
                        "Fuel line bore",
                        Spin(1.0, 250.0, 0.1, 3, _MM),
                        "Fuel line inside diameter.",
                        state_key="line_diameter_fuel_m",
                        to_state=1e-3,
                    ),
                    Field(
                        "line_diameter_oxidizer_mm",
                        "Oxidizer line bore",
                        Spin(1.0, 250.0, 0.1, 3, _MM),
                        "Oxidizer line inside diameter.",
                        state_key="line_diameter_oxidizer_m",
                        to_state=1e-3,
                    ),
                ),
                Row(
                    Field(
                        "line_length_fuel_m",
                        "Fuel line length",
                        Spin(0.01, 100.0, 0.05, 3, " m"),
                        "Hydraulic fuel line length.",
                    ),
                    Field(
                        "line_length_oxidizer_m",
                        "Oxidizer line length",
                        Spin(0.01, 100.0, 0.05, 3, " m"),
                        "Hydraulic oxidizer line length.",
                    ),
                ),
                Row(
                    Field(
                        "minor_loss_fuel_k",
                        "Fuel minor-loss sum K",
                        Spin(0.0, 1000.0, 0.5, 3),
                        "Sum of fuel valve, bend, fitting, and contraction loss coefficients.",
                    ),
                    Field(
                        "minor_loss_oxidizer_k",
                        "Oxidizer minor-loss sum K",
                        Spin(0.0, 1000.0, 0.5, 3),
                        "Sum of oxidizer valve, bend, fitting, and contraction loss coefficients.",
                    ),
                ),
                Row(
                    Field(
                        "line_roughness_fuel_um",
                        "Fuel line roughness",
                        Spin(0.0, 1000.0, 0.1, 3, " um"),
                        "Fuel line absolute roughness.",
                        state_key="line_roughness_fuel_m",
                        to_state=1e-6,
                    ),
                    Field(
                        "line_roughness_oxidizer_um",
                        "Oxidizer line roughness",
                        Spin(0.0, 1000.0, 0.1, 3, " um"),
                        "Oxidizer line absolute roughness.",
                        state_key="line_roughness_oxidizer_m",
                        to_state=1e-6,
                    ),
                ),
            ),
        ),
    ),
)

PAGES: Tuple[Page, ...] = (
    MISSION_PAGE,
    ENVELOPE_PAGE,
    ARCHITECTURE_PAGE,
    MATERIALS_PAGE,
    OBJECTIVES_PAGE,
    SOLVER_PAGE,
    HYDRAULICS_PAGE,
)

OBJECTIVE_KEYS = {
    "objective_thrust": "thrust",
    "objective_mass": "mass",
    "objective_packaging": "packaging",
    "objective_thermal": "thermal",
}

#: Fields enabled only in hardware-analysis closure mode.
ANALYSIS_ONLY_FIELDS = (
    "fuel_injector_area_mm2",
    "oxidizer_injector_area_mm2",
    "fuel_supply_pressure_kpa",
    "oxidizer_supply_pressure_kpa",
    "analysis_throat_diameter_mm",
)

#: Fields enabled only in design-sizing closure mode.
DESIGN_ONLY_FIELDS = (
    "target_chamber_pressure_kpa",
    "design_injector_dp_ratio",
    "design_supply_margin_ratio",
)


def iter_fields() -> List[Field]:
    """Return every declared field in page order."""
    fields: List[Field] = []
    for page in PAGES:
        for group in page.groups:
            for row in group.rows:
                fields.extend(row.fields)
    return fields


# --------------------------------------------------------------------------- #
# Panel
# --------------------------------------------------------------------------- #

class InputsPanel(QWidget):
    """Category rail plus stacked input pages, with state conversion helpers."""

    #: Emitted whenever any control changes and a preview refresh is wanted.
    changed = pyqtSignal()
    #: Emitted with a field's helper text when the pointer or focus enters it.
    hint = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.widgets: Dict[str, QWidget] = {}
        self._fields: Dict[str, Field] = {}
        self._pages: Dict[str, QWidget] = {}
        self._nav_buttons: Dict[str, object] = {}
        self._suspended = False
        self._full_workspace = False
        self._build()

    # -- construction ------------------------------------------------------ #

    #: Category buttons per row in the navigation rail.
    _RAIL_COLUMNS = 2

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])

        rail = QWidget()
        rail_layout = QGridLayout(rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setHorizontalSpacing(SPACE["xs"])
        rail_layout.setVerticalSpacing(SPACE["xs"])
        for column in range(self._RAIL_COLUMNS):
            rail_layout.setColumnStretch(column, 1)
        self._stack = QStackedWidget()

        for index, page in enumerate(PAGES):
            button = QPushButton(page.title)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setMinimumHeight(30)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setToolTip(page.subtitle)
            button.clicked.connect(lambda _checked=False, key=page.key: self.show_page(key))
            rail_layout.addWidget(button, index // self._RAIL_COLUMNS, index % self._RAIL_COLUMNS)
            self._nav_buttons[page.key] = button

            widget = self._build_page(page)
            self._pages[page.key] = widget
            self._stack.addWidget(widget)

        layout.addWidget(rail)
        layout.addWidget(divider())
        layout.addWidget(self._stack, 1)
        self.show_page(PAGES[0].key)

    def _build_page(self, page: Page) -> QWidget:
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(SPACE["md"])

        heading = QLabel(page.subtitle)
        heading.setObjectName("sectionBody")
        heading.setWordWrap(True)
        content_layout.addWidget(heading)

        if "feed_mode" in page.extras:
            content_layout.addWidget(self._build_feed_mode_card())

        for group in page.groups:
            content_layout.addWidget(self._build_group(group))
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        scroll.verticalScrollBar().setSingleStep(24)
        return scroll

    def _build_group(self, group: Group) -> Card:
        card = Card(group.title, group.subtitle)
        grid = QGridLayout()
        grid.setContentsMargins(0, SPACE["xs"], 0, 0)
        grid.setHorizontalSpacing(SPACE["md"])
        grid.setVerticalSpacing(SPACE["md"])
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for row_index, row in enumerate(group.rows):
            span = 2 if len(row.fields) == 1 else 1
            for column, spec in enumerate(row.fields):
                grid.addWidget(self._build_field(spec), row_index, column, 1, span)
        card.body().addLayout(grid)
        return card

    def _build_field(self, spec: Field) -> QWidget:
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        if spec.label:
            label = QLabel(spec.label)
            label.setObjectName("fieldLabel")
            label.setWordWrap(True)
            column.addWidget(label)

        widget = self._make_control(spec)
        widget.setToolTip(spec.helper)
        widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        widget.installEventFilter(self)
        column.addWidget(widget)

        self.widgets[spec.key] = widget
        self._fields[spec.key] = spec
        return container

    def _make_control(self, spec: Field) -> QWidget:
        control = spec.control
        if isinstance(control, Combo):
            combo = NoWheelComboBox()
            for value in control.values:
                text_value = str(value)
                if control.display is None:
                    combo.addItem(display_option_name(text_value), text_value)
                else:
                    combo.addItem(control.display.get(text_value, text_value), text_value)
            combo.setMaxVisibleItems(14)
            combo.currentIndexChanged.connect(self._on_change)
            return combo
        if isinstance(control, Spin):
            spin = NoWheelDoubleSpinBox()
            spin.setRange(control.minimum, control.maximum)
            spin.setSingleStep(control.step)
            spin.setDecimals(control.decimals)
            spin.setSuffix(control.suffix)
            spin.setKeyboardTracking(False)
            spin.setAccelerated(True)
            spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            spin.valueChanged.connect(self._on_change)
            return spin
        if isinstance(control, Check):
            check = QCheckBox(control.text)
            check.toggled.connect(self._on_change)
            return check
        raise TypeError("unsupported control specification: {0!r}".format(control))

    def _build_feed_mode_card(self) -> Card:
        card = Card("Feed system", "Choose how propellant reaches the injector.")
        row = QHBoxLayout()
        row.setContentsMargins(0, SPACE["xs"], 0, 0)
        row.setSpacing(SPACE["lg"])
        self._feed_group = QButtonGroup(self)
        for key, text, helper in (
            ("feed_mode_pump", "Pump-fed", "Electric-motor driven pumps raise propellant to injector pressure."),
            ("feed_mode_blowdown", "Blowdown", "Pressurised tanks supply the injector with no pump."),
        ):
            button = QRadioButton(text)
            button.setToolTip(helper)
            button.installEventFilter(self)
            button.toggled.connect(self._on_change)
            self._feed_group.addButton(button)
            self.widgets[key] = button
            self._fields[key] = Field(key, text, Check(text), helper, local_only=True)
            row.addWidget(button)
        row.addStretch(1)
        card.body().addLayout(row)
        return card

    # -- interaction ------------------------------------------------------- #

    def eventFilter(self, watched, event):  # type: ignore[override]
        from PyQt5.QtCore import QEvent

        if event.type() in (QEvent.Enter, QEvent.FocusIn):
            for key, widget in self.widgets.items():
                if widget is watched:
                    spec = self._fields.get(key)
                    if spec is not None and spec.helper:
                        self.hint.emit(spec.helper)
                    break
        elif event.type() in (QEvent.Leave, QEvent.FocusOut):
            self.hint.emit("")
        return super().eventFilter(watched, event)

    def _on_change(self, *_args) -> None:
        if self._suspended:
            return
        self.changed.emit()

    def show_page(self, key: str) -> None:
        """Bring one input category to the front."""
        page = self._pages.get(key)
        if page is None:
            return
        self._stack.setCurrentWidget(page)
        for button_key, button in self._nav_buttons.items():
            button.setChecked(button_key == key)

    def page_keys(self, include_advanced: bool = True) -> List[str]:
        """Return page keys, optionally limited to the always-visible ones."""
        return [page.key for page in PAGES if include_advanced or not page.advanced]

    def set_full_workspace(self, enabled: bool) -> None:
        """Show or hide the advanced input categories."""
        self._full_workspace = bool(enabled)
        for page in PAGES:
            visible = self._full_workspace or not page.advanced
            self._nav_buttons[page.key].setVisible(visible)
        current = self._stack.currentWidget()
        for page in PAGES:
            if self._pages[page.key] is current and page.advanced and not self._full_workspace:
                self.show_page(PAGES[0].key)
                return

    # -- interlocks -------------------------------------------------------- #

    def apply_interlocks(self) -> None:
        """Enable or disable fields that only apply in a particular mode."""
        auto_exit = bool(self.widgets["nozzle_exit_auto"].isChecked())
        self.widgets["nozzle_diameter_mm"].setEnabled(not auto_exit)
        self.widgets["nozzle_expansion_bias"].setEnabled(auto_exit)

        analysis = self.value("pressure_solve_mode") == "analysis"
        for key in ANALYSIS_ONLY_FIELDS:
            self.widgets[key].setEnabled(analysis)
        for key in DESIGN_ONLY_FIELDS:
            self.widgets[key].setEnabled(not analysis)

    # -- state ------------------------------------------------------------- #

    def value(self, key: str) -> object:
        """Return one widget's value in solver-state form."""
        widget = self.widgets[key]
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            return widget.currentText() if data is None else str(data)
        if isinstance(widget, QDoubleSpinBox):
            return float(widget.value())
        if isinstance(widget, (QCheckBox, QRadioButton)):
            return bool(widget.isChecked())
        raise TypeError("unsupported widget for key {0!r}".format(key))

    def is_checked(self, key: str) -> bool:
        """Return a check box or radio button state."""
        return bool(self.widgets[key].isChecked())

    def collect_state(self) -> Dict[str, object]:
        """Build the solver input state from the current control values."""
        state: Dict[str, object] = {}
        for spec in iter_fields():
            if spec.key in OBJECTIVE_KEYS or spec.local_only:
                continue
            value = self.value(spec.key)
            if isinstance(value, float) and spec.to_state != 1.0:
                value = value * spec.to_state
            state[spec.target] = value
        state["uncertainty_sample_count"] = int(state["uncertainty_sample_count"])
        state["nozzle_exit_mode"] = "auto" if self.is_checked("nozzle_exit_auto") else "manual"
        state["use_pumps"] = self.is_checked("feed_mode_pump")
        return state

    def collect_objective_weights(self) -> Dict[str, float]:
        """Return the raw objective weights keyed by objective name."""
        return {name: float(self.value(key)) for key, name in OBJECTIVE_KEYS.items()}

    def apply_state(self, state: Dict[str, object], objective_weights: Dict[str, float]) -> None:
        """Load a saved project state into the controls."""
        with self.suspended():
            for spec in iter_fields():
                if spec.key in OBJECTIVE_KEYS:
                    continue
                self._apply_field(spec, state)
            for key, name in OBJECTIVE_KEYS.items():
                default = DEFAULT_OBJECTIVE_WEIGHTS[name]
                self.widgets[key].setValue(float(objective_weights.get(name, default)))

            use_pumps = bool(state.get("use_pumps", DEFAULT_STATE.use_pumps))
            self.widgets["feed_mode_pump"].setChecked(use_pumps)
            self.widgets["feed_mode_blowdown"].setChecked(not use_pumps)

            raw_mode = state.get("nozzle_exit_mode")
            if raw_mode is None:
                mode = "manual" if "nozzle_diameter_mm" in state else DEFAULT_STATE.nozzle_exit_mode
            else:
                mode = str(raw_mode).strip().lower()
            self.widgets["nozzle_exit_auto"].setChecked(mode != "manual")
            self.widgets["ga_enabled"].setChecked(bool(state.get("ga_enabled", True)))
            self.apply_interlocks()

    @staticmethod
    def _default_for(spec: Field) -> Optional[object]:
        """Return the reset value for a field."""
        if spec.default is not None:
            return spec.default
        return getattr(DEFAULT_STATE, spec.target, None)

    def _apply_field(self, spec: Field, state: Dict[str, object]) -> None:
        widget = self.widgets[spec.key]
        fallback = self._default_for(spec)
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(state.get(spec.target, bool(fallback))))
            return
        raw = state.get(spec.target, fallback)
        if raw is None:
            return
        if isinstance(widget, QComboBox):
            self._set_combo(widget, str(raw))
            return
        value = float(raw)
        if spec.to_state != 1.0:
            value = value / spec.to_state
        widget.setValue(value)

    def reset(self) -> None:
        """Return every control to its documented default."""
        with self.suspended():
            for spec in iter_fields():
                widget = self.widgets[spec.key]
                default = self._default_for(spec)
                if isinstance(widget, QComboBox):
                    if default is None:
                        widget.setCurrentIndex(0)
                    else:
                        self._set_combo(widget, str(default))
                elif isinstance(widget, QDoubleSpinBox):
                    if default is None:
                        continue
                    value = float(default)
                    if spec.to_state != 1.0:
                        value = value / spec.to_state
                    widget.setValue(value)
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(bool(default))

            for key, name in OBJECTIVE_KEYS.items():
                self.widgets[key].setValue(DEFAULT_OBJECTIVE_WEIGHTS[name])

            self.widgets["nozzle_exit_auto"].setChecked(DEFAULT_STATE.nozzle_exit_mode == "auto")
            self.widgets["feed_mode_pump"].setChecked(DEFAULT_STATE.use_pumps)
            self.widgets["feed_mode_blowdown"].setChecked(not DEFAULT_STATE.use_pumps)
            self.apply_interlocks()

    def solver_resolution(self) -> Dict[str, object]:
        """Return the bounded interactive solver resolution settings."""
        flow_model = str(self.value("solver_flow_model")).strip().lower()
        if flow_model not in {"fast", "refined", "viscous"}:
            flow_model = "viscous"
        return {
            "flow_model": flow_model,
            "station_count": max(6, min(240, int(self.value("solver_station_count")))),
            "convergence_tolerance": max(0.0001, min(0.1, float(self.value("solver_convergence_tolerance")))),
            "iteration_limit": max(3, min(500, int(self.value("solver_iteration_limit")))),
        }

    # -- helpers ----------------------------------------------------------- #

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)
            return
        combo.setCurrentText(value)

    def suspended(self) -> _SuspendChanges:
        """Return a context manager that mutes the ``changed`` signal."""
        return _SuspendChanges(self)


class _SuspendChanges:
    """Context manager that suppresses change notifications while loading."""

    def __init__(self, panel: InputsPanel) -> None:
        self._panel = panel
        self._previous = False

    def __enter__(self) -> InputsPanel:
        self._previous = self._panel._suspended
        self._panel._suspended = True
        return self._panel

    def __exit__(self, *_exc) -> None:
        self._panel._suspended = self._previous
