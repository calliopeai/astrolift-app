"""Actual declared Azure SDK serialization/typed errors over a recording HTTP transport.

No Azure endpoints, credentials or delivery/retention service are contacted.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from _sdk.azure_ownership import AzureOwnershipError
from _sdk.managed_service import (
    UPDATE_NOT_SUPPORTED_IN_PLACE,
    DeprovisionSpec,
    ProvisionSpec,
    ServiceHandle,
    UpdateSpec,
)
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.managed.queue_servicebus import ServiceBusConfig, ServiceBusDriver
from azure.mgmt.servicebus import ServiceBusManagementClient

OWNER = "018f42f0-4420-7000-8000-000000000001"
SUBSCRIPTION = "018f42f0-4420-7000-8000-000000000002"


class _Credential:
    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken("controlled-recording-transport", int(time.time()) + 3600)


class _Response(HttpResponse):
    def __init__(self, request: Any, status: int, payload: dict[str, Any]):
        super().__init__(request, None)
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.content_type = "application/json"
        self._body = json.dumps(payload).encode()
        self.reason = "controlled response"

    def body(self) -> bytes:
        return self._body

    def json(self) -> dict[str, Any]:
        return json.loads(self._body)


class RecordingServiceBus(HttpTransport):
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        self.failures: dict[str, tuple[int, str]] = {}
        self.replaced_id: str | None = None
        self.closed = False
        self.payloads: dict[str, list[bytes]] = {}

    def open(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> RecordingServiceBus:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def send(self, request: Any, **kwargs: Any) -> HttpResponse:
        parsed = urlsplit(request.url)
        assert parsed.hostname == "management.azure.com"
        assert parse_qs(parsed.query)["api-version"] == ["2026-01-01"]
        path = unquote(parsed.path)
        assert "/providers/Microsoft.ServiceBus/namespaces/controlled-sb/queues/" in path
        payload = json.loads(request.body) if request.body else {}
        self.calls.append((request.method, path, payload, kwargs))
        if request.method in self.failures:
            status, code = self.failures[request.method]
            return _Response(
                request, status, {"error": {"code": code, "message": "access refused: resource not found"}}
            )
        if request.method == "GET":
            row = self.rows.get(path)
            if row is None:
                return _Response(request, 404, {"error": {"code": "ResourceNotFound", "message": "queue absent"}})
            row = copy.deepcopy(row)
            if self.replaced_id is not None:
                row["id"] = self.replaced_id
            return _Response(request, 200, row)
        if request.method == "PUT":
            assert set(payload) == {"properties"}, payload
            props = payload["properties"]
            assert "userMetadata" in props and "maxSizeInMegabytes" in props
            assert "user_metadata" not in props and "max_size_in_megabytes" not in props
            props["status"] = "Active"
            row = {"id": path, "name": path.partition("/queues/")[2], "properties": props}
            self.rows[path] = row
            return _Response(request, 200, row)
        if request.method == "DELETE":
            if path not in self.rows:
                return _Response(request, 404, {"error": {"code": "ResourceNotFound", "message": "queue absent"}})
            del self.rows[path]
            self.payloads.pop(path, None)
            return _Response(request, 200, {})
        raise AssertionError(request.method)


def recording_client(api: RecordingServiceBus) -> ServiceBusManagementClient:
    return ServiceBusManagementClient(_Credential(), SUBSCRIPTION, transport=api, retry_total=0)


def recording_config(api: RecordingServiceBus) -> ServiceBusConfig:
    return ServiceBusConfig(
        subscription_id=SUBSCRIPTION,
        resource_group="controlled-rg",
        namespace_name="controlled-sb",
        client=recording_client(api),
    )


def spec(**kwargs: Any) -> ProvisionSpec:
    fields: dict[str, Any] = dict(
        organization_id=OWNER,
        organization_slug="alpha",
        app_id=OWNER,
        app_slug="api",
        environment_id=OWNER,
        environment_name="prod",
        tenant_cluster_id=OWNER,
        service_handle_hint="tasks",
        size="small",
        managed_service_id=OWNER,
    )
    fields.update(kwargs)
    return ProvisionSpec(**fields)


@pytest.fixture
def runtime():
    api = RecordingServiceBus()
    cfg = recording_config(api)
    driver = ServiceBusDriver(config=cfg)
    yield api, driver
    cfg.client.close()
    assert api.closed


def test_actual_sdk_writes_properties_and_owner_and_guards_sender_receiver_binding(runtime):
    api, driver = runtime
    result = driver.provision(spec())
    assert result.ok, result
    row = next(iter(api.rows.values()))
    assert row["properties"]["lockDuration"] == "PT30S"
    assert row["properties"]["defaultMessageTimeToLive"] == "P14D"
    assert row["properties"]["userMetadata"].startswith("astrolift-managed-by=platform;")
    assert f"astrolift-managed-service-id={OWNER}" in row["properties"]["userMetadata"]
    handle = ServiceHandle(result.handle, managed_service_id=OWNER)
    assert driver.status(handle).state == "available"
    binding = driver.binding(handle)
    assert binding.env_vars["SERVICEBUS_QUEUE"].literal == result.handle.partition("/")[2]
    assert binding.iam_grants[0].resource == row["id"]
    assert binding.iam_grants[0].actions == ["Azure Service Bus Data Sender", "Azure Service Bus Data Receiver"]
    for _, _, _, options in api.calls:
        assert 0 < options["connection_timeout"] <= 5 and 0 < options["read_timeout"] <= 5


@pytest.mark.parametrize("identity", ["", "unknown", "0" * 32, OWNER.replace("-", ""), OWNER.upper()])
def test_invalid_or_noncanonical_identity_fails_before_any_sdk_request(runtime, identity):
    api, driver = runtime
    if identity == OWNER.upper():
        identity = "018F42F0-4420-7000-8000-000000000001"
    assert not driver.provision(spec(managed_service_id=identity)).ok
    assert api.calls == []


@pytest.mark.parametrize("hostile", ["delimiter", "oversize"])
def test_metadata_injection_and_incomplete_envelope_refuse_before_sdk(runtime, hostile):
    api, driver = runtime
    tags = {"note": "safe;astrolift-managed-service-id=foreign"}
    if hostile == "oversize":
        tags = {f"note{i}": "x" * 256 for i in range(8)}
    assert not driver.provision(spec(tags=tags)).ok
    assert api.calls == []


@pytest.mark.parametrize(
    "replacement", ["foreign", "missing-owner", "missing-platform", "duplicate", "foreign-arm", "missing-arm"]
)
def test_every_operational_path_refuses_replaced_or_unknown_live_resource_before_mutation(runtime, replacement):
    api, driver = runtime
    result = driver.provision(spec())
    assert result.ok
    row = next(iter(api.rows.values()))
    metadata = row["properties"]["userMetadata"]
    if replacement == "foreign":
        metadata = metadata.replace(OWNER, "018f42f0-4420-7000-8000-000000000099")
    elif replacement == "missing-owner":
        metadata = ";".join(
            part for part in metadata.split(";") if not part.startswith("astrolift-managed-service-id=")
        )
    elif replacement == "missing-platform":
        metadata = metadata.replace("astrolift-managed-by=platform", "astrolift-managed-by=other")
    elif replacement == "duplicate":
        metadata += f";astrolift-managed-service-id={OWNER}"
    elif replacement == "foreign-arm":
        api.replaced_id = row["id"].replace(SUBSCRIPTION, OWNER)
    else:
        api.replaced_id = ""
    row["properties"]["userMetadata"] = metadata
    api.calls.clear()
    assert not driver.provision(spec(recorded_handle=result.handle)).ok
    before_update = len(api.calls)
    update = driver.update(UpdateSpec(result.handle, managed_service_id=OWNER))
    assert not update.ok and not update.retryable and update.errors == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    assert len(api.calls) == before_update
    assert driver.status(ServiceHandle(result.handle, managed_service_id=OWNER)).state == "error"
    with pytest.raises((AzureOwnershipError, RuntimeError)):
        driver.binding(ServiceHandle(result.handle, managed_service_id=OWNER))
    for delete_data in (False, True):
        for force in (False, True):
            outcome = driver.deprovision(
                DeprovisionSpec(result.handle, managed_service_id=OWNER), delete_data=delete_data, force_destroy=force
            )
            assert not outcome.ok and set(outcome.errors) & {"ownership_refused", "ownership_unknown"}
    assert all(method == "GET" for method, *_ in api.calls)
    assert len(api.rows) == 1


@pytest.mark.parametrize("method", ["GET", "DELETE"])
@pytest.mark.parametrize("status", [401, 403, 409, 500])
def test_actual_sdk_denial_with_not_found_diagnostic_is_never_typed_absence_or_force_success(runtime, method, status):
    api, driver = runtime
    result = driver.provision(spec())
    assert result.ok
    api.calls.clear()
    api.failures[method] = (status, "UnauthorizedResourceNotFound")
    outcome = driver.deprovision(
        DeprovisionSpec(result.handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    )
    assert not outcome.ok and outcome.errors == ["ownership_unknown"]
    assert len(api.rows) == 1
    assert [verb for verb, *_ in api.calls] == (["GET"] if method == "GET" else ["GET", "DELETE"])


def test_actual_sdk_retained_refusal_and_concrete_missing_convergence(runtime):
    api, driver = runtime
    result = driver.provision(spec())
    assert result.ok
    api.calls.clear()
    handle = DeprovisionSpec(result.handle, managed_service_id=OWNER)
    for force in (False, True):
        outcome = driver.deprovision(handle, force_destroy=force)
        assert not outcome.ok and outcome.errors == ["delete_data_required"]
    assert all(method == "GET" for method, *_ in api.calls)
    assert driver.deprovision(handle, delete_data=True).ok and not api.rows
    api.calls.clear()
    assert driver.deprovision(handle, delete_data=True, force_destroy=True).ok
    assert [method for method, *_ in api.calls] == ["GET"]


def test_reserved_spelling_is_namespaced_and_does_not_override_platform_metadata(runtime):
    api, driver = runtime
    result = driver.provision(spec(tags={"astrolift-managed-service-id": "foreign", "formula": "a=b"}))
    assert result.ok, result
    metadata = next(iter(api.rows.values()))["properties"]["userMetadata"]
    assert f"astrolift-managed-service-id={OWNER}" in metadata
    assert "astrolift-extra-astrolift-managed-service-id-" in metadata
    assert driver.status(ServiceHandle(result.handle, managed_service_id=OWNER)).state == "available"


@pytest.mark.parametrize(
    "handle",
    [
        "topic/owned",
        "queue/",
        "queue/a/../b",
        "queue//b",
        "queue/a?other=b",
        "queue/https://foreign",
        "queue/" + "a" * 261,
    ],
)
def test_invalid_recorded_targets_never_rederive_names_or_issue_requests(runtime, handle):
    api, driver = runtime
    assert not driver.provision(spec(recorded_handle=handle)).ok
    for force in (False, True):
        assert not driver.deprovision(
            DeprovisionSpec(handle, managed_service_id=OWNER), delete_data=True, force_destroy=force
        ).ok
    assert api.calls == []


def test_exception_named_like_sdk_not_found_is_still_unknown(runtime, monkeypatch):
    api, driver = runtime
    result = driver.provision(spec())
    assert result.ok

    class Impostor(Exception):
        pass

    Impostor.__name__ = "ResourceNotFoundError"

    def denied(**kwargs):
        raise Impostor("resource not found")

    monkeypatch.setattr(driver._client.queues, "get", denied)
    api.calls.clear()
    result = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id=OWNER), delete_data=True)
    assert not result.ok and result.errors == ["ownership_unknown"] and api.calls == []


def test_budget_exhaustion_after_lookup_refuses_before_put(runtime, monkeypatch):
    api, driver = runtime
    monkeypatch.setattr(
        "azure.managed.queue_servicebus.time", SimpleNamespace(monotonic=iter([0.0, 1.0, 21.0]).__next__)
    )
    result = driver.provision(spec())
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert [method for method, *_ in api.calls] == ["GET"] and api.rows == {}


def test_typed_delete_missing_converges_but_no_diagnostic_text_can_do_so(runtime):
    api, driver = runtime
    result = driver.provision(spec())
    assert result.ok
    api.failures["DELETE"] = (404, "ResourceNotFound")
    outcome = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id=OWNER), delete_data=True)
    assert outcome.ok


def test_malformed_sdk_payload_is_unknown_without_echoing_provider_diagnostics(runtime, monkeypatch):
    api, driver = runtime
    result = driver.provision(spec())
    assert result.ok

    def malformed(**kwargs):
        raise ValueError("malformed upstream secret-bearing diagnostic: resource not found")

    monkeypatch.setattr(driver._client.queues, "get", malformed)
    api.calls.clear()
    outcome = driver.deprovision(DeprovisionSpec(result.handle, managed_service_id=OWNER), delete_data=True)
    assert not outcome.ok and outcome.errors == ["ownership_unknown"] and outcome.retryable
    assert "secret-bearing" not in outcome.message and api.calls == []


def test_valid_arm_subscription_casing_and_unicode_resource_group_are_preserved(runtime):
    api, original = runtime
    driver = ServiceBusDriver(
        config=dataclasses.replace(
            original._config, subscription_id=SUBSCRIPTION.upper(), resource_group="Région-(East)"
        )
    )
    outcome = driver.provision(spec())
    assert outcome.ok, outcome
    row = next(iter(api.rows.values()))
    assert "/resourceGroups/Région-(East)/" in row["id"]
    assert driver.status(ServiceHandle(outcome.handle, managed_service_id=OWNER)).state == "available"


def test_actual_sdk_cannot_follow_management_redirect_to_another_host(runtime, monkeypatch):
    api, driver = runtime
    outcome = driver.provision(spec())
    assert outcome.ok
    calls = []

    def redirect(request, **kwargs):
        calls.append(request.url)
        assert urlsplit(request.url).hostname == "management.azure.com"
        response = _Response(request, 302, {})
        response.headers["Location"] = "https://other.invalid/foreign"
        return response

    monkeypatch.setattr(api, "send", redirect)
    result = driver.deprovision(DeprovisionSpec(outcome.handle, managed_service_id=OWNER), delete_data=True)
    assert not result.ok and result.errors == ["ownership_unknown"] and len(calls) == 1
    assert len(api.rows) == 1


@pytest.mark.parametrize("source", ["foreign", "unavailable", "unknown", "invalid-handle"])
def test_permanently_unsupported_update_does_not_read_or_mutate_any_source(runtime, source):
    api, driver = runtime
    outcome = driver.provision(spec())
    assert outcome.ok
    handle = outcome.handle
    identity = OWNER
    if source == "foreign":
        row = next(iter(api.rows.values()))
        row["properties"]["userMetadata"] = row["properties"]["userMetadata"].replace(OWNER, "foreign")
    elif source == "unavailable":
        api.failures["GET"] = (403, "UnavailableResourceNotFound")
    elif source == "unknown":
        identity = ""
    else:
        handle = "probe/handle"
    api.calls.clear()
    before = copy.deepcopy(api.rows)
    result = driver.update(UpdateSpec(handle, managed_service_id=identity, config={"max_size_in_megabytes": 2048}))
    assert not result.ok and not result.retryable and result.errors == [UPDATE_NOT_SUPPORTED_IN_PLACE]
    assert result.handle == handle and api.calls == [] and api.rows == before
