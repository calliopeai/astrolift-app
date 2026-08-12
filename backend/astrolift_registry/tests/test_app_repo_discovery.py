"""Monorepo / multi-service app discovery + registration (#979).

The app-side mirror of ``test_agent_repo_discovery``: "point at a repo,
register each app manifest as its own RegisteredApp", against real Postgres.
The repo tree is injected (a fixture ``{path: contents}`` map) so the
production service code path runs end-to-end with only the SCM I/O stubbed —
never a real GitHub call. Covers:

  1. a monorepo with apps/web + apps/api registers TWO apps, each building
     from its own subdir;
  2. a single root manifest registers ONE (build_context ".");
  3. a re-scan after adding apps/worker adds the third WITHOUT duplicating the
     existing two;
  4. pure-agent manifests are IGNORED (owned by the agent path);
  5. all rows are tenancy-scoped to the project.

Plus the ``registerAppRepo`` mutation's tenancy + envelope behaviour. The
mutation seeds a managed cluster (apps are deploy targets) and bootstraps each
created app, so it runs against the test Temporal env like ``register_app``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import AppTeamAccess, Container, RegisteredApp, Workload
from astrolift_registry.schema.mutations import RegisterAppRepoInput, RegistryMutation
from astrolift_registry.services.manifest_sync import (
    discover_app_manifests,
    register_app_repo,
)
from astrolift_scm.models import SourceConnection
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- fixtures ---------------------------------------------------------


def _app_toml(name: str, *, kind: str = "deployment", port: int = 8080) -> str:
    return (
        f'name = "{name}"\n'
        "[[workloads]]\n"
        f'name = "{name}"\n'
        f'kind = "{kind}"\n'
        "is_public = true\n"
        "[[workloads.containers]]\n"
        f'name = "{name}"\n'
        "is_primary = true\n"
        f"port = {port}\n"
    )


_AGENT_TOML = (
    'name = "bot"\n'
    "[[workloads]]\n"
    'name = "bot"\n'
    'kind = "agent"\n'
    "[[workloads.containers]]\n"
    'name = "bot"\n'
    "is_primary = true\n"
    'image_ref = "ecr.example/agent:latest"\n'
)


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Globex", slug="globex")


def _project(org, *, slug="demo"):
    team = Team.objects.create(organization=org, name=f"Eng-{slug}", slug=f"eng-{slug}")
    return Project.objects.create(organization=org, team=team, name=slug.title(), slug=slug)


@pytest.fixture
def with_connection():
    def _make(org):
        return SourceConnection.objects.create(
            organization=org,
            kind=SourceConnection.Kind.GITHUB_PAT,
            display_name=f"{org.slug} PAT",
            account_login=org.slug,
        )

    return _make


def _tree_of(files: dict[str, str]):
    """Build an injectable ``_TreeFn`` returning a fixed file map, asserting
    the service passes the repo handle through unchanged."""

    def _tree(connection, repo_full_name, ref):
        _tree.calls.append((repo_full_name, ref))
        return dict(files)

    _tree.calls = []
    return _tree


# ---------------------------------------------------------------------------
# Acceptance 1: monorepo with two apps registers two apps, per-subdir context
# ---------------------------------------------------------------------------


def test_monorepo_registers_two_apps_with_own_build_context(org, with_connection):
    with_connection(org)
    project = _project(org)
    tree = _tree_of(
        {
            "apps/web/astrolift.toml": _app_toml("web"),
            "apps/api/astrolift.toml": _app_toml("api"),
            "README.md": "docs",
        }
    )

    result = register_app_repo(
        project=project,
        source_kind="github",
        source_repo="acme/monorepo",
        ref="main",
        tree=tree,
    )

    assert result.status == "ok"
    assert {a.slug for a in result.apps} == {"web", "api"}
    assert all(a.created for a in result.apps)
    assert tree.calls == [("acme/monorepo", "main")]

    apps = {
        a.manifest_path: a for a in RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True)
    }
    assert set(apps) == {"apps/web/astrolift.toml", "apps/api/astrolift.toml"}
    assert (
        AppTeamAccess.objects.filter(
            registered_app__in=apps.values(),
            team=project.team,
            access_level=AppTeamAccess.AccessLevel.OWNER.value,
            deleted_at__isnull=True,
        ).count()
        == 2
    )
    # Each service builds from its own subdir — the load-bearing #979 contract.
    assert apps["apps/web/astrolift.toml"].build_context == "apps/web"
    assert apps["apps/api/astrolift.toml"].build_context == "apps/api"
    # The workload + container rows were materialized from each manifest.
    web = apps["apps/web/astrolift.toml"]
    w = Workload.objects.get(registered_app=web, deleted_at__isnull=True)
    assert w.slug == "web"
    assert Container.objects.filter(workload=w, deleted_at__isnull=True).count() == 1


# ---------------------------------------------------------------------------
# Acceptance 2: single root manifest registers ONE app, build_context "."
# ---------------------------------------------------------------------------


def test_single_root_manifest_registers_one_app(org, with_connection):
    with_connection(org)
    project = _project(org)
    tree = _tree_of({"astrolift.toml": _app_toml("solo"), "Dockerfile": "FROM scratch"})

    result = register_app_repo(
        project=project, source_kind="github", source_repo="acme/solo", ref="main", tree=tree
    )

    assert result.status == "ok"
    assert [a.slug for a in result.apps] == ["solo"]
    app = RegisteredApp.objects.get(organization=org, deleted_at__isnull=True)
    assert app.manifest_path == "astrolift.toml"
    assert app.build_context == "."


# ---------------------------------------------------------------------------
# Acceptance 3: re-run after adding apps/worker adds the third, no duplicates
# ---------------------------------------------------------------------------


def test_rescan_adds_new_app_without_duplicating(org, with_connection):
    with_connection(org)
    project = _project(org)

    first = register_app_repo(
        project=project,
        source_kind="github",
        source_repo="acme/monorepo",
        ref="main",
        tree=_tree_of(
            {
                "apps/web/astrolift.toml": _app_toml("web"),
                "apps/api/astrolift.toml": _app_toml("api"),
            }
        ),
    )
    assert first.status == "ok"
    assert len(first.apps) == 2

    rescan = register_app_repo(
        project=project,
        source_kind="github",
        source_repo="acme/monorepo",
        ref="main",
        tree=_tree_of(
            {
                "apps/web/astrolift.toml": _app_toml("web"),
                "apps/api/astrolift.toml": _app_toml("api"),
                "apps/worker/astrolift.toml": _app_toml("worker"),
            }
        ),
    )
    assert rescan.status == "ok"

    apps = RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True)
    assert apps.count() == 3
    assert {a.slug for a in apps} == {"web", "api", "worker"}

    by_path = {a.manifest_path: a for a in rescan.apps}
    assert by_path["apps/worker/astrolift.toml"].created is True
    assert by_path["apps/web/astrolift.toml"].created is False
    assert by_path["apps/api/astrolift.toml"].created is False


# ---------------------------------------------------------------------------
# Acceptance 4: pure-agent manifests are ignored
# ---------------------------------------------------------------------------


def test_pure_agent_manifests_are_ignored(org, with_connection):
    with_connection(org)
    project = _project(org)
    tree = _tree_of(
        {
            # A pure agent at the root — ignored (owned by the agent path).
            "astrolift.toml": _AGENT_TOML,
            # A pure agent under apps/ — ignored.
            "apps/bot/astrolift.toml": _AGENT_TOML,
            # The one real app.
            "apps/web/astrolift.toml": _app_toml("web"),
        }
    )

    result = register_app_repo(
        project=project, source_kind="github", source_repo="acme/mixed", ref="main", tree=tree
    )

    assert result.status == "ok"
    assert [a.slug for a in result.apps] == ["web"]
    apps = RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True)
    assert apps.count() == 1
    assert apps.first().manifest_path == "apps/web/astrolift.toml"


def test_repo_with_no_apps_returns_no_apps(org, with_connection):
    with_connection(org)
    project = _project(org)
    result = register_app_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        tree=_tree_of({"astrolift.toml": _AGENT_TOML, "README.md": "docs"}),
    )
    assert result.status == "no_apps"
    assert result.apps == []
    assert RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True).count() == 0


# ---------------------------------------------------------------------------
# Acceptance 5: tenancy + preview behaviour
# ---------------------------------------------------------------------------


def test_registration_is_scoped_to_project_org(org, with_connection):
    with_connection(org)
    project_a = _project(org, slug="team-a")
    files = {"apps/web/astrolift.toml": _app_toml("web")}

    res_a = register_app_repo(
        project=project_a,
        source_kind="github",
        source_repo="shared/monorepo",
        ref="main",
        tree=_tree_of(files),
    )
    assert res_a.status == "ok"

    app_a = RegisteredApp.objects.get(source_repo="shared/monorepo", deleted_at__isnull=True)
    assert app_a.organization_id == org.id
    assert app_a.project_id == project_a.id


def test_same_repo_and_manifest_register_independently_per_organization(org, other_org, with_connection):
    with_connection(org)
    with_connection(other_org)
    project_a = _project(org, slug="team-a")
    project_b = _project(other_org, slug="team-b")
    files = {"apps/web/astrolift.toml": _app_toml("web")}

    first = register_app_repo(
        project=project_a,
        source_kind="github",
        source_repo="shared/monorepo",
        ref="main",
        tree=_tree_of(files),
    )
    second = register_app_repo(
        project=project_b,
        source_kind="github",
        source_repo="shared/monorepo",
        ref="main",
        tree=_tree_of(files),
    )

    assert first.status == "ok"
    assert second.status == "ok"
    apps = RegisteredApp.objects.filter(
        source_repo="shared/monorepo",
        manifest_path="apps/web/astrolift.toml",
        deleted_at__isnull=True,
    )
    assert apps.count() == 2
    assert set(apps.values_list("organization_id", flat=True)) == {org.id, other_org.id}


def test_discovery_does_not_report_another_organizations_registration(org, other_org, with_connection):
    with_connection(org)
    with_connection(other_org)
    other_project = _project(other_org, slug="other")
    files = {"apps/web/astrolift.toml": _app_toml("web")}
    registered = register_app_repo(
        project=other_project,
        source_kind="github",
        source_repo="shared/monorepo",
        ref="main",
        tree=_tree_of(files),
    )
    assert registered.status == "ok"

    preview = discover_app_manifests(
        organization_id=org.id,
        source_kind="github",
        source_repo="shared/monorepo",
        ref="main",
        tree=_tree_of(files),
    )

    assert preview.status == "ok"
    assert len(preview.apps) == 1
    assert preview.apps[0].already_registered is False


def test_discover_preview_does_not_persist(org, with_connection):
    with_connection(org)
    _project(org)
    result = discover_app_manifests(
        organization_id=org.id,
        source_kind="github",
        source_repo="acme/monorepo",
        ref="main",
        tree=_tree_of(
            {
                "apps/web/astrolift.toml": _app_toml("web"),
                "apps/api/astrolift.toml": _app_toml("api"),
            }
        ),
    )
    assert result.status == "ok"
    assert {a.name for a in result.apps} == {"web", "api"}
    assert all(a.already_registered is False for a in result.apps)
    assert RegisteredApp.objects.filter(organization=org).count() == 0


def test_discover_preview_flags_already_registered(org, with_connection):
    with_connection(org)
    project = _project(org)
    files = {
        "apps/web/astrolift.toml": _app_toml("web"),
        "apps/api/astrolift.toml": _app_toml("api"),
    }
    register_app_repo(
        project=project,
        source_kind="github",
        source_repo="acme/monorepo",
        ref="main",
        tree=_tree_of({"apps/web/astrolift.toml": _app_toml("web")}),
    )
    preview = discover_app_manifests(
        organization_id=org.id,
        source_kind="github",
        source_repo="acme/monorepo",
        ref="main",
        tree=_tree_of(files),
    )
    by_name = {a.name: a for a in preview.apps}
    assert by_name["web"].already_registered is True
    assert by_name["api"].already_registered is False


def test_fetch_failed_without_connection(org):
    project = _project(org)
    result = register_app_repo(
        project=project,
        source_kind="github",
        source_repo="acme/monorepo",
        ref="main",
        tree=_tree_of({"apps/web/astrolift.toml": _app_toml("web")}),
    )
    assert result.status == "fetch_failed"
    assert "source connection" in (result.error or "")


# ---------------------------------------------------------------------------
# Mutation surface: registerAppRepo tenancy + envelope
# ---------------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _enter(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_register_app_repo_mutation_happy_path(
    monkeypatch, org, with_connection, permission_resolver, seed_cluster
):
    with_connection(org)
    seed_cluster(org)  # apps are deploy targets — a managed cluster is required.
    project = _project(org)
    permission_resolver.grant(Permission.APP_CREATE)

    import astrolift_registry.services.manifest_sync as ms

    monkeypatch.setattr(
        ms,
        "_default_tree_fetch",
        lambda connection, repo, ref: {
            "apps/web/astrolift.toml": _app_toml("web"),
            "apps/api/astrolift.toml": _app_toml("api"),
        },
    )

    with _enter(org):
        result = RegistryMutation().register_app_repo(
            _info(),
            input=RegisterAppRepoInput(
                project_id=str(project.guid),
                source_repo="acme/monorepo",
            ),
        )

    assert result.ok is True, result.errors
    assert {a.slug for a in result.data.apps} == {"web", "api"}
    apps = RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True)
    assert apps.count() == 2
    # Each created app is bound to the managed cluster (deployable, at parity
    # with register_app).
    assert all(a.default_tenant_cluster_id is not None for a in apps)


def test_register_app_repo_mutation_requires_managed_cluster(
    monkeypatch, org, with_connection, permission_resolver
):
    # No managed cluster seeded → precondition trips before anything registers.
    with_connection(org)
    project = _project(org)
    permission_resolver.grant(Permission.APP_CREATE)

    import astrolift_registry.services.manifest_sync as ms

    monkeypatch.setattr(
        ms, "_default_tree_fetch", lambda connection, repo, ref: {"apps/web/astrolift.toml": _app_toml("web")}
    )

    with _enter(org):
        result = RegistryMutation().register_app_repo(
            _info(),
            input=RegisterAppRepoInput(project_id=str(project.guid), source_repo="acme/monorepo"),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PRECONDITION.value
    assert RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True).count() == 0


def test_register_app_repo_mutation_rejects_foreign_org_project(
    monkeypatch, org, other_org, with_connection, permission_resolver, seed_cluster
):
    with_connection(other_org)
    seed_cluster(other_org)
    foreign_project = _project(other_org, slug="foreign")
    permission_resolver.grant(Permission.APP_CREATE)

    import astrolift_registry.services.manifest_sync as ms

    monkeypatch.setattr(
        ms, "_default_tree_fetch", lambda connection, repo, ref: {"apps/web/astrolift.toml": _app_toml("web")}
    )

    with _enter(org):
        result = RegistryMutation().register_app_repo(
            _info(),
            input=RegisterAppRepoInput(
                project_id=str(foreign_project.guid),
                source_repo="acme/monorepo",
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    assert RegisteredApp.objects.filter(deleted_at__isnull=True).count() == 0


def test_register_app_repo_mutation_denied_without_permission(org, with_connection):
    with_connection(org)
    project = _project(org)
    with _enter(org):
        result = RegistryMutation().register_app_repo(
            _info(),
            input=RegisterAppRepoInput(project_id=str(project.guid), source_repo="acme/monorepo"),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value


def test_register_app_repo_mutation_no_apps_envelope(
    monkeypatch, org, with_connection, permission_resolver, seed_cluster
):
    with_connection(org)
    seed_cluster(org)
    project = _project(org)
    permission_resolver.grant(Permission.APP_CREATE)

    import astrolift_registry.services.manifest_sync as ms

    monkeypatch.setattr(
        ms, "_default_tree_fetch", lambda connection, repo, ref: {"astrolift.toml": _AGENT_TOML}
    )

    with _enter(org):
        result = RegistryMutation().register_app_repo(
            _info(),
            input=RegisterAppRepoInput(project_id=str(project.guid), source_repo="acme/agents"),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value
