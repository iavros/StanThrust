"""Solved-value data table with category filtering and text search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Sequence, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stanthrust.theme import SPACE, mono_font_families
from stanthrust.ui.formatting import display_solver_stage, format_number

ALL_CATEGORY = "All"

#: Filter categories, in display order.
CATEGORIES: Tuple[str, ...] = (
    ALL_CATEGORY,
    "Tanks",
    "Feed and pumps",
    "Chamber",
    "Injector",
    "Nozzle",
    "Cooling",
    "Flow",
    "Uncertainty",
    "Overall",
)

_KEYWORD_CATEGORIES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("tank",), "Tanks"),
    (("impeller", "motor", "feed", "pressurization bay"), "Feed and pumps"),
    (("chamber",), "Chamber"),
    (("throat",), "Nozzle"),
    (("injector", "pintle", "impinging"), "Injector"),
    (("nozzle",), "Nozzle"),
    (("regen", "film", "cooling"), "Cooling"),
)


@dataclass(frozen=True)
class MeasurementRow:
    """One label/value pair together with the categories it belongs to."""

    label: str
    value: str
    categories: FrozenSet[str]


def category_for_label(label: str) -> str:
    """Classify a measurement label into one filter category."""
    text = label.lower()
    for keywords, category in _KEYWORD_CATEGORIES:
        if any(keyword in text for keyword in keywords):
            return category
    return "Overall"


def _row(label: str, value: object, *extra_categories: str) -> MeasurementRow:
    categories = {category_for_label(label)}
    categories.update(extra_categories)
    return MeasurementRow(label=label, value=str(value), categories=frozenset(categories))


def _flow_rows(combustion_result: dict) -> List[MeasurementRow]:
    summary = dict(combustion_result.get("summary", {}))
    heat_transfer = dict(combustion_result.get("heat_transfer", {}))
    heat_summary = dict(heat_transfer.get("summary", {}))
    metadata = dict(combustion_result.get("metadata", {}))
    thermo = dict(metadata.get("thermochemistry", {}))
    stage_label = str(
        metadata.get(
            "solver_stage_label",
            display_solver_stage(
                metadata.get("solver_stage", "--"),
                metadata.get("flow_model_label", summary.get("flow_model_label", "")),
            ),
        )
    )
    entries: Sequence[Tuple[str, object]] = (
        ("Flow status", combustion_result.get("status", "unknown")),
        ("Flow stage", stage_label),
        ("Thermochemistry", "{0} ({1})".format(thermo.get("provider", "--"), thermo.get("status", "--"))),
        (
            "Gas transport",
            "{0} | {1}".format(
                summary.get("gas_transport_status", "--"), summary.get("gas_transport_source", "--")
            ),
        ),
        ("Solved thrust", "{0} N".format(format_number(summary.get("predicted_thrust_newtons", "--"), 2))),
        ("Solved impulse", "{0} N*s".format(format_number(summary.get("predicted_impulse_newton_seconds", "--"), 2))),
        ("Solved specific impulse", "{0} s".format(format_number(summary.get("predicted_isp_seconds", "--"), 3))),
        ("Solved chamber pressure", "{0} kPa".format(format_number(summary.get("chamber_pressure_kpa", "--"), 3))),
        ("Heat transfer status", str(summary.get("heat_transfer_status", "--"))),
        ("Boundary layer model", str(heat_summary.get("boundary_layer_model", "--"))),
        ("Wall-normal nodes", str(heat_summary.get("wall_normal_node_count", "--"))),
        (
            "Thermal grid refinement error",
            "{0}%".format(
                format_number(heat_summary.get("maximum_thermal_grid_refinement_error_percent", "--"), 3)
            ),
        ),
        ("Heat load", "{0} kW".format(format_number(summary.get("heat_load_kw", "--"), 3))),
        ("Maximum hot wall", "{0} K".format(format_number(summary.get("max_hot_wall_temperature_k", "--"), 2))),
        ("Coolant outlet", "{0} K".format(format_number(summary.get("coolant_outlet_temperature_k", "--"), 2))),
        (
            "Coolant required inlet pressure",
            "{0} kPa".format(format_number(heat_summary.get("coolant_required_inlet_pressure_kpa", "--"), 2)),
        ),
        (
            "Coolant pressure margin",
            "{0} kPa".format(format_number(heat_summary.get("coolant_pressure_margin_kpa", "--"), 2)),
        ),
        ("Coolant phase pressure basis", str(heat_summary.get("coolant_phase_pressure_basis", "--"))),
        (
            "Nozzle shock regime",
            "{0} ({1})".format(summary.get("shock_regime", "--"), summary.get("shock_status", "--")),
        ),
    )
    return [
        MeasurementRow(label, str(value), frozenset({"Flow", "Overall"}))
        for label, value in entries
    ]


def _structural_rows(structural_result: dict) -> List[MeasurementRow]:
    payload = dict(structural_result.get("payload", {}))
    summary = dict(payload.get("summary", {}))
    rows = [
        _row(
            "Minimum stress margin",
            "{0} x".format(format_number(summary.get("minimum_stress_margin_ratio", "--"), 3)),
            "Cooling",
            "Overall",
        ),
        _row(
            "Minimum heat margin",
            "{0} x".format(format_number(summary.get("minimum_heat_transfer_margin_ratio", "--"), 3)),
            "Cooling",
            "Overall",
        ),
        _row(
            "Minimum material margin",
            "{0} x".format(format_number(summary.get("minimum_combined_margin_ratio", "--"), 3)),
            "Cooling",
            "Overall",
        ),
        _row(
            "Material redesigns recommended",
            str(summary.get("redesign_recommendation_count", 0)),
            "Cooling",
            "Overall",
        ),
    ]
    for section_row in list(payload.get("section_property_rows", [])):
        if not isinstance(section_row, dict):
            continue
        section = str(section_row.get("section", "")).replace("_", " ").title()
        fields = dict(section_row.get("fields", {}))
        rows.extend(
            [
                _row(
                    "{0} material status".format(section),
                    str(dict(fields.get("redesign_status", {})).get("status", "--")),
                    "Cooling",
                ),
                _row(
                    "{0} recommended material".format(section),
                    str(dict(fields.get("recommended_material", {})).get("value", "--")),
                    "Cooling",
                ),
                _row(
                    "{0} recommended wall".format(section),
                    "{0} mm".format(
                        format_number(dict(fields.get("recommended_wall_thickness_mm", {})).get("value", "--"), 3)
                    ),
                    "Cooling",
                ),
            ]
        )
    return rows


def _uncertainty_rows(bounds: Sequence[dict]) -> List[MeasurementRow]:
    rows: List[MeasurementRow] = []
    for bound in bounds:
        if not isinstance(bound, dict):
            continue
        name = str(bound.get("name", "")).replace("_", " ").strip()
        if not name:
            continue
        unit = str(bound.get("unit", "")).strip()
        rows.append(
            MeasurementRow(
                label="{0} (P05 - P95)".format(name[:1].upper() + name[1:]),
                value="{0} - {1} {2}".format(
                    format_number(bound.get("lower"), 4), format_number(bound.get("upper"), 4), unit
                ).strip(),
                categories=frozenset({"Uncertainty", "Overall"}),
            )
        )
    return rows


def build_measurement_rows(
    design,
    combustion_result: Optional[dict] = None,
    structural_result: Optional[dict] = None,
    uncertainty_bounds: Optional[Sequence[dict]] = None,
) -> List[MeasurementRow]:
    """Assemble every reportable value for the data table."""
    rows: List[MeasurementRow] = [
        _row(row.label, row.value) for row in design.derived.measurement_rows
    ]
    if isinstance(combustion_result, dict) and combustion_result:
        rows.extend(_flow_rows(combustion_result))
    if isinstance(structural_result, dict) and structural_result:
        rows.extend(_structural_rows(structural_result))
    if uncertainty_bounds:
        rows.extend(_uncertainty_rows(uncertainty_bounds))
    return rows


class DataPanel(QWidget):
    """A filterable table of every solved value."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: List[MeasurementRow] = []
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["sm"])

        controls = QHBoxLayout()
        controls.setSpacing(SPACE["sm"])
        self._search = QLineEdit()
        self._search.setObjectName("searchField")
        self._search.setPlaceholderText("Search measurements")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        controls.addWidget(self._search, 1)

        self._category = QComboBox()
        self._category.addItems(CATEGORIES)
        self._category.setFixedWidth(168)
        self._category.currentIndexChanged.connect(self._apply_filter)
        controls.addWidget(self._category)

        self._count_label = QLabel("")
        self._count_label.setObjectName("helperLabel")
        controls.addWidget(self._count_label)

        copy_button = QPushButton("Copy")
        copy_button.setObjectName("ghost")
        copy_button.setToolTip("Copy the visible rows as tab-separated text")
        copy_button.clicked.connect(self.copy_visible)
        controls.addWidget(copy_button)
        layout.addLayout(controls)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Measurement", "Value"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._table.horizontalHeaderItem(1).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        layout.addWidget(self._table, 1)

        families = mono_font_families()
        self._value_font = QFont(families[0])
        self._value_font.setPointSizeF(8.5)

    def set_rows(self, rows: Sequence[MeasurementRow]) -> None:
        """Replace the table contents."""
        self._rows = list(rows)
        self._apply_filter()

    def _visible_rows(self) -> List[MeasurementRow]:
        category = self._category.currentText()
        needle = self._search.text().strip().lower()
        result = []
        for row in self._rows:
            if category != ALL_CATEGORY and category not in row.categories:
                continue
            if needle and needle not in row.label.lower() and needle not in row.value.lower():
                continue
            result.append(row)
        return result

    def _apply_filter(self, *_args) -> None:
        rows = self._visible_rows()
        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            label_item = QTableWidgetItem(row.label)
            value_item = QTableWidgetItem(row.value)
            value_item.setFont(self._value_font)
            value_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(index, 0, label_item)
            self._table.setItem(index, 1, value_item)
        self._table.setUpdatesEnabled(True)
        total = len(self._rows)
        if len(rows) == total:
            self._count_label.setText("{0} values".format(total))
        else:
            self._count_label.setText("{0} of {1}".format(len(rows), total))

    def copy_visible(self) -> None:
        """Put the visible rows on the clipboard as tab-separated text."""
        from PyQt5.QtWidgets import QApplication

        lines = ["{0}\t{1}".format(row.label, row.value) for row in self._visible_rows()]
        QApplication.clipboard().setText("\n".join(lines))
