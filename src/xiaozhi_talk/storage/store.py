"""The credential store that load and saves the vault."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .backends import (
    BACKEND_KEYRING,
    EncryptedFileBackend,
    KeyringBackend,
    SecretBackend,
    probe_keyring,
)
from .errors import StorageError
from .models import VAULT_VERSION, Vault
from .paths import config_dir, write_private_file

logger = logging.getLogger(__name__)


class CredentialStore:
    """Loads and saves the :class:`~xiaozhi_talk.storage.models.Vault`.

    The backend is remembered in ``config.json`` so that later starts use the
    same place. When a backend cannot serve the vault anymore, the other one is
    tried before giving up.

    ``prefer_keyring=False`` turns the system keyring off completely: neither
    reading nor writing touches it. Tests and tools that work on a throw-away
    directory must use it, otherwise they would read and overwrite the
    credentials of the signed in user.
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        prefer_keyring: bool = True,
        keyring_module: Any = None,
    ) -> None:
        self.directory = Path(directory) if directory is not None else config_dir()
        self.meta_path = self.directory / "config.json"
        self._prefer_keyring = prefer_keyring
        self._keyring_module = keyring_module
        self.backend: SecretBackend = self._default_backend()

    # -- backend selection --------------------------------------------------

    @property
    def backend_label(self) -> str:
        return f"{self.backend.name} ({self.backend.description})"

    def _default_backend(self) -> SecretBackend:
        if self._prefer_keyring and probe_keyring(self._keyring_module):
            return self._keyring_backend()
        return self._file_backend()

    def _keyring_backend(self) -> KeyringBackend:
        return KeyringBackend(self._keyring_module)

    def _file_backend(self) -> EncryptedFileBackend:
        return EncryptedFileBackend(self.directory)

    def _candidates(self, preferred: str | None) -> list[SecretBackend]:
        if not self._prefer_keyring:
            return [self._file_backend()]
        preferred = preferred or self.backend.key
        if preferred == BACKEND_KEYRING:
            return [self._keyring_backend(), self._file_backend()]
        return [self._file_backend(), self._keyring_backend()]

    # -- meta file ----------------------------------------------------------

    def _read_meta(self) -> dict[str, Any]:
        try:
            return json.loads(self.meta_path.read_text("utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable %s: %s", self.meta_path, exc)
            return {}

    def _write_meta(self, backend: SecretBackend) -> None:
        meta = self._read_meta()
        meta["version"] = VAULT_VERSION
        meta["secret_backend"] = backend.key
        payload = json.dumps(meta, indent=2).encode("utf-8")
        try:
            write_private_file(self.meta_path, payload)
        except OSError as exc:  # pragma: no cover - read-only filesystem, ...
            logger.warning("Could not write %s: %s", self.meta_path, exc)

    # -- public API ---------------------------------------------------------

    def load(self) -> Vault:
        """Read the vault, falling back to the other backend when needed."""
        preferred = self._read_meta().get("secret_backend")
        candidates = self._candidates(preferred)
        failed: list[SecretBackend] = []

        for backend in candidates:
            try:
                payload = backend.read()
            except StorageError:
                raise
            except Exception as exc:  # keyring unavailable, file unreadable, ...
                logger.warning("Secret backend %s is unavailable: %s", backend.name, exc)
                failed.append(backend)
                continue
            if not payload:
                continue

            vault = self._decode(payload)
            self.backend = backend
            if preferred != backend.key:
                self._write_meta(backend)
            return vault

        # Nothing readable. Keep using the preferred backend when it merely
        # failed, so a locked keyring does not silently switch the storage.
        primary = candidates[0]
        self.backend = primary
        if primary in failed:
            return Vault()
        if preferred != primary.key:
            self._write_meta(primary)
        return Vault()

    def save(self, vault: Vault) -> None:
        """Persist ``vault``, switching the backend when the current one fails."""
        payload = json.dumps(vault.to_dict(), ensure_ascii=False, indent=2)
        problems: list[str] = []

        for backend in self._candidates(self.backend.key):
            try:
                backend.write(payload)
            except Exception as exc:
                logger.warning("Could not write vault via %s: %s", backend.name, exc)
                problems.append(f"{backend.name}: {exc}")
                continue
            self.backend = backend
            self._write_meta(backend)
            return

        raise StorageError("Could not save the credentials: " + "; ".join(problems))

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _decode(payload: str) -> Vault:
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise StorageError("The stored credentials are damaged and cannot be read.") from exc
        return Vault.from_dict(data)
