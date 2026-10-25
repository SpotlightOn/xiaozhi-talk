"""First run assistant that collects the Xiaozhi credentials."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ..storage import DEFAULT_OTA_URL, Account, CredentialStore
from ..text import format_mac


class CredentialsPage(QWizardPage):
    """Form page that validates the device identity while typing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Device credentials")
        self.setSubTitle("Use the identity of a Xiaozhi device that is registered with the server.")

        self._name = QLineEdit("My Xiaozhi device")
        self._name.setPlaceholderText("Home speaker")
        self._mac = QLineEdit()
        self._mac.setPlaceholderText("00:11:22:33:44:55")
        self._ota = QLineEdit(DEFAULT_OTA_URL)
        self._ws = QLineEdit()
        self._ws.setPlaceholderText("ws://192.168.1.20:8000  (optional)")
        self._serial = QLineEdit()
        self._license = QLineEdit()
        self._license.setEchoMode(QLineEdit.Password)
        self._show_license = QCheckBox("Show")

        layout = QFormLayout(self)
        layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(9)
        layout.addRow("Name", self._name)
        layout.addRow("MAC address", self._mac)
        layout.addRow("OTA URL", self._ota)
        layout.addRow("WebSocket URL", self._ws)
        layout.addRow("Serial number", self._serial)

        license_row = QWidget()
        license_layout = QHBoxLayout(license_row)
        license_layout.setContentsMargins(0, 0, 0, 0)
        license_layout.setSpacing(8)
        license_layout.addWidget(self._license, 1)
        license_layout.addWidget(self._show_license)
        layout.addRow("License key", license_row)

        hint = QLabel(
            "The MAC address is the Device-Id that the Xiaozhi server knows; you can "
            "copy it from the Xiaozhi app or web console. The OTA URL is queried for the "
            "WebSocket URL and access token; leave it empty to use the official server. "
            "Enter a WebSocket URL only if you run your own Xiaozhi server. Serial number "
            "and license key are optional: leave both empty unless your server asks you "
            "to activate this device."
        )
        hint.setObjectName("FormHint")
        hint.setWordWrap(True)
        layout.addRow("", hint)

        self._issues = QLabel("")
        self._issues.setObjectName("FormError")
        self._issues.setWordWrap(True)
        layout.addRow("", self._issues)

        for editor in (self._name, self._mac, self._ota, self._ws, self._serial, self._license):
            editor.textChanged.connect(self._revalidate)
        self._show_license.toggled.connect(
            lambda shown: self._license.setEchoMode(
                QLineEdit.Normal if shown else QLineEdit.Password
            )
        )
        self._revalidate()

    # -- validation ---------------------------------------------------------

    def account(self) -> Account:
        return Account(
            name=self._name.text().strip(),
            mac_address=format_mac(self._mac.text()),
            ota_url=self._ota.text().strip(),
            websocket_url=self._ws.text().strip(),
            serial_number=self._serial.text().strip(),
            license_key=self._license.text().strip(),
        )

    def isComplete(self) -> bool:  # noqa: N802 - Qt naming
        return self.account().is_valid()

    def _revalidate(self) -> None:
        problems = self.account().problems()
        self._issues.setText("\n".join(problems))
        self.completeChanged.emit()


class SummaryPage(QWizardPage):
    """Final page that repeats what will be stored and where."""

    def __init__(self, backend_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Ready to chat")
        self._backend_label = backend_label
        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.RichText)
        layout = QVBoxLayout(self)
        layout.addWidget(self._body)
        layout.addStretch(1)

    def set_account(self, account: Account) -> None:
        self._body.setText(
            "<p>The following credentials will be stored:</p>"
            f"<p><b>Name:</b> {_escape(account.name)}<br>"
            f"<b>MAC address:</b> {_escape(account.mac_address)}<br>"
            f"<b>OTA URL:</b> {_escape(account.ota_url or '—')}<br>"
            f"<b>WebSocket URL:</b> {_escape(account.websocket_url or 'from the OTA URL')}</p>"
            f"<p style='color:#7b828d'>Storage: {_escape(self._backend_label)}</p>"
            "<p>You can change or add more accounts later in "
            "<i>Settings → Manage settings and accounts…</i></p>"
        )


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class SetupWizard(QWizard):
    """Three step guide shown when no account has been configured yet."""

    def __init__(self, store: CredentialStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set up Xiaozhi Talk")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.HaveHelpButton, False)
        # Qt translates standard buttons for the desktop locale; this app is
        # English only, so the texts are set explicitly.
        self.setButtonText(QWizard.NextButton, "Next >")
        self.setButtonText(QWizard.BackButton, "< Back")
        self.setButtonText(QWizard.CancelButton, "Cancel")
        self.setButtonText(QWizard.FinishButton, "Start chatting")
        self.resize(680, 520)

        self._store = store
        self.addPage(self._welcome_page())
        self._credentials = CredentialsPage()
        self.addPage(self._credentials)
        self._summary = SummaryPage(store.backend_label)
        self.addPage(self._summary)

    # -- pages --------------------------------------------------------------

    def _welcome_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Welcome to Xiaozhi Talk")
        page.setSubTitle("This desktop client talks to a Xiaozhi server like a device does.")
        body = QLabel()
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setText(
            "<p>To start a conversation you need the identity of a Xiaozhi device:</p>"
            "<ul>"
            "<li><b>MAC address</b> – the Device-Id registered with the server. "
            "Copy it from the Xiaozhi app or from xiaozhi.me, or use the MAC address of "
            "an already activated device.</li>"
            "<li><b>OTA URL</b> – asked for the WebSocket URL and access token. The "
            "public server address is filled in already.</li>"
            "<li><b>Serial number and license key</b> – only needed while a device is "
            "not activated yet.</li>"
            "</ul>"
            f"<p>Your credentials are kept in the <b>{_escape(self._store.backend_label)}</b>. "
            "No plain text file is written.</p>"
        )
        layout = QVBoxLayout(page)
        layout.addWidget(body)
        layout.addStretch(1)
        return page

    # -- behaviour ----------------------------------------------------------

    def account(self) -> Account:
        return self._credentials.account()

    def accept(self) -> None:
        if not self.account().is_valid():
            return
        super().accept()

    def nextId(self) -> int:  # noqa: N802 - Qt naming
        next_id = super().nextId()
        if next_id == 2:
            self._summary.set_account(self.account())
        return next_id
