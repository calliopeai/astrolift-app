"""Actual SDK10.4 transport/decoding; no cloud, credentials or event delivery."""

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
from azure.managed.event_grid import AzureEventGridConfig, AzureEventGridDriver, AzureEventGridError
from azure.mgmt.eventgrid import EventGridManagementClient
from azure.mgmt.resource.locks import ManagementLockClient

OWNER = "018f42f0-4420-7000-8000-000000000001"
SUBSCRIPTION = "018f42f0-4420-7000-8000-000000000002"
GROUP = "controlled-rg"


class _Credential:
    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken("controlled-transport-only", int(time.time()) + 3600)


class _Response(HttpResponse):
    def __init__(self, request: Any, status: int, data: dict[str, Any]):
        super().__init__(request, None)
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.content_type = "application/json"
        self.reason = "controlled response"
        self._body = json.dumps(data).encode()

    def body(self) -> bytes:
        return self._body

    def read(self):
        return self._body

    def iter_bytes(self):
        yield self._body

    def iter_raw(self):
        yield self._body

    def json(self):
        return json.loads(self._body)

    def stream_download(self, *args: Any, **kwargs: Any):
        return iter([self._body])


class RecordingEventGrid(HttpTransport):
    def __init__(self):
        self.rows: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
        self.failures: dict[tuple[str, str], tuple[int, str]] = {}
        self.replaced_ids: dict[str, str] = {}
        self.pages: dict[str, list[dict[str, Any] | tuple[int, str]]] = {}
        self.locks: list[dict[str, Any]] = []
        self.pending_topic = False
        self.pending_child = False
        self.pending_delete = False
        self.omit_state = False
        self.closed = False

    def open(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, request: Any, **kwargs: Any) -> HttpResponse:
        parsed = urlsplit(request.url)
        assert parsed.hostname == "management.azure.com"
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        is_lock = "/Microsoft.Authorization/locks" in path
        assert query["api-version"] == ["2020-05-01" if is_lock else "2025-02-15"]
        body = json.loads(request.body) if request.body else {}
        self.calls.append((request.method, path, body, kwargs))
        category = (
            "locks"
            if is_lock
            else "children"
            if path.endswith("/eventSubscriptions")
            else "child"
            if "/eventSubscriptions/" in path
            else "topic"
        )
        if (request.method, category) in self.failures:
            status, code = self.failures[request.method, category]
            return _Response(
                request, status, {"error": {"code": code, "message": "controlled denial ResourceNotFound diagnostic"}}
            )
        if request.method == "GET" and category in {"locks", "children"}:
            if category in self.pages:
                page = self.pages[category][int(query.get("$skip", ["0"])[0])]
                if isinstance(page, tuple):
                    return _Response(
                        request, page[0], {"error": {"code": page[1], "message": "denied ResourceNotFound"}}
                    )
                return _Response(request, 200, page)
            values = (
                [
                    row
                    for row in self.locks
                    if not row.get("id")
                    or row["id"]
                    .casefold()
                    .startswith(path.rsplit("/providers/Microsoft.Authorization/locks", 1)[0].casefold() + "/")
                ]
                if is_lock
                else [copy.deepcopy(v) for k, v in self.rows.items() if k.startswith(path + "/")]
            )
            return _Response(request, 200, {"value": values})
        if request.method == "GET":
            if path not in self.rows:
                return _Response(
                    request, 404, {"error": {"code": "ResourceNotFound", "message": "exact resource absent"}}
                )
            row = copy.deepcopy(self.rows[path])
            if path in self.replaced_ids:
                row["id"] = self.replaced_ids[path]
            return _Response(request, 200, row)
        if request.method in {"PUT", "PATCH"}:
            row = self.rows.setdefault(
                path,
                {
                    "id": path,
                    "name": path.rsplit("/", 1)[1],
                    "type": "Microsoft.EventGrid/topics"
                    if category == "topic"
                    else "Microsoft.EventGrid/topics/eventSubscriptions",
                    "properties": {},
                },
            )
            row["properties"].update(body.get("properties", {}))
            for key in ("tags", "identity", "location"):
                if key in body:
                    row[key] = copy.deepcopy(body[key])
            if category == "child":
                row["properties"]["topic"] = path.partition("/eventSubscriptions/")[0]
            else:
                row["properties"]["endpoint"] = f"https://{row['name']}.eastus-1.eventgrid.azure.net/api/events"
            pending = self.pending_topic if category == "topic" else self.pending_child
            row["properties"]["provisioningState"] = "Creating" if pending else "Succeeded"
            if self.omit_state:
                row["properties"].pop("provisioningState", None)
            response = _Response(
                request, 201 if request.method == "PUT" else (202 if pending else 200), copy.deepcopy(row)
            )
            if pending:
                response.headers["Azure-AsyncOperation"] = "https://management.azure.com/controlled-poll-must-not-run"
            return response
        if request.method == "DELETE":
            if path not in self.rows:
                return _Response(request, 404, {"error": {"code": "ResourceNotFound"}})
            if not self.pending_delete:
                self.rows.pop(path)
            response = _Response(request, 202 if self.pending_delete else 204, {})
            if self.pending_delete:
                response.headers["Azure-AsyncOperation"] = "https://management.azure.com/controlled-poll-must-not-run"
            return response
        raise AssertionError("unexpected controlled HTTP method")


def recording_config(api: RecordingEventGrid) -> AzureEventGridConfig:
    return AzureEventGridConfig(
        subscription_id=SUBSCRIPTION,
        resource_group=GROUP,
        location="eastus",
        mgmt_client=EventGridManagementClient(_Credential(), SUBSCRIPTION, transport=api, retry_total=0),
        locks_client=ManagementLockClient(_Credential(), SUBSCRIPTION, transport=api, retry_total=0),
    )


def source(**config: Any) -> ProvisionSpec:
    return ProvisionSpec(
        organization_id=OWNER,
        organization_slug="same-org",
        app_id=OWNER,
        app_slug="same-app",
        environment_id=OWNER,
        environment_name="prod",
        tenant_cluster_id=OWNER,
        service_handle_hint="same-name",
        size="small",
        managed_service_id=OWNER,
        config=config,
    )


def declared(name="events"):
    return {"name": name, "destination": {"type": "webhook", "endpoint_url": "https://example.com/controlled"}}


def owned(*, children=True):
    api = RecordingEventGrid()
    cfg = recording_config(api)
    driver = AzureEventGridDriver(config=cfg)
    spec = source(**({"subscriptions": [declared()]} if children else {}))
    result = driver.provision(spec)
    assert result.ok and result.ready, result
    api.calls.clear()
    return api, cfg, driver, dataclasses.replace(spec, recorded_handle=result.handle)


def writes(api):
    return [method for method, *_ in api.calls if method != "GET"]


def test_real_sdk_typed_wire_observed_owner_and_exact_publisher_manage_grants():
    api, cfg, driver, spec = owned()
    assert len(api.rows) == 2
    assert spec.recorded_handle.endswith("/astrolift-eg-" + OWNER.replace("-", ""))
    child = next(row for key, row in api.rows.items() if "/eventSubscriptions/" in key)
    assert child["name"].startswith("astrolift-" + OWNER.replace("-", "") + "-")
    assert child["properties"]["labels"] == ["astrolift-managed", "astrolift-owner-" + OWNER.replace("-", "")]
    assert child["properties"]["destination"]["endpointType"] == "WebHook"
    binding = driver.binding(
        ServiceHandle(spec.recorded_handle, managed_service_id=OWNER), config={"access_mode": "manage"}
    )
    assert binding.iam_grants[0].resource == next(k for k in api.rows if "/eventSubscriptions/" not in k)
    assert binding.iam_grants[0].actions == ["EventGrid Data Sender", "EventGrid EventSubscription Contributor"]
    assert driver.status(ServiceHandle(spec.recorded_handle, managed_service_id=OWNER)).state == "available"
    result = driver.provision(spec)
    assert result.ok and writes(api) == []
    cfg.mgmt_client.close()
    cfg.locks_client.close()


@pytest.mark.parametrize("part", ["topic", "child"])
@pytest.mark.parametrize(
    "damage", ["owner", "platform", "arm", "missing-arm", "unlabelled", "duplicate", "missing-source", "platform-alias"]
)
def test_current_source_and_exact_arm_proof_before_every_supported_effect(part, damage):
    api, _, driver, spec = owned()
    path = next(key for key in api.rows if ("/eventSubscriptions/" in key) == (part == "child"))
    row = api.rows[path]
    if damage in {"arm", "missing-arm"}:
        api.replaced_ids[path] = path.replace(GROUP, "foreign-rg") if damage == "arm" else ""
    elif part == "topic":
        if damage == "owner":
            row["tags"]["astrolift-managed-service-id"] = "018f42f0-4420-7000-8000-000000000099"
        elif damage == "platform":
            row["tags"]["astrolift-managed-by"] = "foreign"
        elif damage == "duplicate":
            row["tags"]["astrolift_managed_service_id"] = "018f42f0-4420-7000-8000-000000000099"
        elif damage == "missing-source":
            del row["tags"]["astrolift-managed-service-id"]
        elif damage == "platform-alias":
            row["tags"]["astrolift_managed_by"] = "foreign"
        else:
            row["tags"] = {}
    elif damage == "owner":
        row["properties"]["labels"][1] = "astrolift-owner-foreign"
    elif damage in {"platform", "platform-alias"}:
        row["properties"]["labels"][0] = "foreign"
    elif damage == "duplicate":
        row["properties"]["labels"].append(row["properties"]["labels"][1])
    elif damage == "missing-source":
        row["properties"]["labels"].pop(1)
    else:
        row["properties"]["labels"] = []
    before = copy.deepcopy(api.rows)
    assert not driver.provision(spec).ok
    assert not driver.update(
        UpdateSpec(spec.recorded_handle, managed_service_id=OWNER, config={"minimum_tls_version_allowed": "1.1"})
    ).ok
    assert driver.status(ServiceHandle(spec.recorded_handle, managed_service_id=OWNER)).state == "error"
    with pytest.raises(AzureEventGridError):
        driver.binding(ServiceHandle(spec.recorded_handle, managed_service_id=OWNER))
    for force in (False, True):
        result = driver.deprovision(
            DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), delete_data=True, force_destroy=force
        )
        assert not result.ok and result.errors[0] in {"ownership_unknown", "ownership_refused"}
    assert writes(api) == [] and api.rows == before


@pytest.mark.parametrize("scope", ["subscription", "group", "topic", "child"])
@pytest.mark.parametrize("force", [False, True])
def test_actual_inherited_and_operator_locks_never_deleted_or_bypassed(scope, force):
    api, _, driver, spec = owned()
    topic = next(k for k in api.rows if "/eventSubscriptions/" not in k)
    parent = (
        f"/subscriptions/{SUBSCRIPTION}"
        if scope == "subscription"
        else f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}"
        if scope == "group"
        else next(k for k in api.rows if "/eventSubscriptions/" in k)
        if scope == "child"
        else topic
    )
    api.locks = [
        {
            "id": parent + "/providers/Microsoft.Authorization/locks/operator-owned",
            "name": "operator-owned",
            "properties": {"level": "CanNotDelete"},
        }
    ]
    before = copy.deepcopy(api.rows)
    assert not driver.provision(spec).ok
    assert not driver.update(
        UpdateSpec(spec.recorded_handle, managed_service_id=OWNER, config={"minimum_tls_version_allowed": "1.1"})
    ).ok
    result = driver.deprovision(
        DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), delete_data=True, force_destroy=force
    )
    assert not result.ok and result.errors == ["resource_lock_present"]
    assert writes(api) == [] and api.rows == before


@pytest.mark.parametrize("category", ["topic", "child", "children", "locks"])
@pytest.mark.parametrize("status", [401, 403, 409, 500])
def test_http_denial_with_notfound_diagnostic_remains_unknown_no_effect(category, status):
    api, _, driver, spec = owned()
    api.failures["GET", category] = (status, "ForbiddenResourceNotFound")
    result = driver.deprovision(
        DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), delete_data=True, force_destroy=True
    )
    assert not result.ok and result.errors == ["ownership_unknown"] and writes(api) == []


@pytest.mark.parametrize("category", ["topic", "child"])
def test_sdk_no_polling_accepted_creation_stays_pending_without_background_http(category):
    api = RecordingEventGrid()
    cfg = recording_config(api)
    driver = AzureEventGridDriver(config=cfg)
    setattr(api, "pending_" + category, True)
    result = driver.provision(source(subscriptions=[declared()]))
    assert not result.ok and not result.ready and result.errors == ["provision_pending"]
    assert not any("controlled-poll-must-not-run" in path for _, path, *_ in api.calls)
    setattr(api, "pending_" + category, False)
    for row in api.rows.values():
        row["properties"]["provisioningState"] = "Succeeded"
    assert driver.provision(source(subscriptions=[declared()])).ok


def test_sdk_pending_delete_never_claims_cleanup_and_then_typed_absence_converges():
    api, _, driver, spec = owned()
    api.pending_delete = True
    outcome = driver.deprovision(DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), delete_data=True)
    assert not outcome.ok and outcome.errors == ["delete_pending"]
    assert not any("controlled-poll-must-not-run" in path for _, path, *_ in api.calls)
    api.pending_delete = False
    assert driver.deprovision(DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), delete_data=True).ok
    api.calls.clear()
    assert driver.deprovision(DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), delete_data=True).ok
    assert writes(api) == []


@pytest.mark.parametrize("gap", ["short", "provider", "missing", "missing-parent-child-left"])
def test_saved_targets_unknown_or_reassigned_refuse_unchanged(gap):
    api, cfg, driver, spec = owned()
    if gap == "short":
        spec = dataclasses.replace(spec, recorded_handle="event_bus/legacy-topic")
    elif gap == "provider":
        driver = AzureEventGridDriver(config=dataclasses.replace(cfg, resource_group="foreign-rg"))
    else:
        parent = next(k for k in api.rows if "/eventSubscriptions/" not in k)
        api.rows.pop(parent)
        if gap == "missing":
            api.rows.clear()
    assert not driver.provision(spec).ok
    assert not driver.update(UpdateSpec(spec.recorded_handle, managed_service_id=OWNER)).ok
    with pytest.raises(AzureEventGridError):
        driver.binding(ServiceHandle(spec.recorded_handle, managed_service_id=OWNER))
    if gap != "missing":
        result = driver.deprovision(DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), delete_data=True)
        assert not result.ok and result.errors[0] in {"ownership_unknown", "ownership_refused"}
    assert writes(api) == []


@pytest.mark.parametrize(
    "bad", [None, "", "not-uuid", "00000000-0000-0000-0000-000000000000", OWNER.replace("-", ""), OWNER.upper()]
)
def test_unknown_or_noncanonical_source_identity_never_reaches_http(bad):
    api = RecordingEventGrid()
    driver = AzureEventGridDriver(config=recording_config(api))
    assert not driver.provision(dataclasses.replace(source(), managed_service_id=bad)).ok
    assert api.calls == []


@pytest.mark.parametrize(
    "config",
    [
        {"subscriptions": [declared("Events"), declared("events")]},
        {"topic_name": "legacy"},
        {"topic_name": ""},
        {"subscriptions": [dict(declared(), labels=["ASTROLIFT_owner_other"])]},
    ],
)
def test_invalid_logical_collisions_overrides_or_reserved_labels_before_effect(config):
    api = RecordingEventGrid()
    driver = AzureEventGridDriver(config=recording_config(api))
    assert not driver.provision(source(**config)).ok
    assert api.calls == []


def test_recorded_exact_names_and_full_noneditable_snapshot_noop_or_truthful_refusal():
    api, cfg, driver, spec = owned()
    topic = spec.recorded_handle.rsplit("/", 1)[1]
    cfg2 = dataclasses.replace(cfg, topic_name_prefix="new-prefix")
    driver = AzureEventGridDriver(config=cfg2)
    full = {**spec.config, "topic_name": topic, "input_schema": "CloudEventSchemaV1_0"}
    assert driver.provision(
        dataclasses.replace(spec, config=full, app_slug="changed-app", service_handle_hint="changed-name")
    ).ok
    assert driver.update(UpdateSpec(spec.recorded_handle, managed_service_id=OWNER, config=full)).ok
    assert writes(api) == []
    for change in ({"topic_name": "changed-name"}, {"input_schema": "EventGridSchema"}):
        assert not driver.update(
            UpdateSpec(spec.recorded_handle, managed_service_id=OWNER, config={**full, **change})
        ).ok
    assert writes(api) == []


@pytest.mark.parametrize("defect", ["later-denied", "external-link", "other-collection", "pages", "items", "duplicate"])
def test_complete_bounded_inventory_precedes_parent_effects(defect):
    api, _, driver, spec = owned()
    child = next(v for k, v in api.rows.items() if "/eventSubscriptions/" in k)
    collection = child["id"].rsplit("/", 1)[0]
    following = f"https://management.azure.com{collection}?api-version=2025-02-15&$skip=1"
    if defect == "later-denied":
        api.pages["children"] = [{"value": [child], "nextLink": following}, (403, "ForbiddenResourceNotFound")]
    elif defect in {"external-link", "other-collection"}:
        following = (
            "https://external.invalid/controlled"
            if defect == "external-link"
            else following.replace("/eventSubscriptions?", "/other?")
        )
        api.pages["children"] = [{"value": [child], "nextLink": following}]
    elif defect == "pages":
        api.pages["children"] = [
            {"value": [], "nextLink": following.replace("$skip=1", f"$skip={n + 1}")} for n in range(4)
        ]
    elif defect == "items":
        api.pages["children"] = [{"value": [child] * 129}]
    else:
        api.pages["children"] = [{"value": [child, child]}]
    outcome = driver.update(
        UpdateSpec(spec.recorded_handle, managed_service_id=OWNER, config={"minimum_tls_version_allowed": "1.1"})
    )
    assert not outcome.ok and outcome.errors == ["ownership_unknown"] and writes(api) == []
    if defect == "pages":
        assert len([c for c in api.calls if c[1].endswith("/eventSubscriptions")]) == 4


def test_actual_typed_notfound_only_and_retention_snapshot_stay_sdk_free():
    api, _, driver, spec = owned()
    outcome = driver.deprovision(DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), force_destroy=True)
    assert not outcome.ok and outcome.errors == ["delete_data_required"] and writes(api) == []
    with pytest.raises(AzureEventGridError, match="no snapshot"):
        driver.snapshot(ServiceHandle(spec.recorded_handle))

    def fake_notfound(*args, **kwargs):
        class ResourceNotFoundError(Exception):
            pass

        raise ResourceNotFoundError("controlled class-name impostor")

    driver._mgmt.topics.get = fake_notfound
    outcome = driver.deprovision(DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), delete_data=True)
    assert not outcome.ok and outcome.errors == ["ownership_unknown"]
    assert writes(api) == []


@pytest.mark.parametrize("state", [None, "Creating", "Failed", "unknown"])
def test_readiness_never_defaults_missing_or_unstable_topic_state_to_success(state):
    api, _, driver, spec = owned()
    parent = next(row for key, row in api.rows.items() if "/eventSubscriptions/" not in key)
    if state is None:
        parent["properties"].pop("provisioningState")
    else:
        parent["properties"]["provisioningState"] = state
    result = driver.provision(spec)
    assert not result.ok and not result.ready and writes(api) == []
    assert driver.status(ServiceHandle(spec.recorded_handle, managed_service_id=OWNER)).state != "available"


@pytest.mark.parametrize("damage", ["unrelated-scope", "missing-id", "malformed-id"])
def test_incomplete_lock_identity_is_unknown_before_parent_mutation(damage):
    api, _, driver, spec = owned()
    if damage == "missing-id":
        api.locks = [{"name": "operator-lock"}]
    elif damage == "malformed-id":
        api.locks = [
            {
                "name": "operator-lock",
                "id": f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.Authorization/locks/invalid/extra",
            }
        ]
    else:
        # A transport returning a foreign lock at this collection is untrustworthy.
        api.pages["locks"] = [
            {
                "value": [
                    {
                        "name": "operator-lock",
                        "id": "/subscriptions/foreign/providers/Microsoft.Authorization/locks/operator-lock",
                    }
                ]
            }
        ]
    result = driver.update(
        UpdateSpec(spec.recorded_handle, managed_service_id=OWNER, config={"minimum_tls_version_allowed": "1.1"})
    )
    assert not result.ok and result.errors == ["ownership_unknown"] and writes(api) == []


def test_unrelated_well_formed_lock_does_not_invent_authority_or_block_owned_topic():
    api, _, driver, spec = owned()
    api.locks = [
        {
            "name": "unrelated",
            "id": (
                f"/subscriptions/{SUBSCRIPTION}/resourceGroups/other-rg"
                "/providers/Microsoft.Storage/storageAccounts/other/providers/Microsoft.Authorization/locks/unrelated"
            ),
            "properties": {"level": "ReadOnly"},
        }
    ]
    result = driver.update(
        UpdateSpec(spec.recorded_handle, managed_service_id=OWNER, config={"minimum_tls_version_allowed": "1.1"})
    )
    assert result.ok and writes(api) == ["PATCH"]
    assert len(api.locks) == 1


def test_retry_free_phase_bounds_and_admission_deadline_do_not_claim_remote_cancellation(monkeypatch):
    import azure.managed.event_grid as module

    api = RecordingEventGrid()
    driver = AzureEventGridDriver(config=recording_config(api))
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    original = api.send

    def timed(request, **kwargs):
        response = original(request, **kwargs)
        clock[0] += 6
        return response

    api.send = timed
    result = driver.provision(source(subscriptions=[declared()]))
    assert not result.ok and result.errors == ["ownership_unknown"]
    assert len(api.calls) == 4
    assert all(0 < options["connection_timeout"] <= 5 and 0 < options["read_timeout"] <= 5 for *_, options in api.calls)
    assert all("/eventSubscriptions/" not in path for _, path, *_ in api.calls)


def test_actual_declared_child_limit_refuses_before_any_write():
    api = RecordingEventGrid()
    driver = AzureEventGridDriver(config=recording_config(api))
    result = driver.provision(source(subscriptions=[declared(f"child-{i}") for i in range(129)]))
    assert not result.ok and result.errors == ["invalid_event_grid_config"] and api.calls == []


def test_complete_saved_legacy_physical_name_is_preserved_without_normalization():
    api, _, driver, spec = owned(children=False)
    previous, row = next(iter(api.rows.items()))
    physical = "Legacy" + ("n" * 44)
    target = driver._target(physical)
    del api.rows[previous]
    row["id"] = target.topic_id
    row["name"] = physical
    row["properties"]["endpoint"] = f"https://{physical}.eastus-1.eventgrid.azure.net/api/events"
    api.rows[target.topic_id] = row
    result = driver.provision(dataclasses.replace(spec, recorded_handle=target.handle, service_handle_hint="renamed"))
    assert result.ok and result.handle == target.handle and writes(api) == []
    binding = driver.binding(ServiceHandle(target.handle, managed_service_id=OWNER))
    assert binding.env_vars["EVENT_BUS_NAME"].literal == physical
    assert all(len(value.literal or "") <= 512 for value in binding.env_vars.values())


@pytest.mark.parametrize("coordinate", ["percent", "delimiter", "oversize", "zero", "noncanonical"])
def test_saved_target_unrepresentable_coordinates_fail_before_http(coordinate):
    api, _, driver, spec = owned()
    parts = spec.recorded_handle.split("/")
    if coordinate == "percent":
        parts[3] = "%2fother"
    elif coordinate == "delimiter":
        parts[3] = "other/extra"
    elif coordinate == "oversize":
        parts[3] = "g" * 91
    elif coordinate == "zero":
        parts[2] = "00000000-0000-0000-0000-000000000000"
    else:
        parts[2] = parts[2].upper()
    handle = "/".join(parts)
    with pytest.raises(AzureEventGridError):
        driver.binding(ServiceHandle(handle, managed_service_id=OWNER))
    assert api.calls == []


def test_typed_missing_parent_and_typed_missing_collection_are_idempotent_without_effects():
    api, _, driver, spec = owned()
    api.rows.clear()
    api.failures["GET", "children"] = (404, "ResourceNotFound")
    result = driver.deprovision(DeprovisionSpec(spec.recorded_handle, managed_service_id=OWNER), delete_data=True)
    assert result.ok and writes(api) == []


@pytest.mark.parametrize("observation", ["remaining-child", "denied-inventory"])
def test_new_parent_does_not_create_over_unknown_or_remaining_child_inventory(observation):
    api, _, driver, spec = owned()
    parent = next(key for key in api.rows if "/eventSubscriptions/" not in key)
    del api.rows[parent]
    if observation == "denied-inventory":
        api.rows.clear()
        api.failures["GET", "children"] = (403, "AuthorizationFailed")
    result = driver.provision(dataclasses.replace(spec, recorded_handle=""))
    assert not result.ok and result.errors == ["ownership_unknown"] and writes(api) == []


def test_child_authority_still_obeys_shared_verifier_before_effects_or_binding(monkeypatch):
    import azure.managed.event_grid as module
    from _sdk.azure_ownership import ARM_TAG_KEYS, AzureOperation, AzureOwnershipError

    api, _, driver, spec = owned()
    original = module.verify_azure_ownership
    observed = []

    def refuse_child(tags, expected, **kwargs):
        if kwargs["resource"] == "recorded Event Grid subscription":
            observed.append((tags, expected, kwargs["operation"]))
            raise AzureOwnershipError("shared ownership refusal")
        return original(tags, expected, **kwargs)

    monkeypatch.setattr(module, "verify_azure_ownership", refuse_child)
    result = driver.update(
        UpdateSpec(spec.recorded_handle, managed_service_id=OWNER, config={"minimum_tls_version_allowed": "1.1"})
    )
    assert not result.ok and result.errors == ["ownership_refused"]
    with pytest.raises(AzureEventGridError):
        driver.binding(ServiceHandle(spec.recorded_handle, managed_service_id=OWNER))
    assert len(observed) == 2 and writes(api) == []
    for tags, expected, operation in observed:
        assert tags == {ARM_TAG_KEYS.managed_by: "platform", ARM_TAG_KEYS.managed_service_id: OWNER}
        assert expected.managed_service_id == OWNER and operation == AzureOperation.UPDATE


@pytest.mark.parametrize("location", [None, "r" * 513])
def test_binding_does_not_guess_unknown_or_truncate_unrepresentable_observed_region(location):
    api, _, driver, spec = owned()
    parent = next(row for key, row in api.rows.items() if "/eventSubscriptions/" not in key)
    if location is None:
        parent.pop("location")
    else:
        parent["location"] = location
    with pytest.raises(AzureEventGridError):
        driver.binding(ServiceHandle(spec.recorded_handle, managed_service_id=OWNER))
    assert writes(api) == []
