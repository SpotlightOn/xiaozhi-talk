"""Dialog for managing accounts and application settings."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..audio_input import MULTIMEDIA_AVAILABLE as CAPTURE_AVAILABLE
from ..audio_input import input_devices
from ..storage import Account, CredentialStore, Vault
from ..text import format_mac


class SettingsAccountsDialog(QDialog):
    """Edit accounts, choose the default account and tweak preferences."""

    def __init__(
        self,
        vault: Vault,
        store: CredentialStore,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings and Accounts")
        self.setModal(True)
        self.resize(820, 620)

        self._vault = copy.deepcopy(vault)
        self._store = store
        self._editing = -1
        self._loading = False

        tabs = QTabWidget()
        tabs.addTab(self._build_accounts_page(), "Accounts")
        tabs.addTab(self._build_settings_page(), "Settings")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        # Explicit texts keep the dialog English on a localised desktop, and
        # the style icons would only crowd the labels.
        save_button = buttons.button(QDialogButtonBox.Ok)
        save_button.setText("Save")
        save_button.setIcon(QIcon())
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        cancel_button.setText("Cancel")
        cancel_button.setIcon(QIcon())
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)

        self._refresh_list(select=0)

    # -- result -------------------------------------------------------------

    @property
    def vault(self) -> Vault:
        return self._vault

    # -- accounts page ------------------------------------------------------

    def _build_accounts_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(16)

        # left: list of accounts
        left = QVBoxLayout()
        left.setSpacing(8)
        self._list = QListWidget()
        self._list.setMaximumWidth(250)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.currentRowChanged.connect(self._on_selected)
        left.addWidget(self._list, 1)

        add_button = QPushButton("Add")
        remove_button = QPushButton("Remove")
        add_button.clicked.connect(self._add_account)
        remove_button.clicked.connect(self._remove_account)
        self._remove_button = remove_button
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(add_button)
        row.addWidget(remove_button)
        left.addLayout(row)

        self._default_button = QPushButton("Set as default")
        self._default_button.clicked.connect(self._set_default)
        left.addWidget(self._default_button)

        self._default_label = QLabel("")
        self._default_label.setObjectName("FormHint")
        self._default_label.setWordWrap(True)
        left.addWidget(self._default_label)

        layout.addLayout(left, 0)

        # right: the form
        group = QGroupBox("Account details")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Living room speaker")
        self._mac = QLineEdit()
        self._mac.setPlaceholderText("00:11:22:33:44:55")
        self._ota = QLineEdit()
        self._ota.setPlaceholderText("https://api.tenclass.net/xiaozhi/ota")
        self._ws = QLineEdit()
        self._ws.setPlaceholderText("ws://192.168.1.20:8000  (optional)")
        self._serial = QLineEdit()
        self._license = QLineEdit()
        self._license.setEchoMode(QLineEdit.Password)
        self._show_license = QCheckBox("Show")
        self._show_license.toggled.connect(
            lambda shown: self._license.setEchoMode(
                QLineEdit.Normal if shown else QLineEdit.Password
            )
        )
        license_row = QWidget()
        license_layout = QHBoxLayout(license_row)
        license_layout.setContentsMargins(0, 0, 0, 0)
        license_layout.setSpacing(8)
        license_layout.addWidget(self._license, 1)
        license_layout.addWidget(self._show_license)

        form.addRow("Name", self._name)
        form.addRow("MAC address", self._mac)
        form.addRow("OTA URL", self._ota)
        form.addRow("WebSocket URL", self._ws)
        form.addRow("Serial number", self._serial)
        form.addRow("License key", license_row)

        hint = QLabel(
            "The MAC address is the device identity the Xiaozhi server knows. "
            "Leave the OTA URL empty for the official server, and the WebSocket URL "
            "empty to let the OTA URL provide it; enter a WebSocket URL only if you "
            "run your own server. Serial number and license key are optional: leave "
            "both empty unless your server asks you to activate this device."
        )
        hint.setObjectName("FormHint")
        hint.setWordWrap(True)
        form.addRow("", hint)

        self._issues = QLabel("")
        self._issues.setObjectName("FormError")
        self._issues.setWordWrap(True)
        form.addRow("", self._issues)

        self._fields: list[QWidget] = [
            self._name,
            self._mac,
            self._ota,
            self._ws,
            self._serial,
            self._license,
            self._show_license,
        ]
        for widget in self._fields:
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._refresh_issues)

        layout.addWidget(group, 1)
        return page

    def _refresh_list(self, select: int = 0) -> None:
        default_id = self._vault.active_account_id
        self._loading = True
        self._list.clear()
        for account in self._vault.accounts:
            label = account.name or "Unnamed account"
            if account.id == default_id:
                label += "  (default)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, account.id)
            self._list.addItem(item)
        self._loading = False

        count = self._list.count()
        if count:
            self._list.setCurrentRow(max(0, min(select, count - 1)))
        else:
            self._editing = -1
            self._load_form(-1)
        self._remove_button.setEnabled(bool(count))
        self._default_button.setEnabled(bool(count))
        self._refresh_default_label()

    def _on_selected(self, row: int) -> None:
        if self._loading:
            return
        self._commit_form()
        self._editing = row
        self._load_form(row)

    def _load_form(self, row: int) -> None:
        accounts = self._vault.accounts
        enabled = 0 <= row < len(accounts)
        for widget in self._fields:
            widget.setEnabled(enabled)
        self._loading = True
        if enabled:
            account = accounts[row]
            self._name.setText(account.name)
            self._mac.setText(account.mac_address)
            self._ota.setText(account.ota_url)
            self._ws.setText(account.websocket_url)
            self._serial.setText(account.serial_number)
            self._license.setText(account.license_key)
        else:
            for widget in (self._name, self._mac, self._ota, self._ws, self._serial, self._license):
                widget.clear()
        self._loading = False
        self._refresh_issues()

    def _commit_form(self) -> None:
        if not (0 <= self._editing < len(self._vault.accounts)):
            return
        account = self._vault.accounts[self._editing]
        account.name = self._name.text().strip()
        account.mac_address = format_mac(self._mac.text())
        account.ota_url = self._ota.text().strip()
        account.websocket_url = self._ws.text().strip()
        account.serial_number = self._serial.text().strip()
        account.license_key = self._license.text().strip()

        item = self._list.item(self._editing)
        if item is not None:
            label = account.name or "Unnamed account"
            if account.id == self._vault.active_account_id:
                label += "  (default)"
            item.setText(label)

    def _form_account(self) -> Account | None:
        if not (0 <= self._editing < len(self._vault.accounts)):
            return None
        base = self._vault.accounts[self._editing]
        return Account(
            id=base.id,
            name=self._name.text().strip(),
            mac_address=format_mac(self._mac.text()),
            ota_url=self._ota.text().strip(),
            websocket_url=self._ws.text().strip(),
            serial_number=self._serial.text().strip(),
            license_key=self._license.text().strip(),
        )

    def _refresh_issues(self) -> None:
        account = self._form_account()
        problems = account.problems() if account is not None else []
        self._issues.setText("\n".join(problems))

    def _refresh_default_label(self) -> None:
        active = self._vault.active_account()
        if active is None:
            self._default_label.setText("No account configured yet.")
        else:
            self._default_label.setText(f"Connects to: {active.name or active.mac_address}")

    def _add_account(self) -> None:
        self._commit_form()
        names = {account.name for account in self._vault.accounts}
        index = len(self._vault.accounts) + 1
        name = f"Account {index}"
        while name in names:
            index += 1
            name = f"Account {index}"
        account = Account(name=name, mac_address="")
        self._vault.add_account(account)
        self._refresh_list(select=len(self._vault.accounts) - 1)
        self._name.setFocus()
        self._name.selectAll()

    def _remove_account(self) -> None:
        row = self._list.currentRow()
        if not (0 <= row < len(self._vault.accounts)):
            return
        account = self._vault.accounts[row]
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Warning)
        message.setWindowTitle("Remove account")
        message.setText(f'Remove "{account.name}" and its stored credentials?')
        remove_button = message.addButton("Remove", QMessageBox.AcceptRole)
        message.addButton("Cancel", QMessageBox.RejectRole)
        message.exec()
        if message.clickedButton() is not remove_button:
            return
        self._editing = -1
        self._vault.remove_account(account.id)
        self._refresh_list(select=max(0, row - 1))

    def _set_default(self) -> None:
        row = self._list.currentRow()
        if not (0 <= row < len(self._vault.accounts)):
            return
        self._commit_form()
        self._vault.set_active(self._vault.accounts[row].id)
        self._refresh_list(select=row)

    # -- settings page ------------------------------------------------------

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        chat_group = QGroupBox("Chat")
        chat_layout = QVBoxLayout(chat_group)
        self._play_audio = QCheckBox("Play voice replies through the speakers")
        self._auto_connect = QCheckBox("Connect to the default account at startup")
        self._auto_reconnect = QCheckBox("Reconnect automatically when the connection drops")
        self._clear_on_connect = QCheckBox("Clear the chat log whenever a new connection opens")
        chat_layout.addWidget(self._play_audio)
        chat_layout.addWidget(self._auto_connect)
        chat_layout.addWidget(self._auto_reconnect)
        chat_layout.addWidget(self._clear_on_connect)

        voice_group = QGroupBox("Voice input")
        voice_layout = QVBoxLayout(voice_group)
        voice_layout.setSpacing(10)
        voice_form = QFormLayout()
        voice_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        voice_form.setHorizontalSpacing(14)
        self._input_device = QComboBox()
        self._input_device.addItem("System default", "")
        for identifier, description in input_devices():
            self._input_device.addItem(description, identifier)
        index = self._input_device.findData(self._vault.settings.input_device)
        self._input_device.setCurrentIndex(max(0, index))
        voice_form.addRow("Microphone", self._input_device)
        voice_layout.addLayout(voice_form)

        voice_hint = QLabel(
            "The microphone button records with this device. The system default "
            "follows whatever the operating system is set to, so it keeps working "
            "when you plug in another headset."
            if CAPTURE_AVAILABLE
            else "Recording needs Qt Multimedia, which is not installed, so the "
            "microphone button stays disabled."
        )
        voice_hint.setObjectName("FormHint")
        voice_hint.setWordWrap(True)
        voice_layout.addWidget(voice_hint)

        security_group = QGroupBox("Credential storage")
        security_layout = QVBoxLayout(security_group)
        backend = QLabel(f"Credentials are stored in: {self._store.backend_label}")
        backend.setWordWrap(True)
        note = QLabel(
            "Account data is never written to a plain text file. It lives in the "
            "operating system keyring, or in a Fernet encrypted file whose key and "
            "data are readable by your user only."
        )
        note.setObjectName("FormHint")
        note.setWordWrap(True)
        security_layout.addWidget(backend)
        security_layout.addWidget(note)

        layout.addWidget(chat_group)
        layout.addWidget(voice_group)
        layout.addWidget(security_group)
        layout.addStretch(1)

        self._play_audio.setChecked(self._vault.settings.play_audio)
        self._auto_connect.setChecked(self._vault.settings.auto_connect)
        self._auto_reconnect.setChecked(self._vault.settings.auto_reconnect)
        self._clear_on_connect.setChecked(self._vault.settings.clear_chat_on_connect)
        return page

    # -- accept -------------------------------------------------------------

    def _on_accept(self) -> None:
        self._commit_form()
        invalid = [account for account in self._vault.accounts if not account.is_valid()]
        if invalid:
            account = invalid[0]
            lines = "\n".join(f"• {problem}" for problem in account.problems())
            QMessageBox.warning(
                self,
                "Incomplete account",
                f'The account "{account.name or "Unnamed account"}" is incomplete:\n\n{lines}',
            )
            return
        if not self._vault.accounts:
            QMessageBox.warning(
                self,
                "No account",
                "Add at least one account before closing the dialog.",
            )
            return
        self._vault.settings.play_audio = self._play_audio.isChecked()
        self._vault.settings.auto_connect = self._auto_connect.isChecked()
        self._vault.settings.auto_reconnect = self._auto_reconnect.isChecked()
        self._vault.settings.clear_chat_on_connect = self._clear_on_connect.isChecked()
        self._vault.settings.input_device = self._input_device.currentData() or ""
        self.accept()
