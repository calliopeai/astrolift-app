from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from _sdk.managed_service import DeprovisionSpec, ProvisionSpec, ServiceHandle, UpdateSpec
from gcp.managed.api_gateway import (
    APIGatewayConfig,
    APIGatewayConflict,
    APIGatewayDriver,
    APIGatewayError,
    APIGatewayNotFound,
    APIGatewayRestClient,
)

SPEC = ProvisionSpec(
    organization_id="org-id",
    organization_slug="acme",
    app_id="app-id",
    app_slug="billing",
    environment_id="env-id",
    environment_name="production",
    tenant_cluster_id="cluster-id",
    service_handle_hint="public-api",
    size="small",
    binding_id="binding-id",
    managed_service_id="managed-id",
)


class FakeGatewayAPI:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[Any, ...]] = []
        self._operation = 0

    def get(self, name: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append(("get", name, deepcopy(params)))
        if name not in self.resources:
            raise APIGatewayNotFound(name)
        return deepcopy(self.resources[name])

    def list_resources(
        self,
        parent: str,
        collection: str,
        response_key: str,
        *,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        del response_key, params
        self.calls.append(("list", parent, collection))
        if collection == "gateways" and parent.endswith("/locations/-"):
            prefix = parent.removesuffix("-")
            return [
                deepcopy(value)
                for name, value in self.resources.items()
                if name.startswith(prefix) and "/gateways/" in name
            ]
        prefix = f"{parent}/{collection}/"
        return [deepcopy(value) for name, value in self.resources.items() if name.startswith(prefix)]

    def create(
        self,
        parent: str,
        collection: str,
        resource_id: str,
        id_parameter: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        del id_parameter
        name = f"{parent}/{collection}/{resource_id}"
        self.calls.append(("create", name, deepcopy(body)))
        if name in self.resources:
            raise APIGatewayConflict(name)
        resource = {"name": name, "state": "ACTIVE", **deepcopy(body)}
        if collection == "configs":
            resource["createTime"] = f"2026-08-14T00:00:{self._operation:02d}Z"
        if collection == "gateways":
            resource["defaultHostname"] = f"{resource_id}-hash.uc.gateway.dev"
        self.resources[name] = resource
        return self._done(resource)

    def patch(self, name: str, body: dict[str, Any], update_mask: list[str]) -> dict[str, Any]:
        self.calls.append(("patch", name, deepcopy(body), list(update_mask)))
        if name not in self.resources:
            raise APIGatewayNotFound(name)
        self.resources[name].update(deepcopy(body))
        return self._done(self.resources[name])

    def delete(self, name: str) -> dict[str, Any]:
        self.calls.append(("delete", name))
        if name not in self.resources:
            raise APIGatewayNotFound(name)
        self.resources.pop(name)
        return self._done({})

    def get_operation(self, name: str) -> dict[str, Any]:
        return deepcopy(self.operations[name])

    def _done(self, response: dict[str, Any]) -> dict[str, Any]:
        self._operation += 1
        operation = {
            "name": f"projects/p/locations/l/operations/{self._operation}",
            "done": True,
            "response": deepcopy(response),
        }
        self.operations[operation["name"]] = operation
        return operation


_ALLOWED_ACCOUNT = "gateway@project-1.iam.gserviceaccount.com"
_FOREIGN_ACCOUNT = "platform-admin@project-1.iam.gserviceaccount.com"


@pytest.fixture
def config() -> APIGatewayConfig:
    return APIGatewayConfig(
        project_id="project-1",
        region="us-central1",
        operation_timeout_seconds=1,
        poll_interval_seconds=0,
        allowed_service_accounts=(_ALLOWED_ACCOUNT,),
    )


@pytest.fixture
def client() -> FakeGatewayAPI:
    return FakeGatewayAPI()


@pytest.fixture
def driver(config: APIGatewayConfig, client: FakeGatewayAPI) -> APIGatewayDriver:
    return APIGatewayDriver(config=config, client=client, sleep=lambda _: None)


def _openapi_config(contents: str = "openapi: 3.0.0\ninfo:\n  title: Billing\n  version: 1.0.0\n") -> dict[str, Any]:
    return {
        "api_id": "billing-api",
        "gateway_id": "billing-gateway",
        "openapi_documents": [
            {
                "document": {
                    "path": "openapi.yaml",
                    "contents": contents,
                },
            },
        ],
        "gateway_service_account": "gateway@project-1.iam.gserviceaccount.com",
        "api_display_name": "Billing API",
        "config_display_name": "Billing API revision",
        "gateway_display_name": "Billing gateway",
        "labels": {"team": "payments"},
    }


def _names() -> tuple[str, str]:
    api = "projects/project-1/locations/global/apis/billing-api"
    gateway = "projects/project-1/locations/us-central1/gateways/billing-gateway"
    return api, gateway


def test_provision_creates_api_immutable_config_and_gateway(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    result = driver.provision(replace(SPEC, config=_openapi_config()))
    assert result.ok and result.ready
    assert result.handle == "api_gateway/us-central1/billing-gateway"
    api, gateway = _names()
    assert client.resources[api]["displayName"] == "Billing API"
    config_name = str(client.resources[gateway]["apiConfig"])
    assert config_name.startswith(f"{api}/configs/cfg-")
    document = client.resources[config_name]["openapiDocuments"][0]["document"]
    assert base64.b64decode(document["contents"]).decode().startswith("openapi: 3.0.0")
    assert client.resources[gateway]["defaultHostname"] == "billing-gateway-hash.uc.gateway.dev"
    for name in (api, config_name, gateway):
        assert client.resources[name]["labels"]["astrolift-io-managed-service-id"] == "managed-id"


def test_repeated_provision_is_idempotent(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    assert driver.provision(replace(SPEC, config=_openapi_config())).ok
    creates = len([call for call in client.calls if call[0] == "create"])
    assert driver.provision(replace(SPEC, config=_openapi_config())).ok
    assert len([call for call in client.calls if call[0] == "create"]) == creates


def test_update_creates_revision_and_atomically_retargets_gateway(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    result = driver.provision(replace(SPEC, config=_openapi_config()))
    _, gateway = _names()
    old_config = str(client.resources[gateway]["apiConfig"])
    updated_cfg = _openapi_config("openapi: 3.0.0\ninfo:\n  title: Billing v2\n  version: 2.0.0\n")
    updated_cfg.pop("gateway_id")
    updated_cfg["api_display_name"] = "Billing API v2"
    updated_cfg["labels"] = {"team": "platform"}
    updated = driver.update(UpdateSpec(result.handle, config=updated_cfg))
    assert updated.ok
    new_config = str(client.resources[gateway]["apiConfig"])
    assert new_config != old_config
    assert old_config in client.resources and new_config in client.resources
    api, _ = _names()
    assert client.resources[api]["displayName"] == "Billing API v2"
    assert client.resources[api]["labels"]["team"] == "platform"
    assert client.resources[new_config]["labels"]["team"] == "platform"
    patch = [call for call in client.calls if call[0] == "patch" and call[1] == gateway][-1]
    assert "apiConfig" in patch[3]


def test_update_cannot_retarget_the_gateway_at_another_services_api(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    """api_id is tenant config; allow_api_retarget must not reach an API this service does not own."""
    result = driver.provision(replace(SPEC, config=_openapi_config()))
    _, gateway = _names()
    active_config = str(client.resources[gateway]["apiConfig"])
    victim = "projects/project-1/locations/global/apis/victim-api"
    victim_labels = {"astrolift-io-managed-by": "platform", "astrolift-io-managed-service-id": "victim-service"}
    client.resources[victim] = {"name": victim, "state": "ACTIVE", "labels": dict(victim_labels)}
    cfg = {**_openapi_config(), "api_id": "victim-api", "allow_api_retarget": True}
    cfg.pop("gateway_id")

    refused = driver.update(UpdateSpec(result.handle, config=cfg, managed_service_id="managed-id"))

    assert not refused.ok
    assert "another managed service" in refused.message
    assert client.resources[victim]["labels"] == victim_labels
    assert not [name for name in client.resources if name.startswith(f"{victim}/configs/")]
    assert client.resources[gateway]["apiConfig"] == active_config


def test_explicit_config_id_rejects_changed_immutable_documents(
    driver: APIGatewayDriver,
) -> None:
    cfg = {**_openapi_config(), "config_id": "revision-one"}
    assert driver.provision(replace(SPEC, config=cfg)).ok
    cfg["openapi_documents"][0]["document"]["contents"] = "openapi: 3.0.0\ninfo: {title: changed}"
    changed = driver.provision(replace(SPEC, config=cfg))
    assert not changed.ok and "immutable" in changed.message


def test_pruning_requires_consent_and_retains_active_revision(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    result = driver.provision(replace(SPEC, config=_openapi_config()))
    denied_cfg = {
        **_openapi_config("openapi: 3.0.0\ninfo: {title: v2}"),
        "prune_config_revisions": True,
    }
    denied_cfg.pop("gateway_id")
    denied = driver.update(UpdateSpec(result.handle, config=denied_cfg))
    assert not denied.ok and "allow_config_revision_delete" in denied.message
    denied_cfg["allow_config_revision_delete"] = True
    denied_cfg["retain_config_revisions"] = 1
    assert driver.update(UpdateSpec(result.handle, config=denied_cfg)).ok
    api, gateway = _names()
    configs = client.list_resources(api, "configs", "apiConfigs")
    assert [item["name"] for item in configs] == [client.resources[gateway]["apiConfig"]]


def test_grpc_config_translates_descriptor_and_service_files(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    cfg = {
        "api_id": "billing-api",
        "gateway_id": "billing-gateway",
        "grpc_services": [
            {
                "file_descriptor_set": {
                    "path": "billing.pb",
                    "contents_base64": base64.b64encode(b"descriptor").decode(),
                },
                "source": [{"path": "billing.proto", "contents": 'syntax = "proto3";'}],
            },
        ],
        "managed_service_configs": [{"path": "api.yaml", "contents": "type: google.api.Service"}],
    }
    assert driver.provision(replace(SPEC, config=cfg)).ok
    api, gateway = _names()
    config = client.resources[str(client.resources[gateway]["apiConfig"])]
    assert (
        config["grpcServices"][0]["fileDescriptorSet"]["contents"]
        == cfg["grpc_services"][0]["file_descriptor_set"]["contents_base64"]
    )
    assert base64.b64decode(config["managedServiceConfigs"][0]["contents"]).decode().startswith("type:")
    assert str(config["name"]).startswith(f"{api}/configs/")


def test_binding_status_snapshot_and_restore_use_active_revision(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    result = driver.provision(replace(SPEC, config=_openapi_config()))
    binding = driver.binding(ServiceHandle(result.handle))
    assert binding.env_vars["API_GATEWAY_URL"].literal == "https://billing-gateway-hash.uc.gateway.dev"
    assert binding.iam_grants == []
    assert driver.status(ServiceHandle(result.handle)).state == "available"
    snapshot = driver.snapshot(ServiceHandle(result.handle))
    assert "/configs/" in snapshot.snapshot_id
    target_cfg = {"api_id": "billing-api", "gateway_id": "restored-gateway"}
    restored = driver.restore(snapshot, replace(SPEC, service_handle_hint="restored", config=target_cfg))
    assert restored.ok
    restored_name = "projects/project-1/locations/us-central1/gateways/restored-gateway"
    assert client.resources[restored_name]["apiConfig"] == snapshot.snapshot_id


@pytest.mark.parametrize(
    "account",
    [_FOREIGN_ACCOUNT, f"projects/-/serviceAccounts/{_FOREIGN_ACCOUNT}", "projects/project-1/accounts/1234567890"],
)
def test_a_gateway_cannot_call_backends_as_an_unlisted_account(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
    account: str,
) -> None:
    denied = driver.provision(replace(SPEC, config={**_openapi_config(), "gateway_service_account": account}))

    assert not denied.ok and "api_gateway_allowed_service_accounts" in denied.message
    assert not [call for call in client.calls if call[0] == "create"]


def test_update_cannot_move_a_gateway_to_an_unlisted_account(driver: APIGatewayDriver, client: FakeGatewayAPI) -> None:
    created = driver.provision(replace(SPEC, config=_openapi_config()))
    assert created.ok, created.message
    creates = len([call for call in client.calls if call[0] == "create"])

    moved = _openapi_config("openapi: 3.0.0\ninfo:\n  title: Billing v2\n  version: 2.0.0\n")
    moved.pop("gateway_id")
    moved["gateway_service_account"] = _FOREIGN_ACCOUNT

    denied = driver.update(UpdateSpec(created.handle, config=moved))

    assert not denied.ok and "api_gateway_allowed_service_accounts" in denied.message
    assert len([call for call in client.calls if call[0] == "create"]) == creates


def test_a_reused_config_keeps_no_identity_the_policy_does_not_list(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    created = driver.provision(replace(SPEC, config=_openapi_config()))
    snapshot = driver.snapshot(ServiceHandle(created.handle))
    # A revision created before the policy, still running as an account the
    # operator never listed.
    client.resources[snapshot.snapshot_id]["gatewayServiceAccount"] = _FOREIGN_ACCOUNT

    restored = driver.restore(
        snapshot,
        replace(SPEC, service_handle_hint="restored", config={"api_id": "billing-api", "gateway_id": "restored"}),
    )

    assert not restored.ok and "api_gateway_allowed_service_accounts" in restored.message
    assert "projects/project-1/locations/us-central1/gateways/restored" not in client.resources


def test_no_allowlist_refuses_every_config_supplied_account(client: FakeGatewayAPI) -> None:
    driver = APIGatewayDriver(
        config=APIGatewayConfig(project_id="project-1", region="us-central1", poll_interval_seconds=0),
        client=client,
        sleep=lambda _: None,
    )
    without_account = {key: value for key, value in _openapi_config().items() if key != "gateway_service_account"}

    denied = driver.provision(replace(SPEC, config=_openapi_config()))
    default_identity = driver.provision(replace(SPEC, config=without_account))

    assert not denied.ok and "api_gateway_allowed_service_accounts" in denied.message
    assert default_identity.ok, default_identity.message


def test_collision_is_refused_without_operator_adoption(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    api, gateway = _names()
    client.resources[api] = {"name": api, "state": "ACTIVE", "labels": {"owner": "customer"}}
    denied = driver.provision(replace(SPEC, config=_openapi_config()))
    assert not denied.ok
    assert "operator-authorized" in denied.message

    # The flag is gone entirely: the schema tenant config is validated against
    # rejects it, and a driver handed one anyway still refuses (#2021).
    validator = Draft202012Validator(driver.config_schema())
    assert validator.is_valid(_openapi_config())
    assert not validator.is_valid({**_openapi_config(), "adopt_existing": True})
    still_denied = driver.provision(replace(SPEC, config={**_openapi_config(), "adopt_existing": True}))
    assert not still_denied.ok
    assert client.resources[api]["labels"] == {"owner": "customer"}
    assert gateway not in client.resources


def test_another_services_resource_is_refused_and_no_flag_reassigns_it(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    api, _ = _names()
    labels = {"astrolift-io-managed-by": "platform", "astrolift-io-managed-service-id": "other-service"}
    client.resources[api] = {"name": api, "state": "ACTIVE", "labels": dict(labels)}
    denied = driver.provision(replace(SPEC, config=_openapi_config()))
    assert not denied.ok
    assert "another managed service" in denied.message

    validator = Draft202012Validator(driver.config_schema())
    assert not validator.is_valid({**_openapi_config(), "reassign_existing": True})
    still_denied = driver.provision(replace(SPEC, config={**_openapi_config(), "reassign_existing": True}))
    assert not still_denied.ok
    assert client.resources[api]["labels"] == labels


def test_deprovision_blocks_external_gateway_and_config_before_mutating(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    cfg = _openapi_config()
    result = driver.provision(replace(SPEC, config=cfg))
    api, gateway = _names()
    active_config = str(client.resources[gateway]["apiConfig"])
    external_gateway = "projects/project-1/locations/europe-west1/gateways/customer"
    client.resources[external_gateway] = {
        "name": external_gateway,
        "apiConfig": active_config,
        "state": "ACTIVE",
        "labels": {"owner": "customer"},
    }
    external_config = f"{api}/configs/customer-config"
    client.resources[external_config] = {
        "name": external_config,
        "state": "ACTIVE",
        "labels": {"owner": "customer"},
    }
    protected = driver.deprovision(DeprovisionSpec(result.handle, cfg))
    assert not protected.ok and protected.errors == ["deletion_protection_enabled"]
    blocked = driver.deprovision(
        DeprovisionSpec(result.handle, {**cfg, "deletion_protection": False}),
        force_destroy=True,
    )
    assert not blocked.ok and blocked.errors == ["external_gateways_present"]
    assert gateway in client.resources and external_gateway in client.resources
    blocked_config = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            {
                **cfg,
                "deletion_protection": False,
                "delete_external_gateways": True,
            },
        ),
        force_destroy=True,
    )
    assert not blocked_config.ok and blocked_config.errors == ["external_configs_present"]
    assert gateway in client.resources and external_gateway in client.resources
    deleted = driver.deprovision(
        DeprovisionSpec(
            result.handle,
            {
                **cfg,
                "deletion_protection": False,
                "delete_external_gateways": True,
                "delete_external_configs": True,
            },
        ),
        force_destroy=True,
    )
    assert deleted.ok
    assert not any(name.startswith("projects/project-1/") for name in client.resources)


@pytest.mark.parametrize(
    ("manifest_config", "message"),
    [
        ({"api_id": "Bad_Name", "api_config_ref": "x"}, "api_id"),
        ({"api_id": "billing-api"}, "exactly one"),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "openapi.yaml", "contents": "x"}},
                ],
                "grpc_services": [{"file_descriptor_set": {"path": "api.pb", "contents": "x"}}],
            },
            "exactly one",
        ),
        (
            {
                "api_id": "billing-api",
                "grpc_services": [{"file_descriptor_set": {"path": "api.pb", "contents": "x"}}],
            },
            "managed_service_configs",
        ),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "../openapi.yaml", "contents": "x"}},
                ],
            },
            "stay within",
        ),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {
                        "document": {
                            "path": "openapi.yaml",
                            "contents": "x",
                            "contents_base64": "eA==",
                        },
                    },
                ],
            },
            "exactly one of contents",
        ),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "openapi.yaml", "contents_base64": "not base64"}},
                ],
            },
            "invalid",
        ),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "openapi.yaml", "contents": "x"}},
                ],
                "labels": {"astrolift.io/managed-by": "attacker"},
            },
            "reserved",
        ),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "openapi.yaml", "contents": "x"}},
                ],
                "gateway_raw_fields": {"apiConfig": "attacker"},
            },
            "gateway_raw_fields",
        ),
        # Google's JSON parser accepts a field's proto name as well as its
        # lowerCamelCase JSON name, so a raw field spelled in proto form must
        # be refused outright rather than compared to the reserved set (#1981).
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "openapi.yaml", "contents": "x"}},
                ],
                "api_raw_fields": {"managed_service": "attacker"},
            },
            "lowerCamelCase JSON field names",
        ),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "openapi.yaml", "contents": "x"}},
                ],
                "config_raw_fields": {"open_api_documents": []},
            },
            "lowerCamelCase JSON field names",
        ),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "openapi.yaml", "contents": "x"}},
                ],
                "gateway_raw_fields": {"api_config": "attacker"},
            },
            "lowerCamelCase JSON field names",
        ),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "openapi.yaml", "contents": "x"}},
                ],
                "gateway_raw_fields": {"ApiConfig": "attacker"},
            },
            "gateway_raw_fields cannot set typed or output fields: ApiConfig",
        ),
        (
            {
                "api_id": "billing-api",
                "openapi_documents": [
                    {"document": {"path": "openapi.yaml", "contents": "x"}},
                ],
                "prune_config_revisions": True,
            },
            "allow_config_revision_delete",
        ),
    ],
)
def test_invalid_config_fails_closed(
    driver: APIGatewayDriver,
    manifest_config: dict[str, Any],
    message: str,
) -> None:
    result = driver.provision(replace(SPEC, config=manifest_config))
    assert not result.ok and message in result.message


def test_a_json_named_raw_field_the_driver_does_not_model_still_passes(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    cfg = {**_openapi_config(), "gateway_raw_fields": {"futureKnob": "beta"}}
    result = driver.provision(replace(SPEC, config=cfg))
    assert result.ok, result.message
    _, gateway = _names()
    assert client.resources[gateway]["futureKnob"] == "beta"


def test_schema_exposes_revision_transport_and_safety_controls(driver: APIGatewayDriver) -> None:
    properties = driver.config_schema()["properties"]
    for field in (
        "openapi_documents",
        "grpc_services",
        "managed_service_configs",
        "api_config_ref",
        "gateway_service_account",
        "prune_config_revisions",
        "delete_external_gateways",
        "delete_external_configs",
    ):
        assert field in properties
    assert "API_GATEWAY_URL" in driver.binding_schema().env_vars


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None, *, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"json" if payload is not None else b""

    def json(self) -> dict[str, Any]:
        return deepcopy(self._payload or {})


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def test_rest_client_uses_documented_create_patch_and_full_view() -> None:
    session = FakeSession(
        [
            FakeResponse(200, {"name": "operations/create"}),
            FakeResponse(200, {"name": "config"}),
            FakeResponse(200, {"name": "operations/patch"}),
        ],
    )
    client = APIGatewayRestClient(session=session)
    parent = "projects/p/locations/global/apis/api"
    name = f"{parent}/configs/revision"
    client.create(parent, "configs", "revision", "apiConfigId", {"openapiDocuments": []})
    client.get(name, params={"view": "FULL"})
    client.patch(name, {"displayName": "new"}, ["displayName"])
    assert session.calls[0]["params"] == {"apiConfigId": "revision"}
    assert session.calls[1]["params"] == {"view": "FULL"}
    assert session.calls[2]["params"] == {"updateMask": "displayName"}


def test_rest_client_maps_errors() -> None:
    session = FakeSession(
        [
            FakeResponse(404, {"error": {"message": "gone"}}),
            FakeResponse(409, {"error": {"message": "exists"}}),
            FakeResponse(403, {"error": {"message": "denied"}}),
        ],
    )
    client = APIGatewayRestClient(session=session)
    with pytest.raises(APIGatewayNotFound):
        client.get("missing")
    with pytest.raises(APIGatewayConflict):
        client.create("projects/p/locations/global", "apis", "api", "apiId", {})
    with pytest.raises(APIGatewayError, match="denied"):
        client.get("forbidden")


def test_operation_error_is_not_success(
    driver: APIGatewayDriver,
    client: FakeGatewayAPI,
) -> None:
    client.get_operation = lambda name: {
        "name": name,
        "done": True,
        "error": {"message": "service config rejected"},
    }
    with pytest.raises(APIGatewayError, match="rejected"):
        driver._wait({"name": "operations/wait", "done": False})
