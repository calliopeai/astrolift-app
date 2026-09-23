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

_GOOD_DIGEST = "sha256:" + "9c" * 32


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


def _make_deployment(build_strategy="dockerfile", image_tag="sha-abc", build_mode="platform_build"):
    import uuid

    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization, Team
    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_registry.models import RegisteredApp

    suffix = uuid.uuid4().hex[:6]
    org = Organization.objects.create(name="BuildOrg", slug=f"bo-{suffix}")
    team = Team.objects.create(organization=org, name="T", slug=f"t-{suffix}")
    # Seeded directly; the plugin row is scaffolding for this test.
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
        build_mode=build_mode,
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
            return [FakeTag("sha-abc", _GOOD_DIGEST)]

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
    assert result["digest"] == _GOOD_DIGEST
    assert result["image_ref"] == "123.dkr.ecr.us-west-2.amazonaws.com/bo/app:sha-abc"
    deployment.refresh_from_db()
    assert deployment.image_digest == _GOOD_DIGEST


# --- container-level dockerfile_path / build_context (#1756) ----------
#
# [[workloads.containers]] accepts dockerfile_path/build_context and
# persist.py writes them onto the Container row, but the build only ever
# read RegisteredApp.dockerfile_path/build_context -- a manifest that set
# a container's build_context to reach a Dockerfile above its own
# directory (the ConflictHQ/bdr#139 monorepo shape) passed validation and
# was silently ignored.


def _capture_build_spec(monkeypatch):
    """Install a FakeDriver that records the BuildSpec it was called
    with, and wire it up as the prepared build for _build_image_sync."""
    from providers._sdk.build import BuildResult

    captured: dict = {}

    class FakeDriver:
        def build(self, spec, repo, tag):
            captured["spec"] = spec
            return BuildResult(success=True, image_uri=f"{repo}:{tag}", digest="", duration_seconds=1.0)

    prepared = _PreparedBuild(
        driver=FakeDriver(),
        registry_driver=object(),
        repo_name="bo/app",
        repo_uri="r/bo/app",
    )
    monkeypatch.setattr(build_image_mod, "cluster_for_deployment", lambda _d: object(), raising=False)
    monkeypatch.setattr(build_image_mod, "_prepare_build", lambda *a, **k: prepared)
    return captured


def test_build_image_honors_the_primary_containers_dockerfile_and_context(monkeypatch):
    from astrolift_registry.models import Workload

    deployment = _make_deployment()
    app = deployment.registered_app
    workload = Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment")
    workload.containers.create(
        name="web",
        is_primary=True,
        dockerfile_path="services/web/Dockerfile",
        build_context="../..",
    )

    captured = _capture_build_spec(monkeypatch)
    _build_image_sync(BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha=""))

    assert captured["spec"].dockerfile_path == "services/web/Dockerfile"
    assert captured["spec"].context_path == "../.."


def test_build_image_falls_back_to_app_level_fields_when_container_is_default(monkeypatch):
    """A container whose manifest never set dockerfile_path/build_context
    reads back as the parser's own default -- indistinguishable from an
    explicit default -- and must not clobber the app-level fields set by
    `astro app register --dockerfile-path/--build-context` or monorepo
    discovery."""
    from astrolift_registry.models import Workload

    deployment = _make_deployment()
    app = deployment.registered_app
    app.dockerfile_path = "custom/Dockerfile"
    app.build_context = "apps/web"
    app.save(update_fields=["dockerfile_path", "build_context"])
    workload = Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment")
    workload.containers.create(name="web", is_primary=True)

    captured = _capture_build_spec(monkeypatch)
    _build_image_sync(BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha=""))

    assert captured["spec"].dockerfile_path == "custom/Dockerfile"
    assert captured["spec"].context_path == "apps/web"


def test_build_image_with_no_workloads_yet_uses_app_level_fields(monkeypatch):
    """Before the manifest has materialized any Workload rows (or for an
    app that never will), the build must behave exactly as before."""
    deployment = _make_deployment()
    app = deployment.registered_app
    app.dockerfile_path = "Dockerfile.prod"
    app.build_context = "services/api"
    app.save(update_fields=["dockerfile_path", "build_context"])

    captured = _capture_build_spec(monkeypatch)
    _build_image_sync(BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha=""))

    assert captured["spec"].dockerfile_path == "Dockerfile.prod"
    assert captured["spec"].context_path == "services/api"


def test_build_image_prefers_the_deployments_own_workload_over_the_apps_first(monkeypatch):
    """Task / static-site / cron deploys scope ``Deployment.workload`` to
    one specific workload -- that one wins over "the app's first"."""
    from astrolift_registry.models import Workload

    deployment = _make_deployment()
    app = deployment.registered_app
    first = Workload.objects.create(registered_app=app, name="migrate", slug="migrate", kind="task")
    first.containers.create(name="migrate", is_primary=True, dockerfile_path="Dockerfile.migrate")
    target = Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment")
    target.containers.create(name="web", is_primary=True, dockerfile_path="Dockerfile.web")
    deployment.workload = target
    deployment.save(update_fields=["workload"])

    captured = _capture_build_spec(monkeypatch)
    _build_image_sync(BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha=""))

    assert captured["spec"].dockerfile_path == "Dockerfile.web"


def test_build_image_sync_ignores_a_malformed_registry_digest(monkeypatch):
    """The render pins containers to whatever lands in ``image_digest``, so a
    registry that answers with something other than ``sha256:<64 hex>`` must
    leave the column empty (deploy on the tag) rather than store a value that
    would render an unpullable image ref."""
    from providers._sdk.build import BuildResult

    deployment = _make_deployment(image_tag="sha-abc")

    class FakeDriver:
        def build(self, spec, repo, tag):
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
    assert result["digest"] == ""
    deployment.refresh_from_db()
    assert deployment.image_digest == ""


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


# --- what a failed build leaves behind (#1686) ------------------------
#
# ``aborted_reason`` keeps a single line by design, so the build pod's
# output has to be persisted somewhere it will not be truncated away.


def _fail_build_with(monkeypatch, deployment, errors):
    from providers._sdk.build import BuildResult

    class FailingDriver:
        def build(self, spec, repo, tag):
            return BuildResult(
                success=False,
                image_uri=f"{repo}:{tag}",
                digest="",
                duration_seconds=1.0,
                errors=list(errors),
            )

    prepared = _PreparedBuild(
        driver=FailingDriver(),
        registry_driver=object(),
        repo_name="bo/app",
        repo_uri="r/bo/app",
    )
    monkeypatch.setattr(build_image_mod, "cluster_for_deployment", lambda _d: object(), raising=False)
    monkeypatch.setattr(build_image_mod, "_prepare_build", lambda *a, **k: prepared)
    with pytest.raises(RuntimeError):
        _build_image_sync(BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha=""))
    deployment.refresh_from_db()
    return deployment


def test_a_failed_build_persists_the_pod_output_on_the_deploy(monkeypatch):
    deployment = _make_deployment()
    _fail_build_with(
        monkeypatch,
        deployment,
        [
            "Job has reached the specified backoff limit",
            "build pod logs (last 100 lines):\nfatal: could not read Username for 'https://github.com'",
        ],
    )

    assert "backoff limit" in deployment.build_error
    assert "could not read Username" in deployment.build_error


def test_the_raised_error_stays_one_line(monkeypatch):
    """The workflow keeps only the first line for ``aborted_reason``, so a
    multi-line message there would drop the condition, not the logs."""

    deployment = _make_deployment()
    from providers._sdk.build import BuildResult

    class FailingDriver:
        def build(self, spec, repo, tag):
            return BuildResult(
                success=False,
                image_uri=f"{repo}:{tag}",
                digest="",
                duration_seconds=1.0,
                errors=["Job has reached the specified backoff limit", "logs:\nline one\nline two"],
            )

    prepared = _PreparedBuild(
        driver=FailingDriver(),
        registry_driver=object(),
        repo_name="bo/app",
        repo_uri="r/bo/app",
    )
    monkeypatch.setattr(build_image_mod, "cluster_for_deployment", lambda _d: object(), raising=False)
    monkeypatch.setattr(build_image_mod, "_prepare_build", lambda *a, **k: prepared)

    with pytest.raises(RuntimeError) as excinfo:
        _build_image_sync(BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha=""))

    assert "\n" not in str(excinfo.value)
    assert "backoff limit" in str(excinfo.value)


def test_a_successful_build_leaves_build_error_empty(monkeypatch):
    from providers._sdk.build import BuildResult

    deployment = _make_deployment(image_tag="sha-abc")

    class OkDriver:
        def build(self, spec, repo, tag):
            return BuildResult(
                success=True,
                image_uri=f"{repo}:{tag}",
                digest="sha256:" + "a" * 64,
                duration_seconds=1.0,
                errors=[],
            )

    prepared = _PreparedBuild(
        driver=OkDriver(),
        registry_driver=object(),
        repo_name="bo/app",
        repo_uri="r/bo/app",
    )
    monkeypatch.setattr(build_image_mod, "cluster_for_deployment", lambda _d: object(), raising=False)
    monkeypatch.setattr(build_image_mod, "_prepare_build", lambda *a, **k: prepared)

    _build_image_sync(BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha=""))

    deployment.refresh_from_db()
    assert deployment.build_error == ""


# ---------------------------------------------------------------------------
# _fetch_app_build_strategy_sync
# ---------------------------------------------------------------------------


def test_fetch_app_build_strategy_returns_correct_value():
    deployment = _make_deployment(build_strategy="nixpacks")
    assert _fetch_app_build_strategy_sync(deployment.registered_app_id) == "nixpacks"


# --- both build axes have to agree (#1687) ----------------------------
#
# ``build_mode`` decides whether the platform builds at all;
# ``build_strategy`` only decides which builder it uses when it does.
# The deploy workflow read the strategy alone, so an app moved to
# ``ci_pushed`` kept running a full platform build on every deploy --
# ``platform_build`` registration persists a dockerfile strategy, and a
# mode-only change never cleared it.


@pytest.mark.parametrize("mode", ["ci_pushed", "none"])
def test_a_non_building_mode_resolves_to_off(mode):
    deployment = _make_deployment(build_strategy="dockerfile", build_mode=mode)
    assert _fetch_app_build_strategy_sync(deployment.registered_app_id) == "off"


def test_platform_build_keeps_its_chosen_builder():
    deployment = _make_deployment(build_strategy="buildpacks", build_mode="platform_build")
    assert _fetch_app_build_strategy_sync(deployment.registered_app_id) == "buildpacks"


def test_platform_build_with_no_strategy_is_off():
    deployment = _make_deployment(build_strategy="off", build_mode="platform_build")
    assert _fetch_app_build_strategy_sync(deployment.registered_app_id) == "off"


def test_the_activity_and_the_builder_agree_on_the_answer(monkeypatch):
    """The workflow branch is not the only guard: an activity invoked
    directly for a ci_pushed app must not build either."""

    deployment = _make_deployment(build_strategy="dockerfile", build_mode="ci_pushed")

    def _no_driver(*args, **kwargs):
        raise AssertionError("no build may be prepared for a ci_pushed app")

    monkeypatch.setattr(build_image_mod, "_prepare_build", _no_driver)

    result = _build_image_sync(
        BuildImageInput(deployment_id=deployment.pk, image_tag="sha-abc", commit_sha="")
    )
    assert result == {"ok": True, "image_ref": "sha-abc", "stub": True}


def test_fetch_app_build_strategy_returns_off_for_missing_app():
    assert _fetch_app_build_strategy_sync(999_999_999) == "off"


# --- clone credential for a private repo (#1685) ----------------------
#
# The build pod got a bare clone URL and no credential, so kaniko could
# not clone any private repo and the pod died immediately. The install
# already holds a GitHub App that can mint an installation token; the
# build path simply never asked for one.


def _connection(app, kind="github_app_install"):
    from astrolift_scm.models import SourceConnection
    from core.secrets import encrypt_at_rest

    encrypted = encrypt_at_rest(b"pem-or-pat")
    return SourceConnection.objects.create(
        organization=app.organization,
        kind=kind,
        account_login="acme",
        installation_id="987" if kind == "github_app_install" else "",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def test_the_clone_credential_is_the_github_documented_pair(monkeypatch):
    deployment = _make_deployment()
    app = deployment.registered_app
    _connection(app)
    monkeypatch.setattr("astrolift_scm.providers.github._token", lambda _c: "ghs_minted")

    assert build_image_mod._clone_credential(app) == ("x-access-token", "ghs_minted")


def test_no_connection_means_an_anonymous_clone(monkeypatch):
    """A public repo must keep cloning exactly as it did."""

    deployment = _make_deployment()
    assert build_image_mod._clone_credential(deployment.registered_app) == ("", "")


def test_a_token_that_cannot_be_minted_does_not_fail_the_build(monkeypatch):
    """Better a clone error in the pod logs than a build that never
    starts because the credential lookup raised."""

    deployment = _make_deployment()
    app = deployment.registered_app
    _connection(app)

    def _boom(_c):
        raise RuntimeError("app not installed on this repo")

    monkeypatch.setattr("astrolift_scm.providers.github._token", _boom)

    assert build_image_mod._clone_credential(app) == ("", "")


def test_an_app_with_no_source_repo_needs_no_credential():
    deployment = _make_deployment()
    app = deployment.registered_app
    app.source_repo = ""
    app.save(update_fields=["source_repo"])

    assert build_image_mod._clone_credential(app) == ("", "")


def test_a_non_github_host_keeps_the_anonymous_clone(monkeypatch):
    """Only the GitHub token path is wired; guessing at another host's
    credential shape would be worse than the clone error."""

    deployment = _make_deployment()
    app = deployment.registered_app
    app.source_kind = "gitlab"
    app.save(update_fields=["source_kind"])
    _connection(app, kind="gitlab_pat")

    assert build_image_mod._clone_credential(app) == ("", "")
