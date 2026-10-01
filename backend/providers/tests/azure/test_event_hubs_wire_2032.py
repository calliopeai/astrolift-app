"""Declared SDK12 decoding/wire behavior; all HTTP stays in this controlled transport."""

from __future__ import annotations

import copy
import dataclasses
import json
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.managed.event_hubs import AzureEventHubsConfig, AzureEventHubsDriver, AzureEventHubsError
from azure.mgmt.eventhub import EventHubManagementClient
from azure.mgmt.resource.locks import ManagementLockClient

OWNER = "018f42f0-4420-7000-8000-000000000001"
SUBSCRIPTION = "018f42f0-4420-7000-8000-000000000002"


class _Credential:
    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken("controlled-transport-only", int(time.time()) + 3600)


class _Response(HttpResponse):
    def __init__(self, request: Any, status: int, data: dict[str, Any]):
        super().__init__(request, None)
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.content_type = "application/json"
        self.reason = "controlled HTTP response"
        self._body = json.dumps(data).encode()

    def body(self) -> bytes:
        return self._body

    def read(self) -> bytes:
        return self._body

    def iter_bytes(self):
        yield self._body

    def iter_raw(self):
        yield self._body

    def json(self) -> dict[str, Any]:
        return json.loads(self._body)


class RecordingEventHubs(HttpTransport):
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        self.failures: dict[tuple[str, str], tuple[int, str]] = {}
        self.replaced_ids: dict[str, str] = {}
        self.pages: dict[str, list[dict[str, Any] | tuple[int, str]]] = {}
        self.locks: list[dict[str, Any]] = []
        self.scoped_locks: dict[str, list[dict[str, Any]]] = {}
        self.omit_namespace_status = False
        self.pending_create = False
        self.pending_delete = False
        self.pending_hub = False
        self.closed = False

    def open(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> RecordingEventHubs:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def send(self, request: Any, **kwargs: Any) -> HttpResponse:
        parsed = urlsplit(request.url)
        assert parsed.hostname == "management.azure.com"
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        assert query["api-version"] == ["2020-05-01" if "/Microsoft.Authorization/locks" in path else "2024-01-01"]
        payload = json.loads(request.body) if request.body else {}
        self.calls.append((request.method, path, payload, kwargs))
        category = (
            "locks"
            if path.endswith("/locks")
            else "groups"
            if path.endswith("/consumergroups")
            else "hubs"
            if path.endswith("/eventhubs")
            else "group"
            if "/consumergroups/" in path
            else "hub"
            if "/eventhubs/" in path
            else "network"
            if "/networkRuleSets/" in path
            else "namespace"
        )
        if (request.method, category) in self.failures:
            status, code = self.failures[request.method, category]
            return _Response(
                request, status, {"error": {"code": code, "message": "access refused: ResourceNotFound diagnostic"}}
            )
        if request.method == "GET" and category in {"locks", "hubs", "groups"}:
            canonical_path = "/" + "/".join(part for part in path.split("/") if part)
            page_key = next((key for key in self.pages if key.casefold() == canonical_path.casefold()), category)
            if page_key in self.pages:
                page = self.pages[page_key][int(query.get("$skip", ["0"])[0])]
                if isinstance(page, tuple):
                    return _Response(
                        request, page[0], {"error": {"code": page[1], "message": "denied ResourceNotFound"}}
                    )
                return _Response(request, 200, page)
            rows = (
                self.scoped_locks.get(path, self.locks)
                if category == "locks"
                else [
                    copy.deepcopy(row)
                    for key, row in self.rows.items()
                    if key.startswith(path + "/") and "/" not in key[len(path) + 1 :]
                ]
            )
            return _Response(request, 200, {"value": rows})
        if request.method == "GET":
            if path not in self.rows:
                return _Response(
                    request, 404, {"error": {"code": "ResourceNotFound", "message": "absent exact resource"}}
                )
            row = copy.deepcopy(self.rows[path])
            if path in self.replaced_ids:
                row["id"] = self.replaced_ids[path]
            return _Response(request, 200, row)
        if request.method in {"PUT", "PATCH"}:
            if category == "network":
                return _Response(request, 200, {"id": path, **payload})
            assert not (category == "group" and path.endswith("/$Default")), "must never write the structural group"
            row = self.rows.setdefault(path, {"id": path, "name": path.rsplit("/", 1)[1], "properties": {}})
            row["properties"].update(payload.get("properties", {}))
            for key in ("sku", "tags", "identity", "location"):
                if key in payload:
                    row[key] = copy.deepcopy(payload[key])
            if category == "namespace":
                row["properties"].update(
                    status="Active", provisioningState="Creating" if self.pending_create else "Succeeded"
                )
                if self.omit_namespace_status:
                    row["properties"].pop("status", None)
                if self.pending_create:
                    response = _Response(request, 202, copy.deepcopy(row))
                    response.headers["Azure-AsyncOperation"] = "https://management.azure.com/controlled-background-poll"
                    return response
            if category == "hub":
                row["properties"]["status"] = (
                    "Creating" if self.pending_hub else row["properties"].get("status", "Active")
                )
                child = path + "/consumergroups/$Default"
                self.rows.setdefault(child, {"id": child, "name": "$Default", "properties": {}})
            return _Response(request, 200, copy.deepcopy(row))
        if request.method == "DELETE":
            assert category == "namespace"
            if self.pending_delete:
                response = _Response(request, 202, {})
                response.headers["Azure-AsyncOperation"] = "https://management.azure.com/controlled-background-poll"
                return response
            for key in list(self.rows):
                if key == path or key.startswith(path + "/"):
                    del self.rows[key]
            return _Response(request, 200, {})
        raise AssertionError((request.method, path))


def recording_config(api: RecordingEventHubs, *, variant: str = "event_hubs") -> AzureEventHubsConfig:
    return AzureEventHubsConfig(
        subscription_id=SUBSCRIPTION,
        resource_group="controlled-rg",
        variant=variant,
        mgmt_client=EventHubManagementClient(_Credential(), SUBSCRIPTION, transport=api, api_version="2024-01-01"),
        locks_client=ManagementLockClient(_Credential(), SUBSCRIPTION, transport=api),
    )


def spec(**overrides: Any) -> ProvisionSpec:
    values = dict(
        organization_id="018f42f0-4420-7000-8000-000000000003",
        organization_slug="org",
        app_id="app-id",
        app_slug="app",
        environment_id="env-id",
        environment_name="prod",
        tenant_cluster_id="cluster-id",
        service_handle_hint="events",
        size="small",
        managed_service_id=OWNER,
    )
    return ProvisionSpec(**(values | overrides))


@pytest.fixture(params=["event_hubs", "event_hubs_kafka"])
def runtime(request):
    api = RecordingEventHubs()
    cfg = recording_config(api, variant=request.param)
    yield api, AzureEventHubsDriver(config=cfg)
    cfg.mgmt_client.close()
    cfg.locks_client.close()


def owned(runtime, **config: Any):
    api, driver = runtime
    result = driver.provision(spec(config=config))
    assert result.ok and result.ready, result
    target = driver._saved_target(result.handle, spec())
    api.calls.clear()
    return api, driver, result.handle, target


def test_actual_sdk_enums_properties_and_exact_hub_scoped_binding(runtime):
    api, driver, handle, target = owned(runtime)
    assert OWNER.replace("-", "") in target.namespace and OWNER.replace("-", "") in target.hub
    assert len(target.namespace) <= 50 and len(handle) <= 512 and len(target.hub_id) <= 512
    source = ServiceHandle(handle, managed_service_id=OWNER)
    assert driver.status(source).state == "available"
    result = driver.update(
        UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 2, "retention_time_in_hours": 48})
    )
    assert result.ok, result
    assert api.rows[target.namespace_id]["sku"] == {"name": "Standard", "tier": "Standard", "capacity": 2}
    assert api.rows[target.hub_id]["properties"]["status"] == "Active"
    assert api.rows[target.hub_id]["properties"]["retentionDescription"]["cleanupPolicy"] == "Delete"
    binding = driver.binding(source)
    assert [(g.resource, g.actions) for g in binding.iam_grants] == [
        (target.hub_id, ["Azure Event Hubs Data Sender"]),
        (target.hub_id, ["Azure Event Hubs Data Receiver"]),
    ]
    assert not any(path.endswith("/$Default") and verb != "GET" for verb, path, *_ in api.calls)
    for _, _, _, opts in api.calls:
        assert 0 < opts["connection_timeout"] <= 5 and 0 < opts["read_timeout"] <= 5


@pytest.mark.parametrize("entity", ["namespace", "hub", "group"])
@pytest.mark.parametrize("replacement", ["foreign", "unlabelled", "wrong-arm", "missing-arm"])
def test_refused_current_sources_have_no_mutation_or_binding(runtime, entity, replacement):
    api, driver, handle, target = owned(runtime)
    if entity == "group" and target.group == "-":
        path = target.group_id("custom")
        api.rows[path] = {
            "id": path,
            "name": "custom",
            "properties": {"userMetadata": api.rows[target.hub_id]["properties"]["userMetadata"]},
        }
    else:
        path = (
            target.namespace_id
            if entity == "namespace"
            else target.hub_id
            if entity == "hub"
            else target.group_id(target.group)
        )
    if replacement == "foreign":
        if entity == "namespace":
            api.rows[path]["tags"]["astrolift-managed-service-id"] = "018f42f0-4420-7000-8000-000000000099"
        else:
            api.rows[path]["properties"]["userMetadata"] = api.rows[path]["properties"]["userMetadata"].replace(
                OWNER, "018f42f0-4420-7000-8000-000000000099"
            )
    elif replacement == "unlabelled":
        if entity == "namespace":
            api.rows[path]["tags"] = {}
        else:
            api.rows[path]["properties"]["userMetadata"] = ""
    else:
        api.replaced_ids[path] = "" if replacement == "missing-arm" else path.replace("controlled-rg", "foreign-rg")
    before = copy.deepcopy(api.rows)
    for result in [
        driver.provision(spec(recorded_handle=handle)),
        driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 3})),
        driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True),
    ]:
        assert not result.ok and set(result.errors) & {"ownership_unknown", "ownership_refused"}, result
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "error"
    with pytest.raises((AzureEventHubsError, ValueError)):
        driver.binding(ServiceHandle(handle, managed_service_id=OWNER))
    assert api.rows == before and all(verb == "GET" for verb, *_ in api.calls)


def test_complete_extra_hub_inventory_refuses_parent_effects(runtime):
    api, driver, handle, target = owned(runtime)
    extra = target.namespace_id + "/eventhubs/foreign"
    api.rows[extra] = {"id": extra, "name": "foreign", "properties": {}}
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["ownership_refused"]
    assert all(verb == "GET" for verb, *_ in api.calls)


@pytest.mark.parametrize("kind", ["hubs", "groups", "locks"])
def test_paging_denial_overflow_or_changed_arm_collection_refuses_before_effects(runtime, kind):
    api, driver, handle, target = owned(runtime)
    suffix = (
        "/eventhubs"
        if kind == "hubs"
        else "/eventhubs/" + target.hub + "/consumergroups"
        if kind == "groups"
        else "/providers/Microsoft.Authorization/locks"
    )
    base = "https://management.azure.com" + target.namespace_id + suffix
    page_key = target.namespace_id + suffix if kind == "locks" else kind
    api.pages[page_key] = [
        {
            "value": [],
            "nextLink": base + "?api-version=" + ("2020-05-01" if kind == "locks" else "2024-01-01") + "&$skip=1",
        },
        (403, "ForbiddenResourceNotFound"),
    ]
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True)
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert (
        len(
            [
                c
                for c in api.calls
                if c[0] == "GET"
                and ("/" + "/".join(part for part in c[1].split("/") if part)).casefold()
                == (target.namespace_id + suffix).casefold()
            ]
        )
        == 2
    )
    api.calls.clear()
    api.pages[page_key] = [{"value": [], "nextLink": "https://foreign.invalid/collection"}]
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 2}))
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert all(verb == "GET" for verb, *_ in api.calls)


def test_no_polling_background_requests_and_partial_retry(runtime):
    api, driver = runtime
    api.pending_create = True
    result = driver.provision(spec())
    assert not result.ok and not result.ready and result.errors == ["provision_pending"]
    assert len(api.calls) == 5 and not any("background" in path for _, path, *_ in api.calls)
    assert len([c for c in api.calls if c[1].endswith("/locks")]) == 2
    target = driver._saved_target(result.handle, spec())
    api.rows[target.namespace_id]["properties"]["provisioningState"] = "Succeeded"
    api.pending_create = False
    result = driver.provision(spec())
    assert result.ok and result.ready, result
    api.calls.clear()
    api.pending_delete = True
    result = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id=OWNER), delete_data=True)
    assert not result.ok and result.errors == ["delete_pending"]
    assert not any("background" in path for _, path, *_ in api.calls)


def test_locks_never_deleted_and_retention_never_overridden(runtime):
    api, driver, handle, target = owned(runtime)
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), force_destroy=True)
    assert not result.ok and result.errors == ["delete_data_required"]
    api.locks = [
        {
            "id": target.namespace_id + "/providers/Microsoft.Authorization/locks/operator",
            "name": "operator",
            "properties": {"level": "CanNotDelete"},
        }
    ]
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["resource_lock_present"] and all(c[0] == "GET" for c in api.calls)


@pytest.mark.parametrize(
    "identity", ["", "service-id", "0" * 32, OWNER.replace("-", ""), "00000000-0000-0000-0000-000000000000"]
)
def test_unknown_source_identity_and_legacy_handle_no_sdk(runtime, identity):
    api, driver = runtime
    result = driver.provision(spec(managed_service_id=identity))
    assert not result.ok and result.errors == ["ownership_unknown"] and not api.calls
    result = driver.deprovision(
        DeprovisionSpec(driver._profile.kind + "/legacy-ns/legacy-hub", managed_service_id=OWNER), delete_data=True
    )
    assert not result.ok and result.errors == ["ownership_unknown"] and not api.calls


@pytest.mark.parametrize("field", ["namespace_name", "event_hub_name"])
@pytest.mark.parametrize("override", ["caller-physical-name", ""])
def test_new_override_without_full_guid_is_not_rewritten_or_ignored(runtime, field, override):
    api, driver = runtime
    result = driver.provision(spec(config={field: override}))
    assert not result.ok and result.errors == ["ownership_refused"] and not api.calls


def test_saved_target_remains_exact_on_slug_and_prefix_change(runtime):
    api, driver, handle, target = owned(runtime)
    changed = AzureEventHubsDriver(
        config=dataclasses.replace(
            driver._config,
            namespace_name_prefix="changed",
            event_hub_name_prefix="changed",
            default_consumer_group="changed",
        )
    )
    result = changed.provision(
        spec(recorded_handle=handle, app_slug="renamed", organization_slug="renamed", environment_name="renamed")
    )
    assert result.ok and result.handle == handle
    assert set(api.rows) == {target.namespace_id, target.hub_id, target.group_id("$Default")} | (
        set() if target.group == "-" else {target.group_id(target.group)}
    )


def test_basic_default_group_structural_and_full_immutable_snapshot_unchanged():
    api = RecordingEventHubs()
    cfg = recording_config(api)
    driver = AzureEventHubsDriver(config=cfg)
    config = {"sku": "Basic", "zone_redundant": False, "kafka_enabled": False, "cleanup_policy": "Delete"}
    result = driver.provision(spec(config=config))
    assert result.ok and result.ready, result
    target = driver._saved_target(result.handle, spec())
    assert target.group == "$Default"
    assert (
        driver.binding(ServiceHandle(result.handle, managed_service_id=OWNER))
        .env_vars["EVENTHUB_CONSUMER_GROUP"]
        .literal
        == "$Default"
    )
    api.calls.clear()
    config.update(namespace_name=target.namespace, event_hub_name=target.hub, capacity=2)
    update = driver.update(UpdateSpec(result.handle, managed_service_id=OWNER, config=config))
    assert update.ok, update
    assert not any("consumergroups" in p and v != "GET" for v, p, *_ in api.calls)
    cfg.mgmt_client.close()
    cfg.locks_client.close()


def test_custom_group_removal_refuses_before_namespace_update():
    api = RecordingEventHubs()
    cfg = recording_config(api)
    driver = AzureEventHubsDriver(config=cfg)
    result = driver.provision(spec(config={"consumer_groups": ["analytics"]}))
    assert result.ok, result
    api.calls.clear()
    result = driver.update(
        UpdateSpec(result.handle, managed_service_id=OWNER, config={"consumer_groups": [], "capacity": 2})
    )
    assert not result.ok and result.errors == ["reprovision_required"] and all(c[0] == "GET" for c in api.calls)
    cfg.mgmt_client.close()
    cfg.locks_client.close()


@pytest.mark.parametrize("status", [None, "Disabled", "SendDisabled", "ReceiveDisabled", "Unknown"])
def test_unknown_or_partial_data_plane_readiness_never_available(runtime, status):
    api, driver, handle, target = owned(runtime)
    api.rows[target.hub_id]["properties"]["status"] = status
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "error"
    with pytest.raises(AzureEventHubsError, match="active"):
        driver.binding(ServiceHandle(handle, managed_service_id=OWNER))
    assert all(c[0] == "GET" for c in api.calls)


def test_supported_status_disable_and_reenable_observe_actual_desired_state(runtime):
    api, driver, handle, target = owned(runtime)
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"status": "Disabled"}))
    assert result.ok and api.rows[target.hub_id]["properties"]["status"] == "Disabled", result
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "error"
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"status": "Active"}))
    assert result.ok and driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "available", result


@pytest.mark.parametrize("overflow", ["pages", "items"])
def test_bounded_complete_inventory_overflow_refuses_without_mutation(runtime, overflow):
    api, driver, handle, target = owned(runtime)
    base = "https://management.azure.com" + target.namespace_id + "/eventhubs?api-version=2024-01-01&$skip="
    if overflow == "pages":
        api.pages["hubs"] = [{"value": [], "nextLink": base + str(i + 1)} for i in range(5)]
    else:
        api.pages["hubs"] = [{"value": [copy.deepcopy(api.rows[target.hub_id])] * 129}]
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert all(c[0] == "GET" for c in api.calls)
    assert len([c for c in api.calls if c[1].endswith("/eventhubs")]) == (4 if overflow == "pages" else 1)


@pytest.mark.parametrize("category", ["namespace", "hub", "group", "groups", "hubs", "locks"])
def test_permission_denial_not_found_text_is_never_cleanup_success(runtime, category):
    api, driver, handle, _ = owned(runtime)
    api.failures["GET", category] = 403, "ForbiddenResourceNotFound"
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["ownership_unknown"] and all(c[0] == "GET" for c in api.calls)


def test_typed_namespace_absence_converges_and_lookalike_errors_never_do(runtime, monkeypatch):
    api, driver, handle, target = owned(runtime)
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True)
    assert result.ok and not api.rows, result
    api.calls.clear()
    assert driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True).ok
    assert all(c[0] == "GET" for c in api.calls)
    fake_not_found = type("ResourceNotFoundError", (Exception,), {"status_code": 404})

    def unavailable(**kwargs):
        raise fake_not_found("denied ResourceNotFound diagnostic")

    monkeypatch.setattr(driver._mgmt.namespaces, "get", unavailable)
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True)
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert target.namespace_id not in api.rows


def test_capture_exact_full_block_pre_authorized_uami_and_honest_archive_contract(runtime):
    api, driver = runtime
    identity = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/controlled-rg"
        "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/capture"
    )
    storage = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/controlled-rg"
        "/providers/Microsoft.Storage/storageAccounts/capture"
    )
    driver = AzureEventHubsDriver(config=dataclasses.replace(driver._config, allowed_identity_resource_ids=(identity,)))
    config = {
        "capture_enabled": True,
        "capture_storage_account_resource_id": storage,
        "capture_blob_container": "archives",
        "capture_identity_type": "UserAssigned",
        "capture_user_assigned_identity_resource_id": identity,
        "capture_interval_seconds": 120,
    }
    result = driver.provision(spec(config=config))
    assert result.ok, result
    target = driver._saved_target(result.handle, spec())
    capture = api.rows[target.hub_id]["properties"]["captureDescription"]
    assert capture["encoding"] == "Avro" and capture["intervalInSeconds"] == 120
    assert capture["destination"]["identity"] == {"type": "UserAssigned", "userAssignedIdentity": identity}
    assert capture["destination"]["properties"]["storageAccountResourceId"] == storage
    api.calls.clear()
    result = driver.update(
        UpdateSpec(result.handle, managed_service_id=OWNER, config={"capture_interval_seconds": 180})
    )
    assert not result.ok and result.errors == ["invalid_runtime_controls"] and all(c[0] == "GET" for c in api.calls)
    assert not any("Microsoft.Storage" in c[1] for c in api.calls)


def test_noop_preserves_children_and_unchanged_immutable_snapshot_may_update_capacity(runtime):
    api, driver, handle, target = owned(runtime)
    config = {
        "sku": "Standard",
        "zone_redundant": False,
        "kafka_enabled": driver._profile.kafka,
        "namespace_name": target.namespace,
        "event_hub_name": target.hub,
        "cleanup_policy": "Delete",
        "capacity": 2,
    }
    assert driver.update(UpdateSpec(handle, managed_service_id=OWNER, config=config)).ok
    api.calls.clear()
    assert driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={})).ok
    assert all(c[0] == "GET" for c in api.calls)
    config["zone_redundant"] = True
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config=config))
    assert not result.ok and result.errors == ["reprovision_required"] and all(c[0] == "GET" for c in api.calls)


@pytest.mark.parametrize(
    "config",
    [
        {"capacity": 2.5},
        {"capacity": True},
        {"status": {}},
        {"capture_enabled": "false"},
        {"consumer_groups": ["bad/name"]},
        {"cleanup_policy": "DeleteOrCompact"},
    ],
)
def test_invalid_declared_config_refuses_before_namespace_mutation(runtime, config):
    api, driver = runtime
    result = driver.provision(spec(config=config))
    assert not result.ok and result.errors == ["invalid_runtime_controls"] and not api.calls


def test_namespace_readiness_requires_actual_status_and_provisioning_state(runtime):
    api, driver, handle, target = owned(runtime)
    del api.rows[target.namespace_id]["properties"]["provisioningState"]
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "error"
    with pytest.raises(AzureEventHubsError, match="active"):
        driver.binding(ServiceHandle(handle, managed_service_id=OWNER))


def test_exact_saved_storage_boundary_has_no_truncation_and_overflow_has_no_http(runtime):
    api, driver = runtime
    driver = AzureEventHubsDriver(config=dataclasses.replace(driver._config, resource_group="r" * 90))
    ns = "n" + "s" * 49
    group = "-" if driver._profile.kafka else "g" * 50
    stub = driver._coordinates(ns, "h", group)
    hub = "h" * (512 - len(stub.hub_id) + 1)
    target = driver._coordinates(ns, hub, group)
    assert len(target.hub_id) == 512 and len(hub) <= 256 and len(target.handle(driver._profile.kind)) <= 512
    assert driver._saved_target(target.handle(driver._profile.kind), spec()) == target
    with pytest.raises(AzureEventHubsError, match=r"storage|representable"):
        driver._coordinates(ns, hub + "h", group)
    assert not api.calls


def test_documented_namespace_get_shape_omits_status_without_inventing_active(runtime):
    api, driver = runtime
    api.omit_namespace_status = True
    result = driver.provision(spec())
    assert result.ok and result.ready, result
    target = driver._saved_target(result.handle, spec())
    assert "status" not in api.rows[target.namespace_id]["properties"]
    source = ServiceHandle(result.handle, managed_service_id=OWNER)
    observed = driver.status(source)
    assert observed.state == "available" and "namespace=unknown" in observed.message
    assert driver.binding(source).env_vars["EVENTHUB_RESOURCE_ID"].literal == target.hub_id


@pytest.mark.parametrize(
    "properties", [{"provisioningState": "Succeeded", "status": "Disabled"}, {"provisioningState": "Unknown"}, {}]
)
def test_explicit_namespace_refusal_and_unknown_provisioning_do_not_enable_binding(runtime, properties):
    api, driver, handle, target = owned(runtime)
    api.rows[target.namespace_id]["properties"] = properties
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "error"
    with pytest.raises(AzureEventHubsError, match="active"):
        driver.binding(ServiceHandle(handle, managed_service_id=OWNER))


def test_explicit_install_builtin_default_is_looked_up_and_never_rewritten():
    api = RecordingEventHubs()
    cfg = dataclasses.replace(recording_config(api), default_consumer_group="$Default")
    driver = AzureEventHubsDriver(config=cfg)
    result = driver.provision(spec())
    assert result.ok and result.ready, result
    target = driver._saved_target(result.handle, spec())
    assert target.group == "$Default"
    assert (
        driver.binding(ServiceHandle(result.handle, managed_service_id=OWNER))
        .env_vars["EVENTHUB_CONSUMER_GROUP"]
        .literal
        == "$Default"
    )
    assert not any("consumergroups" in path and verb != "GET" for verb, path, *_ in api.calls)
    cfg.mgmt_client.close()
    cfg.locks_client.close()


@pytest.mark.parametrize(
    "alias",
    [
        "astrolift_io_managed_service_id",
        "astrolift_managed_service_id",
        "astrolift.io/managed_service_id",
        "x-astrolift-managed-service-id",
        "ASTROLIFT-MANAGED-SERVICE-ID",
    ],
)
@pytest.mark.parametrize("entity", ["namespace", "hub", "group"])
def test_conflicting_owner_alias_refuses_all_effects_and_binding(runtime, entity, alias):
    api, driver, handle, target = owned(runtime)
    path = target.namespace_id if entity == "namespace" else target.hub_id
    if entity == "group":
        path = target.group_id("custom")
        api.rows[path] = {
            "id": path,
            "name": "custom",
            "properties": {"userMetadata": api.rows[target.hub_id]["properties"]["userMetadata"]},
        }
    tags = api.rows[path]["tags"] if entity == "namespace" else json.loads(api.rows[path]["properties"]["userMetadata"])
    tags[alias] = "018f42f0-4420-7000-8000-000000000099"
    if entity != "namespace":
        api.rows[path]["properties"]["userMetadata"] = json.dumps(tags)
    before = copy.deepcopy(api.rows)
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 3}))
    assert not result.ok and result.errors == ["ownership_refused"], result
    assert not driver.provision(spec(recorded_handle=handle)).ok
    assert not driver.deprovision(
        DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    ).ok
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "error"
    with pytest.raises((AzureEventHubsError, ValueError)):
        driver.binding(ServiceHandle(handle, managed_service_id=OWNER))
    assert api.rows == before and all(verb == "GET" for verb, *_ in api.calls)


@pytest.mark.parametrize("scope", ["subscription", "resource-group"])
def test_inherited_lock_is_not_visible_at_resource_level_but_refuses_effects(runtime, scope):
    api, driver, handle, _ = owned(runtime)
    parent = f"/subscriptions/{SUBSCRIPTION}" + ("/resourceGroups/controlled-rg" if scope == "resource-group" else "")
    collection = parent + "/providers/Microsoft.Authorization/locks"
    api.scoped_locks[collection] = [
        {"id": collection + "/operator", "name": "operator", "properties": {"level": "CanNotDelete"}}
    ]
    before = copy.deepcopy(api.rows)
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 3}))
    assert not result.ok and result.errors == ["resource_lock_present"], result
    assert not driver.deprovision(
        DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    ).ok
    assert api.rows == before and all(verb == "GET" for verb, *_ in api.calls)


def lock_collection(scope, target):
    base = f"/subscriptions/{SUBSCRIPTION}"
    if scope == "resource-group":
        base += "/resourceGroups/controlled-rg"
    elif scope == "resource":
        base = target.namespace_id
    return base + "/providers/Microsoft.Authorization/locks"


def wire_lock(scope, name="operator", level="ReadOnly"):
    return {
        "id": scope + "/providers/Microsoft.Authorization/locks/" + name,
        "name": name,
        "properties": {"level": level},
    }


@pytest.mark.parametrize("alias", ["astrolift_io_managed_service_id", "astrolift_managed_service_id"])
@pytest.mark.parametrize("keep_canonical", [True, False])
def test_matching_released_aliases_remain_usable(runtime, alias, keep_canonical):
    api, driver, handle, target = owned(runtime)
    api.rows[target.namespace_id]["tags"][alias] = OWNER
    if not keep_canonical:
        api.rows[target.namespace_id]["tags"].pop("astrolift-managed-service-id")
    for row in api.rows.values():
        if row["properties"].get("userMetadata"):
            tags = json.loads(row["properties"]["userMetadata"])
            tags[alias] = OWNER
            if not keep_canonical:
                tags.pop("astrolift-managed-service-id")
            row["properties"]["userMetadata"] = json.dumps(tags)
    assert driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 2})).ok
    assert driver.binding(ServiceHandle(handle, managed_service_id=OWNER)).iam_grants
    assert driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True).ok


@pytest.mark.parametrize("scope", ["subscription", "resource-group"])
def test_inherited_lock_refuses_initial_namespace_creation(runtime, scope):
    api, driver = runtime
    target = driver._provision_target(spec())
    collection = lock_collection(scope, target)
    api.scoped_locks[collection] = [wire_lock(collection.removesuffix("/providers/Microsoft.Authorization/locks"))]
    result = driver.provision(spec())
    assert not result.ok and result.errors == ["resource_lock_present"]
    assert not api.rows and all(verb == "GET" for verb, *_ in api.calls)


@pytest.mark.parametrize("scope", ["subscription", "resource-group", "resource"])
@pytest.mark.parametrize("failure", ["denial", "wrong-scope", "untrusted-host", "pages", "items"])
def test_complete_inherited_lock_pagination_required_before_effects(runtime, scope, failure):
    api, driver, handle, target = owned(runtime)
    collection = lock_collection(scope, target)
    base = "https://management.azure.com" + collection + "?api-version=2020-05-01&$skip="
    if failure == "denial":
        pages = [{"value": [], "nextLink": base + "1"}, (403, "ForbiddenResourceNotFound")]
    elif failure in {"wrong-scope", "untrusted-host"}:
        next_link = (
            base.replace("controlled-rg", "foreign-rg")
            if failure == "wrong-scope" and scope != "subscription"
            else base.replace(SUBSCRIPTION, "018f42f0-4420-7000-8000-000000000099")
        )
        if failure == "untrusted-host":
            next_link = base.replace("management.azure.com", "foreign.invalid")
        pages = [{"value": [], "nextLink": next_link + "1"}]
    elif failure == "pages":
        pages = [{"value": [], "nextLink": base + str(index + 1)} for index in range(5)]
    else:
        pages = [{"value": [wire_lock(collection.removesuffix("/providers/Microsoft.Authorization/locks"))] * 129}]
    api.pages[collection] = pages
    before = copy.deepcopy(api.rows)
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["ownership_unknown"], result
    assert api.rows == before and all(verb == "GET" for verb, *_ in api.calls)
    if failure == "pages":
        assert (
            len(
                [
                    c
                    for c in api.calls
                    if ("/" + "/".join(p for p in c[1].split("/") if p)).casefold() == collection.casefold()
                ]
            )
            == 4
        )


@pytest.mark.parametrize("scope", ["subscription", "resource-group", "resource"])
def test_later_lock_page_refuses_even_when_first_page_is_empty(runtime, scope):
    api, driver, handle, target = owned(runtime)
    collection = lock_collection(scope, target)
    base = "https://management.azure.com" + collection + "?api-version=2020-05-01&$skip=1"
    api.pages[collection] = [
        {"value": [], "nextLink": base},
        {"value": [wire_lock(collection.removesuffix("/providers/Microsoft.Authorization/locks"))]},
    ]
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 2}))
    assert not result.ok and result.errors == ["resource_lock_present"]
    assert all(verb == "GET" for verb, *_ in api.calls)


@pytest.mark.parametrize("bad", ["no-id", "foreign-sub", "malformed", "name-mismatch", "unknown-level"])
def test_unknown_lock_identity_cannot_be_classified_as_unrelated(runtime, bad):
    api, driver, handle, target = owned(runtime)
    collection = lock_collection("subscription", target)
    lock = wire_lock(
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/other/providers/Microsoft.Storage/storageAccounts/unrelated"
    )
    if bad == "no-id":
        lock.pop("id")
    elif bad == "foreign-sub":
        lock["id"] = lock["id"].replace(SUBSCRIPTION, "018f42f0-4420-7000-8000-000000000099")
    elif bad == "malformed":
        lock["id"] = lock["id"].replace("storageAccounts/unrelated", "storageAccounts")
    elif bad == "name-mismatch":
        lock["name"] = "other"
    else:
        lock["properties"]["level"] = "FutureLevel"
    api.scoped_locks[collection] = [lock]
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 2}))
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert all(verb == "GET" for verb, *_ in api.calls)


def test_valid_unrelated_locks_do_not_block_target_but_child_locks_do(runtime):
    api, driver, handle, target = owned(runtime)
    subscription_collection = lock_collection("subscription", target)
    group_collection = lock_collection("resource-group", target)
    api.scoped_locks[subscription_collection] = [wire_lock(f"/subscriptions/{SUBSCRIPTION}/resourceGroups/other")]
    api.scoped_locks[group_collection] = [
        wire_lock(
            f"/subscriptions/{SUBSCRIPTION}/resourceGroups/controlled-rg/providers/Microsoft.EventHub/namespaces/other"
        )
    ]
    assert driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 2})).ok
    api.calls.clear()
    api.scoped_locks[subscription_collection] = [wire_lock(target.group_id("$Default"))]
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["resource_lock_present"]
    assert all(verb == "GET" for verb, *_ in api.calls)


def test_resource_lock_next_link_preserves_exact_sdk_empty_parent_collection(runtime):
    api, driver, handle, target = owned(runtime)
    collection = lock_collection("resource", target)
    wire_path = collection.replace(
        "/providers/Microsoft.EventHub/namespaces/", "/providers/Microsoft.EventHub//namespaces/"
    )
    api.pages[collection] = [
        {"value": [], "nextLink": "https://management.azure.com" + wire_path + "?api-version=2020-05-01&$skip=1"},
        {"value": [wire_lock(target.namespace_id)]},
    ]
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"capacity": 2}))
    assert not result.ok and result.errors == ["resource_lock_present"]
    assert all(verb == "GET" for verb, *_ in api.calls)
