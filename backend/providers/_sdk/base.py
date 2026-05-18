"""Provider plugin base types and driver registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from _sdk.cluster import ClusterDriver
from _sdk.dns import DnsDriver
from _sdk.identity import WorkloadIdentityDriver
from _sdk.ingress import IngressDriver
from _sdk.log_stream import LogStreamDriver
from _sdk.managed_service import ManagedServiceDriver
from _sdk.metrics import MetricsDriver
from _sdk.notification import NotificationDriver
from _sdk.object_store import ObjectStoreDriver
from _sdk.registry import ImageRegistryDriver
from _sdk.secrets import SecretsBackend
from _sdk.tls import TlsDriver

# Maps a driver role name to the concrete class implementing it.
DriverRegistry = dict[str, type[Any]]

# Maps a (kind, variant) tuple to a ManagedServiceDriver implementation.
ManagedServiceRegistry = dict[tuple[str, str], type[ManagedServiceDriver]]


@dataclass(frozen=True)
class ProviderPlugin:
    """Manifest for a provider plugin.

    A plugin is a typed bundle that implements some subset of the driver
    protocols. It is loaded at control-plane boot via the
    ``astrolift.providers`` entry point and bound to tenant clusters by
    their ``provider_plugin_id``.

    Not every plugin must implement every driver. The control plane
    validates at cluster registration time that a cluster's required
    capabilities are covered.
    """

    id: str
    display_name: str
    drivers: DriverRegistry = field(default_factory=dict)
    managed_service_drivers: ManagedServiceRegistry = field(default_factory=dict)
    config_schema: dict[str, Any] = field(default_factory=dict)

    def has_driver(self, role: str) -> bool:
        """Return whether this plugin provides a driver for the given role."""
        return role in self.drivers

    def get_driver(self, role: str) -> type[Any] | None:
        """Return the driver class for the given role, or None."""
        return self.drivers.get(role)

    def get_managed_service_driver(self, kind: str, variant: str) -> type[ManagedServiceDriver] | None:
        """Return the managed-service driver for the given (kind, variant), or None."""
        return self.managed_service_drivers.get((kind, variant))


__all__ = [
    "ClusterDriver",
    "DnsDriver",
    "DriverRegistry",
    "ImageRegistryDriver",
    "IngressDriver",
    "LogStreamDriver",
    "ManagedServiceDriver",
    "ManagedServiceRegistry",
    "MetricsDriver",
    "NotificationDriver",
    "ObjectStoreDriver",
    "ProviderPlugin",
    "SecretsBackend",
    "TlsDriver",
    "WorkloadIdentityDriver",
]
