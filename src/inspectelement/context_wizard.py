from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .project_discovery import ModuleInfo, discover_modules


@dataclass(frozen=True, slots=True)
class ContextSelection:
    project_root: Path
    module: ModuleInfo


class ContextWizardDialog(QDialog):
    def __init__(
        self,
        parent=None,
        initial_project_root: Path | None = None,
        initial_module_name: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Automation Context Wizard")
        self.setModal(True)
        self.setMinimumWidth(620)

        self._initial_module_name = (initial_module_name or "").strip()
        self._selected_context: ContextSelection | None = None
        self._modules: list[ModuleInfo] = []

        title = QLabel("Select Automation Context")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")

        description = QLabel(
            "Pick your automation project root and module before inspection. "
            "Modules are detected from <root>/modules/apps/*."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #475569;")

        self.project_root_input = QLineEdit(str(initial_project_root) if initial_project_root else "")
        self.project_root_input.setPlaceholderText("Select automation project root")
        self.project_root_input.textChanged.connect(self._reload_modules)

        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self._browse_project_root)

        root_row = QHBoxLayout()
        root_row.addWidget(self.project_root_input, 1)
        root_row.addWidget(browse_button)

        module_label = QLabel("Module")
        module_label.setStyleSheet("font-weight: 600;")

        self.module_combo = QComboBox()
        self.module_combo.currentIndexChanged.connect(self._refresh_continue_state)

        self.status_label = QLabel("Select a project root.")
        self.status_label.setObjectName("WizardStatus")
        self.status_label.setWordWrap(True)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)

        self.continue_button = QPushButton("Continue")
        self.continue_button.clicked.connect(self._accept_selection)
        self.continue_button.setEnabled(False)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.continue_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(QLabel("Project Root"))
        layout.addLayout(root_row)
        layout.addWidget(module_label)
        layout.addWidget(self.module_combo)
        layout.addWidget(self.status_label)
        layout.addLayout(button_row)
        self._apply_style()

        self._reload_modules(self.project_root_input.text())

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog {
                background: #f3f5f9;
                color: #0f172a;
            }
            QLabel {
                color: #0f172a;
            }
            QLabel#WizardStatus {
                color: #334155;
            }
            QLineEdit, QComboBox {
                background: #ffffff;
                color: #0f172a;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 6px 8px;
            }
            QComboBox::drop-down {
                width: 24px;
                border-left: 1px solid #cbd5e1;
            }
            QPushButton {
                border: 1px solid #c4cad8;
                border-radius: 8px;
                padding: 7px 12px;
                background: #ffffff;
                color: #0f172a;
                min-width: 90px;
            }
            QPushButton:hover {
                background: #f1f5f9;
            }
            QPushButton:disabled {
                color: #94a3b8;
                background: #f8fafc;
            }
            """
        )

    @property
    def selected_context(self) -> ContextSelection | None:
        return self._selected_context

    def _browse_project_root(self) -> None:
        start_dir = self.project_root_input.text().strip() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "Select Automation Project Root", start_dir)
        if not directory:
            return
        self.project_root_input.setText(directory)

    def _reload_modules(self, value: str) -> None:
        project_root = Path(value.strip()).expanduser()

        self.module_combo.blockSignals(True)
        self.module_combo.clear()
        self.module_combo.addItem("Select module", None)
        self.module_combo.blockSignals(False)

        self._modules = []
        if not value.strip():
            self.status_label.setText("Select a project root.")
            self._refresh_continue_state()
            return

        if not project_root.is_dir():
            self.status_label.setText("Selected path is not a valid folder.")
            self._refresh_continue_state()
            return

        modules = discover_modules(project_root)
        self._modules = modules
        if not modules:
            self.status_label.setText("No modules found under modules/apps.")
            self._refresh_continue_state()
            return

        self.module_combo.blockSignals(True)
        for module in modules:
            self.module_combo.addItem(module.name, module)
        self.module_combo.blockSignals(False)

        selected_index = 0
        if self._initial_module_name:
            for index in range(1, self.module_combo.count()):
                module = self.module_combo.itemData(index)
                if isinstance(module, ModuleInfo) and module.name == self._initial_module_name:
                    selected_index = index
                    break

        if selected_index == 0:
            selected_index = 1
        self.module_combo.setCurrentIndex(selected_index)
        self.status_label.setText(f"{len(modules)} module(s) found.")
        self._refresh_continue_state()

    def _refresh_continue_state(self) -> None:
        root_text = self.project_root_input.text().strip()
        module = self.module_combo.currentData()
        enabled = bool(root_text) and isinstance(module, ModuleInfo)
        self.continue_button.setEnabled(enabled)

    def _accept_selection(self) -> None:
        module = self.module_combo.currentData()
        project_root = Path(self.project_root_input.text().strip()).expanduser()
        if not isinstance(module, ModuleInfo) or not project_root.is_dir():
            self.status_label.setText("Select a valid project root and module.")
            self._refresh_continue_state()
            return

        self._selected_context = ContextSelection(project_root=project_root, module=module)
        self.accept()
