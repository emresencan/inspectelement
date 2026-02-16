from __future__ import annotations

import logging
from pathlib import Path
import re
import sys

from PySide6.QtCore import QObject, QPoint, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLayout,
    QLayoutItem,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .browser_manager import BrowserManager
from .action_catalog import (
    ACTION_PRESETS,
    CATEGORY_FILTERS,
    ActionSignaturePreview,
    action_label,
    build_signature_previews,
    filter_action_specs,
    has_combo_actions,
    has_table_actions,
    normalize_selected_actions,
    required_parameter_keys,
    return_kind_badge,
)
from .context_wizard import ContextSelection, ContextWizardDialog
from .diff_preview_dialog import DiffPreviewDialog
from .java_pom_writer import (
    JavaPreview,
    apply_java_preview,
    generate_java_preview,
)
from .locator_recommendation import recommend_locator_candidates
from .models import ElementSummary, LocatorCandidate
from .name_suggester import suggest_element_name
from .project_discovery import ModuleInfo, PageClassInfo, discover_page_classes


class EventBridge(QObject):
    capture_received = Signal(object, object)
    status_changed = Signal(str)
    page_changed = Signal(str, str)


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, margin: int = 0, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            item_size = item.sizeHint()
            next_x = x + item_size.width() + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + spacing
                next_x = x + item_size.width() + spacing
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.logger = self._build_logger()
        self.setWindowTitle("inspectelement")
        self._fit_window_to_screen()
        self._set_icon()

        self.bridge = EventBridge()
        self.bridge.capture_received.connect(self._on_capture)
        self.bridge.status_changed.connect(self._set_status)
        self.bridge.page_changed.connect(self._on_page_changed)

        self.browser = BrowserManager(
            on_capture=lambda summary, candidates: self.bridge.capture_received.emit(summary, candidates),
            on_status=lambda message: self.bridge.status_changed.emit(message),
            on_page_info=lambda title, url: self.bridge.page_changed.emit(title, url),
        )
        self.browser.start()

        self.current_summary: ElementSummary | None = None
        self.current_candidates: list[LocatorCandidate] = []
        self.project_root: Path | None = None
        self.selected_module: ModuleInfo | None = None
        self.discovered_pages: list[PageClassInfo] = []
        self.pending_java_preview: JavaPreview | None = None

        self.url_input = QLineEdit("https://example.com")
        self.url_input.setPlaceholderText("https://your-app-url")

        self.launch_button = QPushButton("Launch Browser")
        self.launch_button.clicked.connect(self._launch)

        self.inspect_toggle = QPushButton("Inspect Mode: OFF")
        self.inspect_toggle.setCheckable(True)
        self.inspect_toggle.clicked.connect(self._toggle_inspect)

        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(["Best", "CSS", "XPath", "Playwright", "Selenium"])
        self.output_format_combo.setCurrentText("Best")
        self.output_format_combo.setToolTip("Choose which locator format to copy")

        self.copy_best_button = QPushButton("Copy")
        self.copy_best_button.clicked.connect(self._copy_best)

        self.reset_learning_button = QPushButton("Reset Learning")
        self.reset_learning_button.clicked.connect(self._reset_learning)
        self.clear_overrides_button = QPushButton("Clear Overrides")
        self.clear_overrides_button.clicked.connect(self._clear_overrides)
        self.exit_button = QPushButton("Exit")
        self.exit_button.clicked.connect(self.close)

        self.context_value_label = QLabel("Project: - | Module: -")
        self.context_value_label.setObjectName("Muted")
        self.context_value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.change_context_button = QPushButton("Change")
        self.change_context_button.clicked.connect(self._change_context)

        self.page_combo = QComboBox()
        self.page_combo.addItem("Select page class", None)
        self.page_combo.setEnabled(False)
        self.page_combo.currentIndexChanged.connect(self._update_add_button_state)

        self.runtime_info_label = QLabel(self._runtime_summary())
        self.runtime_info_label.setObjectName("Muted")
        self.runtime_info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.runtime_info_label.setToolTip(f"Python executable: {sys.executable}\nPython version: {sys.version}")

        self.element_name_input = QLineEdit()
        self.element_name_input.setPlaceholderText("Element name (e.g. KURAL_ADI_TXT)")
        self.element_name_input.textChanged.connect(self._on_element_name_changed)

        self.selected_actions: list[str] = []
        self.current_action_category: str = "All"
        self.show_advanced_actions = True
        self.preview_locator_name_override: str | None = None
        self.preview_signatures_override: list[str] | None = None
        self.preview_signatures_actions_snapshot: tuple[str, ...] = ()
        self.pick_table_root_mode = False
        self.auto_table_root_selector_type: str | None = None
        self.auto_table_root_selector_value: str | None = None
        self.auto_table_root_locator_name: str | None = None
        self.manual_table_root_selector_type: str | None = None
        self.manual_table_root_selector_value: str | None = None
        self.manual_table_root_locator_name: str | None = None

        self.action_filter_group = QButtonGroup(self)
        self.action_filter_group.setExclusive(True)
        self.action_filter_buttons: dict[str, QPushButton] = {}
        self.selected_action_flow: FlowLayout | None = None
        self.available_action_specs = []

        self.action_search_input = QLineEdit()
        self.action_search_input.setPlaceholderText("Search action by name or description")
        self.action_search_input.textChanged.connect(self._refresh_action_dropdown)

        self.action_dropdown = QComboBox()
        self.action_dropdown.currentIndexChanged.connect(self._on_action_dropdown_changed)
        self.action_dropdown.activated.connect(self._on_action_dropdown_activated)

        self.action_add_button = QPushButton("+ Add action")
        self.action_add_button.clicked.connect(self._add_selected_dropdown_action)

        self.advanced_actions_checkbox = QCheckBox("Show advanced")
        self.advanced_actions_checkbox.setChecked(True)
        self.advanced_actions_checkbox.toggled.connect(self._on_show_advanced_toggled)

        self.generated_methods_preview = QPlainTextEdit()
        self.generated_methods_preview.setReadOnly(True)
        self.generated_methods_preview.setObjectName("GeneratedPreview")
        self.generated_methods_preview.setMaximumHeight(138)
        self.generated_methods_preview.setPlaceholderText("Select actions to preview generated method signatures.")

        self.table_root_section = QFrame()
        self.table_root_section.setObjectName("TableRootSection")
        self.table_root_locator_preview = QLineEdit()
        self.table_root_locator_preview.setReadOnly(True)
        self.pick_table_root_button = QPushButton("Pick Table Root")
        self.pick_table_root_button.clicked.connect(self._start_table_root_pick_mode)
        self.clear_table_root_button = QPushButton("Clear Override")
        self.clear_table_root_button.clicked.connect(self._clear_manual_table_root)

        self.parameter_panel = QFrame()
        self.parameter_panel.setObjectName("ActionParamsPanel")
        self.param_timeout_input = QLineEdit("10")
        self.param_column_header_input = QLineEdit()
        self.param_expected_text_input = QLineEdit()
        self.param_filter_text_input = QLineEdit()
        self.param_select_id_input = QLineEdit()
        self.param_wait_before_select_checkbox = QCheckBox("waitBeforeSelect")
        self.param_match_type_combo = QComboBox()
        self.param_match_type_combo.addItems(["equals", "contains"])
        self.param_match_column_input = QLineEdit()
        self.param_match_text_input = QLineEdit()
        self.param_inner_locator_input = QLineEdit()
        self.param_widgets: dict[str, QWidget] = {}

        self.action_picker_widget = self._build_action_picker()

        self.log_language_combo = QComboBox()
        self.log_language_combo.addItems(["TR", "EN"])
        self.log_language_combo.setCurrentText("TR")
        self.log_language_combo.currentTextChanged.connect(self._on_action_selection_changed)

        self.add_button = QPushButton("Add")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._prepare_add_request)

        self.payload_status_label = QLabel("Waiting for page, locator, and element name.")
        self.payload_status_label.setObjectName("Muted")
        self.payload_status_label.setWordWrap(True)

        self.results_table = QTableWidget(0, 5)
        self.results_table.setHorizontalHeaderLabels(["Rank", "Type", "Locator", "Score", "Guidance"])
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.itemSelectionChanged.connect(self._on_selection_changed)
        self.results_table.setShowGrid(True)
        self.results_table.setGridStyle(Qt.PenStyle.SolidLine)
        self.results_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_table.horizontalHeader().setStretchLastSection(False)
        self.results_table.horizontalHeader().setSectionResizeMode(0, self.results_table.horizontalHeader().ResizeMode.Fixed)
        self.results_table.horizontalHeader().setSectionResizeMode(1, self.results_table.horizontalHeader().ResizeMode.Fixed)
        self.results_table.horizontalHeader().setSectionResizeMode(2, self.results_table.horizontalHeader().ResizeMode.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(3, self.results_table.horizontalHeader().ResizeMode.Fixed)
        self.results_table.horizontalHeader().setSectionResizeMode(4, self.results_table.horizontalHeader().ResizeMode.Fixed)
        self.results_table.setColumnWidth(0, 70)
        self.results_table.setColumnWidth(1, 110)
        self.results_table.setColumnWidth(3, 96)
        self.results_table.setColumnWidth(4, 120)
        self.results_table.verticalHeader().setDefaultSectionSize(36)
        self.results_table.setMinimumHeight(225)
        self.results_table.setMaximumHeight(245)

        self.detail_labels: dict[str, QLabel] = {}
        detail_form = QFormLayout()
        detail_form.setContentsMargins(0, 0, 0, 0)
        detail_form.setHorizontalSpacing(12)
        detail_form.setVerticalSpacing(4)
        for key in ["tag", "id", "classes", "name", "role", "text", "placeholder", "aria-label"]:
            label = QLabel("-")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            detail_form.addRow(f"{key}:", label)
            self.detail_labels[key] = label

        self.breakdown_text = QPlainTextEdit()
        self.breakdown_text.setReadOnly(True)
        self.breakdown_text.setPlaceholderText("Select a locator row to inspect score details")
        self.breakdown_text.setMaximumHeight(120)

        self.good_button = QPushButton("Good")
        self.good_button.clicked.connect(lambda: self._feedback(True))
        self.bad_button = QPushButton("Bad")
        self.bad_button.clicked.connect(lambda: self._feedback(False))
        self.good_edited_button = QPushButton("Good (edited)")
        self.good_edited_button.clicked.connect(self._good_edited)
        self.apply_edit_button = QPushButton("Apply edit")
        self.apply_edit_button.clicked.connect(self._apply_edit)
        self.copy_edited_button = QPushButton("Copy edited")
        self.copy_edited_button.clicked.connect(self._copy_edited)

        self.locator_editor = QPlainTextEdit()
        self.locator_editor.setPlaceholderText("Select a locator row, edit it here, then apply/copy/save.")
        self.locator_editor.setObjectName("Editor")
        self.locator_editor.setMaximumHeight(96)

        feedback_row = QHBoxLayout()
        feedback_row.addWidget(self.good_button)
        feedback_row.addWidget(self.bad_button)

        editor_actions_row = QHBoxLayout()
        editor_actions_row.addWidget(self.apply_edit_button)
        editor_actions_row.addWidget(self.copy_edited_button)
        editor_actions_row.addWidget(self.good_edited_button)

        header_card = QFrame()
        header_card.setObjectName("Card")
        header_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_layout = QVBoxLayout(header_card)

        title_label = QLabel("Inspect Element for Automation")
        title_label.setObjectName("Title")
        subtitle_label = QLabel("Open a URL, enable inspect mode, click an element, and copy the best locator.")
        subtitle_label.setObjectName("Muted")

        quick_start = QLabel(
            """
            <b>Quick Start</b><br/>
            1. Enter URL and click <b>Launch Browser</b>.<br/>
            2. Click <b>Inspect Mode: OFF</b> to turn it ON.<br/>
            3. Click any element in the browser page.<br/>
            4. Pick a locator row or use <b>Copy</b> with selected format.
            """
        )
        quick_start.setObjectName("Help")
        quick_start.setWordWrap(True)
        quick_start.setMaximumHeight(100)

        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        header_layout.addWidget(quick_start)

        controls_card = QFrame()
        controls_card.setObjectName("Card")
        controls_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        controls_layout = QGridLayout(controls_card)
        controls_layout.setColumnStretch(1, 1)

        url_label = QLabel("Target URL")
        url_label.setObjectName("FieldLabel")
        format_label = QLabel("Copy Format")
        format_label.setObjectName("FieldLabel")
        context_label = QLabel("Context")
        context_label.setObjectName("FieldLabel")
        page_label = QLabel("Page Class")
        page_label.setObjectName("FieldLabel")

        controls_layout.addWidget(url_label, 0, 0)
        controls_layout.addWidget(self.url_input, 0, 1, 1, 5)
        controls_layout.addWidget(self.launch_button, 1, 0)
        controls_layout.addWidget(self.inspect_toggle, 1, 1)
        controls_layout.addWidget(format_label, 1, 2)
        controls_layout.addWidget(self.output_format_combo, 1, 3)
        controls_layout.addWidget(self.copy_best_button, 1, 4)
        controls_layout.addWidget(self.reset_learning_button, 1, 5)
        controls_layout.addWidget(self.clear_overrides_button, 1, 6)
        controls_layout.addWidget(self.exit_button, 1, 7)
        controls_layout.addWidget(context_label, 2, 0)
        controls_layout.addWidget(self.context_value_label, 2, 1, 1, 6)
        controls_layout.addWidget(self.change_context_button, 2, 7)
        controls_layout.addWidget(page_label, 3, 0)
        controls_layout.addWidget(self.page_combo, 3, 1, 1, 3)
        controls_layout.addWidget(self.runtime_info_label, 4, 0, 1, 8)

        left_card = QFrame()
        left_card.setObjectName("Card")
        left_card_layout = QVBoxLayout(left_card)
        left_card_layout.setContentsMargins(0, 0, 0, 0)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_content = QWidget()
        left_col = QVBoxLayout(left_content)
        left_title = QLabel("Locator Suggestions")
        left_title.setObjectName("SectionTitle")
        left_hint = QLabel("After clicking an element in Inspect Mode, top 5 locator candidates appear below.")
        left_hint.setObjectName("Muted")
        left_col.addWidget(left_title)
        left_col.addWidget(left_hint)
        left_col.addWidget(self.results_table)
        left_col.addWidget(QLabel("Locator Editor:"))
        left_col.addWidget(self.locator_editor)
        left_col.addLayout(editor_actions_row)
        left_col.addWidget(QLabel("Element Name:"))
        left_col.addWidget(self.element_name_input)
        left_col.addWidget(QLabel("Action Picker:"))
        left_col.addWidget(self.action_picker_widget)
        left_col.addWidget(self._build_table_root_section())
        left_col.addWidget(self._build_parameter_panel())
        left_col.addWidget(QLabel("Generated Methods Preview:"))
        left_col.addWidget(self.generated_methods_preview)

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Log:"))
        action_row.addWidget(self.log_language_combo)
        action_row.addStretch(1)
        action_row.addWidget(self.add_button)
        left_col.addLayout(action_row)
        left_col.addWidget(self.payload_status_label)
        left_col.addStretch(1)
        left_scroll.setWidget(left_content)
        left_card_layout.addWidget(left_scroll)

        details_card = QFrame()
        details_card.setObjectName("Card")
        right_col = QVBoxLayout(details_card)
        details_title = QLabel("Clicked Element Details")
        details_title.setObjectName("SectionTitle")
        right_col.addWidget(details_title)
        right_col.addLayout(detail_form)
        right_col.addWidget(QLabel("Score breakdown:"))
        right_col.addWidget(self.breakdown_text)
        right_col.addLayout(feedback_row)
        right_col.addStretch(1)

        content_layout = QHBoxLayout()
        content_layout.addWidget(left_card, 3)
        content_layout.addWidget(details_card, 2)

        self.status_label = QLabel("Ready. Step 1: enter URL and launch browser.")
        self.status_label.setObjectName("Status")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)
        root_layout.addWidget(header_card)
        root_layout.addWidget(controls_card)
        root_layout.addLayout(content_layout)
        root_layout.addWidget(self.status_label)

        self.setCentralWidget(root)

        self.toast_label = QLabel("", self)
        self.toast_label.setObjectName("Toast")
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)

        self._apply_style()
        self._refresh_table_root_section()
        self._refresh_parameter_panel()
        self._update_generated_methods_preview()
        if not self._ensure_context_selected(initial=True):
            QTimer.singleShot(0, self.close)

    def _fit_window_to_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if not screen:
            self.resize(1200, 700)
            return

        available = screen.availableGeometry()
        target_width = max(980, min(1320, available.width() - 48))
        target_height = max(600, min(720, available.height() - 72))
        target_width = min(target_width, available.width() - 16)
        target_height = min(target_height, available.height() - 16)
        x = available.x() + max(8, (available.width() - target_width) // 2)
        y = available.y() + max(8, (available.height() - target_height) // 2)
        self.setGeometry(x, y, target_width, target_height)

    def _set_icon(self) -> None:
        icon_path = Path(__file__).resolve().parents[2] / "assets" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                color: #0f172a;
                font-family: "SF Pro Text", "Segoe UI", "Helvetica Neue", sans-serif;
                font-size: 13px;
            }
            QMainWindow {
                background: #f3f5f9;
            }
            QFrame#Card {
                background: #ffffff;
                border: 1px solid #d7dfed;
                border-radius: 12px;
            }
            QLabel#Title {
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#SectionTitle {
                font-size: 15px;
                font-weight: 600;
            }
            QLabel#Muted {
                color: #475569;
            }
            QLabel#Help {
                color: #334155;
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                padding: 10px;
            }
            QLabel#FieldLabel {
                color: #334155;
                font-weight: 600;
            }
            QLabel#Status {
                color: #0f172a;
                background: #e2e8f0;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 8px 10px;
            }
            QLabel#Toast {
                color: #ffffff;
                background: rgba(15, 23, 42, 0.92);
                border: 1px solid #1e293b;
                border-radius: 10px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton {
                border: 1px solid #c4cad8;
                border-radius: 8px;
                padding: 7px 12px;
                background: #ffffff;
                color: #0f172a;
            }
            QPushButton#TableCopyButton {
                padding: 3px 8px;
                min-height: 26px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f1f5f9;
            }
            QPushButton:checked {
                background: #0284c7;
                border-color: #0369a1;
                color: #ffffff;
            }
            QLineEdit, QPlainTextEdit, QTableWidget, QComboBox {
                background: #ffffff;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QTableWidget {
                gridline-color: #94a3b8;
                selection-background-color: #dbeafe;
                selection-color: #0f172a;
            }
            QTableWidget::item:selected {
                background: #dbeafe;
                color: #0f172a;
            }
            QTableWidget::item:selected:active {
                background: #bfdbfe;
                color: #0f172a;
            }
            QTableWidget::item:selected:!active {
                background: #dbeafe;
                color: #0f172a;
            }
            QWidget#LocatorCell {
                background: transparent;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QComboBox {
                padding-right: 24px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border-left: 1px solid #cbd5e1;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #0f172a;
                border: 1px solid #94a3b8;
                selection-background-color: #0284c7;
                selection-color: #ffffff;
                outline: 0px;
            }
            QComboBox QAbstractItemView::item {
                min-height: 24px;
                color: #0f172a;
                background: #ffffff;
                padding: 4px 8px;
            }
            QComboBox QAbstractItemView::item:selected {
                color: #ffffff;
                background: #0284c7;
            }
            QHeaderView::section {
                background: #e2e8f0;
                color: #334155;
                border: none;
                border-right: 1px solid #cbd5e1;
                padding: 6px;
                font-weight: 600;
            }
            QFrame#ActionPickerCard {
                border: 1px solid #d5deec;
                border-radius: 10px;
                background: #f8fafc;
            }
            QFrame#ActionChipTray {
                border: 1px solid #d7dfed;
                border-radius: 8px;
                background: #ffffff;
            }
            QPushButton#ActionChip {
                border: 1px solid #bfd6ec;
                border-radius: 12px;
                padding: 2px 8px;
                font-size: 12px;
                background: #eef6ff;
            }
            QPushButton#ActionChip:hover {
                background: #dbeafe;
            }
            QPushButton#FilterChip {
                border: 1px solid #cad6e5;
                border-radius: 10px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QPushButton#FilterChip:checked {
                background: #0ea5e9;
                color: #ffffff;
                border-color: #0284c7;
            }
            QPushButton#PresetChip {
                border: 1px solid #cad6e5;
                border-radius: 10px;
                padding: 3px 8px;
                font-size: 11px;
                background: #ffffff;
            }
            QPushButton#PresetChip:hover {
                background: #eef2ff;
            }
            QPlainTextEdit#GeneratedPreview {
                font-family: "Menlo", "Consolas", monospace;
                font-size: 12px;
            }
            QFrame#TableRootSection, QFrame#ActionParamsPanel {
                border: 1px solid #d7dfed;
                border-radius: 8px;
                background: #ffffff;
            }
            """
        )

    @staticmethod
    def _runtime_summary() -> str:
        version = sys.version.split()[0]
        return f"Runtime: {sys.executable} (Python {version})"

    def _build_action_picker(self) -> QWidget:
        container = QFrame()
        container.setObjectName("ActionPickerCard")
        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        selected_row = QHBoxLayout()
        selected_row.setContentsMargins(0, 0, 0, 0)
        selected_row.addWidget(QLabel("Selected Actions:"))
        selected_row.addStretch(1)
        root_layout.addLayout(selected_row)

        chip_tray = QFrame()
        chip_tray.setObjectName("ActionChipTray")
        chip_tray_layout = QVBoxLayout(chip_tray)
        chip_tray_layout.setContentsMargins(8, 8, 8, 8)
        chip_tray_layout.setSpacing(0)
        chips_host = QWidget()
        self.selected_action_flow = FlowLayout(chips_host, margin=0, spacing=6)
        chips_host.setLayout(self.selected_action_flow)
        chip_tray_layout.addWidget(chips_host)
        root_layout.addWidget(chip_tray)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(6)
        add_row.addWidget(QLabel("+ Add action"))
        add_row.addWidget(self.action_search_input, 1)
        add_row.addWidget(self.action_dropdown, 2)
        add_row.addWidget(self.action_add_button)
        root_layout.addLayout(add_row)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(4)
        filter_row.addWidget(QLabel("Filter:"))
        for category in CATEGORY_FILTERS:
            filter_button = QPushButton(category)
            filter_button.setObjectName("FilterChip")
            filter_button.setCheckable(True)
            filter_button.clicked.connect(lambda _checked=False, value=category: self._set_category_filter(value))
            self.action_filter_group.addButton(filter_button)
            self.action_filter_buttons[category] = filter_button
            filter_row.addWidget(filter_button)
        filter_row.addSpacing(8)
        filter_row.addWidget(self.advanced_actions_checkbox)
        filter_row.addStretch(1)
        root_layout.addLayout(filter_row)

        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(4)
        preset_row.addWidget(QLabel("Presets:"))
        for preset_name in ("Common UI", "Read", "JS", "Table Common", "ComboBox", "Clear"):
            preset_button = QPushButton(preset_name)
            preset_button.setObjectName("PresetChip")
            preset_button.clicked.connect(lambda _checked=False, value=preset_name: self._apply_action_preset(value))
            preset_row.addWidget(preset_button)
        preset_row.addStretch(1)
        root_layout.addLayout(preset_row)

        self.action_filter_buttons["All"].setChecked(True)
        self._refresh_action_dropdown()
        self._render_selected_action_chips()

        return container

    def _build_table_root_section(self) -> QWidget:
        layout = QVBoxLayout(self.table_root_section)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        title = QLabel("Table Root")
        title.setObjectName("FieldLabel")
        hint = QLabel("Auto-detected from ancestry when table actions are selected.")
        hint.setObjectName("Muted")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.table_root_locator_preview, 1)
        row.addWidget(self.pick_table_root_button)
        row.addWidget(self.clear_table_root_button)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(row)
        self.table_root_section.setVisible(False)
        return self.table_root_section

    def _build_parameter_panel(self) -> QWidget:
        self.parameter_form_layout = QFormLayout(self.parameter_panel)
        layout = self.parameter_form_layout
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        layout.addRow(QLabel("Action Parameters"), QLabel(""))

        self.param_widgets = {
            "timeoutSec": self.param_timeout_input,
            "columnHeader": self.param_column_header_input,
            "expectedText": self.param_expected_text_input,
            "filterText": self.param_filter_text_input,
            "selectId": self.param_select_id_input,
            "waitBeforeSelect": self.param_wait_before_select_checkbox,
            "matchType": self.param_match_type_combo,
            "matchColumnHeader": self.param_match_column_input,
            "matchText": self.param_match_text_input,
            "innerLocator": self.param_inner_locator_input,
        }

        layout.addRow("timeoutSec", self.param_timeout_input)
        layout.addRow("columnHeader", self.param_column_header_input)
        layout.addRow("expectedText", self.param_expected_text_input)
        layout.addRow("filterText", self.param_filter_text_input)
        layout.addRow("selectId", self.param_select_id_input)
        layout.addRow("waitBeforeSelect", self.param_wait_before_select_checkbox)
        layout.addRow("matchType", self.param_match_type_combo)
        layout.addRow("matchColumnHeader", self.param_match_column_input)
        layout.addRow("matchText", self.param_match_text_input)
        layout.addRow("innerLocator (By expression)", self.param_inner_locator_input)

        for widget in self.param_widgets.values():
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._on_action_selection_changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._on_action_selection_changed)
            elif isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self._on_action_selection_changed)

        self.parameter_panel.setVisible(False)
        return self.parameter_panel

    def _on_show_advanced_toggled(self, enabled: bool) -> None:
        self.show_advanced_actions = bool(enabled)
        self._refresh_action_dropdown()

    def _set_category_filter(self, category: str) -> None:
        if category not in CATEGORY_FILTERS:
            category = "All"
        self.current_action_category = category
        for key, button in self.action_filter_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == category)
            button.blockSignals(False)
        self._refresh_action_dropdown()

    def _refresh_action_dropdown(self) -> None:
        search_text = self.action_search_input.text()
        filtered = filter_action_specs(
            search_text=search_text,
            category=self.current_action_category,
            selected_actions=self.selected_actions,
            include_advanced=self.show_advanced_actions,
        )
        self.available_action_specs = filtered

        previous_key = self.action_dropdown.currentData()
        self.action_dropdown.blockSignals(True)
        self.action_dropdown.clear()
        if not filtered:
            self.action_dropdown.addItem("No actions found", None)
            self.action_add_button.setEnabled(False)
            self.action_dropdown.blockSignals(False)
            return

        selected_index = 0
        for index, spec in enumerate(filtered):
            item_text = f"{spec.label} ({spec.category}) - {spec.description}"
            self.action_dropdown.addItem(item_text, spec.key)
            if spec.key == previous_key:
                selected_index = index

        self.action_dropdown.setCurrentIndex(selected_index)
        self.action_dropdown.blockSignals(False)
        self.action_add_button.setEnabled(True)

    def _on_action_dropdown_changed(self, _index: int) -> None:
        self.action_add_button.setEnabled(self.action_dropdown.currentData() is not None)

    def _on_action_dropdown_activated(self, _index: int) -> None:
        self._add_selected_dropdown_action()

    def _add_selected_dropdown_action(self) -> None:
        action_key = self.action_dropdown.currentData()
        if not isinstance(action_key, str):
            return
        self._add_action(action_key)
        self.action_search_input.clear()
        self._refresh_action_dropdown()

    def _add_action(self, action_key: str) -> None:
        if action_key in self.selected_actions:
            self._show_toast("Already selected")
            return

        updated = self.selected_actions + [action_key]
        self._set_selected_actions(updated)

    def _remove_action(self, action_key: str) -> None:
        updated = [key for key in self.selected_actions if key != action_key]
        self._set_selected_actions(updated)

    def _set_selected_actions(self, actions: list[str]) -> None:
        normalized = normalize_selected_actions(actions)
        if normalized == self.selected_actions:
            return

        self.selected_actions = normalized
        self._reset_generated_preview_override()
        self._render_selected_action_chips()
        self._refresh_action_dropdown()
        self._refresh_table_root_section()
        self._refresh_parameter_panel()
        self._on_action_selection_changed()

    def _clear_selected_action_chips(self) -> None:
        if not self.selected_action_flow:
            return
        while self.selected_action_flow.count():
            item = self.selected_action_flow.takeAt(0)
            if not item:
                continue
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def _render_selected_action_chips(self) -> None:
        self._clear_selected_action_chips()
        if not self.selected_action_flow:
            return

        if not self.selected_actions:
            placeholder = QLabel("No actions selected")
            placeholder.setObjectName("Muted")
            self.selected_action_flow.addWidget(placeholder)
            return

        for action_key in self.selected_actions:
            chip_button = QPushButton(f"{action_label(action_key)}  x")
            chip_button.setObjectName("ActionChip")
            chip_button.clicked.connect(lambda _checked=False, key=action_key: self._remove_action(key))
            self.selected_action_flow.addWidget(chip_button)

    def _apply_action_preset(self, preset_name: str) -> None:
        if preset_name == "Clear":
            self._set_selected_actions([])
            return

        preset_actions = ACTION_PRESETS.get(preset_name)
        if not preset_actions:
            return
        self._set_selected_actions(list(preset_actions))

    def _refresh_table_root_section(self) -> None:
        needs_table = has_table_actions(self.selected_actions)
        self.table_root_section.setVisible(needs_table)
        if not needs_table:
            return

        selector = self._selected_table_root_selector()
        if selector:
            selector_type, selector_value = selector
            self.table_root_locator_preview.setText(f"{selector_type}: {selector_value}")
        else:
            self.table_root_locator_preview.setText("Table root could not be detected.")

    def _refresh_parameter_panel(self) -> None:
        required = set(required_parameter_keys(self.selected_actions))
        self.parameter_panel.setVisible(bool(required))
        for key, widget in self.param_widgets.items():
            widget.setVisible(key in required)
            label = self.parameter_form_layout.labelForField(widget)
            if label:
                label.setVisible(key in required)

        if "selectId" in required:
            candidate = self._selected_candidate()
            resolved = self._resolve_java_selector(candidate) if candidate else None
            if resolved and resolved[0] == "id":
                self.param_select_id_input.setText(resolved[1])
                self.param_select_id_input.setEnabled(False)
            else:
                self.param_select_id_input.setEnabled(True)
        else:
            self.param_select_id_input.setEnabled(True)

    def _collect_action_parameters(self) -> dict[str, str]:
        parameters: dict[str, str] = {
            "timeoutSec": self.param_timeout_input.text().strip() or "10",
            "columnHeader": self.param_column_header_input.text().strip(),
            "expectedText": self.param_expected_text_input.text().strip(),
            "filterText": self.param_filter_text_input.text().strip(),
            "selectId": self.param_select_id_input.text().strip(),
            "waitBeforeSelect": "true" if self.param_wait_before_select_checkbox.isChecked() else "false",
            "matchType": self.param_match_type_combo.currentText().strip() or "equals",
            "matchColumnHeader": self.param_match_column_input.text().strip(),
            "matchText": self.param_match_text_input.text().strip(),
            "innerLocator": self.param_inner_locator_input.text().strip(),
        }
        return parameters

    def _selected_table_root_selector(self) -> tuple[str, str] | None:
        if self.manual_table_root_selector_type and self.manual_table_root_selector_value:
            return self.manual_table_root_selector_type, self.manual_table_root_selector_value
        if self.auto_table_root_selector_type and self.auto_table_root_selector_value:
            return self.auto_table_root_selector_type, self.auto_table_root_selector_value
        return None

    def _selected_table_root_locator_name(self) -> str:
        if self.manual_table_root_locator_name:
            return self.manual_table_root_locator_name
        if self.auto_table_root_locator_name:
            return self.auto_table_root_locator_name
        base_name = self.element_name_input.text().strip().upper() or "TABLE"
        if not base_name.endswith("_TABLE"):
            base_name = f"{base_name}_TABLE"
        return re.sub(r"[^A-Z0-9_]+", "_", base_name)

    def _set_auto_table_root_from_summary(self, summary: ElementSummary | None) -> None:
        if not summary or not summary.table_root:
            self.auto_table_root_selector_type = None
            self.auto_table_root_selector_value = None
            self.auto_table_root_locator_name = None
            self._refresh_table_root_section()
            return

        selector_type = summary.table_root.get("selector_type")
        selector_value = summary.table_root.get("selector_value")
        locator_name_hint = summary.table_root.get("locator_name_hint")
        if selector_type and selector_value:
            self.auto_table_root_selector_type = selector_type
            self.auto_table_root_selector_value = selector_value
            self.auto_table_root_locator_name = locator_name_hint or self._selected_table_root_locator_name()
        else:
            self.auto_table_root_selector_type = None
            self.auto_table_root_selector_value = None
            self.auto_table_root_locator_name = None
        self._refresh_table_root_section()

    def _set_manual_table_root(self, selector_type: str, selector_value: str, locator_name: str | None = None) -> None:
        self.manual_table_root_selector_type = selector_type
        self.manual_table_root_selector_value = selector_value
        if locator_name:
            self.manual_table_root_locator_name = locator_name
        else:
            self.manual_table_root_locator_name = self._selected_table_root_locator_name()
        self._refresh_table_root_section()

    def _clear_manual_table_root(self) -> None:
        self.manual_table_root_selector_type = None
        self.manual_table_root_selector_value = None
        self.manual_table_root_locator_name = None
        self.pick_table_root_mode = False
        self._set_status("Manual table root override cleared.")
        self._refresh_table_root_section()
        self._update_generated_methods_preview()

    def _start_table_root_pick_mode(self) -> None:
        self.pick_table_root_mode = True
        self._set_status("Pick Table Root mode is ON. Click table root container in browser.")
        self._show_toast("Table root pick mode ON")

    def _reset_generated_preview_override(self) -> None:
        self.preview_locator_name_override = None
        self.preview_signatures_override = None
        self.preview_signatures_actions_snapshot = ()

    def _on_element_name_changed(self, _value: str) -> None:
        self._reset_generated_preview_override()
        self._update_add_button_state()

    def _update_generated_methods_preview(self) -> None:
        actions = self._selected_actions()
        if not actions:
            self.generated_methods_preview.setPlainText("No actions selected.")
            return

        page = self._selected_page_class()
        page_class_name = page.class_name if page else "PageClass"
        base_locator_name = self.element_name_input.text().strip() or "ELEMENT"
        locator_name = self.preview_locator_name_override or base_locator_name
        table_locator_name = self._selected_table_root_locator_name() if has_table_actions(actions) else None
        action_parameters = self._collect_action_parameters()

        lines: list[str] = []
        use_override = (
            self.preview_locator_name_override is not None
            and self.preview_signatures_override is not None
            and tuple(actions) == self.preview_signatures_actions_snapshot
        )
        if use_override:
            for action_key, signature in zip(actions, self.preview_signatures_override):
                preview_kind = "fluent"
                previews = build_signature_previews(
                    page_class_name,
                    locator_name,
                    [action_key],
                    table_locator_name=table_locator_name,
                    action_parameters=action_parameters,
                )
                if previews:
                    preview_kind = previews[0].return_kind
                lines.append(f"[{return_kind_badge(preview_kind)}] {signature}")
        else:
            previews: list[ActionSignaturePreview] = build_signature_previews(
                page_class_name=page_class_name,
                locator_name=locator_name,
                selected_actions=actions,
                table_locator_name=table_locator_name,
                action_parameters=action_parameters,
            )
            for preview in previews:
                lines.append(f"[{return_kind_badge(preview.return_kind)}] {preview.signature}")

        if self.preview_locator_name_override:
            lines.insert(0, f"Locator constant: {self.preview_locator_name_override}")
        if has_table_actions(actions):
            table_selector = self._selected_table_root_selector()
            if table_selector:
                lines.insert(0, f"Table root: {table_selector[0]}={table_selector[1]}")
            else:
                lines.insert(0, "Table root: not detected")
        if has_combo_actions(actions):
            lines.append(f"selectId={action_parameters.get('selectId', '') or '-'}")
            lines.append(f"waitBeforeSelect={action_parameters.get('waitBeforeSelect', 'false')}")

        self.generated_methods_preview.setPlainText("\n".join(lines) if lines else "No preview available.")

    @staticmethod
    def _build_logger() -> logging.Logger:
        logger = logging.getLogger("inspectelement.ui")
        if logger.handlers:
            return logger

        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            log_dir = Path.home() / ".inspectelement"
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "ui.log", encoding="utf-8")
            file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(file_handler)
        except Exception:
            # Fallback to stderr logging if file logger cannot be initialized.
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(stream_handler)
        return logger

    def _handle_ui_exception(self, user_message: str, exc: Exception) -> None:
        self.logger.exception("%s: %s", user_message, exc)
        self._set_status(user_message)
        self.payload_status_label.setText(user_message)
        self._show_toast(user_message)

    def _change_context(self) -> None:
        if self._ensure_context_selected(initial=False):
            return
        self._set_status("Context change canceled.")

    def _ensure_context_selected(self, initial: bool) -> bool:
        wizard = ContextWizardDialog(
            self,
            initial_project_root=self.project_root,
            initial_module_name=self.selected_module.name if self.selected_module else None,
        )
        if wizard.exec() != QDialog.DialogCode.Accepted:
            if initial:
                self._set_status("Context selection canceled. Reopen app to continue.")
            return False

        selection = wizard.selected_context
        if not selection:
            if initial:
                self._set_status("Context selection did not return a valid value.")
            return False

        self._apply_context(selection)
        return True

    def _apply_context(self, selection: ContextSelection) -> None:
        self.project_root = selection.project_root
        self.selected_module = selection.module
        self.manual_table_root_selector_type = None
        self.manual_table_root_selector_value = None
        self.manual_table_root_locator_name = None
        self.auto_table_root_selector_type = None
        self.auto_table_root_selector_value = None
        self.auto_table_root_locator_name = None
        self._refresh_page_classes()

        root_text = str(selection.project_root)
        display_root = root_text if len(root_text) <= 56 else f"...{root_text[-53:]}"
        self.context_value_label.setText(f"Project: {display_root} | Module: {selection.module.name}")
        self.context_value_label.setToolTip(f"Project: {selection.project_root}\nModule: {selection.module.name}")

        if self.discovered_pages:
            self._set_status(
                f"Context loaded: {selection.module.name} ({len(self.discovered_pages)} page class(es) found)."
            )
            return
        self._set_status(f"Context loaded: {selection.module.name}. No page classes found.")

    def _refresh_page_classes(self) -> None:
        self._reset_generated_preview_override()
        self.page_combo.clear()
        self.page_combo.addItem("Select page class", None)

        if not self.selected_module:
            self.discovered_pages = []
            self.page_combo.setEnabled(False)
            self._update_add_button_state()
            return

        pages = discover_page_classes(self.selected_module)
        self.discovered_pages = pages
        for page in pages:
            self.page_combo.addItem(page.class_name, page)

        has_pages = bool(pages)
        self.page_combo.setEnabled(has_pages)
        if has_pages:
            self.page_combo.setCurrentIndex(1)
        self._update_add_button_state()

    def _selected_page_class(self) -> PageClassInfo | None:
        selected = self.page_combo.currentData()
        if isinstance(selected, PageClassInfo):
            return selected
        return None

    def _update_add_button_state(self) -> None:
        self._update_generated_methods_preview()
        has_page = self._selected_page_class() is not None
        has_locator = self._selected_candidate() is not None
        has_name = bool(self.element_name_input.text().strip())
        self.add_button.setEnabled(has_page and has_locator and has_name)
        if not (has_page and has_locator and has_name):
            self.payload_status_label.setText("Waiting for page, locator, and element name.")
        else:
            self._refresh_payload_status("Payload ready. Click Add to prepare output.")

    def _selected_actions(self) -> list[str]:
        return list(self.selected_actions)

    def _on_action_selection_changed(self) -> None:
        self._refresh_table_root_section()
        self._refresh_parameter_panel()
        self._update_generated_methods_preview()
        self._refresh_payload_status()

    def _refresh_payload_status(self, prefix: str = "Payload preview") -> None:
        page = self._selected_page_class()
        candidate = self._selected_candidate()
        name = self.element_name_input.text().strip()
        if not page or not candidate or not name:
            self.payload_status_label.setText("Waiting for page, locator, and element name.")
            return

        actions = self._selected_actions()
        action_text = ", ".join(action_label(action) for action in actions) if actions else "none"
        log_language = self.log_language_combo.currentText()
        self.payload_status_label.setText(
            f"{prefix}: {page.class_name} | {name} | {candidate.locator_type} | "
            f"actions={action_text} | log={log_language}"
        )

    def _prepare_add_request(self) -> None:
        try:
            page = self._selected_page_class()
            candidate = self._selected_candidate()
            element_name = self.element_name_input.text().strip()
            if not page or not candidate or not element_name:
                self._set_status("Select page, locator, and element name before Add.")
                self._show_toast("Add icin zorunlu alanlar eksik")
                self._update_add_button_state()
                return

            actions = self._selected_actions()
            action_parameters = self._collect_action_parameters()
            if has_table_actions(actions):
                table_root_selector = self._selected_table_root_selector()
                if not table_root_selector:
                    message = "Table root could not be detected. Please pick the table root container."
                    self._set_status(message)
                    self.payload_status_label.setText(message)
                    self._show_toast(message)
                    return
            if "selectBySelectIdAuto" in actions:
                if not action_parameters.get("selectId", "").strip():
                    message = "Select Id is required for selectBySelectIdAuto."
                    self._set_status(message)
                    self.payload_status_label.setText(message)
                    self._show_toast(message)
                    return
            selector_details = self._resolve_java_selector(candidate)
            if not selector_details:
                self._set_status("Selected locator type cannot be written to Java. Choose CSS/XPath/Selenium.")
                self.payload_status_label.setText("Selected locator type cannot be written to Java.")
                self._show_toast("Desteklenmeyen locator tipi")
                return

            selector_type, selector_value = selector_details
            selected_table_root = self._selected_table_root_selector()
            preview = generate_java_preview(
                target_file=page.file_path,
                locator_name=element_name,
                selector_type=selector_type,
                selector_value=selector_value,
                actions=actions,
                log_language=self.log_language_combo.currentText(),
                action_parameters=action_parameters,
                table_root_selector_type=selected_table_root[0] if selected_table_root else None,
                table_root_selector_value=selected_table_root[1] if selected_table_root else None,
                table_root_locator_name=self._selected_table_root_locator_name() if selected_table_root else None,
            )
            if not preview.ok:
                self.pending_java_preview = None
                self._set_status(preview.message)
                self.payload_status_label.setText(preview.message)
                self._show_toast(preview.message)
                return

            self.pending_java_preview = preview
            self._set_status(preview.message)
            self.payload_status_label.setText(preview.message)
            self.preview_locator_name_override = preview.final_locator_name
            self.preview_signatures_override = list(preview.added_method_signatures)
            self.preview_signatures_actions_snapshot = tuple(actions)
            self._update_generated_methods_preview()

            preview_dialog = DiffPreviewDialog(
                target_file=preview.target_file,
                final_locator_name=preview.final_locator_name,
                method_names=list(preview.added_methods),
                method_signatures=list(preview.added_method_signatures),
                diff_text=preview.diff_text,
                summary_message=preview.message,
                parent=self,
            )
            if preview_dialog.exec() != QDialog.DialogCode.Accepted:
                self.pending_java_preview = None
                self._set_status("Cancelled — no changes.")
                self.payload_status_label.setText("Cancelled — no changes.")
                self._show_toast("Cancelled — no changes.")
                self._update_generated_methods_preview()
                return

            applied, message, _backup_path = apply_java_preview(preview)
            self.pending_java_preview = None
            self._set_status(message)
            self.payload_status_label.setText(message)
            self._show_toast(message)
            self._update_generated_methods_preview()
            return
        except Exception as exc:
            self.pending_java_preview = None
            self._handle_ui_exception("Unexpected error during preview/apply. See ~/.inspectelement/ui.log.", exc)

    def _resolve_java_selector(self, candidate: LocatorCandidate) -> tuple[str, str] | None:
        locator_type = candidate.locator_type
        if locator_type == "CSS":
            return "css", candidate.locator
        if locator_type == "XPath":
            return "xpath", candidate.locator

        if locator_type == "Selenium":
            selector_kind = candidate.metadata.get("selector_kind")
            selector_value = candidate.metadata.get("selector_value")
            if isinstance(selector_kind, str) and isinstance(selector_value, str):
                normalized = selector_kind.lower()
                if normalized in {"css", "xpath", "id", "name"}:
                    return normalized, selector_value

            by_pattern = re.search(r'By\\.([A-Z_]+)\\(\"(.*)\"\\)', candidate.locator)
            if not by_pattern:
                return None
            mapping = {
                "CSS_SELECTOR": "css",
                "XPATH": "xpath",
                "ID": "id",
                "NAME": "name",
            }
            selector = mapping.get(by_pattern.group(1))
            if not selector:
                return None
            return selector, by_pattern.group(2)

        return None

    def _suggest_element_name(self, candidate: LocatorCandidate | None, force: bool = False) -> None:
        if not force and self.element_name_input.text().strip():
            return

        fallback = candidate.locator if candidate else None
        suggestion = suggest_element_name(self.current_summary, fallback=fallback)
        self.element_name_input.setText(suggestion)

    def _launch(self) -> None:
        self.browser.launch(self.url_input.text())

    def _toggle_inspect(self) -> None:
        enabled = self.inspect_toggle.isChecked()
        self.inspect_toggle.setText(f"Inspect Mode: {'ON' if enabled else 'OFF'}")
        self.browser.set_inspect_mode(enabled)

    def _copy(self, value: str) -> None:
        QApplication.clipboard().setText(value)
        self._set_status("Locator copied.")
        self._show_toast("Panoya kopyalandi")

    def _copy_best(self) -> None:
        if not self.current_candidates:
            self._set_status("No locator candidates yet.")
            return

        selected_format = self.output_format_combo.currentText()
        if selected_format == "Best":
            self._copy(self.current_candidates[0].locator)
            return

        for candidate in self.current_candidates:
            if candidate.locator_type == selected_format:
                self._copy(candidate.locator)
                return
        self._set_status("No candidate for selected format")
        self._show_toast("Secilen formatta locator yok")

    def _reset_learning(self) -> None:
        self.browser.reset_learning()

    def _clear_overrides(self) -> None:
        self.browser.clear_overrides()
        self._show_toast("Overrides temizlendi")

    def _feedback(self, was_good: bool) -> None:
        candidate = self._selected_candidate()
        if not candidate:
            self._set_status("Select a locator first.")
            self._show_toast("Once bir locator sec")
            return
        ok = self.browser.record_feedback(candidate, was_good)
        if ok:
            self._set_status("Feedback recorded.")
            self._show_toast("Feedback eklendi")
            return
        self._set_status("Capture an element before sending feedback.")
        self._show_toast("Once element secimi yap")

    def _good_edited(self) -> None:
        candidate = self._selected_candidate()
        if not candidate:
            self._set_status("Select a locator first.")
            self._show_toast("Once bir locator sec")
            return

        edited = self.locator_editor.toPlainText().strip()
        ok, message = self.browser.record_feedback_with_edited_locator(candidate, edited)
        if ok:
            self._set_status("Edited locator saved as override.")
            self._show_toast("Edited locator kaydedildi")
            return

        self._set_status(message)
        self._show_toast(message)

    def _apply_edit(self) -> None:
        candidate = self._selected_candidate()
        if not candidate:
            self._set_status("Select a locator first.")
            self._show_toast("Once bir locator sec")
            return

        edited = self.locator_editor.toPlainText().strip()
        if not edited:
            self._set_status("Locator editor is empty.")
            self._show_toast("Editor bos")
            return

        selected = self.results_table.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()
        candidate.locator = edited
        self._render_candidates(self.current_candidates)
        if 0 <= row < self.results_table.rowCount():
            self.results_table.selectRow(row)
        self._set_status("Edited locator applied.")
        self._show_toast("Degisiklik uygulandi")

    def _copy_edited(self) -> None:
        edited = self.locator_editor.toPlainText().strip()
        if not edited:
            self._set_status("Locator editor is empty.")
            self._show_toast("Editor bos")
            return
        self._copy(edited)

    def _on_capture(self, summary: ElementSummary, candidates: list[LocatorCandidate]) -> None:
        self._reset_generated_preview_override()
        self.current_summary = summary
        self._set_auto_table_root_from_summary(summary)
        scoring_failed = False
        try:
            ranked_candidates = recommend_locator_candidates(candidates)
        except Exception as exc:
            self.logger.exception("Failed to score locator recommendations", exc_info=exc)
            ranked_candidates = candidates
            scoring_failed = True

        self.current_candidates = ranked_candidates
        self._render_summary(summary)
        self._render_candidates(self.current_candidates)
        if self.pick_table_root_mode and self.current_candidates:
            root_selector = self._resolve_java_selector(self.current_candidates[0])
            if root_selector:
                locator_name_hint = None
                if summary.table_root:
                    locator_name_hint = summary.table_root.get("locator_name_hint")
                self._set_manual_table_root(
                    selector_type=root_selector[0],
                    selector_value=root_selector[1],
                    locator_name=locator_name_hint or "TABLE_ROOT_TABLE",
                )
                self._set_status("Table root overridden from picked element.")
                self._show_toast("Table root overridden")
            else:
                self._set_status("Picked element locator type is unsupported for table root.")
                self._show_toast("Unsupported table root locator")
            self.pick_table_root_mode = False
        if self.current_candidates:
            self._suggest_element_name(self.current_candidates[0], force=True)
        else:
            self.element_name_input.clear()
        self._update_add_button_state()
        status_message = f"Captured <{summary.tag}> with {len(self.current_candidates)} suggestions."
        if scoring_failed:
            status_message += " Recommendation scoring failed; using base order."
        self._set_status(status_message)

    def _on_page_changed(self, title: str, url: str) -> None:
        self.setWindowTitle(f"inspectelement - {title or url}")

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _show_toast(self, message: str, duration_ms: int = 1800) -> None:
        self.toast_label.setText(message)
        self.toast_label.adjustSize()
        self._position_toast()
        self.toast_label.show()
        self.toast_label.raise_()
        self._toast_timer.stop()
        self._toast_timer.start(duration_ms)

    def _position_toast(self) -> None:
        margin = 18
        x = self.width() - self.toast_label.width() - margin
        y = self.height() - self.toast_label.height() - 56
        self.toast_label.move(max(12, x), max(12, y))

    def _render_summary(self, summary: ElementSummary) -> None:
        classes = " ".join(summary.classes) if summary.classes else "-"
        mapping = {
            "tag": summary.tag or "-",
            "id": summary.id or "-",
            "classes": classes,
            "name": summary.name or "-",
            "role": summary.role or "-",
            "text": summary.text or "-",
            "placeholder": summary.placeholder or "-",
            "aria-label": summary.aria_label or "-",
        }
        for key, label in self.detail_labels.items():
            label.setText(mapping.get(key, "-"))

    def _render_candidates(self, candidates: list[LocatorCandidate]) -> None:
        self.results_table.setRowCount(len(candidates))

        for row, candidate in enumerate(candidates):
            rank_item = QTableWidgetItem(str(row + 1))
            type_item = QTableWidgetItem(candidate.locator_type)
            recommendation_score = candidate.metadata.get("write_recommendation_score", candidate.score)
            score_value = float(recommendation_score) if isinstance(recommendation_score, (int, float)) else candidate.score
            score_item = QTableWidgetItem(f"{score_value:.1f}")
            guidance_text = str(candidate.metadata.get("write_recommendation_label", "")).strip() or "-"
            guidance_item = QTableWidgetItem(guidance_text)
            rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            guidance_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.results_table.setItem(row, 0, rank_item)
            self.results_table.setItem(row, 1, type_item)
            self.results_table.setItem(row, 3, score_item)
            self.results_table.setItem(row, 4, guidance_item)

            if guidance_text == "Recommended":
                guidance_item.setForeground(QColor("#166534"))
            elif guidance_text == "Risky":
                guidance_item.setForeground(QColor("#b91c1c"))

            locator_cell = QWidget()
            locator_cell.setObjectName("LocatorCell")
            locator_layout = QHBoxLayout(locator_cell)
            locator_layout.setContentsMargins(6, 3, 6, 3)
            locator_layout.setSpacing(6)

            locator_label = QLabel(candidate.locator)
            locator_label.setObjectName("LocatorText")
            locator_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            locator_label.setWordWrap(False)
            locator_label.setTextFormat(Qt.TextFormat.PlainText)
            locator_label.setProperty("full_locator", candidate.locator)
            locator_label.setToolTip(candidate.locator)
            locator_label.setMinimumWidth(0)
            locator_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            locator_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

            copy_button = QPushButton("Copy")
            copy_button.setObjectName("TableCopyButton")
            copy_button.setFixedSize(72, 28)
            copy_button.clicked.connect(lambda _checked=False, text=candidate.locator: self._copy(text))

            locator_layout.addWidget(locator_label, 1)
            locator_layout.addWidget(copy_button, 0)
            self.results_table.setCellWidget(row, 2, locator_cell)

        self._update_locator_text_elide()

        if candidates:
            self.results_table.selectRow(0)
            self._show_breakdown(candidates[0])
            self.locator_editor.setPlainText(candidates[0].locator)
        else:
            self.breakdown_text.clear()
            self.locator_editor.clear()
        self._update_add_button_state()

    def _locator_text_width(self) -> int:
        column_width = self.results_table.columnWidth(2)
        # margins + spacing + fixed copy button width
        reserved = 6 + 6 + 6 + 72
        return max(120, column_width - reserved)

    def _update_locator_text_elide(self) -> None:
        max_width = self._locator_text_width()
        for row in range(self.results_table.rowCount()):
            cell = self.results_table.cellWidget(row, 2)
            if not cell:
                continue
            label = cell.findChild(QLabel, "LocatorText")
            if not label:
                continue
            full_locator = label.property("full_locator")
            if isinstance(full_locator, str):
                label.setText(label.fontMetrics().elidedText(full_locator, Qt.TextElideMode.ElideRight, max_width))

    def _selected_candidate(self) -> LocatorCandidate | None:
        selected = self.results_table.selectionModel().selectedRows()
        if not selected:
            return None
        row = selected[0].row()
        if row < 0 or row >= len(self.current_candidates):
            return None
        return self.current_candidates[row]

    def _on_selection_changed(self) -> None:
        candidate = self._selected_candidate()
        if candidate:
            self._show_breakdown(candidate)
            self.locator_editor.setPlainText(candidate.locator)
        self._reset_generated_preview_override()
        self._refresh_parameter_panel()
        self._update_add_button_state()

    def _show_breakdown(self, candidate: LocatorCandidate) -> None:
        if not candidate.breakdown:
            self.breakdown_text.setPlainText("No breakdown available.")
            return
        breakdown = candidate.breakdown
        lines = [
            f"Rule: {candidate.rule}",
            f"Uniqueness count: {candidate.uniqueness_count}",
            f"Uniqueness score: {breakdown.uniqueness:+.2f}",
            f"Stability score: {breakdown.stability:+.2f}",
            f"Length penalty: {breakdown.length_penalty:+.2f}",
            f"Dynamic penalty: {breakdown.dynamic_penalty:+.2f}",
            f"Learning adjustment: {breakdown.learning_adjustment:+.2f}",
            f"Total: {breakdown.total:+.2f}",
        ]
        recommendation_score = candidate.metadata.get("write_recommendation_score")
        recommendation_label = candidate.metadata.get("write_recommendation_label")
        if isinstance(recommendation_score, (int, float)):
            lines.append(f"Write recommendation score: {float(recommendation_score):.1f}")
        if isinstance(recommendation_label, str) and recommendation_label:
            lines.append(f"Write recommendation label: {recommendation_label}")

        reasons = candidate.metadata.get("write_recommendation_reasons")
        if isinstance(reasons, list) and reasons:
            lines.append(f"Write recommendation reasons: {', '.join(str(reason) for reason in reasons)}")
        if candidate.metadata:
            for key in ("depth", "nth_count", "depth_penalty", "nth_penalty", "is_override"):
                if key in candidate.metadata:
                    lines.append(f"{key}: {candidate.metadata[key]}")
        self.breakdown_text.setPlainText("\n".join(lines))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        try:
            self.browser.shutdown()
        except Exception as exc:
            QMessageBox.warning(self, "Shutdown warning", str(exc))
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._update_locator_text_elide()
        if self.toast_label.isVisible():
            self._position_toast()
        super().resizeEvent(event)
