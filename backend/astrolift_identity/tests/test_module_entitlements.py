"""Acceptance tests for the entity-module RBAC foundation (spec 36 §0.6).

Phase 0 makes the Agents + Workflows modules grantable mix-and-match:
standalone ``agent.*`` / ``workflow.*`` permissions, role grants that
preserve existing access, the re-gated agent workload/run resolvers, and
the server-computed ``me.modules`` capability manifest.

The four acceptance items from §0.6 map to the test classes below:

1. :class:`TestMixAndMatch` — the core proof: a principal with
   ``agent.read`` but NOT ``app.read`` can query the agent workload
   surface, cannot query the apps surface, and ``me.modules`` reflects
   ``{agents.canView=true, apps.canView=false}`` (and the inverse).
2. :class:`TestModulesPerRole` — ``me.modules`` per system role
   (owner → all true; viewer → view-only; developer → view+create+run,
   not manage; anonymous → ``[]``; superuser → all true).
3. :class:`TestNoAccessRegression` — a user under a role that held
   ``app.read`` still sees agents/workflows, because §0.2 granted the
   parallel reads onto every such role.
4. :class:`TestRegatedResolversReject` — the re-gated agent resolvers
   reject a caller without the new perm.

Tests follow the agents/identity convention: resolvers are exercised by
direct invocation with a real Postgres, a bound :class:`TenantContext`,
and — for the resolver gates — the controllable ``permission_resolver``
fixture. ``me.modules`` computes from real ``RoleBinding`` rows (it
routes through ``resolve_effective_permissions``, which reads the
bindings table, not the pluggable resolver), so those assertions seed
bindings directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_agents.schema.queries import AgentsQuery
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Role, RoleBinding
from astrolift_identity.schema.queries import MeType
from astrolift_identity.system_roles import SYSTEM_ROLES
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """The ``me`` resolver path doesn't index profiles, but seeding orgs
    elsewhere can; mirror the no-op stub the other identity tests use so
    OpenSearch is never reached in unit tests."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
        raising=False,
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
        raising=False,
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-modules-test")


@pytest.fixture
def user():
    User = get_user_model()
    return User.objects.create(username="member@modules", email="member@modules")


def _info_for(user=None):
    """``info``-shaped object whose ``request.user`` is ``user`` (or an
    anonymous user when ``None``). ``me.modules`` reads is_staff /
    is_superuser off this user."""
    if user is None:
        request_user = SimpleNamespace(
            is_authenticated=False, is_superuser=False, is_staff=False, is_active=True
        )
    else:
        request_user = user
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=request_user)))


def _bind(user, org, role_slug, perms):
    """Create a custom org-scoped Role with ``perms`` and bind ``user``."""
    role = Role.objects.create(
        name=role_slug,
        slug=role_slug,
        scope_level=Role.ScopeLevel.ORG,
        permissions=list(perms),
        is_system=False,
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.id)
    return role


def _system_perms(slug: str) -> list[str]:
    """The permission slugs a system role ships with, straight from the
    code source of truth (mirrors what the resync migration persists)."""
    for s, _scope, _name, _desc, perms in SYSTEM_ROLES:
        if s == slug:
            return [p.value for p in perms]
    raise AssertionError(f"unknown system role {slug!r}")


def _bind_system(user, org, slug: str):
    """Bind ``user`` to a system role rebuilt from ``SYSTEM_ROLES`` (so
    the test doesn't depend on the resync migration having run in this
    DB — the role row carries the same persisted slug list either way)."""
    return _bind(user, org, slug, _system_perms(slug))


def _modules(me_field_owner: MeType, info) -> dict[str, SimpleNamespace]:
    """Invoke ``MeType.modules`` and index the result by key."""
    rows = me_field_owner.modules(info)
    return {r.key: r for r in rows}


# ---------------------------------------------------------------------------
# §0.6.1 — Mix-and-match (the core proof)
# ---------------------------------------------------------------------------


class TestMixAndMatch:
    def test_agent_read_without_app_read(self, org, user, permission_resolver):
        """A principal with ``agent.read`` but NOT ``app.read`` CAN query
        the agent workload surface, CANNOT query the apps surface, and
        ``me.modules`` shows agents visible / apps not."""
        # --- resolver gate: only agent.read is allowed ----------------
        permission_resolver.grant(Permission.AGENT_READ)
        info = _info_for(user)

        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            # CAN query the agent workload surface (empty fleet → []).
            assert AgentsQuery().agent_workloads(info, org_id=GUID(str(org.guid))) == []
            assert AgentsQuery().agent_fleet(info, org_id=GUID(str(org.guid))) == []

            # CANNOT query the apps surface (app.read not granted).
            with pytest.raises(PermissionDenied):
                RegistryQuery().astrolift_apps(info)

        # --- me.modules reflects the split (real RoleBindings) --------
        _bind(user, org, "agent-reader", [Permission.AGENT_READ.value])
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            mods = _modules(MeType(id=str(user.pk), profile=None), info)
        assert mods["agents"].can_view is True
        assert mods["apps"].can_view is False

    def test_app_read_without_agent_read_inverse(self, org, user, permission_resolver):
        """Inverse: ``app.read`` but NOT ``agent.read`` CAN query the apps
        surface, CANNOT query the agent workload surface, and
        ``me.modules`` shows apps visible / agents not."""
        permission_resolver.grant(Permission.APP_READ)
        info = _info_for(user)

        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            # CAN query the apps surface (empty → []).
            assert RegistryQuery().astrolift_apps(info) == []

            # CANNOT query the agent workload surface (agent.read missing).
            with pytest.raises(PermissionDenied):
                AgentsQuery().agent_workloads(info, org_id=GUID(str(org.guid)))
            with pytest.raises(PermissionDenied):
                AgentsQuery().agent_fleet(info, org_id=GUID(str(org.guid)))

        _bind(user, org, "app-reader", [Permission.APP_READ.value])
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            mods = _modules(MeType(id=str(user.pk), profile=None), info)
        assert mods["apps"].can_view is True
        assert mods["agents"].can_view is False


# ---------------------------------------------------------------------------
# §0.6.2 — me.modules per role
# ---------------------------------------------------------------------------


class TestModulesPerRole:
    def test_org_owner_all_caps_true(self, org, user):
        """org_owner holds the full enum, so every module capability is
        true (every ``agent.*`` / ``workflow.*`` / ``app.*`` slug is in
        its set; admin slugs too)."""
        _bind_system(user, org, "org_owner")
        info = _info_for(user)
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            mods = _modules(MeType(id=str(user.pk), profile=None), info)

        for key in ("apps", "agents", "workflows", "admin"):
            m = mods[key]
            # admin.can_run is always false by mapping, even for owner —
            # there is no "run" verb on the admin module.
            assert m.can_view is True, key
            assert m.can_create is True, key
            assert m.can_manage is True, key
        assert mods["admin"].can_run is False
        for key in ("apps", "agents", "workflows"):
            assert mods[key].can_run is True, key

    def test_viewer_only_can_view(self, org, user):
        """A viewer role (app_viewer) sees only ``canView`` on the
        entities it can read; no create/manage/run."""
        _bind_system(user, org, "app_viewer")
        info = _info_for(user)
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            mods = _modules(MeType(id=str(user.pk), profile=None), info)

        for key in ("apps", "agents", "workflows"):
            m = mods[key]
            assert m.can_view is True, key
            assert m.can_create is False, key
            assert m.can_manage is False, key
            assert m.can_run is False, key
        # No admin slugs and not staff → admin module dark.
        assert mods["admin"].can_view is False

    def test_developer_view_create_run(self, org, user):
        """A developer role (team_developer) gets view+create+run on the
        agents/workflows modules, but NOT manage.

        Per §0.2 a developer is view+create+run, not manage: it holds
        ``agent.read``/``agent.create``/``agent.dispatch`` and
        ``workflow.read``/``workflow.create``/``workflow.trigger``, but
        NOT ``*.update``/``*.delete``. With §0.3's
        ``canManage = *.update | *.delete`` that yields
        ``canManage = False`` — consistent with §0.6.2 and the app
        baseline (developers have deploy ops but not ``app.update``/
        ``delete``; managing existing resources is admin-tier).
        """
        _bind_system(user, org, "team_developer")
        info = _info_for(user)
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            mods = _modules(MeType(id=str(user.pk), profile=None), info)

        for key in ("agents", "workflows"):
            m = mods[key]
            assert m.can_view is True, key
            assert m.can_create is True, key
            assert m.can_run is True, key
            # Developer holds no *.update/*.delete (§0.2), so
            # canManage = *.update | *.delete (§0.3) is False.
            assert m.can_manage is False, key

        # Apps: team_developer holds app.read + deploy ops but NOT
        # app.create/update/delete, so apps is view+run, not create/manage
        # — the same view+create+run-not-manage shape as the entity modules.
        assert mods["apps"].can_view is True
        assert mods["apps"].can_run is True
        assert mods["apps"].can_create is False
        assert mods["apps"].can_manage is False

    def test_anonymous_no_tenant_empty(self):
        """No tenant context → ``me.modules`` is ``[]`` (no slug leak)."""
        info = _info_for(None)
        # No tenant bound at all.
        assert MeType(id="0", profile=None).modules(info) == []

    def test_tenant_without_actor_empty(self, org):
        """Tenant exists but no actor → ``[]`` (defensive)."""
        info = _info_for(None)
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=None)):
            assert MeType(id="0", profile=None).modules(info) == []

    def test_superuser_all_caps_true(self, org):
        """Superuser → every capability true on every module (the bypass
        forces all-true, including admin.can_view/can_manage)."""
        User = get_user_model()
        su = User.objects.create_user(
            username="root@modules", email="root@modules", is_superuser=True, is_staff=True
        )
        info = _info_for(su)
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=su.id)):
            mods = _modules(MeType(id=str(su.pk), profile=None), info)

        for key in ("apps", "agents", "workflows", "admin"):
            m = mods[key]
            assert (m.can_view, m.can_create, m.can_manage, m.can_run) == (True, True, True, True), key

    def test_dashboard_never_listed(self, org, user):
        """``dashboard`` is always-visible and must NOT appear as a
        module row (the shell renders it unconditionally)."""
        _bind_system(user, org, "org_owner")
        info = _info_for(user)
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            mods = _modules(MeType(id=str(user.pk), profile=None), info)
        assert "dashboard" not in mods
        # Per-org modules (#1859, #2069, #1903) ride along as rows of their own.
        assert set(mods) == {
            "apps",
            "agents",
            "workflows",
            "admin",
            "chat_studio_integration",
            "agent_live_attach",
            "chat_studio_agent_runs",
            "agent_policy_enforcement",
        }


# ---------------------------------------------------------------------------
# §0.6.3 — No access regression
# ---------------------------------------------------------------------------


class TestNoAccessRegression:
    def test_env_spec_grants_preserve_legacy_app_crud_access(self):
        """Dedicated env-spec gates preserve every system role's old app gate."""
        parallels = {
            Permission.APP_READ.value: Permission.AGENT_ENV_SPEC_READ.value,
            Permission.APP_CREATE.value: Permission.AGENT_ENV_SPEC_CREATE.value,
            Permission.APP_UPDATE.value: Permission.AGENT_ENV_SPEC_UPDATE.value,
            Permission.APP_DELETE.value: Permission.AGENT_ENV_SPEC_DELETE.value,
        }
        for role_slug, _scope, _name, _description, permissions in SYSTEM_ROLES:
            granted = {permission.value for permission in permissions}
            for app_permission, env_spec_permission in parallels.items():
                if app_permission in granted:
                    assert (
                        env_spec_permission in granted
                    ), f"{role_slug} lost {env_spec_permission} while retaining {app_permission}"

    @pytest.mark.parametrize(
        "role_slug",
        [
            "org_owner",
            "org_admin",
            "org_auditor",
            "team_owner",
            "team_admin",
            "team_developer",
            "team_viewer",
            "project_admin",
            "project_developer",
            "project_viewer",
            "app_admin",
            "app_deployer",
            "app_viewer",
        ],
    )
    def test_role_with_app_read_now_sees_agents_and_workflows(self, org, user, role_slug):
        """Every system role that could see apps via ``app.read`` now
        also holds ``agent.read`` + ``workflow.read`` (§0.2), so the
        ``me.modules`` agents/workflows surfaces stay visible — no
        existing user loses access in the re-shell.

        This is asserted against the role's code-defined permission set
        (the same list the resync migration persists)."""
        perms = set(_system_perms(role_slug))
        # Precondition: the role is one we expect to read apps.
        assert Permission.APP_READ.value in perms, role_slug
        # The parallel reads must have been granted by §0.2.
        assert Permission.AGENT_READ.value in perms, role_slug
        assert Permission.WORKFLOW_READ.value in perms, role_slug

        _bind(user, org, role_slug, perms)
        info = _info_for(user)
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            mods = _modules(MeType(id=str(user.pk), profile=None), info)
        assert mods["agents"].can_view is True, role_slug
        assert mods["workflows"].can_view is True, role_slug

    def test_org_auditor_reads_via_read_all_set(self, org, user):
        """org_auditor's read-all set picked up AGENT_READ + WORKFLOW_READ
        (added to ``_READ_ALL``), so the auditor still sees the agent +
        workflow surfaces it could previously reach via app.read."""
        perms = set(_system_perms("org_auditor"))
        assert {Permission.AGENT_READ.value, Permission.WORKFLOW_READ.value} <= perms
        _bind(user, org, "org_auditor", perms)
        info = _info_for(user)
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
            mods = _modules(MeType(id=str(user.pk), profile=None), info)
        assert mods["agents"].can_view is True
        assert mods["workflows"].can_view is True


# ---------------------------------------------------------------------------
# §0.6.4 — Re-gated resolvers reject without the new perm
# ---------------------------------------------------------------------------


class TestRegatedResolversReject:
    def test_agent_workload_resolvers_reject_without_agent_read(self, org, permission_resolver):
        """The re-gated agent workload/run readers raise PermissionDenied
        when ``agent.read`` is absent — even if ``app.read`` is held
        (proves the gate moved off ``app.read``)."""
        # Grant the OLD perm only — the resolvers must no longer accept it.
        permission_resolver.grant(Permission.APP_READ)
        permission_resolver.grant(Permission.APP_READ_LOGS)
        info = _info_for(None)

        gid = GUID(str(org.guid))
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=1)):
            with pytest.raises(PermissionDenied):
                AgentsQuery().agent_workloads(info, org_id=gid)
            with pytest.raises(PermissionDenied):
                AgentsQuery().agent_fleet(info, org_id=gid)
            with pytest.raises(PermissionDenied):
                AgentsQuery().agent_live_status(info, org_id=gid)
            with pytest.raises(PermissionDenied):
                AgentsQuery().agent_tasks(info, org_id=gid)
            with pytest.raises(PermissionDenied):
                AgentsQuery().scan_agent_manifests(info, org_id=gid, source_repo="acme/agents")
            # agent_task_logs collapsed from app.read_logs to agent.read.
            with pytest.raises(PermissionDenied):
                AgentsQuery().agent_task_logs(info, id=gid)

    def test_agent_resolvers_pass_with_agent_read(self, org, permission_resolver):
        """With ``agent.read`` granted, the list resolvers run (empty
        fleet → ``[]``) — the gate accepts the new perm."""
        permission_resolver.grant(Permission.AGENT_READ)
        info = _info_for(None)
        gid = GUID(str(org.guid))
        with tenant_context(TenantContext(organization_id=org.id, actor_user_id=1)):
            assert AgentsQuery().agent_workloads(info, org_id=gid) == []
            assert AgentsQuery().agent_fleet(info, org_id=gid) == []
            assert AgentsQuery().agent_live_status(info, org_id=gid) == []

    def test_register_agent_repo_requires_agent_create(self, org, permission_resolver):
        """``registerAgentRepo`` re-gated APP_CREATE → AGENT_CREATE: it
        rejects when only the old APP_CREATE is held, and the gate
        accepts AGENT_CREATE.

        Asserted via the resolver's declared permission metadata
        (``__astrolift_permissions__``) so the test doesn't have to drive
        the full repo-scan mutation body — the gate is what changed."""
        from astrolift_registry.schema.mutations import RegistryMutation

        gate = RegistryMutation.register_agent_repo.__astrolift_permissions__
        assert gate == (Permission.AGENT_CREATE,)

    def test_run_astrolift_agent_unchanged_dispatch(self):
        """``runAstroliftAgent`` stays on AGENT_DISPATCH (no Phase-0
        change) — guards against an accidental re-gate."""
        from astrolift_agents.schema.mutations import AgentsMutation

        gate = AgentsMutation.run_astrolift_agent.__astrolift_permissions__
        assert gate == (Permission.AGENT_DISPATCH,)
