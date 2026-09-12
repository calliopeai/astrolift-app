"""A selected organization must never silently become the token's organization."""

from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory

from astrolift_identity.api_tokens import get_current_api_token, mint_token
from astrolift_identity.middleware import ApiTokenAuthMiddleware
from astrolift_identity.models import ApiToken, Member, Organization
from astrolift_identity.schema.queries import IdentityQuery
from core.middleware.tenant import TenantContextMiddleware
from core.schema.exec_ws import exec_ws_application
from core.tenancy import get_current_tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def identity():
    user = get_user_model().objects.create(username="target-operator", email="target-operator@astrolift.dev")
    org = Organization.objects.create(name="Token org", slug="token-org")
    other = Organization.objects.create(name="Other org", slug="other-org")
    for row in (org, other):
        Member.objects.create(user=user, scope_kind="ORG", scope_id=row.pk)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=user,
        organization=org,
        name="target-token",
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
        scopes=["admin"],
    )
    return SimpleNamespace(user=user, org=org, other=other, token=token, bearer=minted.plaintext)


@pytest.mark.parametrize("privilege", ["member", "staff", "superuser"])
def test_token_organization_picker_excludes_other_memberships(identity, privilege):
    identity.user.is_staff = privilege == "staff"
    identity.user.is_superuser = privilege == "superuser"
    request = SimpleNamespace(user=identity.user, _api_token=identity.token)
    info = SimpleNamespace(context=SimpleNamespace(request=request))
    assert [org.slug for org in IdentityQuery().astrolift_organizations(info)] == [identity.org.slug]

    # A browser session still discovers all of the user's available organizations.
    del request._api_token
    assert {org.slug for org in IdentityQuery().astrolift_organizations(info)} == {
        identity.org.slug,
        identity.other.slug,
    }


@pytest.mark.parametrize("selection", ["other", "invalid", "matching", "absent"])
def test_http_rejects_token_target_mismatch_before_the_view(identity, selection):
    header = {
        "other": str(identity.other.guid),
        "invalid": "not-an-organization-guid",
        "matching": str(identity.org.guid).upper(),
        "absent": "",
    }[selection]
    request = RequestFactory().post(
        "/app/gql/config/",
        HTTP_AUTHORIZATION=f"Bearer {identity.bearer}",
        HTTP_X_ASTROLIFT_ORGANIZATION=header,
    )
    request.user = AnonymousUser()
    reached = []

    def view(request):
        reached.append(get_current_tenant().organization_id)
        return HttpResponse("ok")

    response = ApiTokenAuthMiddleware(TenantContextMiddleware(view))(request)
    if selection in ("matching", "absent"):
        assert response.status_code == 200
        assert reached == [identity.org.pk]
    else:
        assert response.status_code == 403
        assert b"organization" in response.content
        assert reached == []
    assert get_current_tenant() is None
    assert get_current_api_token() is None


@pytest.mark.parametrize("selection", ["other", "invalid", "matching", "absent"])
def test_exec_handshake_checks_selected_organization_before_target_lookup(identity, selection, monkeypatch):
    header = {
        "other": str(identity.other.guid),
        "invalid": "not-an-organization-guid",
        "matching": str(identity.org.guid),
        "absent": "",
    }[selection]
    looked_up = []

    async def resolve_target(*, target_slug, tenant_org_id):
        looked_up.append(tenant_org_id)
        return None

    monkeypatch.setattr("core.schema.exec_ws._resolve_exec_target", resolve_target)
    sent = []

    async def receive():
        pytest.fail("a rejected handshake must not open a terminal")

    async def send(message):
        sent.append(message)

    async_to_sync(exec_ws_application)(
        {
            "type": "websocket",
            "path": "/app/exec/same-box-slug/pod",
            "headers": [
                (b"authorization", f"Bearer {identity.bearer}".encode()),
                (b"x-astrolift-organization", header.encode()),
            ],
        },
        receive,
        send,
    )
    if selection in ("matching", "absent"):
        assert looked_up == [identity.org.pk]
        assert sent == [{"type": "websocket.close", "code": 4404}]
    else:
        assert looked_up == []
        assert sent == [{"type": "websocket.close", "code": 4403}]
