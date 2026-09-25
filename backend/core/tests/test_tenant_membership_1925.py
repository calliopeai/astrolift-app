"""A session may act only in an org it is an active member of (#1925).

The selected-org header, the session's saved org and the websocket's pinned
org were honoured by guid or id with no membership check, so a browser
session kept access to an org after SCIM removed the person, and anyone could
select any org by guid.
"""

from __future__ import annotations

import uuid

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from astrolift_identity.models import Member, Organization, Project, Team
from core.middleware.tenant import TenantContextMiddleware
from core.schema.ws_auth import _resolve_tenant_for_user

pytestmark = pytest.mark.django_db
User = get_user_model()


def _org(tag: str) -> Organization:
    return Organization.objects.create(name=tag, slug=f"{tag}-{uuid.uuid4().hex[:6]}")


def _user(tag: str, **extra):
    return User.objects.create(username=f"{tag}-{uuid.uuid4().hex[:6]}", **extra)


def _member(org, user, *, active=True, deleted=False):
    row = Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.id,
        is_active=active,
        lifecycle=Member.Lifecycle.ACTIVE if active else Member.Lifecycle.DEACTIVATED,
    )
    if deleted:
        row.soft_delete()
    return user


def _tenant(user, *, org=None, session_org=None, team=None, project=None):
    meta = {}
    if org is not None:
        meta["HTTP_X_ASTROLIFT_ORGANIZATION"] = str(org.guid)
    if team is not None:
        meta["HTTP_X_ASTROLIFT_TEAM"] = str(team.pk)
    if project is not None:
        meta["HTTP_X_ASTROLIFT_PROJECT"] = str(project.pk)
    request = RequestFactory().get("/", **meta)
    request.user = user
    request.session = SessionStore()
    if session_org is not None:
        request.session["organization_id"] = session_org.pk
    TenantContextMiddleware(lambda r: None).process_request(request)
    return request.astrolift_tenant


def test_a_member_selects_their_org():
    org = _org("a")
    user = _member(org, _user("u"))
    assert _tenant(user, org=org).organization_id == org.pk


def test_a_non_member_cannot_select_an_org_by_guid():
    a, b = _org("a"), _org("b")
    user = _member(a, _user("u"))
    assert _tenant(user, org=b).organization_id is None


@pytest.mark.parametrize("state", ["deactivated", "removed", "account_off"])
def test_a_deprovisioned_person_loses_the_org_at_once(state):
    org = _org("a")
    user = _user("u")
    _member(org, user, active=state != "deactivated", deleted=state == "removed")
    if state == "account_off":
        user.is_active = False
        user.save(update_fields=["is_active"])
    assert _tenant(user, org=org).organization_id is None
    assert _tenant(user, session_org=org).organization_id is None


def test_the_platform_operator_may_select_any_org():
    org = _org("a")
    root = _user("root", is_superuser=True)
    assert _tenant(root, org=org).organization_id == org.pk


def test_a_team_or_project_from_another_org_is_dropped():
    a, b = _org("a"), _org("b")
    user = _member(a, _user("u"))
    foreign_team = Team.objects.create(organization=b, name="t", slug=f"t-{uuid.uuid4().hex[:6]}")
    foreign_project = Project.objects.create(
        organization=b, team=foreign_team, name="p", slug=f"p-{uuid.uuid4().hex[:6]}"
    )
    tenant = _tenant(user, org=a, team=foreign_team, project=foreign_project)
    assert tenant.organization_id == a.pk
    assert tenant.team_id is None
    assert tenant.project_id is None


def test_single_membership_inference_needs_exactly_one_live_org():
    a, b = _org("a"), _org("b")
    one = _member(a, _user("one"))
    two = _member(b, _member(a, _user("two")))
    assert _tenant(one).organization_id == a.pk
    assert _tenant(two).organization_id is None


def test_websocket_pinned_org_needs_membership_and_never_picks_the_first_of_many():
    a, b = _org("a"), _org("b")
    user = _member(a, _user("u"))
    pinned_foreign = async_to_sync(_resolve_tenant_for_user)(user, {"astrolift_active_org": str(b.guid)})
    pinned_own = async_to_sync(_resolve_tenant_for_user)(user, {"astrolift_active_org": str(a.guid)})
    multi = _member(b, _member(a, _user("multi")))
    inferred = async_to_sync(_resolve_tenant_for_user)(multi, {})

    assert pinned_foreign is None
    assert pinned_own.organization_id == a.pk
    assert inferred is None
