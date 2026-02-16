from __future__ import annotations

import sys


def main() -> int:
    if sys.version_info < (3, 11):
        raise SystemExit(
            "inspectelement requires Python 3.11+. "
            f"Current interpreter: {sys.executable} (Python {sys.version.split()[0]})"
        )
    print(f"[inspectelement doctor] sys.executable={sys.executable}")
    print(f"[inspectelement doctor] sys.version={sys.version}")
    try:
        from PySide6.QtGui import QColor, QPalette
        from PySide6.QtWidgets import QApplication
        from .main_window import MainWindow
    except ModuleNotFoundError as exc:
        if exc.name == "PySide6":
            raise SystemExit(
                "PySide6 is not installed in this interpreter. "
                "Activate the project venv and run `pip install -r requirements.txt`."
            ) from exc
        raise

    def _apply_light_palette(app: QApplication) -> None:
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#f3f5f9"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#0f172a"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafc"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#0f172a"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#0f172a"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#0284c7"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        app.setPalette(palette)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    _apply_light_palette(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
