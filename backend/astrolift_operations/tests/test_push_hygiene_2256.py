"""Push housekeeping left open by #2256, on real Postgres.

* ``astroliftMyDevices`` ordered by ``last_used_at``, which
  ``DeviceRegistration`` does not have, so every caller got a ``FieldError``.
* A Notification Hubs send answered 404, which is a wrong hub name, came back
  ``invalid_token``, and the dispatcher soft-deleted the registration. The
  real driver runs here; only ``urlopen`` is replaced, by the hub's answer.
"""

from __future__ import annotations

import datetime as dt
import io
import urllib.error
import urllib.request
import uuid
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

import core.events as _events_mod
from astrolift_identity.models import Member, Organization
from astrolift_operations.models import AuditEvent, DeviceRegistration, NotificationProfile
from astrolift_operations.notification_dispatch import dispatch_event, unset_driver_override_for_tests
from core.events import Event, register_event_subscriber
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import make_info

pytestmark = pytest.mark.django_db

HUB_SEND = "https://acme-ns.servicebus.windows.net/no-such-hub/messages?api-version=2020-06"


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def dispatcher(monkeypatch):
    """Production driver resolution and the real subscriber."""
    monkeypatch.setenv("ASTROLIFT_INSTALL_ID", "install-2256ff0000000000000000ff")
    unset_driver_override_for_tests()
    snapshot = list(_events_mod._subscribers)
    register_event_subscriber(dispatch_event)
    yield
    _events_mod._subscribers[:] = snapshot


def _org(slug: str) -> Organization:
    return Organization.objects.create(name=slug.title(), slug=f"{slug}-{uuid.uuid4().hex[:6]}")


def _member(org: Organization):
    user = get_user_model().objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.test", username=f"u-{uuid.uuid4().hex[:8]}"
    )
    Member.objects.create(
        user=user,
        scope_kind=Member.ScopeKind.ORG.value,
        scope_id=org.pk,
        is_active=True,
        lifecycle=Member.Lifecycle.ACTIVE,
    )
    return user


def _device(user, org: Organization, label: str, *, driver: str = "apns") -> DeviceRegistration:
    return DeviceRegistration.objects.create(
        user=user,
        organization=org,
        device_token=f"{uuid.uuid4().hex}{uuid.uuid4().hex}",
        platform="ios",
        label=label,
        driver=driver,
    )


def _push_audits(org: Organization) -> list[dict[str, Any]]:
    rows = AuditEvent.objects.filter(action="notification.push", organization_id=org.pk).order_by("pk")
    return [{**row.data, "decision": row.decision} for row in rows]


# ---- astroliftMyDevices -------------------------------------------------------


def test_my_devices_lists_the_callers_live_rows_most_recently_seen_first() -> None:
    from config.schema import schema

    org = _org("acme")
    me, teammate = _member(org), _member(org)
    seen = _device(me, org, "seen")
    # Written in neither registration order nor its reverse, so only the
    # registered_at tie-breaker puts the never-seen rows in order.
    never_seen = {days: _device(me, org, f"registered {days}d ago") for days in (2, 4, 1, 3)}
    _device(me, org, "revoked").soft_delete()
    _device(me, org, "stale").mark_stale()
    _device(teammate, org, "a teammate's")
    now = timezone.now()
    # Registered first but seen since, so it leads; Postgres puts NULLs
    # first in a descending order unless told otherwise.
    DeviceRegistration.objects.filter(pk=seen.pk).update(
        registered_at=now - dt.timedelta(days=9), last_seen_at=now - dt.timedelta(hours=1)
    )
    for days, device in never_seen.items():
        DeviceRegistration.objects.filter(pk=device.pk).update(registered_at=now - dt.timedelta(days=days))

    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=me.pk)):
        result = schema.execute_sync(
            "{ astroliftMyDevices { id label lastSeenAt } }", context_value=make_info(me).context
        )

    assert result.errors is None
    rows = result.data["astroliftMyDevices"]
    assert [(row["id"], row["label"]) for row in rows] == [
        (str(seen.guid), "seen"),
        *((str(never_seen[days].guid), f"registered {days}d ago") for days in (1, 2, 3, 4)),
    ]
    assert rows[0]["lastSeenAt"] is not None
    assert {row["lastSeenAt"] for row in rows[1:]} == {None}


# ---- Notification Hubs ----------------------------------------------------------


def test_a_notification_hubs_404_fails_the_send_and_keeps_the_registration(dispatcher, monkeypatch) -> None:
    org = _org("acme")
    NotificationProfile.objects.create(
        organization=org,
        driver="azure_anh",
        config={
            "namespace": "acme-ns",
            "hub_name": "no-such-hub",
            "shared_access_key_name": "DefaultFullSharedAccessSignature",
            "shared_access_key": "c2VjcmV0LWtleQ==",
        },
    )
    device = _device(_member(org), org, "iPhone", driver="azure_anh")
    sent: list[str] = []

    def hub(request: urllib.request.Request, timeout: float):
        sent.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", hub)
    incident = str(uuid.uuid4())
    Event.emit(
        "alert.fired",
        {
            "rule_name": "5xx surge",
            "severity": "critical",
            "summary": "web is firing",
            "alert_event_guid": incident,
        },
        resource_kind="alert_event",
        resource_id=incident,
        organization_id=org.pk,
    )

    assert sent == [HUB_SEND]
    device.refresh_from_db()
    assert (device.stale_at, device.deleted_at) == (None, None)
    [audit] = _push_audits(org)
    assert (audit["status"], audit["decision"], audit["driver"]) == ("failed", "DENY", "azure_anh")
    assert audit["detail"] == (
        "anh http 404: HTTP Error 404: Not Found (no hub at this address: check the profile's namespace and hub_name)"
    )
