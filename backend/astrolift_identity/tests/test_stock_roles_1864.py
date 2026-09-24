"""The stock role catalogue (#1864): pinned, on every install, and doing what each tier says.

* **Pinned.** Every system role's permission list is written out below, slug
  by slug. Org owner's list is the whole ``Permission`` enum and org admin's
  is all of it but ``org.delete`` and ``billing.update``, so adding a
  permission fails here as well. Update the pin, and add a resync migration
  (copy ``0034``), or existing installs keep their old lists.
* **On every install.** ``0034`` turns an install holding the catalogue as it
  stood before #1864 (these pins without the three new roles and the eight
  new Zentinelle permissions) into this one, in place and idempotently.
* **The tiers hold through the real resolver.** An operator runs, attaches
  and cancels inside its scope and configures nothing; the org viewer reads
  the whole org but not its audit log.
* **Read-only, clonable.** The new system roles refuse edits and deletes and
  clone into a custom role.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.db import migrations

from astrolift_agents.models import AgentTask
from astrolift_agents.scopes import agent_task_scope
from astrolift_agents.visibility import watchable_task_ids
from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_identity.permission_resolver import resolve
from astrolift_identity.schema.mutations import (
    CreateRoleInput,
    DeleteRoleInput,
    IdentityMutation,
    UpdateRoleInput,
)
from astrolift_identity.system_roles import SYSTEM_ROLES
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
from core.tenancy import TenantContext, tenant_context

User = get_user_model()

RESYNC = importlib.import_module("astrolift_identity.migrations.0034_resync_system_roles_stock_catalogue")

# Org owner holds every permission, org admin all but two.
EVERY_PERMISSION = frozenset(
    (
        "admin.elevate agent.create agent.delete agent.dispatch agent.read agent.update "
        "agent_box.attach agent_env_spec.create agent_env_spec.delete agent_env_spec.read "
        "agent_env_spec.update agent_task.send_input agent_task.watch api_token.create "
        "api_token.revoke app.approve_deploy app.create app.delete app.deploy app.exec_pod "
        "app.log_export app.read app.read_logs app.read_metrics app.rollback app.transfer app.update "
        "audit_log.export audit_log.read billing.read billing.update cluster.manage cluster.register "
        "cluster.unregister cluster.update deploy_token.create deploy_token.revoke deploy_token.rotate "
        "form.create form.delete form.moderate form.read form.submit form.update managed_service.adopt "
        "managed_service.create managed_service.destroy managed_service.update org.delete "
        "org.manage_members org.read org.update pipeline.cancel pipeline.create pipeline.delete "
        "pipeline.read pipeline.secret_manage pipeline.trigger pipeline.update project.create "
        "project.delete project.read project.update provider_plugin.configure provider_plugin.read "
        "scm.connect scm.disconnect scm.key_create scm.key_delete scm.read secret.approve secret.list "
        "secret.read secret.write skill.import skill.read skill.write team.create team.delete "
        "team.manage_members team.read team.update webhook.create webhook.delete webhook.update "
        "workflow.create workflow.delete workflow.read workflow.trigger workflow.update "
        "zentinelle.audit_export zentinelle.audit_view zentinelle.configure "
        "zentinelle.conformance_view zentinelle.connect zentinelle.gateway_manage "
        "zentinelle.policy_edit zentinelle.policy_view zentinelle.status_view zentinelle.usage_view"
    ).split()
)
FULL_ENUM = {
    "org_owner": EVERY_PERMISSION,
    "org_admin": EVERY_PERMISSION - {"org.delete", "billing.update"},
}

PINNED = {
    "org_billing": (
        "ORG",
        "billing.read billing.update",
    ),
    "org_auditor": (
        "ORG",
        "agent.read agent_env_spec.read app.log_export app.read app.read_logs app.read_metrics "
        "audit_log.export audit_log.read form.read org.read project.read secret.list skill.read "
        "team.read workflow.read zentinelle.audit_export zentinelle.audit_view "
        "zentinelle.policy_view zentinelle.status_view",
    ),
    "org_viewer": (
        "ORG",
        "agent.read agent_env_spec.read app.read app.read_logs app.read_metrics form.read org.read "
        "project.read secret.list skill.read team.read workflow.read zentinelle.status_view",
    ),
    "cluster_owner": (
        "ORG",
        "cluster.manage cluster.register cluster.unregister cluster.update "
        "provider_plugin.configure provider_plugin.read",
    ),
    "team_owner": (
        "TEAM",
        "agent.create agent.delete agent.dispatch agent.read agent.update agent_box.attach "
        "agent_env_spec.create agent_env_spec.delete agent_env_spec.read agent_env_spec.update "
        "app.create app.delete app.deploy app.log_export app.read app.read_logs app.read_metrics "
        "app.rollback app.update project.create project.delete project.read project.update "
        "secret.list secret.read secret.write skill.import skill.read skill.write team.delete "
        "team.manage_members team.read team.update workflow.create workflow.delete workflow.read "
        "workflow.update zentinelle.policy_view zentinelle.usage_view",
    ),
    "team_admin": (
        "TEAM",
        "agent.create agent.delete agent.dispatch agent.read agent.update agent_box.attach "
        "agent_env_spec.create agent_env_spec.read agent_env_spec.update app.create app.deploy "
        "app.log_export app.read app.read_logs app.read_metrics app.rollback app.update "
        "project.create project.read project.update secret.list secret.read secret.write "
        "skill.import skill.read skill.write team.manage_members team.read team.update "
        "workflow.create workflow.delete workflow.read workflow.update zentinelle.policy_view "
        "zentinelle.usage_view",
    ),
    "team_developer": (
        "TEAM",
        "agent.create agent.dispatch agent.read agent_box.attach agent_env_spec.read app.deploy "
        "app.log_export app.read app.read_logs app.read_metrics app.rollback form.read form.submit "
        "project.read secret.list secret.read secret.write skill.import skill.read team.read "
        "workflow.create workflow.read workflow.trigger zentinelle.policy_view "
        "zentinelle.usage_view",
    ),
    "team_operator": (
        "TEAM",
        "agent.dispatch agent.read agent_box.attach agent_env_spec.read agent_task.watch "
        "app.deploy app.log_export app.read app.read_logs app.read_metrics app.rollback "
        "project.read team.read workflow.read workflow.trigger zentinelle.policy_view "
        "zentinelle.usage_view",
    ),
    "team_viewer": (
        "TEAM",
        "agent.read agent_env_spec.read app.read app.read_logs app.read_metrics form.read "
        "form.submit project.read team.read workflow.read",
    ),
    "project_admin": (
        "PROJECT",
        "agent.create agent.delete agent.dispatch agent.read agent.update agent_box.attach "
        "agent_env_spec.create agent_env_spec.delete agent_env_spec.read agent_env_spec.update "
        "app.create app.delete app.deploy app.log_export app.read app.read_logs app.read_metrics "
        "app.rollback app.update project.read project.update secret.list secret.read secret.write "
        "workflow.create workflow.delete workflow.read workflow.update zentinelle.policy_view "
        "zentinelle.usage_view",
    ),
    "project_developer": (
        "PROJECT",
        "agent.create agent.dispatch agent.read agent_box.attach agent_env_spec.read app.deploy "
        "app.log_export app.read app.read_logs app.read_metrics app.rollback project.read "
        "secret.list secret.read secret.write workflow.create workflow.read workflow.trigger "
        "zentinelle.policy_view zentinelle.usage_view",
    ),
    "project_operator": (
        "PROJECT",
        "agent.dispatch agent.read agent_box.attach agent_env_spec.read agent_task.watch "
        "app.deploy app.log_export app.read app.read_logs app.read_metrics app.rollback "
        "project.read workflow.read workflow.trigger zentinelle.policy_view zentinelle.usage_view",
    ),
    "project_viewer": (
        "PROJECT",
        "agent.read agent_env_spec.read app.read app.read_logs app.read_metrics project.read "
        "workflow.read",
    ),
    "app_admin": (
        "APP",
        "agent.create agent.delete agent.dispatch agent.read agent.update agent_box.attach "
        "agent_env_spec.delete agent_env_spec.read agent_env_spec.update app.delete app.deploy "
        "app.log_export app.read app.read_logs app.read_metrics app.rollback app.update "
        "secret.list secret.read secret.write workflow.create workflow.delete workflow.read "
        "workflow.update",
    ),
    "app_deployer": (
        "APP",
        "agent.dispatch agent.read agent_box.attach agent_env_spec.read app.deploy app.read "
        "app.rollback workflow.read workflow.trigger",
    ),
    "app_developer": (
        "APP",
        "agent_env_spec.read app.log_export app.read app.read_logs app.read_metrics secret.list "
        "secret.read secret.write",
    ),
    "app_viewer": (
        "APP",
        "agent.read agent_env_spec.read app.read app.read_logs app.read_metrics workflow.read",
    ),
    "app_approver": (
        "APP",
        "agent_env_spec.read app.approve_deploy app.read secret.approve",
    ),
}

# What #1864 added. Every role that existed before it held exactly its pin
# without these permissions.
NEW_ROLES = frozenset({"org_viewer", "team_operator", "project_operator"})
NEW_PERMISSIONS = frozenset(
    {
        "zentinelle.configure",
        "zentinelle.policy_view",
        "zentinelle.policy_edit",
        "zentinelle.usage_view",
        "zentinelle.audit_view",
        "zentinelle.audit_export",
        "zentinelle.status_view",
        "zentinelle.conformance_view",
    }
)


def _catalogue() -> dict[str, tuple[str, frozenset[str]]]:
    return {slug: (level, frozenset(p.value for p in perms)) for slug, level, _n, _d, perms in SYSTEM_ROLES}


def _pinned(slug: str) -> tuple[str, frozenset[str]]:
    if slug in FULL_ENUM:
        return "ORG", FULL_ENUM[slug]
    level, slugs = PINNED[slug]
    return level, frozenset(slugs.split())


# ---------------------------------------------------------------------------
# Pinned
# ---------------------------------------------------------------------------


def test_every_stock_role_is_pinned():
    catalogue = _catalogue()
    assert set(catalogue) == set(PINNED) | set(FULL_ENUM)
    assert len(SYSTEM_ROLES) == len(catalogue), "a role slug appears twice"
    for slug, held in catalogue.items():
        assert held == _pinned(slug), f"{slug} changed: update its pin and add a resync migration (copy 0034)"


def test_zentinelle_defaults_follow_the_1888_table():
    zentinelle = {
        slug: {p for p in perms if p.startswith("zentinelle.")}
        for slug, (_level, perms) in _catalogue().items()
    }
    every = {p for p in EVERY_PERMISSION if p.startswith("zentinelle.")}
    hands_on = {"zentinelle.usage_view", "zentinelle.policy_view"}
    expected = {
        # "Org Admin: all."
        "org_owner": every,
        "org_admin": every,
        # "Auditor: status, policy and audit view, and audit export."
        "org_auditor": {
            "zentinelle.status_view",
            "zentinelle.policy_view",
            "zentinelle.audit_view",
            "zentinelle.audit_export",
        },
        # "Viewer: status." Status is org-scoped, so only the org viewer.
        "org_viewer": {"zentinelle.status_view"},
        # "Developer: usage and policy view on their projects", and every
        # hands-on tier at team and project level with them.
        "team_owner": hands_on,
        "team_admin": hands_on,
        "team_developer": hands_on,
        "team_operator": hands_on,
        "project_admin": hands_on,
        "project_developer": hands_on,
        "project_operator": hands_on,
    }
    assert zentinelle == {slug: expected.get(slug, set()) for slug in zentinelle}


def test_the_org_viewer_is_the_auditor_without_the_audit_grants():
    catalogue = _catalogue()
    viewer = catalogue["org_viewer"][1]
    auditor = catalogue["org_auditor"][1]
    assert viewer <= auditor
    assert auditor - viewer == {
        "audit_log.read",
        "audit_log.export",
        "app.log_export",
        "zentinelle.policy_view",
        "zentinelle.audit_view",
        "zentinelle.audit_export",
    }


# ---------------------------------------------------------------------------
# Through the real resolver
# ---------------------------------------------------------------------------


class _World:
    """One org with two teams, each holding a project, an app and a running agent task."""

    def __init__(self):
        self.org = Organization.objects.create(name="Stock", slug="stock-1864")
        self.ops = Team.objects.create(organization=self.org, name="Ops", slug="ops-1864")
        self.other = Team.objects.create(organization=self.org, name="Other", slug="other-1864")
        self.ops_project = Project.objects.create(
            organization=self.org, team=self.ops, name="Ops project", slug="ops-project-1864"
        )
        self.other_project = Project.objects.create(
            organization=self.org, team=self.other, name="Other project", slug="other-project-1864"
        )
        self.ops_app = RegisteredApp.objects.create(
            organization=self.org,
            team=self.ops,
            project=self.ops_project,
            name="Ops app",
            slug="ops-app-1864",
            provisioning_status="ready",
        )
        self.other_app = RegisteredApp.objects.create(
            organization=self.org,
            team=self.other,
            project=self.other_project,
            name="Other app",
            slug="other-app-1864",
            provisioning_status="ready",
        )
        self.ops_task = AgentTask.objects.create(
            organization=self.org,
            team=self.ops,
            project=self.ops_project,
            status=AgentTask.Status.RUNNING,
            vnc_enabled=True,
        )
        self.other_task = AgentTask.objects.create(
            organization=self.org,
            team=self.other,
            project=self.other_project,
            status=AgentTask.Status.RUNNING,
            vnc_enabled=True,
        )


@pytest.fixture
def world(db):
    return _World()


def _stock_roles() -> dict[str, Role]:
    """The catalogue's roles, as ``0034`` writes them.

    Running the migration here, rather than trusting the rows the test
    database was built with, keeps these tests honest under ``--reuse-db``:
    transactional tests elsewhere flush migration-seeded rows, and a reused
    database can hold system roles another version of the catalogue wrote,
    which the upsert never removes. Only the slugs ``SYSTEM_ROLES`` defines
    count.
    """
    RESYNC.upsert_system_roles(django_apps, None)
    return {
        slug: Role.objects.get(slug=slug, is_system=True, organization=None) for slug, *_rest in SYSTEM_ROLES
    }


def _holder(slug: str, kind: str, scope_id: int) -> User:
    user = User.objects.create(username=f"{slug}-1864", email=f"{slug}-1864@stock.test")
    RoleBinding.objects.create(user=user, role=_stock_roles()[slug], scope_kind=kind, scope_id=scope_id)
    return user


def _holds(user, world: _World, permission: Permission, kind: ScopeKind, ident: int) -> bool:
    tenant = TenantContext(organization_id=world.org.id, actor_user_id=user.id)
    granted, _reason = resolve(tenant, permission, PermissionScope(kind=kind, id=ident))
    return granted


RUN_ATTACH_CANCEL = (
    Permission.APP_DEPLOY,
    Permission.APP_ROLLBACK,
    Permission.AGENT_DISPATCH,
    Permission.WORKFLOW_TRIGGER,
    Permission.AGENT_BOX_ATTACH,
    Permission.AGENT_TASK_WATCH,
)
CONFIGURE = (
    Permission.APP_CREATE,
    Permission.APP_UPDATE,
    Permission.APP_DELETE,
    Permission.SECRET_READ,
    Permission.SECRET_WRITE,
    Permission.AGENT_CREATE,
    Permission.AGENT_UPDATE,
    Permission.AGENT_ENV_SPEC_UPDATE,
    Permission.WORKFLOW_CREATE,
    Permission.WORKFLOW_UPDATE,
    Permission.AGENT_TASK_SEND_INPUT,
    Permission.TEAM_MANAGE_MEMBERS,
    Permission.ZENTINELLE_CONFIGURE,
    Permission.ZENTINELLE_POLICY_EDIT,
)


@pytest.mark.parametrize(
    ("slug", "kind", "scope"),
    [("team_operator", "TEAM", "ops"), ("project_operator", "PROJECT", "ops_project")],
)
def test_operators_run_attach_and_cancel_in_their_scope_and_configure_nothing(world, slug, kind, scope):
    operator = _holder(slug, kind, getattr(world, scope).pk)

    for permission in RUN_ATTACH_CANCEL:
        assert _holds(operator, world, permission, ScopeKind.APP, world.ops_app.pk), permission
        assert not _holds(operator, world, permission, ScopeKind.APP, world.other_app.pk), permission
    for permission in CONFIGURE:
        assert not _holds(operator, world, permission, ScopeKind.APP, world.ops_app.pk), permission


@pytest.mark.parametrize(
    ("slug", "kind", "scope"),
    [("team_operator", "TEAM", "ops"), ("project_operator", "PROJECT", "ops_project")],
)
def test_operators_cancel_and_watch_their_own_agent_tasks_only(world, slug, kind, scope):
    """The real gates: ``cancelTask`` checks ``agent.dispatch`` at the task's scope, and the
    VNC relay authorizes ``agent_task.watch`` through ``watchable_task_ids``."""
    operator = _holder(slug, kind, getattr(world, scope).pk)
    cancel_scope = agent_task_scope("id", Permission.AGENT_DISPATCH)

    with tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=operator.id)):
        check_permission(Permission.AGENT_DISPATCH, scope=cancel_scope({"id": str(world.ops_task.guid)}))
        with pytest.raises(PermissionDenied):
            check_permission(
                Permission.AGENT_DISPATCH, scope=cancel_scope({"id": str(world.other_task.guid)})
            )
        assert watchable_task_ids([world.ops_task, world.other_task]) == {world.ops_task.pk}


def test_a_project_operator_does_not_reach_up_to_its_team(world):
    operator = _holder("project_operator", "PROJECT", world.ops_project.pk)

    assert not _holds(operator, world, Permission.TEAM_READ, ScopeKind.TEAM, world.ops.pk)


def test_the_org_viewer_reads_the_whole_org_but_not_its_audit_log(world):
    viewer = _holder("org_viewer", "ORG", world.org.pk)

    for permission in (Permission.APP_READ, Permission.APP_READ_LOGS, Permission.AGENT_READ):
        assert _holds(viewer, world, permission, ScopeKind.APP, world.other_app.pk), permission
    assert _holds(viewer, world, Permission.TEAM_READ, ScopeKind.TEAM, world.other.pk)
    assert _holds(viewer, world, Permission.ZENTINELLE_STATUS_VIEW, ScopeKind.ORG, world.org.pk)
    for permission in (
        Permission.AUDIT_LOG_READ,
        Permission.AUDIT_LOG_EXPORT,
        Permission.ZENTINELLE_AUDIT_VIEW,
        Permission.ORG_UPDATE,
    ):
        assert not _holds(viewer, world, permission, ScopeKind.ORG, world.org.pk), permission
    for permission in (Permission.APP_LOG_EXPORT, Permission.APP_DEPLOY, Permission.SECRET_READ):
        assert not _holds(viewer, world, permission, ScopeKind.APP, world.other_app.pk), permission
    assert not _holds(viewer, world, Permission.FORM_SUBMIT, ScopeKind.TEAM, world.other.pk)


# ---------------------------------------------------------------------------
# On every install
# ---------------------------------------------------------------------------


def test_0034_brings_an_install_with_the_old_catalogue_up_to_date(world):
    roles = _stock_roles()
    # The install as it stood before #1864: none of the new roles, and every
    # existing role without the new Zentinelle permissions.
    Role.all_objects.filter(is_system=True, organization=None, slug__in=NEW_ROLES).delete()
    for slug, role in roles.items():
        if slug not in NEW_ROLES:
            Role.all_objects.filter(pk=role.pk).update(permissions=sorted(_pinned(slug)[1] - NEW_PERMISSIONS))
    # An org that already named a custom role after one of the new ones, and
    # a grant made on the old catalogue.
    custom = Role.objects.create(
        organization=world.org,
        slug="team_operator",
        name="Our operators",
        scope_level="TEAM",
        permissions=["app.read"],
        is_system=False,
    )
    developer = User.objects.create(username="dev-1864", email="dev-1864@stock.test")
    RoleBinding.objects.create(
        user=developer, role=roles["team_developer"], scope_kind="TEAM", scope_id=world.ops.pk
    )
    assert not _holds(developer, world, Permission.ZENTINELLE_USAGE_VIEW, ScopeKind.TEAM, world.ops.pk)

    RESYNC.upsert_system_roles(django_apps, None)
    RESYNC.upsert_system_roles(django_apps, None)

    stored = Role.objects.filter(is_system=True, organization=None)
    assert stored.count() == len(SYSTEM_ROLES)
    assert {r.slug: (r.scope_level, r.name, r.description, frozenset(r.permissions)) for r in stored} == {
        slug: (level, name, description, frozenset(p.value for p in perms))
        for slug, level, name, description, perms in SYSTEM_ROLES
    }
    # Updated in place, so the old grant now carries the new defaults.
    assert stored.get(slug="team_developer").pk == roles["team_developer"].pk
    assert _holds(developer, world, Permission.ZENTINELLE_USAGE_VIEW, ScopeKind.TEAM, world.ops.pk)
    custom.refresh_from_db()
    assert (custom.organization_id, custom.is_system, custom.name, custom.permissions) == (
        world.org.id,
        False,
        "Our operators",
        ["app.read"],
    )


def test_0034_reverses_as_a_no_op():
    """Deleting the new roles would be refused once anyone holds one (``RoleBinding.role`` is PROTECT)."""
    (operation,) = RESYNC.Migration.operations
    assert operation.reverse_code is migrations.RunPython.noop


# ---------------------------------------------------------------------------
# Read-only, clonable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", sorted(NEW_ROLES))
def test_the_new_stock_roles_refuse_edits_and_clone_into_custom_roles(world, permission_resolver, slug):
    role = _stock_roles()[slug]
    user = User.objects.create(username=f"admin-{slug}-1864", email=f"admin-{slug}-1864@stock.test")
    info = SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)

    with tenant_context(TenantContext(organization_id=world.org.id)):
        edited = IdentityMutation().update_role(info, input=UpdateRoleInput(id=role.guid, permissions=[]))
        deleted = IdentityMutation().soft_delete_role(info, input=DeleteRoleInput(id=role.guid))
        cloned = IdentityMutation().create_role(
            info,
            input=CreateRoleInput(
                slug=f"my-{slug}",
                name=f"My {role.name}",
                scope_level=role.scope_level,
                permissions=list(role.permissions),
            ),
        )

    assert not edited.ok and edited.errors[0].code == "PRECONDITION"
    assert not deleted.ok and deleted.errors[0].code == "PRECONDITION"
    role.refresh_from_db()
    assert role.deleted_at is None and frozenset(role.permissions) == _pinned(slug)[1]
    assert cloned.ok, cloned.errors
    clone = Role.objects.get(organization=world.org, slug=f"my-{slug}")
    assert clone.is_system is False and frozenset(clone.permissions) == _pinned(slug)[1]
