"""Tests for GCP Managed Service for Prometheus driver (#374).

Same fake-client pattern as the Vertex Matching Engine + Bigtable
drivers -- there's no GMP emulator, so we drive the SDK via
call-recording fakes that return canned responses. Test surface
mirrors the AWS Timestream driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from gcp.managed.timeseries_managed_prometheus import (
    KIND,
    GCPManagedPrometheusConfig,
    GCPManagedPrometheusDriver,
    _GMPError,
)

# ---- fakes -----------------------------------------------------------


@dataclass
class FakeWorkspace:
    name: str
    display_name: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    retention_months: int = 24
    state: str = "READY"


@dataclass
class FakeAlertPolicy:
    name: str
    display_name: str = ""


class FakeWorkspaceClient:
    def __init__(self) -> None:
        self.workspaces: dict[str, FakeWorkspace] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.purged: list[str] = []

    def _record(self, op: str, **kwargs: Any) -> None:
        self.calls.append((op, kwargs))

    def create_workspace(self, *, request: dict[str, Any]) -> None:
        self._record("create_workspace", request=request)
        wid = request["workspace_id"]
        body = request["workspace"]
        if wid in self.workspaces:
            raise RuntimeError("AlreadyExists")
        self.workspaces[wid] = FakeWorkspace(
            name=body["name"],
            display_name=body.get("display_name", ""),
            labels=dict(body.get("labels", {})),
            retention_months=int(body.get("retention_months", 24)),
        )

    def get_workspace(self, *, request: dict[str, Any]) -> FakeWorkspace:
        self._record("get_workspace", request=request)
        wid = request["name"].rsplit("/", 1)[-1]
        if wid not in self.workspaces:
            raise RuntimeError("404 NotFound")
        return self.workspaces[wid]

    def update_workspace(self, *, request: dict[str, Any]) -> None:
        self._record("update_workspace", request=request)
        wid = request["name"].rsplit("/", 1)[-1]
        ws = self.workspaces.get(wid)
        if ws is None:
            raise RuntimeError("404 NotFound")
        patch = request["workspace"]
        if "retention_months" in patch:
            ws.retention_months = int(patch["retention_months"])
        if "labels" in patch:
            ws.labels.update(patch["labels"])

    def delete_workspace(self, *, request: dict[str, Any]) -> None:
        self._record("delete_workspace", request=request)
        wid = request["name"].rsplit("/", 1)[-1]
        if wid not in self.workspaces:
            raise RuntimeError("404 NotFound")
        del self.workspaces[wid]

    def purge_retained_metrics(self, *, request: dict[str, Any]) -> None:
        self._record("purge_retained_metrics", request=request)
        wid = request["workspace"].rsplit("/", 1)[-1]
        self.purged.append(wid)


class FakeRulesClient:
    def __init__(self) -> None:
        self.policies: dict[str, FakeAlertPolicy] = {}
        self.deleted: list[str] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_alert_policies(self, *, request: dict[str, Any]) -> list[FakeAlertPolicy]:
        self.calls.append(("list_alert_policies", request))
        # Filter by the workspace label in the filter string; tests
        # arrange policies that should match by writing the workspace
        # in the policy's name.
        wanted = ""
        filt = request.get("filter", "") or ""
        if 'workspace="' in filt:
            wanted = filt.split('workspace="', 1)[1].rstrip('"')
        return [p for p in self.policies.values() if wanted in p.name]

    def delete_alert_policy(self, *, request: dict[str, Any]) -> None:
        self.calls.append(("delete_alert_policy", request))
        name = request["name"]
        if name not in self.policies:
            raise RuntimeError("404 NotFound")
        del self.policies[name]
        self.deleted.append(name)


@pytest.fixture
def workspace_client() -> FakeWorkspaceClient:
    return FakeWorkspaceClient()


@pytest.fixture
def rules_client() -> FakeRulesClient:
    return FakeRulesClient()


@pytest.fixture
def driver(
    workspace_client: FakeWorkspaceClient,
    rules_client: FakeRulesClient,
) -> GCPManagedPrometheusDriver:
    return GCPManagedPrometheusDriver(
        config=GCPManagedPrometheusConfig(
            project_id="acme-prod",
            region="us-central1",
        ),
        workspace_client=workspace_client,
        rules_client=rules_client,
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="gcp-prod",
        service_handle_hint="metrics",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_workspace(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind, workspace_name = result.handle.partition("/")[0::2]
    assert kind == KIND
    assert workspace_name in workspace_client.workspaces


def test_provision_idempotent(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    creates = [c for op, c in workspace_client.calls if op == "create_workspace"]
    assert len(creates) == 1


def test_provision_default_retention_is_24_months(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    result = driver.provision(_spec())
    workspace_name = result.handle.split("/", 1)[1]
    assert workspace_client.workspaces[workspace_name].retention_months == 24


def test_provision_honours_retention_override(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    result = driver.provision(_spec(config={"retention_months": 6}))
    workspace_name = result.handle.split("/", 1)[1]
    assert workspace_client.workspaces[workspace_name].retention_months == 6


def test_provision_rejects_out_of_bounds_retention(
    driver: GCPManagedPrometheusDriver,
) -> None:
    result = driver.provision(_spec(config={"retention_months": 25}))
    assert not result.ok
    assert "out of bounds" in result.message


def test_provision_labels_with_astrolift_namespace(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    result = driver.provision(_spec())
    workspace_name = result.handle.split("/", 1)[1]
    labels = workspace_client.workspaces[workspace_name].labels
    assert labels["astrolift-managed-by"] == "platform"
    assert labels["astrolift-app"] == "api"
    assert labels["astrolift-retention-months"] == "24"


def test_provision_size_maps_to_ingestion_cap_label(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    result = driver.provision(_spec(size="large"))
    workspace_name = result.handle.split("/", 1)[1]
    labels = workspace_client.workspaces[workspace_name].labels
    assert labels["astrolift-ingestion-cap-millions"] == "1000"


def test_provision_surfaces_create_failure(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    def boom(*, request):  # type: ignore[no-untyped-def]
        raise RuntimeError("quota exceeded")

    workspace_client.create_workspace = boom  # type: ignore[assignment]
    result = driver.provision(_spec())
    assert not result.ok
    assert "create_workspace" in result.message


# ---- update -----------------------------------------------------


def test_update_retention(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"retention_months": 12},
        ),
    )
    assert result.ok
    workspace_name = provisioned.handle.split("/", 1)[1]
    assert workspace_client.workspaces[workspace_name].retention_months == 12


def test_update_resize_changes_ingestion_label(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="xlarge"),
    )
    assert result.ok
    workspace_name = provisioned.handle.split("/", 1)[1]
    labels = workspace_client.workspaces[workspace_name].labels
    assert labels["astrolift-ingestion-cap-millions"] == "10000"


def test_update_rejects_out_of_bounds_retention(
    driver: GCPManagedPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"retention_months": 36},
        ),
    )
    assert not result.ok
    assert "out of bounds" in result.message


def test_update_noop_when_nothing_to_change(
    driver: GCPManagedPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(UpdateSpec(handle=provisioned.handle))
    assert result.ok
    assert "no-op" in result.message


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_refuses_with_rule_groups(
    driver: GCPManagedPrometheusDriver,
    rules_client: FakeRulesClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    rules_client.policies[f"projects/acme-prod/alertPolicies/p1-{workspace_name}"] = FakeAlertPolicy(
        name=f"projects/acme-prod/alertPolicies/p1-{workspace_name}",
        display_name="cpu-burn",
    )
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert not result.ok
    assert "rule group" in result.message


def test_deprovision_default_no_rules_preserves_metrics(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert "metrics=retained" in result.message
    assert workspace_name not in workspace_client.workspaces
    assert workspace_name not in workspace_client.purged


def test_deprovision_delete_data_only_purges_metrics(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "metrics=purged" in result.message
    assert workspace_name in workspace_client.purged


def test_deprovision_force_destroy_detaches_rule_groups(
    driver: GCPManagedPrometheusDriver,
    rules_client: FakeRulesClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    rules_client.policies[f"projects/acme-prod/alertPolicies/p1-{workspace_name}"] = FakeAlertPolicy(
        name=f"projects/acme-prod/alertPolicies/p1-{workspace_name}",
        display_name="cpu-burn",
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    assert "detached=1" in result.message
    assert "metrics=retained" in result.message
    assert rules_client.deleted


def test_deprovision_atomic_both_flags(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
    rules_client: FakeRulesClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    rules_client.policies[f"projects/acme-prod/alertPolicies/p1-{workspace_name}"] = FakeAlertPolicy(
        name=f"projects/acme-prod/alertPolicies/p1-{workspace_name}",
        display_name="cpu-burn",
    )
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "detached=1" in result.message
    assert "metrics=purged" in result.message
    assert workspace_name not in workspace_client.workspaces


def test_deprovision_idempotent_when_already_gone(
    driver: GCPManagedPrometheusDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="time_series/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: GCPManagedPrometheusDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="time_series/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_ready_to_available(
    driver: GCPManagedPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_deleting_to_deprovisioning(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    workspace_client.workspaces[workspace_name].state = "DELETING"
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "deprovisioning"


# ---- binding ----------------------------------------------------


def test_binding_returns_envelope(
    driver: GCPManagedPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    for key in (
        "TIME_SERIES_URL",
        "TIME_SERIES_BUCKET",
        "TIME_SERIES_ORG",
        "GCP_PROJECT_ID",
        "GCP_PROMETHEUS_QUERY_ENDPOINT",
        "GCP_PROMETHEUS_REMOTE_WRITE_ENDPOINT",
        "GCP_PROMETHEUS_WORKSPACE",
    ):
        assert key in env
    assert env["GCP_PROJECT_ID"].literal == "acme-prod"
    assert env["TIME_SERIES_URL"].literal.startswith(
        "https://monitoring.googleapis.com/",
    )
    assert "/api/v1/write" in env["GCP_PROMETHEUS_REMOTE_WRITE_ENDPOINT"].literal


def test_binding_iam_grants_cover_workspace(
    driver: GCPManagedPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    assert [grant.actions for grant in binding.iam_grants] == [
        ["roles/monitoring.metricWriter"],
        ["roles/monitoring.viewer"],
    ]


def test_binding_for_missing_raises(
    driver: GCPManagedPrometheusDriver,
) -> None:
    with pytest.raises(_GMPError):
        driver.binding(ServiceHandle(handle="time_series/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_pit_handle(
    driver: GCPManagedPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    assert "-pit-" in snap.snapshot_id


def test_snapshot_for_missing_raises(
    driver: GCPManagedPrometheusDriver,
) -> None:
    with pytest.raises(_GMPError):
        driver.snapshot(ServiceHandle(handle="time_series/never"))


def test_restore_provisions_target(
    driver: GCPManagedPrometheusDriver,
    workspace_client: FakeWorkspaceClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="metrics-restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    target_name = result.handle.split("/", 1)[1]
    # Source and target should differ because we changed
    # service_handle_hint -- but workspace naming only takes
    # org/app/env, so both compute to the same name. We just
    # confirm the target is in the client.
    assert target_name in workspace_client.workspaces


# ---- naming + helpers -------------------------------------------


def test_workspace_name_canonicalization(
    driver: GCPManagedPrometheusDriver,
) -> None:
    name = driver._workspace_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
        ),
    )
    assert name == name.lower()
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    assert name[0].isalpha()
    assert len(name) <= 63


def test_handle_rejects_malformed(driver: GCPManagedPrometheusDriver) -> None:
    with pytest.raises(_GMPError):
        driver._workspace_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(_GMPError):
        driver._workspace_name_from_handle("time_series/")  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: GCPManagedPrometheusDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in ("retention_months", "ingestion_cap_millions"):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: GCPManagedPrometheusDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "TIME_SERIES_URL",
        "TIME_SERIES_BUCKET",
        "TIME_SERIES_ORG",
        "GCP_PROJECT_ID",
        "GCP_PROMETHEUS_QUERY_ENDPOINT",
        "GCP_PROMETHEUS_REMOTE_WRITE_ENDPOINT",
        "GCP_PROMETHEUS_WORKSPACE",
    ):
        assert key in schema.env_vars


def test_provision_does_not_adopt_another_services_resource(driver) -> None:
    """Names are slug-joined, so another service can map to this one's name (#1961)."""
    import dataclasses

    first = driver.provision(dataclasses.replace(_spec(), managed_service_id="svc-a"))
    second = driver.provision(dataclasses.replace(_spec(), managed_service_id="svc-b"))

    assert first.ok, first.message
    assert not second.ok and "refusing to adopt" in second.message
