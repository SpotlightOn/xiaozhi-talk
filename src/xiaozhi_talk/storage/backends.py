"""Places that can keep the vault payload.

Two backends exist: the operating system keyring, which is preferred, and a
Fernet encrypted file that is used when no keyring is usable.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path
from typing import Any

from .errors import StorageError
from .paths import write_private_file

logger = logging.getLogger(__name__)

BACKEND_KEYRING = "keyring"
BACKEND_FILE = "file"


class SecretBackend:
    """Interface of a store for the serialised vault."""

    key = "abstract"
    name = "abstract"
    description = "abstract"

    def read(self) -> str | None:
        raise NotImplementedError

    def write(self, payload: str) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


class KeyringBackend(SecretBackend):
    """Vault stored in the operating system keyring through ``keyring``."""

    key = BACKEND_KEYRING
    name = "System keyring"
    description = "operating system keyring, Secret Service or KWallet"

    SERVICE = "xiaozhi-talk"
    ENTRY = "vault"

    def __init__(self, module: Any = None) -> None:
        self._module = module

    def _keyring(self) -> Any:
        if self._module is None:
            # Imported lazily: the package is an optional dependency.
            import keyring

            self._module = keyring
        return self._module

    def read(self) -> str | None:
        return self._keyring().get_password(self.SERVICE, self.ENTRY)

    def write(self, payload: str) -> None:
        self._keyring().set_password(self.SERVICE, self.ENTRY, payload)

    def clear(self) -> None:
        try:
            self._keyring().delete_password(self.SERVICE, self.ENTRY)
        except Exception as exc:  # nothing stored yet
            logger.debug("Keyring delete failed: %s", exc)


class EncryptedFileBackend(SecretBackend):
    """Vault stored as a Fernet encrypted file next to its 0600 key."""

    key = BACKEND_FILE
    name = "Encrypted file"
    description = "Fernet encrypted file with owner-only permissions"

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.key_path = self.directory / "vault.key"
        self.data_path = self.directory / "vault.enc"

    def _fernet(self) -> Any:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:  # pragma: no cover - required dependency
            raise StorageError(
                "The 'cryptography' package is required for encrypted storage."
            ) from exc

        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            write_private_file(self.key_path, key)
        return Fernet(key)

    def read(self) -> str | None:
        if not self.data_path.exists():
            return None

        from cryptography.fernet import InvalidToken

        try:
            return self._fernet().decrypt(self.data_path.read_bytes()).decode("utf-8")
        except InvalidToken as exc:
            raise StorageError(
                "The stored credentials could not be decrypted. "
                f"Delete {self.data_path} and set up the account again."
            ) from exc

    def write(self, payload: str) -> None:
        token = self._fernet().encrypt(payload.encode("utf-8"))
        write_private_file(self.data_path, token)

    def clear(self) -> None:
        for path in (self.data_path, self.key_path):
            with contextlib.suppress(FileNotFoundError):
                path.unlink()


def probe_keyring(module: Any = None, timeout: float = 3.0) -> bool:
    """Return True when a usable, unlocked keyring answers in time.

    Secret Service calls can block while a wallet is locked or while a dialog
    waits for the user, so the probe runs in a daemon thread.
    """
    result = {"usable": False}

    def probe() -> None:
        try:
            if module is not None:
                keyring_module = module
            else:
                import keyring as keyring_module

            backend = keyring_module.get_keyring()
            if type(backend).__module__.endswith("backends.fail"):
                return
            keyring_module.get_password(KeyringBackend.SERVICE, KeyringBackend.ENTRY)
            result["usable"] = True
        except Exception as exc:  # no keyring, no session bus, locked wallet, ...
            logger.debug("Keyring probe failed: %s", exc)

    thread = threading.Thread(target=probe, name="keyring-probe", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        logger.debug("Keyring probe timed out after %.1fs", timeout)
        return False
    return bool(result["usable"])
