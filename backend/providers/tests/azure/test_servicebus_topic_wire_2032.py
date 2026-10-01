"""Real SDK 10 serialization/error/paging through controlled recording HTTP only."""

from __future__ import annotations

import copy
import dataclasses
import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from _sdk.azure_ownership import AzureOwnershipError
from _sdk.managed_service import UPDATE_NOT_SUPPORTED_IN_PLACE, DeprovisionSpec, ServiceHandle, UpdateSpec
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.managed.queue_servicebus import AzureServiceBusConfig, AzureServiceBusDriver
from azure.mgmt.servicebus import ServiceBusManagementClient

from .test_servicebus_queue_wire_2032 import OWNER, SUBSCRIPTION, _Credential, _Response, spec


class RecordingTopics(HttpTransport):
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        self.failures: dict[tuple[str, str], tuple[int, str]] = {}
        self.inventory_pages: list[dict[str, Any] | tuple[int, str]] | None = None
        self.replaced_ids: dict[str, str] = {}
        self.payloads: dict[str, list[bytes]] = {}
        self.closed = False

    def open(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> RecordingTopics:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def send(self, request: Any, **kwargs: Any) -> HttpResponse:
        parsed = urlsplit(request.url)
        assert parsed.hostname == "management.azure.com"
        query = parse_qs(parsed.query)
        assert query["api-version"] == ["2026-01-01"]
        path = unquote(parsed.path)
        assert "/providers/Microsoft.ServiceBus/namespaces/" in path and "/topics/" in path
        payload = json.loads(request.body) if request.body else {}
        self.calls.append((request.method, path, payload, kwargs))
        category = (
            "inventory"
            if path.endswith("/subscriptions")
            else "child"
            if "/subscriptions/" in path.partition("/topics/")[2]
            else "topic"
        )
        error = self.failures.get((request.method, category))
        if error:
            return _Response(
                request,
                error[0],
                {"error": {"code": error[1], "message": "access denied: ResourceNotFound diagnostic"}},
            )
        if request.method == "GET" and category == "inventory":
            if self.inventory_pages is not None:
                page = self.inventory_pages[int(query.get("$skip", ["0"])[0])]
                if isinstance(page, tuple):
                    return _Response(
                        request, page[0], {"error": {"code": page[1], "message": "denied: resource not found"}}
                    )
                return _Response(request, 200, page)
            return _Response(
                request,
                200,
                {"value": [copy.deepcopy(row) for key, row in self.rows.items() if key.startswith(path + "/")]},
            )
        if request.method == "GET":
            if path not in self.rows:
                return _Response(request, 404, {"error": {"code": "ResourceNotFound", "message": "entity absent"}})
            row = copy.deepcopy(self.rows[path])
            if path in self.replaced_ids:
                row["id"] = self.replaced_ids[path]
            return _Response(request, 200, row)
        if request.method == "PUT":
            assert set(payload) == {"properties"}, payload
            assert "userMetadata" in payload["properties"]
            assert all("_" not in key for key in payload["properties"])
            row = self.rows.setdefault(
                path, {"id": path, "name": path.rsplit("/", 1)[1], "properties": {"status": "Active"}}
            )
            row["properties"].update(payload["properties"])
            return _Response(request, 200, copy.deepcopy(row))
        if request.method == "DELETE":
            if path not in self.rows:
                return _Response(request, 404, {"error": {"code": "ResourceNotFound", "message": "entity absent"}})
            del self.rows[path]
            self.payloads.pop(path, None)
            return _Response(request, 200, {})
        raise AssertionError(request.method)


def recording_config(api: RecordingTopics, *, kind: str = "queue") -> AzureServiceBusConfig:
    client = ServiceBusManagementClient(_Credential(), SUBSCRIPTION, transport=api, retry_total=0)
    return AzureServiceBusConfig(
        subscription_id=SUBSCRIPTION,
        resource_group="controlled-rg",
        namespace_name="controlled-sb",
        handle_kind=kind,
        location="eastus2",
        client=client,
    )


@pytest.fixture(params=["queue", "topic"])
def runtime(request):
    api = RecordingTopics()
    cfg = recording_config(api, kind=request.param)
    driver = AzureServiceBusDriver(config=cfg)
    yield api, driver
    cfg.client.close()
    assert api.closed


def owned(runtime):
    api, driver = runtime
    result = driver.provision(spec())
    assert result.ok, result
    target = driver._saved_target(result.handle, spec())
    api.calls.clear()
    return api, driver, result.handle, target


def test_actual_sdk_parent_child_properties_saved_identity_and_split_binding(runtime):
    api, driver, handle, target = owned(runtime)
    assert len(handle) <= 512 and handle.startswith(driver._config.handle_kind + "/arm-v1/")
    assert target.topic.endswith(OWNER.replace("-", "")) and len(target.topic) <= 42
    assert target.child == target.topic + "-default" and len(target.child) <= 50
    topic, child = api.rows[target.topic_id], api.rows[target.child_id]
    assert topic["properties"]["defaultMessageTimeToLive"] == "P14D"
    assert child["properties"]["lockDuration"] == "PT30S"
    assert child["properties"]["maxDeliveryCount"] == 10
    for row in (topic, child):
        assert f"astrolift-managed-service-id={OWNER}" in row["properties"]["userMetadata"]
    source = ServiceHandle(handle, managed_service_id=OWNER)
    assert driver.status(source).state == "available"
    binding = driver.binding(source)
    assert [(grant.resource, grant.actions) for grant in binding.iam_grants] == [
        (target.topic_id, ["Azure Service Bus Data Sender"]),
        (target.child_id, ["Azure Service Bus Data Receiver"]),
    ]
    assert binding.env_vars["SERVICEBUS_SUBSCRIPTION"].literal == target.child
    for _, _, _, options in api.calls:
        assert 0 < options["connection_timeout"] <= 5 and 0 < options["read_timeout"] <= 5
    if driver._config.handle_kind == "topic":
        assert binding.env_vars["TOPIC_ARN_OR_ID"].literal == target.topic_id


def test_actual_update_changes_only_declared_topic_fields_and_observes_typed_values(runtime):
    api, driver, handle, target = owned(runtime)
    child_before = copy.deepcopy(api.rows[target.child_id])
    result = driver.update(
        UpdateSpec(
            handle, managed_service_id=OWNER, config={"max_size_in_megabytes": 2048, "default_message_ttl": "PT3600S"}
        )
    )
    assert result.ok, result
    assert api.rows[target.topic_id]["properties"]["maxSizeInMegabytes"] == 2048
    assert api.rows[target.topic_id]["properties"]["defaultMessageTimeToLive"] == "PT01H00M00S"
    assert api.rows[target.child_id] == child_before
    assert driver.editable_fields() == ["max_size_in_megabytes", "default_message_ttl"]
    assert [(verb, path) for verb, path, *_ in api.calls if verb != "GET"] == [("PUT", target.topic_id)]


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"enable_partitioning": True},
        {"max_delivery_count": 5},
        {"lock_duration": "PT1M"},
        {"dead_lettering_on_message_expiration": False},
    ],
)
def test_unsupported_only_updates_are_permanent_and_sdk_free_even_for_unknown_handles(runtime, config):
    api, driver = runtime
    result = driver.update(UpdateSpec("probe/handle", config=config))
    assert not result.ok and not result.retryable and result.errors == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    assert api.calls == []


@pytest.mark.parametrize("identity", ["", "unknown", "0" * 32, OWNER.replace("-", ""), OWNER.upper().replace("f", "F")])
def test_invalid_source_uuid_no_sdk(runtime, identity):
    api, driver = runtime
    result = driver.provision(spec(managed_service_id=identity))
    assert not result.ok and "ownership_refused" in result.errors and api.calls == []


@pytest.mark.parametrize(
    "tags", [{"note": "safe;astrolift-managed-service-id=foreign"}, {f"label{i}": "x" * 250 for i in range(8)}]
)
def test_hostile_or_incomplete_metadata_no_sdk(runtime, tags):
    api, driver = runtime
    result = driver.provision(spec(tags=tags))
    assert not result.ok and "ownership_refused" in result.errors and api.calls == []


@pytest.mark.parametrize("entity", ["topic", "child"])
@pytest.mark.parametrize(
    "replacement",
    [
        "foreign",
        "missing-platform",
        "missing-owner",
        "unlabelled",
        "duplicate",
        "alias",
        "different-arm",
        "missing-arm",
    ],
)
def test_current_parent_and_child_identity_gate_all_supported_effects_and_binding(runtime, entity, replacement):
    api, driver, handle, target = owned(runtime)
    path = target.topic_id if entity == "topic" else target.child_id
    row = api.rows[path]
    blob = row["properties"]["userMetadata"]
    if replacement == "foreign":
        blob = blob.replace(OWNER, "018f42f0-4420-7000-8000-000000000099")
    elif replacement == "missing-platform":
        blob = blob.replace("astrolift-managed-by=platform", "astrolift-managed-by=foreign")
    elif replacement == "missing-owner":
        blob = ";".join(entry for entry in blob.split(";") if not entry.startswith("astrolift-managed-service-id="))
    elif replacement == "unlabelled":
        blob = ""
    elif replacement == "duplicate":
        blob += f";astrolift-managed-service-id={OWNER}"
    elif replacement == "alias":
        blob += ";astrolift_managed_service_id=foreign"
    elif replacement == "different-arm":
        api.replaced_ids[path] = path.replace("controlled-rg", "foreign-rg")
    else:
        api.replaced_ids[path] = ""
    row["properties"]["userMetadata"] = blob
    before = copy.deepcopy(api.rows)
    for result in [
        driver.provision(spec(recorded_handle=handle)),
        driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"max_size_in_megabytes": 2048})),
        driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True),
    ]:
        assert not result.ok and set(result.errors) & {"ownership_unknown", "ownership_refused"}, result
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "error"
    with pytest.raises((RuntimeError, AzureOwnershipError)):
        driver.binding(ServiceHandle(handle, managed_service_id=OWNER))
    assert all(verb == "GET" for verb, *_ in api.calls) and api.rows == before


@pytest.mark.parametrize("kind", ["topic", "child", "inventory"])
@pytest.mark.parametrize("status", [401, 403, 409, 500])
def test_typed_denial_diagnostic_never_means_absence_or_forced_cleanup(runtime, kind, status):
    api, driver, handle, _ = owned(runtime)
    api.failures[("GET", kind)] = status, "ForbiddenResourceNotFound"
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["ownership_unknown"] and len(api.rows) == 2
    assert all(verb == "GET" for verb, *_ in api.calls)


@pytest.mark.parametrize("kind", ["topic", "child"])
def test_delete_denial_cannot_force_success(runtime, kind):
    api, driver, handle, target = owned(runtime)
    api.failures[("DELETE", kind)] = 403, "ForbiddenResourceNotFound"
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert target.topic_id in api.rows
    assert target.child_id in api.rows if kind == "child" else target.child_id not in api.rows


def test_typed_both_missing_converges_and_recorded_missing_never_recreates(runtime):
    api, driver, handle, _ = owned(runtime)
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True)
    assert result.ok, result
    assert not api.rows
    api.calls.clear()
    assert driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True).ok
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "deprovisioned"
    assert not driver.provision(spec(recorded_handle=handle)).ok
    assert all(verb == "GET" for verb, *_ in api.calls)


@pytest.mark.parametrize("force", [False, True])
def test_retention_never_delete_seek_or_fake_snapshot(runtime, force):
    api, driver, handle, _ = owned(runtime)
    before = copy.deepcopy(api.rows)
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), force_destroy=force)
    assert not result.ok and result.errors == ["delete_data_required"] and not result.retryable
    assert api.rows == before and all(verb == "GET" for verb, *_ in api.calls)


@pytest.mark.parametrize("state", ["Disabled", "SendDisabled", "ReceiveDisabled", "Unknown", "unrecognized"])
def test_real_sdk_enum_status_is_not_false_readiness_or_unguarded_force(runtime, state):
    api, driver, handle, target = owned(runtime)
    api.rows[target.topic_id]["properties"]["status"] = state
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "error"
    assert not driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True).ok
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert result.ok == (state in {"Disabled", "SendDisabled", "ReceiveDisabled"})


@pytest.mark.parametrize(
    "handle",
    [
        "queue/legacy",
        "topic/legacy",
        "queue/arm-v1/a/b/c/d/e",
        "queue/arm-v1/../b/c/d/e",
        "queue/arm-v1/" + SUBSCRIPTION + "/controlled-rg/controlled-sb/x?other=1/x-default",
    ],
)
def test_unknown_legacy_or_invalid_placement_is_not_rederived_or_adopted(runtime, handle):
    api, driver = runtime
    for result in [
        driver.provision(spec(recorded_handle=handle)),
        driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True),
        driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"max_size_in_megabytes": 2048})),
    ]:
        assert not result.ok and set(result.errors) & {"ownership_unknown", "ownership_refused"}
    assert driver.status(ServiceHandle(handle, managed_service_id=OWNER)).state == "error"
    with pytest.raises((RuntimeError, AzureOwnershipError)):
        driver.binding(ServiceHandle(handle, managed_service_id=OWNER))
    assert not api.calls


@pytest.mark.parametrize("field", ["subscription_id", "resource_group", "namespace_name"])
def test_reassigned_current_provider_coordinates_refuse_before_sdk(runtime, field):
    api, original, handle, _ = owned(runtime)
    values = {"subscription_id": OWNER, "resource_group": "foreign-rg", "namespace_name": "foreign-sb"}
    driver = AzureServiceBusDriver(config=dataclasses.replace(original._config, **{field: values[field]}))
    assert not driver.provision(spec(recorded_handle=handle)).ok
    assert not driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True).ok
    assert api.calls == []


def test_foreign_extra_child_blocks_parent_reconcile_update_and_delete(runtime):
    api, driver, handle, target = owned(runtime)
    extra = target.topic_id + "/subscriptions/foreign"
    api.rows[extra] = copy.deepcopy(api.rows[target.child_id])
    api.rows[extra]["id"] = extra
    for result in [
        driver.provision(spec(recorded_handle=handle)),
        driver.update(UpdateSpec(handle, managed_service_id=OWNER, config={"max_size_in_megabytes": 2048})),
        driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True),
    ]:
        assert not result.ok and "ownership_refused" in result.errors
    assert all(verb == "GET" for verb, *_ in api.calls) and len(api.rows) == 3


@pytest.mark.parametrize("failure", ["denied-second-page", "overflow", "foreign-link", "repeated-link"])
def test_partial_unknown_or_unbounded_inventory_has_no_effects(runtime, failure):
    api, driver, handle, target = owned(runtime)
    base = "https://management.azure.com" + target.topic_id + "/subscriptions?api-version=2026-01-01&$skip="
    if failure == "denied-second-page":
        pages = [{"value": [api.rows[target.child_id]], "nextLink": base + "1"}, (403, "ForbiddenResourceNotFound")]
    elif failure == "overflow":
        pages = [{"value": [], "nextLink": base + str(i + 1)} for i in range(5)]
    elif failure == "foreign-link":
        pages = [{"value": [], "nextLink": "https://foreign.invalid/elsewhere"}]
    else:
        pages = [{"value": [], "nextLink": base + "1"}, {"value": [], "nextLink": base + "1"}]
    api.inventory_pages = pages
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True)
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert all(verb == "GET" for verb, *_ in api.calls) and len(api.rows) == 2
    assert len([path for _, path, *_ in api.calls if path.endswith("/subscriptions")]) <= 4


def test_valid_second_inventory_page_is_collected_before_effects(runtime):
    api, driver, handle, target = owned(runtime)
    base = "https://management.azure.com" + target.topic_id + "/subscriptions?api-version=2026-01-01&$skip=1"
    api.inventory_pages = [{"value": [], "nextLink": base}, {"value": [api.rows[target.child_id]]}]
    result = driver.deprovision(DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True)
    assert result.ok, result
    verbs = [verb for verb, *_ in api.calls]
    assert verbs[:4] == ["GET"] * 4 and verbs[4:6] == ["DELETE"] * 2


def test_unrecorded_retry_is_identity_stable_and_does_not_adopt_unlabelled_child(runtime):
    api, driver, handle, target = owned(runtime)
    assert driver.provision(spec()).handle == handle
    api.rows[target.child_id]["properties"]["userMetadata"] = ""
    api.calls.clear()
    result = driver.provision(spec())
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert all(verb == "GET" for verb, *_ in api.calls)


@pytest.mark.parametrize("change", ["unchanged", "partitioning", "delivery", "lock", "dead-letter"])
def test_whole_desired_config_respects_unchanged_snapshot_but_never_silently_applies_noneditable_changes(
    runtime, change
):
    api, driver, handle, target = owned(runtime)
    cfg = {
        "max_size_in_megabytes": 2048,
        "default_message_ttl": "P14D",
        "enable_partitioning": False,
        "max_delivery_count": 10,
        "lock_duration": "PT30S",
        "dead_lettering_on_message_expiration": True,
    }
    if change == "partitioning":
        cfg["enable_partitioning"] = True
    elif change == "delivery":
        cfg["max_delivery_count"] = 3
    elif change == "lock":
        cfg["lock_duration"] = "PT1M"
    elif change == "dead-letter":
        cfg["dead_lettering_on_message_expiration"] = False
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config=cfg))
    if change == "unchanged":
        assert result.ok and api.rows[target.topic_id]["properties"]["maxSizeInMegabytes"] == 2048
    else:
        assert not result.ok and not result.retryable and result.errors == [UPDATE_NOT_SUPPORTED_IN_PLACE]
        assert all(verb == "GET" for verb, *_ in api.calls)


def test_sdk_bodies_are_bounded_to_real_typed_properties_for_parent_and_child(runtime):
    api, driver = runtime
    result = driver.provision(spec())
    assert result.ok
    puts = [(path, body) for verb, path, body, _ in api.calls if verb == "PUT"]
    assert len(puts) == 2
    assert set(puts[0][1]["properties"]) == {
        "maxSizeInMegabytes",
        "enablePartitioning",
        "supportOrdering",
        "defaultMessageTimeToLive",
        "userMetadata",
    }
    assert set(puts[1][1]["properties"]) == {
        "lockDuration",
        "maxDeliveryCount",
        "deadLetteringOnMessageExpiration",
        "defaultMessageTimeToLive",
        "userMetadata",
    }


def test_lookalike_exception_name_and_diagnostic_are_not_typed_absence(runtime, monkeypatch):
    api, driver, handle, _ = owned(runtime)

    class Impostor(Exception):
        pass

    Impostor.__name__ = "ResourceNotFoundError"

    def denied(**kwargs):
        raise Impostor("ResourceNotFound secret-bearing provider diagnostic")

    monkeypatch.setattr(driver._client.topics, "get", denied)
    outcome = driver.deprovision(
        DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    )
    assert not outcome.ok and outcome.errors == ["ownership_unknown"]
    assert "secret-bearing" not in outcome.message and api.calls == [] and len(api.rows) == 2


def test_expired_budget_stops_between_lookup_and_parent_write(runtime, monkeypatch):
    from types import SimpleNamespace

    api, driver = runtime
    monkeypatch.setattr(
        "azure.managed.queue_servicebus.time", SimpleNamespace(monotonic=iter([0.0, 1.0, 2.0, 21.0]).__next__)
    )
    outcome = driver.provision(spec())
    assert not outcome.ok and outcome.errors == ["ownership_unknown"]
    assert [verb for verb, *_ in api.calls] == ["GET", "GET"] and not api.rows


def test_inventory_identity_is_required_before_sender_binding(runtime):
    api, driver, handle, target = owned(runtime)
    extra = target.topic_id + "/subscriptions/foreign"
    api.rows[extra] = copy.deepcopy(api.rows[target.child_id])
    api.rows[extra]["id"] = extra
    with pytest.raises(AzureOwnershipError):
        driver.binding(ServiceHandle(handle, managed_service_id=OWNER))
    assert all(verb == "GET" for verb, *_ in api.calls)


def test_unicode_resource_groups_cannot_be_collapsed_into_another_arm_target(runtime):
    api, original = runtime
    driver = AzureServiceBusDriver(config=dataclasses.replace(original._config, resource_group="Straße-RG"))
    result = driver.provision(spec())
    assert result.ok, result
    target = driver._saved_target(result.handle, spec())
    api.replaced_ids[target.topic_id] = target.topic_id.replace("Straße-RG", "Strasse-RG")
    api.calls.clear()
    outcome = driver.deprovision(
        DeprovisionSpec(result.handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    )
    assert not outcome.ok and "ownership_refused" in outcome.errors
    assert all(verb == "GET" for verb, *_ in api.calls)
    driver = AzureServiceBusDriver(config=dataclasses.replace(original._config, resource_group="Strasse-RG"))
    api.calls.clear()
    assert not driver.provision(spec(recorded_handle=result.handle)).ok and api.calls == []


@pytest.mark.parametrize("field", ["enable_partitioning", "max_delivery_count", "dead_lettering_on_message_expiration"])
def test_full_snapshot_type_coercion_cannot_report_an_invalid_noneditable_value_applied(runtime, field):
    api, driver, handle, _ = owned(runtime)
    values = {"enable_partitioning": 0, "max_delivery_count": 10.0, "dead_lettering_on_message_expiration": 1}
    cfg = {"max_size_in_megabytes": 2048, field: values[field]}
    result = driver.update(UpdateSpec(handle, managed_service_id=OWNER, config=cfg))
    assert not result.ok and "ownership_refused" in result.errors
    assert all(verb == "GET" for verb, *_ in api.calls)


def test_complete_handle_can_fit_while_topic_arm_binding_is_unrepresentable_and_must_refuse_before_sdk():
    api = RecordingTopics()
    cfg = dataclasses.replace(recording_config(api, kind="topic"), resource_group="r" * 90, namespace_name="n" * 50)
    driver = AzureServiceBusDriver(config=cfg)
    topic, child = "t" * 260, "c" * 50
    handle = "/".join(("topic", "arm-v1", SUBSCRIPTION, cfg.resource_group, cfg.namespace_name, topic, child))
    assert len(handle) <= 512
    arm_id = (
        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{cfg.resource_group}"
        f"/providers/Microsoft.ServiceBus/namespaces/{cfg.namespace_name}/topics/{topic}"
    )
    assert len(arm_id) > 512
    try:
        result = driver.provision(spec(recorded_handle=handle))
        assert not result.ok and "ownership_refused" in result.errors
        with pytest.raises(AzureOwnershipError, match="binding storage limit"):
            driver.binding(ServiceHandle(handle, managed_service_id=OWNER))
        assert api.calls == []
    finally:
        cfg.client.close()
