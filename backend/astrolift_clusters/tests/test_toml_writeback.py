"""Tests for DB -> TOML write-back of cluster ingress auth config (#853).

The SCM round-trip (``fetch_file`` / ``put_file`` / ``open_pull_request``)
is injected through the service's ``fetch`` / ``put`` / ``open_pr`` seams
so these tests drive every branch — enable, disable, no-op, no-connection,
non-git source, and the protected-branch PR fallback — without touching
the network. The DB is real (no model mocks), matching the rest of the
cluster suite.
"""

from __future__ import annotations

import tomllib
import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.services.toml_writeback import (
    apply_ingress_auth,
    ingress_auth_section_from_db,
    write_auth_config_for_cluster,
    write_auth_config_to_toml,
)
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError
from core.secrets import encrypt_at_rest

pytestmark = pytest.mark.django_db


AUTH_CONFIG = {
    "user_pool_arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_abc",
    "user_pool_client_id": "client-abc",
    "user_pool_domain": "acme-auth",
}

_BASE_TOML = """\
name = "hello"

[ingress]
class = "alb"

[[workloads]]
name = "web"
kind = "deployment"
"""

_TOML_WITH_AUTH = """\
name = "hello"

[ingress]
class = "alb"

[ingress.auth]
kind = "cognito"
user_pool_arn = "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_abc"
user_pool_client_id = "client-abc"
user_pool_domain = "acme-auth"

[[workloads]]
name = "web"
kind = "deployment"
"""


# ---- fixtures ------------------------------------------------------


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def team(org):
    return Team.objects.create(organization=org, name="Platform", slug=f"plat-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    # bulk_create bypasses BaseCoreModel.save() (ProviderPlugin.version
    # is a CharField that shadows the base int version) — same trick the
    # ingress_reconcile suite uses.
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="aws", slug=f"aws-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    return p


@pytest.fixture
def cluster(org, plugin):
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={},
        region="us-west-2",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        ingress_class="alb",
        alb_auth_config=AUTH_CONFIG,
    )


def _make_app(
    org,
    team,
    *,
    slug: str,
    source_kind: str = "github",
    source_repo: str = "acme/hello",
) -> RegisteredApp:
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        name=slug,
        slug=slug,
        source_kind=source_kind,
        source_repo=source_repo,
        manifest_path="astrolift.toml",
        default_branch="main",
        deploy_branch="main",
        provisioning_status="ready",
    )


def _make_connection(org, *, kind=SourceConnection.Kind.GITHUB_OAUTH_USER) -> SourceConnection:
    encrypted = encrypt_at_rest(b"gho_token_never_hits_the_network")
    return SourceConnection.objects.create(
        organization=org,
        kind=kind,
        display_name="GitHub",
        account_login="acme",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def _bind_env(app: RegisteredApp, cluster: TenantCluster, *, name: str = "prod") -> AppEnvironment:
    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name=name,
        required_approvals=0,
    )


class _RecordingPut:
    """Capture every ``put_file`` call so the test can assert on the
    branch + content that was committed."""

    def __init__(self, *, fail_protected_on: str | None = None):
        self.calls: list[dict] = []
        # When set, raise a protected-branch error for a put targeting
        # this branch (drives the PR fallback).
        self._fail_protected_on = fail_protected_on

    def __call__(self, connection, *, repo_full_name, path, branch, content, commit_message):
        if self._fail_protected_on is not None and branch == self._fail_protected_on:
            raise ProviderError("API_ERROR", "GitHub returned 422: protected branch update failed")
        self.calls.append(
            {
                "repo": repo_full_name,
                "path": path,
                "branch": branch,
                "content": content,
                "message": commit_message,
            }
        )
        return SimpleNamespace(commit_sha="deadbeef", file_path=path, web_url="https://host/commit")


def _fetch_returning(text: str | None):
    def _fetch(connection, repo_full_name, path, ref):
        return text

    return _fetch


def _fetch_raising(exc: Exception):
    def _fetch(connection, repo_full_name, path, ref):
        raise exc

    return _fetch


# ---- unit: section mapping + TOML transform ------------------------


def test_section_from_db_full_config_renders_cognito_section():
    section = ingress_auth_section_from_db(AUTH_CONFIG)
    assert section == {
        "kind": "cognito",
        "user_pool_arn": AUTH_CONFIG["user_pool_arn"],
        "user_pool_client_id": AUTH_CONFIG["user_pool_client_id"],
        "user_pool_domain": AUTH_CONFIG["user_pool_domain"],
    }


def test_section_from_db_none_and_partial_treated_as_disabled():
    assert ingress_auth_section_from_db(None) is None
    assert ingress_auth_section_from_db({}) is None
    # Missing one of the three required fields => disabled (matches the
    # reconcile annotation patch's all-or-nothing rule).
    partial = dict(AUTH_CONFIG)
    del partial["user_pool_domain"]
    assert ingress_auth_section_from_db(partial) is None
    # Present-but-empty also counts as disabled.
    blanked = dict(AUTH_CONFIG, user_pool_client_id="")
    assert ingress_auth_section_from_db(blanked) is None


def test_apply_ingress_auth_enable_preserves_other_sections():
    out = apply_ingress_auth(_BASE_TOML, ingress_auth_section_from_db(AUTH_CONFIG))
    data = tomllib.loads(out)
    assert data["ingress"]["class"] == "alb"
    assert data["ingress"]["auth"] == {
        "kind": "cognito",
        **{k: AUTH_CONFIG[k] for k in ("user_pool_arn", "user_pool_client_id", "user_pool_domain")},
    }
    # The unrelated workload + top-level name survive the round-trip.
    assert data["name"] == "hello"
    assert data["workloads"][0]["name"] == "web"


def test_apply_ingress_auth_disable_removes_section_keeps_ingress():
    out = apply_ingress_auth(_TOML_WITH_AUTH, None)
    data = tomllib.loads(out)
    assert "auth" not in data["ingress"]
    # The [ingress] table itself survives because it still carries class.
    assert data["ingress"]["class"] == "alb"
    assert data["workloads"][0]["name"] == "web"


def test_apply_ingress_auth_enable_on_empty_manifest_creates_ingress_table():
    out = apply_ingress_auth("", ingress_auth_section_from_db(AUTH_CONFIG), ingress_class="alb")
    data = tomllib.loads(out)
    assert data["ingress"]["class"] == "alb"
    assert data["ingress"]["auth"]["kind"] == "cognito"


# ---- single-app write-back -----------------------------------------


def test_write_back_enable_commits_patched_section(org, team, cluster):
    _make_connection(org)
    app = _make_app(org, team, slug="hello")
    put = _RecordingPut()

    result = write_auth_config_to_toml(
        cluster,
        app,
        SimpleNamespace(email="op@acme.test"),
        fetch=_fetch_returning(_BASE_TOML),
        put=put,
    )

    assert result.status == "committed", result.error
    assert result.commit_branch == "main"
    assert result.via_pull_request is False
    assert len(put.calls) == 1
    call = put.calls[0]
    assert call["repo"] == "acme/hello"
    assert call["path"] == "astrolift.toml"
    assert call["branch"] == "main"
    assert call["message"] == "chore(astrolift): sync ingress auth config from operator settings"
    committed = tomllib.loads(call["content"])
    assert committed["ingress"]["auth"]["kind"] == "cognito"
    assert committed["ingress"]["auth"]["user_pool_domain"] == "acme-auth"


def test_write_back_disable_removes_section(org, team, cluster):
    cluster.alb_auth_config = None
    cluster.save(update_fields=["alb_auth_config"])
    _make_connection(org)
    app = _make_app(org, team, slug="hello")
    put = _RecordingPut()

    result = write_auth_config_to_toml(
        cluster,
        app,
        SimpleNamespace(email="op@acme.test"),
        fetch=_fetch_returning(_TOML_WITH_AUTH),
        put=put,
    )

    assert result.status == "committed", result.error
    assert len(put.calls) == 1
    committed = tomllib.loads(put.calls[0]["content"])
    assert "auth" not in committed["ingress"]
    assert committed["ingress"]["class"] == "alb"


def test_write_back_no_source_connection_skips_gracefully(org, team, cluster):
    # No SourceConnection created for the org => graceful skip, no commit.
    app = _make_app(org, team, slug="hello")
    put = _RecordingPut()

    result = write_auth_config_to_toml(
        cluster,
        app,
        SimpleNamespace(email="op@acme.test"),
        fetch=_fetch_returning(_BASE_TOML),
        put=put,
    )

    assert result.status == "skipped"
    assert result.error is None
    assert put.calls == []


def test_write_back_non_git_source_kind_skips(org, team, cluster):
    _make_connection(org)
    app = _make_app(org, team, slug="hello", source_kind="git_url", source_repo="")
    put = _RecordingPut()

    result = write_auth_config_to_toml(
        cluster,
        app,
        SimpleNamespace(email="op@acme.test"),
        fetch=_fetch_returning(_BASE_TOML),
        put=put,
    )

    assert result.status == "skipped"
    assert put.calls == []


def test_write_back_already_in_sync_makes_no_commit(org, team, cluster):
    _make_connection(org)
    app = _make_app(org, team, slug="hello")
    put = _RecordingPut()

    # Repo already carries exactly the DB's auth section.
    result = write_auth_config_to_toml(
        cluster,
        app,
        SimpleNamespace(email="op@acme.test"),
        fetch=_fetch_returning(_TOML_WITH_AUTH),
        put=put,
    )

    assert result.status == "unchanged"
    assert put.calls == []


def test_write_back_fetch_failure_is_failed_not_raised(org, team, cluster):
    _make_connection(org)
    app = _make_app(org, team, slug="hello")
    put = _RecordingPut()

    result = write_auth_config_to_toml(
        cluster,
        app,
        SimpleNamespace(email="op@acme.test"),
        fetch=_fetch_raising(ProviderError("AUTH_FAILED", "token rejected")),
        put=put,
    )

    assert result.status == "failed"
    assert "AUTH_FAILED" in (result.error or "")
    assert put.calls == []


def test_write_back_protected_branch_falls_back_to_pull_request(org, team, cluster):
    _make_connection(org)
    app = _make_app(org, team, slug="hello")
    # Direct push to the deploy branch is rejected as protected; the
    # second put (to the feature branch) succeeds.
    put = _RecordingPut(fail_protected_on="main")
    pr_calls: list[dict] = []

    def _open_pr(connection, *, repo_full_name, head_branch, base_branch, title, body):
        pr_calls.append({"head": head_branch, "base": base_branch, "title": title})
        return SimpleNamespace(url="https://host/pull/7", number="7")

    result = write_auth_config_to_toml(
        cluster,
        app,
        SimpleNamespace(email="op@acme.test"),
        fetch=_fetch_returning(_BASE_TOML),
        put=put,
        open_pr=_open_pr,
    )

    assert result.status == "committed", result.error
    assert result.via_pull_request is True
    assert result.pull_request_url == "https://host/pull/7"
    assert result.commit_branch == "astrolift/ingress-auth-hello"
    # The feature-branch put happened; the protected-branch put did not record.
    assert [c["branch"] for c in put.calls] == ["astrolift/ingress-auth-hello"]
    assert pr_calls == [
        {
            "head": "astrolift/ingress-auth-hello",
            "base": "main",
            "title": "Sync ingress auth config from Astrolift operator settings",
        }
    ]


# ---- cluster-wide fan-out ------------------------------------------


def test_cluster_writeback_covers_bound_apps_and_dedupes_envs(org, team, cluster):
    _make_connection(org)
    # App A: writeable, two environments on the cluster (must commit once).
    app_a = _make_app(org, team, slug="app-a", source_repo="acme/app-a")
    _bind_env(app_a, cluster, name="prod")
    _bind_env(app_a, cluster, name="staging")
    # App B: bound to the cluster but its repo has no manifest yet — still
    # writeable (enabling auth writes a fresh [ingress] table).
    app_b = _make_app(org, team, slug="app-b", source_repo="acme/app-b")
    _bind_env(app_b, cluster, name="prod")
    # App C: NOT bound to this cluster — must be left untouched.
    _make_app(org, team, slug="app-c", source_repo="acme/app-c")

    put = _RecordingPut()
    summary = write_auth_config_for_cluster(
        cluster,
        SimpleNamespace(email="op@acme.test"),
        fetch=_fetch_returning(None),  # no existing manifest in any repo
        put=put,
    )

    assert summary.repos_updated == 2
    assert summary.failed == 0
    committed_repos = {c["repo"] for c in put.calls}
    assert committed_repos == {"acme/app-a", "acme/app-b"}
    # app-a appears once despite two environments.
    assert [c["repo"] for c in put.calls].count("acme/app-a") == 1
    # app-c (unbound) never appears.
    assert "acme/app-c" not in committed_repos


def test_cluster_writeback_skips_app_without_connection_but_continues(org, team, cluster):
    # No SourceConnection for the org at all => every app skips, sweep
    # still completes and reports skipped without raising.
    app = _make_app(org, team, slug="app-a", source_repo="acme/app-a")
    _bind_env(app, cluster, name="prod")

    put = _RecordingPut()
    summary = write_auth_config_for_cluster(
        cluster,
        SimpleNamespace(email="op@acme.test"),
        fetch=_fetch_returning(_BASE_TOML),
        put=put,
    )

    assert summary.repos_updated == 0
    assert summary.skipped == 1
    assert summary.failed == 0
    assert put.calls == []
