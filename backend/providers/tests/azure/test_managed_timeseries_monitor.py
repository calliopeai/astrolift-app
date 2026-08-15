"""Tests for AzureMonitorPrometheusDriver (#374 -- time_series/azure_monitor_prometheus).

Same fake-client pattern as the cosmos / vector_search drivers --
no Azure emulator is broadly usable for Monitor + DCE/DCR, so we
drive the SDK via call-recording fakes that return canned responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk.managed_service import (
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    SnapshotHandle,
    UpdateSpec,
)
from azure.managed.timeseries_monitor import (
    KIND,
    AzureMonitorPrometheusConfig,
    AzureMonitorPrometheusDriver,
    AzureMonitorPrometheusError,
)

# ---- fakes ------------------------------------------------------


class _NotFound(Exception):
    pass


_NotFound.__name__ = "ResourceNotFoundError"


@dataclass
class FakePoller:
    value: Any = None

    def result(self) -> Any:
        return self.value


@dataclass
class FakeMonitorWorkspace:
    name: str
    location: str = "eastus"
    tags: dict[str, str] = field(default_factory=dict)
    provisioning_state: str = "Succeeded"
    public_network_access: str = "Enabled"
    metrics: dict[str, str] = field(
        default_factory=lambda: {
            "prometheus_query_endpoint": "https://test-ws.eastus.prometheus.monitor.azure.com",
        },
    )


@dataclass
class FakeDCE:
    name: str
    location: str = "eastus"
    tags: dict[str, str] = field(default_factory=dict)
    logs_ingestion: dict[str, str] = field(
        default_factory=lambda: {
            "endpoint": "https://test-dce.eastus.handler.control.monitor.azure.com",
        },
    )


@dataclass
class FakeDCR:
    name: str
    location: str = "eastus"
    tags: dict[str, str] = field(default_factory=dict)
    immutable_id: str = "dcr-immutable-aaaa1111bbbb2222"


@dataclass
class FakeLAWorkspace:
    name: str
    location: str = "eastus"
    retention_in_days: int = 30
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeMonitorWorkspacesOps:
    workspaces: dict[str, FakeMonitorWorkspace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def get(
        self,
        *,
        resource_group_name: str,
        azure_monitor_workspace_name: str,
    ) -> FakeMonitorWorkspace:
        if azure_monitor_workspace_name not in self.workspaces:
            raise _NotFound(azure_monitor_workspace_name)
        return self.workspaces[azure_monitor_workspace_name]

    def begin_create(
        self,
        *,
        resource_group_name: str,
        azure_monitor_workspace_name: str,
        azure_monitor_workspace_properties: dict[str, Any],
    ) -> FakePoller:
        self.create_calls.append(
            {
                "name": azure_monitor_workspace_name,
                "params": azure_monitor_workspace_properties,
            },
        )
        ws = FakeMonitorWorkspace(
            name=azure_monitor_workspace_name,
            location=azure_monitor_workspace_properties.get("location", "eastus"),
            tags=dict(azure_monitor_workspace_properties.get("tags", {})),
            public_network_access=(
                azure_monitor_workspace_properties.get("properties", {}).get(
                    "public_network_access",
                    "Enabled",
                )
            ),
        )
        self.workspaces[azure_monitor_workspace_name] = ws
        return FakePoller(value=ws)

    def update(
        self,
        *,
        resource_group_name: str,
        azure_monitor_workspace_name: str,
        azure_monitor_workspace_properties: dict[str, Any],
    ) -> FakeMonitorWorkspace:
        self.update_calls.append(
            {
                "name": azure_monitor_workspace_name,
                "params": azure_monitor_workspace_properties,
            },
        )
        ws = self.workspaces.get(azure_monitor_workspace_name)
        if ws is None:
            raise _NotFound(azure_monitor_workspace_name)
        if "tags" in azure_monitor_workspace_properties:
            ws.tags.update(azure_monitor_workspace_properties["tags"])
        props = azure_monitor_workspace_properties.get("properties") or {}
        if "public_network_access" in props:
            ws.public_network_access = props["public_network_access"]
        return ws

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        azure_monitor_workspace_name: str,
    ) -> FakePoller:
        self.delete_calls.append(azure_monitor_workspace_name)
        self.workspaces.pop(azure_monitor_workspace_name, None)
        return FakePoller(value=None)


@dataclass
class FakeDCEOps:
    dces: dict[str, FakeDCE] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def create(
        self,
        *,
        resource_group_name: str,
        data_collection_endpoint_name: str,
        body: dict[str, Any],
    ) -> FakeDCE:
        self.create_calls.append(
            {"name": data_collection_endpoint_name, "body": body},
        )
        dce = FakeDCE(
            name=data_collection_endpoint_name,
            location=body.get("location", "eastus"),
            tags=dict(body.get("tags", {})),
        )
        self.dces[data_collection_endpoint_name] = dce
        return dce

    def get(
        self,
        *,
        resource_group_name: str,
        data_collection_endpoint_name: str,
    ) -> FakeDCE:
        if data_collection_endpoint_name not in self.dces:
            raise _NotFound(data_collection_endpoint_name)
        return self.dces[data_collection_endpoint_name]

    def delete(
        self,
        *,
        resource_group_name: str,
        data_collection_endpoint_name: str,
    ) -> None:
        self.delete_calls.append(data_collection_endpoint_name)
        self.dces.pop(data_collection_endpoint_name, None)


@dataclass
class FakeDCROps:
    dcrs: dict[str, FakeDCR] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def create(
        self,
        *,
        resource_group_name: str,
        data_collection_rule_name: str,
        body: dict[str, Any],
    ) -> FakeDCR:
        self.create_calls.append(
            {"name": data_collection_rule_name, "body": body},
        )
        dcr = FakeDCR(
            name=data_collection_rule_name,
            location=body.get("location", "eastus"),
            tags=dict(body.get("tags", {})),
        )
        self.dcrs[data_collection_rule_name] = dcr
        return dcr

    def get(
        self,
        *,
        resource_group_name: str,
        data_collection_rule_name: str,
    ) -> FakeDCR:
        if data_collection_rule_name not in self.dcrs:
            raise _NotFound(data_collection_rule_name)
        return self.dcrs[data_collection_rule_name]

    def delete(
        self,
        *,
        resource_group_name: str,
        data_collection_rule_name: str,
    ) -> None:
        self.delete_calls.append(data_collection_rule_name)
        self.dcrs.pop(data_collection_rule_name, None)


@dataclass
class FakeMonitorClient:
    azure_monitor_workspaces_obj: FakeMonitorWorkspacesOps = field(
        default_factory=FakeMonitorWorkspacesOps,
    )
    data_collection_endpoints_obj: FakeDCEOps = field(default_factory=FakeDCEOps)
    data_collection_rules_obj: FakeDCROps = field(default_factory=FakeDCROps)

    @property
    def azure_monitor_workspaces(self) -> FakeMonitorWorkspacesOps:
        return self.azure_monitor_workspaces_obj

    @property
    def data_collection_endpoints(self) -> FakeDCEOps:
        return self.data_collection_endpoints_obj

    @property
    def data_collection_rules(self) -> FakeDCROps:
        return self.data_collection_rules_obj


@dataclass
class FakeLAWorkspacesOps:
    workspaces: dict[str, FakeLAWorkspace] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    update_calls: list[dict[str, Any]] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)

    def begin_create_or_update(
        self,
        *,
        resource_group_name: str,
        workspace_name: str,
        parameters: dict[str, Any],
    ) -> FakePoller:
        self.create_calls.append(
            {"name": workspace_name, "params": parameters},
        )
        props = parameters.get("properties") or {}
        ws = FakeLAWorkspace(
            name=workspace_name,
            location=parameters.get("location", "eastus"),
            retention_in_days=int(props.get("retention_in_days", 30)),
            tags=dict(parameters.get("tags", {})),
        )
        self.workspaces[workspace_name] = ws
        return FakePoller(value=ws)

    def update(
        self,
        *,
        resource_group_name: str,
        workspace_name: str,
        parameters: dict[str, Any],
    ) -> FakeLAWorkspace:
        self.update_calls.append(
            {"name": workspace_name, "params": parameters},
        )
        ws = self.workspaces.get(workspace_name)
        if ws is None:
            raise _NotFound(workspace_name)
        props = parameters.get("properties") or {}
        if "retention_in_days" in props:
            ws.retention_in_days = int(props["retention_in_days"])
        return ws

    def begin_delete(
        self,
        *,
        resource_group_name: str,
        workspace_name: str,
    ) -> FakePoller:
        self.delete_calls.append(workspace_name)
        self.workspaces.pop(workspace_name, None)
        return FakePoller(value=None)


@dataclass
class FakeLogAnalyticsClient:
    workspaces_obj: FakeLAWorkspacesOps = field(default_factory=FakeLAWorkspacesOps)

    @property
    def workspaces(self) -> FakeLAWorkspacesOps:
        return self.workspaces_obj


@dataclass
class FakeManagementLocksOps:
    locks: list[Any] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def list_at_resource_level(self, **_kwargs: Any) -> list[Any]:
        return list(self.locks)

    def delete_at_resource_level(self, *, lock_name: str, **_kwargs: Any) -> None:
        self.locks = [lk for lk in self.locks if getattr(lk, "name", "") != lock_name]
        self.deleted.append(lock_name)


@dataclass
class FakeLocksClient:
    management_locks_obj: FakeManagementLocksOps = field(default_factory=FakeManagementLocksOps)

    @property
    def management_locks(self) -> FakeManagementLocksOps:
        return self.management_locks_obj


@dataclass
class FakeLock:
    name: str
    level: str = "CanNotDelete"


# ---- fixtures ---------------------------------------------------


@pytest.fixture
def monitor_client() -> FakeMonitorClient:
    return FakeMonitorClient()


@pytest.fixture
def la_client() -> FakeLogAnalyticsClient:
    return FakeLogAnalyticsClient()


@pytest.fixture
def locks_client() -> FakeLocksClient:
    return FakeLocksClient()


@pytest.fixture
def driver(
    monitor_client: FakeMonitorClient,
    la_client: FakeLogAnalyticsClient,
    locks_client: FakeLocksClient,
) -> AzureMonitorPrometheusDriver:
    return AzureMonitorPrometheusDriver(
        config=AzureMonitorPrometheusConfig(
            subscription_id="sub-1",
            resource_group="rg-test",
            location="eastus",
            monitor_client=monitor_client,
            log_analytics_client=la_client,
            locks_client=locks_client,
        ),
    )


def _spec(**overrides: Any) -> ProvisionSpec:
    base = dict(
        organization_id="1",
        organization_slug="acme",
        app_id="1",
        app_slug="api",
        environment_id="1",
        environment_name="prod",
        tenant_cluster_id="azure-prod",
        service_handle_hint="metrics",
        size="small",
    )
    base.update(overrides)
    return ProvisionSpec(**base)


# ---- provision --------------------------------------------------


def test_provision_creates_workspace_dce_dcr_and_la(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
    la_client: FakeLogAnalyticsClient,
) -> None:
    result = driver.provision(_spec())
    assert result.ok
    kind, workspace_name = result.handle.partition("/")[0::2]
    assert kind == KIND
    assert workspace_name in monitor_client.azure_monitor_workspaces_obj.workspaces
    # DCE + DCR created
    dce_name = f"{workspace_name}-dce"
    dcr_name = f"{workspace_name}-dcr"
    assert dce_name in monitor_client.data_collection_endpoints_obj.dces
    assert dcr_name in monitor_client.data_collection_rules_obj.dcrs
    # Linked LA workspace created
    la_name = f"{workspace_name}-la"
    assert la_name in la_client.workspaces_obj.workspaces


def test_provision_idempotent(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    a = driver.provision(_spec())
    b = driver.provision(_spec())
    assert a.handle == b.handle
    assert a.ok and b.ok
    assert "already exists" in b.message
    assert len(monitor_client.azure_monitor_workspaces_obj.create_calls) == 1


def test_provision_size_maps_to_retention(
    driver: AzureMonitorPrometheusDriver,
    la_client: FakeLogAnalyticsClient,
) -> None:
    result = driver.provision(_spec(size="large"))
    workspace_name = result.handle.split("/", 1)[1]
    la_name = f"{workspace_name}-la"
    assert la_client.workspaces_obj.workspaces[la_name].retention_in_days == 365


def test_provision_honours_retention_override(
    driver: AzureMonitorPrometheusDriver,
    la_client: FakeLogAnalyticsClient,
) -> None:
    result = driver.provision(_spec(config={"retention_days": 7}))
    workspace_name = result.handle.split("/", 1)[1]
    la_name = f"{workspace_name}-la"
    assert la_client.workspaces_obj.workspaces[la_name].retention_in_days == 7


def test_provision_can_disable_linked_la(
    driver: AzureMonitorPrometheusDriver,
    la_client: FakeLogAnalyticsClient,
) -> None:
    driver.provision(_spec(config={"create_linked_log_analytics": False}))
    assert not la_client.workspaces_obj.workspaces


def test_provision_tags_with_astrolift_namespace(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    result = driver.provision(_spec())
    workspace_name = result.handle.split("/", 1)[1]
    tags = monitor_client.azure_monitor_workspaces_obj.workspaces[workspace_name].tags
    assert tags["astrolift-managed-by"] == "platform"
    assert tags["astrolift-app"] == "api"


def test_provision_honours_public_network_access_override(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    result = driver.provision(
        _spec(config={"public_network_access": "Disabled"}),
    )
    workspace_name = result.handle.split("/", 1)[1]
    assert monitor_client.azure_monitor_workspaces_obj.workspaces[workspace_name].public_network_access == "Disabled"


def test_provision_surfaces_create_failure(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    def boom(**_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("quota exceeded")

    monitor_client.azure_monitor_workspaces_obj.begin_create = boom  # type: ignore[assignment]
    result = driver.provision(_spec())
    assert not result.ok
    assert "begin_create" in result.message


# ---- update -----------------------------------------------------


def test_update_public_network_access(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"public_network_access": "Disabled"},
        ),
    )
    assert result.ok
    workspace_name = provisioned.handle.split("/", 1)[1]
    assert monitor_client.azure_monitor_workspaces_obj.workspaces[workspace_name].public_network_access == "Disabled"


def test_update_retention_days_patches_la(
    driver: AzureMonitorPrometheusDriver,
    la_client: FakeLogAnalyticsClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(
            handle=provisioned.handle,
            config={"retention_days": 90},
        ),
    )
    assert result.ok
    workspace_name = provisioned.handle.split("/", 1)[1]
    la_name = f"{workspace_name}-la"
    assert la_client.workspaces_obj.workspaces[la_name].retention_in_days == 90


def test_update_resize_changes_tags(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    provisioned = driver.provision(_spec())
    result = driver.update(
        UpdateSpec(handle=provisioned.handle, size="xlarge"),
    )
    assert result.ok
    workspace_name = provisioned.handle.split("/", 1)[1]
    tags = monitor_client.azure_monitor_workspaces_obj.workspaces[workspace_name].tags
    assert tags["astrolift-ingestion-cap-millions"] == "10000"


# ---- deprovision four-corner matrix -----------------------------


def test_deprovision_default_keeps_linked_la(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
    la_client: FakeLogAnalyticsClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    la_name = f"{workspace_name}-la"
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert "linked_log_analytics=kept" in result.message
    assert workspace_name not in monitor_client.azure_monitor_workspaces_obj.workspaces
    assert la_name in la_client.workspaces_obj.workspaces


def test_deprovision_delete_data_only_drops_la(
    driver: AzureMonitorPrometheusDriver,
    la_client: FakeLogAnalyticsClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    la_name = f"{workspace_name}-la"
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
    )
    assert result.ok
    assert "linked_log_analytics=deleted" in result.message
    assert la_name not in la_client.workspaces_obj.workspaces


def test_deprovision_default_refuses_with_resource_lock(
    driver: AzureMonitorPrometheusDriver,
    locks_client: FakeLocksClient,
) -> None:
    provisioned = driver.provision(_spec())
    locks_client.management_locks_obj.locks.append(FakeLock(name="finance-lock"))
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert not result.ok
    assert "resource locks" in result.message


def test_deprovision_force_destroy_clears_locks(
    driver: AzureMonitorPrometheusDriver,
    locks_client: FakeLocksClient,
) -> None:
    provisioned = driver.provision(_spec())
    locks_client.management_locks_obj.locks.append(FakeLock(name="finance-lock"))
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        force_destroy=True,
    )
    assert result.ok
    assert "linked_log_analytics=kept" in result.message
    assert "finance-lock" in locks_client.management_locks_obj.deleted


def test_deprovision_atomic_both_flags(
    driver: AzureMonitorPrometheusDriver,
    locks_client: FakeLocksClient,
    la_client: FakeLogAnalyticsClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    la_name = f"{workspace_name}-la"
    locks_client.management_locks_obj.locks.append(FakeLock(name="finance-lock"))
    result = driver.deprovision(
        DeprovisionSpec(handle=provisioned.handle),
        delete_data=True,
        force_destroy=True,
    )
    assert result.ok
    assert "linked_log_analytics=deleted" in result.message
    assert "finance-lock" in locks_client.management_locks_obj.deleted
    assert la_name not in la_client.workspaces_obj.workspaces


def test_deprovision_idempotent_when_already_gone(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    result = driver.deprovision(
        DeprovisionSpec(handle="time_series/never-existed"),
    )
    assert result.ok
    assert "already gone" in result.message


def test_deprovision_tears_down_dcr_and_dce(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    dce_name = f"{workspace_name}-dce"
    dcr_name = f"{workspace_name}-dcr"
    result = driver.deprovision(DeprovisionSpec(handle=provisioned.handle))
    assert result.ok
    assert dce_name not in monitor_client.data_collection_endpoints_obj.dces
    assert dcr_name not in monitor_client.data_collection_rules_obj.dcrs


# ---- status -----------------------------------------------------


def test_status_for_missing_returns_deprovisioned(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    state = driver.status(ServiceHandle(handle="time_series/missing"))
    assert state.state == "deprovisioned"


def test_status_maps_succeeded_to_available(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "available"


def test_status_maps_deleting_to_deprovisioning(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    provisioned = driver.provision(_spec())
    workspace_name = provisioned.handle.split("/", 1)[1]
    monitor_client.azure_monitor_workspaces_obj.workspaces[workspace_name].provisioning_state = "Deleting"
    state = driver.status(ServiceHandle(handle=provisioned.handle))
    assert state.state == "deprovisioning"


# ---- binding ----------------------------------------------------


def test_binding_returns_envelope(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    env = binding.env_vars
    for key in (
        "TIME_SERIES_URL",
        "TIME_SERIES_BUCKET",
        "TIME_SERIES_ORG",
        "AZURE_MONITOR_QUERY_ENDPOINT",
        "AZURE_MONITOR_INGESTION_ENDPOINT",
        "AZURE_MONITOR_DCR_IMMUTABLE_ID",
        "AZURE_MONITOR_WORKSPACE_NAME",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_RESOURCE_GROUP",
    ):
        assert key in env
    assert env["TIME_SERIES_URL"].literal is not None
    assert env["AZURE_MONITOR_INGESTION_ENDPOINT"].literal.startswith("https://")
    # DCR immutable id is read from the DCR fake
    assert env["AZURE_MONITOR_DCR_IMMUTABLE_ID"].literal == "dcr-immutable-aaaa1111bbbb2222"


def test_binding_iam_grants_cover_workspace_and_dcr(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    binding = driver.binding(ServiceHandle(handle=provisioned.handle))
    actions = {a for g in binding.iam_grants for a in g.actions}
    assert any("Microsoft.Monitor" in a for a in actions)
    assert any("dataCollectionRules" in a for a in actions)


def test_binding_for_missing_raises(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    with pytest.raises(AzureMonitorPrometheusError):
        driver.binding(ServiceHandle(handle="time_series/missing"))


# ---- snapshot + restore -----------------------------------------


def test_snapshot_returns_pit_handle(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    assert "-pit-" in snap.snapshot_id


def test_snapshot_for_missing_raises(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    with pytest.raises(AzureMonitorPrometheusError):
        driver.snapshot(ServiceHandle(handle="time_series/never"))


def test_restore_provisions_target(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    provisioned = driver.provision(_spec())
    snap = driver.snapshot(ServiceHandle(handle=provisioned.handle))
    restore_spec = _spec(service_handle_hint="metrics-restored")
    result = driver.restore(snap, restore_spec)
    assert result.ok
    target_name = result.handle.split("/", 1)[1]
    assert target_name in monitor_client.azure_monitor_workspaces_obj.workspaces


def test_restore_surfaces_provision_failure(
    driver: AzureMonitorPrometheusDriver,
    monitor_client: FakeMonitorClient,
) -> None:
    def boom(**_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("quota exceeded")

    monitor_client.azure_monitor_workspaces_obj.begin_create = boom  # type: ignore[assignment]
    snap = SnapshotHandle(
        handle="time_series/some-source",
        snapshot_id="snap-1",
        created_at="2026-05-15T00:00:00+00:00",
    )
    result = driver.restore(snap, _spec(service_handle_hint="failed"))
    assert not result.ok
    assert "begin_create" in result.message


# ---- naming + helpers -------------------------------------------


def test_workspace_name_canonicalization(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    name = driver._workspace_name_for(  # type: ignore[attr-defined]
        spec=_spec(
            organization_slug="ACME!",
            app_slug="My API",
            environment_name="Prod",
        ),
    )
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")
    for c in name:
        assert c.isalnum() or c == "-"
    assert 4 <= len(name) <= 63


def test_handle_rejects_malformed(driver: AzureMonitorPrometheusDriver) -> None:
    with pytest.raises(AzureMonitorPrometheusError):
        driver._workspace_name_from_handle("no-slash")  # type: ignore[attr-defined]
    with pytest.raises(AzureMonitorPrometheusError):
        driver._workspace_name_from_handle("time_series/")  # type: ignore[attr-defined]


# ---- config + binding schemas -----------------------------------


def test_config_schema_shape(driver: AzureMonitorPrometheusDriver) -> None:
    schema = driver.config_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    for key in (
        "public_network_access",
        "ingestion_cap_millions",
        "retention_days",
        "create_linked_log_analytics",
    ):
        assert key in props


def test_binding_schema_lists_all_env_vars(
    driver: AzureMonitorPrometheusDriver,
) -> None:
    schema = driver.binding_schema()
    for key in (
        "TIME_SERIES_URL",
        "TIME_SERIES_BUCKET",
        "TIME_SERIES_ORG",
        "AZURE_MONITOR_QUERY_ENDPOINT",
        "AZURE_MONITOR_INGESTION_ENDPOINT",
        "AZURE_MONITOR_DCR_IMMUTABLE_ID",
        "AZURE_MONITOR_WORKSPACE_NAME",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_RESOURCE_GROUP",
    ):
        assert key in schema.env_vars
