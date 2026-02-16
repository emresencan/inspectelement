from __future__ import annotations

import os
import sys


def _configure_webengine_logging() -> None:
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    current_flags = {item.strip() for item in flags.split() if item.strip()}
    for required in ("--disable-logging", "--log-level=3", "--v=0"):
        current_flags.add(required)
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(sorted(current_flags))
    rules = os.environ.get("QT_LOGGING_RULES", "").strip()
    extra_rules = "qt.webengine.console=false;qt.qpa.fonts.warning=false"
    if rules:
        if extra_rules not in rules:
            os.environ["QT_LOGGING_RULES"] = f"{rules};{extra_rules}"
    else:
        os.environ["QT_LOGGING_RULES"] = extra_rules


def main() -> int:
    if sys.version_info < (3, 11):
        raise SystemExit(
            "inspectelement requires Python 3.11+. "
            f"Current interpreter: {sys.executable} (Python {sys.version.split()[0]})"
        )
    _configure_webengine_logging()
    print(f"[inspectelement doctor] sys.executable={sys.executable}")
    print(f"[inspectelement doctor] sys.version={sys.version}")
    try:
        from PySide6.QtGui import QColor, QPalette
        from PySide6.QtWidgets import QApplication
        from .main_window import WorkspaceWindow
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
    window = WorkspaceWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
