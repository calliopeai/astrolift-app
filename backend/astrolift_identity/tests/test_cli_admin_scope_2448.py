"""``astro auth login --scope admin``: a platform operator's CLI sign-in (#2448).

The CLI asks with ``client_kind=cli-admin``. Only the platform operator may
approve it; the approval page says it is full administrative access; the
sign-in lasts ``CLI_ADMIN_SESSION_HOURS`` (12 by default) and never longer
than a plain CLI sign-in; who approved it, and from where, is audited. A
session never gains scopes by claiming a client label.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from astrolift_identity import device_flow
from astrolift_identity.api_tokens import DEFAULT_SCOPES, SCOPE_ADMIN
from astrolift_identity.models import ApiToken, DeviceFlowSession, Member, Organization

pytestmark = pytest.mark.django_db
User = get_user_model()

APPROVER_IP = "203.0.113.50"
DEVICE_IP = "198.51.100.60"


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


def _user(org, *, operator: bool):
    name = f"u-{uuid.uuid4().hex[:8]}"
    user = User.objects.create_user(username=name, email=f"{name}@astrolift.dev", password="pw")
    if operator:
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
    Member.objects.create(
        user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.id, is_active=True, lifecycle="active"
    )
    return user


@pytest.fixture
def audits(monkeypatch):
    seen = []
    monkeypatch.setattr("core.mutations._audit_writer", seen.append)
    return SimpleNamespace(admin=lambda: [e for e in seen if e.action == "auth.device_flow.admin_approved"])


def _admin_request():
    row, sid = device_flow.create_session(
        client_label="astro on laptop", client_kind="cli-admin", client_ip=DEVICE_IP
    )
    return row, sid


def _approve_in_browser(user, sid):
    client = Client(REMOTE_ADDR=APPROVER_IP)
    client.force_login(user)
    return client.post(f"/app/cli/auth/device/{sid}/", data={"action": "approve"})


def _issue(sid, now=None):
    later = (now or timezone.now()) + device_flow.MIN_POLL_INTERVAL + dt.timedelta(seconds=1)
    result = device_flow.poll_complete(sid, now=later)
    assert result.status == "issued"
    return result.credentials, later


def test_the_approval_page_says_it_is_full_administrative_access(org):
    operator = _user(org, operator=True)
    _, sid = _admin_request()
    client = Client()
    client.force_login(operator)
    page = client.get(f"/app/cli/auth/device/{sid}/").content.decode()
    assert "Full administrative access." in page
    assert "for 12 hours" in page
    assert '<span class="scope">admin</span>' in page
    assert 'value="approve"' in page


def test_the_platform_operator_approves_and_the_token_is_admin(org, audits):
    operator = _user(org, operator=True)
    row, sid = _admin_request()

    response = _approve_in_browser(operator, sid)

    assert response.status_code == 200
    row.refresh_from_db()
    assert row.state == DeviceFlowSession.STATE_APPROVED
    assert row.approved_user_id == operator.pk
    creds, issued_at = _issue(sid)
    row.refresh_from_db()
    assert row.api_token.scopes == [SCOPE_ADMIN]
    assert ApiToken.objects.get(pk=row.api_token_id).user_id == operator.pk
    [entry] = audits.admin()
    assert (entry.action, entry.decision, entry.actor_user_id, entry.organization_id) == (
        "auth.device_flow.admin_approved",
        "ALLOW",
        operator.pk,
        org.pk,
    )
    assert entry.extra["approver_ip"] == APPROVER_IP
    assert entry.extra["device_ip"] == DEVICE_IP
    assert entry.extra["session_hours"] == 12


def test_anyone_but_the_platform_operator_is_refused_with_a_clear_message(org, audits):
    owner = _user(org, operator=False)  # an org member, not the install's operator
    row, sid = _admin_request()
    client = Client()
    client.force_login(owner)

    page = client.get(f"/app/cli/auth/device/{sid}/").content.decode()
    assert "Only the platform operator can approve full administrative access." in page
    assert 'value="approve"' not in page

    response = _approve_in_browser(owner, sid)

    assert response.status_code == 403
    assert b"Only the platform operator can approve full administrative access." in response.content
    row.refresh_from_db()
    assert row.state == DeviceFlowSession.STATE_PENDING
    assert row.approved_user_id is None
    [entry] = audits.admin()
    assert (entry.decision, entry.actor_user_id, entry.extra["approver_ip"]) == (
        "DENY",
        owner.pk,
        APPROVER_IP,
    )
    assert device_flow.approve_session(row, user=owner, organization=org) == "operator_required"


def test_the_sign_in_lasts_twelve_hours_and_refreshing_never_extends_it(org):
    operator = _user(org, operator=True)
    row, sid = _admin_request()
    device_flow.approve_session(row, user=operator, organization=org)
    creds, issued_at = _issue(sid)
    row.refresh_from_db()
    end = issued_at + dt.timedelta(hours=12)
    assert row.refresh_token_expires_at == end
    assert row.access_token_expires_at == issued_at + device_flow.ACCESS_TOKEN_TTL

    near_end = end - dt.timedelta(minutes=20)
    refreshed = device_flow.refresh_credentials(creds.refresh_token, now=near_end)
    assert refreshed.status == "issued"
    row.refresh_from_db()
    assert row.refresh_token_expires_at == end
    # The access token stops with the sign-in, not an hour later.
    assert refreshed.credentials.access_token_expires_at == end
    assert row.api_token.scopes == [SCOPE_ADMIN]

    late = device_flow.refresh_credentials(
        refreshed.credentials.refresh_token, now=end + dt.timedelta(seconds=1)
    )
    assert late.status == "expired"


@pytest.mark.parametrize(
    ("hours", "lasts"),
    [(2, dt.timedelta(hours=2)), (0, dt.timedelta(hours=1)), (24 * 365, device_flow.REFRESH_TOKEN_TTL)],
)
def test_the_length_is_configurable_but_never_longer_than_a_plain_cli_sign_in(settings, hours, lasts):
    settings.CLI_ADMIN_SESSION_HOURS = hours
    assert device_flow.admin_session_ttl() == lasts


def test_a_plain_cli_sign_in_keeps_its_thirty_days(org):
    user = _user(org, operator=True)
    row, sid = device_flow.create_session(client_kind="cli")
    device_flow.approve_session(row, user=user, organization=org)
    _, issued_at = _issue(sid)
    row.refresh_from_db()
    assert row.refresh_token_expires_at == issued_at + device_flow.REFRESH_TOKEN_TTL
    assert SCOPE_ADMIN not in row.api_token.scopes


def test_an_enrollment_cannot_claim_admin_by_its_client_kind(org):
    operator = _user(org, operator=True)
    created = device_flow.create_enrollment(user=operator, organization=org)
    result = device_flow.consume_enrollment(created.token_plaintext, client_kind="cli-admin")
    assert result.status == "issued"
    row = DeviceFlowSession.all_objects.get(pk=result.session.pk)
    assert row.client_kind == "cli-admin"
    assert row.api_token.scopes == list(DEFAULT_SCOPES)
    assert row.refresh_token_expires_at > timezone.now() + dt.timedelta(days=29)


def test_an_admin_request_approved_by_anyone_else_issues_only_the_read_only_baseline(org):
    # approve_session refuses them; this is the second gate, at issue.
    owner = _user(org, operator=False)
    row, sid = _admin_request()
    row.state, row.approved_user, row.organization = DeviceFlowSession.STATE_APPROVED, owner, org
    row.save()
    _issue(sid)
    row.refresh_from_db()
    assert row.api_token.scopes == list(DEFAULT_SCOPES)


def test_a_demoted_operator_cannot_refresh_an_admin_sign_in(org):
    operator = _user(org, operator=True)
    row, sid = _admin_request()
    device_flow.approve_session(row, user=operator, organization=org)
    creds, issued_at = _issue(sid)
    operator.is_superuser = False
    operator.save(update_fields=["is_superuser"])

    result = device_flow.refresh_credentials(creds.refresh_token, now=issued_at + dt.timedelta(minutes=5))

    assert result.status == "expired"
    row.refresh_from_db()
    assert ApiToken.all_objects.get(pk=row.api_token_id).is_revoked is True


def test_an_unknown_kind_never_becomes_admin():
    assert device_flow.normalize_client_kind("CLI-ADMIN") == "cli-admin"
    assert device_flow.normalize_client_kind("admin") == "cli"
    assert SCOPE_ADMIN not in device_flow.token_scopes_for_client_kind("admin")
