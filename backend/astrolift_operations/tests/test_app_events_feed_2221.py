"""A read-only bearer reads one app's event summaries through app.read (#2221).

The org event stream stays behind ``audit_log.read``. ``astroliftAppEventsPage``
admits ``app.read`` at the named app, returns only events linked to that live
app and withholds payloads.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest

from astrolift_identity.api_tokens import DEFAULT_SCOPES, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization, Team
from astrolift_operations.models import Event
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry.models import RegisteredApp
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_index(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *args: None))


def _event(world, app, event_type, **extra):
    return Event.objects.create(
        organization=world.org,
        registered_app=app,
        team=app.team if app else None,
        project=app.project if app else None,
        event_type=event_type,
        resource_kind="app",
        resource_id=str(app.guid) if app else "",
        payload={"secret_name": "DATABASE_URL"},
        **extra,
    )


@pytest.fixture
def world():
    w = ScopeWorld("events-2221")
    w.user = make_user("events-2221")
    Member.objects.get_or_create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    w.own = _event(w, w.medops_app, "app.deployed")
    w.own_error = _event(w, w.medops_app, "deployment.failed", severity="error")
    w.sibling = _event(w, w.platform_app, "app.deployed")
    w.org_level = _event(w, None, "org.member_added")
    return w


def _grant(w, kind, *permissions):
    scope_id = {"ORG": w.org.pk, "TEAM": w.medops.pk, "PROJECT": w.medops_project.pk, "APP": w.medops_app.pk}[
        kind
    ]
    bind_role(w.user, permissions=permissions, kind=kind, scope_id=scope_id, slug=f"grant-{uuid.uuid4().hex}")


@contextmanager
def _tenant(w):
    with tenant_context(TenantContext(organization_id=w.org.pk, actor_user_id=w.user.pk)):
        yield


@contextmanager
def _token(w, scopes):
    token = ApiToken.objects.create(
        user=w.user, organization=w.org, name="events2221", token_hash=uuid.uuid4().hex, scopes=list(scopes)
    )
    handle = set_current_api_token(token)
    try:
        yield
    finally:
        reset_current_api_token(handle)


def _feed(w, slug=None, **kwargs):
    return OperationsQuery().astrolift_app_events_page(
        make_info(w.user), app_slug=slug or w.medops_app.slug, **kwargs
    )


@pytest.mark.parametrize("kind", ["ORG", "TEAM", "PROJECT", "APP"])
def test_mobile_default_bearer_reads_only_the_named_apps_event_summaries(world, kind):
    _grant(world, kind, Permission.APP_READ)
    with _tenant(world), _token(world, DEFAULT_SCOPES):
        page = _feed(world)
        with pytest.raises(PermissionDenied):
            OperationsQuery().astrolift_events_page(make_info(world.user))
    assert {str(item.id) for item in page.items} == {str(world.own.guid), str(world.own_error.guid)}
    assert all(item.payload == {} for item in page.items)
    assert page.reason.name == "OK"


def test_severity_and_type_filters_and_cursor_walk(world):
    _grant(world, "APP", Permission.APP_READ)
    with _tenant(world):
        errors = _feed(world, severity="error")
        first = _feed(world, limit=1)
        second = _feed(world, limit=1, after=first.next_cursor)
    assert [str(i.id) for i in errors.items] == [str(world.own_error.guid)]
    assert len(first.items) == len(second.items) == 1
    assert {str(first.items[0].id), str(second.items[0].id)} == {
        str(world.own.guid),
        str(world.own_error.guid),
    }


def test_sibling_app_and_missing_grant_are_refused(world):
    _grant(world, "APP", Permission.APP_READ)
    with _tenant(world), pytest.raises(PermissionDenied):
        _feed(world, slug=world.platform_app.slug)


def test_without_app_read_the_feed_is_refused(world):
    _grant(world, "ORG", Permission.ORG_READ)
    with _tenant(world), pytest.raises(PermissionDenied):
        _feed(world)


def test_bearer_without_read_apps_is_refused_even_for_an_org_owner(world):
    _grant(world, "ORG", *Permission)
    with _tenant(world), _token(world, ["read:clusters"]), pytest.raises(PermissionDenied):
        _feed(world)


def test_deleted_same_slug_predecessor_events_stay_hidden(world):
    _grant(world, "ORG", Permission.APP_READ)
    old = world.medops_app
    slug = old.slug
    RegisteredApp.objects.filter(pk=old.pk).update(slug=f"{slug}-retired", deleted_at=old.created_at)
    RegisteredApp.objects.create(
        organization=world.org,
        team=world.medops,
        project=world.medops_project,
        name="QsOps again",
        slug=slug,
        k8s_namespace=f"acme-{slug}-2",
        provisioning_status="ready",
    )
    with _tenant(world):
        page = _feed(world, slug=slug)
    assert page.items == []


def test_other_org_same_slug_events_never_appear(world):
    other = Organization.objects.create(name="Other", slug="other-events-2221")
    other_team = Team.objects.create(organization=other, name="Other", slug="other-team-2221")
    foreign = RegisteredApp.objects.create(
        organization=other,
        team=other_team,
        name="Foreign",
        slug=world.medops_app.slug,
        k8s_namespace="other-ns-2221",
    )
    Event.objects.create(organization=other, registered_app=foreign, event_type="app.deployed")
    _grant(world, "ORG", Permission.APP_READ)
    with _tenant(world):
        page = _feed(world)
    assert {str(item.id) for item in page.items} == {str(world.own.guid), str(world.own_error.guid)}
