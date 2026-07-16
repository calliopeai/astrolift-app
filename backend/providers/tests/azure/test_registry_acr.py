"""Tests for ACRDriver (#46, #764)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from azure._errors import NotFoundError
from azure.registry_acr import ACRConfig, ACRDriver


class _NotFound(Exception):
    pass


@dataclass
class FakeRegistry:
    name: str


@dataclass
class FakeRegistries:
    registries: dict[str, FakeRegistry] = field(default_factory=dict)

    def get(self, *, resource_group_name: str, registry_name: str) -> FakeRegistry:
        if registry_name not in self.registries:
            raise _NotFound(registry_name)
        return self.registries[registry_name]


@dataclass
class FakeACRClient:
    registries_obj: FakeRegistries = field(default_factory=FakeRegistries)
    tags_data: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def registries(self) -> FakeRegistries:
        return self.registries_obj

    def list_tags(
        self,
        *,
        resource_group_name: str,
        registry_name: str,
        repository: str,
    ) -> list[Any]:
        if repository not in self.tags_data:
            raise _NotFound(repository)
        return self.tags_data[repository]


@pytest.fixture
def fake_client() -> FakeACRClient:
    _NotFound.__name__ = "ResourceNotFoundError"
    client = FakeACRClient()
    client.registries_obj.registries["acmeprod"] = FakeRegistry(
        name="acmeprod",
    )
    return client


@pytest.fixture
def driver(fake_client: FakeACRClient) -> ACRDriver:
    return ACRDriver(
        config=ACRConfig(
            subscription_id="00000000-0000-0000-0000-000000000000",
            resource_group="rg-prod",
            registry_name="acmeprod",
            client=fake_client,
        ),
    )


def test_login_server_format(driver: ACRDriver) -> None:
    assert driver.login_server == "acmeprod.azurecr.io"


def test_ensure_repo_returns_uri(driver: ACRDriver) -> None:
    repo = driver.ensure_repo("api")
    assert repo.name == "api"
    assert repo.uri == "acmeprod.azurecr.io/api"


def test_ensure_repo_missing_registry(
    fake_client: FakeACRClient,
) -> None:
    fake_client.registries_obj.registries.pop("acmeprod")
    driver = ACRDriver(
        config=ACRConfig(
            subscription_id="0",
            resource_group="rg",
            registry_name="acmeprod",
            client=fake_client,
        ),
    )
    with pytest.raises(NotFoundError):
        driver.ensure_repo("api")


def test_pull_secret_marker_for_aks(driver: ACRDriver) -> None:
    secret = driver.get_pull_secret(cluster="prod", namespace="acme-api")
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["namespace"] == "acme-api"
    assert secret["metadata"]["annotations"]["astrolift.io/note"].startswith("AKS clusters use --attach-acr")
    docker = json.loads(
        base64.b64decode(secret["data"][".dockerconfigjson"]).decode(),
    )
    assert docker == {"auths": {}}


def test_push_returns_full_uri(driver: ACRDriver) -> None:
    uri = driver.push("local:dev", "api", "abc123")
    assert uri == "acmeprod.azurecr.io/api:abc123"


def test_push_rejects_blank_local_image(driver: ACRDriver) -> None:
    with pytest.raises(ValueError, match="local_image"):
        driver.push("", "api", "abc123")


def test_list_tags_translates_response(
    driver: ACRDriver,
    fake_client: FakeACRClient,
) -> None:
    @dataclass
    class _T:
        name: str
        digest: str = "sha256:abc"
        last_updated: str = "2026-01-01"

    fake_client.tags_data["api"] = [_T(name="v1")]
    tags = driver.list_tags("api")
    assert len(tags) == 1
    assert tags[0].name == "v1"
    assert tags[0].digest == "sha256:abc"


def test_list_tags_missing_repo_raises(driver: ACRDriver) -> None:
    with pytest.raises(NotFoundError):
        driver.list_tags("never")


def test_delete_repo_archive_noop(driver: ACRDriver) -> None:
    driver.delete_repo("api", archive=True)


def test_delete_repo_force_unsupported(driver: ACRDriver) -> None:
    with pytest.raises(NotImplementedError):
        driver.delete_repo("api", archive=False)


# ---- ensure_ci_push_role (#764) ----------------------------------------------


@dataclass
class FakeManagedIdentity:
    principal_id: str = "00000000-0000-0000-0000-aaaaaaaaaaaa"
    client_id: str = "00000000-0000-0000-0000-bbbbbbbbbbbb"
    tenant_id: str = ""


@dataclass
class FakeUserAssignedIdentities:
    identities: dict[str, FakeManagedIdentity] = field(default_factory=dict)
    create_calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def create_or_update(
        self,
        *,
        resource_group_name: str,
        resource_name: str,
        parameters: dict[str, Any],
    ) -> FakeManagedIdentity:
        self.create_calls.append((resource_group_name, resource_name, parameters))
        if resource_name not in self.identities:
            self.identities[resource_name] = FakeManagedIdentity()
        return self.identities[resource_name]


@dataclass
class FakeFederatedIdentityCredentials:
    credentials: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def create_or_update(
        self,
        *,
        resource_group_name: str,
        resource_name: str,
        federated_identity_credential_resource_name: str,
        parameters: dict[str, Any],
    ) -> None:
        self.credentials[(resource_name, federated_identity_credential_resource_name)] = parameters


@dataclass
class FakeMSIClient:
    user_assigned_identities_obj: FakeUserAssignedIdentities = field(
        default_factory=FakeUserAssignedIdentities,
    )
    federated_identity_credentials_obj: FakeFederatedIdentityCredentials = field(
        default_factory=FakeFederatedIdentityCredentials,
    )

    @property
    def user_assigned_identities(self) -> FakeUserAssignedIdentities:
        return self.user_assigned_identities_obj

    @property
    def federated_identity_credentials(self) -> FakeFederatedIdentityCredentials:
        return self.federated_identity_credentials_obj


@dataclass
class FakeAssignmentProperties:
    principal_id: str
    role_definition_id: str


@dataclass
class FakeRoleAssignment:
    properties: FakeAssignmentProperties


@dataclass
class FakeRoleAssignments:
    existing: list[FakeRoleAssignment] = field(default_factory=list)
    created: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def list_for_scope(self, *, scope: str) -> list[FakeRoleAssignment]:
        return list(self.existing)

    def create(
        self,
        *,
        scope: str,
        role_assignment_name: str,
        parameters: dict[str, Any],
    ) -> None:
        self.created.append((scope, role_assignment_name, parameters))


@dataclass
class FakeAuthzClient:
    role_assignments_obj: FakeRoleAssignments = field(default_factory=FakeRoleAssignments)

    @property
    def role_assignments(self) -> FakeRoleAssignments:
        return self.role_assignments_obj


@pytest.fixture
def msi_client() -> FakeMSIClient:
    return FakeMSIClient()


@pytest.fixture
def authz_client() -> FakeAuthzClient:
    return FakeAuthzClient()


@pytest.fixture
def ci_driver(
    fake_client: FakeACRClient,
    msi_client: FakeMSIClient,
    authz_client: FakeAuthzClient,
) -> ACRDriver:
    return ACRDriver(
        config=ACRConfig(
            subscription_id="11111111-1111-1111-1111-111111111111",
            resource_group="rg-prod",
            registry_name="acmeprod",
            tenant_id="22222222-2222-2222-2222-222222222222",
            client=fake_client,
            msi_client=msi_client,
            authz_client=authz_client,
        ),
    )


def test_ensure_ci_push_role_creates_new(
    ci_driver: ACRDriver,
    msi_client: FakeMSIClient,
    authz_client: FakeAuthzClient,
) -> None:
    """Fresh registry: MI + AcrPush role + FIC all land + a
    structured CiPushRole comes back with the three IDs the
    azure/login@v2 action needs."""
    role = ci_driver.ensure_ci_push_role(
        repo="api",
        scm_provider="github",
        scm_repo_full_name="acme/api-repo",
    )
    assert role.scm_provider == "github"
    payload = json.loads(role.role_ref)
    assert payload == {
        "client_id": "00000000-0000-0000-0000-bbbbbbbbbbbb",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "subscription_id": "11111111-1111-1111-1111-111111111111",
    }

    # MI was created with the expected name template
    assert "astrolift-api-acr-push" in msi_client.user_assigned_identities_obj.identities
    # FIC named 'github-push' bound to the correct issuer + subject
    fic_key = ("astrolift-api-acr-push", "github-push")
    assert fic_key in msi_client.federated_identity_credentials_obj.credentials
    fic_params = msi_client.federated_identity_credentials_obj.credentials[fic_key]
    assert fic_params["properties"]["issuer"] == "https://token.actions.githubusercontent.com"
    assert fic_params["properties"]["subject"] == "repo:acme/api-repo:*"
    assert fic_params["properties"]["audiences"] == ["api://AzureADTokenExchange"]

    # AcrPush role assignment created at the registry scope
    assert len(authz_client.role_assignments_obj.created) == 1
    scope, _name, params = authz_client.role_assignments_obj.created[0]
    assert scope.endswith("/registries/acmeprod")
    assert params["properties"]["roleDefinitionId"].endswith(
        "/8311e382-0749-4cb8-b61a-304f252e45ec",
    )
    assert params["properties"]["principalType"] == "ServicePrincipal"


def test_ensure_ci_push_role_idempotent(
    ci_driver: ACRDriver,
    msi_client: FakeMSIClient,
    authz_client: FakeAuthzClient,
) -> None:
    """Pre-existing MI + role assignment: driver skips the duplicate
    role create (FIC + MI calls are inherently idempotent in Azure)
    + returns the same CiPushRole the create path would."""
    # Seed an existing MI + matching role assignment so the
    # idempotency branch fires.
    existing_mi = FakeManagedIdentity(
        principal_id="aaaa1111-aaaa-1111-aaaa-111111111111",
        client_id="bbbb2222-bbbb-2222-bbbb-222222222222",
    )
    msi_client.user_assigned_identities_obj.identities["astrolift-api-acr-push"] = existing_mi
    authz_client.role_assignments_obj.existing.append(
        FakeRoleAssignment(
            properties=FakeAssignmentProperties(
                principal_id=existing_mi.principal_id,
                role_definition_id=(
                    "/subscriptions/11111111-1111-1111-1111-111111111111"
                    "/providers/Microsoft.Authorization/roleDefinitions"
                    "/8311e382-0749-4cb8-b61a-304f252e45ec"
                ),
            ),
        ),
    )

    first = ci_driver.ensure_ci_push_role(
        repo="api",
        scm_provider="github",
        scm_repo_full_name="acme/api-repo",
    )
    second = ci_driver.ensure_ci_push_role(
        repo="api",
        scm_provider="github",
        scm_repo_full_name="acme/api-repo",
    )

    # Same role_ref both times (deterministic; client_id-driven)
    assert first == second
    # Role assignment create was never invoked because list_for_scope
    # returned the seeded assignment
    assert authz_client.role_assignments_obj.created == []
    # MI create_or_update is allowed to be called per invocation —
    # Azure's API is idempotent — but the principal/client IDs we
    # echoed back came from the seeded identity, not a fresh one.
    payload = json.loads(first.role_ref)
    assert payload["client_id"] == "bbbb2222-bbbb-2222-bbbb-222222222222"


def test_ensure_ci_push_role_unsupported_scm(ci_driver: ACRDriver) -> None:
    """Any scm_provider other than 'github' raises
    UnsupportedOperationError — keeps the resolver layer's not-
    supported-on-this-cloud branch reachable for gitlab/bitbucket."""
    with pytest.raises(UnsupportedOperationError):
        ci_driver.ensure_ci_push_role(
            repo="api",
            scm_provider="gitlab",
            scm_repo_full_name="acme/api-repo",
        )
