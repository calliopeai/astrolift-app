"""Tests for GCPWorkloadIdentityDriver (#39)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from gcp._errors import NotFoundError
from gcp.identity_wi import (
    GCPWIConfig,
    GCPWorkloadIdentityDriver,
    _ResourceManagerIAMClient,
    service_account_email_for,
    service_account_id_for,
)


class _NotFound(Exception):
    pass


class _AlreadyExists(Exception):
    pass


@dataclass
class FakeBinding:
    role: str
    members: list[str] = field(default_factory=list)


@dataclass
class FakePolicy:
    bindings: FakeBindingList


class FakeBindingList(list):
    def add(self, **kwargs: Any) -> FakeBinding:
        b = FakeBinding(
            role=kwargs["role"],
            members=list(kwargs.get("members", [])),
        )
        self.append(b)
        return b


@dataclass
class FakeServiceAccount:
    email: str


@dataclass
class FakeIAMClient:
    service_accounts: dict[str, FakeServiceAccount] = field(
        default_factory=dict,
    )
    policies: dict[str, FakePolicy] = field(default_factory=dict)

    def get_iam_policy(self, *, resource: str) -> FakePolicy:
        return self.policies.setdefault(
            resource,
            FakePolicy(bindings=FakeBindingList()),
        )

    def set_iam_policy(self, *, resource: str, policy: FakePolicy) -> None:
        self.policies[resource] = policy

    def create_service_account(
        self,
        *,
        name: str,
        account_id: str,
        service_account: dict[str, Any],
    ) -> FakeServiceAccount:
        if account_id in self.service_accounts:
            raise _AlreadyExists(account_id)
        # The driver uses self._config.project_id when emailing; we
        # mirror the driver's _sa_email shape for symmetry.
        project = name.split("/", 1)[1]
        email = f"{account_id}@{project}.iam.gserviceaccount.com"
        sa = FakeServiceAccount(email=email)
        self.service_accounts[account_id] = sa
        return sa

    def delete_service_account(self, *, name: str) -> None:
        # name shape: projects/-/serviceAccounts/<email>
        email = name.rsplit("/", 1)[-1]
        sa_id = email.split("@")[0]
        if sa_id not in self.service_accounts:
            raise _NotFound(name)
        del self.service_accounts[sa_id]


@dataclass
class FakeProjectIAMClient:
    policy: dict[str, Any] = field(
        default_factory=lambda: {"version": 1, "etag": "etag-1", "bindings": []},
    )
    set_calls: list[dict[str, Any]] = field(default_factory=list)

    def get_project_iam_policy(self, *, project_id: str) -> dict[str, Any]:
        return {
            **self.policy,
            "bindings": [dict(binding) for binding in self.policy["bindings"]],
        }

    def set_project_iam_policy(
        self,
        *,
        project_id: str,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        self.policy = policy
        self.set_calls.append(policy)
        return policy


@pytest.fixture
def fake_iam() -> FakeIAMClient:
    _NotFound.__name__ = "NotFound"
    _AlreadyExists.__name__ = "AlreadyExists"
    return FakeIAMClient()


@pytest.fixture
def driver(fake_iam: FakeIAMClient) -> GCPWorkloadIdentityDriver:
    return GCPWorkloadIdentityDriver(
        config=GCPWIConfig(
            project_id="acme",
            iam_client=fake_iam,
            project_iam_client=FakeProjectIAMClient(),
        ),
    )


def test_create_identity_role_creates_sa(
    driver: GCPWorkloadIdentityDriver,
    fake_iam: FakeIAMClient,
) -> None:
    email = driver.create_identity_role("my-role", permissions=[])
    assert email == "my-role@acme.iam.gserviceaccount.com"
    assert "my-role" in fake_iam.service_accounts


def test_create_identity_role_idempotent(
    driver: GCPWorkloadIdentityDriver,
) -> None:
    driver.create_identity_role("dupe", permissions=[])
    # Second call hits AlreadyExists path → returns email
    email = driver.create_identity_role("dupe", permissions=[])
    assert email == service_account_email_for("dupe", "acme")


def test_create_identity_role_reconciles_project_roles_after_already_exists(
    fake_iam: FakeIAMClient,
) -> None:
    project_iam = FakeProjectIAMClient()
    driver = GCPWorkloadIdentityDriver(
        config=GCPWIConfig(
            project_id="acme",
            iam_client=fake_iam,
            project_iam_client=project_iam,
        ),
    )
    driver.create_identity_role("service-account", permissions=[])
    driver.create_identity_role(
        "service-account",
        permissions=[{"role": "roles/pubsub.subscriber"}],
    )

    assert project_iam.policy["etag"] == "etag-1"
    assert project_iam.policy["version"] == 3
    assert project_iam.policy["bindings"] == [
        {
            "role": "roles/pubsub.subscriber",
            "members": [
                "serviceAccount:service-account@acme.iam.gserviceaccount.com",
            ],
        },
    ]


def test_create_identity_role_deduplicates_project_roles(
    fake_iam: FakeIAMClient,
) -> None:
    project_iam = FakeProjectIAMClient()
    driver = GCPWorkloadIdentityDriver(
        config=GCPWIConfig(
            project_id="acme",
            iam_client=fake_iam,
            project_iam_client=project_iam,
        ),
    )
    driver.create_identity_role(
        "service-account",
        permissions=[
            {"role": "roles/storage.objectViewer"},
            {"role": "roles/storage.objectViewer"},
        ],
    )
    driver.create_identity_role(
        "service-account",
        permissions=[{"role": "roles/storage.objectViewer"}],
    )
    assert len(project_iam.set_calls) == 1


@pytest.mark.parametrize(
    "permission",
    [
        {"Action": ["pubsub.subscriptions.consume"]},
        {"role": "pubsub.subscriptions.consume"},
        {"role": "roles/with spaces"},
    ],
)
def test_create_identity_role_rejects_non_role_permissions(
    driver: GCPWorkloadIdentityDriver,
    permission: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="valid predefined or custom IAM role"):
        driver.create_identity_role("invalid-role-test", permissions=[permission])


def test_bind_service_account_returns_annotation(
    driver: GCPWorkloadIdentityDriver,
    fake_iam: FakeIAMClient,
) -> None:
    driver.create_identity_role("api", permissions=[])
    annotations = driver.bind_service_account(
        cluster="prod",
        namespace="acme-api",
        sa_name="api-sa",
        identity_role="api",
    )
    assert annotations["iam.gke.io/gcp-service-account"] == service_account_email_for(
        "api",
        "acme",
    )


def test_bind_creates_workload_identity_user_binding(
    driver: GCPWorkloadIdentityDriver,
    fake_iam: FakeIAMClient,
) -> None:
    driver.create_identity_role("api", permissions=[])
    driver.bind_service_account(
        cluster="prod",
        namespace="ns",
        sa_name="sa",
        identity_role="api",
    )
    sa_resource = f"projects/-/serviceAccounts/{service_account_email_for('api', 'acme')}"
    policy = fake_iam.policies[sa_resource]
    binding = next(b for b in policy.bindings if b.role == "roles/iam.workloadIdentityUser")
    assert "serviceAccount:acme.svc.id.goog[ns/sa]" in binding.members


def test_bind_appends_to_existing_binding(
    driver: GCPWorkloadIdentityDriver,
    fake_iam: FakeIAMClient,
) -> None:
    driver.create_identity_role("api", permissions=[])
    driver.bind_service_account(
        cluster="c",
        namespace="ns1",
        sa_name="sa1",
        identity_role="api",
    )
    driver.bind_service_account(
        cluster="c",
        namespace="ns2",
        sa_name="sa2",
        identity_role="api",
    )
    sa_resource = f"projects/-/serviceAccounts/{service_account_email_for('api', 'acme')}"
    policy = fake_iam.policies[sa_resource]
    bindings = [b for b in policy.bindings if b.role == "roles/iam.workloadIdentityUser"]
    assert len(bindings) == 1
    assert len(bindings[0].members) == 2


def test_delete_identity_role_raises_for_missing(
    driver: GCPWorkloadIdentityDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.delete_identity_role("never")


def test_delete_removes_sa(
    driver: GCPWorkloadIdentityDriver,
    fake_iam: FakeIAMClient,
) -> None:
    driver.create_identity_role("doomed", permissions=[])
    driver.delete_identity_role("doomed")
    assert "doomed" not in fake_iam.service_accounts


def test_sa_email_format() -> None:
    driver = GCPWorkloadIdentityDriver(
        config=GCPWIConfig(
            project_id="proj-123",
            iam_client=FakeIAMClient(),
        ),
    )
    assert driver._sa_email(name="x") == service_account_email_for("x", "proj-123")


def test_service_account_id_preserves_valid_names_and_hashes_long_names() -> None:
    assert service_account_id_for("valid-service-account") == "valid-service-account"
    first = service_account_id_for("astrolift-very-long-organization-first-app")
    second = service_account_id_for("astrolift-very-long-organization-second-app")
    assert len(first) <= 30
    assert len(second) <= 30
    assert first != second
    assert first[0].isalpha() and first[-1].isalnum()


@dataclass
class _Response:
    payload: dict[str, Any]

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


@dataclass
class _Session:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response({"version": 3, "etag": "etag", "bindings": []})


def test_resource_manager_adapter_uses_authenticated_iam_action_endpoints() -> None:
    session = _Session()
    client = _ResourceManagerIAMClient(session=session)
    policy = client.get_project_iam_policy(project_id="acme")
    client.set_project_iam_policy(project_id="acme", policy=policy)

    assert session.calls[0]["url"].endswith("/projects/acme:getIamPolicy")
    assert session.calls[0]["json"]["options"]["requestedPolicyVersion"] == 3
    assert session.calls[1]["url"].endswith("/projects/acme:setIamPolicy")
    assert session.calls[1]["json"]["policy"]["etag"] == "etag"
    assert session.calls[1]["json"]["updateMask"] == "bindings,etag,version"
