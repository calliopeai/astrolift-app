"""Registration detects the build context of a monorepo app (astrolift-cli #159).

SteadyMD registered abbott-cgm from ``src/abbott-cgm/astrolift.toml`` and its
managed CI workflow built the repository root. A registration that names no
build context now builds from the manifest's directory, unless the manifest
names its own build paths, which then apply as they always have. An explicit
context wins, apps already registered keep what they stored, and ``updateApp``
changes it later.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation, UpdateAppInput
from astrolift_registry.services.manifest_sync import register_app_repo
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.workflow_sync import render_astrolift_ci_workflow
from astrolift_workflows.activities.build_image import _resolve_build_paths
from core import mutations as core_mutations
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

MONOREPO_PATH = "src/abbott-cgm/astrolift.toml"

PLAIN_MANIFEST = """\
name = "abbott-cgm"

[[workloads]]
name = "web"
kind = "deployment"

[[workloads.containers]]
name = "web"
is_primary = true
port = 8080
"""

# The ConflictHQ/bdr#139 shape: the manifest reaches the repository root
# itself, and the Dockerfile sits beside the manifest.
OWN_PATHS_MANIFEST = """\
name = "web"

[[workloads]]
name = "web"
kind = "deployment"

[[workloads.containers]]
name = "web"
is_primary = true
port = 8080
build_context = "../.."
dockerfile_path = "Dockerfile.web"
"""


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Steady", slug="steady")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    # register_app needs a managed cluster (#315/#316); bulk_create dodges the
    # plugin version-field collision the other registry tests document.
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="Local", slug="local", capabilities_manifest={}, config_schema={})]
    )
    TenantCluster.objects.create(
        organization=org,
        name="local",
        slug="local",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, project


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _register(org, project, *, slug="abbott-cgm", **fields):
    fields.setdefault("source_repo", "steady/monorepo")
    with _ctx(org):
        return RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(project_id=str(project.guid), name=slug, slug=slug, **fields),
        )


def _build_paths(app):
    return _resolve_build_paths(app, SimpleNamespace(workload=None))


def test_sub_path_manifest_builds_from_its_directory(permission_resolver):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, manifest_path=MONOREPO_PATH, manifest_raw=PLAIN_MANIFEST)

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="abbott-cgm")
    assert (app.build_context, app.dockerfile_path) == ("src/abbott-cgm", "Dockerfile")
    assert (result.data.build_context, result.data.dockerfile_path) == ("src/abbott-cgm", "Dockerfile")
    # The platform build reads the same pair: src/abbott-cgm/Dockerfile.
    assert _build_paths(app) == ("Dockerfile", "src/abbott-cgm")


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_a_blank_context_is_detected(permission_resolver, blank):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(
        org, project, manifest_path=MONOREPO_PATH, manifest_raw=PLAIN_MANIFEST, build_context=blank
    )

    assert result.ok, result.errors
    assert RegisteredApp.objects.get(slug="abbott-cgm").build_context == "src/abbott-cgm"


@pytest.mark.parametrize(("explicit", "stored"), [(".", "."), ("services", "services")])
def test_an_explicit_context_is_kept(permission_resolver, explicit, stored):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(
        org,
        project,
        manifest_path=MONOREPO_PATH,
        manifest_raw=PLAIN_MANIFEST,
        build_context=explicit,
        dockerfile_path="docker/Dockerfile.prod",
    )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="abbott-cgm")
    assert (app.build_context, app.dockerfile_path) == (stored, "docker/Dockerfile.prod")


def test_a_manifest_with_its_own_build_paths_keeps_the_repository_root(permission_resolver):
    """The manifest's container paths win, resolved from its directory."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(
        org, project, manifest_path="services/web/astrolift.toml", manifest_raw=OWN_PATHS_MANIFEST
    )

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="abbott-cgm")
    assert app.build_context == "."
    assert _build_paths(app) == ("services/web/Dockerfile.web", ".")


@pytest.mark.parametrize(
    "manifest_path",
    [None, "astrolift.toml", "../outside/astrolift.toml", "/etc/astrolift.toml"],
)
def test_a_root_or_unusable_manifest_path_keeps_the_repository_root(permission_resolver, manifest_path):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, manifest_path=manifest_path, manifest_raw=PLAIN_MANIFEST)

    assert result.ok, result.errors
    assert RegisteredApp.objects.get(slug="abbott-cgm").build_context == "."


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [(PLAIN_MANIFEST, "src/abbott-cgm"), (OWN_PATHS_MANIFEST, ".")],
    ids=["plain", "own-build-paths"],
)
def test_a_manifest_read_from_the_repo_is_detected_after_the_fetch(
    permission_resolver, monkeypatch, manifest, expected
):
    """No inline manifest: the repo fetch persists the workloads first."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    def _fetched(app, **kwargs):
        persist_manifest(
            app, normalize(parse_raw(manifest), defaults=NormalizationDefaults()), raw_text=manifest
        )
        return SimpleNamespace(status="applied", error="")

    monkeypatch.setattr("astrolift_registry.services.manifest_sync.resync_app_manifest_from_repo", _fetched)

    result = _register(org, project, manifest_path=MONOREPO_PATH)

    assert result.ok, result.errors
    assert RegisteredApp.objects.get(slug="abbott-cgm").build_context == expected


def test_an_unreadable_manifest_still_builds_from_its_directory(permission_resolver):
    """Nothing says the manifest names its own paths: the directory applies."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, manifest_path=MONOREPO_PATH, manifest_raw="not toml {{{")

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="abbott-cgm")
    assert app.manifest_bootstrap_status == "parse_failed"
    assert app.build_context == "src/abbott-cgm"


def test_the_graphql_input_detects_an_omitted_or_null_context(permission_resolver):
    """The wire contract: ``buildContext`` may be omitted or null."""
    from config.schema import schema
    from core.schema.context import StrawberryContext

    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)
    document = """
        mutation($input: RegisterAppInput!) {
          registerApp(input: $input) { ok errors { code message } data { buildContext dockerfilePath } }
        }
    """
    context = StrawberryContext(SimpleNamespace(user=None, session=None, META={}))
    replies = []
    for slug, extra in (("omitted", {}), ("null", {"buildContext": None}), ("root", {"buildContext": "."})):
        variables = {
            "input": {
                "projectId": str(project.guid),
                "slug": slug,
                "sourceRepo": f"steady/{slug}",
                "manifestPath": MONOREPO_PATH,
                "manifestRaw": PLAIN_MANIFEST,
                **extra,
            }
        }
        with _ctx(org):
            result = schema.execute_sync(document, variable_values=variables, context_value=context)
        assert result.errors is None, result.errors
        replies.append(result.data["registerApp"])

    assert [r["ok"] for r in replies] == [True, True, True], replies
    assert [r["data"]["buildContext"] for r in replies] == ["src/abbott-cgm", "src/abbott-cgm", "."]
    assert {r["data"]["dockerfilePath"] for r in replies} == {"Dockerfile"}


def test_an_app_registered_before_detection_keeps_its_stored_context(permission_resolver):
    """No migration rewrites an existing app, and neither an unrelated update
    nor a manifest re-apply touches the stored context."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    # The shape every pre-#159 registration of a sub-path manifest stored.
    app = RegisteredApp.objects.create(
        organization=org,
        team=project.team,
        project=project,
        name="Legacy",
        slug="legacy",
        source_repo="steady/monorepo",
        manifest_path=MONOREPO_PATH,
    )
    assert (app.build_context, app.dockerfile_path) == (".", "Dockerfile")

    with _ctx(org):
        result = RegistryMutation().update_app(
            _info(), input=UpdateAppInput(id=str(app.guid), name="Legacy app")
        )
    assert result.ok, result.errors
    persist_manifest(
        app, normalize(parse_raw(PLAIN_MANIFEST), defaults=NormalizationDefaults()), raw_text=PLAIN_MANIFEST
    )

    app.refresh_from_db()
    assert (app.build_context, app.dockerfile_path) == (".", "Dockerfile")
    assert _build_paths(app) == ("Dockerfile", ".")


def test_a_repo_rescan_keeps_an_existing_apps_context():
    """Re-running repo-scan registration reuses the app it already made."""
    org, project = _scaffold()
    SourceConnection.objects.create(
        organization=org, kind=SourceConnection.Kind.GITHUB_PAT, display_name="PAT", account_login="steady"
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=project.team,
        project=project,
        name="web",
        slug="web",
        source_repo="steady/monorepo",
        manifest_path="apps/web/astrolift.toml",
        build_context="apps/web",
    )

    result = register_app_repo(
        project=project,
        source_kind="github",
        source_repo="steady/monorepo",
        ref="main",
        tree=lambda connection, repo, ref: {"apps/web/astrolift.toml": OWN_PATHS_MANIFEST},
    )

    assert result.status == "ok", result.error
    assert [(entry.created, entry.build_context) for entry in result.apps] == [(False, "apps/web")]
    app.refresh_from_db()
    assert app.build_context == "apps/web"


def test_update_app_changes_the_build_paths_behind_its_version_gate(permission_resolver):
    """How an existing app opts in: updateApp, audited, with ifMatchVersion."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    app = RegisteredApp.objects.create(
        organization=org,
        team=project.team,
        project=project,
        name="Legacy",
        slug="legacy",
        source_repo="steady/monorepo",
        manifest_path=MONOREPO_PATH,
    )
    stale = app.version
    captured = []
    original = core_mutations._audit_writer
    core_mutations.register_audit_writer(captured.append)
    try:
        with _ctx(org):
            changed = RegistryMutation().update_app(
                _info(),
                input=UpdateAppInput(
                    id=str(app.guid),
                    build_context="src/abbott-cgm",
                    dockerfile_path="Dockerfile.prod",
                    if_match_version=app.version,
                ),
            )
            refused = RegistryMutation().update_app(
                _info(),
                input=UpdateAppInput(id=str(app.guid), build_context=".", if_match_version=stale),
            )
    finally:
        core_mutations.register_audit_writer(original)

    assert changed.ok, changed.errors
    assert (changed.data.build_context, changed.data.dockerfile_path) == ("src/abbott-cgm", "Dockerfile.prod")
    assert not refused.ok
    assert refused.errors[0].code == "VERSION_MISMATCH"
    app.refresh_from_db()
    assert (app.build_context, app.dockerfile_path) == ("src/abbott-cgm", "Dockerfile.prod")
    assert _build_paths(app) == ("Dockerfile.prod", "src/abbott-cgm")
    rows = [entry for entry in captured if entry.action == "app.update"]
    assert len(rows) == 2
    assert rows[0].decision == "ALLOW"


def test_update_app_build_paths_need_app_update(permission_resolver):
    org, project = _scaffold()
    app = RegisteredApp.objects.create(
        organization=org,
        team=project.team,
        project=project,
        name="Legacy",
        slug="legacy",
        source_repo="steady/monorepo",
        manifest_path=MONOREPO_PATH,
    )

    with _ctx(org):
        result = RegistryMutation().update_app(
            _info(), input=UpdateAppInput(id=str(app.guid), build_context="src/abbott-cgm")
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    app.refresh_from_db()
    assert app.build_context == "."


def test_the_managed_ci_workflow_builds_the_detected_context(permission_resolver, settings):
    """``astro ci setup`` renders this file: no hand-written workflow."""
    settings.PLATFORM_API_URL = "https://platform.example.test"
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)
    result = _register(org, project, manifest_path=MONOREPO_PATH, manifest_raw=PLAIN_MANIFEST)
    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="abbott-cgm")
    assert app.build_mode == RegisteredApp.BuildMode.CI_PUSHED.value
    # What image publishing provisions; the workflow refuses to render without it.
    app.registry_repo_uri = "123456789012.dkr.ecr.us-east-1.amazonaws.com/steady/abbott-cgm"
    app.push_role_ref = "arn:aws:iam::123456789012:role/ci-build"
    app.default_tenant_cluster = None

    document = yaml.safe_load(render_astrolift_ci_workflow(app))

    steps = document["jobs"]["build-and-deploy"]["steps"]
    build = next(step for step in steps if step["name"] == "Build and push image")
    assert build["env"]["BUILD_CONTEXT"] == "src/abbott-cgm"
    assert build["env"]["DOCKERFILE"] == "src/abbott-cgm/Dockerfile"
