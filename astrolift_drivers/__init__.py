"""Astrolift driver SDK — typed Protocols every provider plugin implements.

The SDK is intentionally pure Python ``Protocol`` types: zero runtime
behavior beyond duck-typed dispatch. Provider plugins live in the
separate ``astrolift-providers`` repo and depend only on this package.

See ``specs/02-multi-cloud-k8s-abstraction.md`` §2 (driver catalog).
"""

from astrolift_drivers.protocols import (
    BuildDriver,
    ClusterDriver,
    DnsDriver,
    EventDriver,
    ImageRegistryDriver,
    IngressDriver,
    LogStreamDriver,
    ManagedServiceDriver,
    MetricsDriver,
    ObjectStoreDriver,
    SecretsBackend,
    TlsDriver,
    TraceDriver,
    WorkloadIdentityDriver,
)
from astrolift_drivers.registry import (
    DriverNotFound,
    PluginManifest,
    PluginRegistry,
    plugins,
)

__all__ = [
    "BuildDriver",
    "ClusterDriver",
    "DnsDriver",
    "DriverNotFound",
    "EventDriver",
    "ImageRegistryDriver",
    "IngressDriver",
    "LogStreamDriver",
    "ManagedServiceDriver",
    "MetricsDriver",
    "ObjectStoreDriver",
    "PluginManifest",
    "PluginRegistry",
    "SecretsBackend",
    "TlsDriver",
    "TraceDriver",
    "WorkloadIdentityDriver",
    "plugins",
]
