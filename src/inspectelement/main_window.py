from __future__ import annotations

from pathlib import Path
import re
import sys

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
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
from .context_wizard import ContextSelection, ContextWizardDialog
from .models import ElementSummary, LocatorCandidate
from .project_discovery import ModuleInfo, PageClassInfo, discover_page_classes


class EventBridge(QObject):
    capture_received = Signal(object, object)
    status_changed = Signal(str)
    page_changed = Signal(str, str)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
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
        self.element_name_input.textChanged.connect(self._update_add_button_state)

        self.click_action_checkbox = QCheckBox("click")
        self.sendkeys_action_checkbox = QCheckBox("sendKeys")
        self.click_action_checkbox.toggled.connect(self._on_action_selection_changed)
        self.sendkeys_action_checkbox.toggled.connect(self._on_action_selection_changed)

        self.add_button = QPushButton("Add")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._prepare_add_request)

        self.payload_status_label = QLabel("Waiting for page, locator, and element name.")
        self.payload_status_label.setObjectName("Muted")
        self.payload_status_label.setWordWrap(True)

        self.results_table = QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(["Rank", "Type", "Locator", "Score"])
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
        self.results_table.setColumnWidth(0, 70)
        self.results_table.setColumnWidth(1, 110)
        self.results_table.setColumnWidth(3, 96)
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

        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Actions:"))
        action_row.addWidget(self.click_action_checkbox)
        action_row.addWidget(self.sendkeys_action_checkbox)
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
            """
        )

    @staticmethod
    def _runtime_summary() -> str:
        version = sys.version.split()[0]
        return f"Runtime: {sys.executable} (Python {version})"

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
        has_page = self._selected_page_class() is not None
        has_locator = self._selected_candidate() is not None
        has_name = bool(self.element_name_input.text().strip())
        self.add_button.setEnabled(has_page and has_locator and has_name)
        if not (has_page and has_locator and has_name):
            self.payload_status_label.setText("Waiting for page, locator, and element name.")
        else:
            self._refresh_payload_status("Payload ready. Click Add to prepare output.")

    def _selected_actions(self) -> list[str]:
        actions: list[str] = []
        if self.click_action_checkbox.isChecked():
            actions.append("click")
        if self.sendkeys_action_checkbox.isChecked():
            actions.append("sendKeys")
        return actions

    def _on_action_selection_changed(self) -> None:
        self._refresh_payload_status()

    def _refresh_payload_status(self, prefix: str = "Payload preview") -> None:
        page = self._selected_page_class()
        candidate = self._selected_candidate()
        name = self.element_name_input.text().strip()
        if not page or not candidate or not name:
            self.payload_status_label.setText("Waiting for page, locator, and element name.")
            return

        actions = self._selected_actions()
        action_text = ", ".join(actions) if actions else "none"
        self.payload_status_label.setText(
            f"{prefix}: {page.class_name} | {name} | {candidate.locator_type} | actions={action_text}"
        )

    def _prepare_add_request(self) -> None:
        page = self._selected_page_class()
        candidate = self._selected_candidate()
        element_name = self.element_name_input.text().strip()
        if not page or not candidate or not element_name:
            self._set_status("Select page, locator, and element name before Add.")
            self._show_toast("Add icin zorunlu alanlar eksik")
            self._update_add_button_state()
            return

        actions = self._selected_actions()

        action_text = ", ".join(actions) if actions else "none"
        self._set_status(
            f"Add payload prepared -> Page: {page.class_name}, Name: {element_name}, "
            f"Locator: {candidate.locator_type}, Actions: {action_text}. "
            "Java write flow will be added in Sprint 3."
        )
        self.payload_status_label.setText("Payload prepared (no file written)")
        self._show_toast("Payload hazir (Sprint 3'te apply)")

    def _suggest_element_name(self, candidate: LocatorCandidate | None, force: bool = False) -> None:
        if not force and self.element_name_input.text().strip():
            return

        suggestion_base = self._to_constant_name(self._preferred_name_source(candidate))
        suffix = self._element_name_suffix()
        suggestion = (
            suggestion_base
            if suggestion_base.endswith(f"_{suffix}")
            else f"{suggestion_base}_{suffix}"
        )
        self.element_name_input.setText(suggestion)

    def _preferred_name_source(self, candidate: LocatorCandidate | None) -> str:
        if self.current_summary:
            summary_values = (
                self.current_summary.text,
                self.current_summary.aria_label,
                self.current_summary.placeholder,
                self.current_summary.name,
                self.current_summary.id,
            )
            for value in summary_values:
                if value and value.strip():
                    return value

        if candidate and candidate.locator:
            return candidate.locator
        return "ELEMENT"

    def _element_name_suffix(self) -> str:
        if not self.current_summary:
            return "TXT"

        tag = (self.current_summary.tag or "").strip().lower()
        role = (self.current_summary.role or "").strip().lower()
        input_type = (self.current_summary.attributes.get("type", "") if self.current_summary.attributes else "").lower()

        if tag == "button" or role == "button":
            return "BTN"
        if tag == "input" and input_type in {"button", "submit", "reset"}:
            return "BTN"
        if tag == "a":
            return "LNK"
        return "TXT"

    @staticmethod
    def _normalize_turkish_text(value: str) -> str:
        translation_table = str.maketrans(
            {
                "ç": "c",
                "Ç": "C",
                "ğ": "g",
                "Ğ": "G",
                "ı": "i",
                "İ": "I",
                "ö": "o",
                "Ö": "O",
                "ş": "s",
                "Ş": "S",
                "ü": "u",
                "Ü": "U",
            }
        )
        return value.translate(translation_table)

    @staticmethod
    def _to_constant_name(value: str) -> str:
        normalized_value = MainWindow._normalize_turkish_text(value)
        fragments = [fragment for fragment in re.split(r"[^A-Za-z0-9]+", normalized_value) if fragment]
        if not fragments:
            return "ELEMENT"

        normalized = "_".join(part.upper() for part in fragments[:4])
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        if not normalized:
            return "ELEMENT"
        if normalized[0].isdigit():
            return f"E_{normalized}"
        return normalized

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
        self.current_summary = summary
        self.current_candidates = candidates
        self._render_summary(summary)
        self._render_candidates(candidates)
        if candidates:
            self._suggest_element_name(candidates[0], force=True)
        else:
            self.element_name_input.clear()
        self._update_add_button_state()
        self._set_status(f"Captured <{summary.tag}> with {len(candidates)} suggestions.")

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
            score_item = QTableWidgetItem(f"{candidate.score:.2f}")
            rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.results_table.setItem(row, 0, rank_item)
            self.results_table.setItem(row, 1, type_item)
            self.results_table.setItem(row, 3, score_item)

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
