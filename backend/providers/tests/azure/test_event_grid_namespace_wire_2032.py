"""Standard lifecycle through actual SDK10.4 HTTP decoding, without Azure."""

import copy
import json
from dataclasses import replace
from urllib.parse import unquote, urlsplit

import pytest

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
from azure._event_grid_namespace_ownership import OwnershipUnknown, Receipts, Target, child_name
from azure.core.pipeline.transport import HttpTransport
from azure.managed.event_grid_namespace import AzureEventGridNamespaceConfig, AzureEventGridNamespaceDriver
from azure.mgmt.eventgrid import EventGridManagementClient
from azure.mgmt.resource.locks import ManagementLockClient
from tests.azure.test_event_grid_wire_2032 import GROUP, OWNER, SUBSCRIPTION, _Credential, _Response, source
from tests.azure.test_managed_event_grid_namespace import FakeSecrets


class Transport(HttpTransport):
    def __init__(self):
        self.rows = {}
        self.calls = []
        self.locks = []
        self.pending = set()
        self.lost = set()
        self.denied = set()
        self.pending_delete = False
        self.pages = {}

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        parsed = urlsplit(request.url)
        assert parsed.hostname == "management.azure.com"
        assert 0 < kwargs["connection_timeout"] <= 5
        assert 0 < kwargs["read_timeout"] <= 5
        path = "/" + unquote(parsed.path).lstrip("/")
        body = json.loads(request.body) if request.body else {}
        self.calls.append((request.method, path, body))
        if path in self.denied:
            return _Response(
                request,
                403,
                {"error": {"code": "AuthorizationFailed", "message": "denied ResourceNotFound diagnostic"}},
            )
        if request.method == "GET" and path in self.pages:
            return _Response(request, 200, self.pages[path])
        if request.method == "GET" and path.endswith("/providers/Microsoft.Authorization/locks"):
            scope = path.removesuffix("/providers/Microsoft.Authorization/locks")
            return _Response(
                request,
                200,
                {
                    "value": [
                        row for row in self.locks if row.get("id", "").casefold().startswith(scope.casefold() + "/")
                    ]
                },
            )
        collections = (
            "/namespaces",
            "/topics",
            "/eventSubscriptions",
            "/clients",
            "/clientGroups",
            "/topicSpaces",
            "/permissionBindings",
        )
        if request.method == "GET" and path.endswith(collections):
            rows = [
                copy.deepcopy(row)
                for key, row in self.rows.items()
                if key.startswith(path + "/") and "/" not in key[len(path) + 1 :]
            ]
            return _Response(request, 200, {"value": rows})
        if request.method == "GET":
            if path not in self.rows:
                return _Response(
                    request, 404, {"error": {"code": "ResourceNotFound", "message": "exact typed absence"}}
                )
            return _Response(request, 200, copy.deepcopy(self.rows[path]))
        if request.method in {"PUT", "PATCH"}:
            row = self.rows.setdefault(path, {"id": path, "name": path.rsplit("/", 1)[1], "properties": {}})
            for key, value in body.items():
                if key == "properties":
                    row["properties"].update(copy.deepcopy(value))
                else:
                    row[key] = copy.deepcopy(value)
            if "/eventSubscriptions/" in path:
                kind = "child"
            elif "/topics/" in path:
                kind = "topic"
            else:
                kind = "namespace"
                row["properties"]["topicsConfiguration"] = {"hostname": row["name"] + ".eastus-1.eventgrid.azure.net"}
                default = path + "/clientGroups/$all"
                self.rows.setdefault(default, {"id": default, "name": "$all", "properties": {}})
            row["properties"]["provisioningState"] = "Creating" if kind in self.pending else "Succeeded"
            if kind in self.lost:
                return _Response(
                    request,
                    500,
                    {"error": {"code": "InternalServerError", "message": "controlled lost write response"}},
                )
            response = _Response(request, 201 if kind in self.pending else 200, copy.deepcopy(row))
            if kind in self.pending:
                response.headers["Azure-AsyncOperation"] = "https://management.azure.com/never-poll"
            return response
        if request.method == "POST" and path.endswith("/listKeys"):
            return _Response(request, 200, {"key1": "controlled-namespace-key", "key2": "controlled-secondary"})
        assert request.method == "DELETE"
        if self.pending_delete:
            if path in self.rows:
                self.rows[path]["properties"]["provisioningState"] = "Deleting"
        else:
            for key in list(self.rows):
                if key == path or key.startswith(path + "/"):
                    self.rows.pop(key)
        return _Response(request, 204, {})


def fixture(config=None):
    transport = Transport()
    credential = _Credential()
    mgmt = EventGridManagementClient(credential, SUBSCRIPTION, transport=transport)
    locks = ManagementLockClient(credential, SUBSCRIPTION, transport=transport)
    secrets = FakeSecrets()
    driver = AzureEventGridNamespaceDriver(
        config=AzureEventGridNamespaceConfig(
            SUBSCRIPTION, GROUP, mgmt_client=mgmt, locks_client=locks, secret_client=secrets
        )
    )
    spec = replace(source(), config=config or {})
    return driver, transport, secrets, spec


def provisioned(config=None):
    driver, transport, secrets, spec = fixture(config)
    result = driver.provision(spec)
    assert result.ok and result.ready, result
    target = Target.saved(result.handle, subscription=SUBSCRIPTION, resource_group=GROUP, source=spec)
    transport.calls.clear()
    return driver, transport, secrets, spec, target


def writes(api):
    return [call for call in api.calls if call[0] != "GET"]


def handle(t, owner=OWNER):
    return ServiceHandle(t.handle, managed_service_id=owner)


def test_actual_complete_sdk_lifecycle_and_exact_readonly_bindings():
    driver, api, secrets, spec, t = provisioned({"subscriptions": [{"name": "workers", "delivery_mode": "pull"}]})
    assert len(t.handle) <= 512 and OWNER.replace("-", "") in t.namespace and OWNER.replace("-", "") in t.topic
    name = child_name(spec, "workers")
    assert len(name) == 50
    assert driver.status(handle(t)).state == "available"
    binding = driver.binding(handle(t), {"access_mode": "publish_pull", "subscription_name": "workers"})
    assert binding.env_vars["EVENT_BUS_ARN"].literal == t.topic_id
    assert binding.iam_grants[0].resource == t.topic_id
    assert binding.iam_grants[0].actions == ["EventGrid Data Sender"]
    assert name in binding.env_vars["EVENT_GRID_NAMESPACE_RECEIVE_ENDPOINT"].literal
    assert binding.env_vars["EVENT_GRID_NAMESPACE_ACCESS_KEY"].secret_ref in secrets.values
    assert writes(api) == []
    config = {
        "namespace_name": t.namespace,
        "topic_name": t.topic,
        "input_schema": "CloudEventSchemaV1_0",
        "is_zone_redundant": False,
        "minimum_tls_version_allowed": "1.2",
        "capacity": 3,
        "topic_retention_days": 5,
    }
    update = driver.update(UpdateSpec(t.handle, managed_service_id=OWNER, config=config))
    assert update.ok, update
    assert api.rows[t.namespace_id]["sku"]["capacity"] == 3
    assert api.rows[t.topic_id]["properties"]["eventRetentionInDays"] == 5
    assert not any(method == "PUT" for method, *_ in api.calls)
    api.calls.clear()
    retained = driver.deprovision(DeprovisionSpec(t.handle, managed_service_id=OWNER), force_destroy=True)
    assert not retained.ok and retained.errors == ["delete_data_required"] and writes(api) == []
    deleted = driver.deprovision(
        DeprovisionSpec(t.handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    )
    assert deleted.ok, deleted
    assert not api.rows and not secrets.values
    assert not any("/clientGroups/$all" in path or "/locks/" in path for method, path, *_ in writes(api))


@pytest.mark.parametrize("kind", ["namespace", "topic", "child"])
def test_accepted_creation_remains_pending_without_polling_or_credentials(kind):
    driver, api, secrets, spec = fixture({"subscriptions": [{"name": "workers", "delivery_mode": "pull"}]})
    api.pending.add(kind)
    result = driver.provision(spec)
    assert not result.ok and not result.ready
    assert not any("never-poll" in path or path.endswith("/listKeys") for _, path, *_ in api.calls)
    assert "controlled-namespace-key" not in secrets.values.values()
    t = driver._provision_target(spec)
    status = driver.status(handle(t))
    assert status.state != "available"


@pytest.mark.parametrize("kind", ["topic", "child"])
def test_lost_put_response_leaves_reserved_only_authority_and_never_adopts_on_retry(kind):
    driver, api, secrets, spec = fixture({"subscriptions": [{"name": "workers", "delivery_mode": "pull"}]})
    api.lost.add(kind)
    first = driver.provision(spec)
    assert not first.ok
    t = driver._provision_target(spec)
    r = Receipts.load(secrets.values[driver._receipt_secret_name(Receipts.empty(t, spec))], t, spec)
    identity = t.topic_id if kind == "topic" else t.child_id(child_name(spec, "workers"))
    assert r.resources[identity.casefold()]["state"] == "reserved"
    api.lost.clear()
    api.calls.clear()
    assert not driver.provision(spec).ok
    assert driver.status(handle(t)).state == "error"
    with pytest.raises(OwnershipUnknown):
        driver.binding(handle(t))
    assert not driver.deprovision(
        DeprovisionSpec(t.handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    ).ok
    assert writes(api) == []


@pytest.mark.parametrize(
    "foreign",
    [
        "topic",
        "child",
        "mqtt-client",
        "mqtt-group",
        "mqtt-space",
        "mqtt-permission",
        "namespace-owner",
        "namespace-arm",
        "topic-arm",
        "source-alias",
        "platform-alias",
    ],
)
def test_every_effect_and_binding_refuses_foreign_or_conflicting_current_resources(foreign):
    driver, api, _, spec, t = provisioned()
    if foreign in {"namespace-owner", "source-alias", "platform-alias"}:
        key = (
            "astrolift-managed-service-id"
            if foreign == "namespace-owner"
            else "Astrolift.io/managed-service-id"
            if foreign == "source-alias"
            else "X-Astrolift-Managed-By"
        )
        api.rows[t.namespace_id]["tags"][key] = "foreign"
    elif foreign in {"namespace-arm", "topic-arm"}:
        identity = t.namespace_id if foreign == "namespace-arm" else t.topic_id
        api.rows[identity]["id"] = identity.replace(GROUP, "foreign-rg")
    else:
        suffix = {
            "topic": "/topics/foreign",
            "child": "/topics/" + t.topic + "/eventSubscriptions/foreign",
            "mqtt-client": "/clients/foreign",
            "mqtt-group": "/clientGroups/foreign",
            "mqtt-space": "/topicSpaces/foreign",
            "mqtt-permission": "/permissionBindings/foreign",
        }[foreign]
        identity = t.namespace_id + suffix
        api.rows[identity] = {"id": identity, "name": "foreign", "properties": {"provisioningState": "Succeeded"}}
    assert not driver.update(UpdateSpec(t.handle, managed_service_id=OWNER, config={"capacity": 2})).ok
    assert not driver.provision(replace(spec, recorded_handle=t.handle)).ok
    assert not driver.deprovision(
        DeprovisionSpec(t.handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    ).ok
    assert driver.status(handle(t)).state == "error"
    with pytest.raises(ValueError):
        driver.binding(handle(t))
    assert writes(api) == []


@pytest.mark.parametrize("scope", ["subscription", "group", "namespace", "topic", "child"])
def test_force_never_deletes_or_bypasses_inherited_or_target_locks(scope):
    driver, api, _, _, t = provisioned({"subscriptions": [{"name": "workers", "delivery_mode": "pull"}]})
    child = t.child_id(child_name(source(), "workers"))
    identity = {
        "subscription": f"/subscriptions/{SUBSCRIPTION}",
        "group": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}",
        "namespace": t.namespace_id,
        "topic": t.topic_id,
        "child": child,
    }[scope]
    api.locks.append(
        {
            "id": identity + "/providers/Microsoft.Authorization/locks/operator",
            "name": "operator",
            "properties": {"level": "CanNotDelete"},
        }
    )
    result = driver.deprovision(
        DeprovisionSpec(t.handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    )
    assert not result.ok and writes(api) == []


@pytest.mark.parametrize("path", ["namespace", "topics", "children", "mqtt", "ancestor-locks"])
def test_denied_observation_never_becomes_absence_or_permission(path):
    driver, api, _, _, t = provisioned()
    identity = {
        "namespace": t.namespace_id,
        "topics": t.namespace_id + "/topics",
        "children": t.topic_id + "/eventSubscriptions",
        "mqtt": t.namespace_id + "/clientGroups",
        "ancestor-locks": f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.Authorization/locks",
    }[path]
    api.denied.add(identity)
    assert not driver.update(UpdateSpec(t.handle, managed_service_id=OWNER, config={"capacity": 2})).ok
    assert writes(api) == []
    if path != "ancestor-locks":
        assert driver.status(handle(t)).state == "error"


def test_missing_or_old_handle_and_foreign_provider_never_dispatch():
    driver, api, _, _, t = provisioned()
    for h in (
        ServiceHandle(t.handle),
        ServiceHandle("event_bus/old/topic", managed_service_id=OWNER),
        handle(t, "018f42f0-4420-7000-8000-000000000099"),
    ):
        assert driver.status(h).state == "error"
        with pytest.raises(ValueError):
            driver.binding(h)
    assert writes(api) == []
    api.calls.clear()
    driver._config = replace(driver._config, resource_group="other-group")
    assert not driver.update(UpdateSpec(t.handle, managed_service_id=OWNER, config={"capacity": 2})).ok
    assert api.calls == []


def test_delete_acceptance_does_not_report_absence():
    driver, api, _, _, t = provisioned()
    api.pending_delete = True
    result = driver.deprovision(DeprovisionSpec(t.handle, managed_service_id=OWNER), delete_data=True)
    assert not result.ok and result.errors == ["delete_pending"]
    assert t.namespace_id in api.rows
    assert not any(path == t.namespace_id for method, path, *_ in api.calls if method == "DELETE")


def test_missing_recorded_topic_cannot_be_recreated_even_if_its_receipt_is_missing():
    driver, api, secrets, spec, t = provisioned()
    api.rows.pop(t.topic_id)
    secrets.values.clear()
    result = driver.provision(replace(spec, recorded_handle=t.handle))
    assert not result.ok and writes(api) == []


def test_missing_recorded_child_cannot_report_ready_or_trigger_capacity_effects():
    driver, api, _, spec, t = provisioned({"subscriptions": [{"name": "workers", "delivery_mode": "pull"}]})
    api.rows.pop(t.child_id(child_name(spec, "workers")))
    assert driver.status(handle(t)).state == "error"
    with pytest.raises(OwnershipUnknown):
        driver.binding(handle(t))
    assert not driver.update(UpdateSpec(t.handle, managed_service_id=OWNER, config={"capacity": 3})).ok
    assert writes(api) == []


def test_desired_direct_read_collision_is_checked_before_any_parent_effect():
    driver, api, _, spec, t = provisioned()
    name = child_name(spec, "workers")
    identity = t.child_id(name)
    api.rows[identity] = {"id": identity, "name": name, "properties": {"provisioningState": "Succeeded"}}
    api.pages[t.topic_id + "/eventSubscriptions"] = {"value": []}
    result = driver.update(
        UpdateSpec(
            t.handle,
            managed_service_id=OWNER,
            config={"capacity": 3, "subscriptions": [{"name": "workers", "delivery_mode": "pull"}]},
        )
    )
    assert not result.ok and writes(api) == []


def test_pull_binding_cannot_emit_a_missing_or_foreign_cached_key():
    driver, api, secrets, _, t = provisioned({"subscriptions": [{"name": "workers", "delivery_mode": "pull"}]})
    key_name = next(name for name, value in secrets.values.items() if value == "controlled-namespace-key")
    secrets.tags[key_name]["astrolift-managed-service-id"] = "foreign"
    with pytest.raises(OwnershipUnknown):
        driver.binding(handle(t), {"access_mode": "pull", "subscription_name": "workers"})
    secrets.values.pop(key_name)
    with pytest.raises(OwnershipUnknown):
        driver.binding(handle(t), {"access_mode": "pull", "subscription_name": "workers"})
    assert writes(api) == []


def test_saved_target_reuses_actual_names_and_respects_secret_prefix():
    driver, api, secrets, spec = fixture()
    driver._config = replace(driver._config, secret_name_prefix="operator-prefix")
    result = driver.provision(spec)
    assert result.ok, result
    assert all(name.startswith("operator-prefix-v2-") for name in secrets.values)
    api.calls.clear()
    renamed = replace(spec, recorded_handle=result.handle, app_slug="renamed", service_handle_hint="renamed-service")
    next_result = driver.provision(renamed)
    assert next_result.ok and next_result.handle == result.handle
    assert not any(method == "PUT" for method, *_ in api.calls)
