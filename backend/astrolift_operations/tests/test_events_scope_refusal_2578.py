"""A token refused the event feed's permission is told how to get it (#2578).

``astro app events`` reads ``astroliftEventsPage``, gated on ``audit_log.read``.
No token scope but ``admin`` carries that permission, so the CLI's default
sign-in is refused it whatever the account's roles hold, and the refusal named
the permission and nothing else. The default sign-in keeps its scopes; the
refusal now says how to get the permission.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http, http_token
from astrolift_identity.api_tokens import (
    ALLOWED_SCOPES,
    CLI_DEVICE_SCOPES,
    DEFAULT_SCOPES,
    SCOPE_ADMIN,
    permissions_for_scopes,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import Member
from core.permissions import Permission, PermissionDenied, check_permission, check_permission_any_scope
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_user

REFUSAL = "api token scope does not allow this permission"
EVENTS = "query($slug: String) { astroliftEventsPage(appSlug: $slug, limit: 5) { reason } }"


@pytest.mark.django_db(transaction=True)
def test_the_default_cli_sign_in_is_told_how_to_read_app_events(client):
    world = ScopeWorld("ev-" + uuid.uuid4().hex[:8])
    auditor = make_user("ev" + uuid.uuid4().hex[:6])
    Member.objects.create(user=auditor, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        auditor,
        permissions=[Permission.AUDIT_LOG_READ],
        kind="ORG",
        scope_id=world.org.pk,
        slug=f"auditor-{world.org.slug}",
    )
    _cli, cli_headers = http_token(world, scopes=CLI_DEVICE_SCOPES, actor=auditor)
    _admin, admin_headers = http_token(world, scopes=(SCOPE_ADMIN,), actor=auditor)
    variables = {"slug": world.medops_app.slug}

    [error] = graphql_http(client, cli_headers, EVENTS, variables)["errors"]

    assert error["extensions"]["code"] == "PERMISSION_DENIED"
    assert (error["extensions"]["reason"], error["extensions"]["permission"]) == (REFUSAL, "audit_log.read")
    assert error["message"].startswith(f"{REFUSAL}: audit_log.read. Only the admin scope carries it: ")
    assert "astro auth login --scope admin" in error["message"]
    reply = graphql_http(client, admin_headers, EVENTS, variables)
    assert "errors" not in reply, reply
    assert reply["data"]["astroliftEventsPage"]["reason"] == "NO_DATA_YET"


def test_only_the_admin_scope_carries_audit_log_read():
    """What the refusal says, and the default sign-in left as it is."""
    for scope in sorted(ALLOWED_SCOPES - {SCOPE_ADMIN}):
        assert "audit_log.read" not in permissions_for_scopes([scope]), scope
    assert "audit_log.read" not in permissions_for_scopes(CLI_DEVICE_SCOPES)


@pytest.mark.parametrize("gate", [check_permission, check_permission_any_scope])
def test_only_an_audit_log_refusal_carries_the_advice(gate):
    """Both token gates add it. Other refusals keep their exact message, which
    clients match on: the mobile app's workflow cancel matches this one."""
    context = set_current_api_token(SimpleNamespace(scopes=list(DEFAULT_SCOPES)))
    try:
        with pytest.raises(PermissionDenied) as audit:
            gate(Permission.AUDIT_LOG_READ)
        with pytest.raises(PermissionDenied) as trigger:
            gate(Permission.WORKFLOW_TRIGGER)
    finally:
        reset_current_api_token(context)

    assert audit.value.reason == REFUSAL
    assert "astro auth login --scope admin" in str(audit.value)
    assert str(trigger.value) == f"{REFUSAL}: workflow.trigger"
