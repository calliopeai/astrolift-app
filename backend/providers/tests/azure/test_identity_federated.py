"""Tests for AzureFederatedIdentityDriver (#45, #1367)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from azure._errors import NotFoundError, ProviderError
from azure.identity_federated import (
    AzureFederatedIdentityDriver,
    AzureRoleAssignmentError,
    FederatedIdentityConfig,
    role_assignment_name,
)
from azure.role_catalog import AZURE_BUILTIN_ROLE_IDS, AzureGrantContractError

_SUB = "sub-1"
_RG = "rg"
_BLOB_ROLE = AZURE_BUILTIN_ROLE_IDS["Storage Blob Data Contributor"]
_QUEUE_ROLE = AZURE_BUILTIN_ROLE_IDS["Azure Service Bus Data Sender"]
_CONTAINER_SCOPE = (
    f"/subscriptions/{_SUB}/resourceGroups/{_RG}/providers/Microsoft.Storage"
    "/storageAccounts/acct/blobServices/default/containers/data"
)
_QUEUE_SCOPE = f"/subscriptions/{_SUB}/resourceGroups/{_RG}/providers/Microsoft.ServiceBus/namespaces/ns/queues/jobs"
_MANAGED_DESCRIPTION = "astrolift.io/managed-by=platform workload-identity grant"


class _NotFound(Exception):
    pass


class _Exists(Exception):
    pass


_NotFound.__name__ = "ResourceNotFoundError"
_Exists.__name__ = "ResourceExistsError"


@dataclass
class FakeUAI:
    name: str
    id: str = ""
    client_id: str = "client-1234"
    principal_id: str = "principal-9999"
    tenant_id: str = "tenant-1"


@dataclass
class FakeUAIs:
    identities: dict[str, FakeUAI] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)

    def create_or_update(
        self,
        *,
        resource_group_name: str,
        resource_name: str,
        parameters: dict[str, Any],
    ) -> FakeUAI:
        self.create_calls.append(parameters)
        identity = self.identities.get(resource_name) or FakeUAI(
            name=resource_name,
            id=(
                f"/subscriptions/{_SUB}/resourceGroups/{resource_group_name}"
                f"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{resource_name}"
            ),
        )
        self.identities[resource_name] = identity
        return identity

    def get(self, *, resource_group_name: str, resource_name: str) -> FakeUAI:
        if resource_name not in self.identities:
            raise _NotFound(resource_name)
        return self.identities[resource_name]

    def delete(self, *, resource_group_name: str, resource_name: str) -> None:
        if resource_name not in self.identities:
            raise _NotFound(resource_name)
        del self.identities[resource_name]


@dataclass
class FakeFederatedCredentials:
    creds: list[dict[str, Any]] = field(default_factory=list)

    def create_or_update(
        self,
        *,
        resource_group_name: str,
        resource_name: str,
        federated_identity_credential_resource_name: str,
        parameters: dict[str, Any],
    ) -> Any:
        self.creds.append(
            {
                "identity": resource_name,
                "credential_name": federated_identity_credential_resource_name,
                "parameters": parameters,
            }
        )


@dataclass
class FakeMSI:
    user_assigned_identities: FakeUAIs = field(default_factory=FakeUAIs)
    federated_identity_credentials: FakeFederatedCredentials = field(
        default_factory=FakeFederatedCredentials,
    )


@dataclass
class FakeAssignment:
    name: str
    scope: str
    role_definition_id: str
    principal_id: str
    principal_type: str = "ServicePrincipal"
    description: str = ""


@dataclass
class FakeRoleAssignments:
    assignments: dict[tuple[str, str], FakeAssignment] = field(default_factory=dict)
    create_calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    list_error: Exception | None = None
    conflict_on_create: bool = False

    def create(
        self,
        *,
        scope: str,
        role_assignment_name: str,
        parameters: dict[str, Any],
    ) -> FakeAssignment:
        props = parameters["properties"]
        self.create_calls.append((scope, role_assignment_name, props))
        key = (scope, role_assignment_name)
        # ARM answers RoleAssignmentExists both when the name is taken and when
        # the same (principal, role, scope) is already granted under any other
        # name, so the fake conflicts on both.
        if key in self.assignments or self.conflict_on_create:
            raise _Exists(role_assignment_name)
        for existing in self.assignments.values():
            if (
                existing.scope == scope
                and existing.principal_id == props["principalId"]
                and existing.role_definition_id == props["roleDefinitionId"]
            ):
                raise _Exists(role_assignment_name)
        self.assignments[key] = FakeAssignment(
            name=role_assignment_name,
            scope=scope,
            role_definition_id=props["roleDefinitionId"],
            principal_id=props["principalId"],
            principal_type=props["principalType"],
            description=props.get("description", ""),
        )
        return self.assignments[key]

    def get(self, *, scope: str, role_assignment_name: str) -> FakeAssignment:
        try:
            return self.assignments[(scope, role_assignment_name)]
        except KeyError:
            raise _NotFound(role_assignment_name) from None

    def delete(self, *, scope: str, role_assignment_name: str) -> None:
        key = (scope, role_assignment_name)
        if key not in self.assignments:
            raise _NotFound(role_assignment_name)
        self.deleted.append(key)
        del self.assignments[key]

    def list_for_subscription(self, *, filter: str) -> list[FakeAssignment]:
        if self.list_error is not None:
            raise self.list_error
        principal_id = filter.split("'")[1]
        return [a for a in self.assignments.values() if a.principal_id == principal_id]

    # -- test helpers ---------------------------------------------------

    def seed(self, assignment: FakeAssignment) -> FakeAssignment:
        self.assignments[(assignment.scope, assignment.name)] = assignment
        return assignment


@dataclass
class FakeAuthz:
    role_assignments: FakeRoleAssignments = field(default_factory=FakeRoleAssignments)


def _role_definition_id(guid: str) -> str:
    return f"/subscriptions/{_SUB}/providers/Microsoft.Authorization/roleDefinitions/{guid}"


def _permission(guid: str, scope: str, role_name: str = "role") -> dict[str, Any]:
    return {"role_definition_id": guid, "role_name": role_name, "scope": scope}


def _blob_assignment_name(scope: str) -> str:
    return role_assignment_name(
        principal_id="principal-9999",
        role_definition_guid=_BLOB_ROLE,
        scope=scope,
    )


@pytest.fixture
def fake_msi() -> FakeMSI:
    return FakeMSI()


@pytest.fixture
def fake_authz() -> FakeAuthz:
    return FakeAuthz()


@pytest.fixture
def driver(fake_msi: FakeMSI, fake_authz: FakeAuthz) -> AzureFederatedIdentityDriver:
    return AzureFederatedIdentityDriver(
        config=FederatedIdentityConfig(
            tenant_id="tenant-1",
            subscription_id=_SUB,
            resource_group=_RG,
            cluster_oidc_issuer="https://oidc.prod.azure.com/abc",
            msi_client=fake_msi,
            authz_client=fake_authz,
        ),
    )


# ---- identity creation ----------------------------------------------------


def test_create_identity_role_returns_the_uami_resource_id(
    driver: AzureFederatedIdentityDriver,
    fake_msi: FakeMSI,
) -> None:
    resource_id = driver.create_identity_role("api", permissions=[])

    assert resource_id == (
        f"/subscriptions/{_SUB}/resourceGroups/{_RG}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/api"
    )
    assert "api" in fake_msi.user_assigned_identities.identities
    assert fake_msi.user_assigned_identities.create_calls[0]["tags"] == {
        "astrolift-managed-by": "platform",
    }


def test_describe_identity_role_exposes_principal_and_client_ids(
    driver: AzureFederatedIdentityDriver,
) -> None:
    driver.create_identity_role("api", permissions=[])

    identity = driver.describe_identity_role("api")

    assert identity.principal_id == "principal-9999"
    assert identity.client_id == "client-1234"
    assert identity.resource_id.endswith("/userAssignedIdentities/api")


def test_bind_emits_workload_identity_annotation(
    driver: AzureFederatedIdentityDriver,
) -> None:
    driver.create_identity_role("api", permissions=[])
    annos = driver.bind_service_account(
        cluster="aks-prod",
        namespace="acme-api",
        sa_name="api-sa",
        identity_role="api",
    )
    assert annos["azure.workload.identity/client-id"] == "client-1234"
    assert annos["azure.workload.identity/tenant-id"] == "tenant-1"


def test_bind_creates_federated_credential(
    driver: AzureFederatedIdentityDriver,
    fake_msi: FakeMSI,
) -> None:
    driver.create_identity_role("api", permissions=[])
    driver.bind_service_account(
        cluster="aks-prod",
        namespace="ns",
        sa_name="sa",
        identity_role="api",
    )
    creds = fake_msi.federated_identity_credentials.creds
    assert len(creds) == 1
    cred = creds[0]
    assert cred["identity"] == "api"
    props = cred["parameters"]["properties"]
    assert props["subject"] == "system:serviceaccount:ns:sa"
    assert props["audiences"] == ["api://AzureADTokenExchange"]
    assert props["issuer"] == "https://oidc.prod.azure.com/abc"


def test_bind_without_msi_client_raises() -> None:
    driver = AzureFederatedIdentityDriver(
        config=FederatedIdentityConfig(
            tenant_id="t",
            subscription_id="s",
            resource_group="rg",
            cluster_oidc_issuer="i",
            msi_client=None,
        ),
    )
    with pytest.raises(RuntimeError, match="msi_client"):
        driver.bind_service_account(
            cluster="c",
            namespace="n",
            sa_name="s",
            identity_role="r",
        )


# ---- role assignments -----------------------------------------------------


def test_grant_becomes_a_role_assignment_at_the_declared_scope(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    driver.create_identity_role(
        "api",
        permissions=[_permission(_BLOB_ROLE, _CONTAINER_SCOPE, "Storage Blob Data Contributor")],
    )

    (scope, name, props) = fake_authz.role_assignments.create_calls[0]
    assert scope == _CONTAINER_SCOPE
    assert props["roleDefinitionId"] == _role_definition_id(_BLOB_ROLE)
    assert props["principalId"] == "principal-9999"
    assert props["principalType"] == "ServicePrincipal"
    assert props["description"] == _MANAGED_DESCRIPTION
    assert name == str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{_CONTAINER_SCOPE}|{_BLOB_ROLE}|principal-9999",
        ),
    )


def test_assignment_name_is_derived_only_from_the_grant_it_expresses() -> None:
    same = role_assignment_name(
        principal_id="principal-9999",
        role_definition_guid=_BLOB_ROLE.upper(),
        scope=_CONTAINER_SCOPE + "/",
    )
    assert same == role_assignment_name(
        principal_id="principal-9999",
        role_definition_guid=_BLOB_ROLE,
        scope=_CONTAINER_SCOPE,
    )
    assert same != role_assignment_name(
        principal_id="principal-9999",
        role_definition_guid=_BLOB_ROLE,
        scope=_QUEUE_SCOPE,
    )


def test_reapplying_the_same_grants_is_idempotent(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    permissions = [
        _permission(_BLOB_ROLE, _CONTAINER_SCOPE),
        _permission(_QUEUE_ROLE, _QUEUE_SCOPE),
    ]
    driver.create_identity_role("api", permissions=permissions)
    first = dict(fake_authz.role_assignments.assignments)

    driver.create_identity_role("api", permissions=permissions)

    assert fake_authz.role_assignments.assignments == first
    assert len(first) == 2
    assert fake_authz.role_assignments.deleted == []


def test_duplicate_grants_across_bindings_collapse_to_one_assignment(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    driver.create_identity_role(
        "api",
        permissions=[
            _permission(_BLOB_ROLE, _CONTAINER_SCOPE),
            _permission(_BLOB_ROLE, _CONTAINER_SCOPE),
        ],
    )
    assert len(fake_authz.role_assignments.assignments) == 1


def test_grant_scope_outside_the_subscription_is_refused(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    # The legacy blob driver emits a templated '/subscriptions/SUB_ID/...'
    # scope; granting into some other subscription must never look configured.
    foreign = (
        "/subscriptions/SUB_ID/resourceGroups/RG/providers/Microsoft.Storage"
        "/storageAccounts/acct/blobServices/default/containers/data"
    )
    with pytest.raises(AzureRoleAssignmentError, match="outside the identity's subscription"):
        driver.create_identity_role("api", permissions=[_permission(_BLOB_ROLE, foreign)])
    assert fake_authz.role_assignments.create_calls == []


def test_identity_without_a_principal_id_refuses_to_report_a_grant(
    fake_msi: FakeMSI,
    fake_authz: FakeAuthz,
) -> None:
    fake_msi.user_assigned_identities.identities["api"] = FakeUAI(name="api", principal_id="")
    driver = AzureFederatedIdentityDriver(
        config=FederatedIdentityConfig(
            tenant_id="tenant-1",
            subscription_id=_SUB,
            resource_group=_RG,
            cluster_oidc_issuer="https://oidc/abc",
            msi_client=fake_msi,
            authz_client=fake_authz,
        ),
    )
    with pytest.raises(AzureRoleAssignmentError, match="no principalId"):
        driver.create_identity_role("api", permissions=[_permission(_BLOB_ROLE, _CONTAINER_SCOPE)])


def test_unvetted_role_definition_in_permissions_is_refused(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    with pytest.raises(AzureGrantContractError, match="vetted built-in catalog"):
        driver.create_identity_role(
            "api",
            permissions=[_permission("00000000-0000-0000-0000-000000000001", _CONTAINER_SCOPE)],
        )
    assert fake_authz.role_assignments.create_calls == []


# ---- removal: the dangerous direction -------------------------------------


def test_dropping_a_grant_removes_only_that_assignment(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    driver.create_identity_role(
        "api",
        permissions=[
            _permission(_BLOB_ROLE, _CONTAINER_SCOPE),
            _permission(_QUEUE_ROLE, _QUEUE_SCOPE),
        ],
    )

    driver.create_identity_role("api", permissions=[_permission(_BLOB_ROLE, _CONTAINER_SCOPE)])

    remaining = {a.scope for a in fake_authz.role_assignments.assignments.values()}
    assert remaining == {_CONTAINER_SCOPE}
    assert [scope for scope, _name in fake_authz.role_assignments.deleted] == [_QUEUE_SCOPE]


def test_operator_assignment_on_the_same_principal_is_never_deleted(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    operator = fake_authz.role_assignments.seed(
        FakeAssignment(
            name=str(uuid.uuid4()),
            scope=f"/subscriptions/{_SUB}/resourceGroups/{_RG}",
            role_definition_id=_role_definition_id(AZURE_BUILTIN_ROLE_IDS["Reader"]),
            principal_id="principal-9999",
            description="granted by hand during the incident",
        ),
    )
    driver.create_identity_role("api", permissions=[_permission(_BLOB_ROLE, _CONTAINER_SCOPE)])

    driver.create_identity_role("api", permissions=[])

    assert fake_authz.role_assignments.assignments == {(operator.scope, operator.name): operator}


def test_assignment_under_our_derived_name_without_our_marker_is_left_alone(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    # Name collision alone must not read as ownership: an assignment we did not
    # stamp stays put even when its name matches the derivation exactly.
    foreign = fake_authz.role_assignments.seed(
        FakeAssignment(
            name=role_assignment_name(
                principal_id="principal-9999",
                role_definition_guid=_QUEUE_ROLE,
                scope=_QUEUE_SCOPE,
            ),
            scope=_QUEUE_SCOPE,
            role_definition_id=_role_definition_id(_QUEUE_ROLE),
            principal_id="principal-9999",
            description="",
        ),
    )

    driver.create_identity_role("api", permissions=[])

    assert fake_authz.role_assignments.deleted == []
    assert fake_authz.role_assignments.assignments == {(foreign.scope, foreign.name): foreign}


def test_existing_assignment_under_our_name_bound_elsewhere_is_a_hard_error(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    name = role_assignment_name(
        principal_id="principal-9999",
        role_definition_guid=_BLOB_ROLE,
        scope=_CONTAINER_SCOPE,
    )
    fake_authz.role_assignments.seed(
        FakeAssignment(
            name=name,
            scope=_CONTAINER_SCOPE,
            role_definition_id=_role_definition_id(_BLOB_ROLE),
            principal_id="someone-elses-principal",
        ),
    )

    with pytest.raises(AzureRoleAssignmentError, match="another party's assignment"):
        driver.create_identity_role("api", permissions=[_permission(_BLOB_ROLE, _CONTAINER_SCOPE)])


def test_operator_grant_of_the_same_role_and_scope_is_accepted_not_duplicated(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    # Brownfield subscription: the operator already granted exactly what the
    # app declares, under a name of their own. ARM refuses the duplicate, and
    # the workload is authorized, so the reconcile must converge instead of
    # failing every deploy forever.
    operator = fake_authz.role_assignments.seed(
        FakeAssignment(
            name=str(uuid.uuid4()),
            scope=_CONTAINER_SCOPE,
            role_definition_id=_role_definition_id(_BLOB_ROLE),
            principal_id="principal-9999",
            description="granted by hand before onboarding",
        ),
    )

    driver.create_identity_role("api", permissions=[_permission(_BLOB_ROLE, _CONTAINER_SCOPE)])

    assert fake_authz.role_assignments.assignments == {(operator.scope, operator.name): operator}

    # Accepting their assignment is not adopting it: dropping the grant leaves
    # the operator's row alone, because its name is not one we can re-derive.
    driver.create_identity_role("api", permissions=[])

    assert fake_authz.role_assignments.deleted == []
    assert fake_authz.role_assignments.assignments == {(operator.scope, operator.name): operator}


def test_conflict_with_no_matching_assignment_is_raised_not_swallowed(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    # A conflict we cannot explain by an existing equivalent grant means the
    # workload may hold no authorization at all; report it.
    fake_authz.role_assignments.conflict_on_create = True

    with pytest.raises(ProviderError):
        driver.create_identity_role("api", permissions=[_permission(_BLOB_ROLE, _CONTAINER_SCOPE)])


def test_aws_policy_statements_are_refused_by_the_azure_driver(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    # The original bug: AWS-shaped statements reached this driver and were
    # dropped, so the binding reported ready while granting nothing.
    with pytest.raises(AzureGrantContractError):
        driver.create_identity_role(
            "api",
            permissions=[
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject"],
                    "Resource": "arn:aws:s3:::astrolift-data/*",
                },
            ],
        )

    assert fake_authz.role_assignments.create_calls == []


def test_nothing_is_deleted_when_the_assignment_listing_fails(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    driver.create_identity_role("api", permissions=[_permission(_BLOB_ROLE, _CONTAINER_SCOPE)])
    fake_authz.role_assignments.list_error = _NotFound("throttled")

    with pytest.raises(ProviderError):
        driver.create_identity_role("api", permissions=[])

    assert fake_authz.role_assignments.deleted == []


# ---- attach_policy --------------------------------------------------------


def test_attach_policy_assigns_the_role_at_the_resource_group(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    driver.create_identity_role("api", permissions=[])

    driver.attach_policy("api", "Storage Blob Data Reader")

    (scope, _name, props) = fake_authz.role_assignments.create_calls[0]
    assert scope == f"/subscriptions/{_SUB}/resourceGroups/{_RG}"
    assert props["roleDefinitionId"] == _role_definition_id(
        AZURE_BUILTIN_ROLE_IDS["Storage Blob Data Reader"],
    )
    assert props["principalId"] == "principal-9999"


def test_attach_policy_rejects_a_control_plane_action(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    driver.create_identity_role("api", permissions=[])

    with pytest.raises(AzureGrantContractError, match="grants the workload nothing"):
        driver.attach_policy("api", "Microsoft.KeyVault/vaults/secrets/getSecret")

    assert fake_authz.role_assignments.create_calls == []


def test_attach_policy_is_idempotent(
    driver: AzureFederatedIdentityDriver,
    fake_authz: FakeAuthz,
) -> None:
    driver.create_identity_role("api", permissions=[])
    driver.attach_policy("api", "Reader")
    driver.attach_policy("api", "Reader")

    assert len(fake_authz.role_assignments.assignments) == 1


# ---- deletion -------------------------------------------------------------


def test_delete_identity_strips_our_grants_before_removing_the_principal(
    driver: AzureFederatedIdentityDriver,
    fake_msi: FakeMSI,
    fake_authz: FakeAuthz,
) -> None:
    driver.create_identity_role("doomed", permissions=[_permission(_BLOB_ROLE, _CONTAINER_SCOPE)])

    driver.delete_identity_role("doomed")

    assert "doomed" not in fake_msi.user_assigned_identities.identities
    assert fake_authz.role_assignments.assignments == {}
    assert fake_authz.role_assignments.deleted == [
        (_CONTAINER_SCOPE, _blob_assignment_name(_CONTAINER_SCOPE)),
    ]


def test_delete_missing_raises_not_found(
    driver: AzureFederatedIdentityDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.delete_identity_role("never")
