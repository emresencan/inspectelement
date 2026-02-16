from __future__ import annotations

import logging
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QPoint, QRect, QSize, QTimer, Qt, Signal, QEvent
from PySide6.QtGui import QCloseEvent, QColor, QGuiApplication, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
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
    QSplitter,
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
from .java_pom_writer import (
    JavaPreview,
    apply_java_preview,
    generate_java_preview,
)
from .locator_recommendation import recommend_locator_candidates
from .models import ElementSummary, LocatorCandidate
from .name_suggester import suggest_element_name
from .page_creator import (
    PageCreationPreview,
    apply_page_creation_preview,
    generate_page_creation_preview,
)
from .project_discovery import ModuleInfo, PageClassInfo, discover_page_classes
from .project_discovery import discover_modules
from .ui_state import (
    WorkspaceConfig,
    can_enable_inspect,
    can_enable_new_page,
    compute_enable_state,
    load_workspace_config,
    save_workspace_config,
)
from .validation import validate_generation_request

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover - environment-dependent
    QWebEngineView = None  # type: ignore[assignment]


class EventBridge(QObject):
    capture_received = Signal(object, object)
    status_changed = Signal(str)
    page_changed = Signal(str, str)
    hover_box_changed = Signal(object)


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
    def __init__(
        self,
        *,
        project_input: QLineEdit,
        project_browse_button: QPushButton,
        module_combo: QComboBox,
        page_combo: QComboBox,
        new_page_button: QPushButton,
        url_input: QLineEdit,
        launch_button: QPushButton,
        inspect_toggle: QPushButton,
        toggle_left_button: QPushButton,
        open_managed_button: QPushButton,
        validate_button: QPushButton,
        preview_button: QPushButton,
        apply_button: QPushButton,
        cancel_button: QPushButton,
        status_pill: QLabel,
    ) -> None:
        super().__init__()
        self.setObjectName("Card")

        layout = QGridLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        layout.addWidget(QLabel("Project"), 0, 0)
        layout.addWidget(project_input, 0, 1, 1, 4)
        layout.addWidget(project_browse_button, 0, 5)

        layout.addWidget(QLabel("Module"), 0, 6)
        layout.addWidget(module_combo, 0, 7)
        layout.addWidget(QLabel("Page"), 0, 8)
        layout.addWidget(page_combo, 0, 9)
        layout.addWidget(new_page_button, 0, 10)

        layout.addWidget(QLabel("URL"), 1, 0)
        layout.addWidget(url_input, 1, 1, 1, 5)
        layout.addWidget(launch_button, 1, 6)
        layout.addWidget(inspect_toggle, 1, 7)
        layout.addWidget(toggle_left_button, 1, 8)
        layout.addWidget(open_managed_button, 1, 9)
        layout.addWidget(validate_button, 1, 10)
        layout.addWidget(preview_button, 1, 11)
        layout.addWidget(apply_button, 1, 12)
        layout.addWidget(cancel_button, 1, 13)
        layout.addWidget(status_pill, 1, 14)

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(9, 1)
        layout.setColumnMinimumWidth(14, 150)


class InspectWebEngineView(QWebEngineView if QWebEngineView is not None else QWidget):
    inspect_click = Signal(float, float)
    inspect_hover = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._inspect_mode = False
        self.setMouseTracking(True)

    def set_inspect_mode(self, enabled: bool) -> None:
        self._inspect_mode = bool(enabled)
        self.setCursor(Qt.CursorShape.CrossCursor if self._inspect_mode else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if self._inspect_mode and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            self.inspect_click.emit(float(pos.x()), float(pos.y()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if self._inspect_mode and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if self._inspect_mode and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if self._inspect_mode:
            pos = event.position()
            self.inspect_hover.emit(float(pos.x()), float(pos.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)


class BrowserPanel(QFrame):
    inspect_capture = Signal(dict)
    inspect_hover_probe = Signal(dict)
    page_changed = Signal(str, str)
    load_finished = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.webview = None
        self._page_title = ""
        self._page_url = ""
        self._inspect_enabled = False
        self._show_clickable_target_outline = False
        self._filtered_widgets: list[QWidget] = []
        self._selection_overlay: QFrame | None = None
        self._clickable_overlay: QFrame | None = None
        self._click_marker: QFrame | None = None
        self._hover_last_point: tuple[int, int] | None = None
        self._hover_pending_point: tuple[float, float] | None = None
        self._hover_probe_interval_ms = 45
        self._hover_probe_timer = QTimer(self)
        self._hover_probe_timer.setSingleShot(True)
        self._hover_probe_timer.setInterval(self._hover_probe_interval_ms)
        self._hover_probe_timer.timeout.connect(self._dispatch_hover_probe)
        self._click_marker_timer = QTimer(self)
        self._click_marker_timer.setSingleShot(True)
        self._click_marker_timer.setInterval(280)
        self._click_marker_timer.timeout.connect(self._hide_click_marker)
        self.info_label = QLabel(
            "Embedded browser view. Inspect capture uses managed Chromium session for locator extraction."
        )
        self.info_label.setObjectName("Muted")
        self.info_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Browser"))
        layout.addWidget(self.info_label)

        if QWebEngineView is not None:
            try:
                self.webview = InspectWebEngineView(self)
                layout.addWidget(self.webview, 1)
                self._wire_webview()
            except Exception:
                self.webview = None

        if self.webview is None:
            fallback = QPlainTextEdit()
            fallback.setReadOnly(True)
            fallback.setPlainText(
                "Qt WebEngine is not available in this environment.\n"
                "Managed Chromium window will still be used for Inspect mode."
            )
            layout.addWidget(fallback, 1)
        else:
            self._install_webview_event_filters()

    def load_url(self, url: str) -> None:
        if not self.webview:
            return
        from PySide6.QtCore import QUrl

        self.webview.load(QUrl(url))

    def set_inspector_enabled(self, enabled: bool) -> None:
        self._inspect_enabled = bool(enabled)
        if not self.webview:
            return
        if enabled:
            self._install_webview_event_filters()
        self.webview.set_inspect_mode(enabled)
        if not enabled:
            self._hover_probe_timer.stop()
            self._hover_pending_point = None
            self._hide_selection_overlay()
            self._hide_click_marker()

    def _wire_webview(self) -> None:
        if not self.webview:
            return
        page = self.webview.page()
        page.loadFinished.connect(self._on_load_finished)
        self.webview.titleChanged.connect(self._on_title_changed)
        self.webview.urlChanged.connect(self._on_url_changed)
        self.webview.inspect_click.connect(self._on_inspect_click)
        self.webview.inspect_hover.connect(self._on_inspect_hover)
        self._install_webview_event_filters()
        self._ensure_selection_overlay()
        self._ensure_clickable_overlay()
        self._ensure_click_marker()

    def _on_load_finished(self, ok: bool) -> None:
        self.load_finished.emit(bool(ok))
        self._install_webview_event_filters()
        self._hover_probe_timer.stop()
        self._hover_pending_point = None
        self._hide_selection_overlay()
        self._hide_click_marker()
        if ok and self._inspect_enabled:
            self.set_inspector_enabled(True)

    def _on_title_changed(self, title: str) -> None:
        self._page_title = title or ""
        self.page_changed.emit(self._page_title, self._page_url)

    def _on_url_changed(self, url) -> None:
        self._page_url = url.toString()
        self.page_changed.emit(self._page_title, self._page_url)

    def _on_inspect_click(self, x: float, y: float) -> None:
        if not self._inspect_enabled or not self.webview:
            return
        self._install_webview_event_filters()
        self._emit_inspect_payload(x, y, self.inspect_capture)
        self._emit_inspect_payload(x, y, self.inspect_hover_probe)
        self._show_click_marker(x, y)

    def _on_inspect_hover(self, x: float, y: float) -> None:
        if not self._inspect_enabled:
            return
        point = (int(round(x)), int(round(y)))
        if point == self._hover_last_point:
            return
        self._hover_last_point = point
        self._hover_pending_point = (x, y)
        if not self._hover_probe_timer.isActive():
            self._hover_probe_timer.start()

    def eventFilter(self, watched, event):  # noqa: N802 (Qt API)
        if not self._inspect_enabled or not self.webview:
            return super().eventFilter(watched, event)

        event_type = event.type()
        mouse_events = (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseMove,
        )
        if event_type not in mouse_events:
            return super().eventFilter(watched, event)
        if not isinstance(event, QMouseEvent):
            return super().eventFilter(watched, event)
        if event_type != QEvent.Type.MouseMove and event.button() != Qt.MouseButton.LeftButton:
            return super().eventFilter(watched, event)

        if not isinstance(watched, QWidget):
            return super().eventFilter(watched, event)
        if watched is not self.webview and not self.webview.isAncestorOf(watched):
            return super().eventFilter(watched, event)
        local_pos = watched.mapTo(self.webview, event.position().toPoint())
        if event_type == QEvent.Type.MouseButtonPress:
            self._on_inspect_click(float(local_pos.x()), float(local_pos.y()))
        elif event_type == QEvent.Type.MouseMove:
            self._on_inspect_hover(float(local_pos.x()), float(local_pos.y()))
        event.accept()
        return True

    def viewport_size(self) -> tuple[int, int]:
        if not self.webview:
            return 1280, 720
        return max(1, self.webview.width()), max(1, self.webview.height())

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        self._clear_webview_event_filters()
        super().closeEvent(event)

    def _install_webview_event_filters(self) -> None:
        if not self.webview:
            return
        widgets: list[QWidget] = [self.webview]
        widgets.extend(self.webview.findChildren(QWidget))
        for widget in widgets:
            if widget in self._filtered_widgets:
                continue
            widget.installEventFilter(self)
            self._filtered_widgets.append(widget)

    def _clear_webview_event_filters(self) -> None:
        for widget in self._filtered_widgets:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:
                continue
        self._filtered_widgets.clear()

    def _ensure_selection_overlay(self) -> None:
        if not self.webview or self._selection_overlay:
            return
        overlay = QFrame(self.webview)
        overlay.setObjectName("SelectionOverlay")
        overlay.setStyleSheet(
            "QFrame#SelectionOverlay { border: 2px solid #0284c7; background: transparent; border-radius: 2px; }"
        )
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay.hide()
        self._selection_overlay = overlay

    def _ensure_clickable_overlay(self) -> None:
        if not self.webview or self._clickable_overlay:
            return
        overlay = QFrame(self.webview)
        overlay.setObjectName("ClickableOverlay")
        overlay.setStyleSheet(
            "QFrame#ClickableOverlay { border: 1px dashed #38bdf8; background: transparent; border-radius: 2px; }"
        )
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay.hide()
        self._clickable_overlay = overlay

    def _ensure_click_marker(self) -> None:
        if not self.webview or self._click_marker:
            return
        marker = QFrame(self.webview)
        marker.setObjectName("ClickMarker")
        marker.setFixedSize(14, 14)
        marker.setStyleSheet(
            "QFrame#ClickMarker { border: 2px solid #f97316; background: rgba(249,115,22,0.18); border-radius: 7px; }"
        )
        marker.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        marker.hide()
        self._click_marker = marker

    def _hide_selection_overlay(self) -> None:
        if self._selection_overlay:
            self._selection_overlay.hide()
        if self._clickable_overlay:
            self._clickable_overlay.hide()
        self._hover_last_point = None
        self._hover_pending_point = None

    def _hide_click_marker(self) -> None:
        if self._click_marker:
            self._click_marker.hide()

    def _show_click_marker(self, x: float, y: float) -> None:
        if not self._click_marker:
            return
        marker_x = int(round(x)) - (self._click_marker.width() // 2)
        marker_y = int(round(y)) - (self._click_marker.height() // 2)
        self._click_marker.move(marker_x, marker_y)
        self._click_marker.show()
        self._click_marker.raise_()
        self._click_marker_timer.start()

    def _emit_inspect_payload(self, x: float, y: float, emit_signal: Signal) -> None:
        if not self.webview:
            return
        fallback_payload = {
            "x": x,
            "y": y,
            "viewport_width": max(1, self.webview.width()),
            "viewport_height": max(1, self.webview.height()),
            "scroll_x": 0,
            "scroll_y": 0,
            "url": self._page_url,
            "device_pixel_ratio": 1.0,
        }
        page = self.webview.page()
        script = (
            "() => ({"
            "innerWidth: window.innerWidth || 0,"
            "innerHeight: window.innerHeight || 0,"
            "scrollX: window.scrollX || 0,"
            "scrollY: window.scrollY || 0,"
            "devicePixelRatio: window.devicePixelRatio || 1,"
            "href: window.location && window.location.href ? window.location.href : ''"
            "})"
        )

        def _on_metrics(result: object) -> None:
            payload = dict(fallback_payload)
            if isinstance(result, dict):
                try:
                    payload["viewport_width"] = int(result.get("innerWidth", payload["viewport_width"])) or payload[
                        "viewport_width"
                    ]
                    payload["viewport_height"] = int(result.get("innerHeight", payload["viewport_height"])) or payload[
                        "viewport_height"
                    ]
                    payload["scroll_x"] = int(result.get("scrollX", 0))
                    payload["scroll_y"] = int(result.get("scrollY", 0))
                    payload["device_pixel_ratio"] = float(result.get("devicePixelRatio", 1.0) or 1.0)
                except (TypeError, ValueError):
                    pass
                href = str(result.get("href", "") or "").strip()
                if href:
                    payload["url"] = href
            emit_signal.emit(payload)

        page.runJavaScript(script, _on_metrics)

    def _dispatch_hover_probe(self) -> None:
        if not self._inspect_enabled:
            return
        point = self._hover_pending_point
        self._hover_pending_point = None
        if not point:
            return
        self._emit_inspect_payload(point[0], point[1], self.inspect_hover_probe)

    def set_show_clickable_target_outline(self, enabled: bool) -> None:
        self._show_clickable_target_outline = bool(enabled)
        if not self._show_clickable_target_outline and self._clickable_overlay:
            self._clickable_overlay.hide()

    def apply_hover_box(self, rect: dict[str, Any] | None) -> None:
        if not self._selection_overlay:
            return
        if not rect:
            self._selection_overlay.hide()
            if self._clickable_overlay:
                self._clickable_overlay.hide()
            return
        raw_rect = rect.get("raw", rect) if isinstance(rect, dict) else None
        refined_rect = rect.get("refined") if isinstance(rect, dict) else None
        if not isinstance(raw_rect, dict):
            self._selection_overlay.hide()
            if self._clickable_overlay:
                self._clickable_overlay.hide()
            return
        try:
            left = int(round(float(raw_rect.get("left", 0))))
            top = int(round(float(raw_rect.get("top", 0))))
            width = max(1, int(round(float(raw_rect.get("width", 1)))))
            height = max(1, int(round(float(raw_rect.get("height", 1)))))
        except (TypeError, ValueError):
            self._selection_overlay.hide()
            if self._clickable_overlay:
                self._clickable_overlay.hide()
            return
        self._selection_overlay.setGeometry(left, top, width, height)
        self._selection_overlay.show()
        self._selection_overlay.raise_()
        if self._show_clickable_target_outline and self._clickable_overlay and isinstance(refined_rect, dict):
            try:
                r_left = int(round(float(refined_rect.get("left", 0))))
                r_top = int(round(float(refined_rect.get("top", 0))))
                r_width = max(1, int(round(float(refined_rect.get("width", 1)))))
                r_height = max(1, int(round(float(refined_rect.get("height", 1)))))
            except (TypeError, ValueError):
                self._clickable_overlay.hide()
            else:
                if r_left == left and r_top == top and r_width == width and r_height == height:
                    self._clickable_overlay.hide()
                    return
                self._clickable_overlay.setGeometry(r_left, r_top, r_width, r_height)
                self._clickable_overlay.show()
                self._clickable_overlay.raise_()
        elif self._clickable_overlay:
            self._clickable_overlay.hide()


class BottomStatusBar(QFrame):
    def __init__(self, status_label: QLabel, warning_label: QLabel, result_label: QLabel) -> None:
        super().__init__()
        self.setObjectName("Card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)
        layout.addWidget(status_label, 2)
        layout.addWidget(warning_label, 2)
        layout.addWidget(result_label, 3)


class LeftPanel(QFrame):
    def __init__(self, content: QWidget) -> None:
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        layout.addWidget(scroll)


class WorkspaceWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.logger = self._build_logger()
        self.setWindowTitle("inspectelement")
        self._fit_window_to_screen()
        self._set_icon()

        self.bridge = EventBridge()
        self.bridge.capture_received.connect(self._on_capture)
        self.bridge.status_changed.connect(self._set_status)
        self.bridge.page_changed.connect(self._on_managed_page_changed)
        self.bridge.hover_box_changed.connect(self._on_hover_box_changed)

        self.browser = BrowserManager(
            on_capture=lambda summary, candidates: self.bridge.capture_received.emit(summary, candidates),
            on_status=lambda message: self.bridge.status_changed.emit(message),
            on_page_info=lambda title, url: self.bridge.page_changed.emit(title, url),
            on_hover_box=lambda rect: self.bridge.hover_box_changed.emit(rect),
        )
        self._browser_worker_started = False

        self.current_summary: ElementSummary | None = None
        self.current_candidates: list[LocatorCandidate] = []
        self.current_visible_candidates: list[LocatorCandidate] = []
        self.show_advanced_locators = False
        self.last_hover_raw_target: dict[str, str] | None = None
        self.last_hover_refined_target: dict[str, str] | None = None
        self.project_root: Path | None = None
        self.selected_module: ModuleInfo | None = None
        self.available_modules: list[ModuleInfo] = []
        self.discovered_pages: list[PageClassInfo] = []
        self.pending_java_preview: JavaPreview | None = None
        self.pending_page_creation_preview: PageCreationPreview | None = None
        self.workspace_config_path = Path.home() / ".inspectelement" / "config.json"

        self.url_input = QLineEdit("https://example.com")
        self.url_input.setPlaceholderText("https://your-app-url")
        self.url_input.editingFinished.connect(self._persist_workspace_config)

        self.launch_button = QPushButton("Launch Browser")
        self.launch_button.clicked.connect(self._launch)

        self.inspect_toggle = QPushButton("Inspect Mode: OFF")
        self.inspect_toggle.setCheckable(True)
        self.inspect_toggle.setEnabled(False)
        self.inspect_toggle.clicked.connect(self._toggle_inspect)
        self.show_clickable_target_outline_checkbox = QCheckBox("Show clickable target outline")
        self.show_clickable_target_outline_checkbox.setChecked(False)
        self.show_clickable_target_outline_checkbox.toggled.connect(self._on_show_clickable_outline_toggled)
        self.toggle_left_panel_button = QPushButton("Hide Panel")
        self.toggle_left_panel_button.clicked.connect(self._toggle_left_panel)
        self.open_managed_inspector_button = QPushButton("Open Managed Inspector")
        self.open_managed_inspector_button.setEnabled(False)
        self.open_managed_inspector_button.clicked.connect(self._open_managed_inspector)

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

        self.project_input = QLineEdit()
        self.project_input.setPlaceholderText("Select automation project root")
        self.project_input.editingFinished.connect(self._on_project_root_edited)
        self.project_browse_button = QPushButton("Browse...")
        self.project_browse_button.clicked.connect(self._browse_project_root)

        self.module_combo = QComboBox()
        self.module_combo.addItem("Select module", None)
        self.module_combo.currentIndexChanged.connect(self._on_module_changed)

        self.page_combo = QComboBox()
        self.page_combo.addItem("Select page class", None)
        self.page_combo.setEnabled(False)
        self.page_combo.currentIndexChanged.connect(self._on_page_combo_changed)
        self.page_combo_previous_index = 0
        self.new_page_button = QPushButton("+ New Page")
        self.new_page_button.clicked.connect(self._toggle_new_page_drawer)

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
        self.table_root_choice_combo = QComboBox()
        self.table_root_choice_combo.currentIndexChanged.connect(self._on_table_root_choice_changed)
        self.table_root_choice_combo.setVisible(False)
        self.table_root_warning_label = QLabel("")
        self.table_root_warning_label.setObjectName("TableRootWarning")
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
        self.add_button.setText("Add -> Preview")
        self.validate_button = QPushButton("Validate Only")
        self.validate_button.setEnabled(False)
        self.validate_button.clicked.connect(self._validate_only_request)
        self.apply_preview_button = QPushButton("Apply")
        self.apply_preview_button.setEnabled(False)
        self.apply_preview_button.clicked.connect(self._apply_pending_preview)
        self.cancel_preview_button = QPushButton("Cancel Preview")
        self.cancel_preview_button.setEnabled(False)
        self.cancel_preview_button.clicked.connect(self._cancel_preview)
        self.left_add_button = QPushButton("Add")
        self.left_add_button.setToolTip("Generate diff preview for current element/action selection")
        self.left_add_button.setEnabled(False)
        self.left_add_button.clicked.connect(self._prepare_add_request)
        self.left_apply_preview_button = QPushButton("Apply")
        self.left_apply_preview_button.setEnabled(False)
        self.left_apply_preview_button.clicked.connect(self._apply_pending_preview)
        self.left_cancel_preview_button = QPushButton("Cancel Preview")
        self.left_cancel_preview_button.setEnabled(False)
        self.left_cancel_preview_button.clicked.connect(self._cancel_preview)
        self.top_status_pill = QLabel("OK")
        self.top_status_pill.setObjectName("StatusPill")

        self.payload_status_label = QLabel("Waiting for page, locator, and element name.")
        self.payload_status_label.setObjectName("Muted")
        self.payload_status_label.setWordWrap(True)

        self.bottom_status_label = QLabel("Ready.")
        self.bottom_status_label.setObjectName("Muted")
        self.bottom_warning_label = QLabel("")
        self.bottom_warning_label.setObjectName("Muted")
        self.bottom_result_label = QLabel("")
        self.bottom_result_label.setObjectName("Muted")

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
        self.results_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
        self.show_advanced_locators_checkbox = QCheckBox("Show advanced locators")
        self.show_advanced_locators_checkbox.setChecked(False)
        self.show_advanced_locators_checkbox.toggled.connect(self._on_show_advanced_locators_toggled)

        self.detail_labels: dict[str, QLabel] = {}
        detail_form = QFormLayout()
        detail_form.setContentsMargins(0, 0, 0, 0)
        detail_form.setHorizontalSpacing(12)
        detail_form.setVerticalSpacing(4)
        for key in [
            "tag",
            "id",
            "classes",
            "name",
            "role",
            "text",
            "placeholder",
            "aria-label",
            "raw-target",
            "refined-target",
        ]:
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

        self.preview_file_label = QLabel("Target file: -")
        self.preview_file_label.setObjectName("Muted")
        self.preview_meta_label = QLabel("Preview not generated.")
        self.preview_meta_label.setObjectName("Muted")
        self.preview_diff_editor = QPlainTextEdit()
        self.preview_diff_editor.setReadOnly(True)
        self.preview_diff_editor.setPlaceholderText("Unified diff preview appears here after Add -> Preview.")
        self.preview_diff_editor.setMaximumHeight(220)
        self.preview_dock = QFrame()
        self.preview_dock.setObjectName("Card")
        self.preview_dock.setVisible(False)

        self.new_page_drawer = QFrame()
        self.new_page_drawer.setObjectName("Card")
        self.new_page_drawer.setVisible(False)
        self.new_page_name_input = QLineEdit()
        self.new_page_name_input.setPlaceholderText("Page Name (PascalCase)")
        self.new_page_name_input.textChanged.connect(self._preview_new_page_inline)
        self.new_page_package_label = QLabel("Package: -")
        self.new_page_package_label.setObjectName("Muted")
        self.new_page_target_label = QLabel("Target: -")
        self.new_page_target_label.setObjectName("Muted")
        self.new_page_preview_editor = QPlainTextEdit()
        self.new_page_preview_editor.setReadOnly(True)
        self.new_page_preview_editor.setMaximumHeight(180)
        self.new_page_apply_button = QPushButton("Create Page")
        self.new_page_apply_button.setEnabled(False)
        self.new_page_apply_button.clicked.connect(self._apply_new_page_inline)
        self.new_page_cancel_button = QPushButton("Cancel")
        self.new_page_cancel_button.clicked.connect(self._cancel_new_page_inline)

        feedback_row = QHBoxLayout()
        feedback_row.addWidget(self.good_button)
        feedback_row.addWidget(self.bad_button)

        editor_actions_row = QHBoxLayout()
        editor_actions_row.addWidget(self.apply_edit_button)
        editor_actions_row.addWidget(self.copy_edited_button)
        editor_actions_row.addWidget(self.good_edited_button)

        top_bar = TopBar(
            project_input=self.project_input,
            project_browse_button=self.project_browse_button,
            module_combo=self.module_combo,
            page_combo=self.page_combo,
            new_page_button=self.new_page_button,
            url_input=self.url_input,
            launch_button=self.launch_button,
            inspect_toggle=self.inspect_toggle,
            toggle_left_button=self.toggle_left_panel_button,
            open_managed_button=self.open_managed_inspector_button,
            validate_button=self.validate_button,
            preview_button=self.add_button,
            apply_button=self.apply_preview_button,
            cancel_button=self.cancel_preview_button,
            status_pill=self.top_status_pill,
        )

        # Left panel with always-visible control workspace
        left_content = QWidget()
        left_col = QVBoxLayout(left_content)

        self.snapshot_card = QFrame()
        self.snapshot_card.setObjectName("Card")
        snapshot_layout = QVBoxLayout(self.snapshot_card)
        snapshot_layout.setContentsMargins(8, 8, 8, 8)
        snapshot_layout.addWidget(QLabel("Element Snapshot"))
        snapshot_layout.addWidget(self.show_clickable_target_outline_checkbox)
        snapshot_layout.addLayout(detail_form)
        snapshot_layout.addWidget(QLabel("Element Name"))
        snapshot_layout.addWidget(self.element_name_input)
        snapshot_layout.addWidget(QLabel("Locator Editor"))
        snapshot_layout.addWidget(self.locator_editor)
        snapshot_layout.addLayout(editor_actions_row)
        snapshot_layout.addWidget(self.payload_status_label)
        left_col.addWidget(self.snapshot_card)
        left_col.addWidget(self.new_page_drawer)

        self.candidates_card = QFrame()
        self.candidates_card.setObjectName("Card")
        candidates_layout = QVBoxLayout(self.candidates_card)
        candidates_layout.setContentsMargins(8, 8, 8, 8)
        candidates_layout.addWidget(QLabel("Locator Candidates"))
        candidates_layout.addWidget(self.show_advanced_locators_checkbox)
        candidates_layout.addWidget(self.results_table)
        self.score_breakdown_label = QLabel("Score Breakdown")
        candidates_layout.addWidget(self.score_breakdown_label)
        candidates_layout.addWidget(self.breakdown_text)
        self.feedback_row_container = QWidget()
        self.feedback_row_container.setLayout(feedback_row)
        candidates_layout.addWidget(self.feedback_row_container)
        left_col.addWidget(self.candidates_card)

        self.actions_section_label = QLabel("Actions")
        left_col.addWidget(self.actions_section_label)
        left_col.addWidget(self.action_picker_widget)
        left_col.addWidget(self._build_table_root_section())
        left_col.addWidget(self._build_parameter_panel())

        log_row = QHBoxLayout()
        log_row.addWidget(QLabel("Log"))
        log_row.addWidget(self.log_language_combo)
        log_row.addStretch(1)
        left_col.addLayout(log_row)

        generate_row = QHBoxLayout()
        generate_row.setContentsMargins(0, 0, 0, 0)
        generate_row.setSpacing(6)
        generate_row.addWidget(self.left_add_button)
        generate_row.addWidget(self.left_apply_preview_button)
        generate_row.addWidget(self.left_cancel_preview_button)
        generate_row.addStretch(1)
        left_col.addLayout(generate_row)

        self.generated_preview_label = QLabel("Generated Methods Preview")
        left_col.addWidget(self.generated_preview_label)
        left_col.addWidget(self.generated_methods_preview)

        preview_layout = QVBoxLayout(self.preview_dock)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.addWidget(QLabel("Preview Diff"))
        preview_layout.addWidget(self.preview_file_label)
        preview_layout.addWidget(self.preview_meta_label)
        preview_layout.addWidget(self.preview_diff_editor)
        left_col.addWidget(self.preview_dock)

        drawer_layout = QVBoxLayout(self.new_page_drawer)
        drawer_layout.setContentsMargins(8, 8, 8, 8)
        drawer_layout.addWidget(QLabel("New Page"))
        drawer_layout.addWidget(self.new_page_name_input)
        drawer_layout.addWidget(self.new_page_package_label)
        drawer_layout.addWidget(self.new_page_target_label)
        drawer_layout.addWidget(self.new_page_preview_editor)
        drawer_actions = QHBoxLayout()
        drawer_actions.addWidget(self.new_page_apply_button)
        drawer_actions.addWidget(self.new_page_cancel_button)
        drawer_actions.addStretch(1)
        drawer_layout.addLayout(drawer_actions)
        left_col.addStretch(1)
        self.left_panel = LeftPanel(left_content)
        self.left_panel.setFixedWidth(400)

        self.browser_panel = BrowserPanel()
        self.browser_panel.inspect_capture.connect(self._on_embedded_click_capture)
        self.browser_panel.inspect_hover_probe.connect(self._on_embedded_hover_probe)
        self.browser_panel.page_changed.connect(self._on_embedded_page_changed)
        self.browser_panel.load_finished.connect(self._on_embedded_load_finished)
        self.browser_panel.set_show_clickable_target_outline(self.show_clickable_target_outline_checkbox.isChecked())

        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.addWidget(self.left_panel)
        self.workspace_splitter.addWidget(self.browser_panel)
        self.workspace_splitter.setChildrenCollapsible(True)
        self.workspace_splitter.setStretchFactor(0, 0)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setSizes([400, 1060])

        self.status_label = QLabel("Ready. Step 1: select project/module/page and launch URL.")
        self.status_label.setObjectName("Status")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bottom_bar = BottomStatusBar(
            status_label=self.bottom_status_label,
            warning_label=self.bottom_warning_label,
            result_label=self.bottom_result_label,
        )

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)
        root_layout.addWidget(top_bar)
        root_layout.addWidget(self.workspace_splitter, 1)
        root_layout.addWidget(bottom_bar)
        root_layout.addWidget(self.status_label)
        self.setCentralWidget(root)

        self.toast_label = QLabel("", self)
        self.toast_label.setObjectName("Toast")
        self.toast_label.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_label.hide)

        self.action_search_timer = QTimer(self)
        self.action_search_timer.setSingleShot(True)
        self.action_search_timer.setInterval(160)
        self.action_search_timer.timeout.connect(self._refresh_action_dropdown)
        self.action_search_input.textChanged.disconnect()
        self.action_search_input.textChanged.connect(lambda _text: self.action_search_timer.start())

        self._apply_style()
        self._refresh_table_root_section()
        self._refresh_parameter_panel()
        self._update_generated_methods_preview()
        self._load_workspace_from_config()
        self._update_workspace_actions_state()

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
                font-family: "Helvetica Neue", Arial, sans-serif;
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
                selection-background-color: #eef2ff;
                selection-color: #0f172a;
            }
            QTableWidget::item:selected {
                background: #eef2ff;
                color: #0f172a;
            }
            QTableWidget::item:selected:active {
                background: #e2e8ff;
                color: #0f172a;
            }
            QTableWidget::item:selected:!active {
                background: #eef2ff;
                color: #0f172a;
            }
            QTableWidget::item:hover {
                background: transparent;
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
        layout.addWidget(self.table_root_choice_combo)
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

    def _on_show_advanced_locators_toggled(self, enabled: bool) -> None:
        self.show_advanced_locators = bool(enabled)
        self._refresh_candidate_list_view()

    def _refresh_candidate_list_view(self) -> None:
        if not self.current_candidates:
            self.current_visible_candidates = []
            self._render_candidates([])
            return

        if self.show_advanced_locators:
            visible = self.current_candidates[:15]
        else:
            valid = [candidate for candidate in self.current_candidates if bool(candidate.metadata.get("display_valid", True))]
            visible = valid[:8]
        self.current_visible_candidates = visible
        self._render_candidates(self.current_visible_candidates)

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
        self.table_root_warning_label.setVisible(False)
        self.table_root_choice_combo.setVisible(False)
        if not needs_table:
            return

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
            self.bottom_warning_label.setText(warning)

        if len(self.auto_table_root_candidates) > 1 and not self.manual_table_root_selector_type:
            self.table_root_choice_combo.blockSignals(True)
            self.table_root_choice_combo.clear()
            for index, candidate in enumerate(self.auto_table_root_candidates, start=1):
                selector_type = candidate.get("selector_type", "?")
                selector_value = candidate.get("selector_value", "?")
                reason = candidate.get("reason", "-")
                label = f"{index}. {selector_type}: {selector_value} ({reason})"
                self.table_root_choice_combo.addItem(label, candidate)
            self.table_root_choice_combo.setCurrentIndex(0)
            self.table_root_choice_combo.blockSignals(False)
            self.table_root_choice_combo.setVisible(True)

    def _on_table_root_choice_changed(self, _index: int) -> None:
        selected_candidate = self.table_root_choice_combo.currentData()
        if not isinstance(selected_candidate, dict):
            return
        selector_type = selected_candidate.get("selector_type")
        selector_value = selected_candidate.get("selector_value")
        if selector_type and selector_value:
            self.auto_table_root_selector_type = selector_type
            self.auto_table_root_selector_value = selector_value
            self.auto_table_root_locator_name = selected_candidate.get("locator_name_hint") or self._selected_table_root_locator_name()
            self.auto_table_root_warning = selected_candidate.get("warning", "").strip() or None
            self._refresh_table_root_section()
            self._update_generated_methods_preview()

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
            self._refresh_table_root_section()
            return

        selected_candidate = candidates[0]

        selector_type = selected_candidate.get("selector_type")
        selector_value = selected_candidate.get("selector_value")
        locator_name_hint = selected_candidate.get("locator_name_hint")
        warning = selected_candidate.get("warning", "").strip() or None
        if selector_type and selector_value:
            self.auto_table_root_selector_type = selector_type
            self.auto_table_root_selector_value = selector_value
            self.auto_table_root_locator_name = locator_name_hint or self._selected_table_root_locator_name()
            self.auto_table_root_warning = warning
        else:
            self.auto_table_root_selector_type = None
            self.auto_table_root_selector_value = None
            self.auto_table_root_locator_name = None
            self.auto_table_root_warning = None
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
        self._refresh_table_root_section()

    def _clear_manual_table_root(self) -> None:
        self.manual_table_root_selector_type = None
        self.manual_table_root_selector_value = None
        self.manual_table_root_locator_name = None
        self.manual_table_root_warning = None
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

    def _reset_context_dependent_state(self) -> None:
        self.manual_table_root_selector_type = None
        self.manual_table_root_selector_value = None
        self.manual_table_root_locator_name = None
        self.manual_table_root_warning = None
        self.auto_table_root_selector_type = None
        self.auto_table_root_selector_value = None
        self.auto_table_root_locator_name = None
        self.auto_table_root_warning = None
        self.auto_table_root_candidates = []

    def _browse_project_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Automation Project Root")
        if not chosen:
            return
        self.project_input.setText(chosen)
        self._on_project_root_edited()

    def _on_project_root_edited(self) -> None:
        raw = self.project_input.text().strip()
        root = Path(raw) if raw else None
        if not root or not root.is_dir():
            self.project_root = None
            self.available_modules = []
            self.module_combo.clear()
            self.module_combo.addItem("Select module", None)
            self.selected_module = None
            self._refresh_page_classes()
            self._set_status("Select a valid automation project root.")
            self._persist_workspace_config()
            return

        self.project_root = root
        self._refresh_modules()
        self._persist_workspace_config()

    def _refresh_modules(self) -> None:
        if not self.project_root:
            self.available_modules = []
            self.module_combo.clear()
            self.module_combo.addItem("Select module", None)
            return

        modules = discover_modules(self.project_root)
        self.available_modules = modules
        self.module_combo.blockSignals(True)
        self.module_combo.clear()
        self.module_combo.addItem("Select module", None)
        for module in modules:
            self.module_combo.addItem(module.name, module)
        self.module_combo.blockSignals(False)

        if not modules:
            self.selected_module = None
            self._refresh_page_classes()
            self._set_status("No modules found under <root>/modules/apps.")
            return

        self._set_status(f"Loaded {len(modules)} module(s).")

    def _on_module_changed(self, _index: int) -> None:
        selected = self.module_combo.currentData()
        if isinstance(selected, ModuleInfo):
            self.selected_module = selected
            self._reset_context_dependent_state()
            self._refresh_page_classes()
            self._persist_workspace_config()
            return

        self.selected_module = None
        self._reset_context_dependent_state()
        self._refresh_page_classes()
        self._persist_workspace_config()

    def _load_workspace_from_config(self) -> None:
        config = load_workspace_config(self.workspace_config_path)
        if config.url:
            self.url_input.setText(config.url)
        if config.project_root:
            self.project_input.setText(config.project_root)
            self._on_project_root_edited()

        if config.module_name and self.available_modules:
            for index in range(self.module_combo.count()):
                data = self.module_combo.itemData(index)
                if isinstance(data, ModuleInfo) and data.name == config.module_name:
                    self.module_combo.setCurrentIndex(index)
                    break

        if config.page_class:
            self._select_page_class(config.page_class)

        self.inspect_toggle.setChecked(False)
        self.inspect_toggle.setText("Inspect Mode: OFF")
        self.inspect_toggle.setEnabled(can_enable_inspect(has_launched_page=False))
        if config.inspect_enabled:
            self.bottom_warning_label.setText("Inspect mode preference loaded. Launch URL, then enable Inspect.")

    def _persist_workspace_config(self) -> None:
        config = WorkspaceConfig(
            project_root=str(self.project_root) if self.project_root else self.project_input.text().strip(),
            module_name=self.selected_module.name if self.selected_module else "",
            page_class=self._selected_page_class().class_name if self._selected_page_class() else "",
            url=self.url_input.text().strip() or "https://example.com",
            inspect_enabled=self.inspect_toggle.isChecked(),
        )
        try:
            save_workspace_config(self.workspace_config_path, config)
        except OSError:
            return

    def _refresh_page_classes(self) -> None:
        self._reset_generated_preview_override()
        self.page_combo.clear()
        self.page_combo.addItem("Select page class", None)
        self.page_combo_previous_index = 0

        if not self.selected_module:
            self.discovered_pages = []
            self.page_combo.setEnabled(False)
            self.new_page_button.setEnabled(False)
            self._update_add_button_state()
            return

        pages = discover_page_classes(self.selected_module)
        self.discovered_pages = pages
        for page in pages:
            self.page_combo.addItem(page.class_name, page)

        can_create_page = can_enable_new_page(
            has_project_root=bool(self.project_root),
            has_module=self.selected_module is not None,
        )
        self.page_combo.setEnabled(can_create_page)
        self.new_page_button.setEnabled(can_create_page)
        if pages:
            self.page_combo.setCurrentIndex(1)
            self.page_combo_previous_index = 1
        else:
            self.page_combo.setCurrentIndex(0)
            self.page_combo_previous_index = 0
        self._update_add_button_state()
        self._persist_workspace_config()
        if self.selected_module and pages:
            self._set_status(f"Context loaded: {self.selected_module.name} ({len(pages)} page class(es) found).")
        elif self.selected_module:
            self._set_status(f"Context loaded: {self.selected_module.name}. No page classes found.")

    def _selected_page_class(self) -> PageClassInfo | None:
        selected = self.page_combo.currentData()
        if isinstance(selected, PageClassInfo):
            return selected
        return None

    def _on_page_combo_changed(self, _index: int) -> None:
        self.page_combo_previous_index = self.page_combo.currentIndex()
        self._persist_workspace_config()
        self._update_add_button_state()

    def _select_page_class(self, class_name: str) -> None:
        for index in range(self.page_combo.count()):
            data = self.page_combo.itemData(index)
            if isinstance(data, PageClassInfo) and data.class_name == class_name:
                self.page_combo.setCurrentIndex(index)
                self.page_combo_previous_index = index
                return

    def _toggle_new_page_drawer(self) -> None:
        self.logger.info("New Page handler invoked.")
        if not self.selected_module:
            self._set_status("Select module before creating page.")
            return
        visible = not self.new_page_drawer.isVisible()
        self.new_page_drawer.setVisible(visible)
        if visible:
            self.new_page_name_input.setFocus()
            self._preview_new_page_inline()
        else:
            self._cancel_new_page_inline()

    def _preview_new_page_inline(self) -> None:
        if not self.selected_module:
            self.pending_page_creation_preview = None
            self.new_page_apply_button.setEnabled(False)
            return

        preview = generate_page_creation_preview(
            module=self.selected_module,
            existing_pages=self.discovered_pages,
            page_name_raw=self.new_page_name_input.text(),
        )
        self.pending_page_creation_preview = preview
        if preview.package_name:
            self.new_page_package_label.setText(f"Package: {preview.package_name}")
        else:
            self.new_page_package_label.setText("Package: -")
        self.new_page_target_label.setText(f"Target: {preview.target_file}")
        self.new_page_preview_editor.setPlainText(preview.file_content or preview.message)
        self.new_page_apply_button.setEnabled(preview.ok)
        if not preview.ok and self.new_page_name_input.text().strip():
            self.bottom_warning_label.setText(preview.message)

    def _apply_new_page_inline(self) -> None:
        preview = self.pending_page_creation_preview
        if not preview or not preview.ok:
            self._set_status("No page creation preview to apply.")
            return
        applied, message = apply_page_creation_preview(preview)
        self._set_status(message)
        self._show_toast(message)
        self.bottom_result_label.setText(message)
        if not applied:
            return

        created_class_name = preview.class_name or ""
        self.new_page_drawer.setVisible(False)
        self.new_page_name_input.clear()
        self.pending_page_creation_preview = None
        self._refresh_page_classes()
        if created_class_name:
            self._select_page_class(created_class_name)
        self._update_add_button_state()

    def _cancel_new_page_inline(self) -> None:
        self.pending_page_creation_preview = None
        self.new_page_name_input.clear()
        self.new_page_preview_editor.clear()
        self.new_page_package_label.setText("Package: -")
        self.new_page_target_label.setText("Target: -")
        self.new_page_apply_button.setEnabled(False)
        self.new_page_drawer.setVisible(False)

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
        validation_ok = self._validate_current_request() is None
        state = compute_enable_state(
            has_page=has_page,
            has_locator=has_locator,
            has_element_name=has_name,
            validation_ok=validation_ok,
            has_preview=self.pending_java_preview is not None,
        )
        self.add_button.setEnabled(state.can_preview)
        self.left_add_button.setEnabled(state.can_preview)
        self.validate_button.setEnabled(state.can_preview)
        self.apply_preview_button.setEnabled(state.can_apply)
        self.left_apply_preview_button.setEnabled(state.can_apply)
        self.cancel_preview_button.setEnabled(state.can_cancel_preview)
        self.left_cancel_preview_button.setEnabled(state.can_cancel_preview)
        if not (has_page and has_locator and has_name):
            missing: list[str] = []
            if not has_page:
                missing.append("page")
            if not has_locator:
                missing.append("locator")
            if not has_name:
                missing.append("element name")
            self.payload_status_label.setText(f"Waiting for: {', '.join(missing)}.")
        else:
            self._refresh_payload_status("Payload ready. Click Add to prepare output.")
        self._update_workspace_actions_state()

    def _update_workspace_actions_state(self) -> None:
        has_page = self._selected_page_class() is not None
        has_locator = self._selected_candidate() is not None
        has_name = bool(self.element_name_input.text().strip())
        has_actions = bool(self._selected_actions())
        has_base_context = has_page and has_locator and has_name
        ready_for_strict_validation = has_base_context and has_actions
        validation_error = self._validate_current_request()
        if not ready_for_strict_validation and self.pending_java_preview is None:
            self.top_status_pill.setText("OK")
            self.top_status_pill.setStyleSheet("background:#dcfce7;color:#166534;padding:3px 8px;border-radius:10px;")
            if self.bottom_warning_label.text().startswith("Table root") or self.bottom_warning_label.text().startswith(
                "Select "
            ):
                self.bottom_warning_label.clear()
            return
        if validation_error:
            self.top_status_pill.setText("Error")
            self.top_status_pill.setStyleSheet("background:#fee2e2;color:#991b1b;padding:3px 8px;border-radius:10px;")
            self.bottom_warning_label.setText(validation_error)
        elif self.pending_java_preview is not None:
            self.top_status_pill.setText("Preview")
            self.top_status_pill.setStyleSheet("background:#dbeafe;color:#1d4ed8;padding:3px 8px;border-radius:10px;")
            self.bottom_warning_label.setText("Preview ready. Click Apply to write or Cancel Preview.")
        else:
            self.top_status_pill.setText("OK")
            self.top_status_pill.setStyleSheet("background:#dcfce7;color:#166534;padding:3px 8px;border-radius:10px;")
            if self.bottom_warning_label.text().startswith("Table root"):
                self.bottom_warning_label.clear()

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
            self.logger.info(
                "Add->Preview requested: page=%s locator_selected=%s element_name=%s actions=%s",
                self._selected_page_class().class_name if self._selected_page_class() else "-",
                bool(self._selected_candidate()),
                self.element_name_input.text().strip() or "-",
                ",".join(self._selected_actions()) or "-",
            )
            preview = self._generate_preview_for_current_request()
            if not preview.ok:
                self.pending_java_preview = None
                self.preview_dock.setVisible(False)
                self._set_status(preview.message)
                self.payload_status_label.setText(preview.message)
                self.bottom_warning_label.setText(preview.message)
                self.bottom_result_label.setText("")
                self.logger.warning("Preview not generated: %s", preview.message)
                self._show_toast(preview.message)
                self._update_add_button_state()
                return

            self.pending_java_preview = preview
            self._set_status(preview.message)
            self.payload_status_label.setText(preview.message)
            self.preview_locator_name_override = preview.final_locator_name
            self.preview_signatures_override = list(preview.added_method_signatures)
            self.preview_signatures_actions_snapshot = tuple(self._selected_actions())
            self._update_generated_methods_preview()
            self._populate_preview_dock(preview)
            self.bottom_result_label.setText(preview.message)
            self.bottom_warning_label.setText("Preview ready. Click Apply to write or Cancel Preview.")
            self.logger.info("Preview generated: target=%s methods=%s", preview.target_file, ",".join(preview.added_methods) or "-")
            self._update_add_button_state()
            return
        except Exception as exc:
            self.pending_java_preview = None
            self._handle_ui_exception("Unexpected error during preview/apply. See ~/.inspectelement/ui.log.", exc)
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
            self.bottom_result_label.setText("Validation successful. No files written.")
            self._update_workspace_actions_state()
        except Exception as exc:
            self._handle_ui_exception("Validation failed unexpectedly. See ~/.inspectelement/ui.log.", exc)

    def _populate_preview_dock(self, preview: JavaPreview) -> None:
        methods = ", ".join(preview.added_methods) if preview.added_methods else "-"
        locator = preview.final_locator_name or "-"
        self.preview_file_label.setText(f"Target file: {preview.target_file}")
        self.preview_meta_label.setText(f"Locator: {locator} | Methods: {methods}")
        self.preview_diff_editor.setPlainText(preview.diff_text)
        self.preview_dock.setVisible(True)

    def _apply_pending_preview(self) -> None:
        preview = self.pending_java_preview
        if not preview:
            self._set_status("No preview to apply.")
            return
        self.logger.info("Apply requested: target=%s", preview.target_file)
        validation_error = self._validate_current_request()
        if validation_error:
            self._set_status(validation_error)
            self.bottom_warning_label.setText(validation_error)
            self._show_toast(validation_error)
            self._update_add_button_state()
            return

        applied, message, backup_path = apply_java_preview(preview)
        self.logger.info("Apply result: applied=%s message=%s backup=%s", applied, message, backup_path or "-")
        self._set_status(message)
        self.payload_status_label.setText(message)
        self.bottom_result_label.setText(message)
        if not applied:
            self.bottom_warning_label.setText(message)
        if backup_path:
            self.bottom_result_label.setText(f"{message} | backup={backup_path}")
        self._show_toast(message)
        if applied:
            self.pending_java_preview = None
            self.preview_dock.setVisible(False)
            self.preview_diff_editor.clear()
            self.preview_file_label.setText("Target file: -")
            self.preview_meta_label.setText("Preview not generated.")
            self.bottom_warning_label.clear()
        self._update_add_button_state()
        self._persist_workspace_config()

    def _cancel_preview(self) -> None:
        self.pending_java_preview = None
        self.preview_dock.setVisible(False)
        self.preview_diff_editor.clear()
        self.preview_file_label.setText("Target file: -")
        self.preview_meta_label.setText("Preview cancelled.")
        self.preview_locator_name_override = None
        self.preview_signatures_override = None
        self.preview_signatures_actions_snapshot = ()
        self._set_status("Cancelled — no changes.")
        self.payload_status_label.setText("Cancelled — no changes.")
        self.bottom_result_label.setText("Cancelled — no changes.")
        self._update_add_button_state()

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

    @staticmethod
    def _normalize_launch_url(raw_url: str) -> str:
        url = raw_url.strip()
        if not url:
            return ""
        parsed = urlparse(url)
        if not parsed.scheme:
            return f"https://{url}"
        return url

    def _launch(self) -> None:
        launch_url = self._normalize_launch_url(self.url_input.text())
        if not launch_url:
            self._set_status("Please enter a URL.")
            return
        self.url_input.setText(launch_url)
        if not self.browser_panel.webview:
            self._set_status("Embedded browser is unavailable in this runtime.")
            self.bottom_warning_label.setText("Qt WebEngine is required for embedded inspect mode.")
            return
        self.inspect_toggle.setChecked(False)
        self.inspect_toggle.setText("Inspect Mode: OFF")
        self.inspect_toggle.setEnabled(can_enable_inspect(has_launched_page=False))
        self.browser_panel.set_inspector_enabled(False)
        self._set_focus_mode(False)
        self.browser_panel.load_url(launch_url)
        self._ensure_browser_worker_started()
        self.browser.launch(launch_url, viewport=self.browser_panel.viewport_size())
        self.open_managed_inspector_button.setEnabled(False)
        self._set_status(f"Launching: {launch_url}")
        self.bottom_result_label.setText("Embedded + managed browser launch requested.")
        self._persist_workspace_config()

    def _toggle_inspect(self) -> None:
        enabled = self.inspect_toggle.isChecked()
        self.logger.info("Inspect toggle changed: enabled=%s", enabled)
        self.inspect_toggle.setText(f"Inspect Mode: {'ON' if enabled else 'OFF'}")
        self.browser.set_inspect_mode(enabled)
        if self.browser_panel.webview:
            self.browser_panel.set_inspector_enabled(enabled)
        if enabled:
            self._set_status("Inspect ON. Click element in embedded browser to capture coordinates.")
        self._set_focus_mode(enabled)
        self._persist_workspace_config()

    def _on_show_clickable_outline_toggled(self, enabled: bool) -> None:
        self.browser_panel.set_show_clickable_target_outline(enabled)

    def _toggle_left_panel(self) -> None:
        visible = self.left_panel.isVisible()
        self.left_panel.setVisible(not visible)
        self.toggle_left_panel_button.setText("Show Panel" if visible else "Hide Panel")
        if visible:
            self.workspace_splitter.setSizes([0, max(600, self.width())])
        else:
            self.workspace_splitter.setSizes([400, max(600, self.width() - 400)])

    def _set_focus_mode(self, enabled: bool) -> None:
        hide_when_focus = [
            self.score_breakdown_label,
            self.breakdown_text,
            self.feedback_row_container,
            self.apply_edit_button,
            self.copy_edited_button,
            self.good_edited_button,
        ]
        for widget in hide_when_focus:
            widget.setVisible(not enabled)
        if enabled:
            self.results_table.setMaximumHeight(360)
        else:
            self.results_table.setMaximumHeight(245)

    def _open_managed_inspector(self) -> None:
        self._ensure_browser_worker_started()
        self.browser.open_managed_inspector()

    def _ensure_browser_worker_started(self) -> None:
        if self._browser_worker_started:
            return
        self.browser.start()
        self._browser_worker_started = True

    def _copy(self, value: str) -> None:
        QApplication.clipboard().setText(value)
        self._set_status("Locator copied.")
        self._show_toast("Panoya kopyalandi")

    def _copy_best(self) -> None:
        source_candidates = self.current_visible_candidates or self.current_candidates
        if not source_candidates:
            self._set_status("No locator candidates yet.")
            return

        selected_format = self.output_format_combo.currentText()
        if selected_format == "Best":
            self._copy(source_candidates[0].locator)
            return

        for candidate in source_candidates:
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
        self._refresh_candidate_list_view()
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

    def _on_embedded_click_capture(self, payload: dict) -> None:
        if not self.inspect_toggle.isChecked():
            return
        try:
            x = float(payload.get("x", 0))
            y = float(payload.get("y", 0))
            viewport_width = int(payload.get("viewport_width", 0))
            viewport_height = int(payload.get("viewport_height", 0))
            scroll_x = int(payload.get("scroll_x", 0))
            scroll_y = int(payload.get("scroll_y", 0))
            source_dpr = float(payload.get("device_pixel_ratio", 1.0))
        except (TypeError, ValueError):
            self._set_status("Invalid inspect capture payload received.")
            self.logger.warning("Invalid inspect payload received: %s", payload)
            return

        try:
            self.logger.info(
                "Embedded inspect click received: x=%s y=%s viewport=%sx%s scroll=%s,%s dpr=%.2f",
                int(x),
                int(y),
                viewport_width,
                viewport_height,
                scroll_x,
                scroll_y,
                source_dpr,
            )
            click_url = str(payload.get("url", "") or "").strip()
            self.browser.capture_at_coordinates(
                x,
                y,
                viewport=(max(1, viewport_width), max(1, viewport_height)),
                scroll=(max(0, scroll_x), max(0, scroll_y)),
                source_url=click_url or None,
                source_dpr=source_dpr,
            )
            self._set_status(f"Capture requested at ({int(x)}, {int(y)}).")
        except Exception as exc:
            self.logger.exception("Embedded inspect click dispatch failed", exc_info=exc)
            self._set_status(f"Capture dispatch failed: {exc}")

    def _on_embedded_hover_probe(self, payload: dict) -> None:
        if not self.inspect_toggle.isChecked():
            return
        try:
            x = float(payload.get("x", 0))
            y = float(payload.get("y", 0))
            viewport_width = int(payload.get("viewport_width", 0))
            viewport_height = int(payload.get("viewport_height", 0))
            scroll_x = int(payload.get("scroll_x", 0))
            scroll_y = int(payload.get("scroll_y", 0))
            source_dpr = float(payload.get("device_pixel_ratio", 1.0))
        except (TypeError, ValueError):
            return

        hover_url = str(payload.get("url", "") or "").strip()
        self.browser.probe_hover_at_coordinates(
            x,
            y,
            viewport=(max(1, viewport_width), max(1, viewport_height)),
            scroll=(max(0, scroll_x), max(0, scroll_y)),
            source_url=hover_url or None,
            source_dpr=source_dpr,
        )

    def _on_hover_box_changed(self, rect: object) -> None:
        if not self.inspect_toggle.isChecked():
            self.browser_panel.apply_hover_box(None)
            return
        if isinstance(rect, dict):
            raw = rect.get("raw")
            refined = rect.get("refined")
            self.last_hover_raw_target = self._normalize_target_dict(raw)
            self.last_hover_refined_target = self._normalize_target_dict(refined)
            self._refresh_target_summary_labels()
            self.browser_panel.apply_hover_box(rect)
            return
        self.last_hover_raw_target = None
        self.last_hover_refined_target = None
        self._refresh_target_summary_labels()
        self.browser_panel.apply_hover_box(None)

    def _on_embedded_load_finished(self, ok: bool) -> None:
        if not ok:
            self.bottom_warning_label.setText("Embedded page failed to load.")
            return
        self.inspect_toggle.setEnabled(can_enable_inspect(has_launched_page=True))
        self.open_managed_inspector_button.setEnabled(True)
        self.browser.sync_viewport(self.browser_panel.viewport_size())
        if self.inspect_toggle.isChecked() and self.browser_panel.webview:
            self.browser_panel.set_inspector_enabled(True)

    def _on_capture(self, summary: ElementSummary, candidates: list[LocatorCandidate]) -> None:
        self._reset_generated_preview_override()
        self.current_summary = summary
        if summary.raw_target:
            self.last_hover_raw_target = summary.raw_target
        if summary.refined_target:
            self.last_hover_refined_target = summary.refined_target
        self.logger.info(
            "Capture received: tag=%s id=%s text=%s candidates=%s",
            summary.tag or "-",
            summary.id or "-",
            (summary.text or "-")[:120],
            len(candidates),
        )
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
        self._refresh_candidate_list_view()
        if self.pick_table_root_mode and self.current_visible_candidates:
            root_selector = self._resolve_java_selector(self.current_visible_candidates[0])
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
        if self.current_visible_candidates:
            self._suggest_element_name(self.current_visible_candidates[0], force=True)
        else:
            self.element_name_input.clear()
        self._update_add_button_state()
        status_message = f"Captured <{summary.tag}> with {len(self.current_visible_candidates)} suggestions."
        if not self.current_visible_candidates and self.current_candidates:
            status_message += " Enable 'Show advanced locators' to inspect non-validated fallbacks."
        if scoring_failed:
            status_message += " Recommendation scoring failed; using base order."
        self._set_status(status_message)

    def _on_embedded_page_changed(self, title: str, url: str) -> None:
        self.setWindowTitle(f"inspectelement - {title or url}")
        if url and self.url_input.text().strip() != url:
            self.url_input.blockSignals(True)
            self.url_input.setText(url)
            self.url_input.blockSignals(False)
            self._persist_workspace_config()
        if url:
            self.inspect_toggle.setEnabled(can_enable_inspect(has_launched_page=True))
            self.open_managed_inspector_button.setEnabled(True)
            self.browser.navigate(url)

    def _on_managed_page_changed(self, title: str, url: str) -> None:
        if not url:
            return
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
            "raw-target": self._format_target_summary(summary.raw_target or self.last_hover_raw_target),
            "refined-target": self._format_target_summary(summary.refined_target or self.last_hover_refined_target),
        }
        for key, label in self.detail_labels.items():
            label.setText(mapping.get(key, "-"))

    def _normalize_target_dict(self, raw: object) -> dict[str, str] | None:
        if not isinstance(raw, dict):
            return None
        tag = str(raw.get("tag", "") or "").strip().lower()
        element_id = str(raw.get("id", "") or "").strip()
        class_name = str(raw.get("class_name", "") or "").strip()
        text = str(raw.get("text", "") or "").strip()
        if not any((tag, element_id, class_name, text)):
            return None
        return {
            "tag": tag,
            "id": element_id,
            "class_name": class_name,
            "text": text,
        }

    def _format_target_summary(self, target: dict[str, str] | None) -> str:
        if not target:
            return "-"
        tag = target.get("tag", "").strip() or "?"
        element_id = target.get("id", "").strip()
        class_name = target.get("class_name", "").strip()
        text = target.get("text", "").strip()
        pieces = [tag]
        if element_id:
            pieces.append(f"#{element_id}")
        if class_name:
            first_class = class_name.split()[0]
            if first_class:
                pieces.append(f".{first_class}")
        if text:
            pieces.append(f"'{text[:50]}'")
        return " ".join(pieces)

    def _refresh_target_summary_labels(self) -> None:
        if "raw-target" in self.detail_labels:
            self.detail_labels["raw-target"].setText(
                self._format_target_summary(self.last_hover_raw_target)
            )
        if "refined-target" in self.detail_labels:
            self.detail_labels["refined-target"].setText(
                self._format_target_summary(self.last_hover_refined_target)
            )

    def _render_candidates(self, candidates: list[LocatorCandidate]) -> None:
        previous_row = self.results_table.currentRow()
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
            target_row = previous_row if 0 <= previous_row < len(candidates) else 0
            self.results_table.selectRow(target_row)
            self._show_breakdown(candidates[target_row])
            self.locator_editor.setPlainText(candidates[target_row].locator)
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
        if row < 0 or row >= len(self.current_visible_candidates):
            return None
        return self.current_visible_candidates[row]

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


# Backward-compatible alias used by __main__.py and older imports.
MainWindow = WorkspaceWindow
