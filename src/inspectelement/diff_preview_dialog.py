from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class DiffPreviewDialog(QDialog):
    def __init__(
        self,
        target_file: Path,
        final_locator_name: str | None,
        method_names: list[str],
        method_signatures: list[str] | None,
        diff_text: str,
        summary_message: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preview Java Changes")
        self.setModal(True)
        self.resize(980, 640)

        path_label = QLabel(f"Target file: {target_file}")
        path_label.setWordWrap(True)
        locator_label = QLabel(f"Final locator: {final_locator_name or '-'}")
        locator_label.setWordWrap(True)

        methods_text = ", ".join(method_names) if method_names else "-"
        methods_label = QLabel(f"Methods to add: {methods_text}")
        methods_label.setWordWrap(True)

        signatures_text = "\n".join(method_signatures or []) if method_signatures else "-"
        signatures_label = QLabel("Method signatures:")
        signatures_label.setWordWrap(True)

        signatures_view = QPlainTextEdit()
        signatures_view.setReadOnly(True)
        signatures_view.setMaximumHeight(110)
        signatures_view.setPlainText(signatures_text)

        summary_label = QLabel(summary_message or "")
        summary_label.setWordWrap(True)
        summary_label.setVisible(bool(summary_message))

        diff_editor = QPlainTextEdit()
        diff_editor.setReadOnly(True)
        diff_editor.setPlainText(diff_text)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.accept)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(apply_button)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)
        root_layout.addWidget(path_label)
        root_layout.addWidget(locator_label)
        root_layout.addWidget(methods_label)
        root_layout.addWidget(signatures_label)
        root_layout.addWidget(signatures_view)
        root_layout.addWidget(summary_label)
        root_layout.addWidget(diff_editor, 1)
        root_layout.addLayout(button_row)
