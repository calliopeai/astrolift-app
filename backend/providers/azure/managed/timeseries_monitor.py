"""Azure Monitor (Managed Prometheus) driver (#374).

Implements ``ManagedServiceDriver`` for the canonical Azure managed
time-series store. Azure Monitor's managed Prometheus surface is
the **Azure Monitor workspace** -- a Log Analytics-adjacent resource
that ingests Prometheus metrics via a Data Collection Endpoint
(DCE) and Data Collection Rule (DCR) pair and exposes both PromQL
and Kusto queries.

Concept map:

  - **Azure Monitor workspace** = the Prometheus metrics tenancy
    unit. Lives under a resource group; one per binding.
  - **Data Collection Endpoint (DCE)** = the regional ingestion
    URL for remote-write.
  - **Data Collection Rule (DCR)** = the transform/filter pipeline
    that routes scraped/remote-written samples into the workspace.
    Has an ``immutable_id`` that the client SDKs key off.
  - **Linked Log Analytics workspace** = optional sidecar for
    long-term retention beyond the Monitor workspace's TTL. The
    driver creates / links one by default; ``delete_data=False``
    preserves it on tear-down.

Four-corner deprovision matrix:

  delete_data=False, force_destroy=False (default):
    Delete the Monitor workspace + DCE + DCR. KEEP the linked Log
    Analytics workspace (long-term retention story stays alive so
    operators can restore by name from LA). Resource locks on the
    workspace are RESPECTED -- refuses cleanly when present.

  delete_data=True, force_destroy=False:
    Delete everything INCLUDING the linked Log Analytics workspace.
    Resource locks still respected.

  delete_data=False, force_destroy=True:
    Delete Monitor workspace + DCE + DCR. Keep linked LA. Remove
    blocking resource locks first.

  delete_data=True, force_destroy=True:
    --atomic. Remove locks, delete everything including linked LA.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

from _sdk._telemetry import driver_op
from _sdk.managed_service import (
    Binding,
    BindingSchema,
    DeprovisionResult,
    DeprovisionSpec,
    Grant,
    ManagedServiceDriver,
    ProvisionResult,
    ProvisionSpec,
    ServiceHandle,
    ServiceStatus,
    SnapshotHandle,
    UpdateResult,
    UpdateSpec,
    ValueRef,
)

KIND = "time_series"


class AzureMonitorPrometheusError(Exception):
    """Distinct from the generic plugin error so the control plane's
    failure-handling logic can tell managed-service failures from
    infra-driver failures."""


# Size -> default monthly ingestion cap (in millions of samples).
# Surfaced as a workspace tag so budget alerts can scope to it.
_SIZE_TO_INGESTION_CAP_MILLIONS = {
    "small": 10,
    "medium": 100,
    "large": 1000,
    "xlarge": 10_000,
}

# Size -> default retention days (Log Analytics-backed long-term
# tier). Azure Monitor workspace itself has its own short-window
# retention; this is the optional LA-linked tier the driver creates.
_SIZE_TO_RETENTION_DAYS = {
    "small": 30,
    "medium": 90,
    "large": 365,
    "xlarge": 730,
}


def tags_for(spec: ProvisionSpec) -> dict[str, str]:
    """Standard Azure tag set the platform applies to every
    managed-service resource. Mirrors the cosmos / cache-redis
    drivers' helper to keep the cross-driver shape identical."""
    base = {
        "astrolift.io/managed-by": "platform",
        "astrolift.io/organization": spec.organization_slug,
        "astrolift.io/app": spec.app_slug,
        "astrolift.io/environment": spec.environment_name,
        "astrolift.io/cluster": spec.tenant_cluster_id,
        "astrolift.io/isolation": spec.isolation,
    }
    # Per-binding cost-attribution keys (#438). Azure allows the
    # slash + dot form so keys stay identical to the canonical
    # platform schema.
    if spec.binding_id:
        base["astrolift.io/binding"] = spec.binding_id
    if spec.managed_service_id:
        base["astrolift.io/managed_service_id"] = spec.managed_service_id
    base.update(
        {f"astrolift.io/extra/{k}": v for k, v in (spec.tags or {}).items()},
    )
    return base


@dataclass(frozen=True)
class AzureMonitorPrometheusConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    subscription_id: str
    resource_group: str
    location: str = "eastus"

    workspace_name_prefix: str = "astrolift-tsdb"

    create_linked_log_analytics_default: bool = True
    """Default-on: a linked Log Analytics workspace gives long-term
    retention (configurable up to 730 days) so the delete_data=False
    path actually preserves something meaningful past the Monitor
    workspace's short window."""

    public_network_access_default: str = "Enabled"
    """``Enabled`` (default) or ``Disabled``. Disabled forces
    Private Link for ingestion + query."""

    monitor_client: Any | None = None
    """Injected ``MonitorManagementClient`` for tests."""

    log_analytics_client: Any | None = None
    """Injected ``LogAnalyticsManagementClient`` for tests."""

    locks_client: Any | None = None
    """Injected ``ManagementLockClient`` for tests. Optional in
    production -- the driver surfaces lock errors via the
    underlying API when this isn't available."""


class AzureMonitorPrometheusDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: AzureMonitorPrometheusConfig,
    ) -> None:
        self._config = config
        if config.monitor_client is not None:
            self._monitor = config.monitor_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.monitor import MonitorManagementClient

            self._monitor = MonitorManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        if config.log_analytics_client is not None:
            self._la = config.log_analytics_client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.loganalytics import LogAnalyticsManagementClient

            self._la = LogAnalyticsManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )
        if config.locks_client is not None:
            self._locks = config.locks_client
        else:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.mgmt.resource import ManagementLockClient

                self._locks = ManagementLockClient(
                    credential=DefaultAzureCredential(),
                    subscription_id=config.subscription_id,
                )
            except Exception:
                self._locks = None

    # ---- lifecycle ----------------------------------------------------

    @driver_op(
        cloud="azure",
        driver="timeseries_monitor",
        audit=True,
        sensitive_kind="managed_service_provision",
    )
    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        workspace_name = self._workspace_name_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(workspace_name)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(workspace_name=workspace_name),
                message=(
                    f"azure monitor workspace {workspace_name} "
                    f"already exists (state={_state_of(existing)})"
                ),
            )

        public_network_access = cfg.get(
            "public_network_access",
            self._config.public_network_access_default,
        )
        ingestion_cap = int(
            cfg.get(
                "ingestion_cap_millions",
                _SIZE_TO_INGESTION_CAP_MILLIONS.get(spec.size, 10),
            ),
        )
        retention_days = int(
            cfg.get(
                "retention_days",
                _SIZE_TO_RETENTION_DAYS.get(spec.size, 30),
            ),
        )
        create_linked_la = bool(
            cfg.get(
                "create_linked_log_analytics",
                self._config.create_linked_log_analytics_default,
            ),
        )

        tags = tags_for(spec)
        tags["astrolift.io/ingestion-cap-millions"] = str(ingestion_cap)
        tags["astrolift.io/retention-days"] = str(retention_days)

        # 1. Create the Azure Monitor workspace.
        workspace_parameters: dict[str, Any] = {
            "location": self._config.location,
            "tags": tags,
            "properties": {
                "public_network_access": public_network_access,
            },
        }
        try:
            poller = self._monitor.azure_monitor_workspaces.begin_create(
                resource_group_name=self._config.resource_group,
                azure_monitor_workspace_name=workspace_name,
                azure_monitor_workspace_properties=workspace_parameters,
            )
            poller.result()
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"workspaces.begin_create: {exc}",
                errors=[str(exc)],
            )

        # 2. Create the Data Collection Endpoint (DCE).
        dce_name = self._dce_name_for(workspace_name=workspace_name)
        try:
            self._monitor.data_collection_endpoints.create(
                resource_group_name=self._config.resource_group,
                data_collection_endpoint_name=dce_name,
                body={
                    "location": self._config.location,
                    "tags": tags,
                    "properties": {
                        "network_acls": {
                            "public_network_access": public_network_access,
                        },
                    },
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"data_collection_endpoints.create: {exc}",
                errors=[str(exc)],
            )

        # 3. Create the Data Collection Rule (DCR) bound to both.
        dcr_name = self._dcr_name_for(workspace_name=workspace_name)
        try:
            self._monitor.data_collection_rules.create(
                resource_group_name=self._config.resource_group,
                data_collection_rule_name=dcr_name,
                body={
                    "location": self._config.location,
                    "tags": tags,
                    "properties": {
                        "data_collection_endpoint_id": (
                            f"/subscriptions/{self._config.subscription_id}"
                            f"/resourceGroups/{self._config.resource_group}"
                            f"/providers/Microsoft.Insights"
                            f"/dataCollectionEndpoints/{dce_name}"
                        ),
                        "destinations": {
                            "monitoring_accounts": [
                                {
                                    "account_resource_id": self._workspace_resource(
                                        workspace_name=workspace_name,
                                    ),
                                    "name": "monitoring-account",
                                },
                            ],
                        },
                        "data_flows": [
                            {
                                "streams": [
                                    "Microsoft-PrometheusMetrics",
                                ],
                                "destinations": ["monitoring-account"],
                            },
                        ],
                    },
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"data_collection_rules.create: {exc}",
                errors=[str(exc)],
            )

        # 4. Optional: linked Log Analytics workspace for long-term
        # retention. The Monitor workspace's own retention is short;
        # the LA workspace is what backs the delete_data=False
        # "preserve linked LA workspace" semantic.
        if create_linked_la:
            la_name = self._la_name_for(workspace_name=workspace_name)
            try:
                self._la.workspaces.begin_create_or_update(
                    resource_group_name=self._config.resource_group,
                    workspace_name=la_name,
                    parameters={
                        "location": self._config.location,
                        "tags": tags,
                        "properties": {
                            "retention_in_days": retention_days,
                            "sku": {"name": "PerGB2018"},
                        },
                    },
                ).result()
            except Exception as exc:
                return ProvisionResult(
                    ok=False,
                    handle="",
                    message=f"log_analytics.workspaces.begin_create: {exc}",
                    errors=[str(exc)],
                )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(workspace_name=workspace_name),
            message=(
                f"azure monitor workspace {workspace_name} provisioning "
                f"(dcr={dcr_name}, dce={dce_name}, retention={retention_days}d)"
            ),
        )

    @driver_op(cloud="azure", driver="timeseries_monitor")
    def update(self, spec: UpdateSpec) -> UpdateResult:
        workspace_name = self._workspace_name_from_handle(spec.handle)
        cfg = spec.config or {}

        tags_patch: dict[str, str] = {}
        if spec.size:
            tags_patch["astrolift.io/ingestion-cap-millions"] = str(
                _SIZE_TO_INGESTION_CAP_MILLIONS.get(spec.size, 10),
            )
            tags_patch["astrolift.io/retention-days"] = str(
                _SIZE_TO_RETENTION_DAYS.get(spec.size, 30),
            )
        if "ingestion_cap_millions" in cfg:
            tags_patch["astrolift.io/ingestion-cap-millions"] = str(
                int(cfg["ingestion_cap_millions"]),
            )

        body: dict[str, Any] = {}
        if "public_network_access" in cfg:
            body["properties"] = {
                "public_network_access": cfg["public_network_access"],
            }
        if tags_patch:
            body["tags"] = tags_patch

        if "retention_days" in cfg:
            la_name = self._la_name_for(workspace_name=workspace_name)
            try:
                self._la.workspaces.update(
                    resource_group_name=self._config.resource_group,
                    workspace_name=la_name,
                    parameters={
                        "properties": {
                            "retention_in_days": int(cfg["retention_days"]),
                        },
                    },
                )
            except Exception as exc:
                return UpdateResult(
                    ok=False,
                    handle=spec.handle,
                    message=f"log_analytics.workspaces.update: {exc}",
                    errors=[str(exc)],
                )

        if not body:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message=("no monitor-workspace attributes provided -- only retention-side update applied"),
            )

        try:
            self._monitor.azure_monitor_workspaces.update(
                resource_group_name=self._config.resource_group,
                azure_monitor_workspace_name=workspace_name,
                azure_monitor_workspace_properties=body,
            )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"workspaces.update: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=(f"azure monitor workspace {workspace_name} update queued"),
        )

    @driver_op(
        cloud="azure",
        driver="timeseries_monitor",
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
        workspace_name = self._workspace_name_from_handle(spec.handle)

        existing = self._describe(workspace_name)
        if existing is None:
            if delete_data:
                self._delete_linked_la(workspace_name=workspace_name)
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=(f"azure monitor workspace {workspace_name} already gone"),
            )

        # Resource-lock guard: enumerate CanNotDelete / ReadOnly locks
        # on the workspace. force_destroy must remove them first.
        locks = self._list_locks(workspace_name=workspace_name)
        if locks and force_destroy:
            for lock in locks:
                try:
                    self._delete_lock(
                        workspace_name=workspace_name,
                        lock_name=_get(lock, "name", ""),
                    )
                except Exception as exc:
                    return DeprovisionResult(
                        ok=False,
                        handle=spec.handle,
                        message=f"failed to clear resource lock: {exc}",
                        errors=[str(exc)],
                    )
        elif locks:
            lock_names = ", ".join(str(_get(lock, "name", "?")) for lock in locks)
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"azure monitor workspace {workspace_name} has "
                    f"resource locks ({lock_names}); pass "
                    f"force_destroy=True to bypass"
                ),
                errors=["resource_lock_present"],
            )

        # Tear down the DCR + DCE first so the workspace delete
        # doesn't trip over dangling references.
        dcr_name = self._dcr_name_for(workspace_name=workspace_name)
        dce_name = self._dce_name_for(workspace_name=workspace_name)
        with contextlib.suppress(Exception):
            self._monitor.data_collection_rules.delete(
                resource_group_name=self._config.resource_group,
                data_collection_rule_name=dcr_name,
            )
        with contextlib.suppress(Exception):
            self._monitor.data_collection_endpoints.delete(
                resource_group_name=self._config.resource_group,
                data_collection_endpoint_name=dce_name,
            )

        try:
            poller = self._monitor.azure_monitor_workspaces.begin_delete(
                resource_group_name=self._config.resource_group,
                azure_monitor_workspace_name=workspace_name,
            )
            poller.result()
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"workspaces.begin_delete: {exc}",
                errors=[str(exc)],
            )

        la_action = "kept"
        if delete_data:
            self._delete_linked_la(workspace_name=workspace_name)
            la_action = "deleted"

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"azure monitor workspace {workspace_name} delete queued "
                f"(linked_log_analytics={la_action}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    @driver_op(cloud="azure", driver="timeseries_monitor")
    def status(self, handle: ServiceHandle) -> ServiceStatus:
        workspace_name = self._workspace_name_from_handle(handle.handle)
        existing = self._describe(workspace_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=(f"azure monitor workspace {workspace_name} not found"),
            )
        azure_state = _state_of(existing)
        return ServiceStatus(
            handle=handle.handle,
            state=_AZURE_STATE_TO_PROTOCOL.get(
                str(azure_state),
                "updating",
            ),
            message=f"azure reports {azure_state}",
        )

    @driver_op(cloud="azure", driver="timeseries_monitor")
    def binding(self, handle: ServiceHandle) -> Binding:
        workspace_name = self._workspace_name_from_handle(handle.handle)
        existing = self._describe(workspace_name)
        if existing is None:
            raise AzureMonitorPrometheusError(
                f"binding requested for missing workspace {workspace_name}",
            )

        dcr_name = self._dcr_name_for(workspace_name=workspace_name)
        dce_name = self._dce_name_for(workspace_name=workspace_name)
        query_endpoint = _query_endpoint_of(existing) or (
            f"https://{workspace_name}.{self._config.location}.prometheus.monitor.azure.com"
        )
        # DCE ingestion URL: looked up on the DCE resource; falls back
        # to the conventional Azure URL when describe is not available
        # (test fakes etc.).
        ingestion_endpoint = self._dce_endpoint(dce_name=dce_name) or (
            f"https://{dce_name}.{self._config.location}.handler.control.monitor.azure.com"
        )
        dcr_immutable_id = self._dcr_immutable_id(dcr_name=dcr_name) or dcr_name

        return Binding(
            env_vars={
                # Canonical contract envs (managed_service_kinds.py)
                "TIME_SERIES_URL": ValueRef(literal=query_endpoint),
                "TIME_SERIES_BUCKET": ValueRef(literal=workspace_name),
                "TIME_SERIES_ORG": ValueRef(literal=self._config.resource_group),
                # Azure-flavoured aliases
                "AZURE_MONITOR_QUERY_ENDPOINT": ValueRef(literal=query_endpoint),
                "AZURE_MONITOR_INGESTION_ENDPOINT": ValueRef(literal=ingestion_endpoint),
                "AZURE_MONITOR_DCR_IMMUTABLE_ID": ValueRef(literal=dcr_immutable_id),
                "AZURE_MONITOR_WORKSPACE_NAME": ValueRef(literal=workspace_name),
                "AZURE_SUBSCRIPTION_ID": ValueRef(literal=self._config.subscription_id),
                "AZURE_RESOURCE_GROUP": ValueRef(literal=self._config.resource_group),
            },
            iam_grants=[
                Grant(
                    resource=self._workspace_resource(
                        workspace_name=workspace_name,
                    ),
                    actions=[
                        "Microsoft.Monitor/accounts/read",
                        "Microsoft.Monitor/accounts/data/metrics/read",
                        "Microsoft.Monitor/accounts/data/metrics/write",
                    ],
                ),
                Grant(
                    resource=self._dcr_resource(dcr_name=dcr_name),
                    actions=[
                        "Microsoft.Insights/dataCollectionRules/read",
                        ("Microsoft.Insights/dataCollectionRules/data/action"),
                    ],
                ),
            ],
            notes=(
                "Azure Monitor managed Prometheus uses AAD tokens via "
                "Workload Identity. TIME_SERIES_URL is the PromQL "
                "endpoint; remote-write goes to the DCE ingestion URL "
                "with the DCR immutable id as the route key. Tokens "
                "are ambient (no static API key)."
            ),
        )

    @driver_op(cloud="azure", driver="timeseries_monitor")
    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """Azure Monitor managed Prometheus has no first-party
        snapshot API. The linked Log Analytics workspace is the
        durability story (queryable retention up to ``retention_days``
        on the LA side). We return a synthetic snapshot id so callers
        can persist a marker for restore-from-LA via Kusto."""
        from datetime import UTC, datetime

        workspace_name = self._workspace_name_from_handle(handle.handle)
        existing = self._describe(workspace_name)
        if existing is None:
            raise AzureMonitorPrometheusError(
                f"snapshot for missing workspace {workspace_name}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{workspace_name}-pit-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

    @driver_op(cloud="azure", driver="timeseries_monitor")
    def restore(
        self,
        snapshot: SnapshotHandle,
        target: ProvisionSpec,
    ) -> ProvisionResult:
        provisioned = self.provision(target)
        if not provisioned.ok:
            return provisioned
        return ProvisionResult(
            ok=True,
            handle=provisioned.handle,
            message=(
                f"target workspace provisioned; metrics at "
                f"{snapshot.snapshot_id} stay queryable on the source's "
                f"linked Log Analytics workspace within retention"
            ),
        )

    @driver_op(cloud="azure", driver="timeseries_monitor", heartbeat=False)
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "public_network_access": {
                    "type": "string",
                    "enum": ["Enabled", "Disabled"],
                },
                "ingestion_cap_millions": {
                    "type": "integer",
                    "minimum": 1,
                },
                "retention_days": {
                    "type": "integer",
                    "minimum": 4,
                    "maximum": 730,
                },
                "create_linked_log_analytics": {"type": "boolean"},
            },
        }

    @driver_op(cloud="azure", driver="timeseries_monitor", heartbeat=False)
    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "TIME_SERIES_URL": "Managed Prometheus PromQL query endpoint",
                "TIME_SERIES_BUCKET": "Workspace name (bucket-equivalent)",
                "TIME_SERIES_ORG": "Azure resource group (org-equivalent)",
                "AZURE_MONITOR_QUERY_ENDPOINT": ("PromQL query URL (alias of TIME_SERIES_URL)"),
                "AZURE_MONITOR_INGESTION_ENDPOINT": ("Data Collection Endpoint ingestion URL"),
                "AZURE_MONITOR_DCR_IMMUTABLE_ID": ("Data Collection Rule immutable id"),
                "AZURE_MONITOR_WORKSPACE_NAME": "Azure Monitor workspace name",
                "AZURE_SUBSCRIPTION_ID": "Azure subscription id hosting the workspace",
                "AZURE_RESOURCE_GROUP": "Resource group hosting the workspace",
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, workspace_name: str) -> Any | None:
        try:
            return self._monitor.azure_monitor_workspaces.get(
                resource_group_name=self._config.resource_group,
                azure_monitor_workspace_name=workspace_name,
            )
        except Exception as exc:
            if type(exc).__name__ == "ResourceNotFoundError":
                return None
            if "ResourceNotFound" in str(exc):
                return None
            return None

    def _list_locks(self, *, workspace_name: str) -> list[Any]:
        if self._locks is None:
            return []
        try:
            return list(
                self._locks.management_locks.list_at_resource_level(
                    resource_group_name=self._config.resource_group,
                    resource_provider_namespace="Microsoft.Monitor",
                    parent_resource_path="",
                    resource_type="accounts",
                    resource_name=workspace_name,
                ),
            )
        except Exception:
            return []

    def _delete_lock(
        self,
        *,
        workspace_name: str,
        lock_name: str,
    ) -> None:
        if self._locks is None:
            return
        self._locks.management_locks.delete_at_resource_level(
            resource_group_name=self._config.resource_group,
            resource_provider_namespace="Microsoft.Monitor",
            parent_resource_path="",
            resource_type="accounts",
            resource_name=workspace_name,
            lock_name=lock_name,
        )

    def _delete_linked_la(self, *, workspace_name: str) -> None:
        la_name = self._la_name_for(workspace_name=workspace_name)
        try:
            self._la.workspaces.begin_delete(
                resource_group_name=self._config.resource_group,
                workspace_name=la_name,
            ).result()
        except Exception:
            return

    def _dce_endpoint(self, *, dce_name: str) -> str:
        try:
            dce = self._monitor.data_collection_endpoints.get(
                resource_group_name=self._config.resource_group,
                data_collection_endpoint_name=dce_name,
            )
        except Exception:
            return ""
        # SDK shape varies; both attribute + dict access supported.
        endpoint = _get(dce, "logs_ingestion", None)
        if endpoint:
            url = _get(endpoint, "endpoint", "")
            if url:
                return str(url)
        # Older SDK surface puts it under metrics_ingestion.
        metrics = _get(dce, "metrics_ingestion", None)
        if metrics:
            url = _get(metrics, "endpoint", "")
            if url:
                return str(url)
        return ""

    def _dcr_immutable_id(self, *, dcr_name: str) -> str:
        try:
            dcr = self._monitor.data_collection_rules.get(
                resource_group_name=self._config.resource_group,
                data_collection_rule_name=dcr_name,
            )
        except Exception:
            return ""
        immutable = _get(dcr, "immutable_id", "") or _get(dcr, "immutableId", "")
        return str(immutable) if immutable else ""

    def _workspace_name_for(self, *, spec: ProvisionSpec) -> str:
        # Azure Monitor workspace names: 4-63 chars, alphanumeric +
        # hyphen, must start with a letter or digit.
        parts = [
            self._config.workspace_name_prefix,
            spec.organization_slug,
            spec.app_slug,
            spec.environment_name,
        ]
        raw = "-".join(p for p in parts if p).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        if not clean or not (clean[0].isalpha() or clean[0].isdigit()):
            clean = "w" + clean
        return clean[:63]

    def _handle_for(self, *, workspace_name: str) -> str:
        return f"{KIND}/{workspace_name}"

    def _workspace_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise AzureMonitorPrometheusError(
                f"handle {handle!r} must be '<kind>/<workspace_name>'",
            )
        kind, _, workspace_name = handle.partition("/")
        if not kind or not workspace_name:
            raise AzureMonitorPrometheusError(
                f"handle {handle!r} has empty component",
            )
        return workspace_name

    def _workspace_resource(self, *, workspace_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.Monitor/accounts/{workspace_name}"
        )

    def _dcr_resource(self, *, dcr_name: str) -> str:
        return (
            f"/subscriptions/{self._config.subscription_id}"
            f"/resourceGroups/{self._config.resource_group}"
            f"/providers/Microsoft.Insights/dataCollectionRules/{dcr_name}"
        )

    def _dce_name_for(self, *, workspace_name: str) -> str:
        return f"{workspace_name}-dce"[:43]

    def _dcr_name_for(self, *, workspace_name: str) -> str:
        return f"{workspace_name}-dcr"[:64]

    def _la_name_for(self, *, workspace_name: str) -> str:
        # Log Analytics workspace names: 4-63 chars, letters/digits/-,
        # must start with letter or digit, can't end with hyphen.
        return f"{workspace_name}-la"[:63].rstrip("-")


# ----- module-level helpers --------------------------------------------


def _state_of(workspace: Any) -> str:
    state = getattr(workspace, "provisioning_state", None)
    if state is not None:
        return str(state)
    state = getattr(workspace, "provisioningState", None)
    if state is not None:
        return str(state)
    if isinstance(workspace, dict):
        if "provisioning_state" in workspace:
            return str(workspace["provisioning_state"])
        if "provisioningState" in workspace:
            return str(workspace["provisioningState"])
    return "Unknown"


def _query_endpoint_of(workspace: Any) -> str:
    ep = getattr(workspace, "metrics", None)
    if ep is not None:
        url = getattr(ep, "prometheus_query_endpoint", None) or getattr(
            ep,
            "prometheusQueryEndpoint",
            None,
        )
        if url:
            return str(url)
    if isinstance(workspace, dict):
        metrics = workspace.get("metrics") or {}
        url = metrics.get("prometheus_query_endpoint") or metrics.get(
            "prometheusQueryEndpoint",
        )
        if url:
            return str(url)
    return ""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_AZURE_STATE_TO_PROTOCOL = {
    "Succeeded": "available",
    "Creating": "provisioning",
    "Updating": "updating",
    "Deleting": "deprovisioning",
    "Failed": "error",
    "Canceled": "error",
    "Unknown": "updating",
}
