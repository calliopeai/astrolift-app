"""Mutation audit rows are filed under the tenant org only for its members (#1955).

Both writers (``MutationAuditExtension`` -> ``MutationAuditLog`` and
``@mutation_audit`` -> ``AuditEvent``) used to stamp whatever org the
request named. ``X-Astrolift-Organization`` is resolved by guid with no
membership check, so an outsider's refused mutation landed in that org's own
audit views. Now the actor must be an active member of the org, or an active
superuser; otherwise the row carries no org and no tenant view shows it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

from astrolift_identity.models import Member, Organization
from astrolift_operations.audit_writer import write_audit_entry
from astrolift_operations.models import AuditEvent
from core.mutations import register_audit_writer
from core.permissions import Permission
from core.schema.audit import MutationAuditLog
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

GQL = f"/{settings.BASE_URL}gql/config/"
SET_MODULE = (
    'mutation { setOrganizationModule(input: {key: "chat_studio_integration", enabled: true}) { ok } }'
)


@pytest.fixture(autouse=True)
def _persistent_audit_writer():
    """Persist AuditEvent rows, restoring whichever writer was installed."""
    from core import mutations as _mod

    previous = _mod._audit_writer
    register_audit_writer(write_audit_entry)
    yield
    register_audit_writer(previous)


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Creating a User indexes its Profile in OpenSearch; not under test."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


def _org(slug: str) -> Organization:
    return Organization.objects.create(name=slug, slug=slug)


def _user(username: str, **extra):
    return get_user_model().objects.create(username=username, **extra)


def _member(user, org, **extra) -> Member:
    return Member.objects.create(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.id, **extra)


def _set_module(org, user):
    from config.schema import schema

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = schema.execute_sync(SET_MODULE, context_value=SimpleNamespace(user=user, request=None))
    assert result.errors is None, result.errors


def _filed_under(operation: str) -> tuple:
    return (
        MutationAuditLog.objects.get(operation=operation).organization_id,
        AuditEvent.objects.get(action=operation).organization_id,
    )


def test_a_members_mutation_is_filed_under_the_org(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org = _org("audit-member-1955")
    user = _user("member-1955")
    _member(user, org)

    _set_module(org, user)

    assert _filed_under("org.module.set") == (org.id, org.id)


def test_an_outsider_naming_the_org_by_header_is_filed_under_no_org(permission_resolver):
    """The forgery: a session names another org in the header. The middleware
    makes it the tenant without a membership check, RBAC refuses the
    mutation, and both writers still ran. They must not file it under the
    org the outsider named."""
    home = _org("audit-home-1955")
    target = _org("audit-target-1955")
    outsider = _user("outsider-1955")
    _member(outsider, home)
    client = Client()
    client.force_login(outsider)

    response = client.post(
        GQL,
        data=json.dumps({"query": SET_MODULE}),
        content_type="application/json",
        HTTP_X_PLATFORM="web",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(target.guid),
    )

    assert response.status_code == 200, response.content
    assert response.json()["data"]["setOrganizationModule"]["ok"] is False
    assert _filed_under("org.module.set") == (None, None)


def test_a_superusers_mutation_is_filed_under_the_org_without_membership(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org = _org("audit-super-1955")
    operator = _user("operator-1955", is_superuser=True)

    _set_module(org, operator)

    assert _filed_under("org.module.set") == (org.id, org.id)


def test_a_deactivated_membership_does_not_count(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org = _org("audit-gone-1955")
    user = _user("gone-1955")
    _member(user, org, is_active=False)

    _set_module(org, user)

    assert _filed_under("org.module.set") == (None, None)


def test_standing_is_judged_before_the_mutation_runs(permission_resolver):
    """Deleting the org takes it out of the live set the membership rule
    requires. The deletion is still the org's own record."""
    from config.schema import schema

    permission_resolver.grant(Permission.ORG_DELETE)
    org = _org("audit-delete-1955")
    user = _user("owner-1955")
    _member(user, org)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = schema.execute_sync(
            "mutation($input: SoftDeleteByGuidInput!) { softDeleteOrganization(input: $input) { ok } }",
            variable_values={"input": {"id": str(org.guid)}},
            context_value=SimpleNamespace(user=user, request=None),
        )

    assert result.errors is None, result.errors
    assert result.data["softDeleteOrganization"]["ok"] is True
    assert _filed_under("org.delete") == (org.id, org.id)


def test_row_written_without_a_tenant_has_no_org():
    """The token is the credential on this public mutation, so it runs
    without a tenant."""
    from config.schema import schema

    result = schema.execute_sync(
        'mutation { approveDeploymentByToken(input: {token: "not-a-token"}) { ok } }',
        context_value=SimpleNamespace(user=None, request=None),
    )

    assert result.errors is None, result.errors
    assert _filed_under("deployment.approve_by_token") == (None, None)
