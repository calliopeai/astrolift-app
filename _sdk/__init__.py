"""Astrolift Provider SDK -- typed driver protocol interfaces.

This package defines the contracts that provider plugins implement. Import
driver protocols and the ProviderPlugin manifest type from here.

    from _sdk import ProviderPlugin, ClusterDriver, IngressDriver
"""

from _sdk.base import (
    ClusterDriver,
    DnsDriver,
    DriverRegistry,
    ImageRegistryDriver,
    IngressDriver,
    LogStreamDriver,
    ManagedServiceDriver,
    ManagedServiceRegistry,
    MetricsDriver,
    ObjectStoreDriver,
    ProviderPlugin,
    SecretsBackend,
    TlsDriver,
    WorkloadIdentityDriver,
)

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
    "ObjectStoreDriver",
    "ProviderPlugin",
    "SecretsBackend",
    "TlsDriver",
    "WorkloadIdentityDriver",
]
