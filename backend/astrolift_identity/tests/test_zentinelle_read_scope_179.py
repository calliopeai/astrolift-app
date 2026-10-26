from types import SimpleNamespace

import pytest

from astrolift_identity import device_flow
from astrolift_identity.api_tokens import (
    ALLOWED_SCOPES,
    CLI_DEVICE_SCOPES,
    SCOPE_CATALOG,
    permissions_for_scopes,
    token_scope_allows_permission,
)
from core.capabilities_registry import shipped_capabilities

READS = {
    "zentinelle.status_view",
    "zentinelle.policy_view",
    "zentinelle.usage_view",
    "zentinelle.conformance_view",
    "zentinelle.audit_view",
}


def test_narrow_scope_is_an_exact_read_allowlist():
    assert permissions_for_scopes(["read:zentinelle"]) == READS
    for permission in [
        "zentinelle.audit_export",
        "zentinelle.connect",
        "zentinelle.gateway_manage",
        "zentinelle.policy_edit",
        "zentinelle.configure",
        "api_token.create",
        "secret.read",
    ]:
        assert not token_scope_allows_permission(SimpleNamespace(scopes=["read:zentinelle"]), permission)
    assert "read:zentinelle" in ALLOWED_SCOPES
    assert "read:zentinelle" in {scope.value for scope in SCOPE_CATALOG}
    assert "identity.zentinelle_read_scope" in shipped_capabilities()


def test_explicit_login_preserves_default_and_cluster_ceilings():
    assert device_flow.normalize_client_kind("CLI-ZENTINELLE") == "cli-zentinelle"
    assert device_flow.token_scopes_for_client_kind("cli-zentinelle") == [
        *CLI_DEVICE_SCOPES,
        "read:zentinelle",
    ]
    for kind in ["cli", "cli-operator", "mobile", "browser", "ide", "unknown"]:
        assert "read:zentinelle" not in device_flow.token_scopes_for_client_kind(kind)


@pytest.mark.django_db
def test_browser_consent_and_refresh_retain_explicit_narrow_scope():
    from django.contrib.auth import get_user_model
    from django.test import Client

    from astrolift_identity.models import Member, Organization

    user = get_user_model().objects.create_user(
        username="narrow-reader", email="reader@example.test", password="test"
    )
    org = Organization.objects.create(name="Read org", slug="read-org")
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG,
        scope_id=org.pk,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    client = Client(REMOTE_ADDR="198.51.100.179")
    response = client.post(
        "/api/cli/v1/auth/start", data={"client_kind": "cli-zentinelle"}, content_type="application/json"
    )
    assert response.status_code == 200
    from astrolift_identity.models import DeviceFlowSession

    session = DeviceFlowSession.objects.get(session_guid=response.json()["session_id"])
    assert session.client_kind == "cli-zentinelle"
    assert device_flow.approve_session(session, user=user, organization=org) is None
    import datetime as dt

    from django.utils import timezone

    now = timezone.now() + device_flow.MIN_POLL_INTERVAL + dt.timedelta(seconds=1)
    issued = device_flow.poll_complete(session.session_guid, now=now)
    assert issued.status == "issued"
    session.refresh_from_db()
    prior_token = session.api_token_id
    refreshed = device_flow.refresh_credentials(
        issued.credentials.refresh_token, now=now + dt.timedelta(seconds=5)
    )
    assert refreshed.status == "issued"
    session.refresh_from_db()
    assert session.api_token_id != prior_token
    assert set(session.api_token.scopes) == set(CLI_DEVICE_SCOPES) | {"read:zentinelle"}
    for permission in READS:
        assert token_scope_allows_permission(session.api_token, permission)
    assert not token_scope_allows_permission(session.api_token, "zentinelle.audit_export")


@pytest.mark.django_db
def test_read_scope_never_replaces_account_or_tenant_authority():
    from django.contrib.auth import get_user_model

    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import Member, Organization, Role, RoleBinding
    from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind, check_permission
    from core.tenancy import TenantContext, tenant_context

    user = get_user_model().objects.create_user(username="scope-authority", email="scope@example.test")
    org = Organization.objects.create(name="Authority", slug="authority")
    other = Organization.objects.create(name="Other authority", slug="other-authority")
    Member.objects.create(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.pk, is_active=True)
    token = set_current_api_token(SimpleNamespace(scopes=["read:zentinelle"]))
    try:
        with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
            with pytest.raises(PermissionDenied):
                check_permission(
                    Permission.ZENTINELLE_STATUS_VIEW, scope=PermissionScope(ScopeKind.ORG, org.pk)
                )
        role = Role.objects.create(
            organization=org,
            name="Zentinelle reader",
            slug="z-reader",
            scope_level=Role.ScopeLevel.ORG,
            permissions=["zentinelle.status_view"],
        )
        RoleBinding.objects.create(
            user=user, role=role, scope_kind=RoleBinding.ScopeKind.ORG, scope_id=org.pk
        )
        with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
            check_permission(Permission.ZENTINELLE_STATUS_VIEW, scope=PermissionScope(ScopeKind.ORG, org.pk))
            with pytest.raises(PermissionDenied):
                check_permission(
                    Permission.ZENTINELLE_AUDIT_EXPORT, scope=PermissionScope(ScopeKind.ORG, org.pk)
                )
        with tenant_context(TenantContext(organization_id=other.pk, actor_user_id=user.pk)):
            with pytest.raises(PermissionDenied):
                check_permission(
                    Permission.ZENTINELLE_STATUS_VIEW, scope=PermissionScope(ScopeKind.ORG, other.pk)
                )
    finally:
        reset_current_api_token(token)
