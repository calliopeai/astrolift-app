"""GCP search managed-service stub driver (#373).

GCP doesn't ship a first-party managed Elasticsearch / OpenSearch
service the way AWS (OpenSearch Service) and Azure (AI Search) do.
The Google Cloud Search product is a Workspace-tenant search
surface for indexing Drive / third-party connectors -- not a
general-purpose full-text index for app workloads. The supported
Google-recommended path is to provision Elastic Cloud on GCP via
the Google Cloud Marketplace, which is out of the control plane's
purchase-flow scope today (separate procurement + contracting).

This stub keeps the (kind, variant) catalog complete so the
platform's matrix validator (#26) recognises GCP as covering the
``search`` kind. Every lifecycle entry raises ``NotImplementedError``
with a clear operator message. When the Marketplace path lands
this file flips to a real driver behind the same class name; no
plugin-registration change required at that point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
)

KIND = "search"


_UNSUPPORTED_MSG = (
    "GCP doesn't ship a first-party managed Elasticsearch — use "
    "Elastic Cloud on GCP via the Marketplace, or self-host on GKE"
)


@dataclass(frozen=True)
class GCPElasticCloudStubConfig:
    """No fields today. Kept as a dataclass so a future real driver
    can grow config without breaking the plugin registration."""

    project_id: str = ""


class GCPElasticCloudStubDriver:
    """Stub driver for the GCP ``search`` slot.

    Implements the ``ManagedServiceDriver`` shape but raises
    ``NotImplementedError`` on lifecycle calls. Read-only schemas
    are populated so docs + binding contracts stay accurate."""

    def __init__(
        self,
        *,
        config: GCPElasticCloudStubConfig | None = None,
    ) -> None:
        self._config = config or GCPElasticCloudStubConfig()

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="gcp",
        driver="search_elastic_cloud_stub",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="search_elastic_cloud_stub")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(
        cloud="gcp",
        driver="search_elastic_cloud_stub",
        audit=True,
        sensitive_kind="managed_service_deprovision",
    )
    def deprovision(
        self,
        spec: DeprovisionSpec,
        *,
        delete_data: bool = False,
        force_destroy: bool = False,
    ) -> DeprovisionResult:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="search_elastic_cloud_stub")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="search_elastic_cloud_stub")
    def binding(self, handle: ServiceHandle) -> Binding:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="search_elastic_cloud_stub")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    @driver_op(cloud="gcp", driver="search_elastic_cloud_stub")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        raise NotImplementedError(_UNSUPPORTED_MSG)

    # ---- read-only schemas (kept accurate so docs render) -------------

    @driver_op(cloud="gcp", driver="search_elastic_cloud_stub", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "description": (
                "Stub: GCP search variant is not yet implemented. Provisioning will raise NotImplementedError."
            ),
            "properties": {},
        }

    @driver_op(cloud="gcp", driver="search_elastic_cloud_stub", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "SEARCH_URL": ("Elastic Cloud HTTPS endpoint (not yet implemented)"),
                "SEARCH_API_KEY": ("Secret Manager ref to the deployment API key (not yet implemented)"),
                "SEARCH_INDEX_PREFIX": ("Index-name prefix for the app (not yet implemented)"),
            },
        )

    @driver_op(cloud="gcp", driver="search_elastic_cloud_stub", heartbeat=False)
    def editable_fields(self) -> list[str]:
        # Stub — update() raises anyway; "*" matches the protocol default.
        return ["*"]
