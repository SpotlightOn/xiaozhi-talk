"""Account and credential storage.

The public names of this package are re-exported here so the rest of the
application only needs ``from xiaozhi_talk.storage import ...``.
"""

from .backends import BACKEND_FILE, BACKEND_KEYRING, probe_keyring
from .errors import StorageError
from .models import DEFAULT_OTA_URL, Account, Settings, Vault
from .paths import CONFIG_DIR_ENV, config_dir
from .store import CredentialStore

__all__ = [
    "BACKEND_FILE",
    "BACKEND_KEYRING",
    "CONFIG_DIR_ENV",
    "DEFAULT_OTA_URL",
    "Account",
    "CredentialStore",
    "Settings",
    "StorageError",
    "Vault",
    "config_dir",
    "probe_keyring",
]
