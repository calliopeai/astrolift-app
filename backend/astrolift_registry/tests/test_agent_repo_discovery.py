"""Monorepo agent discovery + registration (spec 33, PR-3).

Covers the five acceptance cases for "point at a repo, register each agent
as an agent Workload", against real Postgres. The repo tree is injected (a
fixture ``{path: contents}`` map) so the production service code path runs
end-to-end with only the SCM I/O stubbed — never a real GitHub call:

  1. a monorepo with agents/a + agents/b registers TWO agent Workloads;
  2. a single root manifest registers ONE;
  3. a re-scan after adding agents/c adds the third WITHOUT duplicating the
     existing two;
  4. non-agent manifests are IGNORED;
  5. all rows are tenancy-scoped to the project (a foreign-org project is
     rejected; a re-scan stays within the repo's own apps).

Plus the ``registerAgentRepo`` mutation's tenancy + envelope behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import AppTeamAccess, Container, RegisteredApp, Workload
from astrolift_registry.schema.mutations import RegisterAgentRepoInput, RegistryMutation
from astrolift_registry.services.manifest_sync import (
    discover_agent_manifests,
    register_agent_repo,
    resync_agent_repo_manifests,
)
from astrolift_scm.models import SourceConnection
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- fixtures ---------------------------------------------------------


def _agent_toml(name: str, *, kind: str = "agent") -> str:
    return (
        f'name = "{name}"\n'
        "[[workloads]]\n"
        f'name = "{name}"\n'
        f'kind = "{kind}"\n'
        "[[workloads.containers]]\n"
        f'name = "{name}"\n'
        "is_primary = true\n"
        'image_ref = "ecr.example/agent:latest"\n'
    )


_WEB_TOML = (
    'name = "web"\n'
    "[[workloads]]\n"
    'name = "web"\n'
    'kind = "deployment"\n'
    "is_public = true\n"
    "[[workloads.containers]]\n"
    'name = "web"\n'
    "is_primary = true\n"
    "port = 8080\n"
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
    """Give an org a GitHub source connection so the picker resolves one."""

    def _make(org):
        return SourceConnection.objects.create(
            organization=org,
            kind=SourceConnection.Kind.GITHUB_PAT,
            display_name=f"{org.slug} PAT",
            account_login=org.slug,
        )

    return _make


def _tree_of(files: dict[str, str]):
    """Build an injectable ``_TreeFn`` returning a fixed file map.

    Asserts the service passes the repo handle through unchanged (a guard
    that the wiring doesn't silently swap repos)."""

    def _tree(connection, repo_full_name, ref):
        _tree.calls.append((repo_full_name, ref))
        return dict(files)

    _tree.calls = []
    return _tree


# ---------------------------------------------------------------------------
# Acceptance 1: monorepo with two agents registers two agent Workloads
# ---------------------------------------------------------------------------


def test_monorepo_registers_two_agent_workloads(org, with_connection):
    with_connection(org)
    project = _project(org)
    tree = _tree_of(
        {
            "agents/triage/astrolift.toml": _agent_toml("triage"),
            "agents/summarize/astrolift.toml": _agent_toml("summarize"),
            "README.md": "docs",
        }
    )

    result = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        tree=tree,
    )

    assert result.status == "ok"
    assert {a.slug for a in result.agents} == {"triage", "summarize"}
    assert all(a.created for a in result.agents)
    assert tree.calls == [("acme/agents", "main")]

    # Two agent Workloads, each under its own app, all scoped to the org.
    workloads = Workload.objects.filter(
        kind=Workload.Kind.AGENT,
        registered_app__organization=org,
        deleted_at__isnull=True,
    )
    assert workloads.count() == 2
    apps = RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True)
    assert apps.count() == 2
    assert (
        AppTeamAccess.objects.filter(
            registered_app__in=apps,
            team=project.team,
            access_level=AppTeamAccess.AccessLevel.OWNER.value,
            deleted_at__isnull=True,
        ).count()
        == 2
    )
    # Distinct manifest paths is what the (source_repo, manifest_path) key keys on.
    assert {a.manifest_path for a in apps} == {
        "agents/triage/astrolift.toml",
        "agents/summarize/astrolift.toml",
    }
    # PR-1 run-spec defaults carried by a fresh agent workload.
    w = workloads.get(slug="triage")
    assert w.run_family == Workload.RunFamily.TASK
    assert w.run_mode == Workload.RunMode.ONCE
    assert w.run_paused is False
    # The container row was persisted from the manifest too.
    assert Container.objects.filter(workload=w, deleted_at__isnull=True).count() == 1


# ---------------------------------------------------------------------------
# Acceptance 2: a single root manifest registers ONE agent
# ---------------------------------------------------------------------------


def test_single_root_manifest_registers_one_agent(org, with_connection):
    with_connection(org)
    project = _project(org)
    tree = _tree_of({"astrolift.toml": _agent_toml("solo"), "Dockerfile": "FROM scratch"})

    result = register_agent_repo(
        project=project, source_kind="github", source_repo="acme/solo", ref="main", tree=tree
    )

    assert result.status == "ok"
    assert [a.slug for a in result.agents] == ["solo"]
    assert (
        Workload.objects.filter(
            kind=Workload.Kind.AGENT, registered_app__organization=org, deleted_at__isnull=True
        ).count()
        == 1
    )
    app = RegisteredApp.objects.get(organization=org, deleted_at__isnull=True)
    assert app.manifest_path == "astrolift.toml"


# ---------------------------------------------------------------------------
# Acceptance 3: re-scan after adding agents/c adds the third, no duplicates
# ---------------------------------------------------------------------------


def test_rescan_adds_new_agent_without_duplicating(org, with_connection):
    with_connection(org)
    project = _project(org)

    first = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        tree=_tree_of(
            {
                "agents/a/astrolift.toml": _agent_toml("a"),
                "agents/b/astrolift.toml": _agent_toml("b"),
            }
        ),
    )
    assert first.status == "ok"
    assert len(first.agents) == 2

    # A third agent appears in the repo; re-scan picks it up.
    rescan = resync_agent_repo_manifests(
        project=project,
        source_repo="acme/agents",
        tree=_tree_of(
            {
                "agents/a/astrolift.toml": _agent_toml("a"),
                "agents/b/astrolift.toml": _agent_toml("b"),
                "agents/c/astrolift.toml": _agent_toml("c"),
            }
        ),
    )
    assert rescan.status == "ok"

    # Exactly three agent workloads total — the original two were NOT
    # duplicated (idempotent on (source_repo, manifest_path)).
    workloads = Workload.objects.filter(
        kind=Workload.Kind.AGENT,
        registered_app__organization=org,
        deleted_at__isnull=True,
    )
    assert workloads.count() == 3
    assert {w.slug for w in workloads} == {"a", "b", "c"}
    apps = RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True)
    assert apps.count() == 3

    # The re-scan reports c as created and a/b as matched (not created).
    by_slug = {a.slug: a for a in rescan.agents}
    assert by_slug["c"].created is True
    assert by_slug["a"].created is False
    assert by_slug["b"].created is False


def test_rescan_with_no_changes_is_idempotent(org, with_connection):
    with_connection(org)
    project = _project(org)
    files = {"agents/a/astrolift.toml": _agent_toml("a")}

    register_agent_repo(
        project=project, source_kind="github", source_repo="acme/agents", ref="main", tree=_tree_of(files)
    )
    # Re-running the same tree adds nothing new.
    again = resync_agent_repo_manifests(project=project, source_repo="acme/agents", tree=_tree_of(files))
    assert again.status == "ok"
    assert all(a.created is False for a in again.agents)
    assert RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True).count() == 1


def test_rescan_leaves_disappeared_agent_in_place(org, with_connection):
    """A manifest removed from the repo is NOT deleted on re-scan (documented
    no-prune policy: we never hard-delete onboarding data)."""
    with_connection(org)
    project = _project(org)

    register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        tree=_tree_of(
            {
                "agents/a/astrolift.toml": _agent_toml("a"),
                "agents/b/astrolift.toml": _agent_toml("b"),
            }
        ),
    )
    # b is gone from the repo on the next scan.
    rescan = resync_agent_repo_manifests(
        project=project,
        source_repo="acme/agents",
        tree=_tree_of({"agents/a/astrolift.toml": _agent_toml("a")}),
    )
    assert rescan.status == "ok"

    # Both apps + workloads still exist; b was left in place, not pruned.
    apps = RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True)
    assert {a.manifest_path for a in apps} == {
        "agents/a/astrolift.toml",
        "agents/b/astrolift.toml",
    }
    assert (
        Workload.objects.filter(
            kind=Workload.Kind.AGENT, registered_app__organization=org, deleted_at__isnull=True
        ).count()
        == 2
    )


# ---------------------------------------------------------------------------
# Acceptance 4: non-agent manifests are ignored
# ---------------------------------------------------------------------------


def test_non_agent_manifests_are_ignored(org, with_connection):
    with_connection(org)
    project = _project(org)
    tree = _tree_of(
        {
            # A web app at the root — ignored.
            "astrolift.toml": _WEB_TOML,
            # A non-agent workload under agents/ — ignored.
            "agents/web/astrolift.toml": _WEB_TOML,
            # The one real agent.
            "agents/triage/astrolift.toml": _agent_toml("triage"),
        }
    )

    result = register_agent_repo(
        project=project, source_kind="github", source_repo="acme/mixed", ref="main", tree=tree
    )

    assert result.status == "ok"
    assert [a.slug for a in result.agents] == ["triage"]
    # Only the agent app was created — the two web manifests registered nothing.
    apps = RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True)
    assert apps.count() == 1
    assert apps.first().manifest_path == "agents/triage/astrolift.toml"


def test_repo_with_no_agents_returns_no_agents(org, with_connection):
    with_connection(org)
    project = _project(org)
    result = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/web",
        ref="main",
        tree=_tree_of({"astrolift.toml": _WEB_TOML, "README.md": "docs"}),
    )
    assert result.status == "no_agents"
    assert result.agents == []
    assert RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True).count() == 0


# ---------------------------------------------------------------------------
# #933: manifestPaths subset registration
# ---------------------------------------------------------------------------


def test_manifest_paths_registers_only_the_requested_subset(org, with_connection):
    """With manifest_paths given, only the listed manifests register; the
    others are left untouched."""
    with_connection(org)
    project = _project(org)
    tree = _tree_of(
        {
            "agents/triage/astrolift.toml": _agent_toml("triage"),
            "agents/summarize/astrolift.toml": _agent_toml("summarize"),
            "agents/report/astrolift.toml": _agent_toml("report"),
        }
    )

    result = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        manifest_paths=["agents/triage/astrolift.toml", "agents/report/astrolift.toml"],
        tree=tree,
    )

    assert result.status == "ok"
    assert {a.manifest_path for a in result.agents} == {
        "agents/triage/astrolift.toml",
        "agents/report/astrolift.toml",
    }
    # Only the two requested agents landed — summarize was excluded.
    apps = RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True)
    assert {a.manifest_path for a in apps} == {
        "agents/triage/astrolift.toml",
        "agents/report/astrolift.toml",
    }
    assert (
        Workload.objects.filter(
            kind=Workload.Kind.AGENT, registered_app__organization=org, deleted_at__isnull=True
        ).count()
        == 2
    )


def test_manifest_paths_omitted_registers_all(org, with_connection):
    """Omitting manifest_paths preserves the register-everything behaviour."""
    with_connection(org)
    project = _project(org)
    tree = _tree_of(
        {
            "agents/triage/astrolift.toml": _agent_toml("triage"),
            "agents/summarize/astrolift.toml": _agent_toml("summarize"),
        }
    )

    result = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        manifest_paths=None,
        tree=tree,
    )

    assert result.status == "ok"
    assert {a.manifest_path for a in result.agents} == {
        "agents/triage/astrolift.toml",
        "agents/summarize/astrolift.toml",
    }


def test_manifest_paths_ignores_unknown_path_in_mixed_request(org, with_connection):
    """A request that mixes a known and an unknown path registers the known
    one and silently drops the unknown — a stale selection never fails the
    whole pass."""
    with_connection(org)
    project = _project(org)
    tree = _tree_of(
        {
            "agents/triage/astrolift.toml": _agent_toml("triage"),
            "agents/summarize/astrolift.toml": _agent_toml("summarize"),
        }
    )

    result = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        manifest_paths=["agents/triage/astrolift.toml", "agents/ghost/astrolift.toml"],
        tree=tree,
    )

    assert result.status == "ok"
    assert {a.manifest_path for a in result.agents} == {"agents/triage/astrolift.toml"}
    assert RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True).count() == 1


def test_manifest_paths_all_unknown_is_no_match(org, with_connection):
    """When every requested path is unknown, nothing registers and the result
    is a clear no_match (not a misleading no_agents)."""
    with_connection(org)
    project = _project(org)
    tree = _tree_of({"agents/triage/astrolift.toml": _agent_toml("triage")})

    result = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        manifest_paths=["agents/nope/astrolift.toml"],
        tree=tree,
    )

    assert result.status == "no_match"
    assert "agents/triage/astrolift.toml" in (result.error or "")
    assert RegisteredApp.objects.filter(organization=org, deleted_at__isnull=True).count() == 0


# ---------------------------------------------------------------------------
# Acceptance 5: tenancy — rows scoped to the project's org
# ---------------------------------------------------------------------------


def test_registration_is_scoped_to_project_org(org, other_org, with_connection):
    """Registering under one org's project creates rows only in that org."""
    with_connection(org)
    with_connection(other_org)
    project_a = _project(org, slug="team-a")
    files = {"agents/triage/astrolift.toml": _agent_toml("triage")}

    res_a = register_agent_repo(
        project=project_a, source_kind="github", source_repo="shared/agents", ref="main", tree=_tree_of(files)
    )
    assert res_a.status == "ok"

    # All rows live under org A's project.
    app_a = RegisteredApp.objects.get(source_repo="shared/agents", deleted_at__isnull=True)
    assert app_a.organization_id == org.id
    assert app_a.project_id == project_a.id
    assert Workload.objects.filter(registered_app=app_a, kind=Workload.Kind.AGENT).count() == 1

    # org B sees zero agent workloads of its own.
    assert (
        Workload.objects.filter(
            kind=Workload.Kind.AGENT,
            registered_app__organization=other_org,
            deleted_at__isnull=True,
        ).count()
        == 0
    )


def test_duplicate_agent_slug_in_same_organization_fails_closed(org, with_connection):
    with_connection(org)
    project = _project(org, slug="duplicate-slug")
    files = {"agents/triage/astrolift.toml": _agent_toml("triage")}

    first = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents-one",
        ref="main",
        tree=_tree_of(files),
    )
    second = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents-two",
        ref="main",
        tree=_tree_of(files),
    )

    assert first.status == "ok"
    assert second.status == "error"
    assert "agent workload slug 'triage' is already registered" in (second.error or "")
    assert (
        Workload.objects.filter(
            registered_app__organization=org,
            slug="triage",
            deleted_at__isnull=True,
        ).count()
        == 1
    )


def test_discover_preview_does_not_persist(org, with_connection):
    with_connection(org)
    _project(org)
    result = discover_agent_manifests(
        organization_id=org.id,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        tree=_tree_of(
            {
                "agents/a/astrolift.toml": _agent_toml("a"),
                "agents/b/astrolift.toml": _agent_toml("b"),
            }
        ),
    )
    assert result.status == "ok"
    assert {a.slug for a in result.agents} == {"a", "b"}
    assert all(a.already_registered is False for a in result.agents)
    # Preview persists nothing.
    assert RegisteredApp.objects.filter(organization=org).count() == 0


def test_discover_preview_flags_already_registered(org, with_connection):
    with_connection(org)
    project = _project(org)
    files = {
        "agents/a/astrolift.toml": _agent_toml("a"),
        "agents/b/astrolift.toml": _agent_toml("b"),
    }
    # Register a only, then preview the repo: a is flagged, b is new.
    register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        tree=_tree_of({"agents/a/astrolift.toml": _agent_toml("a")}),
    )
    preview = discover_agent_manifests(
        organization_id=org.id,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        tree=_tree_of(files),
    )
    by_slug = {a.slug: a for a in preview.agents}
    assert by_slug["a"].already_registered is True
    assert by_slug["b"].already_registered is False


def test_fetch_failed_without_connection(org):
    # No SourceConnection for the org → the picker returns None → fetch_failed.
    project = _project(org)
    result = register_agent_repo(
        project=project,
        source_kind="github",
        source_repo="acme/agents",
        ref="main",
        tree=_tree_of({"agents/a/astrolift.toml": _agent_toml("a")}),
    )
    assert result.status == "fetch_failed"
    assert "source connection" in (result.error or "")


# ---------------------------------------------------------------------------
# Mutation surface: registerAgentRepo tenancy + envelope
# ---------------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _enter(org):
    return tenant_context(TenantContext(organization_id=org.id))


def test_register_agent_repo_mutation_happy_path(monkeypatch, org, with_connection, permission_resolver):
    with_connection(org)
    project = _project(org)
    # Re-gated APP_CREATE → AGENT_CREATE in the entity-module re-shell
    # (spec 36 §0.4).
    permission_resolver.grant(Permission.AGENT_CREATE)

    # Patch the service's tree fetch so the mutation runs end-to-end with no
    # network. The resolver calls register_agent_repo (no tree arg), which
    # falls back to _default_tree_fetch → fetch_repo_tree → fetch_zipball.
    import astrolift_registry.services.manifest_sync as ms

    monkeypatch.setattr(
        ms,
        "_default_tree_fetch",
        lambda connection, repo, ref: {
            "agents/triage/astrolift.toml": _agent_toml("triage"),
            "agents/summarize/astrolift.toml": _agent_toml("summarize"),
        },
    )

    with _enter(org):
        result = RegistryMutation().register_agent_repo(
            _info(),
            input=RegisterAgentRepoInput(
                project_id=str(project.guid),
                source_repo="acme/agents",
            ),
        )

    assert result.ok is True
    assert {a.slug for a in result.data.agents} == {"triage", "summarize"}
    assert (
        Workload.objects.filter(
            kind=Workload.Kind.AGENT, registered_app__organization=org, deleted_at__isnull=True
        ).count()
        == 2
    )


def test_register_agent_repo_mutation_rejects_foreign_org_project(
    monkeypatch, org, other_org, with_connection, permission_resolver
):
    """A project GUID belonging to another org is rejected even with the
    permission granted — the active tenant is the boundary."""
    with_connection(other_org)
    foreign_project = _project(other_org, slug="foreign")
    # Grant the real gate (AGENT_CREATE after the §0.4 re-gate) so the
    # rejection that this test asserts is the foreign-org boundary, not
    # the permission gate firing first.
    permission_resolver.grant(Permission.AGENT_CREATE)

    import astrolift_registry.services.manifest_sync as ms

    monkeypatch.setattr(
        ms,
        "_default_tree_fetch",
        lambda connection, repo, ref: {"agents/a/astrolift.toml": _agent_toml("a")},
    )

    # Caller's active tenant is ``org``, but the project belongs to ``other_org``.
    with _enter(org):
        result = RegistryMutation().register_agent_repo(
            _info(),
            input=RegisterAgentRepoInput(
                project_id=str(foreign_project.guid),
                source_repo="acme/agents",
            ),
        )

    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value
    # Nothing was registered in either org.
    assert RegisteredApp.objects.filter(deleted_at__isnull=True).count() == 0


def test_register_agent_repo_mutation_denied_without_permission(org, with_connection):
    with_connection(org)
    project = _project(org)
    # No permission grant installed → the @require_permission gate denies.
    with _enter(org):
        result = RegistryMutation().register_agent_repo(
            _info(),
            input=RegisterAgentRepoInput(project_id=str(project.guid), source_repo="acme/agents"),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.PERMISSION_DENIED.value


def test_register_agent_repo_mutation_no_agents_envelope(
    monkeypatch, org, with_connection, permission_resolver
):
    with_connection(org)
    project = _project(org)
    # Re-gated APP_CREATE → AGENT_CREATE in the entity-module re-shell
    # (spec 36 §0.4).
    permission_resolver.grant(Permission.AGENT_CREATE)

    import astrolift_registry.services.manifest_sync as ms

    monkeypatch.setattr(
        ms, "_default_tree_fetch", lambda connection, repo, ref: {"astrolift.toml": _WEB_TOML}
    )

    with _enter(org):
        result = RegistryMutation().register_agent_repo(
            _info(),
            input=RegisterAgentRepoInput(project_id=str(project.guid), source_repo="acme/web"),
        )
    assert result.ok is False
    assert result.errors[0].code == ErrorCode.NOT_FOUND.value


def test_register_agent_repo_mutation_manifest_paths_subset(
    monkeypatch, org, with_connection, permission_resolver
):
    """The mutation threads manifestPaths into the service so only the checked
    agents register (#933)."""
    with_connection(org)
    project = _project(org)
    permission_resolver.grant(Permission.AGENT_CREATE)

    import astrolift_registry.services.manifest_sync as ms

    monkeypatch.setattr(
        ms,
        "_default_tree_fetch",
        lambda connection, repo, ref: {
            "agents/triage/astrolift.toml": _agent_toml("triage"),
            "agents/summarize/astrolift.toml": _agent_toml("summarize"),
        },
    )

    with _enter(org):
        result = RegistryMutation().register_agent_repo(
            _info(),
            input=RegisterAgentRepoInput(
                project_id=str(project.guid),
                source_repo="acme/agents",
                manifest_paths=["agents/summarize/astrolift.toml"],
            ),
        )

    assert result.ok is True
    assert {a.manifest_path for a in result.data.agents} == {"agents/summarize/astrolift.toml"}
    assert (
        Workload.objects.filter(
            kind=Workload.Kind.AGENT, registered_app__organization=org, deleted_at__isnull=True
        ).count()
        == 1
    )
