"""Tests for BuildImageActivity (#865, #867, #978).

Covers:
- ``BuildImageInput`` is a frozen, primitive-typed dataclass (Temporal).
- ``_build_image_sync`` short-circuits to the stub when build_strategy is
  "off", and when no build driver is available for the cluster.
- ``_build_image_sync`` runs a real build, records the pushed digest on the
  Deployment row, and reports ``stub:false`` on success.
- ``_build_image_sync`` raises on a failed build (so the workflow marks the
  deployment failed rather than rolling out an un-built image).
- ``_resolve_source_url`` formats the git ref from a commit SHA / branch.
- ``_fetch_app_build_strategy_sync`` returns the correct value.
"""

from __future__ import annotations

import importlib

import pytest

from astrolift_workflows.activities.build_image import (
    BuildImageInput,
    _build_image_sync,
    _fetch_app_build_strategy_sync,
    _PreparedBuild,
    _resolve_source_url,
)

build_image_mod = importlib.import_module("astrolift_workflows.activities.build_image")


# ---------------------------------------------------------------------------
# BuildImageInput contract
# ---------------------------------------------------------------------------


def test_build_image_input_is_frozen():
    inp = BuildImageInput(deployment_id=1, image_tag="reg/repo:sha", commit_sha="deadbeef")
    with pytest.raises((AttributeError, TypeError)):
        inp.deployment_id = 2  # type: ignore[misc]


def test_build_image_input_fields():
    inp = BuildImageInput(deployment_id=7, image_tag="t", commit_sha="s")
    assert (inp.deployment_id, inp.image_tag, inp.commit_sha) == (7, "t", "s")


# ---------------------------------------------------------------------------
# _resolve_source_url (pure-ish; SourceConnection lookup is best-effort)
# ---------------------------------------------------------------------------


class _App:
    def __init__(self, source_repo="acme/web", default_branch="main", organization=None):
        self.source_repo = source_repo
        self.default_branch = default_branch
        self.organization = organization


def test_resolve_source_url_uses_commit_sha_ref():
    url = _resolve_source_url(_App(), "abc123")
    assert url == "git+https://github.com/acme/web#abc123"


def test_resolve_source_url_falls_back_to_default_branch():
    url = _resolve_source_url(_App(default_branch="develop"), "")
    assert url == "git+https://github.com/acme/web#refs/heads/develop"


def test_resolve_source_url_empty_without_repo():
    assert _resolve_source_url(_App(source_repo=""), "abc") == ""


# ---------------------------------------------------------------------------
# DB-backed: _build_image_sync
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.django_db


def _make_deployment(build_strategy="dockerfile", image_tag="sha-abc"):
    import uuid

    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization, Team
    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_registry.models import RegisteredApp

    suffix = uuid.uuid4().hex[:6]
    org = Organization.objects.create(name="BuildOrg", slug=f"bo-{suffix}")
    team = Team.objects.create(organization=org, name="T", slug=f"t-{suffix}")
    # bulk_create bypasses BaseCoreModel.save() (ProviderPlugin.version is a
    # CharField shadowing the base int version) — same trick the cluster suites use.
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="aws", slug=f"aws-{suffix}", capabilities_manifest={}, config_schema={})]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"c-{suffix}",
        name="dev",
        provider_plugin=plugin,
        provider_config={},
        region="us-west-2",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        name="App",
        slug=f"app-{suffix}",
        provisioning_status="ready",
        subdomain=f"app-{suffix}",
        source_repo="calliopeai/astrolift-sample-web",
        build_strategy=build_strategy,
    )
    env = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="prod", required_approvals=0
    )
    deployment = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind="manual",
        image_tag=image_tag,
        commit_sha="cafebabe",
    )
    return deployment


def test_build_image_sync_stub_when_strategy_off():
    deployment = _make_deployment(build_strategy="off")
    result = _build_image_sync(
        BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha="")
    )
    assert result == {"ok": True, "image_ref": "sha-abc", "stub": True}


def test_build_image_sync_stub_when_no_driver(monkeypatch):
    deployment = _make_deployment()
    monkeypatch.setattr(build_image_mod, "cluster_for_deployment", lambda _d: object(), raising=False)
    # No env cluster bound → cluster_for_deployment would raise; force a
    # sentinel cluster, then have _prepare_build decline (non-buildable).
    monkeypatch.setattr(build_image_mod, "_prepare_build", lambda *a, **k: None)

    result = _build_image_sync(
        BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha="")
    )
    assert result["stub"] is True
    assert result["image_ref"] == "sha-abc"


def test_build_image_sync_real_build_records_digest(monkeypatch):
    from providers._sdk.build import BuildResult

    deployment = _make_deployment(image_tag="sha-abc")

    class FakeDriver:
        def build(self, spec, repo, tag):
            assert tag == "sha-abc"
            return BuildResult(success=True, image_uri=f"{repo}:{tag}", digest="", duration_seconds=1.0)

    class FakeTag:
        def __init__(self, name, digest):
            self.name = name
            self.digest = digest

    class FakeRegistry:
        def list_tags(self, repo_name):
            return [FakeTag("sha-abc", "sha256:feedface")]

    prepared = _PreparedBuild(
        driver=FakeDriver(),
        registry_driver=FakeRegistry(),
        repo_name="bo/app",
        repo_uri="123.dkr.ecr.us-west-2.amazonaws.com/bo/app",
    )
    monkeypatch.setattr(build_image_mod, "cluster_for_deployment", lambda _d: object(), raising=False)
    monkeypatch.setattr(build_image_mod, "_prepare_build", lambda *a, **k: prepared)

    result = _build_image_sync(
        BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha="cafebabe")
    )

    assert result["stub"] is False
    assert result["digest"] == "sha256:feedface"
    assert result["image_ref"] == "123.dkr.ecr.us-west-2.amazonaws.com/bo/app:sha-abc"
    deployment.refresh_from_db()
    assert deployment.image_digest == "sha256:feedface"


def test_build_image_sync_raises_on_build_failure(monkeypatch):
    from providers._sdk.build import BuildResult

    deployment = _make_deployment()

    class FailingDriver:
        def build(self, spec, repo, tag):
            return BuildResult(
                success=False,
                image_uri=f"{repo}:{tag}",
                digest="",
                duration_seconds=1.0,
                errors=["kaniko exited 1: COPY failed"],
            )

    prepared = _PreparedBuild(
        driver=FailingDriver(),
        registry_driver=object(),
        repo_name="bo/app",
        repo_uri="r/bo/app",
    )
    monkeypatch.setattr(build_image_mod, "cluster_for_deployment", lambda _d: object(), raising=False)
    monkeypatch.setattr(build_image_mod, "_prepare_build", lambda *a, **k: prepared)

    with pytest.raises(RuntimeError, match="COPY failed"):
        _build_image_sync(BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha=""))


# ---------------------------------------------------------------------------
# _fetch_app_build_strategy_sync
# ---------------------------------------------------------------------------


def test_fetch_app_build_strategy_returns_correct_value():
    deployment = _make_deployment(build_strategy="nixpacks")
    assert _fetch_app_build_strategy_sync(deployment.registered_app_id) == "nixpacks"


def test_fetch_app_build_strategy_returns_off_for_missing_app():
    assert _fetch_app_build_strategy_sync(999_999_999) == "off"
