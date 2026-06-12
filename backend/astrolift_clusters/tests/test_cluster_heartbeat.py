"""Tests for the cluster keep-alive / heartbeat endpoint (#808).

POST /api/dispatch/v1/clusters/<cluster_id>/heartbeat/

Boundaries pinned here:
* valid Bearer key stamps last_heartbeat_at and returns ok
* is_live is True when last_heartbeat_at is within 5 minutes
* is_live is False when last_heartbeat_at is older than 5 minutes
* is_live is False when last_heartbeat_at is None
* bad Bearer token returns 401
* missing Authorization header returns 401
* correct key against wrong cluster_id returns 401 (no enumeration)
* inactive cluster returns 401
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization

HEARTBEAT = "/api/dispatch/v1/clusters/{}/heartbeat/"

RAW_KEY = "test-cluster-secret-key-abcdefghijklmnopqrstuvwxyz01234567890abc"


def _key_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _auth(raw: str = RAW_KEY) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Silence the User -> Profile -> OpenSearch indexing chain."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="HbOrg", slug=f"hb-org-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s",
                slug=f"k8s-hb-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


@pytest.fixture
def cluster(org, plugin):
    return TenantCluster.objects.create(
        organization=org,
        provider_plugin=plugin,
        name="HB Cluster",
        slug=f"hb-cluster-{uuid.uuid4().hex[:6]}",
        auth_method=TenantCluster.AuthMethod.SERVICE_ACCOUNT_TOKEN,
        api_key_hash=_key_hash(RAW_KEY),
    )


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_heartbeat_stamps_last_heartbeat_at(cluster):
    before = timezone.now()
    resp = Client().post(HEARTBEAT.format(cluster.guid), **_auth())

    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["ok"] is True
    assert body["cluster_id"] == str(cluster.guid)

    cluster.refresh_from_db()
    assert cluster.last_heartbeat_at is not None
    assert cluster.last_heartbeat_at >= before


@pytest.mark.django_db
def test_heartbeat_response_includes_received_at(cluster):
    resp = Client().post(HEARTBEAT.format(cluster.guid), **_auth())

    assert resp.status_code == 200
    body = resp.json()
    assert "received_at" in body
    # Must parse as ISO 8601
    dt.datetime.fromisoformat(body["received_at"])


# ---------------------------------------------------------------------------
# is_live property
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_is_live_true_after_recent_heartbeat(cluster):
    cluster.last_heartbeat_at = timezone.now() - dt.timedelta(minutes=2)
    cluster.save(update_fields=["last_heartbeat_at"])
    assert cluster.is_live is True


@pytest.mark.django_db
def test_is_live_false_when_heartbeat_stale(cluster):
    cluster.last_heartbeat_at = timezone.now() - dt.timedelta(minutes=6)
    cluster.save(update_fields=["last_heartbeat_at"])
    assert cluster.is_live is False


@pytest.mark.django_db
def test_is_live_false_when_no_heartbeat(cluster):
    assert cluster.last_heartbeat_at is None
    assert cluster.is_live is False


@pytest.mark.django_db
def test_is_live_boundary_exactly_at_threshold(cluster):
    """A heartbeat exactly at the 300s boundary is NOT live (strictly less than)."""
    cluster.last_heartbeat_at = timezone.now() - dt.timedelta(seconds=300)
    cluster.save(update_fields=["last_heartbeat_at"])
    assert cluster.is_live is False


@pytest.mark.django_db
def test_is_live_one_second_before_threshold(cluster):
    cluster.last_heartbeat_at = timezone.now() - dt.timedelta(seconds=299)
    cluster.save(update_fields=["last_heartbeat_at"])
    assert cluster.is_live is True


# ---------------------------------------------------------------------------
# auth failures
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_heartbeat_bad_token_401(cluster):
    resp = Client().post(HEARTBEAT.format(cluster.guid), **_auth("wrong-key"))
    assert resp.status_code == 401
    cluster.refresh_from_db()
    assert cluster.last_heartbeat_at is None


@pytest.mark.django_db
def test_heartbeat_no_auth_header_401(cluster):
    resp = Client().post(HEARTBEAT.format(cluster.guid))
    assert resp.status_code == 401


@pytest.mark.django_db
def test_heartbeat_correct_key_wrong_cluster_401(cluster, org, plugin):
    """A valid key must not update a different cluster (no cross-cluster access)."""
    other = TenantCluster.objects.create(
        organization=org,
        provider_plugin=plugin,
        name="Other Cluster",
        slug=f"other-cluster-{uuid.uuid4().hex[:6]}",
        auth_method=TenantCluster.AuthMethod.SERVICE_ACCOUNT_TOKEN,
        api_key_hash=_key_hash("totally-different-key"),
    )
    # Use cluster's key but target other's GUID
    resp = Client().post(HEARTBEAT.format(other.guid), **_auth(RAW_KEY))
    assert resp.status_code == 401
    other.refresh_from_db()
    assert other.last_heartbeat_at is None


@pytest.mark.django_db
def test_heartbeat_inactive_cluster_401(cluster):
    cluster.is_active = False
    cluster.save(update_fields=["is_active"])
    resp = Client().post(HEARTBEAT.format(cluster.guid), **_auth())
    assert resp.status_code == 401


@pytest.mark.django_db
def test_heartbeat_unknown_cluster_id_401():
    """Completely unknown cluster_id must not reveal whether it exists."""
    resp = Client().post(
        HEARTBEAT.format("00000000-0000-0000-0000-000000000000"),
        **_auth(),
    )
    assert resp.status_code == 401
