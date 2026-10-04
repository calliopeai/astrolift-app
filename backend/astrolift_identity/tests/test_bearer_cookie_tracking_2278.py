"""Actual bearer middleware must not repurpose an unrelated browser carrier."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_identity.models import AstroliftSession, Member
from astrolift_services.tests.test_cluster_model_foundation_2213 import world as foundation_world
from astrolift_services.tests.test_model_connection_2270 import graphql_http, http_token
from core.tests.utils.scope_world import make_user

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch):
    return foundation_world.__wrapped__(monkeypatch)


@pytest.mark.parametrize("state", ["healthy", "revoked", "expired", "deleted", "same_user"])
def test_actual_bearer_query_does_not_touch_cookie_sidecar(world, client, state):
    client.force_login(world.user)
    browser_headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    graphql_http(client, browser_headers, "query{__typename}", {})
    row = AstroliftSession.objects.get(session_key=client.session.session_key)
    if state == "revoked":
        row.revoked_at = timezone.now()
    elif state == "expired":
        row.expires_at = timezone.now() - timedelta(seconds=1)
    elif state == "deleted":
        row.deleted_at = timezone.now()
    if state in ("revoked", "expired", "deleted"):
        row.save()
    before = AstroliftSession.all_objects.filter(pk=row.pk).values().get()
    voter = world.user if state == "same_user" else make_user("bearer-cookie-reviewer")
    if voter != world.user:
        Member.objects.create(user=voter, scope_kind="ORG", scope_id=world.org.pk)
    _, headers = http_token(world, actor=voter, scopes=("read:apps",))
    result = graphql_http(client, headers, "query{__typename}", {})
    assert not result.get("errors")
    assert AstroliftSession.all_objects.filter(pk=row.pk).values().get() == before
    assert AstroliftSession.all_objects.count() == 1


def test_actual_bearer_query_without_cookie_creates_no_browser_sidecar(world):
    _, headers = http_token(world, scopes=("read:apps",))
    result = graphql_http(Client(), headers, "query{__typename}", {})
    assert not result.get("errors") and not AstroliftSession.all_objects.exists()
