from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .browser_manager import BrowserManager
from .models import ElementSummary, LocatorCandidate


class EventBridge(QObject):
    capture_received = Signal(object, object)
    status_changed = Signal(str)
    page_changed = Signal(str, str)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("inspectelement")
        self.resize(1200, 760)
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

        self.url_input = QLineEdit("https://example.com")
        self.url_input.setPlaceholderText("https://your-app-url")

        self.launch_button = QPushButton("Launch")
        self.launch_button.clicked.connect(self._launch)

        self.inspect_toggle = QPushButton("Inspect Mode: OFF")
        self.inspect_toggle.setCheckable(True)
        self.inspect_toggle.clicked.connect(self._toggle_inspect)

        self.copy_best_button = QPushButton("Copy best")
        self.copy_best_button.clicked.connect(self._copy_best)
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(["Best", "CSS", "XPath", "Playwright", "Selenium"])
        self.output_format_combo.setCurrentText("Best")

        self.reset_learning_button = QPushButton("Reset learning")
        self.reset_learning_button.clicked.connect(self._reset_learning)

        self.results_table = QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(["Rank", "Type", "Locator", "Score"])
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.itemSelectionChanged.connect(self._on_selection_changed)
        self.results_table.horizontalHeader().setStretchLastSection(False)
        self.results_table.horizontalHeader().setSectionResizeMode(0, self.results_table.horizontalHeader().ResizeMode.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(1, self.results_table.horizontalHeader().ResizeMode.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(2, self.results_table.horizontalHeader().ResizeMode.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(3, self.results_table.horizontalHeader().ResizeMode.ResizeToContents)

        self.detail_labels: dict[str, QLabel] = {}
        detail_form = QFormLayout()
        for key in ["tag", "id", "classes", "name", "role", "text", "placeholder", "aria-label"]:
            label = QLabel("-")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            detail_form.addRow(f"{key}:", label)
            self.detail_labels[key] = label

        self.breakdown_text = QPlainTextEdit()
        self.breakdown_text.setReadOnly(True)
        self.breakdown_text.setPlaceholderText("Score breakdown appears for selected locator")

        self.good_button = QPushButton("Good")
        self.good_button.clicked.connect(lambda: self._feedback(True))
        self.bad_button = QPushButton("Bad")
        self.bad_button.clicked.connect(lambda: self._feedback(False))

        feedback_row = QHBoxLayout()
        feedback_row.addWidget(self.good_button)
        feedback_row.addWidget(self.bad_button)

        details_card = QFrame()
        details_card.setFrameShape(QFrame.Shape.StyledPanel)
        details_layout = QVBoxLayout(details_card)
        details_layout.addLayout(detail_form)
        details_layout.addWidget(QLabel("Score breakdown:"))
        details_layout.addWidget(self.breakdown_text)
        details_layout.addLayout(feedback_row)

        left_col = QVBoxLayout()
        left_col.addWidget(self.results_table)

        right_col = QVBoxLayout()
        right_col.addWidget(details_card)

        grid = QHBoxLayout()
        grid.addLayout(left_col, 3)
        grid.addLayout(right_col, 2)

        top_bar = QHBoxLayout()
        top_bar.addWidget(self.url_input)
        top_bar.addWidget(self.launch_button)
        top_bar.addWidget(self.inspect_toggle)
        top_bar.addWidget(self.output_format_combo)
        top_bar.addWidget(self.copy_best_button)
        top_bar.addWidget(self.reset_learning_button)

        self.status_label = QLabel("Ready")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.addLayout(top_bar)
        root_layout.addLayout(grid)
        root_layout.addWidget(self.status_label)

        self.setCentralWidget(root)
        self._apply_style()

    def _set_icon(self) -> None:
        icon_path = Path(__file__).resolve().parents[2] / "assets" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Segoe UI", "Helvetica Neue", sans-serif;
                font-size: 13px;
            }
            QMainWindow {
                background: #f6f7fb;
            }
            QPushButton {
                border: 1px solid #c4cad8;
                border-radius: 8px;
                padding: 7px 12px;
                background: #ffffff;
            }
            QPushButton:checked {
                background: #dbeafe;
                border-color: #3b82f6;
            }
            QLineEdit, QPlainTextEdit, QTableWidget {
                background: #ffffff;
                border: 1px solid #d3d8e2;
                border-radius: 8px;
            }
            QHeaderView::section {
                background: #edf2ff;
                border: none;
                border-right: 1px solid #d3d8e2;
                padding: 6px;
            }
            """
        )

    def _launch(self) -> None:
        self.browser.launch(self.url_input.text())

    def _toggle_inspect(self) -> None:
        enabled = self.inspect_toggle.isChecked()
        self.inspect_toggle.setText(f"Inspect Mode: {'ON' if enabled else 'OFF'}")
        self.browser.set_inspect_mode(enabled)

    def _copy(self, value: str) -> None:
        QApplication.clipboard().setText(value)
        self._set_status("Locator copied.")

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

    def _reset_learning(self) -> None:
        self.browser.reset_learning()

    def _feedback(self, was_good: bool) -> None:
        candidate = self._selected_candidate()
        if not candidate:
            self._set_status("Select a locator first.")
            return
        ok = self.browser.record_feedback(candidate, was_good)
        if ok:
            self._set_status("Feedback recorded.")
            return
        self._set_status("Capture an element before sending feedback.")

    def _on_capture(self, summary: ElementSummary, candidates: list[LocatorCandidate]) -> None:
        self.current_summary = summary
        self.current_candidates = candidates
        self._render_summary(summary)
        self._render_candidates(candidates)
        self._set_status(f"Captured <{summary.tag}> with {len(candidates)} suggestions.")

    def _on_page_changed(self, title: str, url: str) -> None:
        self.setWindowTitle(f"inspectelement - {title or url}")

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

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
            locator_layout = QHBoxLayout(locator_cell)
            locator_layout.setContentsMargins(6, 1, 6, 1)

            locator_label = QLabel(candidate.locator)
            locator_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            locator_label.setWordWrap(False)

            copy_button = QPushButton("Copy")
            copy_button.clicked.connect(lambda _checked=False, text=candidate.locator: self._copy(text))

            locator_layout.addWidget(locator_label, 1)
            locator_layout.addWidget(copy_button, 0)
            self.results_table.setCellWidget(row, 2, locator_cell)

        if candidates:
            self.results_table.selectRow(0)
            self._show_breakdown(candidates[0])
        else:
            self.breakdown_text.clear()

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
        self.breakdown_text.setPlainText("\n".join(lines))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        try:
            self.browser.shutdown()
        except Exception as exc:
            QMessageBox.warning(self, "Shutdown warning", str(exc))
        super().closeEvent(event)
