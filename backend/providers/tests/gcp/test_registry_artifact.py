"""Tests for ArtifactRegistryDriver (#40, #763)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from _sdk import UnsupportedOperationError
from gcp._errors import NotFoundError
from gcp.registry_artifact import (
    ArtifactRegistryConfig,
    ArtifactRegistryDriver,
)


class _NotFound(Exception):  # noqa: N818 — mocks google.cloud exception name verbatim
    pass


class _AlreadyExists(Exception):  # noqa: N818 — mocks google.cloud exception name verbatim
    pass


@dataclass
class FakeRepository:
    name: str


@dataclass
class FakeTag:
    name: str
    version: str = ""


@dataclass
class FakeOperation:
    def result(self) -> None:
        return None


@dataclass
class FakeARClient:
    repos: dict[str, FakeRepository] = field(default_factory=dict)
    tags: dict[str, list[FakeTag]] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)

    def get_repository(self, *, name: str) -> FakeRepository:
        if name not in self.repos:
            raise _NotFound(name)
        return self.repos[name]

    def create_repository(
        self, *, parent: str, repository: Any, repository_id: str,
    ) -> FakeOperation:
        full = f"{parent}/repositories/{repository_id}"
        self.repos[full] = FakeRepository(name=full)
        self.create_calls.append({
            "parent": parent, "repository_id": repository_id,
            "repository": repository,
        })
        return FakeOperation()

    def list_tags(self, *, parent: str) -> list[FakeTag]:
        return self.tags.get(parent, [])


@pytest.fixture(autouse=True)
def patch_ar_module(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    fake_module = types.ModuleType("google.cloud.artifactregistry_v1")

    class _RepoStub:
        class Format:
            DOCKER = "DOCKER"

        class DockerRepositoryConfig:
            def __init__(self, *, immutable_tags: bool = False) -> None:
                self.immutable_tags = immutable_tags

        def __init__(self, *, format_: str = "DOCKER") -> None:
            self.format_ = format_
            self.docker_config: Any = None
            self.kms_key_name: str | None = None

    fake_module.Repository = _RepoStub
    fake_module.ArtifactRegistryClient = lambda: None

    google_module = sys.modules.get("google", types.ModuleType("google"))
    cloud_module = sys.modules.get(
        "google.cloud", types.ModuleType("google.cloud"),
    )
    sys.modules.setdefault("google", google_module)
    sys.modules.setdefault("google.cloud", cloud_module)
    sys.modules["google.cloud.artifactregistry_v1"] = fake_module


@pytest.fixture
def fake_client() -> FakeARClient:
    _NotFound.__name__ = "NotFound"
    return FakeARClient()


@pytest.fixture
def driver(fake_client: FakeARClient) -> ArtifactRegistryDriver:
    return ArtifactRegistryDriver(
        config=ArtifactRegistryConfig(
            project_id="acme",
            location="us-central1",
            repository_id="astrolift-images",
            client=fake_client,
        ),
    )


def test_ensure_repo_creates_ar_repo_when_missing(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    repo = driver.ensure_repo("api")
    assert repo.name == "api"
    assert "us-central1-docker.pkg.dev/acme/astrolift-images/api" in repo.uri
    assert len(fake_client.create_calls) == 1
    assert fake_client.create_calls[0]["repository_id"] == "astrolift-images"


def test_ensure_repo_idempotent(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    driver.ensure_repo("api")
    driver.ensure_repo("api")  # second call hits the existing AR repo
    assert len(fake_client.create_calls) == 1


def test_ensure_repo_different_apps_share_ar_repo(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    a = driver.ensure_repo("api")
    b = driver.ensure_repo("worker")
    assert a.uri.endswith("/api")
    assert b.uri.endswith("/worker")
    # Only the underlying AR repo got created once
    assert len(fake_client.create_calls) == 1


def test_get_pull_secret_marker_for_gke(
    driver: ArtifactRegistryDriver,
) -> None:
    secret = driver.get_pull_secret(
        cluster="prod", namespace="acme-api",
    )
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["namespace"] == "acme-api"
    assert (
        secret["metadata"]["annotations"]["astrolift.io/note"]
        .startswith("GKE clusters use Workload Identity")
    )
    docker = json.loads(
        base64.b64decode(secret["data"][".dockerconfigjson"]).decode(),
    )
    assert docker == {"auths": {}}


def test_push_returns_full_uri(
    driver: ArtifactRegistryDriver,
) -> None:
    uri = driver.push("local:dev", "api", "abc123")
    assert uri == (
        "us-central1-docker.pkg.dev/acme/astrolift-images/api:abc123"
    )


def test_push_rejects_blank_local_image(
    driver: ArtifactRegistryDriver,
) -> None:
    with pytest.raises(ValueError, match="local_image"):
        driver.push("", "api", "abc123")


def test_list_tags_translates_response(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    package_path = (
        "projects/acme/locations/us-central1"
        "/repositories/astrolift-images/packages/api"
    )
    fake_client.tags[package_path] = [
        FakeTag(
            name=f"{package_path}/tags/v1",
            version=(
                "projects/acme/locations/us-central1"
                "/repositories/astrolift-images/packages/api/versions/sha256:abc"
            ),
        ),
    ]
    tags = driver.list_tags("api")
    assert len(tags) == 1
    assert tags[0].name == "v1"
    assert tags[0].digest == "sha256:abc"


def test_list_tags_missing_package_raises_not_found(
    driver: ArtifactRegistryDriver,
) -> None:
    # FakeARClient.list_tags returns [] for unknown parents — but
    # we want to assert NotFound surfaces for genuinely-missing paths.
    # Patch the method to raise.
    def raise_nf(*args: Any, **kw: Any) -> Any:
        raise _NotFound("nope")

    driver._client.list_tags = raise_nf  # type: ignore[assignment]
    with pytest.raises(NotFoundError):
        driver.list_tags("never")


def test_delete_repo_archive_is_noop(
    driver: ArtifactRegistryDriver, fake_client: FakeARClient,
) -> None:
    # archive=True doesn't touch any AR state
    driver.delete_repo("api", archive=True)
    assert fake_client.repos == {}


def test_delete_repo_force_not_implemented(
    driver: ArtifactRegistryDriver,
) -> None:
    with pytest.raises(NotImplementedError):
        driver.delete_repo("api", archive=False)


# --- ensure_ci_push_role (#763) -----------------------------------


@dataclass
class _FakeBinding:
    role: str
    members: list[str] = field(default_factory=list)


class _FakeBindingList(list):
    def add(self, **kwargs: Any) -> _FakeBinding:
        b = _FakeBinding(
            role=kwargs["role"],
            members=list(kwargs.get("members", [])),
        )
        self.append(b)
        return b


@dataclass
class _FakePolicy:
    bindings: _FakeBindingList = field(default_factory=_FakeBindingList)


@dataclass
class _FakeServiceAccount:
    email: str


@dataclass
class _FakeARIAMClient(FakeARClient):
    """AR client extended with IAM policy CRUD for the AR repo path."""

    iam_policies: dict[str, _FakePolicy] = field(default_factory=dict)
    set_iam_calls: list[tuple[str, _FakePolicy]] = field(default_factory=list)

    def get_iam_policy(self, *, resource: str) -> _FakePolicy:
        return self.iam_policies.setdefault(resource, _FakePolicy())

    def set_iam_policy(
        self,
        *,
        resource: str,
        policy: _FakePolicy,
    ) -> _FakePolicy:
        self.iam_policies[resource] = policy
        self.set_iam_calls.append((resource, policy))
        return policy


@dataclass
class _FakeIAMClient:
    """Stand-in for ``iam_admin_v1.IAMClient`` covering SA create +
    SA IAM policy CRUD — the surface the driver exercises."""

    service_accounts: dict[str, _FakeServiceAccount] = field(default_factory=dict)
    policies: dict[str, _FakePolicy] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    set_iam_calls: list[tuple[str, _FakePolicy]] = field(default_factory=list)

    def create_service_account(
        self,
        *,
        name: str,
        account_id: str,
        service_account: dict[str, Any],
    ) -> _FakeServiceAccount:
        self.create_calls.append(
            {
                "name": name,
                "account_id": account_id,
                "service_account": service_account,
            }
        )
        if account_id in self.service_accounts:
            raise _AlreadyExists(account_id)
        project = name.split("/", 1)[1]
        email = f"{account_id}@{project}.iam.gserviceaccount.com"
        sa = _FakeServiceAccount(email=email)
        self.service_accounts[account_id] = sa
        return sa

    def get_iam_policy(self, *, resource: str) -> _FakePolicy:
        return self.policies.setdefault(resource, _FakePolicy())

    def set_iam_policy(
        self,
        *,
        resource: str,
        policy: _FakePolicy,
    ) -> _FakePolicy:
        self.policies[resource] = policy
        self.set_iam_calls.append((resource, policy))
        return policy


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def json(self) -> dict[str, Any]:
        return self.payload


@dataclass
class _FakeWipClient:
    """Stand-in ``AuthorizedSession``. Tracks GET/POST against the
    WIF REST surface. POSTs flip a 404 to 200 for the matching
    pool/provider, mirroring real-API create-then-get semantics."""

    pools: dict[str, dict[str, Any]] = field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    get_calls: list[str] = field(default_factory=list)
    post_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def get(self, url: str) -> _FakeResponse:
        self.get_calls.append(url)
        pool = self._match_pool(url)
        if pool is not None:
            return _FakeResponse(
                status_code=200 if pool in self.pools else 404,
                payload=self.pools.get(pool, {}),
            )
        provider = self._match_provider(url)
        if provider is not None:
            return _FakeResponse(
                status_code=200 if provider in self.providers else 404,
                payload=self.providers.get(provider, {}),
            )
        return _FakeResponse(status_code=404)

    def post(
        self,
        url: str,
        json: dict[str, Any] | None = None,
    ) -> _FakeResponse:
        self.post_calls.append((url, json or {}))
        pool = self._match_pool_create(url)
        if pool is not None:
            self.pools[pool] = json or {}
            return _FakeResponse(status_code=200, payload={"name": pool})
        provider = self._match_provider_create(url)
        if provider is not None:
            self.providers[provider] = json or {}
            return _FakeResponse(status_code=200, payload={"name": provider})
        return _FakeResponse(status_code=400)

    @staticmethod
    def _match_pool(url: str) -> str | None:
        marker = "/workloadIdentityPools/"
        if marker not in url or "/providers/" in url:
            return None
        return url.split(marker, 1)[1]

    @staticmethod
    def _match_provider(url: str) -> str | None:
        marker = "/providers/"
        if "/workloadIdentityPools/" not in url or marker not in url:
            return None
        return url.split(marker, 1)[1]

    @staticmethod
    def _match_pool_create(url: str) -> str | None:
        if "workloadIdentityPoolId=" not in url:
            return None
        return url.split("workloadIdentityPoolId=", 1)[1]

    @staticmethod
    def _match_provider_create(url: str) -> str | None:
        if "workloadIdentityPoolProviderId=" not in url:
            return None
        return url.split("workloadIdentityPoolProviderId=", 1)[1]


@dataclass
class _FakeProject:
    name: str


@dataclass
class _FakeProjectsClient:
    project_number: str = "123456789"

    def get_project(self, *, name: str) -> _FakeProject:
        return _FakeProject(name=f"projects/{self.project_number}")


@pytest.fixture
def fake_ar_iam_client() -> _FakeARIAMClient:
    _NotFound.__name__ = "NotFound"
    _AlreadyExists.__name__ = "AlreadyExists"
    return _FakeARIAMClient()


@pytest.fixture
def fake_iam_client() -> _FakeIAMClient:
    _AlreadyExists.__name__ = "AlreadyExists"
    return _FakeIAMClient()


@pytest.fixture
def fake_wip_client() -> _FakeWipClient:
    return _FakeWipClient()


@pytest.fixture(autouse=True)
def patch_resourcemanager(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    fake_module = types.ModuleType("google.cloud.resourcemanager_v3")
    fake_module.ProjectsClient = _FakeProjectsClient  # type: ignore[attr-defined]
    google_module = sys.modules.get("google", types.ModuleType("google"))
    cloud_module = sys.modules.get(
        "google.cloud",
        types.ModuleType("google.cloud"),
    )
    sys.modules.setdefault("google", google_module)
    sys.modules.setdefault("google.cloud", cloud_module)
    sys.modules["google.cloud.resourcemanager_v3"] = fake_module


@pytest.fixture
def push_driver(
    fake_ar_iam_client: _FakeARIAMClient,
    fake_iam_client: _FakeIAMClient,
    fake_wip_client: _FakeWipClient,
) -> ArtifactRegistryDriver:
    return ArtifactRegistryDriver(
        config=ArtifactRegistryConfig(
            project_id="acme",
            location="us-central1",
            repository_id="astrolift-images",
            client=fake_ar_iam_client,
            iam_client=fake_iam_client,
            wip_client=fake_wip_client,
        ),
    )


def test_ensure_ci_push_role_creates_new(
    push_driver: ArtifactRegistryDriver,
    fake_ar_iam_client: _FakeARIAMClient,
    fake_iam_client: _FakeIAMClient,
    fake_wip_client: _FakeWipClient,
) -> None:
    role = push_driver.ensure_ci_push_role(
        repo="api",
        scm_provider="github",
        scm_repo_full_name="acme-co/api",
    )

    assert role.scm_provider == "github"
    payload = json.loads(role.role_ref)
    assert payload["sa_email"] == "astrolift-api-ar-push@acme.iam.gserviceaccount.com"
    assert payload["wip_provider"] == (
        "projects/acme/locations/global/workloadIdentityPools/astrolift-github-actions/providers/github-oidc"
    )

    pool_creates = [
        url for url, _ in fake_wip_client.post_calls if "workloadIdentityPoolId=astrolift-github-actions" in url
    ]
    assert len(pool_creates) == 1
    provider_creates = [
        url for url, _ in fake_wip_client.post_calls if "workloadIdentityPoolProviderId=github-oidc" in url
    ]
    assert len(provider_creates) == 1

    _, provider_body = next(
        (u, body) for u, body in fake_wip_client.post_calls if "workloadIdentityPoolProviderId=github-oidc" in u
    )
    assert provider_body["oidc"]["issuerUri"] == "https://token.actions.githubusercontent.com"
    assert provider_body["attributeMapping"] == {
        "google.subject": "assertion.sub",
        "attribute.repository": "assertion.repository",
    }

    assert len(fake_iam_client.create_calls) == 1
    assert fake_iam_client.create_calls[0]["account_id"] == "astrolift-api-ar-push"

    ar_resource = "projects/acme/locations/us-central1/repositories/astrolift-images"
    ar_policy = fake_ar_iam_client.iam_policies[ar_resource]
    writer = next(b for b in ar_policy.bindings if b.role == "roles/artifactregistry.writer")
    assert "serviceAccount:astrolift-api-ar-push@acme.iam.gserviceaccount.com" in writer.members

    sa_resource = "projects/-/serviceAccounts/astrolift-api-ar-push@acme.iam.gserviceaccount.com"
    sa_policy = fake_iam_client.policies[sa_resource]
    wif_binding = next(b for b in sa_policy.bindings if b.role == "roles/iam.workloadIdentityUser")
    expected_member = (
        "principalSet://iam.googleapis.com/projects/123456789"
        "/locations/global/workloadIdentityPools/astrolift-github-actions"
        "/attribute.repository/acme-co/api"
    )
    assert expected_member in wif_binding.members


def test_ensure_ci_push_role_idempotent(
    push_driver: ArtifactRegistryDriver,
    fake_ar_iam_client: _FakeARIAMClient,
    fake_iam_client: _FakeIAMClient,
    fake_wip_client: _FakeWipClient,
) -> None:
    first = push_driver.ensure_ci_push_role(
        repo="api",
        scm_provider="github",
        scm_repo_full_name="acme-co/api",
    )
    pre_calls = {
        "pool_posts": len(fake_wip_client.post_calls),
        "sa_creates": len(fake_iam_client.create_calls),
        "ar_set": len(fake_ar_iam_client.set_iam_calls),
        "sa_set": len(fake_iam_client.set_iam_calls),
    }

    second = push_driver.ensure_ci_push_role(
        repo="api",
        scm_provider="github",
        scm_repo_full_name="acme-co/api",
    )

    assert second == first

    # No new WIF pool / provider creates.
    assert len(fake_wip_client.post_calls) == pre_calls["pool_posts"]

    # SA create was attempted again (driver swallows AlreadyExists),
    # but no duplicate SA materialized.
    assert len(fake_iam_client.create_calls) == pre_calls["sa_creates"] + 1
    assert len(fake_iam_client.service_accounts) == 1

    # Bindings short-circuit BEFORE set_iam_policy when the member is already
    # present, so the set counter is unchanged.
    assert len(fake_ar_iam_client.set_iam_calls) == pre_calls["ar_set"]
    assert len(fake_iam_client.set_iam_calls) == pre_calls["sa_set"]

    ar_resource = "projects/acme/locations/us-central1/repositories/astrolift-images"
    writer = next(
        b for b in fake_ar_iam_client.iam_policies[ar_resource].bindings if b.role == "roles/artifactregistry.writer"
    )
    assert len(writer.members) == 1

    sa_resource = "projects/-/serviceAccounts/astrolift-api-ar-push@acme.iam.gserviceaccount.com"
    wif_binding = next(
        b for b in fake_iam_client.policies[sa_resource].bindings if b.role == "roles/iam.workloadIdentityUser"
    )
    assert len(wif_binding.members) == 1


def test_ensure_ci_push_role_unsupported_scm(
    push_driver: ArtifactRegistryDriver,
) -> None:
    with pytest.raises(UnsupportedOperationError):
        push_driver.ensure_ci_push_role(
            repo="api",
            scm_provider="gitlab",
            scm_repo_full_name="acme-co/api",
        )
