"""Shared pytest fixtures."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Qt must not try to open a real window while testing.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never touch the real user configuration or keyring during tests.
os.environ.setdefault("XIAOZHI_TALK_LOG_LEVEL", "CRITICAL")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Qt reads the locale while it is imported, so this has to happen first.
from xiaozhi_talk.localization import use_english_qt_strings  # noqa: E402

use_english_qt_strings()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    from xiaozhi_talk.ui.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    yield app


@pytest.fixture
def store(tmp_path):
    from xiaozhi_talk.storage import CredentialStore

    return CredentialStore(tmp_path, prefer_keyring=False)


@pytest.fixture
def account():
    from xiaozhi_talk.storage import Account

    return Account(name="Test device", mac_address="00:11:22:33:44:55")


@pytest.fixture
def vault(account):
    from xiaozhi_talk.storage import Vault

    vault = Vault()
    vault.add_account(account)
    vault.settings.auto_connect = False
    return vault
