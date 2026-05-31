"""astrolift-sdk-python — public Astrolift provider SDK (#67).

This package is the typed surface plugin authors depend on. It is
deliberately small + dependency-free so a third-party plugin can
import it without pulling in any cloud SDK.

Anything exported here is part of the public API and is governed by
the project's compatibility policy. Internal helpers live in their
respective sibling modules and are not re-exported.

Splitting into a separate distribution (`astrolift-sdk-python` on
PyPI) is a packaging-only change: copy `_sdk/` to its own repo +
publish. The import path stays `from _sdk.<module> import ...` so
plugin code doesn't churn.

Usage:
    from _sdk import ProviderPlugin, ClusterDriver, IngressDriver
"""

from __future__ import annotations


class UnsupportedOperationError(NotImplementedError):
    """The driver does not implement this operation on this backend.

    Distinct from ``NotImplementedError`` so resolvers + the no-stub CI gate
    can tell a deliberate "not supported on this cloud" from a partial-stub
    bug. The audit (#586-#623) flagged 14 files with ``raise
    NotImplementedError`` inside otherwise-real driver methods; the CI gate
    catches whole-class stubs but missed those because the surrounding
    class is real. This subtype lets the resolver layer catch the
    capability-gap case explicitly and translate to a "not supported on
    this cloud" user-facing message, while a bare ``NotImplementedError``
    still surfaces as a 500 / bug.
    """


# Exception class is defined ahead of imports on purpose: the SDK submodules
# below import this class via a deferred ``from _sdk import
# UnsupportedOperationError`` from inside their methods. Declaring it here
# at module top means the import resolves the moment ``_sdk`` is loaded.


# Catalog + matrix + capability + cluster registry
# ruff: noqa: E402
# #586-#623 driver-level telemetry framework. ``@driver_op`` wraps every
# driver method with structured logging + Prometheus metrics + OTEL spans +
# Temporal heartbeats + audit emission. ``maybe_heartbeat`` is the loop-
# inside helper for driver methods whose body takes longer than the
# decorator's single entry-time heartbeat allows.
from _sdk._telemetry import (
    driver_op,
    maybe_heartbeat,
)
from _sdk.availability import (
    OPTIONAL_ROLES,
    REQUIRED_ROLES,
    AvailabilityMatrix,
    DriverEntry,
    ManagedServiceEntry,
)

# Core plugin manifest types + per-role driver protocols
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

# Cross-cutting protocols
from _sdk.blob_store import (
    ABSBlobStoreDriver,
    BlobInfo,
    BlobStoreDriver,
    BlobStoreError,
    BlobStoreNotConfiguredError,
    BlobStoreNotFoundError,
    BlobStoreSizeError,
    GCSBlobStoreDriver,
    LocalFsBlobStoreDriver,
    MinioBlobStoreDriver,
    S3BlobStoreDriver,
    artifact_blob_key,
    get_blob_driver,
    run_blob_prefix,
)
from _sdk.build import BuildDriver
from _sdk.capabilities import (
    BindingValidation,
    ServiceDependency,
    validate_cluster_binding,
)

# Pod observability shapes (#299) — re-exported so backend resolvers
# only depend on the public SDK surface. ``ClusterContext`` +
# ``ManagementReport`` join them in the #316 bring-into-management
# surface for the same reason.
from _sdk.cluster import (
    ApplyError,
    ApplyResult,
    ClusterAuth,
    ClusterContext,
    ContainerStatusInfo,
    ManagementReport,
    PodInfo,
    PodLogLine,
    classify_apply_error,
)
from _sdk.cluster_capabilities import (
    ClusterCapabilities,
    probe_capabilities,
)
from _sdk.cluster_registry import (
    ProbeResult,
    TenantClusterRecord,
    TenantClusterRegistry,
    probe_connectivity,
)
from _sdk.composition import CompositionRegistry
from _sdk.cost import (
    CostEstimate,
    CostEstimateRequest,
    CostEstimateUnavailable,
    CostEstimator,
    CostLineItem,
    CostResult,
)

# Policy modules (storage encryption, edge security, postgres
# extensions, variant migration, identity chains, CSI, instrumentation)
from _sdk.csi import CsiDriverProfile, render_storage_class
from _sdk.edge_security import (
    DEFAULT_PROFILE,
    EdgeSecurityProfile,
    HstsPolicy,
    SecurityHeaders,
    TlsPolicy,
    WafPolicy,
)
from _sdk.email import (
    AccountSendStatus,
    DkimToken,
    DnsAuthCheck,
    DnsAuthStatus,
    DnsCheckOutcome,
    EmailObservabilityDriver,
    EmailTemplate,
    IdentityVerification,
    SendQuota,
    SendStatPoint,
    SuppressionEntry,
    SuppressionReason,
    TemplateSendStatPoint,
)
from _sdk.event import EventDriver
from _sdk.identity_chain import (
    IdentityChain,
    IdentityHop,
    build_simple_chain,
    validate_chain,
)
from _sdk.instrumentation import (
    OTEL_COLLECTOR_SIDECAR,
    VECTOR_SIDECAR,
    IngressTracingConfig,
    ServiceMeshConfig,
    SidecarSpec,
    compose_instrumentation,
    inject_sidecar,
)
from _sdk.managed_service_kinds import (
    KINDS,
    KindCatalog,
    ManagedServiceKind,
    validate_binding_envs,
)
from _sdk.parity import (
    ParityIssue,
    ParityReport,
    check_plugin_parity,
)
from _sdk.postgres_extensions import (
    ExtensionEntry,
    VariantExtensionPolicy,
    validate_extensions,
)
from _sdk.storage_encryption import (
    EncryptionPolicy,
    check_encryption,
)
from _sdk.trace import TraceDriver
from _sdk.variant_migration import (
    MigrationCatalog,
    MigrationRecipe,
)

__all__ = [
    "ABSBlobStoreDriver",
    "BlobInfo",
    "BlobStoreDriver",
    "BlobStoreError",
    "BlobStoreNotConfiguredError",
    "BlobStoreNotFoundError",
    "BlobStoreSizeError",
    "GCSBlobStoreDriver",
    "LocalFsBlobStoreDriver",
    "MinioBlobStoreDriver",
    "S3BlobStoreDriver",
    "artifact_blob_key",
    "get_blob_driver",
    "run_blob_prefix",
    "DEFAULT_PROFILE",
    "KINDS",
    "OPTIONAL_ROLES",
    "OTEL_COLLECTOR_SIDECAR",
    "REQUIRED_ROLES",
    "VECTOR_SIDECAR",
    "ApplyError",
    "ApplyResult",
    "AvailabilityMatrix",
    "BindingValidation",
    "BuildDriver",
    "ClusterAuth",
    "ClusterCapabilities",
    "ClusterContext",
    "ClusterDriver",
    "CompositionRegistry",
    "ContainerStatusInfo",
    "CostEstimate",
    "CostEstimateRequest",
    "CostEstimateUnavailable",
    "CostEstimator",
    "CostLineItem",
    "CostResult",
    "AccountSendStatus",
    "CsiDriverProfile",
    "DkimToken",
    "DnsAuthCheck",
    "DnsAuthStatus",
    "DnsCheckOutcome",
    "DnsDriver",
    "EmailObservabilityDriver",
    "EmailTemplate",
    "DriverEntry",
    "DriverRegistry",
    "EdgeSecurityProfile",
    "EncryptionPolicy",
    "EventDriver",
    "ExtensionEntry",
    "HstsPolicy",
    "IdentityChain",
    "IdentityHop",
    "IdentityVerification",
    "ImageRegistryDriver",
    "IngressDriver",
    "IngressTracingConfig",
    "KindCatalog",
    "LogStreamDriver",
    "ManagedServiceDriver",
    "ManagedServiceEntry",
    "ManagedServiceKind",
    "ManagedServiceRegistry",
    "ManagementReport",
    "MetricsDriver",
    "MigrationCatalog",
    "MigrationRecipe",
    "ObjectStoreDriver",
    "ParityIssue",
    "ParityReport",
    "PodInfo",
    "PodLogLine",
    "ProbeResult",
    "ProviderPlugin",
    "SecretsBackend",
    "SecurityHeaders",
    "SendQuota",
    "SendStatPoint",
    "ServiceDependency",
    "ServiceMeshConfig",
    "SidecarSpec",
    "SuppressionEntry",
    "SuppressionReason",
    "TemplateSendStatPoint",
    "TenantClusterRecord",
    "TenantClusterRegistry",
    "TlsDriver",
    "TlsPolicy",
    "TraceDriver",
    "UnsupportedOperationError",
    "VariantExtensionPolicy",
    "WafPolicy",
    "WorkloadIdentityDriver",
    "build_simple_chain",
    "check_encryption",
    "check_plugin_parity",
    "classify_apply_error",
    "compose_instrumentation",
    "driver_op",
    "inject_sidecar",
    "maybe_heartbeat",
    "probe_capabilities",
    "probe_connectivity",
    "render_storage_class",
    "validate_binding_envs",
    "validate_chain",
    "validate_cluster_binding",
    "validate_extensions",
]
