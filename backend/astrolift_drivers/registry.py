"""
In-process plugin registry.

Plugins register themselves at import time via ``plugins.register(...)``.
The control plane resolves a driver by ``(plugin_id, driver_name)``:

    cluster_driver = plugins.get("aws", "cluster")

The registry is in-process; it doesn't talk to the database. The
database-side ``ProviderPlugin`` row is the *configuration* artifact;
this registry holds the *implementation* references.
"""

from __future__ import annotations

import dataclasses
from typing import Any


class DriverNotFound(LookupError):
    """Raised when a plugin or driver isn't registered."""


@dataclasses.dataclass(slots=True, frozen=True)
class PluginManifest:
    plugin_id: str
    display_name: str
    version: str
    drivers: dict[str, type | Any]
    capabilities: tuple[str, ...] = ()


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, PluginManifest] = {}

    def register(self, manifest: PluginManifest) -> None:
        if manifest.plugin_id in self._plugins:
            raise ValueError(
                f"plugin already registered: {manifest.plugin_id} "
                f"(existing: {self._plugins[manifest.plugin_id].version}, "
                f"new: {manifest.version})"
            )
        self._plugins[manifest.plugin_id] = manifest

    def get(self, plugin_id: str, driver: str):
        try:
            manifest = self._plugins[plugin_id]
        except KeyError as exc:
            raise DriverNotFound(f"unknown plugin: {plugin_id!r}") from exc

        impl = manifest.drivers.get(driver)
        if impl is None:
            raise DriverNotFound(f"plugin {plugin_id!r} does not implement driver {driver!r}")
        return impl

    def list(self) -> list[PluginManifest]:
        return list(self._plugins.values())

    def reset(self) -> None:
        """Test-only: drop everything."""
        self._plugins.clear()


plugins = PluginRegistry()
