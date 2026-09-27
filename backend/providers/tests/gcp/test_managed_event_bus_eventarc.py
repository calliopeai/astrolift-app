from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.event_bus_eventarc import (
    EventarcConfig,
    EventarcConflict,
    EventarcDriver,
    EventarcError,
    EventarcNotFound,
    EventarcRestClient,
)

SPEC = ProvisionSpec(
    organization_id="org-id",
    organization_slug="acme",
    app_id="app-id",
    app_slug="events",
    environment_id="env-id",
    environment_name="production",
    tenant_cluster_id="cluster-id",
    service_handle_hint="shared",
    size="small",
    binding_id="binding-id",
    managed_service_id="managed-id",
)
MSID = SPEC.managed_service_id


class FakeEventarc:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[Any, ...]] = []
        self._operation = 0

    def get(self, name: str) -> dict[str, Any]:
        self.calls.append(("get", name))
        if name not in self.resources:
            raise EventarcNotFound(name)
        return deepcopy(self.resources[name])

    def list_resources(self, parent: str, collection: str) -> list[dict[str, Any]]:
        self.calls.append(("list", parent, collection))
        prefix = f"{parent}/{collection}/"
        return [deepcopy(row) for name, row in self.resources.items() if name.startswith(prefix)]

    def list_bus_enrollments(self, bus_name: str) -> list[str]:
        self.calls.append(("list_bus_enrollments", bus_name))
        return [
            name
            for name, row in self.resources.items()
            if "/enrollments/" in name and row.get("messageBus") == bus_name
        ]

    def create(
        self,
        parent: str,
        collection: str,
        resource_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        name = f"{parent}/{collection}/{resource_id}"
        self.calls.append(("create", name, deepcopy(body)))
        if name in self.resources:
            raise EventarcConflict(name)
        stored = deepcopy(body)
        if collection == "channelConnections":
            stored.pop("activationToken", None)
        self.resources[name] = {"name": name, "etag": f"etag-{resource_id}", **stored}
        return self._done(self.resources[name])

    def patch(
        self,
        name: str,
        body: dict[str, Any],
        *,
        update_mask: list[str],
    ) -> dict[str, Any]:
        self.calls.append(("patch", name, deepcopy(body), list(update_mask)))
        if name not in self.resources:
            raise EventarcNotFound(name)
        self.resources[name].update(deepcopy(body))
        self.resources[name]["etag"] = f"etag-{self._operation + 1}"
        return self._done(self.resources[name])

    def delete(self, name: str, *, etag: str = "") -> dict[str, Any]:
        self.calls.append(("delete", name, etag))
        self.resources.pop(name, None)
        return self._done({})

    def get_operation(self, name: str) -> dict[str, Any]:
        return deepcopy(self.operations[name])

    def publish(self, bus_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("publish", bus_name, deepcopy(payload)))
        return {}

    def _done(self, response: dict[str, Any]) -> dict[str, Any]:
        self._operation += 1
        operation = {
            "name": f"projects/p/locations/l/operations/{self._operation}",
            "done": True,
            "response": deepcopy(response),
        }
        self.operations[operation["name"]] = operation
        return operation


@pytest.fixture
def config() -> EventarcConfig:
    return EventarcConfig(
        project_id="project-1",
        location="us-central1",
        operation_timeout_seconds=1,
        poll_interval_seconds=0,
    )


@pytest.fixture
def client() -> FakeEventarc:
    return FakeEventarc()


@pytest.fixture
def driver(config: EventarcConfig, client: FakeEventarc) -> EventarcDriver:
    return EventarcDriver(config=config, client=client, sleep=lambda _: None)


def _provision(driver: EventarcDriver, **config: Any):
    return driver.provision(replace(SPEC, config=config))


def _full_config() -> dict[str, Any]:
    return {
        "message_bus_id": "astrolift",
        "display_name": "Shared event fabric",
        "crypto_key_name": "projects/p/locations/us/keyRings/r/cryptoKeys/k",
        "logging_config": {"log_severity": "INFO"},
        "pipelines": [
            {
                "id": "to-run",
                "destinations": [
                    {
                        "http_endpoint": {
                            "uri": "https://receiver.example.test/events",
                            "message_binding_template": '{"body": body}',
                        },
                        "authentication_config": {
                            "google_oidc": {
                                "service_account": "eventarc@project-1.iam.gserviceaccount.com",
                                "audience": "https://receiver.example.test",
                            },
                        },
                        "network_config": {
                            "network_attachment": ("projects/project-1/regions/us-central1/networkAttachments/events"),
                        },
                        "output_payload_format": {"json": {}},
                    },
                ],
                "input_payload_format": {"json": {}},
                "mediations": [
                    {"transformation": {"transformation_template": "merge(message, {'seen': true})"}},
                ],
                "retry_policy": {
                    "max_attempts": 20,
                    "min_retry_delay": "5s",
                    "max_retry_delay": "60s",
                },
                "logging_config": {"log_severity": "WARNING"},
            },
        ],
        "enrollments": [
            {
                "id": "all-app-events",
                "cel_match": "message.type.startsWith('com.acme.')",
                "destination_pipeline": "to-run",
            },
        ],
        "google_api_sources": [
            {
                "id": "google-events",
                "project_subscriptions": ["source-project-1", "source-project-2"],
                "logging_config": {"log_severity": "ERROR"},
            },
        ],
        "triggers": [
            {
                "id": "storage-finalized",
                "event_filters": [
                    {"attribute": "type", "value": "google.cloud.storage.object.v1.finalized"},
                    {"attribute": "bucket", "value": "uploads"},
                ],
                "destination": {
                    "cloud_run": {"service": "receiver", "region": "us-central1", "path": "/event"},
                },
                "service_account": "eventarc@project-1.iam.gserviceaccount.com",
            },
        ],
        "channels": [
            {
                "id": "partner-events",
                "provider": "partner-provider",
                "crypto_key_name": "projects/p/locations/us/keyRings/r/cryptoKeys/k",
            },
        ],
    }


def test_provision_composes_advanced_and_standard_eventarc_resources(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    assert result.ok and result.ready
    assert result.handle == "event_bus/us-central1/astrolift"

    parent = "projects/project-1/locations/us-central1"
    bus_name = f"{parent}/messageBuses/astrolift"
    bus = client.resources[bus_name]
    assert bus["displayName"] == "Shared event fabric"
    assert bus["loggingConfig"] == {"logSeverity": "INFO"}
    assert bus["labels"]["astrolift-io-managed-service-id"] == "managed-id"
    assert bus["labels"]["astrolift-io-resource-parent"] == "astrolift"

    pipeline = client.resources[f"{parent}/pipelines/to-run"]
    destination = pipeline["destinations"][0]
    assert destination["httpEndpoint"]["messageBindingTemplate"] == '{"body": body}'
    assert destination["authenticationConfig"]["googleOidc"]["audience"].startswith("https://")
    assert destination["networkConfig"]["networkAttachment"].endswith("/events")
    assert pipeline["inputPayloadFormat"] == {"json": {}}
    assert pipeline["retryPolicy"]["maxAttempts"] == 20

    enrollment = client.resources[f"{parent}/enrollments/all-app-events"]
    assert enrollment["messageBus"] == bus_name
    assert enrollment["destination"] == f"{parent}/pipelines/to-run"
    source = client.resources[f"{parent}/googleApiSources/google-events"]
    assert source["destination"] == bus_name
    assert source["projectSubscriptions"] == {"list": ["source-project-1", "source-project-2"]}
    trigger = client.resources[f"{parent}/triggers/storage-finalized"]
    assert trigger["destination"]["cloudRun"]["service"] == "receiver"
    channel = client.resources[f"{parent}/channels/partner-events"]
    assert channel["provider"] == f"{parent}/providers/partner-provider"

    binding = driver.binding(ServiceHandle(result.handle, managed_service_id=MSID), {"access_mode": "manage"})
    assert binding.env_vars["EVENT_BUS_NAME"].literal == bus_name
    assert binding.env_vars["EVENT_BUS_PUBLISH_URL"].literal == (
        f"https://eventarcpublishing.googleapis.com/v1/{bus_name}:publish"
    )
    assert binding.iam_grants[0].actions == ["roles/eventarc.messageBusAdmin"]
    assert binding.iam_grants[1].actions == ["roles/eventarc.developer"]


def test_repeated_provision_is_idempotent(driver: EventarcDriver, client: FakeEventarc) -> None:
    first = driver.provision(replace(SPEC, config=_full_config()))
    creates = len([call for call in client.calls if call[0] == "create"])
    second = driver.provision(replace(SPEC, config=_full_config()))
    assert first.ok and second.ok
    assert len([call for call in client.calls if call[0] == "create"]) == creates
    assert not [call for call in client.calls if call[0] == "patch"]


def test_update_is_partial_and_prunes_only_declared_managed_children(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    parent = "projects/project-1/locations/us-central1"
    external_name = f"{parent}/pipelines/external"
    client.resources[external_name] = {
        "name": external_name,
        "labels": {"owner": "customer"},
        "destinations": [{"topic": "projects/p/topics/external"}],
    }
    updated = driver.update(
        UpdateSpec(
            result.handle,
            managed_service_id=MSID,
            config={
                "display_name": "Renamed",
                "pipelines": [],
            },
        ),
    )
    assert updated.ok
    assert client.resources[f"{parent}/messageBuses/astrolift"]["displayName"] == "Renamed"
    assert f"{parent}/pipelines/to-run" not in client.resources
    assert external_name in client.resources
    bus_patch = next(call for call in client.calls if call[0] == "patch" and "/messageBuses/" in call[1])
    assert bus_patch[3] == ["displayName"]


def test_missing_child_list_does_not_prune(driver: EventarcDriver, client: FakeEventarc) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    updated = driver.update(UpdateSpec(result.handle, managed_service_id=MSID, config={"display_name": "Only bus"}))
    assert updated.ok
    assert any("/pipelines/to-run" in name for name in client.resources)


def test_immutable_trigger_type_requires_replacement(driver: EventarcDriver) -> None:
    result = _provision(
        driver,
        triggers=[
            {
                "id": "route",
                "event_filters": [{"attribute": "type", "value": "com.acme.created"}],
                "destination": {"workflow": "projects/p/locations/l/workflows/created"},
            },
        ],
    )
    assert result.ok
    updated = driver.update(
        UpdateSpec(
            result.handle,
            managed_service_id=MSID,
            config={
                "triggers": [
                    {
                        "id": "route",
                        "event_filters": [{"attribute": "type", "value": "com.acme.deleted"}],
                        "destination": {"workflow": "projects/p/locations/l/workflows/deleted"},
                    },
                ],
            },
        ),
    )
    assert not updated.ok and "event type is immutable" in updated.message


def test_pruning_does_not_cross_managed_service_ownership(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    result = _provision(driver, pipelines=[])
    parent = "projects/project-1/locations/us-central1"
    other = f"{parent}/pipelines/other-service"
    client.resources[other] = {
        "name": other,
        "labels": {
            "astrolift-io-managed-by": "platform",
            "astrolift-io-resource-parent": "astrolift",
            "astrolift-io-managed-service-id": "different-service",
        },
        "destinations": [{"topic": "projects/p/topics/external"}],
    }
    updated = driver.update(UpdateSpec(result.handle, managed_service_id=MSID, config={"pipelines": []}))
    assert updated.ok and other in client.resources


def test_raw_fields_cannot_override_ownership_labels(driver: EventarcDriver) -> None:
    result = _provision(
        driver,
        raw_fields={"labels": {"astrolift-io-managed-by": "attacker"}},
    )
    assert not result.ok and "output-only" in result.message


def test_clear_fields_explicitly_removes_mutable_provider_values(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    result = _provision(driver, display_name="Named")
    updated = driver.update(
        UpdateSpec(result.handle, managed_service_id=MSID, config={"clear_fields": ["displayName"]}),
    )
    assert updated.ok
    bus = client.resources["projects/project-1/locations/us-central1/messageBuses/astrolift"]
    assert bus["displayName"] is None


def test_existing_external_bus_is_refused_without_operator_adoption(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    name = "projects/project-1/locations/us-central1/messageBuses/astrolift"
    client.resources[name] = {"name": name, "labels": {"owner": "customer"}}
    denied = _provision(driver)
    assert not denied.ok
    assert "operator-authorized" in denied.message
    assert "one bus per project and region" in denied.message

    # The flag is gone entirely, on the bus and on every child declaration:
    # the schema tenant config is validated against rejects it, and a driver
    # handed one anyway still refuses (#2021).
    validator = Draft202012Validator(driver.config_schema())
    assert validator.is_valid(_full_config())
    assert not validator.is_valid({**_full_config(), "adopt_existing": True})
    child = deepcopy(_full_config())
    child["triggers"][0]["adopt_existing"] = True
    assert not validator.is_valid(child)
    still_denied = _provision(driver, adopt_existing=True)
    assert not still_denied.ok
    assert client.resources[name]["labels"] == {"owner": "customer"}


def test_existing_managed_bus_cannot_be_reassigned_by_config(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    name = "projects/project-1/locations/us-central1/messageBuses/astrolift"
    labels = {
        "astrolift-io-managed-by": "platform",
        "astrolift-io-managed-service-id": "another-service",
    }
    client.resources[name] = {"name": name, "labels": dict(labels)}
    denied = _provision(driver)
    assert not denied.ok and "another managed service" in denied.message

    validator = Draft202012Validator(driver.config_schema())
    assert not validator.is_valid({**_full_config(), "reassign_existing": True})
    still_denied = _provision(driver, reassign_existing=True)
    assert not still_denied.ok
    assert client.resources[name]["labels"] == labels


def test_existing_external_child_is_refused_without_operator_adoption(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    name = "projects/project-1/locations/us-central1/triggers/storage-finalized"
    client.resources[name] = {"name": name, "labels": {"owner": "customer"}}
    denied = driver.provision(replace(SPEC, config=_full_config()))
    assert not denied.ok
    assert "operator-authorized" in denied.message
    assert client.resources[name]["labels"] == {"owner": "customer"}


def test_deprovision_requires_protection_override_and_removes_children_first(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    blocked = driver.deprovision(DeprovisionSpec(result.handle, _full_config(), managed_service_id=MSID))
    assert not blocked.ok and not blocked.retryable

    deleted = driver.deprovision(
        DeprovisionSpec(result.handle, _full_config(), managed_service_id=MSID),
        force_destroy=True,
    )
    assert deleted.ok
    deletes = [call[1] for call in client.calls if call[0] == "delete"]
    assert deletes[-1].endswith("/messageBuses/astrolift")
    assert not client.resources


def test_external_enrollment_requires_double_confirmation(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    result = _provision(driver)
    parent = "projects/project-1/locations/us-central1"
    bus_name = f"{parent}/messageBuses/astrolift"
    external_name = f"{parent}/enrollments/customer"
    client.resources[external_name] = {
        "name": external_name,
        "messageBus": bus_name,
        "destination": f"{parent}/pipelines/customer",
        "labels": {"owner": "customer"},
    }
    source_name = f"{parent}/googleApiSources/customer-source"
    client.resources[source_name] = {
        "name": source_name,
        "destination": bus_name,
        "labels": {"owner": "customer"},
    }
    pipeline_name = f"{parent}/pipelines/customer-pipeline"
    client.resources[pipeline_name] = {
        "name": pipeline_name,
        "destinations": [{"messageBus": bus_name}],
        "labels": {"owner": "customer"},
    }
    blocked = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}, managed_service_id=MSID),
        force_destroy=True,
    )
    assert not blocked.ok and blocked.errors == ["external_dependents_present"]
    deleted = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            {"deletion_protection": False, "delete_external_dependents": True},
            managed_service_id=MSID,
        ),
        force_destroy=True,
    )
    assert deleted.ok
    assert all(name not in client.resources for name in (external_name, source_name, pipeline_name))


def test_adopted_bus_requires_separate_deletion_consent(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    name = "projects/project-1/locations/us-central1/messageBuses/astrolift"
    result = _provision(driver, deletion_protection=False)
    assert result.ok
    # A bus adopted before #2074 still carries the marker, and teardown still
    # asks for the second acknowledgement.
    client.resources[name]["labels"]["astrolift-io-adopted"] = "true"
    denied = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}, managed_service_id=MSID),
        force_destroy=True,
    )
    assert not denied.ok and denied.errors == ["adopted_resource_guard"]
    accepted = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            {"deletion_protection": False, "delete_adopted": True},
            managed_service_id=MSID,
        ),
        force_destroy=True,
    )
    assert accepted.ok


def test_status_reports_child_condition_failure(driver: EventarcDriver, client: FakeEventarc) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    trigger = next(row for name, row in client.resources.items() if "/triggers/" in name)
    trigger["conditions"] = {"transport": {"code": "FAILED_PRECONDITION", "message": "topic denied"}}
    status = driver.status(ServiceHandle(result.handle, managed_service_id=MSID))
    assert status.state == "error"
    assert "topic denied" in status.message


@pytest.mark.parametrize(
    ("channel_state", "expected_state"),
    [("PENDING", "provisioning"), ("INACTIVE", "error")],
)
def test_status_surfaces_partner_channel_readiness(
    driver: EventarcDriver,
    client: FakeEventarc,
    channel_state: str,
    expected_state: str,
) -> None:
    result = driver.provision(replace(SPEC, config=_full_config()))
    channel = next(row for name, row in client.resources.items() if "/channels/" in name)
    channel["state"] = channel_state

    status = driver.status(ServiceHandle(result.handle, managed_service_id=MSID))

    assert status.state == expected_state
    assert channel_state.lower() in status.message.lower() or "partner connection" in status.message


def test_status_and_delete_are_idempotent_when_bus_is_gone(driver: EventarcDriver) -> None:
    handle = ServiceHandle("event_bus/us-central1/astrolift")
    assert driver.status(handle).state == "deprovisioned"
    assert driver.deprovision(DeprovisionSpec(handle.handle)).ok


@pytest.mark.parametrize(
    ("manifest_config", "message"),
    [
        ({"message_bus_id": "Bad_Name"}, "message_bus_id"),
        ({"logging_config": {"log_severity": "VERBOSE"}}, "severity"),
        ({"pipelines": [{"id": "p", "destinations": []}]}, "exactly one destination"),
        (
            {
                "pipelines": [
                    {
                        "id": "p",
                        "destinations": [{"topic": "t", "workflow": "w"}],
                    },
                ],
            },
            "exactly one target",
        ),
        (
            {
                "pipelines": [
                    {
                        "id": "p",
                        "destinations": [{"topic": "t", "output_payload_format": {"json": {}}}],
                    },
                ],
            },
            "requires input_payload_format",
        ),
        (
            {
                "pipelines": [
                    {
                        "id": "p",
                        "destinations": [{"topic": "t"}],
                        "retry_policy": {"min_retry_delay": "601s"},
                    },
                ],
            },
            "between 1s and 600s",
        ),
        (
            {
                "pipelines": [
                    {
                        "id": "p",
                        "destinations": [{"http_endpoint": {"uri": "http://insecure.test"}}],
                    },
                ],
            },
            "requires an HTTPS URI",
        ),
        (
            {
                "google_api_sources": [
                    {
                        "id": "source",
                        "project_subscriptions": ["p"],
                        "organization_subscription": True,
                    },
                ],
            },
            "cannot combine",
        ),
        ({"triggers": [{"id": "t", "event_filters": [], "destination": {}}]}, "event_filters"),
        (
            {
                "triggers": [
                    {
                        "id": "t",
                        "event_filters": [{"attribute": "bucket", "value": "uploads"}],
                        "destination": {"workflow": "projects/p/locations/l/workflows/w"},
                    },
                ],
            },
            "requires a type event filter",
        ),
        ({"channels": [{"id": "c"}]}, "requires provider"),
        ({"raw_fields": {"etag": "forbidden"}}, "output-only"),
        (
            {"google_api_sources": [{"id": "one"}, {"id": "two"}]},
            "one Google API source",
        ),
        # Google's JSON parser accepts a field's proto name as well as its
        # lowerCamelCase JSON name, so a raw or cleared field spelled in proto
        # form must be refused outright rather than compared to the protected
        # set (#1981).
        ({"raw_fields": {"activation_token": "forbidden"}}, "lowerCamelCase JSON field names"),
        ({"raw_fields": {"Etag": "forbidden"}}, "raw_fields cannot set output-only fields: Etag"),
        ({"clear_fields": ["create_time"]}, "lowerCamelCase JSON field names"),
    ],
)
def test_invalid_configs_are_rejected(
    driver: EventarcDriver,
    manifest_config: dict[str, Any],
    message: str,
) -> None:
    result = driver.provision(replace(SPEC, config=manifest_config))
    assert not result.ok and message in result.message


def test_update_rejects_immutable_location_and_bus_id(driver: EventarcDriver) -> None:
    location = driver.update(
        UpdateSpec("event_bus/us-central1/astrolift", config={"location": "us-east1"}),
    )
    bus = driver.update(
        UpdateSpec("event_bus/us-central1/astrolift", config={"message_bus_id": "other"}),
    )
    assert not location.ok and "immutable" in location.message
    assert not bus.ok and "immutable" in bus.message


def test_publish_test_event_uses_publishing_contract(driver: EventarcDriver, client: FakeEventarc) -> None:
    result = _provision(driver)
    driver.publish_test_event(
        ServiceHandle(result.handle, managed_service_id=MSID),
        json_message='{"specversion":"1.0","type":"test","source":"astrolift","id":"1"}',
    )
    publish = next(call for call in client.calls if call[0] == "publish")
    assert publish[1].endswith("/messageBuses/astrolift")
    assert publish[2]["jsonMessage"].startswith("{")


def test_partner_connection_consumes_token_without_persisting_it(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    result = _provision(driver)
    handle = ServiceHandle(result.handle, managed_service_id=MSID)
    status = driver.connect_partner_channel(
        handle,
        connection_id="provider-link",
        channel="subscriber-channel",
        activation_token="input-only-token",
    )
    assert status.state == "available"
    name = "projects/project-1/locations/us-central1/channelConnections/provider-link"
    assert "activationToken" not in client.resources[name]
    create = next(call for call in client.calls if call[0] == "create" and call[1] == name)
    assert create[2]["activationToken"] == "input-only-token"
    driver.connect_partner_channel(
        handle,
        connection_id="provider-link",
        channel="subscriber-channel",
        activation_token="another-one-time-token",
    )
    assert len([call for call in client.calls if call[0] == "create" and call[1] == name]) == 1
    assert driver.disconnect_partner_channel(handle, connection_id="provider-link").state == "available"
    assert name not in client.resources


def test_snapshot_contract_is_honest(driver: EventarcDriver) -> None:
    with pytest.raises(EventarcError, match="no snapshot or archive API"):
        driver.snapshot(ServiceHandle("event_bus/us-central1/astrolift"))
    with pytest.raises(EventarcError, match="cannot be restored"):
        driver.restore(SimpleNamespace(), SPEC)  # type: ignore[arg-type]


def test_schema_exposes_both_editions_and_forward_compatible_fields(driver: EventarcDriver) -> None:
    schema = driver.config_schema()
    properties = schema["properties"]
    for field in (
        "pipelines",
        "enrollments",
        "google_api_sources",
        "triggers",
        "channels",
        "raw_fields",
        "deletion_protection",
        "delete_external_dependents",
    ):
        assert field in properties
    assert properties["pipelines"]["items"]["properties"]["destinations"]["maxItems"] == 1
    assert properties["raw_fields"]["additionalProperties"] is True
    assert "EVENT_BUS_PUBLISH_URL" in driver.binding_schema().env_vars


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, Any] | None = None,
        *,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = b"json" if payload is not None else b""

    def json(self) -> dict[str, Any]:
        return deepcopy(self._payload)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_rest_client_uses_control_and_publishing_endpoints() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": "bus"}),
            FakeResponse(200, {}),
            FakeResponse(200, {}),
            FakeResponse(200, {}),
        ],
    )
    client = EventarcRestClient(session=session)
    assert client.get("projects/p/locations/l/messageBuses/b")["name"] == "bus"
    client.publish("projects/p/locations/l/messageBuses/b", {"jsonMessage": "{}"})
    assert session.calls[0]["url"].startswith("https://eventarc.googleapis.com/v1/")
    assert session.calls[1]["url"].startswith("https://eventarcpublishing.googleapis.com/v1/")
    client.delete("projects/p/locations/l/channels/c", etag="ignored")
    client.delete("projects/p/locations/l/messageBuses/b", etag="current")
    assert session.calls[2]["params"] is None
    assert session.calls[3]["params"] == {"allowMissing": "true", "etag": "current"}


def test_rest_client_paginates_and_maps_provider_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"pipelines": [{"name": "one"}], "nextPageToken": "next"}),
            FakeResponse(200, {"pipelines": [{"name": "two"}]}),
            FakeResponse(404, {"error": {"message": "gone"}}),
            FakeResponse(409, {"error": {"message": "exists"}}),
            FakeResponse(403, {"error": {"message": "denied"}}),
        ],
    )
    client = EventarcRestClient(session=session)
    assert [row["name"] for row in client.list_resources("projects/p/locations/l", "pipelines")] == [
        "one",
        "two",
    ]
    assert session.calls[1]["params"]["pageToken"] == "next"
    with pytest.raises(EventarcNotFound):
        client.get("missing")
    with pytest.raises(EventarcConflict):
        client.create("projects/p/locations/l", "messageBuses", "b", {})
    with pytest.raises(EventarcError, match="denied"):
        client.get("forbidden")


def test_operation_error_is_never_reported_as_success(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    client.get_operation = lambda name: {
        "name": name,
        "done": True,
        "error": {"message": "quota exhausted"},
    }
    with pytest.raises(EventarcError, match="quota exhausted"):
        driver._wait_operation({"name": "operations/wait", "done": False})


def test_a_bus_adopted_before_2074_keeps_its_marker_through_reprovision_and_update(
    driver: EventarcDriver,
    client: FakeEventarc,
) -> None:
    """#2086: provision built the label map from the spec alone, so the next
    one dropped the marker, and with it the ``delete_adopted`` guard."""
    config = {**_full_config(), "deletion_protection": False}
    result = driver.provision(replace(SPEC, config=config))
    bus = "projects/project-1/locations/us-central1/messageBuses/astrolift"
    trigger = next(name for name in client.resources if "/triggers/" in name)
    for name in (bus, trigger):
        client.resources[name]["labels"]["astrolift-io-adopted"] = "true"

    assert driver.provision(replace(SPEC, config=config)).ok
    assert driver.update(UpdateSpec(result.handle, managed_service_id=MSID, config={"labels": {"team": "platform"}})).ok

    assert client.resources[bus]["labels"]["astrolift-io-adopted"] == "true"
    assert client.resources[trigger]["labels"]["astrolift-io-adopted"] == "true"
    denied = driver.deprovision(
        DeprovisionSpec(result.handle, {"deletion_protection": False}, managed_service_id=MSID), force_destroy=True
    )
    assert not denied.ok and denied.errors == ["adopted_resource_guard"]


def test_children_do_not_inherit_an_adopted_buss_marker(driver: EventarcDriver, client: FakeEventarc) -> None:
    result = _provision(driver, message_bus_id="astrolift", deletion_protection=False)
    bus = "projects/project-1/locations/us-central1/messageBuses/astrolift"
    client.resources[bus]["labels"]["astrolift-io-adopted"] = "true"

    assert driver.update(
        UpdateSpec(result.handle, managed_service_id=MSID, config={"pipelines": _full_config()["pipelines"]})
    ).ok

    pipeline = client.resources["projects/project-1/locations/us-central1/pipelines/to-run"]
    assert "astrolift-io-adopted" not in pipeline["labels"]
