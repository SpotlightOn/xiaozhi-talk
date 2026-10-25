"""Tests for account validation and the credential store."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from xiaozhi_talk.storage import (
    DEFAULT_OTA_URL,
    Account,
    CredentialStore,
    StorageError,
    Vault,
    config_dir,
)
from xiaozhi_talk.storage.backends import BACKEND_FILE, BACKEND_KEYRING


class FakeKeyring:
    """Minimal stand-in for the ``keyring`` module."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], str] = {}
        self.locked = False

    def get_keyring(self) -> FakeKeyring:
        return self

    def _check(self) -> None:
        if self.locked:
            raise RuntimeError("wallet is locked")

    def get_password(self, service: str, entry: str) -> str | None:
        self._check()
        return self.entries.get((service, entry))

    def set_password(self, service: str, entry: str, value: str) -> None:
        self._check()
        self.entries[(service, entry)] = value

    def delete_password(self, service: str, entry: str) -> None:
        self._check()
        self.entries.pop((service, entry), None)


# ---------------------------------------------------------------------------
# account validation
# ---------------------------------------------------------------------------


def test_account_accepts_a_valid_device() -> None:
    account = Account(name="Kitchen", mac_address="00:11:22:33:44:55")
    assert account.problems() == []
    assert account.is_valid()


@pytest.mark.parametrize(
    "mac",
    ["", "00:11:22:33:44", "001122334455", "zz:11:22:33:44:55", "00-11-22-33-44-55"],
)
def test_account_rejects_bad_mac_addresses(mac: str) -> None:
    account = Account(name="Kitchen", mac_address=mac)
    assert any("MAC address" in problem for problem in account.problems())


def test_account_needs_a_name() -> None:
    account = Account(name="  ", mac_address="00:11:22:33:44:55", ota_url="")
    assert any("name" in problem for problem in account.problems())


def test_account_without_any_url_uses_the_official_server() -> None:
    account = Account(name="Kitchen", mac_address="00:11:22:33:44:55", ota_url="", websocket_url="")
    assert account.problems() == []


@pytest.mark.parametrize(
    ("ota_url", "websocket_url", "valid"),
    [
        (DEFAULT_OTA_URL, "", True),
        ("", "ws://192.168.1.20:8000", True),
        ("", "wss://example.com/ws", True),
        ("ftp://example.com", "", False),
        ("", "http://example.com", False),
    ],
)
def test_account_url_schemes(ota_url: str, websocket_url: str, valid: bool) -> None:
    account = Account(
        name="Kitchen",
        mac_address="00:11:22:33:44:55",
        ota_url=ota_url,
        websocket_url=websocket_url,
    )
    assert account.is_valid() is valid


def test_cleaned_strips_whitespace() -> None:
    account = Account(name="  Kitchen  ", mac_address=" 00:11:22:33:44:55 ")
    cleaned = account.cleaned()
    assert cleaned.name == "Kitchen"
    assert cleaned.mac_address == "00:11:22:33:44:55"
    assert cleaned.id == account.id


# ---------------------------------------------------------------------------
# vault behaviour
# ---------------------------------------------------------------------------


def test_vault_tracks_the_active_account(account: Account) -> None:
    vault = Vault()
    first = vault.add_account(account)
    second = vault.add_account(Account(name="Second", mac_address="aa:bb:cc:dd:ee:ff"))

    assert vault.active_account() is first
    vault.set_active(second.id)
    assert vault.active_account() is second

    vault.remove_account(second.id)
    assert vault.active_account() is first
    assert len(vault.accounts) == 1


def test_vault_round_trip(account: Account) -> None:
    vault = Vault()
    account.license_key = "secret-license"
    vault.add_account(account)
    vault.settings.play_audio = False
    vault.settings.input_device = "usb-headset"
    vault.settings.auto_speak = True

    restored = Vault.from_dict(vault.to_dict())
    assert restored.accounts[0].license_key == "secret-license"
    assert restored.accounts[0].id == account.id
    assert restored.active_account_id == account.id
    assert restored.settings.play_audio is False
    assert restored.settings.input_device == "usb-headset"
    assert restored.settings.auto_speak is True


# ---------------------------------------------------------------------------
# encrypted file backend
# ---------------------------------------------------------------------------


def test_file_backend_round_trip(store: CredentialStore, vault: Vault, account: Account) -> None:
    account.license_key = "very-secret"
    store.save(vault)

    assert store.backend.key == BACKEND_FILE
    assert (store.directory / "vault.enc").exists()
    assert (store.directory / "vault.key").exists()

    loaded = CredentialStore(store.directory, prefer_keyring=False).load()
    assert loaded.accounts[0].mac_address == account.mac_address
    assert loaded.accounts[0].license_key == "very-secret"


def test_file_backend_keeps_credentials_out_of_plain_text(
    store: CredentialStore, vault: Vault, account: Account
) -> None:
    account.license_key = "very-secret"
    store.save(vault)

    meta = (store.directory / "config.json").read_text("utf-8")
    encrypted = (store.directory / "vault.enc").read_bytes()

    assert "very-secret" not in meta
    assert account.mac_address not in meta
    assert b"very-secret" not in encrypted
    assert account.mac_address.encode() not in encrypted


def test_stored_files_are_private(store: CredentialStore, vault: Vault) -> None:
    store.save(vault)
    for name in ("config.json", "vault.enc", "vault.key"):
        mode = stat.S_IMODE((store.directory / name).stat().st_mode)
        assert mode == 0o600, f"{name} has mode {oct(mode)}"


def test_corrupted_vault_raises(store: CredentialStore, vault: Vault) -> None:
    store.save(vault)
    (store.directory / "vault.enc").write_bytes(b"not a fernet token")

    with pytest.raises(StorageError):
        CredentialStore(store.directory, prefer_keyring=False).load()


# ---------------------------------------------------------------------------
# keyring backend
# ---------------------------------------------------------------------------


def test_keyring_backend_round_trip(tmp_path: Path, vault: Vault, account: Account) -> None:
    keyring = FakeKeyring()
    store = CredentialStore(tmp_path, prefer_keyring=True, keyring_module=keyring)
    store.save(vault)

    assert store.backend.key == BACKEND_KEYRING
    assert keyring.entries
    assert not (tmp_path / "vault.enc").exists()

    loaded = CredentialStore(tmp_path, prefer_keyring=True, keyring_module=keyring).load()
    assert loaded.accounts[0].name == account.name


def test_without_keyring_the_stored_keyring_vault_is_ignored(tmp_path: Path, vault: Vault) -> None:
    keyring = FakeKeyring()
    CredentialStore(tmp_path, prefer_keyring=True, keyring_module=keyring).save(vault)

    isolated = CredentialStore(tmp_path / "elsewhere", prefer_keyring=False, keyring_module=keyring)

    assert isolated.load().accounts == []


def test_without_keyring_nothing_is_written_to_it(tmp_path: Path, vault: Vault) -> None:
    keyring = FakeKeyring()
    store = CredentialStore(tmp_path, prefer_keyring=False, keyring_module=keyring)

    store.save(vault)

    assert store.backend.key == BACKEND_FILE
    assert keyring.entries == {}
    assert (tmp_path / "vault.enc").exists()


def test_locked_keyring_keeps_the_stored_backend(tmp_path: Path, vault: Vault) -> None:
    keyring = FakeKeyring()
    CredentialStore(tmp_path, prefer_keyring=True, keyring_module=keyring).save(vault)

    keyring.locked = True
    store = CredentialStore(tmp_path, prefer_keyring=True, keyring_module=keyring)
    # The probe fails, so the encrypted file is tried first, but the meta file
    # still names the keyring as the authoritative store.
    assert store.load().accounts == []
    assert json.loads((tmp_path / "config.json").read_text("utf-8"))["secret_backend"] == (
        BACKEND_KEYRING
    )


def test_saves_to_file_when_keyring_reports_failure(tmp_path: Path, vault: Vault) -> None:
    keyring = FakeKeyring()
    store = CredentialStore(tmp_path, prefer_keyring=True, keyring_module=keyring)
    keyring.locked = True
    store.backend = store._file_backend()
    store.save(vault)

    assert (tmp_path / "vault.enc").exists()
    assert CredentialStore(tmp_path, prefer_keyring=False).load().accounts


# ---------------------------------------------------------------------------
# configuration directory
# ---------------------------------------------------------------------------


def test_config_dir_honours_the_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XIAOZHI_TALK_CONFIG_DIR", str(tmp_path / "custom"))
    assert config_dir() == tmp_path / "custom"


def test_config_dir_falls_back_to_xdg(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("XIAOZHI_TALK_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_dir() == tmp_path / "xiaozhi-talk"
