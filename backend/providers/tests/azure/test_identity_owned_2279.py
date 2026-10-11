"""Real MSI7/Authorization SDK wire contracts, without Azure/cloud calls."""

from __future__ import annotations

import copy
import dataclasses
import json
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

import pytest

from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.identity_federated import AzureFederatedIdentityDriver, FederatedIdentityConfig
from azure.identity_owned import (
    AzureFederation,
    AzureIdentityContext,
    AzureIdentityGrant,
    AzureOwnedIdentityDriver,
    AzureOwnedIdentityError,
    assignment_name,
    federation_name,
)
from azure.role_catalog import AZURE_BUILTIN_ROLE_IDS

if TYPE_CHECKING:
    from collections.abc import Callable


def guid(number: int) -> str:
    return f"018f42f0-4420-7000-8000-{number:012d}"


SUB = guid(1)
TENANT = guid(2)
RESOURCE = (
    f"/subscriptions/{SUB}/resourceGroups/controlled-rg"
    "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/controlled-uami"
)
CONTEXT = AzureIdentityContext(
    guid(3),
    guid(4),
    guid(5),
    guid(6),
    TENANT,
    SUB,
    "controlled-rg",
    "controlled-uami",
    RESOURCE,
    guid(7),
    guid(8),
    "https://controlled-oidc.example/issuer/",
)
FEDERATION = AzureFederation("app-alpha", "worker")
GRANT = AzureIdentityGrant(
    AZURE_BUILTIN_ROLE_IDS["Storage Blob Data Contributor"],
    f"/subscriptions/{SUB}/resourceGroups/controlled-rg/providers/Microsoft.Storage/storageAccounts/controlled/blobServices/default/containers/data",
)


class Credential:
    def __init__(self) -> None:
        self.scopes: list[tuple[str, ...]] = []
        self.tenants: list[str | None] = []

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        self.scopes.append(scopes)
        self.tenants.append(kwargs.get("tenant_id"))
        return AccessToken("private-controlled-token", int(time.time()) + 3600)


class Response(HttpResponse):
    def __init__(self, request: Any, status: int, payload: dict[str, Any]) -> None:
        super().__init__(request, None)
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.content_type = "application/json"
        self._body = json.dumps(payload).encode()
        self.reason = "controlled native response"

    def body(self) -> bytes:
        return self._body


class NativeARM(HttpTransport):
    def __init__(self) -> None:
        self.subscription = {
            "id": f"/subscriptions/{SUB}",
            "subscriptionId": SUB,
            "tenantId": TENANT,
            "state": "Enabled",
        }
        self.identity: dict[str, Any] = {
            "id": RESOURCE,
            "name": "controlled-uami",
            "type": "Microsoft.ManagedIdentity/userAssignedIdentities",
            "location": "eastus",
            "tags": {
                "astrolift-managed-by": "platform",
                "astrolift-organization-id": guid(3),
                "astrolift-app-id": guid(4),
                "astrolift-cluster-id": guid(6),
            },
            "properties": {"tenantId": TENANT, "principalId": guid(8), "clientId": guid(7)},
        }
        self.fics: dict[str, dict[str, Any]] = {}
        self.roles: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.hook: Callable[[str, str], None] = lambda method, path: None
        self.fic_next: str | None = None
        self.role_next: str | None = None
        self.fail: tuple[str, int] | None = None
        self.closed = False
        self.discard_effect = False

    def open(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> NativeARM:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @property
    def writes(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [call for call in self.calls if call[0] != "GET"]

    def send(self, request: Any, **kwargs: Any) -> HttpResponse:
        url = urlsplit(request.url)
        assert url.netloc == "management.azure.com"
        assert request.headers["Authorization"] == "Bearer private-controlled-token"
        assert kwargs["connection_timeout"] == 5 and kwargs["read_timeout"] == 10
        path = unquote(url.path)
        payload = json.loads(request.body) if request.body else {}
        self.calls.append((request.method, path, payload))
        self.hook(request.method, path)
        if self.fail is not None and request.method == self.fail[0]:
            return Response(
                request, self.fail[1], {"error": {"code": "ControlledFailure", "message": "private-error-marker"}}
            )
        if path == f"/subscriptions/{SUB}":
            assert request.method == "GET"
            return Response(request, 200, copy.deepcopy(self.subscription))
        if path == RESOURCE:
            assert request.method == "GET", "UAMI must never be changed"
            return Response(request, 200, copy.deepcopy(self.identity))
        if path == RESOURCE + "/federatedIdentityCredentials":
            result: dict[str, Any] = {"value": list(copy.deepcopy(self.fics).values())}
            if self.fic_next:
                result["nextLink"] = self.fic_next
            return Response(request, 200, result)
        if path.casefold() == f"/subscriptions/{SUB}/providers/microsoft.authorization/roleassignments":
            result = {"value": list(copy.deepcopy(self.roles).values())}
            if self.role_next:
                result["nextLink"] = self.role_next
            return Response(request, 200, result)
        name = path.rsplit("/", 1)[-1]
        rows = self.fics if "/federatedIdentityCredentials/" in path else self.roles
        if request.method == "GET":
            return (
                Response(request, 200, copy.deepcopy(rows[name]))
                if name in rows
                else Response(request, 404, {"error": {"code": "ResourceNotFound", "message": "absent"}})
            )
        if request.method == "PUT":
            if rows is self.roles:
                payload["properties"]["scope"] = path.split("/providers/Microsoft.Authorization/roleAssignments/")[0]
            row = {"id": path, "name": name, "properties": payload["properties"]}
            if not self.discard_effect:
                rows[name] = row
            return Response(request, 200, row)
        if request.method == "DELETE":
            if not self.discard_effect:
                rows.pop(name, None)
            return Response(request, 204, {})
        raise AssertionError(request.method)


@pytest.fixture
def native() -> tuple[AzureOwnedIdentityDriver, NativeARM, Credential]:
    arm = NativeARM()
    credential = Credential()
    driver = AzureOwnedIdentityDriver(CONTEXT, credential=credential, transport=arm, checkpoint=lambda: None)
    yield driver, arm, credential
    driver.close()


def test_actual_msi7_factory_and_subscription_tenant_read(native: Any) -> None:
    driver, arm, credential = native
    record = driver.observe()
    assert record.resource_id == RESOURCE and record.principal_id == CONTEXT.principal_id
    assert type(driver._msi).__name__ == "ManagedServiceIdentityClient"
    assert credential.scopes and all(
        scopes == ("https://management.azure.com/.default",) for scopes in credential.scopes
    )
    assert not arm.writes


def test_actual_sdk_union_reconcile_repeat_detach_and_foreign_preservation(native: Any) -> None:
    driver, arm, _ = native
    foreign_fic = "operator-federation"
    arm.fics[foreign_fic] = {
        "id": RESOURCE + "/federatedIdentityCredentials/" + foreign_fic,
        "name": foreign_fic,
        "properties": {
            "issuer": "https://operator.example/",
            "subject": "operator",
            "audiences": ["operator-audience"],
        },
    }
    foreign_role = guid(77)
    rolepath = GRANT.scope + "/providers/Microsoft.Authorization/roleAssignments/" + foreign_role
    arm.roles[foreign_role] = {
        "id": rolepath,
        "name": foreign_role,
        "properties": {
            "scope": GRANT.scope,
            "principalId": CONTEXT.principal_id,
            "roleDefinitionId": (
                f"/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{GRANT.role_definition_id}"
            ),
            "description": "operator assignment",
        },
    }
    other = AzureFederation("app-beta", "worker")
    result = driver.reconcile(federations=(FEDERATION, other), grants=(GRANT,))
    assert result.configuration_observed and not result.workload_ready and not result.propagation_verified
    assert dict(result.annotations)["azure.workload.identity/client-id"] == CONTEXT.client_id
    writes = len(arm.writes)
    driver.reconcile(federations=(FEDERATION, other), grants=(GRANT,))
    assert len(arm.writes) == writes
    driver.reconcile(federations=(other,), grants=(GRANT,))
    assert federation_name(CONTEXT, FEDERATION.subject) not in arm.fics
    assert federation_name(CONTEXT, other.subject) in arm.fics
    driver.reconcile(federations=(), grants=())
    assert set(arm.fics) == {foreign_fic} and set(arm.roles) == {foreign_role}
    assert arm.identity["properties"]["clientId"] == CONTEXT.client_id


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "name",
        "clientId",
        "principalId",
        "tenantId",
        "owner",
        "owner_alias",
        "missing_owner",
        "subscription",
        "subscription_tenant",
        "subscription_state",
    ],
)
def test_native_parent_source_substitution_refuses_before_effect(native: Any, field: str) -> None:
    driver, arm, _ = native
    if field in ("id", "name"):
        arm.identity[field] = "foreign"
    elif field in ("clientId", "principalId", "tenantId"):
        arm.identity["properties"][field] = guid(99)
    elif field == "owner":
        arm.identity["tags"]["astrolift-app-id"] = guid(99)
    elif field == "owner_alias":
        arm.identity["tags"]["ASTROLIFT.IO/APP-ID"] = guid(99)
    elif field == "missing_owner":
        arm.identity["tags"].pop("astrolift-app-id")
    elif field == "subscription":
        arm.subscription["subscriptionId"] = guid(99)
    elif field == "subscription_tenant":
        arm.subscription["tenantId"] = guid(99)
    else:
        arm.subscription["state"] = "Disabled"
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert not arm.writes


@pytest.mark.parametrize(
    "next_link",
    [
        "https://foreign.example/steal",
        "https://management.azure.com/subscriptions/foreign/federatedIdentityCredentials",
        RESOURCE + "/../foreign",
        "https://management.azure.com"
        + RESOURCE
        + "/federatedIdentityCredentials?api-version=2024-11-30&$skiptoken=repeat",
    ],
)
def test_incomplete_or_external_paging_never_writes(native: Any, next_link: str) -> None:
    driver, arm, _ = native
    arm.fic_next = next_link
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert not arm.writes and len(arm.calls) < 10


@pytest.mark.parametrize("kind", ["fic", "role"])
def test_owned_signature_collision_is_preserved(native: Any, kind: str) -> None:
    driver, arm, _ = native
    driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    if kind == "fic":
        arm.fics[federation_name(CONTEXT, FEDERATION.subject)]["properties"]["issuer"] = "https://foreign.example/"
    else:
        arm.roles[assignment_name(CONTEXT, GRANT)]["properties"]["description"] = "foreign-marker"
    writes = len(arm.writes)
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert len(arm.writes) == writes


def test_checkpoint_withdrawal_after_inventory_blocks_first_write(native: Any) -> None:
    _, arm, credential = native
    withdrawn = False

    def hook(method: str, path: str) -> None:
        nonlocal withdrawn
        if path.casefold().endswith("/roleassignments"):
            withdrawn = True

    def checkpoint() -> None:
        if withdrawn:
            raise RuntimeError("private-checkpoint-marker")

    arm.hook = hook
    driver = AzureOwnedIdentityDriver(CONTEXT, credential=credential, transport=arm, checkpoint=checkpoint)
    with pytest.raises(AzureOwnedIdentityError, match="not confirmed") as error:
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert "private-checkpoint-marker" not in str(error.value) and not arm.writes


def test_replacement_after_inventory_blocks_first_write(native: Any) -> None:
    driver, arm, _ = native

    def hook(method: str, path: str) -> None:
        if path.casefold().endswith("/roleassignments"):
            arm.identity["properties"]["principalId"] = guid(99)

    arm.hook = hook
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert not arm.writes


@pytest.mark.parametrize("status", [403, 404, 409, 429, 500, 302])
def test_native_failure_is_sanitized_without_retry(native: Any, caplog: Any, status: int) -> None:
    driver, arm, _ = native
    arm.fail = ("PUT", status)
    with pytest.raises(AzureOwnedIdentityError) as error:
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert "private-error-marker" not in str(error.value) + caplog.text
    assert len(arm.writes) == 1


def test_successful_receipt_without_observed_configuration_is_not_ready(native: Any) -> None:
    driver, arm, _ = native
    arm.discard_effect = True
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))


@pytest.mark.parametrize(
    "change",
    [
        {"subscription_id": "sub-1"},
        {"app_id": str(__import__("uuid").UUID(int=0))},
        {"resource_id": RESOURCE + "-foreign"},
        {"issuer": "http://issuer.example"},
        {"identity_name": "../foreign"},
    ],
)
def test_context_invalid_before_any_native_client(change: dict[str, str]) -> None:
    with pytest.raises(AzureOwnedIdentityError):
        dataclasses.replace(CONTEXT, **change)


def test_legacy_ambient_writes_still_refuse_and_owned_factory_requires_context() -> None:
    config = FederatedIdentityConfig(TENANT, SUB, "controlled-rg", CONTEXT.issuer, credential=Credential())
    legacy = AzureFederatedIdentityDriver(config=config)
    with pytest.raises(RuntimeError):
        legacy.create_identity_role("controlled-uami", [])
    with pytest.raises(RuntimeError):
        legacy.bind_service_account("cluster", "namespace", "worker", "controlled-uami")
    with pytest.raises(RuntimeError):
        legacy.owned_reconciler()
    assert "private-controlled-token" not in repr(config)


def test_actual_config_factory_without_msi_injection(monkeypatch: Any) -> None:
    arm = NativeARM()
    credential = Credential()
    monkeypatch.setattr("azure.core.pipeline.transport.RequestsTransport", lambda: arm)
    options: list[dict[str, Any]] = []

    def credential_factory(**kwargs: Any) -> Credential:
        options.append(kwargs)
        return credential

    monkeypatch.setattr("azure.identity.DefaultAzureCredential", credential_factory)
    config = FederatedIdentityConfig(
        TENANT, SUB, "controlled-rg", CONTEXT.issuer, owned_context=CONTEXT, native_checkpoint=lambda: None
    )
    driver = AzureFederatedIdentityDriver(config=config).owned_reconciler()
    assert driver.observe().client_id == CONTEXT.client_id
    assert credential.tenants and set(credential.tenants) == {TENANT}
    assert options == [
        {
            "authority": "https://login.microsoftonline.com",
            "additionally_allowed_tenants": [],
            "exclude_interactive_browser_credential": True,
        }
    ]
    assert not arm.writes
    driver.close()
    assert arm.closed


@pytest.mark.parametrize(
    "case",
    [
        "fic_count",
        "role_count",
        "body",
        "duplicate",
        "foreign_fic_id",
        "foreign_role_id",
        "other_principal",
        "unknown_owned_role",
    ],
)
def test_bounded_complete_inventory_required_before_any_effect(native: Any, case: str) -> None:
    driver, arm, _ = native
    if case in ("fic_count", "body", "duplicate", "foreign_fic_id"):
        for i in range(21 if case == "fic_count" else 1):
            name = f"operator-fic-{i}"
            arm.fics[name] = {
                "id": RESOURCE + "/federatedIdentityCredentials/" + name,
                "name": name,
                "properties": {"issuer": "https://operator.example/", "subject": "operator", "audiences": ["operator"]},
            }
        if case == "body":
            arm.fics[name]["properties"]["subject"] = "x" * (2 * 1024 * 1024 + 1)
        elif case == "duplicate":
            arm.fics["duplicate"] = arm.fics[name]
        elif case == "foreign_fic_id":
            arm.fics[name]["id"] = RESOURCE + "-foreign/federatedIdentityCredentials/" + name
    else:
        for i in range(129 if case == "role_count" else 1):
            name = guid(1000 + i)
            arm.roles[name] = {
                "id": GRANT.scope + "/providers/Microsoft.Authorization/roleAssignments/" + name,
                "name": name,
                "properties": {
                    "scope": GRANT.scope,
                    "principalId": CONTEXT.principal_id,
                    "roleDefinitionId": (
                        f"/subscriptions/{SUB}"
                        f"/providers/Microsoft.Authorization/roleDefinitions/{GRANT.role_definition_id}"
                    ),
                    "description": "foreign",
                },
            }
        if case == "foreign_role_id":
            arm.roles[name]["id"] = arm.roles[name]["id"].replace(SUB, guid(99))
        elif case == "other_principal":
            arm.roles[name]["properties"]["principalId"] = guid(99)
        elif case == "unknown_owned_role":
            arm.roles[name]["properties"]["description"] = f"astrolift.io/owned-workload-identity={CONTEXT.owner_key}"
            arm.roles[name]["properties"]["roleDefinitionId"] = (
                f"/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{guid(99)}"
            )
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert not arm.writes


@pytest.mark.parametrize("case", ["issuer", "audience", "subject", "role_principal", "role_description"])
def test_named_child_replacement_between_inventory_and_effect_is_preserved(native: Any, case: str) -> None:
    driver, arm, _ = native
    driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    name = (
        federation_name(CONTEXT, FEDERATION.subject) if not case.startswith("role") else assignment_name(CONTEXT, GRANT)
    )
    row = copy.deepcopy((arm.fics if not case.startswith("role") else arm.roles)[name])
    rows = arm.fics if not case.startswith("role") else arm.roles
    rows.pop(name)
    if case == "issuer":
        row["properties"]["issuer"] = "https://foreign.example/"
    elif case == "audience":
        row["properties"]["audiences"] = ["foreign"]
    elif case == "subject":
        row["properties"]["subject"] = "system:serviceaccount:foreign:worker"
    elif case == "role_principal":
        row["properties"]["principalId"] = guid(99)
    else:
        row["properties"]["description"] = "foreign"

    def hook(method: str, path: str) -> None:
        if method == "GET" and path == row["id"]:
            rows[name] = row

    arm.hook = hook
    writes = len(arm.writes)
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert len(arm.writes) == writes and rows[name] == row


def test_changed_child_before_delete_is_preserved(native: Any) -> None:
    driver, arm, _ = native
    driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    name = federation_name(CONTEXT, FEDERATION.subject)

    def hook(method: str, path: str) -> None:
        if method == "GET" and path.endswith("/" + name):
            arm.fics[name]["properties"]["subject"] = "foreign"

    arm.hook = hook
    writes = len(arm.writes)
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(), grants=())
    assert len(arm.writes) == writes and name in arm.fics


def test_failed_role_after_fic_receipt_is_not_success_and_reobserve_does_not_duplicate(native: Any) -> None:
    driver, arm, _ = native

    def hook(method: str, path: str) -> None:
        if path.endswith("/" + assignment_name(CONTEXT, GRANT)) and method == "GET":
            arm.fail = ("PUT", 403)

    arm.hook = hook
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert federation_name(CONTEXT, FEDERATION.subject) in arm.fics
    arm.fail = None
    arm.hook = lambda method, path: None
    driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert sum(method == "PUT" and "/federatedIdentityCredentials/" in path for method, path, _ in arm.writes) == 1


@pytest.mark.parametrize(
    "scope,tenant",
    [(("https://foreign.example/.default",), TENANT), (("https://management.azure.com/.default",), guid(99))],
)
def test_credential_factory_cannot_widen_scope_or_tenant(native: Any, scope: tuple[str], tenant: str) -> None:
    driver, arm, credential = native
    with pytest.raises(AzureOwnedIdentityError):
        driver._credential.get_token(*scope, tenant_id=tenant)
    assert not credential.scopes and not arm.calls


@pytest.mark.parametrize("collection", ["role", "fic"])
def test_inventory_second_page_permission_failure_blocks_all_writes(native: Any, collection: str) -> None:
    driver, arm, _ = native
    path = (
        (RESOURCE + "/federatedIdentityCredentials")
        if collection == "fic"
        else f"/subscriptions/{SUB}/providers/Microsoft.Authorization/roleAssignments"
    )
    link = "https://management.azure.com" + path + "?api-version=2024-11-30&$skiptoken=second"
    if collection == "fic":
        arm.fic_next = link
    else:
        arm.role_next = link
    reads = 0

    def hook(method: str, current_path: str) -> None:
        nonlocal reads
        if current_path.casefold() == path.casefold():
            reads += 1
            if reads == 2:
                arm.fail = ("GET", 403)

    arm.hook = hook
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert reads == 2 and not arm.writes


def test_post_effect_current_source_withdrawal_blocks_next_write(native: Any) -> None:
    _, arm, credential = native
    withdrawn = False

    def hook(method: str, path: str) -> None:
        nonlocal withdrawn
        if method == "PUT":
            withdrawn = True

    def checkpoint() -> None:
        if withdrawn:
            raise RuntimeError("source withdrawn")

    arm.hook = hook
    driver = AzureOwnedIdentityDriver(CONTEXT, credential=credential, transport=arm, checkpoint=checkpoint)
    with pytest.raises(AzureOwnedIdentityError):
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    assert len(arm.writes) == 1 and not arm.roles


def test_formatted_error_and_sdk_debug_logs_never_expose_private_token_or_native_body(native: Any, caplog: Any) -> None:
    import logging
    import traceback

    driver, arm, _ = native
    caplog.set_level(logging.DEBUG)
    arm.fail = ("PUT", 403)
    with pytest.raises(AzureOwnedIdentityError) as error:
        driver.reconcile(federations=(FEDERATION,), grants=(GRANT,))
    formatted = "".join(traceback.format_exception(error.value)) + caplog.text
    assert "private-controlled-token" not in formatted and "private-error-marker" not in formatted
    assert arm.writes, "must exercise actual denied SDK write"


def test_same_driver_nested_use_refuses_without_resetting_outer_inventory(native: Any) -> None:
    driver, arm, _ = native
    inner_refused = False

    def hook(method: str, path: str) -> None:
        nonlocal inner_refused
        if method == "GET" and path == RESOURCE and not inner_refused:
            with pytest.raises(AzureOwnedIdentityError, match="Concurrent"):
                driver.observe()
            inner_refused = True

    arm.hook = hook
    assert driver.reconcile(federations=(FEDERATION,), grants=(GRANT,)).configuration_observed
    assert inner_refused


def test_held_final_native_read_withdrawal_cannot_report_observed_success(native: Any) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _, arm, credential = native
    arrived = Event()
    release = Event()
    withdrawn = Event()

    def checkpoint() -> None:
        if withdrawn.is_set():
            raise RuntimeError("final current authority withdrawn")

    def hook(method: str, path: str) -> None:
        complete_role_inventories = sum(p.casefold().endswith("/roleassignments") for _, p, _ in arm.calls)
        if method == "GET" and path == RESOURCE and complete_role_inventories == 2:
            arrived.set()
            assert release.wait(5)

    arm.hook = hook
    driver = AzureOwnedIdentityDriver(CONTEXT, credential=credential, transport=arm, checkpoint=checkpoint)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(driver.reconcile, federations=(FEDERATION,), grants=(GRANT,))
        try:
            assert arrived.wait(5), "must hold the final real MSI7 UAMI response"
            withdrawn.set()
        finally:
            release.set()
        with pytest.raises(AzureOwnedIdentityError, match="not confirmed"):
            future.result(timeout=5)
    assert len(arm.writes) == 2, "native configuration can exist while current admission is withdrawn"
