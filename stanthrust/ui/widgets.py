"""Reusable presentation widgets for the StanThrust desktop interface."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from stanthrust.plotting import EngineeringPlotCanvas, FlowFieldPlotCanvas
from stanthrust.theme import PALETTE, SPACE
from stanthrust.ui.formatting import EMPTY


def repolish(widget: QWidget) -> None:
    """Re-evaluate style-sheet selectors after a dynamic property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def divider() -> QFrame:
    """Return a one-pixel horizontal rule."""
    line = QFrame()
    line.setProperty("role", "divider")
    line.setFrameShape(QFrame.NoFrame)
    return line


def pill(text: str, tone: str = "neutral") -> QLabel:
    """Return a small status chip label."""
    label = QLabel(text)
    label.setProperty("pill", tone)
    label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return label


def set_pill(label: QLabel, text: str, tone: str) -> None:
    """Update a chip's text and tone in place."""
    label.setText(text)
    label.setProperty("pill", tone)
    repolish(label)


def legend(entries: Sequence[Tuple[str, str]]) -> QWidget:
    """Return a horizontal colour key from ``(color, label)`` pairs."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE["lg"])
    for color, text in entries:
        item = QWidget()
        item_layout = QHBoxLayout(item)
        item_layout.setContentsMargins(0, 0, 0, 0)
        item_layout.setSpacing(6)
        swatch = QLabel()
        swatch.setFixedSize(10, 10)
        swatch.setStyleSheet("background: {0}; border-radius: 2px;".format(color))
        item_layout.addWidget(swatch)
        label = QLabel(text)
        label.setObjectName("helperLabel")
        item_layout.addWidget(label)
        layout.addWidget(item)
    layout.addStretch(1)
    return row


class Card(QFrame):
    """A titled surface with an optional subtitle and a vertical body layout."""

    def __init__(
        self,
        title: str = "",
        subtitle: str = "",
        *,
        object_name: str = "card",
        margins: Tuple[int, int, int, int] = (SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"]),
    ) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(SPACE["sm"])
        self.title_label: Optional[QLabel] = None
        self.subtitle_label: Optional[QLabel] = None
        if title:
            header = QHBoxLayout()
            header.setSpacing(SPACE["sm"])
            self.title_label = QLabel(title)
            self.title_label.setObjectName("sectionTitle")
            header.addWidget(self.title_label)
            header.addStretch(1)
            self.header_row = header
            self._layout.addLayout(header)
        else:
            self.header_row = None
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("sectionBody")
            self.subtitle_label.setWordWrap(True)
            self._layout.addWidget(self.subtitle_label)

    def body(self) -> QVBoxLayout:
        """Return the card's content layout."""
        return self._layout

    def add_header_widget(self, widget: QWidget) -> None:
        """Place a widget on the right-hand side of the card title row."""
        if self.header_row is None:
            self._layout.addWidget(widget)
            return
        self.header_row.addWidget(widget)


class MetricCard(QFrame):
    """A single headline number with a unit and a supporting detail line."""

    def __init__(self, title: str, unit: str = "") -> None:
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(2)

        title_label = QLabel(title.upper())
        title_label.setObjectName("metricTitle")
        layout.addWidget(title_label)

        value_row = QHBoxLayout()
        value_row.setSpacing(5)
        value_row.setContentsMargins(0, 2, 0, 0)
        self.value_label = QLabel(EMPTY)
        self.value_label.setObjectName("metricValue")
        value_row.addWidget(self.value_label, 0, Qt.AlignBottom)
        self.unit_label = QLabel(unit)
        self.unit_label.setObjectName("metricUnit")
        value_row.addWidget(self.unit_label, 0, Qt.AlignBottom)
        value_row.addStretch(1)
        layout.addLayout(value_row)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("metricDetail")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

    def set_metric(self, value: str, detail: str = "", unit: Optional[str] = None) -> None:
        """Update the displayed number, detail line, and optionally the unit."""
        self.value_label.setText(value)
        self.detail_label.setText(detail)
        if unit is not None:
            self.unit_label.setText(unit)


class KeyValueGrid(QWidget):
    """A two-column label/value grid with monospaced values."""

    def __init__(self, columns: int = 1) -> None:
        super().__init__()
        self._columns = max(1, columns)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(SPACE["lg"])
        self._grid.setVerticalSpacing(6)
        for index in range(self._columns):
            self._grid.setColumnStretch(index * 2 + 1, 1)
        self._value_labels: Dict[str, QLabel] = {}

    def add_row(self, key: str, label_text: str) -> None:
        """Append a labelled row addressable later by ``key``."""
        position = len(self._value_labels)
        row = position // self._columns
        column = (position % self._columns) * 2
        key_label = QLabel(label_text)
        key_label.setObjectName("keyLabel")
        value_label = QLabel(EMPTY)
        value_label.setObjectName("valueLabel")
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._grid.addWidget(key_label, row, column)
        self._grid.addWidget(value_label, row, column + 1)
        self._value_labels[key] = value_label

    def add_rows(self, rows: Iterable[Tuple[str, str]]) -> None:
        """Append several rows at once."""
        for key, label_text in rows:
            self.add_row(key, label_text)

    def set_value(self, key: str, value: str, tone: str = "") -> None:
        """Set a row's value, optionally recoloring it by semantic tone."""
        label = self._value_labels.get(key)
        if label is None:
            return
        label.setText(value)
        color = PALETTE.get(tone) if tone else None
        label.setStyleSheet("color: {0};".format(color) if color else "")

    def clear_values(self) -> None:
        """Reset every row back to the empty placeholder."""
        for key in self._value_labels:
            self.set_value(key, EMPTY)


class StatusStrip(QFrame):
    """The headline validation state shown at the top of the overview."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        layout.setSpacing(SPACE["md"])

        self.chip = pill("Ready", "ready")
        layout.addWidget(self.chip, 0, Qt.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        self.title = QLabel("Ready to solve")
        self.title.setObjectName("statusTitle")
        self.title.setWordWrap(True)
        text_column.addWidget(self.title)
        self.message = QLabel("Set the inputs on the left, then run the coupled solve.")
        self.message.setObjectName("statusMessage")
        self.message.setWordWrap(True)
        text_column.addWidget(self.message)
        layout.addLayout(text_column, 1)

    def update_status(self, label: str, title: str, message: str, tone: str) -> None:
        """Set the state chip, the validation headline, and the next-step line."""
        set_pill(self.chip, label, tone)
        self.title.setText(title)
        self.message.setText(message)


class StepList(QWidget):
    """Solver pipeline steps rendered as state dots with status text."""

    _TONE_COLORS = {
        "waiting": PALETTE["muted_soft"],
        "active": PALETTE["warning"],
        "done": PALETTE["success"],
        "skipped": PALETTE["muted"],
        "failed": PALETTE["danger"],
    }

    def __init__(self, steps: Sequence[Tuple[str, str]]) -> None:
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE["md"])
        grid.setVerticalSpacing(7)
        grid.setColumnStretch(1, 1)
        self._dots: Dict[str, QLabel] = {}
        self._status: Dict[str, QLabel] = {}
        for row, (key, label_text) in enumerate(steps):
            dot = QLabel("●")
            dot.setFixedWidth(12)
            name = QLabel(label_text)
            name.setObjectName("fieldLabel")
            status = QLabel("Waiting")
            status.setObjectName("helperLabel")
            status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(dot, row, 0)
            grid.addWidget(name, row, 1)
            grid.addWidget(status, row, 2)
            self._dots[key] = dot
            self._status[key] = status
        self.reset()

    def keys(self) -> List[str]:
        """Return the step keys in display order."""
        return list(self._dots)

    def set_step(self, key: str, tone: str, status: str) -> None:
        """Update one step's indicator color and status text."""
        dot = self._dots.get(key)
        if dot is not None:
            color = self._TONE_COLORS.get(tone, PALETTE["muted_soft"])
            dot.setStyleSheet("color: {0}; font-size: 11pt;".format(color))
        label = self._status.get(key)
        if label is not None:
            label.setText(status)

    def reset(self) -> None:
        """Return every step to the waiting state."""
        for key in self._dots:
            self.set_step(key, "waiting", "Waiting")


class NoWheelComboBox(QComboBox):
    """Combo box that ignores wheel events so scrolling never changes values."""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Spin box that ignores wheel events so scrolling never changes values."""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()


class EngineeringPlotCard(QFrame):
    """A titled frame wrapping one dual-axis Matplotlib line plot."""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        layout.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("sectionBody")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)
        self.canvas = EngineeringPlotCanvas()
        layout.addWidget(self.canvas, 1)
        self.note_label = QLabel("")
        self.note_label.setObjectName("helperLabel")
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
        """Forward series data to the canvas and refresh the surrounding text."""
        self.subtitle_label.setText(subtitle)
        self.note_label.setText(note)
        self.note_label.setVisible(bool(note))
        self.canvas.set_plot_data(
            x_label=x_label,
            primary_label=primary_label,
            primary_series=primary_series,
            secondary_label=secondary_label,
            secondary_series=secondary_series or [],
            empty_message=empty_message,
        )


class FlowFieldPlotCard(QFrame):
    """A titled frame wrapping the axisymmetric station-field canvas."""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        layout.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("sectionBody")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)
        self.canvas = FlowFieldPlotCanvas()
        layout.addWidget(self.canvas, 1)
        self.note_label = QLabel("")
        self.note_label.setObjectName("helperLabel")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)

    def set_flow_data(
        self,
        *,
        subtitle: str,
        axial_profile: List[Dict[str, object]],
        variable: str = "mach",
        variable_label: str = "Mach",
        note: str = "",
    ) -> None:
        """Forward station data to the canvas and refresh the surrounding text."""
        self.subtitle_label.setText(subtitle)
        self.note_label.setText(note)
        self.note_label.setVisible(bool(note))
        self.canvas.set_flow_data(axial_profile, variable=variable, variable_label=variable_label)
