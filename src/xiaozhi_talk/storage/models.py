"""Accounts, preferences and the vault that holds them."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

DEFAULT_OTA_URL = "https://api.tenclass.net/xiaozhi/ota"
MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

VAULT_VERSION = 1


def _has_scheme(value: str, schemes: tuple) -> bool:
    lowered = value.strip().lower()
    return any(lowered.startswith(scheme) for scheme in schemes)


@dataclass
class Account:
    """One Xiaozhi device identity used to connect to the server."""

    name: str
    mac_address: str
    ota_url: str = DEFAULT_OTA_URL
    websocket_url: str = ""
    serial_number: str = ""
    license_key: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    # -- validation ---------------------------------------------------------

    def problems(self) -> list[str]:
        """Return human readable validation problems, empty when valid."""
        issues: list[str] = []
        if not self.name.strip():
            issues.append("An account name is required.")
        if not MAC_PATTERN.match(self.mac_address.strip()):
            issues.append("The MAC address must look like 00:11:22:33:44:55.")
        # An empty OTA URL is fine: it means "use the official server".
        if self.ota_url.strip() and not _has_scheme(self.ota_url, ("http://", "https://")):
            issues.append("The OTA URL must start with http:// or https://.")
        if self.websocket_url.strip() and not _has_scheme(self.websocket_url, ("ws://", "wss://")):
            issues.append("The WebSocket URL must start with ws:// or wss://.")
        return issues

    def is_valid(self) -> bool:
        return not self.problems()

    def cleaned(self) -> Account:
        """Return a copy with surrounding whitespace removed."""
        return Account(
            id=self.id,
            name=self.name.strip(),
            mac_address=self.mac_address.strip(),
            ota_url=self.ota_url.strip(),
            websocket_url=self.websocket_url.strip(),
            serial_number=self.serial_number.strip(),
            license_key=self.license_key.strip(),
        )

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "mac_address": self.mac_address,
            "ota_url": self.ota_url,
            "websocket_url": self.websocket_url,
            "serial_number": self.serial_number,
            "license_key": self.license_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex),
            name=str(data.get("name", "")),
            mac_address=str(data.get("mac_address", "")),
            ota_url=str(data.get("ota_url", DEFAULT_OTA_URL)),
            websocket_url=str(data.get("websocket_url", "")),
            serial_number=str(data.get("serial_number", "")),
            license_key=str(data.get("license_key", "")),
        )


@dataclass
class Settings:
    """Application preferences that are not credentials."""

    play_audio: bool = True
    auto_connect: bool = True
    auto_reconnect: bool = True
    clear_chat_on_connect: bool = False
    #: Identifier of the recording device; empty means the system default.
    input_device: str = ""
    #: Hands-free: keep the microphone open and let the server split turns.
    auto_speak: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "play_audio": self.play_audio,
            "auto_connect": self.auto_connect,
            "auto_reconnect": self.auto_reconnect,
            "clear_chat_on_connect": self.clear_chat_on_connect,
            "input_device": self.input_device,
            "auto_speak": self.auto_speak,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        defaults = cls()
        return cls(
            play_audio=bool(data.get("play_audio", defaults.play_audio)),
            auto_connect=bool(data.get("auto_connect", defaults.auto_connect)),
            auto_reconnect=bool(data.get("auto_reconnect", defaults.auto_reconnect)),
            clear_chat_on_connect=bool(
                data.get("clear_chat_on_connect", defaults.clear_chat_on_connect)
            ),
            input_device=str(data.get("input_device", defaults.input_device)),
            auto_speak=bool(data.get("auto_speak", defaults.auto_speak)),
        )


@dataclass
class Vault:
    """All accounts plus the preferences, as stored in the secret backend."""

    accounts: list[Account] = field(default_factory=list)
    active_account_id: str | None = None
    settings: Settings = field(default_factory=Settings)

    # -- lookups ------------------------------------------------------------

    def account(self, account_id: str | None) -> Account | None:
        for account in self.accounts:
            if account.id == account_id:
                return account
        return None

    def active_account(self) -> Account | None:
        account = self.account(self.active_account_id)
        if account is None and self.accounts:
            return self.accounts[0]
        return account

    def add_account(self, account: Account) -> Account:
        self.accounts.append(account)
        if self.active_account_id is None:
            self.active_account_id = account.id
        return account

    def remove_account(self, account_id: str) -> None:
        self.accounts = [account for account in self.accounts if account.id != account_id]
        if self.active_account_id == account_id:
            self.active_account_id = self.accounts[0].id if self.accounts else None

    def set_active(self, account_id: str) -> None:
        if self.account(account_id) is not None:
            self.active_account_id = account_id

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": VAULT_VERSION,
            "accounts": [account.to_dict() for account in self.accounts],
            "active_account_id": self.active_account_id,
            "settings": self.settings.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Vault:
        accounts = [
            Account.from_dict(item) for item in data.get("accounts", []) if isinstance(item, dict)
        ]
        active_id = data.get("active_account_id")
        if active_id is not None and not any(a.id == active_id for a in accounts):
            active_id = accounts[0].id if accounts else None
        return cls(
            accounts=accounts,
            active_account_id=active_id,
            settings=Settings.from_dict(data.get("settings") or {}),
        )
