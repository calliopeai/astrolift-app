"""Easy-mode access presets map onto the stock roles (#2272)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http, http_token
from astrolift_identity.access_presets import PRESETS, TASKS, role_tasks, validate_presets
from astrolift_identity.models import Member
from astrolift_identity.system_roles import SYSTEM_ROLES
from core.permissions import Permission
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user


def test_every_preset_names_an_existing_stock_role_at_its_own_scope():
    validate_presets()
    stock = {slug for slug, *_ in SYSTEM_ROLES}
    assert {slug for preset in PRESETS for _scope, slug in preset.roles} <= stock


def test_tasks_follow_the_roles_actual_permissions():
    assert role_tasks("app_viewer") == {
        "browse_apps": True,
        "read_logs_metrics": True,
        "deploy_apps": False,
        "approve_deploys": False,
        "change_apps": False,
        "run_agents_workflows": False,
        "manage_agents_workflows": False,
        "manage_access": False,
        "manage_org_settings": False,
    }
    assert role_tasks("app_deployer")["deploy_apps"] is True
    assert role_tasks("app_deployer")["manage_access"] is False
    assert role_tasks("app_approver")["approve_deploys"] is True
    assert all(role_tasks("org_admin").values())


def test_no_preset_reaches_installation_super_admin():
    # Super-admin is is_superuser, not a permission; no stock role can carry it,
    # and org admin is the widest preset, still short of org delete and billing.
    [admin] = [p for p in PRESETS if p.key == "org_admin"]
    assert admin.roles == (("ORG", "org_admin"),)
    org_admin = next(perms for slug, _s, _n, _d, perms in SYSTEM_ROLES if slug == "org_admin")
    assert Permission.ORG_DELETE not in org_admin and Permission.BILLING_UPDATE not in org_admin
    assert {task.key for task in TASKS} >= {"browse_apps", "deploy_apps", "manage_access"}


@pytest.mark.django_db(transaction=True)
def test_the_query_lists_presets_with_granted_and_excluded_tasks(client):
    world = ScopeWorld("presets-" + uuid4().hex[:8])
    world.user = make_user(uuid4().hex[:8])
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        world.user, permissions=[Permission.ORG_READ], kind="ORG", scope_id=world.org.pk, slug=uuid4().hex
    )
    _token, headers = http_token(world, scopes=("admin",))

    reply = graphql_http(
        client,
        headers,
        "{astroliftAccessPresets{key label roles{scopeLevel roleSlug tasks{key granted}}}}",
        {},
    )

    assert not reply.get("errors"), reply.get("errors")
    presets = {p["key"]: p for p in reply["data"]["astroliftAccessPresets"]}
    assert list(presets) == [p.key for p in PRESETS]
    [deployer_app] = [r for r in presets["deployer"]["roles"] if r["scopeLevel"] == "APP"]
    granted = {t["key"] for t in deployer_app["tasks"] if t["granted"]}
    assert (
        deployer_app["roleSlug"] == "app_deployer"
        and "deploy_apps" in granted
        and "manage_access" not in granted
    )
