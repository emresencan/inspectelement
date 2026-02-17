from __future__ import annotations

from dataclasses import replace
import logging
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QPoint, QRect, QSize, QSettings, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QGuiApplication, QIcon
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .action_catalog import (
    ACTION_PRESETS,
    CATEGORY_FILTERS,
    ActionSignaturePreview,
    add_action_by_trigger,
    action_label,
    build_signature_previews,
    filter_action_specs,
    has_combo_actions,
    has_table_actions,
    normalize_selected_actions,
    required_parameter_keys,
    return_kind_badge,
)
from .embedded_inspector import (
    EMBEDDED_INSPECTOR_BOOTSTRAP_SCRIPT,
    build_element_summary_from_payload,
    build_fallback_locator_payload,
    build_capture_from_point_script,
    build_locator_candidates_from_payload,
)
from .capture_guard import CaptureGuard
from .java_pom_writer import (
    JavaPreview,
    apply_java_preview,
    generate_java_preview,
)
from .learning_store import LearningStore
from .locator_recommendation import recommend_locator_candidates
from .models import ElementSummary, LocatorCandidate, PageContext
from .name_suggester import suggest_element_name
from .override_logic import build_override_candidate, inject_override_candidate
from .page_creator import (
    PageCreationPreview,
    apply_page_creation_preview,
    generate_page_creation_preview,
)
from .project_discovery import ModuleInfo, PageClassInfo, discover_module, discover_modules, discover_page_classes
from .selector_rules import is_obvious_root_container_locator
from .ui_state import (
    WorkspaceState,
    can_enable_inspect_toggle,
    can_enable_new_page_button,
    compute_workspace_button_state,
    load_workspace_state,
    save_workspace_state,
)
from .validation import validate_generation_request

class EmbeddedInspectBridge(QObject):
    payload_received = Signal(object)
    log_received = Signal(str)

    @Slot("QVariant")
    def report(self, payload: Any) -> None:
        self.payload_received.emit(payload)

    @Slot(str)
    def log(self, message: str) -> None:
        self.log_received.emit(str(message))


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


class TopBar(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("TopBar")

        self.project_path_input = QLineEdit()
        self.project_path_input.setPlaceholderText("/path/to/automation/project")
        self.project_browse_button = QPushButton("Browse...")

        self.module_combo = QComboBox()
        self.module_combo.addItem("Select module", None)
        self.module_combo.setMinimumWidth(160)

        self.page_combo = QComboBox()
        self.page_combo.addItem("Select page class", None)
        self.page_combo.setMinimumWidth(160)
        self.page_combo.setEnabled(False)
        self.new_page_button = QPushButton("+ New Page")
        self.new_page_button.setEnabled(False)

        self.url_input = QLineEdit("https://example.com")
        self.url_input.setPlaceholderText("https://your-app-url")
        self.launch_button = QPushButton("Launch")

        self.inspect_toggle = QPushButton("Inspect: OFF")
        self.inspect_toggle.setCheckable(True)
        self.inspect_toggle.setEnabled(False)

        self.validate_button = QPushButton("Validate Only")
        self.add_button = QPushButton("Add -> Preview")
        self.apply_button = QPushButton("Apply")
        self.cancel_preview_button = QPushButton("Cancel Preview")
        self.apply_button.setEnabled(False)
        self.cancel_preview_button.setEnabled(False)

        self.status_pill = QLabel("OK")
        self.status_pill.setObjectName("StatusPill")
        self.status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_pill.setMinimumWidth(76)
        self.status_pill.setMaximumWidth(96)

        fixed_buttons: list[tuple[QPushButton, int]] = [
            (self.project_browse_button, 90),
            (self.new_page_button, 110),
            (self.launch_button, 88),
            (self.inspect_toggle, 110),
            (self.validate_button, 120),
            (self.add_button, 130),
            (self.apply_button, 80),
            (self.cancel_preview_button, 120),
        ]
        for button, width in fixed_buttons:
            button.setMinimumWidth(width)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QGridLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        layout.setColumnStretch(1, 4)

        layout.addWidget(QLabel("Project"), 0, 0)
        layout.addWidget(self.project_path_input, 0, 1, 1, 3)
        layout.addWidget(self.project_browse_button, 0, 4)
        layout.addWidget(QLabel("Module"), 0, 5)
        layout.addWidget(self.module_combo, 0, 6)
        layout.addWidget(QLabel("Page"), 0, 7)
        layout.addWidget(self.page_combo, 0, 8)
        layout.addWidget(self.new_page_button, 0, 9)

        layout.addWidget(QLabel("URL"), 1, 0)
        layout.addWidget(self.url_input, 1, 1, 1, 3)
        layout.addWidget(self.launch_button, 1, 4)
        layout.addWidget(self.inspect_toggle, 1, 5)
        layout.addWidget(self.validate_button, 1, 6)
        layout.addWidget(self.add_button, 1, 7)
        layout.addWidget(self.apply_button, 1, 8)
        layout.addWidget(self.cancel_preview_button, 1, 9)
        layout.addWidget(self.status_pill, 1, 10)

    def set_status_pill(self, level: str) -> None:
        normalized = level.lower()
        if normalized not in {"ok", "warning", "error"}:
            normalized = "ok"
        self.status_pill.setText(normalized.capitalize())
        self.status_pill.setProperty("level", normalized)
        self.status_pill.style().unpolish(self.status_pill)
        self.status_pill.style().polish(self.status_pill)


class LeftPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("LeftPanel")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("LeftScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll_area = scroll

        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(8)

        self.content_layout = content_layout
        scroll.setWidget(content)
        root.addWidget(scroll)

    def ensure_widget_visible(self, widget: QWidget) -> None:
        if not widget:
            return
        self.scroll_area.ensureWidgetVisible(widget, 12, 24)


class BrowserPanel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("BrowserPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._web_view = None
        self._current_title = ""
        self._current_url = ""
        self._fallback_label = QLabel("")
        self._fallback_label.setObjectName("Muted")
        self._fallback_label.setWordWrap(True)

        self.url_label = QLabel("URL: -")
        self.url_label.setObjectName("Muted")
        self.page_label = QLabel("Page: -")
        self.page_label.setObjectName("Muted")
        self.info_label = QLabel("Embedded browser container is ready.")
        self.info_label.setObjectName("Muted")

        title = QLabel("Browser Workspace")
        title.setObjectName("SectionTitle")

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addWidget(title)
        root.addWidget(self.info_label)
        root.addWidget(self.url_label)
        root.addWidget(self.page_label)

        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore

            class InspectableWebView(QWebEngineView):
                inspect_click = Signal(int, int)

                def __init__(self, parent: QWidget | None = None) -> None:
                    super().__init__(parent)
                    self._inspect_capture_enabled = False

                def set_inspect_capture_enabled(self, enabled: bool) -> None:
                    self._inspect_capture_enabled = bool(enabled)

                def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
                    if self._inspect_capture_enabled and event.button() == Qt.MouseButton.LeftButton:
                        point = event.position()
                        self.inspect_click.emit(int(point.x()), int(point.y()))
                        event.accept()
                        return
                    super().mousePressEvent(event)

            self._web_view = InspectableWebView(self)
            self._web_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._web_view.setUrl(QUrl("about:blank"))
            root.addWidget(self._web_view, 1)
            self._fallback_label.hide()
        except Exception:
            self._fallback_label.setText(
                "Qt WebEngine is not available in this environment. "
                "Embedded inspect mode cannot start."
            )
            root.addWidget(self._fallback_label, 1)

    @property
    def web_view(self):
        return self._web_view

    @property
    def has_web_view(self) -> bool:
        return self._web_view is not None

    def set_inspect_capture_enabled(self, enabled: bool) -> None:
        view = self._web_view
        if view is None:
            return
        setter = getattr(view, "set_inspect_capture_enabled", None)
        if callable(setter):
            setter(bool(enabled))

    @property
    def current_title(self) -> str:
        return self._current_title

    @property
    def current_url(self) -> str:
        return self._current_url

    def load_url(self, url: str) -> None:
        self._current_url = url or ""
        self.url_label.setText(f"URL: {url or '-'}")
        if self._web_view is not None and url:
            self._web_view.setUrl(QUrl(url))

    def set_page_info(self, title: str, url: str) -> None:
        self._current_title = title or ""
        self._current_url = url or self._current_url
        self.page_label.setText(f"Page: {title or '-'}")
        self.url_label.setText(f"URL: {url or '-'}")


class BottomStatusBar(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("BottomStatusBar")

        self.last_action_value = QLabel("-")
        self.last_action_value.setObjectName("Muted")
        self.warning_value = QLabel("-")
        self.warning_value.setObjectName("TableRootWarning")
        self.write_value = QLabel("-")
        self.write_value.setObjectName("Muted")
        for label in (self.last_action_value, self.warning_value, self.write_value):
            label.setMinimumWidth(0)
            label.setWordWrap(False)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(12)
        root.addWidget(QLabel("Last Action:"))
        root.addWidget(self.last_action_value, 3)
        root.addWidget(QLabel("Warning:"))
        root.addWidget(self.warning_value, 2)
        root.addWidget(QLabel("Write:"))
        root.addWidget(self.write_value, 2)

    def set_last_action(self, text: str) -> None:
        value = text or "-"
        self.last_action_value.setText(value)
        self.last_action_value.setToolTip(value)

    def set_warning(self, text: str) -> None:
        value = text or "-"
        self.warning_value.setText(value)
        self.warning_value.setToolTip(value)

    def set_write_result(self, text: str) -> None:
        value = text or "-"
        self.write_value.setText(value)
        self.write_value.setToolTip(value)


class WorkspaceWindow(QMainWindow):
    CREATE_PAGE_COMBO_LABEL = "+ Create New Page..."
    CREATE_PAGE_COMBO_TOKEN = "__create_new_page__"

    def __init__(self) -> None:
        super().__init__()
        self.logger = self._build_logger()
        self.setWindowTitle("inspectelement")
        self._fit_window_to_screen()
        self._set_icon()

        self.current_summary: ElementSummary | None = None
        self.current_candidates: list[LocatorCandidate] = []
        self.current_page_context: PageContext | None = None
        self.project_root: Path | None = None
        self.selected_module: ModuleInfo | None = None
        self.discovered_pages: list[PageClassInfo] = []
        self.pending_java_preview: JavaPreview | None = None
        self.pending_page_preview: PageCreationPreview | None = None
        self._available_modules: list[ModuleInfo] = []
        self._loading_workspace_state = False
        self._pending_inspect_restore = False
        self._has_launched_page = False
        self._capture_busy = False
        self._capture_guard = CaptureGuard()
        self._capture_seq = 0
        self._capture_active_seq = 0
        self._settings = QSettings("inspectelement", "workspace")
        self.learning_store = LearningStore()

        self._embedded_channel: QWebChannel | None = None
        self._embedded_bridge: EmbeddedInspectBridge | None = None

        self.top_bar = TopBar()
        self.project_path_input = self.top_bar.project_path_input
        self.project_browse_button = self.top_bar.project_browse_button
        self.module_combo = self.top_bar.module_combo
        self.page_combo = self.top_bar.page_combo
        self.new_page_button = self.top_bar.new_page_button
        self.url_input = self.top_bar.url_input
        self.launch_button = self.top_bar.launch_button
        self.inspect_toggle = self.top_bar.inspect_toggle
        self.validate_button = self.top_bar.validate_button
        self.add_button = self.top_bar.add_button
        self.apply_button = self.top_bar.apply_button
        self.cancel_preview_button = self.top_bar.cancel_preview_button

        self.project_browse_button.clicked.connect(self._browse_project_path)
        self.project_path_input.returnPressed.connect(self._on_project_path_changed)
        self.project_path_input.editingFinished.connect(self._on_project_path_changed)
        self.module_combo.currentIndexChanged.connect(self._on_module_changed)
        self.page_combo.currentIndexChanged.connect(self._on_page_combo_changed)
        self.new_page_button.clicked.connect(self._create_new_page_flow)
        self.url_input.editingFinished.connect(self._persist_workspace_state)
        self.launch_button.clicked.connect(self._launch)
        self.inspect_toggle.clicked.connect(self._toggle_inspect)
        self.validate_button.clicked.connect(self._validate_only_request)
        self.add_button.clicked.connect(self._prepare_add_request)
        self.apply_button.clicked.connect(self._apply_pending_preview)
        self.cancel_preview_button.clicked.connect(self._cancel_pending_preview)

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

        self.page_combo_previous_index = 0

        self.element_name_input = QLineEdit()
        self.element_name_input.setPlaceholderText("Element name (e.g. KURAL_ADI_TXT)")
        self.element_name_input.textChanged.connect(self._on_element_name_changed)

        self.locator_constant_input = QLineEdit()
        self.locator_constant_input.setPlaceholderText("Locator constant (resolved after preview)")
        self.locator_constant_input.setReadOnly(True)

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
        self.auto_table_root_warning: str | None = None
        self.auto_table_root_candidates: list[dict[str, str]] = []
        self.manual_table_root_selector_type: str | None = None
        self.manual_table_root_selector_value: str | None = None
        self.manual_table_root_locator_name: str | None = None
        self.manual_table_root_warning: str | None = None

        self.action_filter_group = QButtonGroup(self)
        self.action_filter_group.setExclusive(True)
        self.action_filter_buttons: dict[str, QPushButton] = {}
        self.selected_action_flow: FlowLayout | None = None
        self.available_action_specs = []
        self._action_search_timer = QTimer(self)
        self._action_search_timer.setSingleShot(True)
        self._action_search_timer.setInterval(180)
        self._action_search_timer.timeout.connect(self._refresh_action_dropdown)

        self.action_search_input = QLineEdit()
        self.action_search_input.setPlaceholderText("Search action by name or description")
        self.action_search_input.textChanged.connect(lambda _value: self._action_search_timer.start())

        self.action_dropdown = QComboBox()
        self.action_dropdown.currentIndexChanged.connect(self._on_action_dropdown_changed)
        self.action_dropdown.activated.connect(self._on_action_dropdown_activated)

        self.action_add_button = QPushButton("Add")
        self.action_add_button.setMinimumWidth(72)
        self.action_add_button.clicked.connect(
            lambda _checked=False: self._add_selected_dropdown_action(trigger="button_click")
        )

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
        self.table_root_warning_label = QLabel("")
        self.table_root_warning_label.setObjectName("TableRootWarning")
        self.table_root_candidates_combo = QComboBox()
        self.table_root_candidates_combo.currentIndexChanged.connect(self._on_table_root_candidate_changed)
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

        left_title = QLabel("Workspace Controls")
        left_title.setObjectName("SectionTitle")
        left_hint = QLabel("Inspect in the browser panel, then generate safely via Preview -> Apply.")
        left_hint.setObjectName("Muted")

        quick_actions_row = QHBoxLayout()
        quick_actions_row.addWidget(QLabel("Copy Format"))
        quick_actions_row.addWidget(self.output_format_combo)
        quick_actions_row.addWidget(self.copy_best_button)
        quick_actions_row.addWidget(self.reset_learning_button)
        quick_actions_row.addWidget(self.clear_overrides_button)
        quick_actions_row.addStretch(1)

        detail_card = QFrame()
        detail_card.setObjectName("Card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(8, 8, 8, 8)
        detail_layout.setSpacing(6)
        detail_layout.addWidget(QLabel("Element Snapshot"))
        detail_layout.addLayout(detail_form)
        detail_layout.addWidget(QLabel("Element Name"))
        detail_layout.addWidget(self.element_name_input)
        detail_layout.addWidget(QLabel("Locator Constant (final)"))
        detail_layout.addWidget(self.locator_constant_input)

        self.left_panel = LeftPanel()
        left_col = self.left_panel.content_layout
        left_col.addWidget(left_title)
        left_col.addWidget(left_hint)
        left_col.addLayout(quick_actions_row)
        left_col.addWidget(detail_card)
        left_col.addWidget(QLabel("Locator Candidates"))
        left_col.addWidget(self.results_table)
        left_col.addWidget(QLabel("Locator Editor"))
        left_col.addWidget(self.locator_editor)
        left_col.addLayout(editor_actions_row)
        left_col.addWidget(QLabel("Score Breakdown"))
        left_col.addWidget(self.breakdown_text)
        left_col.addLayout(feedback_row)
        left_col.addWidget(QLabel("Actions"))
        left_col.addWidget(self.action_picker_widget)
        left_col.addWidget(self._build_table_root_section())
        left_col.addWidget(self._build_parameter_panel())
        left_col.addWidget(QLabel("Generated Method Signatures"))
        left_col.addWidget(self.generated_methods_preview)
        left_col.addWidget(QLabel("Log Language"))
        left_col.addWidget(self.log_language_combo)
        left_col.addWidget(self.payload_status_label)
        left_col.addWidget(self._build_inline_new_page_drawer())
        left_col.addWidget(self._build_diff_preview_dock())
        left_col.addStretch(1)

        self.browser_panel = BrowserPanel()
        self._setup_embedded_browser_bridge()
        self.bottom_status_bar = BottomStatusBar()

        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setObjectName("WorkspaceSplitter")
        self.left_panel.setMinimumWidth(400)
        self.browser_panel.setMinimumWidth(700)
        self.workspace_splitter.addWidget(self.left_panel)
        self.workspace_splitter.addWidget(self.browser_panel)
        self.workspace_splitter.setStretchFactor(0, 0)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.splitterMoved.connect(self._save_splitter_sizes)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)
        root_layout.addWidget(self.top_bar)
        root_layout.addWidget(self.workspace_splitter, 1)
        root_layout.addWidget(self.bottom_status_bar)
        self.setCentralWidget(root)

        self.toast_label = QLabel("", self)
        self.toast_label.setObjectName("Toast")
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)

        self._apply_style()
        self._restore_splitter_sizes()
        self._refresh_table_root_section()
        self._refresh_parameter_panel()
        self._update_generated_methods_preview()
        self._load_initial_workspace_state()
        self._update_add_button_state()
        self._refresh_inspect_toggle_state()
        if not self.selected_module:
            self._set_status("Select project/module/page from the top bar to start.")

    def _build_inline_new_page_drawer(self) -> QWidget:
        self.new_page_drawer = QFrame()
        self.new_page_drawer.setObjectName("Card")
        self.new_page_drawer.setVisible(False)

        self.new_page_name_input = QLineEdit()
        self.new_page_name_input.setPlaceholderText("PascalCase page name, e.g. LoginPage")
        self.new_page_name_input.returnPressed.connect(self._preview_new_page)
        self.new_page_preview_button = QPushButton("Preview Page")
        self.new_page_preview_button.clicked.connect(self._preview_new_page)
        self.new_page_apply_button = QPushButton("Create Page")
        self.new_page_apply_button.setEnabled(False)
        self.new_page_apply_button.clicked.connect(self._apply_new_page_preview)
        self.new_page_cancel_button = QPushButton("Cancel")
        self.new_page_cancel_button.clicked.connect(self._cancel_new_page_drawer)

        self.new_page_package_label = QLabel("Package: -")
        self.new_page_package_label.setObjectName("Muted")
        self.new_page_target_label = QLabel("Target: -")
        self.new_page_target_label.setObjectName("Muted")

        self.new_page_file_preview = QPlainTextEdit()
        self.new_page_file_preview.setReadOnly(True)
        self.new_page_file_preview.setMaximumHeight(120)
        self.new_page_file_preview.setPlaceholderText("Generated page content preview")

        self.new_page_diff_preview = QPlainTextEdit()
        self.new_page_diff_preview.setReadOnly(True)
        self.new_page_diff_preview.setMaximumHeight(140)
        self.new_page_diff_preview.setPlaceholderText("New page diff preview")

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("New Page (inline)"))
        header_row.addStretch(1)
        header_row.addWidget(self.new_page_preview_button)
        header_row.addWidget(self.new_page_apply_button)
        header_row.addWidget(self.new_page_cancel_button)

        layout = QVBoxLayout(self.new_page_drawer)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(header_row)
        layout.addWidget(self.new_page_name_input)
        layout.addWidget(self.new_page_package_label)
        layout.addWidget(self.new_page_target_label)
        layout.addWidget(self.new_page_file_preview)
        layout.addWidget(self.new_page_diff_preview)
        return self.new_page_drawer

    def _build_diff_preview_dock(self) -> QWidget:
        self.diff_preview_dock = QFrame()
        self.diff_preview_dock.setObjectName("Card")
        self.diff_preview_dock.setVisible(False)

        self.diff_target_label = QLabel("Target file: -")
        self.diff_target_label.setObjectName("Muted")
        self.diff_locator_label = QLabel("Final locator: -")
        self.diff_locator_label.setObjectName("Muted")
        self.diff_methods_label = QLabel("Methods: -")
        self.diff_methods_label.setObjectName("Muted")
        self.diff_notes_label = QLabel("Notes: -")
        self.diff_notes_label.setObjectName("Muted")

        self.diff_signatures_preview = QPlainTextEdit()
        self.diff_signatures_preview.setReadOnly(True)
        self.diff_signatures_preview.setMaximumHeight(110)
        self.diff_signatures_preview.setPlaceholderText("Generated method signatures")

        self.diff_preview_text = QPlainTextEdit()
        self.diff_preview_text.setReadOnly(True)
        self.diff_preview_text.setPlaceholderText("Unified diff preview")
        self.diff_preview_text.setMaximumHeight(180)

        layout = QVBoxLayout(self.diff_preview_dock)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Preview Diff Dock"))
        layout.addWidget(self.diff_target_label)
        layout.addWidget(self.diff_locator_label)
        layout.addWidget(self.diff_methods_label)
        layout.addWidget(self.diff_notes_label)
        layout.addWidget(self.diff_signatures_preview)
        layout.addWidget(self.diff_preview_text)
        return self.diff_preview_dock

    def _render_java_preview_dock(self, preview: JavaPreview) -> None:
        self.diff_target_label.setText(f"Target file: {preview.target_file}")
        self.diff_locator_label.setText(f"Final locator: {preview.final_locator_name or '-'}")
        self.diff_methods_label.setText(f"Methods: {', '.join(preview.added_methods) if preview.added_methods else '-'}")
        self.diff_notes_label.setText(f"Notes: {' | '.join(preview.notes) if preview.notes else '-'}")
        self.diff_signatures_preview.setPlainText("\n".join(preview.added_method_signatures) if preview.added_method_signatures else "-")
        self.diff_preview_text.setPlainText(preview.diff_text)
        self.diff_preview_dock.setVisible(True)

    def _clear_java_preview_dock(self) -> None:
        self.diff_target_label.setText("Target file: -")
        self.diff_locator_label.setText("Final locator: -")
        self.diff_methods_label.setText("Methods: -")
        self.diff_notes_label.setText("Notes: -")
        self.diff_signatures_preview.clear()
        self.diff_preview_text.clear()
        self.diff_preview_dock.setVisible(False)

    def _open_new_page_drawer(self) -> None:
        self.logger.info("New Page handler invoked.")
        if not self.selected_module:
            self._set_status("Select module before creating page.")
            self._show_toast("Select module first")
            return
        self.pending_page_preview = None
        self.new_page_name_input.clear()
        self.new_page_package_label.setText("Package: -")
        self.new_page_target_label.setText("Target: -")
        self.new_page_file_preview.clear()
        self.new_page_diff_preview.clear()
        self.new_page_apply_button.setEnabled(False)
        self.new_page_drawer.setVisible(True)
        self.new_page_drawer.raise_()
        QTimer.singleShot(0, lambda: self.left_panel.ensure_widget_visible(self.new_page_drawer))
        self.new_page_name_input.setFocus()
        self._set_status("New page drawer opened.")
        self._show_toast("New Page drawer opened")

    def _preview_new_page(self) -> None:
        if not self.selected_module:
            self._set_status("Select module before creating page.")
            return

        preview = generate_page_creation_preview(
            module=self.selected_module,
            existing_pages=self.discovered_pages,
            page_name_raw=self.new_page_name_input.text().strip(),
        )
        self.pending_page_preview = preview
        self.new_page_package_label.setText(f"Package: {preview.package_name or '-'}")
        self.new_page_target_label.setText(f"Target: {preview.target_file}")
        self.new_page_file_preview.setPlainText(preview.file_content or "")
        self.new_page_diff_preview.setPlainText(preview.diff_text or "")
        self.new_page_apply_button.setEnabled(preview.ok)
        self._set_status(preview.message)

    def _apply_new_page_preview(self) -> None:
        preview = self.pending_page_preview
        if preview is None or not preview.ok:
            self._set_status("No valid page preview to apply.")
            return

        applied, message = apply_page_creation_preview(preview)
        self._set_status(message)
        self._show_toast(message)
        if not applied:
            return

        created_class_name = preview.class_name
        self._refresh_page_classes()
        if created_class_name:
            self._select_page_in_combo(created_class_name)
        self._cancel_new_page_drawer(clear_status=False)

    def _cancel_new_page_drawer(self, clear_status: bool = True) -> None:
        self.pending_page_preview = None
        self.new_page_name_input.clear()
        self.new_page_package_label.setText("Package: -")
        self.new_page_target_label.setText("Target: -")
        self.new_page_file_preview.clear()
        self.new_page_diff_preview.clear()
        self.new_page_apply_button.setEnabled(False)
        self.new_page_drawer.setVisible(False)
        if clear_status:
            self._set_status("Cancelled — no page created.")

    def _load_initial_workspace_state(self) -> None:
        state = load_workspace_state()
        if state is None:
            return

        self._loading_workspace_state = True
        self.url_input.setText(state.url or "https://example.com")
        self.project_path_input.setText(state.project_root)
        self._reload_modules_from_project_root(preferred_module_name=state.module_name, persist=False)
        if state.page_class_name:
            self._select_page_in_combo(state.page_class_name)
        # Startup policy: always begin with inspect mode OFF.
        self.inspect_toggle.setChecked(False)
        self.inspect_toggle.setText("Inspect: OFF")
        self._pending_inspect_restore = False
        self._loading_workspace_state = False
        self._set_status("Workspace context restored from config.json.")

    def _build_workspace_state(self) -> WorkspaceState:
        page = self._selected_page_class()
        return WorkspaceState(
            project_root=self.project_path_input.text().strip(),
            module_name=self.selected_module.name if self.selected_module else "",
            page_class_name=page.class_name if page else "",
            url=self.url_input.text().strip() or "https://example.com",
            inspect_enabled=bool(self.inspect_toggle.isChecked()),
        )

    def _persist_workspace_state(self) -> None:
        if self._loading_workspace_state:
            return
        ok, message = save_workspace_state(self._build_workspace_state())
        if not ok and message:
            self.logger.warning("Failed to persist workspace state: %s", message)

    def _browse_project_path(self) -> None:
        start_dir = self.project_path_input.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select Project Root", start_dir)
        if not selected:
            return
        self.project_path_input.setText(selected)
        self._reload_modules_from_project_root()

    def _on_project_path_changed(self) -> None:
        self._reload_modules_from_project_root()

    def _reload_modules_from_project_root(self, preferred_module_name: str | None = None, persist: bool = True) -> None:
        root_text = self.project_path_input.text().strip()
        project_root = Path(root_text).expanduser() if root_text else None
        self._available_modules = []

        self.module_combo.blockSignals(True)
        self.module_combo.clear()
        self.module_combo.addItem("Select module", None)
        self.module_combo.blockSignals(False)

        if not project_root or not project_root.is_dir():
            self.project_root = None
            self.selected_module = None
            self.discovered_pages = []
            self._cancel_pending_preview(clear_status=False)
            self._refresh_page_classes()
            self.new_page_button.setEnabled(False)
            self._update_add_button_state()
            if root_text:
                self._set_status("Project path is not a valid folder.")
            return

        modules = discover_modules(project_root)
        self._available_modules = modules
        self.project_root = project_root
        if not modules:
            self.selected_module = None
            self.discovered_pages = []
            self._cancel_pending_preview(clear_status=False)
            self._refresh_page_classes()
            self.new_page_button.setEnabled(False)
            self._update_add_button_state()
            self._set_status("No modules found under modules/apps.")
            if persist:
                self._persist_workspace_state()
            return

        self.module_combo.blockSignals(True)
        selected_index = 1
        for idx, module in enumerate(modules, start=1):
            self.module_combo.addItem(module.name, module)
            if preferred_module_name and module.name == preferred_module_name:
                selected_index = idx
        self.module_combo.setCurrentIndex(selected_index)
        self.module_combo.blockSignals(False)
        self._on_module_changed()
        if persist:
            self._persist_workspace_state()

    def _on_module_changed(self) -> None:
        selected = self.module_combo.currentData()
        if not isinstance(selected, ModuleInfo):
            self.selected_module = None
            self._cancel_pending_preview(clear_status=False)
            self._refresh_page_classes()
            self._update_add_button_state()
            return

        if self.project_root is None:
            root_text = self.project_path_input.text().strip()
            root_candidate = Path(root_text).expanduser() if root_text else None
            if root_candidate and root_candidate.is_dir():
                self.project_root = root_candidate
        if self.project_root is not None:
            discovered = discover_module(self.project_root, selected.name)
            self.selected_module = discovered or selected
        else:
            self.selected_module = selected

        self.manual_table_root_selector_type = None
        self.manual_table_root_selector_value = None
        self.manual_table_root_locator_name = None
        self.manual_table_root_warning = None
        self.auto_table_root_selector_type = None
        self.auto_table_root_selector_value = None
        self.auto_table_root_locator_name = None
        self.auto_table_root_warning = None
        self.auto_table_root_candidates = []

        self._cancel_pending_preview(clear_status=False)
        self._refresh_page_classes()
        self._set_status(f"Context loaded: {self.selected_module.name}")
        self._persist_workspace_state()

    def _select_page_in_combo(self, class_name: str) -> bool:
        target = class_name.strip()
        if not target:
            return False
        for index in range(self.page_combo.count()):
            data = self.page_combo.itemData(index)
            if isinstance(data, PageClassInfo) and data.class_name == target:
                self.page_combo.setCurrentIndex(index)
                self.page_combo_previous_index = index
                return True
        return False

    @staticmethod
    def _normalize_url_for_workspace(raw_url: str) -> str:
        text = raw_url.strip()
        if not text:
            return ""
        parsed = urlparse(text)
        if parsed.scheme:
            return text
        return f"https://{text}"

    @staticmethod
    def _status_level(message: str) -> str:
        text = message.lower()
        if any(token in text for token in ("error", "failed", "could not", "invalid")):
            return "error"
        if any(token in text for token in ("warning", "risky", "cancelled")):
            return "warning"
        return "ok"

    def _restore_splitter_sizes(self) -> None:
        if not hasattr(self, "workspace_splitter"):
            return

        saved_sizes = self._settings.value("workspace/splitter_sizes")
        parsed: list[int] = []
        if isinstance(saved_sizes, list):
            for item in saved_sizes[:2]:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    parsed.append(value)
        if len(parsed) == 2:
            self.workspace_splitter.setSizes(parsed)
            return

        left_default = 420
        right_default = max(900, self.width() - left_default)
        self.workspace_splitter.setSizes([left_default, right_default])

    def _save_splitter_sizes(self, *_args) -> None:
        if not hasattr(self, "workspace_splitter"):
            return
        sizes = self.workspace_splitter.sizes()
        if len(sizes) < 2:
            return
        if sizes[0] <= 0 or sizes[1] <= 0:
            return
        self._settings.setValue("workspace/splitter_sizes", [int(sizes[0]), int(sizes[1])])

    def _setup_embedded_browser_bridge(self) -> None:
        web_view = self.browser_panel.web_view
        if web_view is None:
            self._set_status("Embedded browser is unavailable; inspect is disabled.")
            return

        self._embedded_channel = QWebChannel(web_view.page())
        self._embedded_bridge = EmbeddedInspectBridge()
        self._embedded_bridge.payload_received.connect(self._on_embedded_capture_payload)
        self._embedded_bridge.log_received.connect(self._on_embedded_js_log)
        self._embedded_channel.registerObject("inspectBridge", self._embedded_bridge)
        web_view.page().setWebChannel(self._embedded_channel)
        inspect_click_signal = getattr(web_view, "inspect_click", None)
        if inspect_click_signal is not None:
            inspect_click_signal.connect(self._on_embedded_view_click)

        web_view.loadFinished.connect(self._on_webview_load_finished)
        web_view.titleChanged.connect(self._on_webview_title_changed)
        web_view.urlChanged.connect(self._on_webview_url_changed)

    def _refresh_inspect_toggle_state(self) -> None:
        enabled = can_enable_inspect_toggle(
            has_launched_page=self._has_launched_page,
            has_embedded_browser=self.browser_panel.has_web_view,
        )
        self.inspect_toggle.setEnabled(enabled)
        if not enabled:
            self.browser_panel.set_inspect_capture_enabled(False)

    def _run_webview_js(self, script: str, callback=None) -> None:
        web_view = self.browser_panel.web_view
        if web_view is None:
            return
        page = web_view.page()
        if callback is None:
            page.runJavaScript(script)
            return
        page.runJavaScript(script, callback)

    def _ensure_embedded_inspector_script(self) -> None:
        self._run_webview_js(EMBEDDED_INSPECTOR_BOOTSTRAP_SCRIPT)

    def _set_embedded_inspect_mode(self, enabled: bool) -> None:
        bool_literal = "true" if enabled else "false"
        script = (
            f"window.__inspectelementDesiredEnabled = {bool_literal};"
            f"if (window.__inspectelementSetEnabled) window.__inspectelementSetEnabled({bool_literal});"
        )
        self.browser_panel.set_inspect_capture_enabled(enabled)
        self._run_webview_js(script)

    def _on_embedded_js_log(self, message: str) -> None:
        clean = message.strip()
        if not clean:
            return
        self.logger.info(clean)

    def _on_embedded_view_click(self, x: int, y: int) -> None:
        if not self.inspect_toggle.isChecked():
            return
        if self._capture_busy:
            self.logger.info("Capture skipped because busy.")
            return
        if not self._capture_guard.begin():
            self.logger.info("Capture skipped because busy.")
            return

        self._capture_busy = True
        self._capture_seq += 1
        capture_seq = self._capture_seq
        self._capture_active_seq = capture_seq
        self.logger.info("Embedded click received (%s, %s)", x, y)
        self.logger.info("Capture started.")
        script = build_capture_from_point_script(x, y)
        QTimer.singleShot(5000, lambda seq=capture_seq: self._release_stuck_capture(seq))

        try:
            self._run_webview_js(script, callback=lambda result: self._on_embedded_capture_result(result, x, y, capture_seq))
        except Exception:
            self.logger.exception("Embedded capture execution failed.")
            self._capture_busy = False
            self._capture_active_seq = 0
            self._capture_guard.finish()
            self._set_status("Inspect capture could not run.")

    def _release_stuck_capture(self, capture_seq: int) -> None:
        if not self._capture_busy:
            return
        if capture_seq != self._capture_active_seq:
            return
        self.logger.info("Capture ended by timeout fallback.")
        self._capture_busy = False
        self._capture_active_seq = 0
        self._capture_guard.finish()

    def _on_embedded_capture_result(self, result: object, x: int, y: int, capture_seq: int) -> None:
        if capture_seq != self._capture_active_seq:
            return

        def _process() -> None:
            if not isinstance(result, dict):
                self._set_status("Inspect payload was invalid.")
                return

            ok = bool(result.get("ok", False))
            click_info = result.get("click")
            if isinstance(click_info, dict):
                view_x = click_info.get("viewportX")
                view_y = click_info.get("viewportY")
                self.logger.info(
                    "Embedded click payload received (%s, %s) mapped=(%s, %s)",
                    x,
                    y,
                    view_x,
                    view_y,
                )
            else:
                self.logger.info("Embedded click payload received (%s, %s)", x, y)

            if not ok:
                warning = str(result.get("warning") or result.get("error") or "Inspect capture failed.")
                self._set_status(warning)
                return

            payload = {
                "summary": result.get("summary"),
                "candidates": result.get("candidates"),
            }
            self._on_embedded_capture_payload(payload)

        try:
            self._capture_guard.run_and_finish(_process)
        except Exception:
            self.logger.exception("Embedded capture processing failed.")
            self._set_status("Embedded capture processing failed.")
        finally:
            self._capture_busy = False
            self._capture_active_seq = 0
            self.logger.info("Capture ended.")

    def _on_embedded_capture_payload(self, payload: object) -> None:
        self.logger.info("Embedded inspect click payload received.")
        if not isinstance(payload, dict):
            self._set_status("Inspect payload was invalid.")
            return

        summary_payload = payload.get("summary")
        candidates_payload = payload.get("candidates")
        if not isinstance(summary_payload, dict) or not isinstance(candidates_payload, list):
            self._set_status("Inspect payload is missing summary/candidates.")
            return

        try:
            summary = build_element_summary_from_payload(summary_payload)
            learning_weights = self.learning_store.get_rule_weights()
            enriched_payload = list(candidates_payload)
            enriched_payload.extend(build_fallback_locator_payload(summary_payload))
            candidates = build_locator_candidates_from_payload(
                enriched_payload,
                learning_weights=learning_weights,
                limit=6,
            )

            page_context = self._build_page_context_from_browser_panel()
            self.current_page_context = page_context
            if page_context is not None:
                override = self.learning_store.get_override(page_context.hostname, summary.signature())
                if override and not is_obvious_root_container_locator(override.locator):
                    override_candidate = build_override_candidate(
                        override=override,
                        uniqueness_count=1,
                        learning_weights=learning_weights,
                    )
                    candidates = inject_override_candidate(candidates, override_candidate, limit=6)

            self._on_capture(summary, candidates)
        except Exception as exc:
            self._handle_ui_exception("Embedded inspect payload could not be processed.", exc)

    def _build_page_context_from_browser_panel(self) -> PageContext | None:
        current_url = self.browser_panel.current_url.strip()
        if not current_url:
            return None
        parsed = urlparse(current_url)
        return PageContext(
            url=current_url,
            hostname=parsed.hostname or "",
            page_title=self.browser_panel.current_title.strip(),
        )

    def _on_webview_load_finished(self, ok: bool) -> None:
        if not ok:
            self._has_launched_page = False
            self._refresh_inspect_toggle_state()
            self._set_status("Embedded browser could not load the URL.")
            return

        self._has_launched_page = True
        self._on_page_changed(self.browser_panel.current_title, self.browser_panel.current_url)
        self._ensure_embedded_inspector_script()
        self._refresh_inspect_toggle_state()
        self._set_status("Embedded browser loaded.")

        desired = bool(self.inspect_toggle.isChecked()) or bool(self._pending_inspect_restore)
        self._set_embedded_inspect_mode(desired)
        self._pending_inspect_restore = False

    def _on_webview_title_changed(self, title: str) -> None:
        self.browser_panel.set_page_info(title or "", self.browser_panel.current_url)
        self._on_page_changed(title or "", self.browser_panel.current_url)

    def _on_webview_url_changed(self, url: QUrl) -> None:
        url_text = url.toString().strip()
        self.browser_panel.set_page_info(self.browser_panel.current_title, url_text)
        self.url_input.setText(url_text)
        self._persist_workspace_state()

    def _fit_window_to_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if not screen:
            self.resize(1240, 760)
            return

        available = screen.availableGeometry()
        target_width = max(1120, min(1600, available.width() - 36))
        target_height = max(680, min(920, available.height() - 56))
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
                font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
                font-size: 13px;
            }
            QMainWindow {
                background: #f3f5f9;
            }
            QFrame#TopBar, QFrame#BottomStatusBar, QFrame#BrowserPanel, QFrame#LeftPanel {
                background: #ffffff;
                border: 1px solid #d7dfed;
                border-radius: 12px;
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
            QLabel#StatusPill {
                min-width: 72px;
                border-radius: 11px;
                padding: 5px 10px;
                font-weight: 700;
                color: #ffffff;
                background: #0284c7;
            }
            QLabel#StatusPill[level="ok"] {
                background: #15803d;
            }
            QLabel#StatusPill[level="warning"] {
                background: #b45309;
            }
            QLabel#StatusPill[level="error"] {
                background: #b91c1c;
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
                selection-background-color: #eaf2ff;
                selection-color: #0f172a;
            }
            QTableWidget::item:hover {
                background: transparent;
            }
            QTableWidget::item:selected {
                background: #eaf2ff;
                color: #0f172a;
            }
            QTableWidget::item:selected:active {
                background: #eaf2ff;
                color: #0f172a;
            }
            QTableWidget::item:selected:!active {
                background: #eaf2ff;
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
            QLabel#TableRootWarning {
                color: #b91c1c;
                font-weight: 600;
            }
            QLabel#GuidanceBadge {
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 1px 6px;
                color: #334155;
                background: #f8fafc;
                font-size: 11px;
            }
            QLabel#GuidanceBadge[kind="recommended"] {
                border-color: #86efac;
                color: #166534;
                background: #f0fdf4;
            }
            QLabel#GuidanceBadge[kind="risky"] {
                border-color: #fecaca;
                color: #991b1b;
                background: #fef2f2;
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
        add_row.addWidget(QLabel("Action"))
        add_row.addWidget(self.action_dropdown, 1)
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
        candidates_label = QLabel("Auto candidates")
        candidates_label.setObjectName("Muted")
        self.table_root_candidates_label = candidates_label
        self.table_root_candidates_combo.setVisible(False)
        self.table_root_candidates_label.setVisible(False)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.table_root_locator_preview, 1)
        row.addWidget(self.pick_table_root_button)
        row.addWidget(self.clear_table_root_button)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.table_root_candidates_label)
        layout.addWidget(self.table_root_candidates_combo)
        layout.addLayout(row)
        layout.addWidget(self.table_root_warning_label)
        self.table_root_warning_label.setVisible(False)
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
        self._add_selected_dropdown_action(trigger="combo_activated")

    def _add_selected_dropdown_action(self, trigger: str = "button_click") -> None:
        action_key = self.action_dropdown.currentData()
        if not isinstance(action_key, str):
            return
        updated = add_action_by_trigger(self.selected_actions, action_key, trigger=trigger)
        if updated == self.selected_actions:
            return
        self._set_selected_actions(updated)
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
        self.table_root_warning_label.setVisible(False)
        if not needs_table:
            self.table_root_candidates_combo.setVisible(False)
            self.table_root_candidates_label.setVisible(False)
            return

        show_candidates = bool(self.auto_table_root_candidates and len(self.auto_table_root_candidates) > 1)
        if self.manual_table_root_selector_type and self.manual_table_root_selector_value:
            show_candidates = False
        self.table_root_candidates_combo.setVisible(show_candidates)
        self.table_root_candidates_label.setVisible(show_candidates)
        selector = self._selected_table_root_selector()
        if selector:
            selector_type, selector_value = selector
            self.table_root_locator_preview.setText(f"{selector_type}: {selector_value}")
        else:
            self.table_root_locator_preview.setText("Table root could not be detected.")
        warning = self._selected_table_root_warning()
        if warning:
            self.table_root_warning_label.setText(warning)
            self.table_root_warning_label.setVisible(True)

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

    def _selected_table_root_warning(self) -> str | None:
        if self.manual_table_root_warning:
            return self.manual_table_root_warning
        return self.auto_table_root_warning

    def _set_auto_table_root_from_summary(self, summary: ElementSummary | None) -> None:
        if not summary:
            self.auto_table_root_selector_type = None
            self.auto_table_root_selector_value = None
            self.auto_table_root_locator_name = None
            self.auto_table_root_warning = None
            self.auto_table_root_candidates = []
            self.table_root_candidates_combo.clear()
            self.table_root_candidates_combo.setVisible(False)
            self.table_root_candidates_label.setVisible(False)
            self._refresh_table_root_section()
            return

        candidates = list(summary.table_roots)
        if not candidates and summary.table_root:
            candidates = [summary.table_root]
        self.auto_table_root_candidates = candidates
        if not candidates:
            self.auto_table_root_selector_type = None
            self.auto_table_root_selector_value = None
            self.auto_table_root_locator_name = None
            self.auto_table_root_warning = None
            self.table_root_candidates_combo.clear()
            self.table_root_candidates_combo.setVisible(False)
            self.table_root_candidates_label.setVisible(False)
            self._refresh_table_root_section()
            return

        self.table_root_candidates_combo.blockSignals(True)
        self.table_root_candidates_combo.clear()
        for index, candidate in enumerate(candidates, start=1):
            selector_type = candidate.get("selector_type", "?")
            selector_value = candidate.get("selector_value", "?")
            reason = candidate.get("reason", "-")
            label = f"{index}. {selector_type}: {selector_value} ({reason})"
            self.table_root_candidates_combo.addItem(label, candidate)
        self.table_root_candidates_combo.blockSignals(False)
        self.table_root_candidates_combo.setVisible(len(candidates) > 1)
        self.table_root_candidates_label.setVisible(len(candidates) > 1)

        self.table_root_candidates_combo.setCurrentIndex(0)
        self._on_table_root_candidate_changed(0)
        self._refresh_table_root_section()

    def _set_manual_table_root(self, selector_type: str, selector_value: str, locator_name: str | None = None) -> None:
        self.manual_table_root_selector_type = selector_type
        self.manual_table_root_selector_value = selector_value
        if locator_name:
            self.manual_table_root_locator_name = locator_name
        else:
            self.manual_table_root_locator_name = self._selected_table_root_locator_name()
        if selector_type == "xpath":
            self.manual_table_root_warning = "Warning: unstable table root locator (xpath)."
        else:
            self.manual_table_root_warning = None
        self._cancel_pending_preview(clear_status=False)
        self._refresh_table_root_section()

    def _clear_manual_table_root(self) -> None:
        self.manual_table_root_selector_type = None
        self.manual_table_root_selector_value = None
        self.manual_table_root_locator_name = None
        self.manual_table_root_warning = None
        self.pick_table_root_mode = False
        self._cancel_pending_preview(clear_status=False)
        self._set_status("Manual table root override cleared.")
        self._refresh_table_root_section()
        self._update_generated_methods_preview()

    def _on_table_root_candidate_changed(self, _index: int) -> None:
        candidate = self.table_root_candidates_combo.currentData()
        if not isinstance(candidate, dict):
            return
        selector_type = candidate.get("selector_type")
        selector_value = candidate.get("selector_value")
        locator_name_hint = candidate.get("locator_name_hint")
        warning = candidate.get("warning", "").strip() or None
        if selector_type and selector_value:
            self.auto_table_root_selector_type = str(selector_type)
            self.auto_table_root_selector_value = str(selector_value)
            self.auto_table_root_locator_name = str(locator_name_hint or self._selected_table_root_locator_name())
            self.auto_table_root_warning = warning
            if len(self.auto_table_root_candidates) > 1 and not self.auto_table_root_warning:
                self.auto_table_root_warning = "Warning: multiple table root candidates available."
        else:
            self.auto_table_root_selector_type = None
            self.auto_table_root_selector_value = None
            self.auto_table_root_locator_name = None
            self.auto_table_root_warning = None
        self._cancel_pending_preview(clear_status=False)
        self._refresh_table_root_section()

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
        self._cancel_pending_preview(clear_status=False)
        self._update_add_button_state()

    def _update_generated_methods_preview(self) -> None:
        actions = self._selected_actions()
        base_locator_name = self.element_name_input.text().strip() or "ELEMENT"
        self.locator_constant_input.setText(self.preview_locator_name_override or base_locator_name)
        if not actions:
            self.generated_methods_preview.setPlainText("No actions selected.")
            return

        page = self._selected_page_class()
        page_class_name = page.class_name if page else "PageClass"
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
        self._set_status("Use top bar Project/Module selectors to change context.")

    def _ensure_context_selected(self, initial: bool) -> bool:
        # Context is selected inline in top bar; this method remains for backward compatibility.
        if self.selected_module is not None:
            return True
        if initial:
            self._set_status("Select project and module from the top bar.")
        return False

    def _refresh_page_classes(self) -> None:
        self._reset_generated_preview_override()
        previous_selected = self._selected_page_class()
        previous_class_name = previous_selected.class_name if previous_selected else ""

        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        self.page_combo.addItem("Select page class", None)
        self.page_combo_previous_index = 0

        if not self.selected_module:
            self.discovered_pages = []
            self.page_combo.setEnabled(False)
            self.new_page_button.setEnabled(
                can_enable_new_page_button(
                    has_project_root=bool(self.project_root),
                    has_module=False,
                    has_pages_source_root=False,
                )
            )
            self.page_combo.blockSignals(False)
            self._update_add_button_state()
            return

        pages = discover_page_classes(self.selected_module)
        self.discovered_pages = pages
        for page in pages:
            self.page_combo.addItem(page.class_name, page)

        can_create_page = can_enable_new_page_button(
            has_project_root=bool(self.project_root),
            has_module=True,
            has_pages_source_root=bool(self.selected_module.pages_source_root),
        )
        if can_create_page:
            self.page_combo.addItem(self.CREATE_PAGE_COMBO_LABEL, self.CREATE_PAGE_COMBO_TOKEN)

        self.new_page_button.setEnabled(can_create_page)
        self.page_combo.setEnabled(bool(pages) or can_create_page)
        if pages:
            selected_index = 1
            if previous_class_name:
                for index in range(1, self.page_combo.count()):
                    data = self.page_combo.itemData(index)
                    if isinstance(data, PageClassInfo) and data.class_name == previous_class_name:
                        selected_index = index
                        break
            self.page_combo.setCurrentIndex(selected_index)
            self.page_combo_previous_index = selected_index
        else:
            self.page_combo.setCurrentIndex(0)
            self.page_combo_previous_index = 0
        self.page_combo.blockSignals(False)
        self._update_add_button_state()
        self._persist_workspace_state()

    def _selected_page_class(self) -> PageClassInfo | None:
        selected = self.page_combo.currentData()
        if isinstance(selected, PageClassInfo):
            return selected
        return None

    def _on_page_combo_changed(self, _index: int) -> None:
        if self.page_combo.currentData() == self.CREATE_PAGE_COMBO_TOKEN:
            self._create_new_page_flow()
            return
        self.page_combo_previous_index = self.page_combo.currentIndex()
        self._cancel_pending_preview(clear_status=False)
        self._update_add_button_state()
        self._persist_workspace_state()

    def _create_new_page_flow(self) -> None:
        self.logger.info("New Page handler invoked.")
        restore_selection = self.page_combo.currentData() == self.CREATE_PAGE_COMBO_TOKEN
        if not self.selected_module:
            self._set_status("Select module before creating page.")
            self._show_toast("Select module first")
            if restore_selection:
                self._restore_page_combo_selection()
            return

        raw_page_name, ok_pressed = QInputDialog.getText(
            self,
            "Create New Page",
            "Page Name (PascalCase):",
            text="",
        )
        if not ok_pressed:
            self._set_status("Cancelled — no page created.")
            if restore_selection:
                self._restore_page_combo_selection()
            return

        preview = generate_page_creation_preview(
            module=self.selected_module,
            existing_pages=self.discovered_pages,
            page_name_raw=raw_page_name.strip(),
        )
        if not preview.ok:
            self._set_status(preview.message)
            QMessageBox.warning(self, "Create New Page", preview.message)
            if restore_selection:
                self._restore_page_combo_selection()
            return

        applied = self._show_page_creation_preview(preview)
        if not applied:
            if restore_selection:
                self._restore_page_combo_selection()
            return

        created_class_name = preview.class_name
        self._refresh_page_classes()
        if created_class_name:
            self._select_page_in_combo(created_class_name)
        self._update_add_button_state()
        self._persist_workspace_state()

    def _show_page_creation_preview(self, preview: PageCreationPreview) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle("Preview New Page")
        dialog.resize(900, 640)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        package_value = preview.package_name or "-"
        info = QLabel(f"Package: {package_value}\nTarget: {preview.target_file}")
        info.setObjectName("Muted")
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(info)

        content_label = QLabel("Generated File")
        content_label.setObjectName("SectionTitle")
        root.addWidget(content_label)

        file_preview = QPlainTextEdit()
        file_preview.setReadOnly(True)
        file_preview.setPlainText(preview.file_content or "")
        file_preview.setMinimumHeight(220)
        root.addWidget(file_preview, 1)

        diff_label = QLabel("Unified Diff Preview")
        diff_label.setObjectName("SectionTitle")
        root.addWidget(diff_label)

        diff_preview = QPlainTextEdit()
        diff_preview.setReadOnly(True)
        diff_preview.setPlainText(preview.diff_text or "")
        diff_preview.setMinimumHeight(180)
        root.addWidget(diff_preview, 1)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        create_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        if create_button is not None:
            create_button.setText("Create Page")
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        root.addWidget(button_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_status("Cancelled — no page created.")
            return False

        applied, message = apply_page_creation_preview(preview)
        self._set_status(message)
        self._show_toast(message)
        if not applied:
            QMessageBox.warning(self, "Create New Page", message)
            return False
        return True

    def _restore_page_combo_selection(self) -> None:
        target_index = self.page_combo_previous_index
        if target_index < 0 or target_index >= self.page_combo.count():
            target_index = 0
        self.page_combo.blockSignals(True)
        self.page_combo.setCurrentIndex(target_index)
        self.page_combo.blockSignals(False)
        self._update_add_button_state()

    def _update_add_button_state(self) -> None:
        self._update_generated_methods_preview()
        has_page = self._selected_page_class() is not None
        has_locator = self._selected_candidate() is not None
        has_name = bool(self.element_name_input.text().strip())
        button_state = compute_workspace_button_state(
            has_page=has_page,
            has_locator=has_locator,
            has_name=has_name,
            has_pending_preview=self.pending_java_preview is not None,
        )
        self.add_button.setEnabled(button_state.can_preview)
        self.validate_button.setEnabled(button_state.can_validate)
        self.apply_button.setEnabled(button_state.can_apply)
        self.cancel_preview_button.setEnabled(button_state.can_cancel_preview)
        if not (has_page and has_locator and has_name):
            self.payload_status_label.setText("Waiting for page, locator, and element name.")
        else:
            if self.pending_java_preview is None:
                self._refresh_payload_status("Payload ready. Click Add -> Preview.")

    def _selected_actions(self) -> list[str]:
        return list(self.selected_actions)

    def _on_action_selection_changed(self) -> None:
        self._cancel_pending_preview(clear_status=False)
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
            preview = self._generate_preview_for_current_request()
            if not preview.ok:
                self.pending_java_preview = None
                self._clear_java_preview_dock()
                self._set_status(preview.message)
                self.payload_status_label.setText(preview.message)
                self._show_toast(preview.message)
                self._update_add_button_state()
                return

            self.pending_java_preview = preview
            self._set_status(preview.message)
            self.payload_status_label.setText(preview.message)
            self.preview_locator_name_override = preview.final_locator_name
            self.preview_signatures_override = list(preview.added_method_signatures)
            self.preview_signatures_actions_snapshot = tuple(self._selected_actions())
            self._render_java_preview_dock(preview)
            self._update_generated_methods_preview()
            self._update_add_button_state()
            self._persist_workspace_state()
            return
        except Exception as exc:
            self.pending_java_preview = None
            self._clear_java_preview_dock()
            self._handle_ui_exception("Unexpected error during preview/apply. See ~/.inspectelement/ui.log.", exc)

    def _apply_pending_preview(self) -> None:
        preview = self.pending_java_preview
        if preview is None:
            self._set_status("No pending preview to apply.")
            return

        try:
            applied, message, _backup_path = apply_java_preview(preview)
            self.pending_java_preview = None
            self._clear_java_preview_dock()
            self._reset_generated_preview_override()
            self._set_status(message)
            self.payload_status_label.setText(message)
            self._show_toast(message)
            self._update_generated_methods_preview()
            self._update_add_button_state()
            self._persist_workspace_state()
            if not applied:
                return
        except Exception as exc:
            self.pending_java_preview = None
            self._clear_java_preview_dock()
            self._handle_ui_exception("Unexpected error during apply. See ~/.inspectelement/ui.log.", exc)

    def _cancel_pending_preview(self, clear_status: bool = True) -> None:
        had_preview = self.pending_java_preview is not None
        self.pending_java_preview = None
        self._clear_java_preview_dock()
        self._reset_generated_preview_override()
        if had_preview and clear_status:
            self._set_status("Cancelled — no changes.")
            self.payload_status_label.setText("Cancelled — no changes.")
            self._show_toast("Cancelled — no changes.")
        self._update_generated_methods_preview()
        self._update_add_button_state()

    def _validate_only_request(self) -> None:
        try:
            preview = self._generate_preview_for_current_request()
            if not preview.ok:
                self._set_status(preview.message)
                self.payload_status_label.setText(preview.message)
                self._show_toast(preview.message)
                return
            self._set_status("Validation successful. Preview can be generated and applied safely.")
            self.payload_status_label.setText("Validation successful. No files written.")
            self._show_toast("Validation successful")
        except Exception as exc:
            self._handle_ui_exception("Validation failed unexpectedly. See ~/.inspectelement/ui.log.", exc)

    def _generate_preview_for_current_request(self) -> JavaPreview:
        validation_error = self._validate_current_request()
        if validation_error:
            return JavaPreview(
                ok=False,
                target_file=Path("."),
                message=validation_error,
                diff_text="",
                final_locator_name=None,
                added_methods=(),
                added_method_signatures=(),
                original_source=None,
                updated_source=None,
                notes=(),
            )

        page = self._selected_page_class()
        candidate = self._selected_candidate()
        assert page is not None
        assert candidate is not None

        selector_details = self._resolve_java_selector(candidate)
        if not selector_details:
            return JavaPreview(
                ok=False,
                target_file=page.file_path,
                message="Selected locator type cannot be written to Java. Choose CSS/XPath/Selenium.",
                diff_text="",
                final_locator_name=None,
                added_methods=(),
                added_method_signatures=(),
                original_source=None,
                updated_source=None,
                notes=(),
            )

        selector_type, selector_value = selector_details
        selected_table_root = self._selected_table_root_selector()
        return generate_java_preview(
            target_file=page.file_path,
            locator_name=self.element_name_input.text().strip(),
            selector_type=selector_type,
            selector_value=selector_value,
            actions=self._selected_actions(),
            log_language=self.log_language_combo.currentText(),
            action_parameters=self._collect_action_parameters(),
            table_root_selector_type=selected_table_root[0] if selected_table_root else None,
            table_root_selector_value=selected_table_root[1] if selected_table_root else None,
            table_root_locator_name=self._selected_table_root_locator_name() if selected_table_root else None,
        )

    def _validate_current_request(self) -> str | None:
        page = self._selected_page_class()
        candidate = self._selected_candidate()
        actions = self._selected_actions()
        action_parameters = self._collect_action_parameters()
        has_table_root = self._selected_table_root_selector() is not None

        result = validate_generation_request(
            has_page=page is not None,
            has_locator=candidate is not None,
            element_name=self.element_name_input.text().strip(),
            actions=actions,
            action_parameters=action_parameters,
            has_table_root=has_table_root,
        )
        if not result.ok:
            return result.message
        return None

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
        normalized = self._normalize_url_for_workspace(self.url_input.text())
        if not normalized:
            self._set_status("Please enter a URL.")
            return
        if not self.browser_panel.has_web_view:
            self._set_status("Embedded browser is not available in this runtime.")
            return
        self.url_input.setText(normalized)
        self._has_launched_page = False
        self._capture_busy = False
        self._capture_active_seq = 0
        self._capture_guard.finish()
        desired_after_load = bool(self.inspect_toggle.isChecked()) or bool(self._pending_inspect_restore)
        self._pending_inspect_restore = desired_after_load
        self.inspect_toggle.blockSignals(True)
        self.inspect_toggle.setChecked(desired_after_load)
        self.inspect_toggle.blockSignals(False)
        self.inspect_toggle.setText(f"Inspect: {'ON' if desired_after_load else 'OFF'}")
        self._refresh_inspect_toggle_state()
        self.browser_panel.load_url(normalized)
        self._set_status(f"Launching: {normalized}")
        self._persist_workspace_state()

    def _toggle_inspect(self) -> None:
        enabled = self.inspect_toggle.isChecked()
        self.logger.info("Inspect toggle changed: %s", "ON" if enabled else "OFF")
        self.inspect_toggle.setText(f"Inspect: {'ON' if enabled else 'OFF'}")
        if not self.inspect_toggle.isEnabled():
            self._set_status("Launch a page first.")
            return
        if not enabled:
            self._capture_busy = False
            self._capture_active_seq = 0
            self._capture_guard.finish()
        self._ensure_embedded_inspector_script()
        self._set_embedded_inspect_mode(enabled)
        self._persist_workspace_state()

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
        self.learning_store.reset()
        self._set_status("Learning store reset.")
        self._show_toast("Learning reset")

    def _clear_overrides(self) -> None:
        self.learning_store.clear_overrides()
        self._set_status("Overrides cleared.")
        self._show_toast("Overrides temizlendi")

    def _feedback(self, was_good: bool) -> None:
        candidate = self._selected_candidate()
        if not candidate:
            self._set_status("Select a locator first.")
            self._show_toast("Once bir locator sec")
            return
        if not self.current_summary:
            self._set_status("Capture an element before sending feedback.")
            self._show_toast("Once element secimi yap")
            return

        page_context = self._build_page_context_from_browser_panel()
        if not page_context:
            self._set_status("Launch a page before sending feedback.")
            return
        self.current_page_context = page_context
        self.learning_store.record_feedback(page_context, self.current_summary, candidate, was_good)
        self._set_status("Feedback recorded.")
        self._show_toast("Feedback eklendi")

    def _good_edited(self) -> None:
        candidate = self._selected_candidate()
        if not candidate:
            self._set_status("Select a locator first.")
            self._show_toast("Once bir locator sec")
            return

        edited = self.locator_editor.toPlainText().strip()
        if not edited:
            self._set_status("Edited locator is empty.")
            self._show_toast("Editor bos")
            return
        if is_obvious_root_container_locator(edited):
            message = "Root container locators cannot be saved as overrides."
            self._set_status(message)
            self._show_toast(message)
            return
        if not self.current_summary:
            self._set_status("Capture an element before saving override.")
            return
        page_context = self._build_page_context_from_browser_panel()
        if not page_context:
            self._set_status("Launch a page before saving override.")
            return

        feedback_candidate = replace(candidate, locator=edited)
        self.learning_store.record_feedback(page_context, self.current_summary, feedback_candidate, True)
        self.learning_store.save_override(
            page_context.hostname,
            self.current_summary.signature(),
            feedback_candidate.locator_type,
            edited,
        )
        self._set_status("Edited locator saved as override.")
        self._show_toast("Edited locator kaydedildi")

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
        self._cancel_pending_preview(clear_status=False)
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
                elif summary.table_roots:
                    locator_name_hint = summary.table_roots[0].get("locator_name_hint")
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
        if hasattr(self, "browser_panel"):
            self.browser_panel.set_page_info(title, url)
        if url and hasattr(self, "url_input"):
            self.url_input.setText(url)
        self.current_page_context = self._build_page_context_from_browser_panel()
        if hasattr(self, "inspect_toggle"):
            self._refresh_inspect_toggle_state()
        self._persist_workspace_state()

    def _set_status(self, message: str) -> None:
        normalized = message.strip()
        if not normalized:
            normalized = "-"
        level = self._status_level(normalized)
        if hasattr(self, "top_bar"):
            self.top_bar.set_status_pill(level)
        if hasattr(self, "bottom_status_bar"):
            self.bottom_status_bar.set_last_action(normalized)
            lowered = normalized.lower()
            if any(token in lowered for token in ("warning", "risky", "unstable")):
                self.bottom_status_bar.set_warning(normalized)
            else:
                self.bottom_status_bar.set_warning("-")
            if any(token in lowered for token in ("applied.", "backup", "no files written", "no changes")):
                self.bottom_status_bar.set_write_result(normalized)

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
            rank_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.results_table.setItem(row, 0, rank_item)
            self.results_table.setItem(row, 1, type_item)
            self.results_table.setItem(row, 3, score_item)
            guidance_host = QWidget()
            guidance_layout = QHBoxLayout(guidance_host)
            guidance_layout.setContentsMargins(4, 2, 4, 2)
            guidance_layout.setSpacing(0)
            guidance_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            guidance_badge = QLabel(guidance_text if guidance_text != "-" else "")
            guidance_badge.setObjectName("GuidanceBadge")
            if guidance_text == "Recommended":
                guidance_badge.setProperty("kind", "recommended")
            elif guidance_text == "Risky":
                guidance_badge.setProperty("kind", "risky")
            else:
                guidance_badge.setProperty("kind", "neutral")
            guidance_badge.style().unpolish(guidance_badge)
            guidance_badge.style().polish(guidance_badge)
            guidance_layout.addWidget(guidance_badge)
            self.results_table.setCellWidget(row, 4, guidance_host)

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
        self._cancel_pending_preview(clear_status=False)
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
            self._save_splitter_sizes()
            self._persist_workspace_state()
        except Exception as exc:
            QMessageBox.warning(self, "Shutdown warning", str(exc))
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._update_locator_text_elide()
        if self.toast_label.isVisible():
            self._position_toast()
        super().resizeEvent(event)


MainWindow = WorkspaceWindow
