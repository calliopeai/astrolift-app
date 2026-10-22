"""Provisioning and removal of the platform's own UAMI over real MSI7/Authorization
SDK clients, against a controlled ARM transport: no Azure calls or credentials."""

from __future__ import annotations

import copy
import dataclasses
import json
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

from _sdk.cloud_credentials import CloudCredential, CredentialMode
from azure.identity_federated import AzureFederatedIdentityDriver, AzureManagedIdentity, FederatedIdentityConfig
from azure.identity_owned import (
    AzureFederation,
    AzureIdentityPlacement,
    AzureIdentityProvisioner,
    AzureIdentityRefused,
    AzureOwnedIdentityDriver,
    AzureOwnedIdentityError,
    assignment_name,
    federation_name,
)

from .test_identity_owned_2279 import CONTEXT, GRANT, RESOURCE, SUB, TENANT, Credential, NativeARM, Response, guid

CLUSTER = (
    f"/subscriptions/{SUB}/resourceGroups/controlled-rg/providers/Microsoft.ContainerService/managedClusters/aks-1"
)
PLACEMENT = AzureIdentityPlacement(
    organization_id=CONTEXT.organization_id,
    app_id=CONTEXT.app_id,
    cluster_id=CONTEXT.cluster_id,
    tenant_id=TENANT,
    subscription_id=SUB,
    resource_group="controlled-rg",
    identity_name="controlled-uami",
    location="eastus",
    cluster_name="aks-1",
    issuer=CONTEXT.issuer,
)
OWNER_TAGS = {
    "astrolift-managed-by": "platform",
    "astrolift-organization-id": CONTEXT.organization_id,
    "astrolift-app-id": CONTEXT.app_id,
    "astrolift-cluster-id": CONTEXT.cluster_id,
}


class ProvisioningARM(NativeARM):
    """The owned-port transport plus the AKS cluster and UAMI lifecycle."""

    def __init__(self) -> None:
        super().__init__()
        self.cluster: dict[str, Any] | None = {
            "id": CLUSTER,
            "name": "aks-1",
            "properties": {
                "oidcIssuerProfile": {"enabled": True, "issuerURL": CONTEXT.issuer},
                "securityProfile": {"workloadIdentity": {"enabled": True}},
            },
        }
        self.created = 0
        self.keep_after_delete = False

    def send(self, request: Any, **kwargs: Any) -> Any:
        path = unquote(urlsplit(request.url).path)
        if path not in (CLUSTER, RESOURCE):
            return super().send(request, **kwargs)
        assert request.headers["Authorization"] == "Bearer private-controlled-token"
        assert kwargs["connection_timeout"] == 5 and kwargs["read_timeout"] == 10
        payload = json.loads(request.body) if request.body else {}
        self.calls.append((request.method, path, payload))
        self.hook(request.method, path)
        if self.fail is not None and request.method == self.fail[0]:
            return Response(request, self.fail[1], {"error": {"code": "ControlledFailure", "message": "private"}})
        if path == CLUSTER:
            assert request.method == "GET", "the AKS cluster is only ever read"
            if self.cluster is None:
                return Response(request, 404, {"error": {"code": "ResourceNotFound", "message": "absent"}})
            return Response(request, 200, copy.deepcopy(self.cluster))
        if request.method == "GET":
            if self.identity is None:
                return Response(request, 404, {"error": {"code": "ResourceNotFound", "message": "absent"}})
            return Response(request, 200, copy.deepcopy(self.identity))
        if request.method == "PUT":
            assert self.identity is None, "an existing identity must never be rewritten"
            self.created += 1
            self.identity = {
                "id": RESOURCE,
                "name": "controlled-uami",
                "type": "Microsoft.ManagedIdentity/userAssignedIdentities",
                "location": payload["location"],
                "tags": payload["tags"],
                "properties": {
                    "tenantId": TENANT,
                    "principalId": guid(800 + self.created),
                    "clientId": guid(700 + self.created),
                },
            }
            return Response(request, 201, copy.deepcopy(self.identity))
        assert request.method == "DELETE"
        if not self.keep_after_delete:
            self.identity = None
            self.fics.clear()
        return Response(request, 200, {})


def _provisioner(arm: ProvisioningARM, **changes: Any) -> AzureIdentityProvisioner:
    placement = dataclasses.replace(PLACEMENT, **changes)
    return AzureIdentityProvisioner(placement, checkpoint=lambda: None, credential=Credential(), transport=arm)


def _recorded(arm: ProvisioningARM) -> AzureManagedIdentity:
    props = arm.identity["properties"]
    return AzureManagedIdentity("controlled-uami", RESOURCE, props["clientId"], props["principalId"], TENANT)


def test_absent_identity_is_created_with_owner_markers_then_observed() -> None:
    arm = ProvisioningARM()
    arm.identity = None
    identity = _provisioner(arm).ensure()
    assert [(method, path) for method, path, _ in arm.calls] == [
        ("GET", f"/subscriptions/{SUB}"),
        ("GET", CLUSTER),
        ("GET", RESOURCE),
        ("PUT", RESOURCE),
        ("GET", RESOURCE),
    ]
    assert arm.writes[0][2] == {"location": "eastus", "tags": OWNER_TAGS}
    assert (identity.resource_id, identity.client_id, identity.principal_id, identity.tenant_id) == (
        RESOURCE,
        guid(701),
        guid(801),
        TENANT,
    )


def test_this_owners_identity_is_observed_without_any_write() -> None:
    arm = ProvisioningARM()
    identity = _provisioner(arm).ensure()
    assert not arm.writes
    assert (identity.client_id, identity.principal_id) == (CONTEXT.client_id, CONTEXT.principal_id)
    assert _provisioner(arm).ensure(recorded=identity) == identity
    assert not arm.writes


@pytest.mark.parametrize("change", ["other_app", "untagged", "conflicting_alias", "not_platform"])
def test_same_name_identity_without_this_owners_markers_is_refused_untouched(change: str) -> None:
    arm = ProvisioningARM()
    tags = arm.identity["tags"]
    if change == "other_app":
        tags["astrolift-app-id"] = guid(99)
    elif change == "untagged":
        arm.identity["tags"] = {}
    elif change == "conflicting_alias":
        tags["ASTROLIFT.IO/APP-ID"] = guid(99)
    else:
        tags["astrolift-managed-by"] = "operator"
    before = copy.deepcopy(arm.identity)
    with pytest.raises(AzureIdentityRefused, match="without this app's ownership markers"):
        _provisioner(arm).ensure()
    assert not arm.writes and arm.identity == before


@pytest.mark.parametrize("field", ["clientId", "principalId"])
def test_recorded_identity_replaced_under_the_same_name_is_refused(field: str) -> None:
    arm = ProvisioningARM()
    recorded = _recorded(arm)
    arm.identity["properties"][field] = guid(99)
    with pytest.raises(AzureIdentityRefused, match="was replaced"):
        _provisioner(arm).ensure(recorded=recorded)
    assert not arm.writes


def test_recorded_identity_that_is_gone_is_refused_not_recreated() -> None:
    arm = ProvisioningARM()
    recorded = _recorded(arm)
    arm.identity = None
    with pytest.raises(AzureIdentityRefused, match="no longer exists"):
        _provisioner(arm).ensure(recorded=recorded)
    assert not arm.writes and arm.identity is None


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("changed_issuer", "signs with OIDC issuer https://changed.example/issuer/, not the configured"),
        ("oidc_disabled", "does not have both its OIDC issuer and workload identity enabled"),
        ("workload_identity_disabled", "does not have both its OIDC issuer and workload identity enabled"),
        ("missing_cluster", "could not be read"),
    ],
)
def test_live_cluster_issuer_and_workload_identity_gate_everything(case: str, message: str) -> None:
    arm = ProvisioningARM()
    arm.identity = None
    properties = arm.cluster["properties"]
    if case == "changed_issuer":
        properties["oidcIssuerProfile"]["issuerURL"] = "https://changed.example/issuer/"
    elif case == "oidc_disabled":
        properties["oidcIssuerProfile"]["enabled"] = False
    elif case == "workload_identity_disabled":
        properties["securityProfile"]["workloadIdentity"]["enabled"] = False
    else:
        arm.cluster = None
    with pytest.raises(AzureIdentityRefused, match=message):
        _provisioner(arm).ensure()
    assert all(path != RESOURCE for _, path, _ in arm.calls), "no identity is read or created first"


def test_unsafe_live_issuer_is_not_echoed() -> None:
    arm = ProvisioningARM()
    arm.cluster["properties"]["oidcIssuerProfile"]["issuerURL"] = "http://private-marker\n"
    with pytest.raises(AzureIdentityRefused) as refused:
        _provisioner(arm).ensure()
    assert "private-marker" not in str(refused.value)


@pytest.mark.parametrize("field", ["cluster_id", "identity_id", "identity_name"])
def test_a_response_naming_another_resource_is_unconfirmed_and_writes_nothing(field: str) -> None:
    arm = ProvisioningARM()
    if field == "cluster_id":
        arm.cluster["id"] = CLUSTER.replace("aks-1", "aks-2")
    elif field == "identity_id":
        arm.identity["id"] = RESOURCE.replace("controlled-uami", "other-uami")
    else:
        arm.identity["name"] = "other-uami"
    with pytest.raises(AzureOwnedIdentityError, match="not confirmed") as error:
        _provisioner(arm).ensure()
    assert not isinstance(error.value, AzureIdentityRefused)
    assert not arm.writes


def test_subscription_of_another_tenant_is_refused_before_the_cluster_is_read() -> None:
    arm = ProvisioningARM()
    arm.subscription["tenantId"] = guid(99)
    with pytest.raises(AzureIdentityRefused, match="not an enabled subscription"):
        _provisioner(arm).ensure()
    assert [path for _, path, _ in arm.calls] == [f"/subscriptions/{SUB}"]


@pytest.mark.parametrize("status", [403, 409, 500])
def test_failed_create_is_sanitized_and_records_nothing(status: int) -> None:
    arm = ProvisioningARM()
    arm.identity = None
    arm.fail = ("PUT", status)
    with pytest.raises(AzureOwnedIdentityError, match="not confirmed") as error:
        _provisioner(arm).ensure()
    assert not isinstance(error.value, AzureIdentityRefused)
    assert "private" not in str(error.value)


def test_withdrawn_checkpoint_before_create_blocks_it() -> None:
    arm = ProvisioningARM()
    arm.identity = None
    withdrawn = False

    def hook(method: str, path: str) -> None:
        nonlocal withdrawn
        if method == "GET" and path == RESOURCE:
            withdrawn = True

    def checkpoint() -> None:
        if withdrawn:
            raise RuntimeError("withdrawn")

    arm.hook = hook
    provisioner = AzureIdentityProvisioner(PLACEMENT, checkpoint=checkpoint, credential=Credential(), transport=arm)
    with pytest.raises(AzureOwnedIdentityError, match="not confirmed"):
        provisioner.ensure()
    assert not arm.writes


@pytest.mark.parametrize(
    "change",
    [
        {"organization_id": "not-a-guid"},
        {"location": "east/us"},
        {"cluster_name": "-aks"},
        {"issuer": "http://issuer.example/"},
        {"identity_name": "../foreign"},
    ],
)
def test_placement_is_validated_before_any_client(change: dict[str, str]) -> None:
    with pytest.raises(AzureOwnedIdentityError):
        dataclasses.replace(PLACEMENT, **change)


def _patch_production_factory(monkeypatch: Any, arm: NativeARM) -> list[dict[str, Any]]:
    credential = Credential()
    options: list[dict[str, Any]] = []

    def credential_factory(**kwargs: Any) -> Credential:
        options.append(kwargs)
        return credential

    monkeypatch.setattr("azure.core.pipeline.transport.RequestsTransport", lambda: arm)
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", credential_factory)
    return options


def _stamped_config(**owned: Any) -> FederatedIdentityConfig:
    # What _config_for_capability hands the driver: stamp_credential puts the
    # cluster's ambient CloudCredential on the config.
    return FederatedIdentityConfig(
        TENANT,
        SUB,
        "controlled-rg",
        CONTEXT.issuer,
        credential=CloudCredential(cloud="azure", declared_account=SUB),
        native_checkpoint=lambda: None,
        **owned,
    )


def test_owned_reconciler_admits_the_stamped_ambient_credential(monkeypatch: Any) -> None:
    # Before #2279 the factory handed the stamped CloudCredential to the token
    # wrapper as if it were a TokenCredential, so every production observation
    # failed at the first token request.
    arm = ProvisioningARM()
    _patch_production_factory(monkeypatch, arm)
    reconciler = AzureFederatedIdentityDriver(config=_stamped_config(owned_context=CONTEXT)).owned_reconciler()
    assert reconciler.observe().principal_id == CONTEXT.principal_id
    reconciler.close()


def test_production_factories_admit_the_stamped_ambient_credential(monkeypatch: Any) -> None:
    arm = ProvisioningARM()
    options = _patch_production_factory(monkeypatch, arm)
    driver = AzureFederatedIdentityDriver(config=_stamped_config(owned_placement=PLACEMENT))
    provisioner = driver.owned_provisioner()
    assert type(provisioner._msi).__name__ == "ManagedServiceIdentityClient"
    identity = provisioner.ensure()
    provisioner.close()
    reconciler = AzureFederatedIdentityDriver(
        config=_stamped_config(
            owned_context=PLACEMENT.context(environment_id=CONTEXT.environment_id, identity=identity)
        )
    ).owned_reconciler()
    result = reconciler.reconcile(federations=(AzureFederation("app-alpha", "worker"),), grants=(GRANT,))
    reconciler.close()
    assert result.configuration_observed and not result.propagation_verified
    assert (
        options
        == [
            {
                "authority": "https://login.microsoftonline.com",
                "additionally_allowed_tenants": [],
                "exclude_interactive_browser_credential": True,
            }
        ]
        * 2
    )
    assert arm.closed


@pytest.mark.parametrize(
    "credential",
    [
        CloudCredential(
            cloud="aws",
            mode=CredentialMode.AWS_ASSUME_ROLE,
            role_arn="arn:aws:iam::123456789012:role/x",
        ),
        CloudCredential(cloud="gcp"),
        object(),
    ],
)
def test_any_other_credential_is_refused_before_a_request(monkeypatch: Any, credential: Any) -> None:
    arm = ProvisioningARM()
    _patch_production_factory(monkeypatch, arm)
    config = dataclasses.replace(_stamped_config(owned_placement=PLACEMENT), credential=credential)
    with pytest.raises(AzureOwnedIdentityError, match="ambient Azure credential"):
        AzureFederatedIdentityDriver(config=config).owned_provisioner()
    assert not arm.calls


@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": guid(98)},
        {"subscription_id": guid(98)},
        {"resource_group": "other-rg"},
        {"location": "westus"},
        {"issuer": "https://other.example/issuer/"},
    ],
)
def test_provisioner_factory_refuses_a_placement_the_config_does_not_name(change: dict[str, str]) -> None:
    config = _stamped_config(owned_placement=dataclasses.replace(PLACEMENT, **change))
    with pytest.raises(RuntimeError, match="placement"):
        AzureFederatedIdentityDriver(config=config).owned_provisioner()


def _reconciled(arm: ProvisioningARM) -> AzureOwnedIdentityDriver:
    driver = AzureOwnedIdentityDriver(CONTEXT, checkpoint=lambda: None, credential=Credential(), transport=arm)
    driver.reconcile(federations=(AzureFederation("app-alpha", "worker"),), grants=(GRANT,))
    return driver


def test_detach_reports_what_it_removed_and_preserves_the_rest() -> None:
    arm = ProvisioningARM()
    driver = AzureOwnedIdentityDriver(CONTEXT, checkpoint=lambda: None, credential=Credential(), transport=arm)
    alpha, beta = AzureFederation("app-alpha", "worker"), AzureFederation("app-beta", "worker")
    driver.reconcile(federations=(alpha, beta), grants=(GRANT,))
    arm.fics["operator-federation"] = {
        "id": RESOURCE + "/federatedIdentityCredentials/operator-federation",
        "name": "operator-federation",
        "properties": {"issuer": "https://operator.example/", "subject": "operator", "audiences": ["operator"]},
    }
    result = driver.reconcile(federations=(alpha,), grants=())
    assert result.removed_federation_names == (federation_name(CONTEXT, beta.subject),)
    assert result.removed_assignment_names == (assignment_name(CONTEXT, GRANT),)
    assert result.foreign_federation_names == ("operator-federation",)
    assert set(arm.fics) == {federation_name(CONTEXT, alpha.subject), "operator-federation"}
    assert not arm.roles


def test_remove_strips_owned_entries_then_deletes_and_confirms_the_identity_gone() -> None:
    arm = ProvisioningARM()
    driver = _reconciled(arm)
    result = driver.remove()
    assert result.identity_deleted and not result.identity_absent and not result.propagation_verified
    assert result.removed_federation_names == (federation_name(CONTEXT, "system:serviceaccount:app-alpha:worker"),)
    assert result.removed_assignment_names == (assignment_name(CONTEXT, GRANT),)
    assert arm.identity is None and not arm.roles
    assert ("DELETE", RESOURCE) in [(method, path) for method, path, _ in arm.writes]
    assert arm.calls[-1][:2] == ("GET", RESOURCE), "absence is observed after the delete"


@pytest.mark.parametrize("foreign", ["federation", "assignment"])
def test_remove_leaves_an_identity_that_carries_foreign_entries(foreign: str) -> None:
    arm = ProvisioningARM()
    driver = _reconciled(arm)
    if foreign == "federation":
        arm.fics["operator-federation"] = {
            "id": RESOURCE + "/federatedIdentityCredentials/operator-federation",
            "name": "operator-federation",
            "properties": {"issuer": "https://operator.example/", "subject": "operator", "audiences": ["operator"]},
        }
    else:
        name = guid(77)
        arm.roles[name] = {
            "id": GRANT.scope + "/providers/Microsoft.Authorization/roleAssignments/" + name,
            "name": name,
            "properties": {
                "scope": GRANT.scope,
                "principalId": CONTEXT.principal_id,
                "roleDefinitionId": (
                    f"/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{GRANT.role_definition_id}"
                ),
                "description": "operator assignment",
            },
        }
    result = driver.remove()
    assert not result.identity_deleted and arm.identity is not None
    assert result.removed_assignment_names == (assignment_name(CONTEXT, GRANT),)
    assert (result.foreign_federation_names or result.foreign_assignment_names) in (
        ("operator-federation",),
        (guid(77),),
    )
    assert all(not (method == "DELETE" and path == RESOURCE) for method, path, _ in arm.writes)


def test_remove_reports_an_identity_already_gone_without_writing() -> None:
    arm = ProvisioningARM()
    arm.identity = None
    driver = AzureOwnedIdentityDriver(CONTEXT, checkpoint=lambda: None, credential=Credential(), transport=arm)
    result = driver.remove()
    assert result.identity_absent and not result.identity_deleted and not arm.writes


def test_remove_reports_a_replaced_identity_gone_without_writing() -> None:
    # The recorded principal is gone, so the caller's removal is done; the
    # identity now holding the name is not this owner's to strip or delete.
    arm = ProvisioningARM()
    arm.identity["properties"]["principalId"] = guid(99)
    arm.identity["properties"]["clientId"] = guid(98)
    driver = AzureOwnedIdentityDriver(CONTEXT, checkpoint=lambda: None, credential=Credential(), transport=arm)
    result = driver.remove()
    assert result.identity_replaced and result.identity_absent and not result.identity_deleted
    assert not arm.writes and arm.identity is not None


@pytest.mark.parametrize("change", ["no_principal", "same_principal_other_client", "other_tenant", "other_name"])
def test_remove_refuses_an_identity_that_is_not_evidently_replaced(change: str) -> None:
    arm = ProvisioningARM()
    properties = arm.identity["properties"]
    if change == "no_principal":
        del properties["principalId"]
    elif change == "same_principal_other_client":
        properties["clientId"] = guid(98)
    elif change == "other_tenant":
        properties["tenantId"], properties["principalId"] = guid(97), guid(99)
    else:
        arm.identity["name"], properties["principalId"] = "other-uami", guid(99)
    driver = AzureOwnedIdentityDriver(CONTEXT, checkpoint=lambda: None, credential=Credential(), transport=arm)
    with pytest.raises(AzureIdentityRefused, match="replaced or is incomplete"):
        driver.remove()
    assert not arm.writes


def test_remove_is_unconfirmed_while_the_identity_survives_its_delete() -> None:
    arm = ProvisioningARM()
    driver = _reconciled(arm)
    arm.keep_after_delete = True
    with pytest.raises(AzureOwnedIdentityError, match="removal was not confirmed"):
        driver.remove()
