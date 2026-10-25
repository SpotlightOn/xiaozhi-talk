"""Application entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .localization import use_english_qt_strings
from .log import configure_logging
from .storage import CredentialStore, StorageError
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def build_application(arguments: list[str]) -> QApplication:
    """Create the Qt application with names and theme applied."""
    # Usually already done by the package initialisation; this covers callers
    # that import the UI modules without going through the package first.
    use_english_qt_strings()
    app = QApplication(arguments)
    app.setApplicationName("Xiaozhi Talk")
    app.setApplicationDisplayName("Xiaozhi Talk")
    app.setOrganizationName("xiaozhi-talk")
    # Groups the window with the menu entry installed by install.sh.
    app.setDesktopFileName("xiaozhi-talk")
    app.setApplicationVersion(__version__)
    apply_theme(app)
    return app


def main(argv: list[str] | None = None) -> int:
    """Start the desktop client."""
    configure_logging()
    app = build_application(list(sys.argv if argv is None else argv))

    store = CredentialStore()
    try:
        vault = store.load()
    except StorageError as exc:
        QMessageBox.critical(None, "Xiaozhi Talk", f"Starting failed:\n\n{exc}")
        return 1

    window = MainWindow(store, vault)
    window.show()
    if not vault.accounts:
        # First start: guide the user through the credentials form.
        window.open_setup_wizard()
    return app.exec()
