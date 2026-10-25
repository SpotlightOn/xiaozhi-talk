"""Compatibility shims that must be applied before ``xiaozhi_sdk`` is imported.

``xiaozhi_sdk`` 0.5.1 uses ``websockets.protocol.State``, which is no longer
reachable as a package attribute in websockets 17 because submodules are
imported lazily.  Importing the module explicitly restores the attribute the
SDK expects.
"""

from __future__ import annotations

import websockets.protocol  # noqa: F401  (needed by xiaozhi_sdk)
