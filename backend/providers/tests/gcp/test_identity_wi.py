"""Tests for GCPWorkloadIdentityDriver (#39)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from gcp._errors import NotFoundError
from gcp.identity_wi import GCPWIConfig, GCPWorkloadIdentityDriver


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
            role=kwargs["role"], members=list(kwargs.get("members", [])),
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
            resource, FakePolicy(bindings=FakeBindingList()),
        )

    def set_iam_policy(self, *, resource: str, policy: FakePolicy) -> None:
        self.policies[resource] = policy

    def create_service_account(
        self, *, name: str, account_id: str, service_account: dict[str, Any],
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


@pytest.fixture
def fake_iam() -> FakeIAMClient:
    _NotFound.__name__ = "NotFound"
    _AlreadyExists.__name__ = "AlreadyExists"
    return FakeIAMClient()


@pytest.fixture
def driver(fake_iam: FakeIAMClient) -> GCPWorkloadIdentityDriver:
    return GCPWorkloadIdentityDriver(
        config=GCPWIConfig(project_id="acme", iam_client=fake_iam),
    )


def test_create_identity_role_creates_sa(
    driver: GCPWorkloadIdentityDriver, fake_iam: FakeIAMClient,
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
    assert email == "dupe@acme.iam.gserviceaccount.com"


def test_bind_service_account_returns_annotation(
    driver: GCPWorkloadIdentityDriver, fake_iam: FakeIAMClient,
) -> None:
    driver.create_identity_role("api", permissions=[])
    annotations = driver.bind_service_account(
        cluster="prod",
        namespace="acme-api",
        sa_name="api-sa",
        identity_role="api",
    )
    assert annotations["iam.gke.io/gcp-service-account"] == (
        "api@acme.iam.gserviceaccount.com"
    )


def test_bind_creates_workload_identity_user_binding(
    driver: GCPWorkloadIdentityDriver, fake_iam: FakeIAMClient,
) -> None:
    driver.create_identity_role("api", permissions=[])
    driver.bind_service_account(
        cluster="prod", namespace="ns",
        sa_name="sa", identity_role="api",
    )
    sa_resource = (
        "projects/-/serviceAccounts/api@acme.iam.gserviceaccount.com"
    )
    policy = fake_iam.policies[sa_resource]
    binding = next(
        b for b in policy.bindings
        if b.role == "roles/iam.workloadIdentityUser"
    )
    assert (
        "serviceAccount:acme.svc.id.goog[ns/sa]" in binding.members
    )


def test_bind_appends_to_existing_binding(
    driver: GCPWorkloadIdentityDriver, fake_iam: FakeIAMClient,
) -> None:
    driver.create_identity_role("api", permissions=[])
    driver.bind_service_account(
        cluster="c", namespace="ns1", sa_name="sa1", identity_role="api",
    )
    driver.bind_service_account(
        cluster="c", namespace="ns2", sa_name="sa2", identity_role="api",
    )
    sa_resource = (
        "projects/-/serviceAccounts/api@acme.iam.gserviceaccount.com"
    )
    policy = fake_iam.policies[sa_resource]
    bindings = [
        b for b in policy.bindings
        if b.role == "roles/iam.workloadIdentityUser"
    ]
    assert len(bindings) == 1
    assert len(bindings[0].members) == 2


def test_delete_identity_role_raises_for_missing(
    driver: GCPWorkloadIdentityDriver,
) -> None:
    with pytest.raises(NotFoundError):
        driver.delete_identity_role("never")


def test_delete_removes_sa(
    driver: GCPWorkloadIdentityDriver, fake_iam: FakeIAMClient,
) -> None:
    driver.create_identity_role("doomed", permissions=[])
    driver.delete_identity_role("doomed")
    assert "doomed" not in fake_iam.service_accounts


def test_sa_email_format() -> None:
    driver = GCPWorkloadIdentityDriver(
        config=GCPWIConfig(
            project_id="proj-123", iam_client=FakeIAMClient(),
        ),
    )
    assert driver._sa_email(name="x") == (
        "x@proj-123.iam.gserviceaccount.com"
    )
