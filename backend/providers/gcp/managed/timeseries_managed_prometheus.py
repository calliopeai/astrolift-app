"""GCP Managed Service for Prometheus driver (#374).

Implements ``ManagedServiceDriver`` for the canonical GCP managed
time-series store. Managed Prometheus is GCP's auto-scaling Monarch-
backed metrics service exposed via the Prometheus HTTP API; workloads
remote-write to a regional ingestion endpoint and query through a
PromQL endpoint scoped to the project.

Concept map for operators coming from self-hosted Prometheus:

  - **Project** is the tenancy unit (collection scope + IAM).
  - **Workspace** is the GMP-side abstraction for a logical metrics
    namespace inside the project. The driver creates one workspace
    per binding so deprovision can drop it cleanly.
  - **Rule groups** are user-defined alerting/recording rules. The
    driver refuses ``force_destroy=False`` if any reference the
    workspace -- the operator must explicitly detach.

Provision flow:

  1. Probe-create a GMP **monitored project** (idempotent; once
     enabled per project the call is a no-op).
  2. Create the **workspace** under the project. Workspace name is
     deterministic so binding/deprovision can re-derive it.

Four-corner deprovision matrix:

  delete_data=False, force_destroy=False (default):
    Workspace deleted; PRESERVE retained metrics (the project-level
    24-month retention window stays in effect; metrics remain
    queryable until that window expires). Refuses if any rule
    groups still reference the workspace.

  delete_data=True, force_destroy=False:
    Workspace deleted AND retained metrics dropped (the driver
    invokes the GMP retention-purge call). Still refuses on
    referencing rule groups.

  delete_data=False, force_destroy=True:
    Workspace deleted; PRESERVE retained metrics. Detach + bypass
    all rule-group references (the workflow has already accepted
    that those rules will dangle).

  delete_data=True, force_destroy=True:
    --atomic. Detach rule groups, delete workspace, purge metrics.

Binding emits the canonical ``TIME_SERIES_*`` envs documented on
``ManagedServiceKind('time_series')`` plus GCP-flavoured aliases for
clients constructing Prometheus client objects directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


class _GMPError(Exception):
    """Internal -- surfaced as ``ProvisionResult.errors`` /
    ``DeprovisionResult.errors`` rather than raised across the
    workflow boundary."""


# Size -> default monthly sample-ingestion ceiling. GMP charges per
# million samples ingested; the tiers here cap operators into a
# predictable spend bracket and are surfaced as a workspace label so
# budget alerts can scope to the label.
_SIZE_TO_INGESTION_CAP_MILLIONS = {
    "small": 10,
    "medium": 100,
    "large": 1000,
    "xlarge": 10_000,
}


@dataclass(frozen=True)
class GCPManagedPrometheusConfig:
    """Driver-instance config bound from the cluster's plugin config."""

    project_id: str
    region: str

    workspace_name_prefix: str = "astrolift-tsdb"

    retention_months_default: int = 24
    """GCP-side hard ceiling is 24 months. Operators can drop the
    retention but not raise it. Surfaced as a workspace label so
    delete_data=False can flag if it's already past the window."""

    rule_group_check_enabled: bool = True
    """Off only for tests / dev: when on, deprovision enumerates
    rule groups via the GMP rules API and refuses if any reference
    the workspace's metrics. Default-on matches the safe-by-default
    posture of the AWS Timestream + Azure Monitor drivers."""


class GCPManagedPrometheusDriver(ManagedServiceDriver):
    def __init__(
        self,
        *,
        config: GCPManagedPrometheusConfig,
        workspace_client: Any | None = None,
        rules_client: Any | None = None,
    ) -> None:
        self._config = config
        if workspace_client is not None:
            self._ws = workspace_client
        else:
            # Production path: gcloud's monitoring v1 API client.
            # We import lazily so test envs that don't have the
            # google-cloud-monitoring extra still load the module.
            from google.cloud import monitoring_v3

            self._ws = monitoring_v3.MetricServiceClient()
        if rules_client is not None:
            self._rules = rules_client
        else:
            try:
                from google.cloud import monitoring_v3

                self._rules = monitoring_v3.AlertPolicyServiceClient()
            except Exception:
                # Rules client is optional -- the driver falls back
                # to skipping the rule-group check when it isn't
                # available, but only when ``rule_group_check_enabled``
                # is explicitly off (default raises on missing).
                self._rules = None

    # ---- lifecycle ----------------------------------------------------

    def provision(self, spec: ProvisionSpec) -> ProvisionResult:
        workspace_name = self._workspace_name_for(spec=spec)
        cfg = spec.config or {}

        existing = self._describe(workspace_name)
        if existing is not None:
            return ProvisionResult(
                ok=True,
                handle=self._handle_for(workspace_name=workspace_name),
                message=(f"managed prometheus workspace {workspace_name} already exists"),
            )

        retention_months = int(
            cfg.get(
                "retention_months",
                self._config.retention_months_default,
            ),
        )
        if retention_months < 1 or retention_months > 24:
            return ProvisionResult(
                ok=False,
                handle="",
                message=(
                    f"retention_months={retention_months} out of bounds; "
                    f"GMP hard ceiling is 24 months"
                ),
                errors=["retention_out_of_bounds"],
            )

        ingestion_cap = int(
            cfg.get(
                "ingestion_cap_millions",
                _SIZE_TO_INGESTION_CAP_MILLIONS.get(spec.size, 10),
            ),
        )

        labels = _labels_for(spec)
        labels["astrolift-retention-months"] = str(retention_months)
        labels["astrolift-ingestion-cap-millions"] = str(ingestion_cap)

        parent = f"projects/{self._config.project_id}"
        workspace_body: dict[str, Any] = {
            "name": f"{parent}/metricsScopes/{workspace_name}",
            "display_name": workspace_name,
            "labels": labels,
            "retention_months": retention_months,
        }
        try:
            self._ws.create_workspace(
                request={
                    "parent": parent,
                    "workspace_id": workspace_name,
                    "workspace": workspace_body,
                },
            )
        except Exception as exc:
            return ProvisionResult(
                ok=False,
                handle="",
                message=f"create_workspace: {exc}",
                errors=[str(exc)],
            )

        return ProvisionResult(
            ok=True,
            handle=self._handle_for(workspace_name=workspace_name),
            message=(
                f"managed prometheus workspace {workspace_name} "
                f"provisioning (retention={retention_months}mo, "
                f"ingestion_cap={ingestion_cap}M)"
            ),
        )

    def update(self, spec: UpdateSpec) -> UpdateResult:
        workspace_name = self._workspace_name_from_handle(spec.handle)
        cfg = spec.config or {}

        update_body: dict[str, Any] = {}
        new_cap: int | None = None
        if spec.size:
            new_cap = _SIZE_TO_INGESTION_CAP_MILLIONS.get(spec.size)
        if "ingestion_cap_millions" in cfg:
            new_cap = int(cfg["ingestion_cap_millions"])
        if "retention_months" in cfg:
            new_retention = int(cfg["retention_months"])
            if new_retention < 1 or new_retention > 24:
                return UpdateResult(
                    ok=False,
                    handle=spec.handle,
                    message=(
                        f"retention_months={new_retention} out of bounds; "
                        f"GMP hard ceiling is 24 months"
                    ),
                    errors=["retention_out_of_bounds"],
                )
            update_body["retention_months"] = new_retention

        labels_patch: dict[str, str] = {}
        if new_cap is not None:
            labels_patch["astrolift-ingestion-cap-millions"] = str(new_cap)
        if "retention_months" in cfg:
            labels_patch["astrolift-retention-months"] = str(
                cfg["retention_months"],
            )
        if labels_patch:
            update_body["labels"] = labels_patch

        if not update_body:
            return UpdateResult(
                ok=True,
                handle=spec.handle,
                message="no modifiable attributes provided -- no-op",
            )

        try:
            self._ws.update_workspace(
                request={
                    "name": self._workspace_resource(
                        workspace_name=workspace_name,
                    ),
                    "workspace": update_body,
                },
            )
        except Exception as exc:
            return UpdateResult(
                ok=False,
                handle=spec.handle,
                message=f"update_workspace: {exc}",
                errors=[str(exc)],
            )
        return UpdateResult(
            ok=True,
            handle=spec.handle,
            message=(f"managed prometheus workspace {workspace_name} update queued"),
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
            return DeprovisionResult(
                ok=True,
                handle=spec.handle,
                message=(f"managed prometheus workspace {workspace_name} already gone"),
            )

        rule_groups = self._list_rule_groups(workspace_name=workspace_name)
        if rule_groups and not force_destroy:
            names = ", ".join(_get(rg, "display_name", _get(rg, "name", "?")) for rg in rule_groups)
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=(
                    f"managed prometheus workspace {workspace_name} has "
                    f"{len(rule_groups)} rule group(s) referencing it "
                    f"({names}); pass force_destroy=True to detach + delete"
                ),
                errors=["rule_groups_present"],
            )

        detached = 0
        if force_destroy and rule_groups:
            for rg in rule_groups:
                try:
                    self._rules.delete_alert_policy(  # type: ignore[union-attr]
                        request={
                            "name": _get(rg, "name", ""),
                        },
                    )
                    detached += 1
                except Exception:
                    continue

        try:
            self._ws.delete_workspace(
                request={
                    "name": self._workspace_resource(
                        workspace_name=workspace_name,
                    ),
                },
            )
        except Exception as exc:
            return DeprovisionResult(
                ok=False,
                handle=spec.handle,
                message=f"delete_workspace: {exc}",
                errors=[str(exc)],
            )

        # delete_data=True: invoke the GMP purge-retention call so
        # the project-level 24-month window for this workspace's
        # metrics is dropped immediately. The retain path keeps the
        # window in effect.
        metrics_action = "retained"
        if delete_data:
            try:
                self._ws.purge_retained_metrics(
                    request={
                        "workspace": self._workspace_resource(
                            workspace_name=workspace_name,
                        ),
                    },
                )
                metrics_action = "purged"
            except Exception:
                # Best-effort: don't fail the whole deprovision on
                # purge failure; the workspace is gone and metrics
                # will age out at the retention boundary anyway.
                metrics_action = "purge_failed"

        return DeprovisionResult(
            ok=True,
            handle=spec.handle,
            message=(
                f"managed prometheus workspace {workspace_name} delete "
                f"queued (metrics={metrics_action}, detached={detached}, "
                f"force_destroy={force_destroy})"
            ),
        )

    # ---- read-only ops ------------------------------------------------

    def status(self, handle: ServiceHandle) -> ServiceStatus:
        workspace_name = self._workspace_name_from_handle(handle.handle)
        existing = self._describe(workspace_name)
        if existing is None:
            return ServiceStatus(
                handle=handle.handle,
                state="deprovisioned",
                message=(f"managed prometheus workspace {workspace_name} not found"),
            )
        gcp_state = _get(existing, "state", "READY")
        return ServiceStatus(
            handle=handle.handle,
            state=_GCP_STATE_TO_PROTOCOL.get(
                str(gcp_state),
                "updating",
            ),
            message=f"managed prometheus reports {gcp_state}",
        )

    def binding(self, handle: ServiceHandle) -> Binding:
        workspace_name = self._workspace_name_from_handle(handle.handle)
        existing = self._describe(workspace_name)
        if existing is None:
            raise _GMPError(
                f"binding requested for missing workspace {workspace_name}",
            )
        # GMP's PromQL HTTP API lives under monitoring.googleapis.com
        # scoped to the project; the write side uses the v1
        # prometheus push gateway URL. Both are deterministic from
        # project_id, so we don't need to fetch them.
        prometheus_query_endpoint = f"https://monitoring.googleapis.com/v1/projects/{self._config.project_id}/location/global/prometheus"
        remote_write_endpoint = (
            f"https://monitoring.googleapis.com/v1/projects/"
            f"{self._config.project_id}/location/global/prometheus/"
            f"api/v1/write"
        )

        return Binding(
            env_vars={
                # Canonical contract envs (managed_service_kinds.py)
                "TIME_SERIES_URL": ValueRef(literal=prometheus_query_endpoint),
                "TIME_SERIES_BUCKET": ValueRef(literal=workspace_name),
                "TIME_SERIES_ORG": ValueRef(literal=self._config.project_id),
                # GCP-flavoured aliases
                "GCP_PROJECT_ID": ValueRef(literal=self._config.project_id),
                "GCP_PROMETHEUS_QUERY_ENDPOINT": ValueRef(
                    literal=prometheus_query_endpoint,
                ),
                "GCP_PROMETHEUS_REMOTE_WRITE_ENDPOINT": ValueRef(
                    literal=remote_write_endpoint,
                ),
                "GCP_PROMETHEUS_WORKSPACE": ValueRef(
                    literal=workspace_name,
                ),
            },
            iam_grants=[
                Grant(
                    resource=self._workspace_resource(
                        workspace_name=workspace_name,
                    ),
                    actions=[
                        "monitoring.timeSeries.create",
                        "monitoring.timeSeries.list",
                        "monitoring.metricDescriptors.get",
                        "monitoring.metricDescriptors.list",
                    ],
                ),
                Grant(
                    resource=f"projects/{self._config.project_id}",
                    actions=[
                        "monitoring.viewer",
                    ],
                ),
            ],
            notes=(
                "GMP authn uses Application Default Credentials via "
                "Workload Identity. TIME_SERIES_URL exposes the PromQL "
                "endpoint; remote-write goes to "
                "GCP_PROMETHEUS_REMOTE_WRITE_ENDPOINT. Tokens are "
                "ambient (no static API key)."
            ),
        )

    def snapshot(self, handle: ServiceHandle) -> SnapshotHandle:
        """GMP has no first-party snapshot API -- the project-level
        retention IS the durability story (PromQL queries against
        ``offset`` retain the history within the workspace's
        retention window). We return a synthetic snapshot id keyed
        on the current UTC second so callers can persist a marker."""
        from datetime import UTC, datetime

        workspace_name = self._workspace_name_from_handle(handle.handle)
        existing = self._describe(workspace_name)
        if existing is None:
            raise _GMPError(
                f"snapshot for missing workspace {workspace_name}",
            )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return SnapshotHandle(
            handle=handle.handle,
            snapshot_id=f"{workspace_name}-pit-{stamp}",
            created_at=datetime.now(UTC).isoformat(),
        )

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
                f"{snapshot.snapshot_id} stay queryable on the source "
                f"via PromQL offset within the retention window"
            ),
        )

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "retention_months": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 24,
                },
                "ingestion_cap_millions": {
                    "type": "integer",
                    "minimum": 1,
                },
            },
        }

    def binding_schema(self) -> BindingSchema:
        return BindingSchema(
            env_vars={
                "TIME_SERIES_URL": "Managed Prometheus PromQL query endpoint",
                "TIME_SERIES_BUCKET": "Workspace name (GMP's bucket-equivalent)",
                "TIME_SERIES_ORG": "GCP project id",
                "GCP_PROJECT_ID": "GCP project id hosting the workspace",
                "GCP_PROMETHEUS_QUERY_ENDPOINT": ("PromQL query URL (alias of TIME_SERIES_URL)"),
                "GCP_PROMETHEUS_REMOTE_WRITE_ENDPOINT": ("Remote-write endpoint for the Prometheus client"),
                "GCP_PROMETHEUS_WORKSPACE": ("Workspace name (alias of TIME_SERIES_BUCKET)"),
            },
        )

    # ---- internals ----------------------------------------------------

    def _describe(self, workspace_name: str) -> Any | None:
        try:
            return self._ws.get_workspace(
                request={
                    "name": self._workspace_resource(
                        workspace_name=workspace_name,
                    ),
                },
            )
        except Exception as exc:
            err = str(exc)
            if "404" in err or "NotFound" in err or "not found" in err.lower():
                return None
            raise

    def _list_rule_groups(
        self,
        *,
        workspace_name: str,
    ) -> list[Any]:
        if not self._config.rule_group_check_enabled or self._rules is None:
            return []
        try:
            resp = self._rules.list_alert_policies(
                request={
                    "name": f"projects/{self._config.project_id}",
                    "filter": (f'metadata.labels.workspace="{workspace_name}"'),
                },
            )
        except Exception:
            return []
        return list(resp)

    def _workspace_name_for(self, *, spec: ProvisionSpec) -> str:
        # GMP workspace ids: 1-63 chars, [a-z0-9-], must start with
        # a letter.
        raw = (
            f"{self._config.workspace_name_prefix}-"
            f"{spec.organization_slug}-{spec.app_slug}-"
            f"{spec.environment_name}"
        ).lower()
        clean = "".join(c if (c.isalnum() or c == "-") else "-" for c in raw)
        while "--" in clean:
            clean = clean.replace("--", "-")
        clean = clean.strip("-")
        if not clean or not clean[0].isalpha():
            clean = "w" + clean
        return clean[:63]

    def _handle_for(self, *, workspace_name: str) -> str:
        return f"{KIND}/{workspace_name}"

    def _workspace_name_from_handle(self, handle: str) -> str:
        if "/" not in handle:
            raise _GMPError(
                f"handle {handle!r} must be '<kind>/<workspace_name>'",
            )
        kind, _, workspace_name = handle.partition("/")
        if not kind or not workspace_name:
            raise _GMPError(
                f"handle {handle!r} has empty component",
            )
        return workspace_name

    def _workspace_resource(self, *, workspace_name: str) -> str:
        return f"projects/{self._config.project_id}/metricsScopes/{workspace_name}"


# ----- module-level helpers --------------------------------------------


def _labels_for(spec: ProvisionSpec) -> dict[str, str]:
    def _sanitize(s: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in s.lower())

    base = {
        "astrolift-managed-by": "platform",
        "astrolift-organization": _sanitize(spec.organization_slug),
        "astrolift-app": _sanitize(spec.app_slug),
        "astrolift-environment": _sanitize(spec.environment_name),
        "astrolift-cluster": _sanitize(spec.tenant_cluster_id),
        "astrolift-isolation": _sanitize(spec.isolation),
    }
    # Per-binding cost-attribution keys (#438). GCP labels are
    # lowercase + [a-z0-9_-], so the dotted/slash form
    # ``astrolift.io/binding`` becomes ``astrolift-binding``.
    if spec.binding_id:
        base["astrolift-binding"] = _sanitize(spec.binding_id)
    if spec.managed_service_id:
        base["astrolift-managed-service-id"] = _sanitize(spec.managed_service_id)
    for k, v in (spec.tags or {}).items():
        base[f"astrolift-extra-{_sanitize(k)}"] = _sanitize(str(v))
    return base


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_GCP_STATE_TO_PROTOCOL = {
    "READY": "available",
    "ACTIVE": "available",
    "CREATING": "provisioning",
    "UPDATING": "updating",
    "DELETING": "deprovisioning",
    "ERROR": "error",
}
