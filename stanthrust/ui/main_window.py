"""The StanThrust main window: menus, input panel, result views, and solve flow."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from stanthrust import __version__ as APP_VERSION
from stanthrust.coupled_cycle_solver import solve as solve_coupled_cycle
from stanthrust.design_model import create_engine_design
from stanthrust.exporter import (
    export_cad_json,
    export_measurements_csv,
    export_profile_dxf,
    export_station_csv,
    load_project,
    save_project,
)
from stanthrust.inputs import get_default_solver_assumptions
from stanthrust.objectives import evaluate_objectives
from stanthrust.optimizer_hooks import (
    apply_multifidelity_confirmation,
    build_optimizer_seed,
    run_feasibility_first_optimizer,
    run_genetic_optimizer,
)
from stanthrust.structural_material_solver import (
    assign_materials,
    build_structural_materials_output,
)
from stanthrust.theme import PALETTE, SPACE, apply_theme
from stanthrust.ui import updates
from stanthrust.ui.data_panel import DataPanel, build_measurement_rows
from stanthrust.ui.formatting import (
    EMPTY,
    FLOW_MODEL_DISPLAY_NAMES,
    display_injector_name,
    format_number,
    format_percent,
    safe_float,
)
from stanthrust.ui.inputs_panel import (
    FINAL_SOLVER_STATION_COUNT,
    InputsPanel,
)
from stanthrust.ui.model3d import Model3DView
from stanthrust.ui.plots_panel import PlotsPanel
from stanthrust.ui.report import (
    build_diagnostic_lines,
    build_report_text,
    convergence_summary,
)
from stanthrust.ui.schematic import SchematicView
from stanthrust.ui.widgets import (
    Card,
    KeyValueGrid,
    MetricCard,
    StatusStrip,
    StepList,
    divider,
    legend,
    pill,
    set_pill,
)
from stanthrust.validation_pack import validate_engine_design

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGO_PATH = PROJECT_ROOT / "assets" / "Logo.png"
ICON_PATH = PROJECT_ROOT / "assets" / "app_icon.ico"

PROJECT_FILTER = "StanThrust Project (*.stanth.json);;JSON Files (*.json)"
LEGACY_PROJECT_FILTER = (
    "StanThrust Project (*.stanth.json);;Legacy Project (*.liquid.json);;JSON Files (*.json)"
)

#: Solve pipeline steps shown on the overview, keyed by progress-message match.
SOLVE_STEPS = (
    ("preview", "Design preview"),
    ("optimization", "Optimisation"),
    ("feed", "Feed transient"),
    ("chamber_nozzle", "Chamber and nozzle"),
    ("structure", "Structure and materials"),
    ("convergence", "Coupled convergence"),
)

RESULT_TABS = (
    ("overview", "Overview"),
    ("schematic", "Schematic"),
    ("model", "3D Model"),
    ("plots", "Plots"),
    ("data", "Data"),
    ("report", "Report"),
    ("log", "Log"),
)

MODEL_VIEWS = (
    ("chamber_nozzle", "Chamber and Nozzle"),
    ("pumps", "Pumps"),
    ("injector", "Injector"),
    ("tanks", "Tanks"),
)

_LOG_LIMIT = 400
_PREVIEW_DEBOUNCE_MS = 130


class MainWindow(QMainWindow):
    """Top-level window tying the input panel to the solver and result views."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("StanThrust")
        self.resize(1560, 980)
        self.setMinimumSize(1240, 800)
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))

        # Solved state.
        self.current_design = None
        self.current_objective_report: Optional[dict] = None
        self.current_validation_report = None
        self.current_combustion_result: Optional[dict] = None
        self.current_solver_interface_result: Optional[dict] = None
        self.current_coupled_cycle_result: Optional[dict] = None
        self.current_structural_result: Optional[dict] = None
        self.current_ga_result = None
        self.current_ga_candidate_state: Optional[dict] = None
        self.current_input_state: Dict[str, object] = {}
        self.solver_assumptions = get_default_solver_assumptions()

        # View state.
        self.is_full_workspace = False
        self._solver_log: List[str] = []
        self._diagnostic_lines: List[str] = []
        self._solver_error_count = 0
        self._log_filter = "All"

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self.refresh_preview)

        self._build_ui()
        self.reset_form()

    # ------------------------------------------------------------------ ui -- #

    def _build_ui(self) -> None:
        self.inputs = InputsPanel()
        self.inputs.changed.connect(self._on_input_changed)
        self.inputs.hint.connect(self._show_hint)
        #: Alias kept so callers and tests can reach controls by key.
        self.widgets = self.inputs.widgets

        self._build_actions()
        self._build_menus()
        self.addToolBar(Qt.TopToolBarArea, self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_inputs_side())
        splitter.addWidget(self._build_results_side())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([430, 1130])

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["sm"])
        root_layout.setSpacing(SPACE["md"])
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)
        self.setStatusBar(self._build_status_bar())

    def _build_actions(self) -> None:
        def action(text: str, slot, shortcut: str = "", tip: str = "") -> QAction:
            item = QAction(text, self)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
            if tip:
                item.setStatusTip(tip)
            item.triggered.connect(slot)
            return item

        self.action_solve = action(
            "&Run Coupled Solve", self.run_solver, "F5", "Run the full coupled design solve"
        )
        self.action_reset = action(
            "Reset &Inputs", self.reset_form, "Ctrl+Shift+R", "Return every input to its default"
        )
        self.action_open = action(
            "&Open Project...", self.load_project_dialog, "Ctrl+O", "Load a saved project file"
        )
        self.action_save = action(
            "&Save Project As...", self.save_project_dialog, "Ctrl+S", "Save the current inputs"
        )
        self.action_export_dxf = action(
            "Profile &DXF...", self.export_profile_dxf, "", "Export the revolved profile for CAD"
        )
        self.action_export_measurements = action(
            "&Measurements CSV...", self.export_measurements, "", "Export every solved value"
        )
        self.action_export_stations = action(
            "&Station CSV...", self.export_stations, "", "Export the axial station table"
        )
        self.action_export_cad_json = action(
            "CAD &JSON...", self.export_cad_json, "", "Export the full CAD payload with provenance"
        )
        self.action_quit = action("E&xit", self.close, "Ctrl+Q")
        self.action_check_update = action(
            "Check for &Updates...", self.check_for_update, "", "Look for a newer release on GitHub"
        )
        self.action_about = action("&About StanThrust", self.show_about)

        self.action_workspace_essential = QAction("&Essential", self)
        self.action_workspace_full = QAction("&Full", self)
        workspace_group = QActionGroup(self)
        for item, full in ((self.action_workspace_essential, False), (self.action_workspace_full, True)):
            item.setCheckable(True)
            item.setActionGroup(workspace_group)
            item.triggered.connect(lambda _checked=False, value=full: self.set_full_workspace(value))
        self.action_workspace_essential.setChecked(True)

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        file_menu.addAction(self.action_open)
        file_menu.addAction(self.action_save)
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("&Export")
        export_menu.addAction(self.action_export_dxf)
        export_menu.addAction(self.action_export_measurements)
        export_menu.addAction(self.action_export_stations)
        export_menu.addAction(self.action_export_cad_json)
        file_menu.addSeparator()
        file_menu.addAction(self.action_quit)

        solve_menu = bar.addMenu("&Solve")
        solve_menu.addAction(self.action_solve)
        solve_menu.addAction(self.action_reset)

        view_menu = bar.addMenu("&View")
        workspace_menu = view_menu.addMenu("&Workspace")
        workspace_menu.addAction(self.action_workspace_essential)
        workspace_menu.addAction(self.action_workspace_full)
        view_menu.addSeparator()
        for index, (_key, label) in enumerate(RESULT_TABS):
            item = QAction(label, self)
            item.setShortcut(QKeySequence("Ctrl+{0}".format(index + 1)))
            item.triggered.connect(lambda _checked=False, value=index: self.result_tabs.setCurrentIndex(value))
            view_menu.addAction(item)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self.action_check_update)
        help_menu.addAction(self.action_about)

    def _build_toolbar(self) -> QToolBar:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)

        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, SPACE["sm"], 0)
        brand_layout.setSpacing(SPACE["sm"])
        if LOGO_PATH.exists():
            logo = QLabel()
            logo.setPixmap(
                QPixmap(str(LOGO_PATH)).scaled(26, 26, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            brand_layout.addWidget(logo)
        name = QLabel("StanThrust")
        name.setObjectName("brandName")
        brand_layout.addWidget(name)
        toolbar.addWidget(brand)
        toolbar.addSeparator()

        self.solve_button = QPushButton("Solve")
        self.solve_button.setObjectName("primary")
        self.solve_button.setMinimumWidth(104)
        self.solve_button.setToolTip("Run the full coupled design solve  (F5)")
        self.solve_button.clicked.connect(self.run_solver)
        toolbar.addWidget(self.solve_button)

        reset_button = QPushButton("Reset")
        reset_button.setToolTip("Return every input to its default")
        reset_button.clicked.connect(self.reset_form)
        toolbar.addWidget(reset_button)
        toolbar.addSeparator()

        workspace_label = QLabel("Workspace")
        workspace_label.setObjectName("eyebrow")
        toolbar.addWidget(workspace_label)
        self._workspace_buttons: Dict[bool, QPushButton] = {}
        for text, full, tip in (
            ("Essential", False, "Mission, envelope, and architecture inputs"),
            ("Full", True, "Every input, including materials, objectives, solver, and hydraulics"),
        ):
            button = QPushButton(text)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, value=full: self.set_full_workspace(value))
            self._workspace_buttons[full] = button
            toolbar.addWidget(button)

        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().Expanding, spacer.sizePolicy().Preferred)
        toolbar.addWidget(spacer)

        self.toolbar_state_pill = pill("Preview", "neutral")
        toolbar.addWidget(self.toolbar_state_pill)
        return toolbar

    def _build_inputs_side(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        frame.setMinimumWidth(400)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        title = QLabel("Design Inputs")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        layout.addWidget(self.inputs, 1)
        layout.addWidget(divider())

        self.hint_label = QLabel("Hover an input for a description.")
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setWordWrap(True)
        self.hint_label.setMinimumHeight(34)
        self.hint_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(self.hint_label)
        return frame

    def _build_results_side(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        self.result_tabs = QTabWidget()
        self.result_tabs.setDocumentMode(True)
        self.result_tabs.tabBar().setExpanding(False)
        self.result_tabs.tabBar().setElideMode(Qt.ElideNone)
        builders = {
            "overview": self._build_overview_tab,
            "schematic": self._build_schematic_tab,
            "model": self._build_model_tab,
            "plots": self._build_plots_tab,
            "data": self._build_data_tab,
            "report": self._build_report_tab,
            "log": self._build_log_tab,
        }
        for key, label in RESULT_TABS:
            self.result_tabs.addTab(builders[key](), label)
        layout.addWidget(self.result_tabs, 1)
        return frame

    @staticmethod
    def _tab_shell() -> tuple:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["md"])
        return tab, layout

    def _build_overview_tab(self) -> QWidget:
        tab, layout = self._tab_shell()

        self.status_strip = StatusStrip()
        layout.addWidget(self.status_strip)

        self.metric_cards: Dict[str, MetricCard] = {}
        metric_row = QHBoxLayout()
        metric_row.setSpacing(SPACE["sm"])
        for key, title, unit in (
            ("thrust", "Thrust", "N"),
            ("isp", "Specific impulse", "s"),
            ("chamber_pressure", "Chamber pressure", "kPa"),
            ("mass_flow", "Mass flow", "kg/s"),
        ):
            card = MetricCard(title, unit)
            self.metric_cards[key] = card
            metric_row.addWidget(card, 1)
        layout.addLayout(metric_row)

        columns = QHBoxLayout()
        columns.setSpacing(SPACE["md"])

        left = QVBoxLayout()
        left.setSpacing(SPACE["md"])
        envelope_card = Card("Geometry and envelope")
        self.envelope_grid = KeyValueGrid()
        self.envelope_grid.add_rows(
            (
                ("total_impulse", "Total impulse"),
                ("propellant_mass", "Propellant used"),
                ("max_diameter", "Maximum outer diameter"),
                ("diameter_limit", "Diameter limit"),
                ("required_diameter", "Uncapped requirement"),
                ("total_length", "Stacked length"),
            )
        )
        envelope_card.body().addWidget(self.envelope_grid)
        left.addWidget(envelope_card)

        margins_card = Card("Margins and residuals")
        self.margins_grid = KeyValueGrid()
        self.margins_grid.add_rows(
            (
                ("feed_margin", "Minimum feed margin"),
                ("stress_margin", "Minimum stress margin"),
                ("material_margin", "Minimum material margin"),
                ("heat_margin", "Minimum heat margin"),
                ("pc_residual", "Chamber pressure residual"),
                ("thrust_error", "Thrust error"),
            )
        )
        margins_card.body().addWidget(self.margins_grid)
        left.addWidget(margins_card)

        configuration_card = Card("Configuration")
        self.configuration_grid = KeyValueGrid()
        self.configuration_grid.add_rows(
            (
                ("propellants", "Propellants"),
                ("mixture_ratio", "Mixture ratio"),
                ("injector", "Injector family"),
                ("feed", "Feed system"),
                ("cooling", "Chamber cooling"),
                ("chamber_material", "Chamber material"),
                ("nozzle_material", "Nozzle material"),
                ("factor_of_safety", "Safety factor"),
            )
        )
        configuration_card.body().addWidget(self.configuration_grid)
        left.addWidget(configuration_card)
        left.addStretch(1)
        columns.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(SPACE["md"])
        pipeline_card = Card("Solve pipeline")
        self.stage_label = QLabel("Idle. Press Solve to run the coupled solver.")
        self.stage_label.setObjectName("sectionBody")
        self.stage_label.setWordWrap(True)
        pipeline_card.body().addWidget(self.stage_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("solveProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        pipeline_card.body().addWidget(self.progress_bar)
        self.step_list = StepList(SOLVE_STEPS)
        pipeline_card.body().addWidget(self.step_list)
        pipeline_card.body().addWidget(divider())
        self.residual_label = QLabel(convergence_summary(None))
        self.residual_label.setObjectName("helperLabel")
        self.residual_label.setWordWrap(True)
        pipeline_card.body().addWidget(self.residual_label)
        right.addWidget(pipeline_card)

        validation_card = Card("Validation")
        self.validation_pill = pill("8 checks", "neutral")
        validation_card.add_header_widget(self.validation_pill)
        self.validation_detail = QLabel("")
        self.validation_detail.setObjectName("sectionBody")
        self.validation_detail.setWordWrap(True)
        validation_card.body().addWidget(self.validation_detail)
        right.addWidget(validation_card)

        flow_card = Card("Flow and thermochemistry")
        self.flow_grid = KeyValueGrid()
        self.flow_grid.add_rows(
            (
                ("flow_model", "Flow model"),
                ("stations", "Axial stations"),
                ("thermochemistry", "Thermochemistry"),
                ("transport", "Gas transport"),
                ("heat_load", "Heat load"),
                ("hot_wall", "Maximum hot wall"),
                ("coolant_outlet", "Coolant outlet"),
                ("shock", "Nozzle shock regime"),
            )
        )
        flow_card.body().addWidget(self.flow_grid)
        right.addWidget(flow_card)
        right.addStretch(1)
        columns.addLayout(right, 1)

        layout.addLayout(columns, 1)
        return tab

    def _build_schematic_tab(self) -> QWidget:
        tab, layout = self._tab_shell()
        caption = QLabel(
            "Propellant stack, feed architecture, and the solved chamber and nozzle contour."
        )
        caption.setObjectName("sectionBody")
        layout.addWidget(caption)
        self.schematic_view = SchematicView()
        # The scene has a fixed aspect ratio, so let it size to the width rather
        # than letterboxing inside a taller frame.
        layout.addWidget(self.schematic_view)
        layout.addWidget(
            legend(
                (
                    (PALETTE["oxidizer"], "Oxidizer path"),
                    (PALETTE["fuel"], "Fuel path"),
                    (PALETTE["cooling"], "Regenerative cooling"),
                    (PALETTE["film"], "Film cooling"),
                    (PALETTE["accent_hover"], "Throat station"),
                )
            )
        )
        layout.addStretch(1)
        return tab

    def _build_model_tab(self) -> QWidget:
        tab, layout = self._tab_shell()
        caption = QLabel("Views are built from the solved geometry. Drag inside a view to orbit it.")
        caption.setObjectName("sectionBody")
        layout.addWidget(caption)

        component_tabs = QTabWidget()
        component_tabs.setObjectName("subTabs")
        component_tabs.setDocumentMode(True)
        component_tabs.tabBar().setExpanding(False)
        self.model_views: Dict[str, Model3DView] = {}
        for key, label in MODEL_VIEWS:
            view = Model3DView(key)
            self.model_views[key] = view
            component_tabs.addTab(view, label)
        layout.addWidget(component_tabs, 1)
        return tab

    def _build_plots_tab(self) -> QWidget:
        tab, layout = self._tab_shell()
        self.plots_panel = PlotsPanel()
        #: Exposed for callers that address individual plot cards by key.
        self.plot_cards = self.plots_panel.cards
        self.flow_field_card = self.plots_panel.flow_field_card
        layout.addWidget(self.plots_panel, 1)
        return tab

    def _build_data_tab(self) -> QWidget:
        tab, layout = self._tab_shell()
        self.data_panel = DataPanel()
        layout.addWidget(self.data_panel, 1)
        return tab

    def _build_report_tab(self) -> QWidget:
        tab, layout = self._tab_shell()
        header = QHBoxLayout()
        header.setSpacing(SPACE["sm"])
        caption = QLabel("Design report for the current preview or solved state.")
        caption.setObjectName("sectionBody")
        header.addWidget(caption, 1)
        copy_button = QPushButton("Copy")
        copy_button.setObjectName("ghost")
        copy_button.clicked.connect(self._copy_report)
        header.addWidget(copy_button)
        layout.addLayout(header)

        self.report_text = QPlainTextEdit()
        self.report_text.setObjectName("reportText")
        self.report_text.setReadOnly(True)
        self.report_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.report_text, 1)
        return tab

    def _build_log_tab(self) -> QWidget:
        tab, layout = self._tab_shell()
        header = QHBoxLayout()
        header.setSpacing(SPACE["sm"])
        caption = QLabel("Solver log and the keyed diagnostic snapshot.")
        caption.setObjectName("sectionBody")
        header.addWidget(caption, 1)

        self.log_filter = QComboBox()
        self.log_filter.addItems(("All", "Info", "Warnings", "Errors"))
        self.log_filter.setToolTip("Filter the log by severity")
        self.log_filter.setFixedWidth(132)
        self.log_filter.currentTextChanged.connect(self._on_log_filter_changed)
        header.addWidget(self.log_filter)

        copy_button = QPushButton("Copy")
        copy_button.setObjectName("ghost")
        copy_button.clicked.connect(self._copy_log)
        header.addWidget(copy_button)
        clear_button = QPushButton("Clear")
        clear_button.setObjectName("ghost")
        clear_button.clicked.connect(self._clear_log)
        header.addWidget(clear_button)
        layout.addLayout(header)

        self.log_text = QPlainTextEdit()
        self.log_text.setObjectName("terminalText")
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.log_text, 1)
        return tab

    def _build_status_bar(self) -> QStatusBar:
        bar = QStatusBar()
        bar.showMessage("Ready")
        self.status_thermo = QLabel("")
        self.status_thermo.setObjectName("helperLabel")
        bar.addPermanentWidget(self.status_thermo)
        self.status_progress = QProgressBar()
        self.status_progress.setFixedWidth(120)
        self.status_progress.setRange(0, 100)
        self.status_progress.setTextVisible(False)
        self.status_progress.setVisible(False)
        bar.addPermanentWidget(self.status_progress)
        version_label = QLabel("v{0}".format(APP_VERSION))
        version_label.setObjectName("helperLabel")
        bar.addPermanentWidget(version_label)
        return bar

    # -------------------------------------------------------------- state -- #

    def mode(self) -> str:
        """Return the saved workspace-mode token."""
        return "expert" if self.is_full_workspace else "explorer"

    def set_full_workspace(self, enabled: bool) -> None:
        """Show or hide the advanced input categories."""
        self.is_full_workspace = bool(enabled)
        self.inputs.set_full_workspace(self.is_full_workspace)
        self._workspace_buttons[False].setChecked(not self.is_full_workspace)
        self._workspace_buttons[True].setChecked(self.is_full_workspace)
        self.action_workspace_essential.setChecked(not self.is_full_workspace)
        self.action_workspace_full.setChecked(self.is_full_workspace)

    def collect_form_state(self) -> dict:
        """Return the solver input state for the current control values."""
        state = self.inputs.collect_state()
        state["ui_mode"] = self.mode()
        return state

    def collect_objective_weights(self) -> dict:
        """Return the objective weights exactly as entered.

        Every consumer normalises, so saving the entered values keeps a project
        round trip exact instead of quantising normalised weights into the
        three-decimal spin boxes.
        """
        return self.inputs.collect_objective_weights()

    def reset_form(self) -> None:
        """Return every input to its default and rebuild the preview."""
        self.inputs.reset()
        self.set_full_workspace(False)
        self.current_ga_result = None
        self.current_ga_candidate_state = None
        self.current_combustion_result = None
        self.current_solver_interface_result = None
        self.current_coupled_cycle_result = None
        self.refresh_preview()

    def _on_input_changed(self) -> None:
        self.current_combustion_result = None
        self.current_solver_interface_result = None
        self.current_coupled_cycle_result = None
        self.current_structural_result = None
        self.current_ga_result = None
        self.current_ga_candidate_state = None
        self.inputs.apply_interlocks()
        self.statusBar().showMessage("Updating preview...")
        self._preview_timer.start(_PREVIEW_DEBOUNCE_MS)

    def _show_hint(self, text: str) -> None:
        self.hint_label.setText(text or "Hover an input for a description.")

    # ------------------------------------------------------------ preview -- #

    def refresh_preview(self, status: str = "Preview ready.") -> None:
        """Rebuild the design preview and every dependent view."""
        state = self.collect_form_state()
        self.current_input_state = dict(state)
        self.current_design = create_engine_design(state)
        self.current_objective_report = evaluate_objectives(
            self.current_design, self.collect_objective_weights()
        )
        self.current_validation_report = validate_engine_design(self.current_design)
        self.current_structural_result = self._build_structural_result()
        self._render_all()
        self.statusBar().showMessage(status)

    def _render_all(self) -> None:
        self._render_status()
        self._render_metrics()
        self._render_configuration()
        self._render_flow()
        self._render_geometry()
        self._render_margins()
        self._render_validation()
        self._render_data()
        self._render_report()
        self._render_diagnostics()
        self._render_plots()
        self._render_scenes()
        self.residual_label.setText(convergence_summary(self.current_coupled_cycle_result))

    def _render_scenes(self) -> None:
        if self.current_design is None:
            return
        self.schematic_view.render_design(self.current_design)
        for view in self.model_views.values():
            view.render_design(self.current_design)

    def _is_solved(self) -> bool:
        return bool(self.current_combustion_result)

    def _combustion_summary(self) -> dict:
        return dict(dict(self.current_combustion_result or {}).get("summary", {}))

    def _coupled_payload(self) -> dict:
        return dict(dict(self.current_coupled_cycle_result or {}).get("payload", {}))

    def _uncertainty_bounds(self) -> List[dict]:
        if not self.inputs.is_checked("show_uncertainty"):
            return []
        bounds = dict(self._coupled_payload().get("final_uncertainty_bounds", {})).get("bounds", [])
        return [dict(row) for row in bounds if isinstance(row, dict)]

    def _render_status(self) -> None:
        solved = self._is_solved()
        label = "Solved" if solved else "Preview"
        set_pill(self.toolbar_state_pill, label, "accent" if solved else "neutral")

        report = self.current_validation_report
        if report is None:
            chip, tone, title = "Ready", "ready", "Ready to solve"
        elif report.passed:
            chip, tone, title = "Feasible", "feasible", report.summary
        else:
            chip, tone, title = "Needs work", "needs-work", report.summary

        if solved:
            summary = self._combustion_summary()
            message = "Solved with {0} at {1} axial stations.".format(
                FLOW_MODEL_DISPLAY_NAMES["viscous"], int(summary.get("station_count", 0) or 0)
            )
        elif report is not None and not report.passed:
            message = (
                "Adjust the flagged inputs, or press Solve to let the optimiser search for a "
                "feasible design."
            )
        else:
            message = "Geometry preview only. Press Solve to run the coupled numerical solve."
        self.status_strip.update_status(chip, title, message, tone)

    def _set_metric(self, key: str, value: object, decimals: int, detail: str) -> None:
        """Write one headline metric, marking unsolved values honestly."""
        text = format_number(value, decimals)
        self.metric_cards[key].set_metric(
            text, detail if text != EMPTY else "Available after the coupled solve"
        )

    def _render_metrics(self) -> None:
        if self.current_design is None:
            return
        values = dict(self.current_design.derived.engineering_values)
        summary = self._combustion_summary()
        solved = self._is_solved()
        source = "Solved" if solved else "Preview estimate"

        thrust = safe_float(
            summary.get("predicted_thrust_newtons") if solved else values.get("calculated_thrust_newtons")
        )
        thrust_target = safe_float(
            values.get("target_thrust_newtons", self.current_design.inputs.target_thrust_newtons), 0.0
        )
        self._set_metric(
            "thrust",
            thrust,
            1,
            "{0} of target  ·  {1}".format(format_percent(thrust, thrust_target), source),
        )
        self._set_metric(
            "isp",
            summary.get("predicted_isp_seconds") if solved else values.get("predicted_isp_seconds"),
            2,
            source,
        )
        self._set_metric(
            "chamber_pressure",
            summary.get("chamber_pressure_kpa") if solved else values.get("chamber_pressure_kpa"),
            1,
            source,
        )
        self._set_metric(
            "mass_flow",
            summary.get("mass_flow_kg_s") if solved else values.get("propellant_mass_flow_kg_s"),
            5,
            source,
        )

    def _render_configuration(self) -> None:
        if self.current_design is None:
            return
        inputs = self.current_design.inputs
        if inputs.regen_cooling and inputs.film_cooling:
            cooling = "Regenerative and film"
        elif inputs.regen_cooling:
            cooling = "Regenerative"
        elif inputs.film_cooling:
            cooling = "Film"
        else:
            cooling = "Passive"
        rows = {
            "propellants": "{0} / {1}".format(inputs.fuel_name, inputs.oxidizer_name),
            "mixture_ratio": format_number(inputs.mixture_ratio, 3),
            "injector": display_injector_name(inputs.injector_type),
            "feed": "Pump-fed" if inputs.use_pumps else "Pressure-fed",
            "cooling": cooling,
            "chamber_material": inputs.chamber_material,
            "nozzle_material": inputs.nozzle_material,
            "factor_of_safety": format_number(inputs.factor_of_safety, 2),
        }
        for key, value in rows.items():
            self.configuration_grid.set_value(key, value)

    def _render_flow(self) -> None:
        if not self._is_solved():
            self.flow_grid.clear_values()
            self.flow_grid.set_value("flow_model", FLOW_MODEL_DISPLAY_NAMES["viscous"])
            return
        summary = self._combustion_summary()
        metadata = dict(dict(self.current_combustion_result or {}).get("metadata", {}))
        thermo = dict(metadata.get("thermochemistry", {}))
        heat_summary = dict(
            dict(dict(self.current_combustion_result or {}).get("heat_transfer", {})).get("summary", {})
        )
        rows = {
            "flow_model": str(
                metadata.get("flow_model_label", FLOW_MODEL_DISPLAY_NAMES["viscous"])
            ),
            "stations": str(int(summary.get("station_count", 0) or 0)),
            "thermochemistry": "{0} ({1})".format(
                thermo.get("provider", EMPTY), thermo.get("status", EMPTY)
            ),
            "transport": str(summary.get("gas_transport_status", EMPTY)),
            "heat_load": "{0} kW".format(
                format_number(
                    heat_summary.get("total_heat_load_kw", summary.get("heat_load_kw")), 3
                )
            ),
            "hot_wall": "{0} K".format(format_number(summary.get("max_hot_wall_temperature_k"), 1)),
            "coolant_outlet": "{0} K".format(
                format_number(summary.get("coolant_outlet_temperature_k"), 1)
            ),
            "shock": "{0} ({1})".format(
                summary.get("shock_regime", EMPTY), summary.get("shock_status", EMPTY)
            ),
        }
        for key, value in rows.items():
            self.flow_grid.set_value(key, value)

    def _render_geometry(self) -> None:
        if self.current_design is None:
            return
        design = self.current_design
        values = dict(design.derived.engineering_values)
        summary = self._combustion_summary()
        impulse = (
            summary.get("predicted_impulse_newton_seconds")
            if self._is_solved()
            else values.get("calculated_impulse_newton_seconds")
        )
        impulse_target = safe_float(
            values.get(
                "target_impulse_newton_seconds", design.inputs.target_impulse_newton_seconds
            ),
            0.0,
        )
        maximum_diameter = safe_float(design.derived.maximum_diameter_mm, 0.0)
        limit = safe_float(design.inputs.target_diameter_mm, 0.0)
        required = safe_float(values.get("maximum_required_outer_diameter_mm"), maximum_diameter)

        self.envelope_grid.set_value(
            "total_impulse",
            "{0} N*s  ({1})".format(format_number(impulse, 1), format_percent(impulse, impulse_target)),
        )
        self.envelope_grid.set_value(
            "propellant_mass", "{0} kg".format(format_number(values.get("propellant_mass_used_kg"), 3))
        )
        self.envelope_grid.set_value(
            "max_diameter",
            "{0} mm".format(format_number(maximum_diameter, 2)),
            "danger" if required > limit else "",
        )
        self.envelope_grid.set_value("diameter_limit", "{0} mm".format(format_number(limit, 2)))
        self.envelope_grid.set_value(
            "required_diameter",
            "{0} mm".format(format_number(required, 2)),
            "warning" if required > limit else "",
        )
        self.envelope_grid.set_value(
            "total_length", "{0} mm".format(format_number(design.derived.total_stack_length_mm, 2))
        )

    def _render_margins(self) -> None:
        structural_summary = dict(
            dict(dict(self.current_structural_result or {}).get("payload", {})).get("summary", {})
        )
        convergence = dict(self._coupled_payload().get("convergence", {}))

        def margin_tone(value: object) -> str:
            number = safe_float(value)
            if number is None:
                return ""
            if number < 1.0:
                return "danger"
            return "success" if number >= 1.25 else "warning"

        stress = structural_summary.get("minimum_stress_margin_ratio")
        material = structural_summary.get("minimum_combined_margin_ratio")
        heat = structural_summary.get("minimum_heat_transfer_margin_ratio")
        feed = convergence.get("minimum_feed_margin_kpa")

        self.margins_grid.set_value(
            "feed_margin",
            "{0} kPa".format(format_number(feed, 2)) if feed is not None else EMPTY,
            "danger" if (safe_float(feed, 1.0) or 1.0) < 0.0 else "",
        )
        self.margins_grid.set_value("stress_margin", "{0} x".format(format_number(stress, 3)), margin_tone(stress))
        self.margins_grid.set_value(
            "material_margin", "{0} x".format(format_number(material, 3)), margin_tone(material)
        )
        self.margins_grid.set_value("heat_margin", "{0} x".format(format_number(heat, 3)), margin_tone(heat))
        self.margins_grid.set_value(
            "pc_residual",
            "{0} kPa".format(format_number(convergence.get("final_residual_kpa"), 3))
            if convergence
            else EMPTY,
        )
        self.margins_grid.set_value(
            "thrust_error",
            "{0}%".format(
                format_number((safe_float(convergence.get("thrust_error_fraction"), 0.0) or 0.0) * 100.0, 3)
            )
            if convergence
            else EMPTY,
        )

    def _render_validation(self) -> None:
        report = self.current_validation_report
        if report is None:
            self.validation_detail.setText("Validation runs with every preview refresh.")
            return
        checks = list(report.checks)
        failed = [check for check in checks if not check.passed]
        set_pill(
            self.validation_pill,
            "{0} of {1} passing".format(len(checks) - len(failed), len(checks)),
            "feasible" if not failed else "warning",
        )
        if not failed:
            self.validation_detail.setText(
                "Every geometry, feed, thermal, and structural check passed for the current inputs."
            )
            return
        self.validation_detail.setText(
            "\n".join(
                "{0}  —  {1}".format(check.check_name.replace("_", " "), check.message)
                for check in failed[:6]
            )
        )

    def _render_data(self) -> None:
        if self.current_design is None:
            return
        self.data_panel.set_rows(
            build_measurement_rows(
                self.current_design,
                self.current_combustion_result,
                self.current_structural_result,
                self._uncertainty_bounds(),
            )
        )

    def _render_report(self) -> None:
        if self.current_design is None:
            return
        self.report_text.setPlainText(
            build_report_text(
                design=self.current_design,
                objective_report=self.current_objective_report,
                combustion_result=self.current_combustion_result,
                solver_interface_result=self.current_solver_interface_result,
                optimizer_result=self.current_ga_result,
                uncertainty_bounds=self._uncertainty_bounds(),
            )
        )

    def _render_plots(self) -> None:
        if not isinstance(self.current_combustion_result, dict) or not self.current_combustion_result:
            self._set_default_plots()
            return
        if not isinstance(self.current_solver_interface_result, dict):
            self._set_default_plots()
            return

        solver_payload = dict(self.current_solver_interface_result.get("payload", {}))
        feed_payload = dict(dict(solver_payload.get("feed_pressure_drop", {})).get("payload", {}))
        coupled_payload = self._coupled_payload()
        summary = self._combustion_summary()
        fallback_thrust = (
            safe_float(
                dict(self.current_design.derived.engineering_values).get("calculated_thrust_newtons"), 0.0
            )
            if self.current_design is not None
            else 0.0
        )
        self.plots_panel.render(
            design=self.current_design,
            combustion_result=self.current_combustion_result,
            feed_summary=dict(feed_payload.get("summary", {})),
            time_history=list(feed_payload.get("time_history_rows", [])),
            axial_profile=list(self.current_combustion_result.get("axial_profile", [])),
            thermal_stations=list(
                dict(self.current_combustion_result.get("heat_transfer", {})).get("axial_stations", [])
            ),
            iteration_trace=list(coupled_payload.get("iteration_trace", []))
            or list(self.current_combustion_result.get("iteration_trace", [])),
            predicted_thrust=safe_float(summary.get("predicted_thrust_newtons"), fallback_thrust) or 0.0,
        )

    def _set_default_plots(self) -> None:
        """Reset every plot to its pre-solve placeholder state."""
        self.plots_panel.set_placeholder()

    def _build_structural_result(self) -> Optional[dict]:
        if self.current_design is None:
            return None
        state = self.collect_form_state()
        material_result = assign_materials({"materials": state}, {})
        return build_structural_materials_output(
            state, {"payload": {}}, material_result, self.current_combustion_result
        )

    # ------------------------------------------------------------ logging -- #

    def _log(self, message: str, code: str = "I-SOLVE-000", level: str = "INFO") -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._solver_log.append("{0} [{1}] {2}: {3}".format(timestamp, code, level, message))
        del self._solver_log[:-_LOG_LIMIT]
        self._refresh_log_view()

    def _log_warnings(self, warnings: List[object]) -> None:
        for index, warning in enumerate(warnings[:12], start=1):
            self._log(str(warning), "W-SOLVE-{0:03d}".format(index), "WARN")

    def _on_log_filter_changed(self, text: str) -> None:
        self._log_filter = text
        self._refresh_log_view()

    def _refresh_log_view(self) -> None:
        wanted = {"Info": "INFO", "Warnings": "WARN", "Errors": "ERROR"}.get(self._log_filter)
        lines = [line for line in self._solver_log if wanted is None or "] {0}:".format(wanted) in line]
        if not lines and self._log_filter == "All":
            lines = ["--:--:-- [I-PREVIEW-000] INFO: Preview ready. Press Solve to run the coupled solver."]
        elif not lines:
            lines = ["No {0} entries.".format(self._log_filter.lower())]
        if self._diagnostic_lines and self._log_filter == "All":
            lines = lines + ["", "# Diagnostic snapshot"] + self._diagnostic_lines
        self.log_text.setPlainText("\n".join(lines))
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _render_diagnostics(self) -> None:
        self._diagnostic_lines = build_diagnostic_lines(
            design=self.current_design,
            combustion_result=self.current_combustion_result,
            solver_interface_result=self.current_solver_interface_result,
            structural_result=self.current_structural_result,
            validation_report=self.current_validation_report,
        )
        self._refresh_log_view()

    def _copy_log(self) -> None:
        QApplication.clipboard().setText(self.log_text.toPlainText())
        self.statusBar().showMessage("Log copied to the clipboard.", 4000)

    def _clear_log(self) -> None:
        self._solver_log = []
        self._refresh_log_view()

    def _copy_report(self) -> None:
        QApplication.clipboard().setText(self.report_text.toPlainText())
        self.statusBar().showMessage("Report copied to the clipboard.", 4000)

    # ------------------------------------------------------------- solving -- #

    def _reset_progress(self, clear_log: bool = False) -> None:
        if clear_log:
            self._solver_log = []
        self.progress_bar.setValue(0)
        self.status_progress.setValue(0)
        self.stage_label.setText("Idle. Press Solve to run the coupled solver.")
        self.residual_label.setText(convergence_summary(None))
        self.step_list.reset()
        self._refresh_log_view()

    def _set_progress(self, progress: float, message: str) -> None:
        bounded = int(max(0.0, min(100.0, progress)))
        self.progress_bar.setValue(bounded)
        self.status_progress.setValue(bounded)
        self.stage_label.setText(message)
        self.statusBar().showMessage(message)
        self._advance_steps(progress, message)
        self._log(message, "I-SOLVE-STEP", "INFO")
        QApplication.processEvents()

    def _advance_steps(self, progress: float, message: str) -> None:
        lower = message.lower()
        if "preparing design" in lower:
            self.step_list.set_step("preview", "done", "Ready")
            self.step_list.set_step("convergence", "active", "Iterating")
        elif "solving feed transient" in lower:
            self.step_list.set_step("feed", "active", "Solving")
        elif "solving chamber and nozzle" in lower:
            self.step_list.set_step("feed", "done", "Solved")
            self.step_list.set_step("chamber_nozzle", "active", "Solving")
        elif "checking structural margins" in lower:
            self.step_list.set_step("chamber_nozzle", "done", "Solved")
            self.step_list.set_step("structure", "active", "Checking")
        elif "residual" in lower:
            self.step_list.set_step("structure", "done", "Checked")
            self.step_list.set_step("convergence", "active", "Checking residual")
        elif "assembling final" in lower:
            for key in ("feed", "chamber_nozzle", "structure"):
                self.step_list.set_step(key, "done", "Solved")
            self.step_list.set_step("convergence", "active", "Assembling")

    def run_solver(self) -> None:
        """Run the optimiser (when enabled) and the coupled cycle solve."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.solve_button.setEnabled(False)
        self.action_solve.setEnabled(False)
        self.status_progress.setVisible(True)
        try:
            self._reset_progress(clear_log=True)
            self._log("Solve requested from the desktop interface.", "I-SOLVE-001", "INFO")
            self.step_list.set_step("preview", "active", "Building")
            self.refresh_preview(status="Solving...")
            self.step_list.set_step("preview", "done", "Ready")
            self.progress_bar.setValue(5)

            passed_validation = (
                self.current_validation_report is not None and self.current_validation_report.passed
            )
            if self.inputs.is_checked("ga_enabled") or not passed_validation:
                self.step_list.set_step("optimization", "active", "Running")
                self.run_design_ga()
                self.step_list.set_step("optimization", "done", "Complete")
            else:
                self.current_ga_result = None
                self.current_ga_candidate_state = None
                self.step_list.set_step("optimization", "skipped", "Skipped")
                self._log(
                    "Optimisation skipped: the preview passed validation and Optimise on solve is off.",
                    "I-SOLVE-002",
                    "INFO",
                )
            self.run_coupled_solve()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user in a dialog
            self._solver_error_count += 1
            code = "E-SOLVE-{0:03d}".format(self._solver_error_count)
            self._log("{0}: {1}".format(code, exc), code, "ERROR")
            for key in self.step_list.keys():
                self.step_list.set_step(key, "failed", "Stopped")
            QMessageBox.critical(
                self, "Solve failed", "StanThrust could not complete the solve.\n\n{0}".format(exc)
            )
        finally:
            self.solve_button.setEnabled(True)
            self.action_solve.setEnabled(True)
            self.status_progress.setVisible(False)
            QApplication.restoreOverrideCursor()

    def run_design_ga(self) -> None:
        """Run the genetic optimiser without overwriting the live inputs."""
        if not self.current_design:
            return
        self._log("Starting feasibility and objective optimisation pass.", "I-GA-001", "INFO")
        QApplication.processEvents()
        seed = build_optimizer_seed(
            self.current_design,
            base_state=self.collect_form_state(),
            objective_weights=self.collect_objective_weights(),
        )
        if self.inputs.is_checked("feasibility_first"):
            self.current_ga_result = run_feasibility_first_optimizer(seed)
        else:
            self.current_ga_result = run_genetic_optimizer(seed)

        multifidelity: Dict[str, object] = {}
        if self.inputs.is_checked("use_multi_fidelity") and self.current_ga_result is not None:
            multifidelity = apply_multifidelity_confirmation(self.current_ga_result)
        self.current_ga_candidate_state = (
            dict(self.current_ga_result.best_state) if self.current_ga_result is not None else None
        )
        if multifidelity.get("screening_applied"):
            message = "Optimisation complete. Screened {0}, confirmed {1}.".format(
                multifidelity.get("candidates_evaluated", 0),
                multifidelity.get("candidates_confirmed", 0),
            )
        else:
            message = "Optimisation complete. Best score: {0:.4f}.".format(
                self.current_ga_result.best_score
            )
        self._log(message, "I-GA-002", "INFO")

    def run_coupled_solve(self) -> None:
        """Run the coupled feed, chamber/nozzle, and structural cycle."""
        if not self.current_design:
            return
        self._set_progress(12, "Coupled solve: initialising feed, chamber/nozzle, and structural loop")

        resolution = self.inputs.solver_resolution()
        state = self.collect_form_state()
        station_count = max(FINAL_SOLVER_STATION_COUNT, int(resolution["station_count"]))
        iteration_limit = max(80, int(resolution["iteration_limit"]))
        convergence_tolerance = min(float(resolution["convergence_tolerance"]), 0.0025)
        state["solver_flow_model"] = "viscous"
        state["solver_station_count"] = station_count
        self._log(
            "Final pass uses the viscous quasi-1D correction with {0} axial stations.".format(station_count),
            "I-SOLVE-010",
            "INFO",
        )

        initial_pressure_kpa = safe_float(
            dict(self.current_design.derived.engineering_values).get("chamber_pressure_kpa"), 1500.0
        )
        self.current_coupled_cycle_result = solve_coupled_cycle(
            state,
            upstream_context={"source": "qt-ui", "stage": "coupled-solve"},
            initial_chamber_pressure_kpa=initial_pressure_kpa,
            initial_design=self.current_design,
            convergence_tolerance_kpa=max(0.5, convergence_tolerance * 1000.0),
            max_iterations=iteration_limit,
            progress_callback=self._set_progress,
        )
        coupled_status = self.current_coupled_cycle_result.get("status", "unknown")
        coupled_payload = dict(self.current_coupled_cycle_result.get("payload", {}))
        if coupled_status == "error":
            self._solver_error_count += 1
            self._log(
                str(coupled_payload.get("error", "The coupled solver returned an error.")),
                "E-SOLVE-{0:03d}".format(self._solver_error_count),
                "ERROR",
            )

        self.current_solver_interface_result = {
            "metadata": {
                "solver_name": "Common Solver Interface",
                "solver_version": "1.0",
                "solver_mode": "coupled-cycle",
            },
            "status": coupled_status,
            "payload": {
                "normalized_request": state,
                "feed_pressure_drop": dict(coupled_payload.get("feed_solver_result", {})),
                "coupled_cycle": self.current_coupled_cycle_result,
            },
            "warnings": list(self.current_coupled_cycle_result.get("warnings", [])),
            "trace": list(self.current_coupled_cycle_result.get("trace", [])),
        }
        self.current_combustion_result = dict(coupled_payload.get("combustion_solver_result", {}))
        structural_result = dict(coupled_payload.get("structural_solver_result", {}))
        self.current_structural_result = structural_result or self._build_structural_result()

        self._render_all()
        self.progress_bar.setValue(100)
        self.status_progress.setValue(100)
        for key in self.step_list.keys():
            self.step_list.set_step(key, "done", "Complete")

        thermo = dict(
            dict(self.current_combustion_result.get("metadata", {})).get("thermochemistry", {})
        )
        self.status_thermo.setText(
            "{0}  ·  thermochemistry {1} ({2})".format(
                FLOW_MODEL_DISPLAY_NAMES["viscous"],
                thermo.get("provider", EMPTY),
                thermo.get("status", EMPTY),
            )
        )
        convergence = dict(coupled_payload.get("convergence", {}))
        self.stage_label.setText(
            "Solve complete. Flow status {0}; coupled status {1}; residual {2} kPa.".format(
                self.current_combustion_result.get("status", "unknown"),
                coupled_status,
                format_number(convergence.get("final_residual_kpa", EMPTY), 3),
            )
        )
        self.statusBar().showMessage("Solve complete.")
        self._log_warnings(list(self.current_coupled_cycle_result.get("warnings", [])))
        self._log("Solve finished with status {0}.".format(coupled_status), "I-SOLVE-999", "INFO")

    # ----------------------------------------------------------- file i/o -- #

    def save_project_dialog(self) -> None:
        """Write the current inputs and optimiser snapshot to a project file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", "stanthrust-project.stanth.json", PROJECT_FILTER
        )
        if not path:
            return
        optimizer_payload = self.current_ga_result.as_dict() if self.current_ga_result else None
        save_project(
            Path(path), self.collect_form_state(), self.collect_objective_weights(), optimizer_payload
        )
        self.statusBar().showMessage("Saved project to {0}".format(path), 6000)

    def load_project_dialog(self) -> None:
        """Load a project file into the inputs and rebuild the preview."""
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", LEGACY_PROJECT_FILTER)
        if not path:
            return
        document = load_project(Path(path))
        state = dict(document.state)
        self.set_full_workspace(str(state.get("ui_mode", "explorer")).lower() == "expert")
        self.inputs.apply_state(state, dict(document.objective_weights))
        self.current_ga_result = None
        self.current_ga_candidate_state = None
        self.current_combustion_result = None
        self.current_solver_interface_result = None
        self.current_coupled_cycle_result = None
        self.refresh_preview(status="Project loaded.")
        if document.ga_result:
            self._log("Loaded project including a stored optimiser snapshot.", "I-IO-001", "INFO")

    def _export_dialog(self, title: str, default_name: str, filters: str) -> Optional[Path]:
        if not self.current_design:
            QMessageBox.warning(self, "Nothing to export", "No design is available yet.")
            return None
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, filters)
        return Path(path) if path else None

    def export_profile_dxf(self) -> None:
        """Export the revolved chamber and nozzle profile as DXF."""
        path = self._export_dialog(
            "Export CAD Profile DXF", "stanthrust-profile.dxf", "DXF Files (*.dxf);;All Files (*)"
        )
        if path is None:
            return
        export_profile_dxf(path, self.current_design)
        self.statusBar().showMessage("Exported profile DXF to {0}".format(path), 6000)

    def export_measurements(self) -> None:
        """Export every solved value as CSV."""
        path = self._export_dialog(
            "Export Measurements CSV", "stanthrust-measurements.csv", "CSV Files (*.csv);;All Files (*)"
        )
        if path is None:
            return
        export_measurements_csv(
            path,
            self.current_design,
            self.current_combustion_result,
            self.current_solver_interface_result,
            self.current_structural_result,
        )
        self.statusBar().showMessage("Exported measurements CSV to {0}".format(path), 6000)

    def export_stations(self) -> None:
        """Export the axial station table as CSV."""
        path = self._export_dialog(
            "Export Station CSV", "stanthrust-stations.csv", "CSV Files (*.csv);;All Files (*)"
        )
        if path is None:
            return
        export_station_csv(
            path,
            self.current_design,
            self.current_combustion_result,
            self.current_solver_interface_result,
            self.current_structural_result,
        )
        self.statusBar().showMessage("Exported station CSV to {0}".format(path), 6000)

    def export_cad_json(self) -> None:
        """Export the full CAD payload, including provenance and uncertainty."""
        path = self._export_dialog(
            "Export CAD JSON", "stanthrust-cad.json", "JSON Files (*.json);;All Files (*)"
        )
        if path is None:
            return
        export_cad_json(
            path,
            self.current_design,
            dict(self.current_objective_report or {}),
            self.current_ga_result.as_dict() if self.current_ga_result else None,
            self.current_combustion_result,
            self.current_solver_interface_result,
            self.current_structural_result,
        )
        self.statusBar().showMessage("Exported CAD JSON to {0}".format(path), 6000)

    # --------------------------------------------------------------- help -- #

    def check_for_update(self) -> None:
        """Look for a newer release and offer to download the installer."""
        self._log(updates.check_for_update(self), "I-UPDATE-001", "INFO")

    def show_about(self) -> None:
        """Show the version and dependency summary."""
        QMessageBox.about(
            self,
            "About StanThrust",
            "<b>StanThrust {0}</b><br><br>"
            "Preliminary liquid-engine sizing with coupled feed, chamber/nozzle, thermal, "
            "and structural solves.<br><br>"
            "Thermochemistry from Cantera. Coolant properties from CoolProp."
            "<br>Plots rendered with Matplotlib.".format(APP_VERSION),
        )


#: Retained name for callers that expect the previous class name.
StanThrustQtWindow = MainWindow


def run() -> None:
    """Create the application, show the main window, and start the event loop."""
    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        import sys

        application = QApplication(sys.argv)
    apply_theme(application)
    window = MainWindow()
    window.show()
    if owns_application:
        application.exec_()
