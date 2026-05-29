"""Tests for `astroliftMembers` discoverability + role-binding source labels (#417).

Covers:

* ``search`` arg filters by username / email / first_name / last_name
  (case-insensitive).
* ``last_active_at`` derives from the per-org ``AuditEvent`` stream
  and stays None for members with no recorded actions yet.
* ``astroliftRoleBindings`` returns a populated
  ``source_scope_label`` for each binding, resolved across all four
  scope kinds (ORG / TEAM / PROJECT / APP).
* Permission gate: ``org.manage_members`` is required.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_identity.models import (
    Member,
    Organization,
    Project,
    Role,
    RoleBinding,
    Team,
)
from astrolift_identity.schema.queries import IdentityQuery
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


def _backfill_audit_event(
    *,
    org,
    actor_id,
    occurred_at,
    action="app.read",
    decision="ALLOW",
):
    """Direct INSERT past ``auto_now_add`` + the append-only trigger.

    ``AuditEvent.objects.bulk_create`` still runs each field's
    ``pre_save`` on modern Django, so ``occurred_at`` gets overridden
    to ``timezone.now()`` — useless for "older than now" timestamps.
    Raw SQL inserts straight into the table, which the append-only
    trigger allows because it only refuses UPDATE / DELETE.
    """
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO astrolift_operations_auditevent
              (guid, organization_id, occurred_at, actor_kind, actor_id,
               actor_display, action, decision, target_kind, target_id,
               target_slug, target_parent_chain, request_id, request_ip,
               request_user_agent, request_session_age_seconds, data, reasoning)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                str(uuid.uuid4()),
                org.id,
                occurred_at,
                "user",
                str(actor_id),
                "",
                action,
                decision,
                "",
                "",
                "",
                "[]",
                "",
                "",
                "",
                None,
                "{}",
                "[]",
            ],
        )


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    """Same pattern as test_my_permissions.py — skip OpenSearch
    side-effects when User / Organization creates fire signal handlers."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, p: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, gid: None),
    )


@pytest.fixture
def actor():
    User = get_user_model()
    return User.objects.create(username="caller-417", email="caller-417@example.com")


@pytest.fixture
def info(actor):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=actor)))


def _add_member(org, *, username, email, first_name="", last_name=""):
    User = get_user_model()
    user = User.objects.create(username=username, email=email, first_name=first_name, last_name=last_name)
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    return user


# ---- search ----------------------------------------------------------


def test_search_matches_username_email_and_name(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="acme-417-search")
    alice = _add_member(
        org, username="alice", email="alice@acme.test", first_name="Alice", last_name="Anderson"
    )
    bob = _add_member(org, username="bob", email="robert@other.test")
    chris = _add_member(org, username="chris", email="chris@acme.test")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        # Username hit
        results = IdentityQuery().astrolift_members(info, search="ali")
        assert {int(r.user.id) for r in results} == {alice.pk}

        # Email hit (case-insensitive)
        results = IdentityQuery().astrolift_members(info, search="OTHER.TEST")
        assert {int(r.user.id) for r in results} == {bob.pk}

        # Last name hit
        results = IdentityQuery().astrolift_members(info, search="Anderson")
        assert {int(r.user.id) for r in results} == {alice.pk}

        # Empty search returns everyone
        results = IdentityQuery().astrolift_members(info, search="")
        assert {int(r.user.id) for r in results} == {alice.pk, bob.pk, chris.pk}

        # No-match search returns nothing
        results = IdentityQuery().astrolift_members(info, search="zzzzz-no-such-thing")
        assert results == []


# ---- last_active_at --------------------------------------------------


def test_last_active_at_derives_from_audit_events(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="acme-417-last-active")
    alice = _add_member(org, username="alice417la", email="alice@acme.test")
    bob = _add_member(org, username="bob417la", email="bob@acme.test")

    older = timezone.now() - timezone.timedelta(days=30)
    newer = timezone.now() - timezone.timedelta(hours=2)

    _backfill_audit_event(org=org, actor_id=alice.pk, occurred_at=older, action="app.deploy")
    _backfill_audit_event(org=org, actor_id=alice.pk, occurred_at=newer, action="app.read")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        results = IdentityQuery().astrolift_members(info)

    by_user = {int(r.user.id): r for r in results}
    # Alice's last_active_at == the newer event's timestamp
    assert by_user[alice.pk].last_active_at is not None
    assert abs((by_user[alice.pk].last_active_at - newer).total_seconds()) < 1
    # Bob has no audit events yet -> None
    assert by_user[bob.pk].last_active_at is None


def test_last_active_at_scoped_to_current_org(actor, info, permission_resolver):
    """Audit events from a different org must not leak into this org's last_active_at."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    home = Organization.objects.create(name="Home", slug="home-417-la")
    other = Organization.objects.create(name="Other", slug="other-417-la")
    user = _add_member(home, username="bridged", email="bridged@home.test")

    foreign_ts = timezone.now() - timezone.timedelta(days=1)
    _backfill_audit_event(org=other, actor_id=user.pk, occurred_at=foreign_ts)

    with tenant_context(TenantContext(organization_id=home.id, actor_user_id=actor.id)):
        results = IdentityQuery().astrolift_members(info)

    by_user = {int(r.user.id): r for r in results}
    assert by_user[user.pk].last_active_at is None


# ---- source_scope_label on bindings ---------------------------------


def test_role_binding_source_scope_label_org_team_project_app(actor, info, permission_resolver):
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="acme-417-sources")
    team = Team.objects.create(organization=org, slug="payments", name="Payments")
    project = Project.objects.create(organization=org, team=team, slug="web", name="Web project")
    app = RegisteredApp.objects.create(
        organization=org, team=team, project=project, slug="web-api", name="Web API"
    )

    user = _add_member(org, username="binder417", email="binder@acme.test")
    role_org = Role.objects.create(
        slug="r417-org", name="org-role", scope_level="ORG", permissions=["org.read"]
    )
    role_team = Role.objects.create(
        slug="r417-team", name="team-role", scope_level="TEAM", permissions=["team.read"]
    )
    role_project = Role.objects.create(
        slug="r417-project", name="project-role", scope_level="PROJECT", permissions=["project.read"]
    )
    role_app = Role.objects.create(
        slug="r417-app", name="app-role", scope_level="APP", permissions=["app.read"]
    )
    RoleBinding.objects.create(user=user, role=role_org, scope_kind="ORG", scope_id=org.id)
    RoleBinding.objects.create(user=user, role=role_team, scope_kind="TEAM", scope_id=team.id)
    RoleBinding.objects.create(user=user, role=role_project, scope_kind="PROJECT", scope_id=project.id)
    RoleBinding.objects.create(user=user, role=role_app, scope_kind="APP", scope_id=app.id)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        bindings = IdentityQuery().astrolift_role_bindings(info)

    by_scope = {b.scope_kind: b.source_scope_label for b in bindings}
    assert by_scope["ORG"] == "organization acme-417-sources"
    assert by_scope["TEAM"] == "team payments"
    assert by_scope["PROJECT"] == "project payments/web"
    assert by_scope["APP"] == "app web-api"


def test_role_binding_source_scope_label_falls_back_on_missing_scope(actor, info, permission_resolver):
    """If the referenced scope row vanished (soft-deleted, migrated away),
    the label still resolves to a non-empty fallback so the FE never
    sees an empty string."""
    permission_resolver.grant(Permission.ORG_MANAGE_MEMBERS)
    org = Organization.objects.create(name="Acme", slug="acme-417-missing")
    user = _add_member(org, username="orphan417", email="orphan@acme.test")
    role = Role.objects.create(
        slug="r417-orphan", name="orphan-role", scope_level="TEAM", permissions=["team.read"]
    )
    # Bind to a non-existent team id
    RoleBinding.objects.create(user=user, role=role, scope_kind="TEAM", scope_id=999_999)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        bindings = IdentityQuery().astrolift_role_bindings(info)

    assert any(b.source_scope_label == "team" for b in bindings)


# ---- permission gate -------------------------------------------------


def test_members_denied_without_permission(actor, info, permission_resolver):
    from core.permissions import PermissionDenied

    org = Organization.objects.create(name="Acme", slug="acme-417-deny")
    _add_member(org, username="someone417", email="someone@acme.test")

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id)):
        with pytest.raises(PermissionDenied):
            IdentityQuery().astrolift_members(info, search="some")
