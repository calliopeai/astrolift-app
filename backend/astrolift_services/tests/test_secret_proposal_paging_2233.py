"""Real PostgreSQL queue pagination and metadata access contracts."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.core import signing
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from graphql import GraphQLError

from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import SecretChangeApproval, SecretChangeProposal
from astrolift_services.schema.queries import ServicesQuery
from astrolift_services.secret_proposal_pages import CURSOR_MAX_AGE, CURSOR_SALT
from astrolift_services.tests.test_secret_change_proposals import _ctx, _info, _make_user, _scaffold
from config.schema import schema
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind

pytestmark = pytest.mark.django_db


def proposals(app, count=1, **extra):
    rows = [
        SecretChangeProposal(
            registered_app=app,
            op="set",
            expires_at=timezone.now() + timedelta(days=1),
            payload={"value": "PRIVATE_PAYLOAD_MARKER"},
            payload_diff={"summary": "PRIVATE_DIFF_MARKER"},
            apply_error="PRIVATE_ERROR_MARKER",
            **extra,
        )
        for _ in range(count)
    ]
    return SecretChangeProposal.objects.bulk_create(rows)


def page(**kwargs):
    return ServicesQuery().astrolift_secret_change_proposals_page(_info(), **kwargs)


def test_more_than_200_tied_rows_are_bounded_complete_and_exact(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    rows = proposals(app, 237)
    SecretChangeProposal.objects.filter(pk__in=[r.pk for r in rows]).update(created_at=timezone.now())
    expected = [
        str(g)
        for g in SecretChangeProposal.objects.order_by("-created_at", "-guid").values_list("guid", flat=True)
    ]
    seen = []
    cursor = None
    with _ctx(org):
        while True:
            with CaptureQueriesContext(connection) as queries:
                result = page(limit=37, after=cursor, status="pending")
            assert len(queries) <= 6
            assert len(result.items) <= 37
            assert result.total_count == 237
            assert result.complete == (result.next_cursor is None)
            seen.extend(str(row.id) for row in result.items)
            if result.complete:
                break
            cursor = result.next_cursor
    assert seen == expected
    assert len(set(seen)) == 237


def test_filters_counts_and_empty_pages_use_only_visible_apps(permission_resolver):
    org, app, _, _ = _scaffold()
    other = RegisteredApp.objects.create(
        organization=org, team=app.team, project=app.project, name="Other", slug="other"
    )
    proposals(app, 3)
    proposals(app, 1, status="rejected")
    proposals(other, 5)
    permission_resolver.grant(Permission.APP_READ, scope=PermissionScope(ScopeKind.APP, app.pk))
    with _ctx(org):
        result = page(status="pending")
        assert result.total_count == 3
        assert {r.registered_app_slug for r in result.items} == {app.slug}
        assert page(app_slug=app.slug, status="rejected").total_count == 1
        empty = page(app_slug=app.slug, status="withdrawn")
        assert empty.items == [] and empty.complete and empty.total_count == 0 and empty.next_cursor is None
        with pytest.raises(PermissionDenied):
            page(app_slug=other.slug)


def test_entry_permission_denies_list_and_detail(permission_resolver):
    org, app, _, _ = _scaffold()
    row = proposals(app)[0]
    with _ctx(org), pytest.raises(PermissionDenied):
        page()
    with _ctx(org), pytest.raises(PermissionDenied):
        ServicesQuery().astrolift_secret_change_proposal_metadata(_info(), id=GUID(str(row.guid)))


def test_foreign_cursor_and_ids_never_expose_metadata_or_counts(permission_resolver):
    org, app, _, _ = _scaffold()
    rows = proposals(app, 2)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        cursor = page(limit=1).next_cursor
    foreign = Organization.objects.create(name="Foreign", slug="foreign")
    with _ctx(foreign):
        with pytest.raises(GraphQLError) as error:
            page(after=cursor)
        assert error.value.extensions["code"] == "INVALID_CURSOR"
        assert (
            ServicesQuery().astrolift_secret_change_proposal_metadata(_info(), id=GUID(str(rows[0].guid)))
            is None
        )
        assert page().total_count == 0


@pytest.mark.parametrize("change", ["status", "insert", "delete", "vote"])
def test_concurrent_queue_changes_explicitly_expire_the_walk(permission_resolver, change):
    org, app, _, _ = _scaffold()
    rows = proposals(app, 3)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        cursor = page(limit=1, status="pending").next_cursor
    row = rows[-1]
    if change == "insert":
        proposals(app)
    elif change == "vote":
        SecretChangeApproval.objects.create(proposal=row, approver=_make_user("voter"), decision="approved")
    else:
        if change == "delete":
            row.deleted_at = timezone.now()
        else:
            row.status = "approved"
        row.save()
    with _ctx(org):
        with pytest.raises(GraphQLError) as error:
            page(limit=1, status="pending", after=cursor)
        assert error.value.extensions["code"] == "STALE_CURSOR"
        fresh = page(status="pending")
        assert fresh.total_count == {"status": 2, "insert": 4, "delete": 2, "vote": 3}[change]


@pytest.mark.parametrize("cursor", ["", "junk", "x" * 2049])
def test_malformed_cursors_never_silently_restart(permission_resolver, cursor):
    org, app, _, _ = _scaffold()
    proposals(app)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org), pytest.raises(GraphQLError) as error:
        page(after=cursor)
    assert error.value.extensions["code"] == "INVALID_CURSOR"


def test_status_aba_below_the_latest_timestamp_expires_the_walk(permission_resolver):
    org, app, _, _ = _scaffold()
    rows = proposals(app, 3)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        cursor = page(limit=1, status="pending").next_cursor
    old = rows[0]
    old.status = "approved"
    old.save(update_fields=["status", "version"])
    old.status = "pending"
    old.save(update_fields=["status", "version"])
    with _ctx(org), pytest.raises(GraphQLError) as error:
        page(limit=1, status="pending", after=cursor)
    assert error.value.extensions["code"] == "STALE_CURSOR"


def test_cursor_is_bound_to_filters_actor_and_expiry(permission_resolver, monkeypatch):
    org, app, _, _ = _scaffold()
    proposals(app, 2)
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        cursor = page(limit=1, status="pending").next_cursor
    for options in [{"status": "rejected"}, {"status": "pending", "app_slug": app.slug}]:
        with _ctx(org), pytest.raises(GraphQLError) as error:
            page(after=cursor, **options)
        assert error.value.extensions["code"] == "INVALID_CURSOR"
    with _ctx(org, actor=_make_user("another")), pytest.raises(GraphQLError) as error:
        page(after=cursor, status="pending")
    assert error.value.extensions["code"] == "INVALID_CURSOR"
    payload = signing.loads(cursor, salt=CURSOR_SALT)
    with monkeypatch.context() as patch:
        patch.setattr(signing.time, "time", lambda: timezone.now().timestamp() - CURSOR_MAX_AGE - 2)
        expired = signing.dumps(payload, salt=CURSOR_SALT)
    with _ctx(org), pytest.raises(GraphQLError) as error:
        page(after=expired, status="pending")
    assert error.value.extensions["code"] == "STALE_CURSOR"


@pytest.mark.parametrize("options", [{"limit": 0}, {"limit": 201}, {"status": "unknown"}])
def test_invalid_filters_and_limits_refuse(permission_resolver, options):
    org, _, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org), pytest.raises(GraphQLError) as error:
        page(**options)
    assert error.value.extensions["code"] == "INVALID_ARGUMENT"


def test_metadata_page_and_exact_detail_do_not_load_or_select_sensitive_fields(permission_resolver):
    org, app, _, _ = _scaffold()
    row = proposals(app)[0]
    permission_resolver.grant(Permission.APP_READ)
    query = """query($id:GUID!){astroliftSecretChangeProposalsPage(status:"pending",limit:25){
        items{id registeredAppSlug environmentName op status proposerDisplayName approvalsCount requiredApproverCount createdAt}
        nextCursor totalCount complete}
        astroliftSecretChangeProposalMetadata(id:$id){id status approvalsCount}}"""
    with _ctx(org), CaptureQueriesContext(connection) as queries:
        result = schema.execute_sync(
            query, variable_values={"id": str(row.guid)}, context_value=_info().context
        )
    assert not result.errors, result.errors
    assert result.data["astroliftSecretChangeProposalMetadata"]["id"] == str(row.guid)
    assert "PRIVATE_" not in json.dumps(result.data)
    for statement in queries.captured_queries:
        select = statement["sql"].partition(" FROM ")[0]
        assert not any(
            '"' + field + '"' in select for field in ("payload", "payload_diff", "apply_error", "reason")
        )
    for field in ("payload", "payloadDiff", "applyError", "approvals"):
        bad = schema.execute_sync(
            '{astroliftSecretChangeProposalMetadata(id:"' + str(row.guid) + '"){' + field + "}}"
        )
        assert bad.errors


def test_team_scope_and_bearer_ceiling_apply_to_counts_and_exact_metadata(permission_resolver):
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken, Team

    org, app, _, _ = _scaffold()
    other_team = Team.objects.create(organization=org, name="Other", slug="other")
    other_app = RegisteredApp.objects.create(organization=org, team=other_team, name="Other", slug="other")
    own = proposals(app)[0]
    foreign = proposals(other_app)[0]
    permission_resolver.grant(Permission.APP_READ, scope=PermissionScope(ScopeKind.TEAM, app.team_id))
    with _ctx(org):
        result = page()
        assert result.total_count == 1 and str(result.items[0].id) == str(own.guid)
        with pytest.raises(PermissionDenied):
            ServicesQuery().astrolift_secret_change_proposal_metadata(_info(), id=GUID(str(foreign.guid)))
        user = _make_user("token-reader")
        token = ApiToken.objects.create(
            user=user,
            organization=org,
            token_hash=uuid.uuid4().hex,
            name="proposal-reader",
            scopes=["read:clusters"],
        )
        marker = set_current_api_token(token)
        try:
            with pytest.raises(PermissionDenied):
                page()
            with pytest.raises(PermissionDenied):
                ServicesQuery().astrolift_secret_change_proposal_metadata(_info(), id=GUID(str(own.guid)))
        finally:
            reset_current_api_token(marker)


def test_org_wide_actor_still_respects_team_credential_and_foreign_org(permission_resolver):
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import ApiToken, Team

    org, app, _, _ = _scaffold()
    other_team = Team.objects.create(organization=org, name="Other", slug="other")
    other_app = RegisteredApp.objects.create(organization=org, team=other_team, name="Other", slug="other")
    own = proposals(app)[0]
    foreign = proposals(other_app)[0]
    permission_resolver.grant(Permission.APP_READ)
    token = ApiToken.objects.create(
        user=_make_user("team-token"),
        organization=org,
        team=app.team,
        token_hash=uuid.uuid4().hex,
        name="team",
        scopes=["read:apps"],
    )
    marker = set_current_api_token(token)
    try:
        with _ctx(org):
            result = page()
            assert result.total_count == 1 and str(result.items[0].id) == str(own.guid)
            with pytest.raises(PermissionDenied):
                ServicesQuery().astrolift_secret_change_proposal_metadata(_info(), id=GUID(str(foreign.guid)))
        other_org = Organization.objects.create(name="Another", slug="another")
        with _ctx(other_org), pytest.raises(PermissionDenied):
            page()
    finally:
        reset_current_api_token(marker)
