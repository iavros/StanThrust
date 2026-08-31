"""Design tokens shared by the desktop interface and the Matplotlib canvases.

Colors, type scale, and spacing live here so that the Qt widgets, the scene
graphics, and the embedded plots cannot drift apart.
"""

from __future__ import annotations

import sys
from typing import Dict, Tuple

# --------------------------------------------------------------------------- #
# Color
# --------------------------------------------------------------------------- #

PALETTE: Dict[str, str] = {
    # Surfaces, darkest to lightest.
    "overlay": "#07090B",
    "bg": "#0B0D10",
    "panel": "#12151A",
    "card": "#171B21",
    "card_alt": "#1D222A",
    "raised": "#232932",
    "input": "#0E1115",
    # Lines.
    "border_soft": "#20262E",
    "border": "#303845",
    "border_strong": "#3D4653",
    # Type.
    "text": "#EDF1EF",
    "muted": "#98A29E",
    "muted_soft": "#68716D",
    # Accent and status.
    "accent": "#2A8D7E",
    "accent_hover": "#35A996",
    "accent_dark": "#186A5E",
    "success": "#61C98C",
    "warning": "#DFA648",
    "danger": "#E36A4E",
    "info": "#6DB4F2",
    # Domain colors reused by the schematic, the 3D views, and the plots.
    "fuel": "#E1A955",
    "oxidizer": "#6DB4F2",
    "cooling": "#55C2A2",
    "film": "#B58CFF",
}

PLOT_COLORS: Dict[str, str] = {
    "background": PALETTE["input"],
    "axes": PALETTE["input"],
    "border": PALETTE["border"],
    "grid": PALETTE["border_soft"],
    "text": PALETTE["text"],
    "muted": PALETTE["muted"],
    "centerline": PALETTE["muted_soft"],
    "throat": PALETTE["cooling"],
    "sonic": PALETTE["text"],
}


def alpha(color: str, fraction: float) -> str:
    """Return ``color`` as an ``rgba(...)`` string for use in a stylesheet."""
    hex_value = color.lstrip("#")
    red = int(hex_value[0:2], 16)
    green = int(hex_value[2:4], 16)
    blue = int(hex_value[4:6], 16)
    return "rgba({0}, {1}, {2}, {3:.3f})".format(red, green, blue, max(0.0, min(1.0, fraction)))


# --------------------------------------------------------------------------- #
# Type
# --------------------------------------------------------------------------- #

_UI_FONT_STACKS = {
    "win32": '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif',
    "darwin": '"SF Pro Text", "Helvetica Neue", "Inter", sans-serif',
}
_MONO_FONT_STACKS = {
    "win32": '"Cascadia Mono", "Consolas", monospace',
    "darwin": '"SF Mono", "Menlo", monospace',
}
_DEFAULT_UI_FONTS = '"Inter", "Cantarell", "DejaVu Sans", sans-serif'
_DEFAULT_MONO_FONTS = '"JetBrains Mono", "DejaVu Sans Mono", monospace'


def ui_font_stack() -> str:
    """Return the platform-appropriate interface font stack."""
    return _UI_FONT_STACKS.get(sys.platform, _DEFAULT_UI_FONTS)


def mono_font_stack() -> str:
    """Return the platform-appropriate monospaced font stack."""
    return _MONO_FONT_STACKS.get(sys.platform, _DEFAULT_MONO_FONTS)


def mono_font_families() -> Tuple[str, ...]:
    """Return the monospaced families as a tuple, for ``QFont`` construction."""
    return tuple(part.strip().strip('"') for part in mono_font_stack().split(","))


#: Point sizes used across the interface. Keep the number of steps small.
TYPE_SCALE = {
    "title": 13.0,
    "heading": 11.0,
    "body": 9.5,
    "label": 9.0,
    "caption": 8.5,
    "metric": 21.0,
}


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

#: 4 px spacing scale. Layout code should use these instead of ad-hoc numbers.
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16}

RADIUS = {"sm": 4, "md": 6, "lg": 8}


# --------------------------------------------------------------------------- #
# Stylesheet
# --------------------------------------------------------------------------- #

def build_stylesheet() -> str:
    """Return the application-wide Qt style sheet."""
    p = PALETTE
    t = TYPE_SCALE
    return f"""
/* ---------------------------------------------------------------- base --- */
/* Only the window paints the base colour. Plain container widgets stay
   transparent so that they take the colour of the card or panel they sit in. */
QMainWindow, QDialog, QMessageBox {{
    background: {p["bg"]};
}}
QWidget {{
    color: {p["text"]};
    font-family: {ui_font_stack()};
    font-size: {t["body"]}pt;
}}
QToolTip {{
    background: {p["raised"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 6px 9px;
    font-size: {t["caption"]}pt;
}}

/* ------------------------------------------------------------ chrome ----- */
QMenuBar {{
    background: {p["panel"]};
    color: {p["muted"]};
    border-bottom: 1px solid {p["border_soft"]};
    padding: 2px 4px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 6px 11px;
    border-radius: {RADIUS["sm"]}px;
}}
QMenuBar::item:selected {{
    background: {p["card_alt"]};
    color: {p["text"]};
}}
QMenu {{
    background: {p["card"]};
    border: 1px solid {p["border"]};
    border-radius: {RADIUS["md"]}px;
    padding: 5px;
}}
QMenu::item {{
    padding: 6px 26px 6px 22px;
    border-radius: {RADIUS["sm"]}px;
    color: {p["text"]};
}}
QMenu::item:selected {{
    background: {p["accent_dark"]};
}}
QMenu::item:disabled {{
    color: {p["muted_soft"]};
}}
QMenu::separator {{
    height: 1px;
    background: {p["border_soft"]};
    margin: 5px 8px;
}}
QMenu::indicator {{
    width: 13px;
    height: 13px;
    left: 6px;
}}

QToolBar {{
    background: {p["panel"]};
    border: none;
    border-bottom: 1px solid {p["border_soft"]};
    padding: 7px 12px;
    spacing: 8px;
}}
QToolBar::separator {{
    background: {p["border_soft"]};
    width: 1px;
    margin: 4px 8px;
}}

QStatusBar {{
    background: {p["panel"]};
    color: {p["muted"]};
    border-top: 1px solid {p["border_soft"]};
    font-size: {t["caption"]}pt;
}}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {p["muted"]}; padding: 0 2px; }}

QSplitter::handle:horizontal {{
    background: transparent;
    width: {SPACE["md"]}px;
}}

/* ----------------------------------------------------------- containers -- */
QFrame#panel {{
    background: {p["panel"]};
    border: 1px solid {p["border_soft"]};
    border-radius: {RADIUS["lg"]}px;
}}
QFrame#card, QFrame#metricCard {{
    background: {p["card"]};
    border: 1px solid {p["border_soft"]};
    border-radius: {RADIUS["md"]}px;
}}
QFrame[role="divider"] {{
    background: {p["border_soft"]};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

/* ---------------------------------------------------------------- type --- */
QLabel#brandName {{
    color: {p["text"]};
    font-size: {t["title"]}pt;
    font-weight: 600;
    letter-spacing: 0.4px;
}}
QLabel#sectionTitle {{
    color: {p["text"]};
    font-size: {t["heading"]}pt;
    font-weight: 600;
}}
QLabel#panelTitle {{
    color: {p["text"]};
    font-size: {t["title"]}pt;
    font-weight: 600;
}}
QLabel#eyebrow {{
    color: {p["muted_soft"]};
    font-size: {t["caption"]}pt;
    font-weight: 600;
    letter-spacing: 0.9px;
}}
QLabel#sectionBody, QLabel#helperLabel {{
    color: {p["muted"]};
    font-size: {t["caption"]}pt;
}}
QLabel#fieldLabel {{
    color: {p["text"]};
    font-size: {t["label"]}pt;
    font-weight: 500;
}}
QLabel#statusTitle {{
    color: {p["text"]};
    font-size: {t["heading"]}pt;
    font-weight: 600;
}}
QLabel#statusMessage {{
    color: {p["muted"]};
    font-size: {t["caption"]}pt;
}}
QLabel#metricTitle {{
    color: {p["muted"]};
    font-size: {t["caption"]}pt;
    font-weight: 600;
    letter-spacing: 0.5px;
}}
QLabel#metricValue {{
    color: {p["text"]};
    font-size: {t["metric"]}pt;
    font-weight: 600;
}}
QLabel#metricUnit {{
    color: {p["muted"]};
    font-size: {t["label"]}pt;
}}
QLabel#metricDetail {{
    color: {p["muted_soft"]};
    font-size: {t["caption"]}pt;
}}
QLabel#keyLabel {{
    color: {p["muted"]};
    font-size: {t["caption"]}pt;
}}
QLabel#valueLabel {{
    color: {p["text"]};
    font-family: {mono_font_stack()};
    font-size: {t["caption"]}pt;
}}
QLabel#hintLabel {{
    color: {p["muted_soft"]};
    font-size: {t["caption"]}pt;
}}

/* --------------------------------------------------------------- pills --- */
QLabel[pill="neutral"], QLabel[pill="ready"] {{
    background: {p["raised"]};
    color: {p["muted"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 3px 9px;
    font-size: {t["caption"]}pt;
    font-weight: 600;
}}
QLabel[pill="feasible"] {{
    background: {alpha(p["success"], 0.14)};
    color: {p["success"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 3px 9px;
    font-size: {t["caption"]}pt;
    font-weight: 600;
}}
QLabel[pill="warning"] {{
    background: {alpha(p["warning"], 0.14)};
    color: {p["warning"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 3px 9px;
    font-size: {t["caption"]}pt;
    font-weight: 600;
}}
QLabel[pill="needs-work"] {{
    background: {alpha(p["danger"], 0.14)};
    color: {p["danger"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 3px 9px;
    font-size: {t["caption"]}pt;
    font-weight: 600;
}}
QLabel[pill="accent"] {{
    background: {alpha(p["accent_hover"], 0.16)};
    color: {p["accent_hover"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 3px 9px;
    font-size: {t["caption"]}pt;
    font-weight: 600;
}}

/* ------------------------------------------------------------- buttons --- */
QPushButton {{
    background: {p["card_alt"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 7px 14px;
    font-size: {t["label"]}pt;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {p["raised"]};
    border-color: {p["border_strong"]};
}}
QPushButton:pressed {{
    background: {p["card"]};
}}
QPushButton:disabled {{
    background: {p["panel"]};
    color: {p["muted_soft"]};
    border-color: {p["border_soft"]};
}}
QPushButton#primary {{
    background: {p["accent"]};
    border-color: {p["accent"]};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background: {p["accent_hover"]};
    border-color: {p["accent_hover"]};
}}
QPushButton#primary:pressed {{
    background: {p["accent_dark"]};
    border-color: {p["accent_dark"]};
}}
QPushButton#primary:disabled {{
    background: {p["accent_dark"]};
    border-color: {p["accent_dark"]};
    color: {alpha("#FFFFFF", 0.55)};
}}
QPushButton#segment {{
    background: {p["input"]};
    border: 1px solid {p["border_soft"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 6px 15px;
    color: {p["muted"]};
}}
QPushButton#segment:hover {{
    color: {p["text"]};
}}
QPushButton#segment:checked {{
    background: {p["accent"]};
    border-color: {p["accent"]};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#navButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS["sm"]}px;
    color: {p["muted"]};
    text-align: left;
    padding: 7px 11px;
    font-weight: 500;
}}
QPushButton#navButton:hover {{
    background: {p["card_alt"]};
    color: {p["text"]};
}}
QPushButton#navButton:checked {{
    background: {alpha(p["accent_hover"], 0.13)};
    border-color: {alpha(p["accent_hover"], 0.35)};
    color: {p["text"]};
    font-weight: 600;
}}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid {p["border_soft"]};
    color: {p["muted"]};
    padding: 5px 11px;
}}
QPushButton#ghost:hover {{
    color: {p["text"]};
    border-color: {p["border"]};
}}

/* ---------------------------------------------------------------- tabs --- */
QTabWidget::pane {{
    border: 1px solid {p["border_soft"]};
    border-radius: {RADIUS["md"]}px;
    background: {p["panel"]};
    top: -1px;
}}
QTabBar {{ qproperty-drawBase: 0; }}
QTabWidget::tab-bar {{ left: 4px; }}
QTabBar::tab {{
    background: transparent;
    color: {p["muted"]};
    padding: 8px 16px;
    margin-right: 5px;
    border: 1px solid transparent;
    border-top-left-radius: {RADIUS["sm"]}px;
    border-top-right-radius: {RADIUS["sm"]}px;
    font-size: {t["label"]}pt;
    font-weight: 500;
}}
QTabBar::tab:hover {{
    color: {p["text"]};
    background: {p["card"]};
}}
QTabBar::tab:selected {{
    background: {p["panel"]};
    color: {p["text"]};
    border-color: {p["border_soft"]};
    border-bottom-color: {p["panel"]};
    font-weight: 600;
}}
QTabWidget#subTabs::pane {{
    border: 1px solid {p["border_soft"]};
    background: {p["card"]};
}}
QTabWidget#subTabs QTabBar::tab {{
    min-width: 104px;
    padding: 7px 14px;
}}
QTabWidget#subTabs QTabBar::tab:selected {{
    background: {p["card"]};
    border-bottom-color: {p["card"]};
}}

/* -------------------------------------------------------------- inputs --- */
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {p["input"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 6px 9px;
    selection-background-color: {p["accent"]};
    selection-color: #FFFFFF;
}}
QLineEdit:hover, QComboBox:hover, QDoubleSpinBox:hover, QSpinBox:hover {{
    border-color: {p["border_strong"]};
}}
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {p["accent_hover"]};
}}
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {{
    background: {p["panel"]};
    color: {p["muted_soft"]};
    border-color: {p["border_soft"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
QComboBox QAbstractItemView {{
    background: {p["card"]};
    border: 1px solid {p["border"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 4px;
    outline: none;
    selection-background-color: {p["accent_dark"]};
    selection-color: {p["text"]};
}}
QDoubleSpinBox::up-button, QSpinBox::up-button,
QDoubleSpinBox::down-button, QSpinBox::down-button {{
    width: 16px;
    border: none;
    background: transparent;
}}
QPlainTextEdit#terminalText {{
    background: {p["overlay"]};
    color: #D9E4DE;
    border: 1px solid {p["border_soft"]};
    font-family: {mono_font_stack()};
    font-size: {t["caption"]}pt;
    padding: 10px 12px;
}}
QPlainTextEdit#reportText {{
    background: {p["input"]};
    color: {p["text"]};
    border: 1px solid {p["border_soft"]};
    font-family: {mono_font_stack()};
    font-size: {t["caption"]}pt;
    padding: 12px 14px;
}}
QLineEdit#searchField {{
    padding: 6px 10px;
}}

QCheckBox, QRadioButton {{
    spacing: 8px;
    font-size: {t["label"]}pt;
    font-weight: 500;
    color: {p["text"]};
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {p["muted_soft"]};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    background: {p["input"]};
    border: 1px solid {p["border"]};
}}
QCheckBox::indicator {{ border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {p["accent_hover"]};
}}
QCheckBox::indicator:checked {{
    background: {p["accent"]};
    border-color: {p["accent"]};
}}
QRadioButton::indicator:checked {{
    background: {p["accent"]};
    border: 4px solid {p["input"]};
    outline: 1px solid {p["accent"]};
}}

/* ------------------------------------------------------------- tables ---- */
QTableWidget, QTableView {{
    background: {p["input"]};
    alternate-background-color: {p["panel"]};
    border: 1px solid {p["border_soft"]};
    border-radius: {RADIUS["sm"]}px;
    gridline-color: {p["border_soft"]};
    selection-background-color: {alpha(p["accent_hover"], 0.18)};
    selection-color: {p["text"]};
}}
QTableView::item {{
    padding: 5px 10px;
    border: none;
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {p["panel"]};
    color: {p["muted"]};
    border: none;
    border-bottom: 1px solid {p["border"]};
    padding: 7px 10px;
    font-size: {t["caption"]}pt;
    font-weight: 600;
}}
QTableCornerButton::section {{
    background: {p["panel"]};
    border: none;
}}

QListWidget {{
    background: {p["input"]};
    border: 1px solid {p["border_soft"]};
    border-radius: {RADIUS["sm"]}px;
    padding: 5px;
    outline: none;
}}
QListWidget::item {{
    padding: 7px 10px;
    border-radius: {RADIUS["sm"]}px;
    color: {p["muted"]};
}}
QListWidget::item:hover {{
    background: {p["card"]};
    color: {p["text"]};
}}
QListWidget::item:selected {{
    background: {alpha(p["accent_hover"], 0.15)};
    color: {p["text"]};
    font-weight: 600;
}}
QListWidget::item:disabled {{
    color: {p["muted_soft"]};
    background: transparent;
}}

/* ------------------------------------------------------------ progress --- */
QProgressBar {{
    background: {p["input"]};
    border: 1px solid {p["border_soft"]};
    border-radius: 3px;
    text-align: center;
    color: {p["muted"]};
    font-size: {t["caption"]}pt;
    max-height: 6px;
    min-height: 6px;
}}
QProgressBar::chunk {{
    background: {p["accent"]};
    border-radius: 2px;
}}
QProgressBar#solveProgress {{
    max-height: 8px;
    min-height: 8px;
}}

/* -------------------------------------------------------------- scroll --- */
/* The viewport and its content widget need explicit transparency, otherwise
   they fall back to the default palette base colour. */
QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p["border"]};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p["border_strong"]};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p["border"]};
    border-radius: 5px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {p["border_strong"]};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* -------------------------------------------------------------- scenes --- */
QGraphicsView {{
    background: {p["input"]};
    border: 1px solid {p["border_soft"]};
    border-radius: {RADIUS["sm"]}px;
}}
"""


def apply_theme(application) -> None:
    """Install the base font and style sheet on a ``QApplication``."""
    from PyQt5.QtGui import QFont

    families = [part.strip().strip('"') for part in ui_font_stack().split(",")]
    font = QFont(families[0])
    font.setPointSizeF(TYPE_SCALE["body"])
    font.setStyleStrategy(QFont.PreferAntialias)
    application.setFont(font)
    application.setStyleSheet(build_stylesheet())
