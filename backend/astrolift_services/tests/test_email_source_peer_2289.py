"""Peer controls exercise persisted verified-account conflicts through actual HTTP."""

import pytest
from django.utils import timezone

from astrolift_clusters.models import TenantCluster
from astrolift_services.models import EmailDeliveryTest

from .test_email_delivery_http_2289 import ACCOUNT, outbound, send
from .test_email_delivery_http_2289 import wire as wire_fixture
from .test_email_delivery_http_2289 import world as world_fixture

pytestmark = pytest.mark.django_db


@pytest.fixture
def wire():
    yield from wire_fixture.__wrapped__()


@pytest.fixture
def world(client, wire, monkeypatch, settings):
    return world_fixture.__wrapped__(client, wire, monkeypatch, settings)


def test_verified_account_conflict_refuses_before_native_io(world):
    TenantCluster.objects.filter(pk=world.cluster.pk).update(cloud_account_id="999999999999")
    result = send(world)
    assert not result["ok"], "VERIFIED_ACCOUNT_CONFLICT_WAS_ACCEPTED"
    assert not world.wire["requests"]
    assert not EmailDeliveryTest.objects.exists()


@pytest.mark.parametrize("change_kind", ["account", "clear", "verification_time"])
def test_verified_account_change_after_identity_read_refuses_send(world, change_kind):
    TenantCluster.objects.filter(pk=world.cluster.pk).update(cloud_account_id=ACCOUNT)

    def change(_path):
        values = {
            "account": {"cloud_account_id": "999999999999"},
            "clear": {"cloud_account_id": ""},
            "verification_time": {"cloud_account_verified_at": timezone.now()},
        }[change_kind]
        TenantCluster.objects.filter(pk=world.cluster.pk).update(**values)

    world.wire["after_source"] = change
    result = send(world)
    assert not result["ok"], "WITHDRAWN_VERIFIED_ACCOUNT_WAS_ACCEPTED"
    assert not outbound(world)


def test_matching_verified_account_allows_native_send(world):
    TenantCluster.objects.filter(pk=world.cluster.pk).update(
        cloud_account_id=ACCOUNT, cloud_account_verified_at=timezone.now()
    )
    assert send(world)["ok"]
    assert len(outbound(world)) == 1


def test_verified_account_withdrawal_after_send_keeps_ack_privately(world):
    TenantCluster.objects.filter(pk=world.cluster.pk).update(cloud_account_id=ACCOUNT)

    def change():
        TenantCluster.objects.filter(pk=world.cluster.pk).update(cloud_account_id="999999999999")

    world.wire["after_send"] = change
    result = send(world)
    assert not result["ok"] and result["data"] is None
    assert result["errors"][0]["message"] == "EMAIL_SOURCE_CHANGED"
    row = EmailDeliveryTest.objects.get()
    assert row.status == "accepted" and row.provider_message_id == "native-message-123"
    assert len(outbound(world)) == 1
