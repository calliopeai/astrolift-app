"""Tests for the cluster keep-alive heartbeat surface (#808).

Covers:
* ``issueClusterAgentKey`` mutation — permission gate, key issuance +
  hash persistence (plaintext never stored), rotation, interval clamp,
  tenant isolation, NOT_FOUND on unknown guid.
* The ``POST /api/clusters/v1/<guid>/heartbeat/`` ingest view —
  unauthorized (no key / wrong key / key scoped to a different
  cluster), happy path (last_heartbeat_at + payload persisted, interval
  returned), payload allow-listing, malformed JSON, NEVER_SEEN ->
  CONNECTED status transition observed end-to-end.
* ``astroliftClusterLiveState`` query — snapshot derivation from the
  persisted payload, offline status when silent, None on unknown guid.

Real Postgres, real HTTP client — no DB / network mocks, per the
project's testing posture.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from types import SimpleNamespace

import pytest
from django.test import Client
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import (
    ClustersMutation,
    IssueClusterAgentKeyInput,
)
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- Scaffolding -----------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Suppress the User -> Profile -> OpenSearch indexing chain that
    fires on Organization create — same pattern the management-mutation
    suite uses."""
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
    return Organization.objects.create(name="Acme", slug=f"acme-hb-{uuid.uuid4().hex[:6]}")


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


def _make_cluster(org, plugin, **overrides):
    defaults = {
        "organization": org,
        "slug": f"c-{uuid.uuid4().hex[:6]}",
        "name": "dev",
        "provider_plugin": plugin,
        "provider_config": {},
        "endpoint": "https://invalid",
        "auth_method": TenantCluster.AuthMethod.KUBECONFIG,
        "auth_config": {"kubeconfig": "fake"},
        "is_active": True,
        "lifecycle": TenantCluster.Lifecycle.MANAGED.value,
    }
    defaults.update(overrides)
    return TenantCluster.objects.create(**defaults)


@pytest.fixture
def cluster(org, plugin):
    return _make_cluster(org, plugin)


def _info():
    request = SimpleNamespace(user=None)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _heartbeat_url(cluster: TenantCluster) -> str:
    return f"/api/clusters/v1/{cluster.guid}/heartbeat/"


def _bearer(raw_key: str) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


# ---- issueClusterAgentKey: permission gate ---------------------------


def test_issue_denied_without_cluster_manage(cluster, org, permission_resolver):
    with _ctx(org):
        result = ClustersMutation().issue_cluster_agent_key(
            _info(),
            IssueClusterAgentKeyInput(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PERMISSION_DENIED"
    cluster.refresh_from_db()
    # Denial MUST NOT provision a key.
    assert cluster.agent_key_hash == ""


def test_issue_returns_not_found_on_unknown_guid(org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().issue_cluster_agent_key(
            _info(),
            IssueClusterAgentKeyInput(cluster_id=GUID(str(uuid.uuid4()))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"


# ---- issueClusterAgentKey: issuance + hashing ------------------------


def test_issue_stores_hash_not_plaintext(cluster, org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().issue_cluster_agent_key(
            _info(),
            IssueClusterAgentKeyInput(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is True
    raw = result.data.agent_key
    # token_hex(32) -> 256 bits of entropy as 64 hex chars (same key
    # shape the dispatch / runner agents use). Long enough to be
    # unguessable; the exact length is fixed by the encoding.
    assert raw and len(raw) == 64
    cluster.refresh_from_db()
    # The plaintext key is NEVER stored — only its SHA-256 (64 hex chars).
    assert cluster.agent_key_hash == _hash(raw)
    assert len(cluster.agent_key_hash) == 64
    assert raw != cluster.agent_key_hash
    # First issuance is not a rotation.
    assert result.data.rotated is False


def test_issue_rotates_existing_key(cluster, org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        first = ClustersMutation().issue_cluster_agent_key(
            _info(),
            IssueClusterAgentKeyInput(cluster_id=GUID(str(cluster.guid))),
        )
        second = ClustersMutation().issue_cluster_agent_key(
            _info(),
            IssueClusterAgentKeyInput(cluster_id=GUID(str(cluster.guid))),
        )
    assert first.ok and second.ok
    # Rotation issues a different key and flags it as a rotation.
    assert first.data.agent_key != second.data.agent_key
    assert second.data.rotated is True
    cluster.refresh_from_db()
    # The OLD key no longer matches the stored hash.
    assert cluster.agent_key_hash != _hash(first.data.agent_key)
    assert cluster.agent_key_hash == _hash(second.data.agent_key)


def test_issue_clamps_interval(cluster, org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        # Above the <=60s AC ceiling -> clamped to 60.
        high = ClustersMutation().issue_cluster_agent_key(
            _info(),
            IssueClusterAgentKeyInput(cluster_id=GUID(str(cluster.guid)), interval_seconds=9999),
        )
        # Below the floor -> clamped to 5.
        low = ClustersMutation().issue_cluster_agent_key(
            _info(),
            IssueClusterAgentKeyInput(cluster_id=GUID(str(cluster.guid)), interval_seconds=1),
        )
    assert high.data.interval_seconds == 60
    assert low.data.interval_seconds == 5
    cluster.refresh_from_db()
    assert cluster.heartbeat_interval_seconds == 5


def test_issue_isolated_across_tenants(org, plugin, permission_resolver):
    """A caller scoped to org A must not be able to mint an agent key
    for org B's cluster — tenant_scoped() filters it out, so the row is
    invisible and the resolver returns NOT_FOUND."""
    other_org = Organization.objects.create(name="Beta", slug=f"beta-hb-{uuid.uuid4().hex[:6]}")
    other_cluster = _make_cluster(other_org, plugin)
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().issue_cluster_agent_key(
            _info(),
            IssueClusterAgentKeyInput(cluster_id=GUID(str(other_cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"
    other_cluster.refresh_from_db()
    assert other_cluster.agent_key_hash == ""


# ---- heartbeat ingest: auth ------------------------------------------


def test_heartbeat_401_without_bearer(cluster):
    client = Client()
    resp = client.post(_heartbeat_url(cluster), data="{}", content_type="application/json")
    assert resp.status_code == 401
    cluster.refresh_from_db()
    assert cluster.last_heartbeat_at is None


def test_heartbeat_401_with_wrong_key(cluster):
    cluster.agent_key_hash = _hash("the-real-key")
    cluster.save(update_fields=["agent_key_hash"])
    client = Client()
    resp = client.post(
        _heartbeat_url(cluster),
        data="{}",
        content_type="application/json",
        **_bearer("a-different-key"),
    )
    assert resp.status_code == 401


def test_heartbeat_401_when_key_scoped_to_other_cluster(org, plugin):
    """A valid key for cluster A cannot drive heartbeats for cluster B —
    the view binds the key hash to the guid in the URL."""
    raw_a = "key-for-a"
    cluster_a = _make_cluster(org, plugin, agent_key_hash=_hash(raw_a))
    cluster_b = _make_cluster(org, plugin)  # no key
    client = Client()
    # Post cluster A's (valid) key to cluster B's URL — must be rejected.
    resp = client.post(
        _heartbeat_url(cluster_b),
        data="{}",
        content_type="application/json",
        **_bearer(raw_a),
    )
    assert resp.status_code == 401
    cluster_b.refresh_from_db()
    assert cluster_b.last_heartbeat_at is None
    # And cluster A itself was never pulsed by this cross-target attempt.
    cluster_a.refresh_from_db()
    assert cluster_a.last_heartbeat_at is None


def test_heartbeat_401_for_soft_deleted_cluster(cluster):
    raw = "live-key"
    cluster.agent_key_hash = _hash(raw)
    cluster.save(update_fields=["agent_key_hash"])
    cluster.soft_delete()
    client = Client()
    resp = client.post(
        _heartbeat_url(cluster),
        data="{}",
        content_type="application/json",
        **_bearer(raw),
    )
    assert resp.status_code == 401


# ---- heartbeat ingest: happy path ------------------------------------


def test_heartbeat_persists_timestamp_and_payload(cluster):
    raw = "good-key"
    cluster.agent_key_hash = _hash(raw)
    cluster.heartbeat_interval_seconds = 45
    cluster.save(update_fields=["agent_key_hash", "heartbeat_interval_seconds"])
    assert cluster.last_heartbeat_at is None

    payload = {
        "node_count": 3,
        "node_ready_count": 2,
        "pods_by_namespace": {"astrolift-system": 4, "acme-prod": 11},
        "app_readiness": {"acme": {"ready": 2, "total": 3}},
        "cpu_utilization": 0.42,
        "memory_utilization": 0.61,
        "ingress_ips": ["203.0.113.10"],
        "agent_version": "0.2.0",
    }
    client = Client()
    resp = client.post(
        _heartbeat_url(cluster),
        data=json.dumps(payload),
        content_type="application/json",
        **_bearer(raw),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # The configured cadence is echoed so the agent can self-tune.
    assert body["interval_seconds"] == 45

    cluster.refresh_from_db()
    assert cluster.last_heartbeat_at is not None
    assert cluster.last_heartbeat_payload["node_count"] == 3
    # #112 rich-telemetry keys round-trip through the allow-list.
    assert cluster.last_heartbeat_payload["node_ready_count"] == 2
    assert cluster.last_heartbeat_payload["app_readiness"]["acme"]["ready"] == 2
    assert cluster.last_heartbeat_payload["pods_by_namespace"]["acme-prod"] == 11
    assert cluster.last_heartbeat_payload["agent_version"] == "0.2.0"


def test_heartbeat_allow_lists_payload_keys(cluster):
    """An over-eager agent can't stuff arbitrary keys onto the row — the
    view projects the body down to the known snapshot keys."""
    raw = "good-key-2"
    cluster.agent_key_hash = _hash(raw)
    cluster.save(update_fields=["agent_key_hash"])
    client = Client()
    resp = client.post(
        _heartbeat_url(cluster),
        data=json.dumps({"node_count": 1, "evil": "drop tables", "secret": "x"}),
        content_type="application/json",
        **_bearer(raw),
    )
    assert resp.status_code == 200
    cluster.refresh_from_db()
    assert cluster.last_heartbeat_payload == {"node_count": 1}


def test_heartbeat_accepts_empty_body(cluster):
    raw = "good-key-3"
    cluster.agent_key_hash = _hash(raw)
    cluster.save(update_fields=["agent_key_hash"])
    client = Client()
    # An empty body must be sent with an explicit content_type — without
    # one Django's test client tries to multipart-encode the string and
    # raises before the request is even built. The agent posts an empty
    # JSON body when it has no snapshot to report (the timestamp is the
    # only signal that matters).
    resp = client.post(
        _heartbeat_url(cluster),
        data="",
        content_type="application/json",
        **_bearer(raw),
    )
    assert resp.status_code == 200
    cluster.refresh_from_db()
    assert cluster.last_heartbeat_at is not None
    assert cluster.last_heartbeat_payload == {}


def test_heartbeat_400_on_malformed_json(cluster):
    raw = "good-key-4"
    cluster.agent_key_hash = _hash(raw)
    cluster.save(update_fields=["agent_key_hash"])
    client = Client()
    resp = client.post(
        _heartbeat_url(cluster),
        data="{not json",
        content_type="application/json",
        **_bearer(raw),
    )
    assert resp.status_code == 400
    cluster.refresh_from_db()
    # A malformed pulse must not stamp last_heartbeat_at.
    assert cluster.last_heartbeat_at is None


def test_heartbeat_400_on_non_object_body(cluster):
    raw = "good-key-5"
    cluster.agent_key_hash = _hash(raw)
    cluster.save(update_fields=["agent_key_hash"])
    client = Client()
    resp = client.post(
        _heartbeat_url(cluster),
        data=json.dumps([1, 2, 3]),
        content_type="application/json",
        **_bearer(raw),
    )
    assert resp.status_code == 400


def test_heartbeat_get_not_allowed(cluster):
    client = Client()
    resp = client.get(_heartbeat_url(cluster))
    assert resp.status_code == 405


# ---- end-to-end: issue -> heartbeat -> live state --------------------


def test_full_round_trip_never_seen_to_connected(cluster, org, permission_resolver):
    """Issue a key, post a heartbeat with it, then read the live state —
    status moves NEVER_SEEN -> CONNECTED and the snapshot surfaces."""
    # The round trip touches both surfaces: issueClusterAgentKey
    # (cluster.manage) and astroliftClusterLiveState (cluster.register).
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    # Before any heartbeat: NEVER_SEEN.
    with _ctx(org):
        before = ClustersQuery().astrolift_cluster_live_state(_info(), GUID(str(cluster.guid)))
    assert before is not None
    assert before.status == "never_seen"
    assert before.agent_provisioned is False
    assert before.last_heartbeat_at is None

    # Issue the agent key.
    with _ctx(org):
        issued = ClustersMutation().issue_cluster_agent_key(
            _info(),
            IssueClusterAgentKeyInput(cluster_id=GUID(str(cluster.guid)), interval_seconds=30),
        )
    assert issued.ok
    raw = issued.data.agent_key

    # Post a heartbeat as the agent.
    client = Client()
    resp = client.post(
        _heartbeat_url(cluster),
        data=json.dumps(
            {
                "node_count": 5,
                "pods_by_namespace": {"astrolift-system": 3, "acme-prod": 9},
                "cpu_utilization": 0.5,
                "memory_utilization": 0.4,
                "ingress_ips": ["198.51.100.7"],
                "agent_version": "0.2.0",
            }
        ),
        content_type="application/json",
        **_bearer(raw),
    )
    assert resp.status_code == 200

    # Now the live state is CONNECTED with the snapshot rolled up.
    with _ctx(org):
        after = ClustersQuery().astrolift_cluster_live_state(_info(), GUID(str(cluster.guid)))
    assert after is not None
    assert after.status == "connected"
    assert after.agent_provisioned is True
    assert after.last_heartbeat_at is not None
    assert after.heartbeat_age_seconds is not None and after.heartbeat_age_seconds >= 0
    assert after.node_count == 5
    assert after.pod_total == 12  # 3 + 9
    assert after.cpu_utilization == 0.5
    assert after.ingress_ips == ["198.51.100.7"]
    assert after.agent_version == "0.2.0"


def test_live_state_offline_when_silent(cluster, org, permission_resolver):
    """A cluster whose last heartbeat is well past the offline threshold
    reads OFFLINE — the signal the dependent tabs key their empty-state
    on."""
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    cluster.last_heartbeat_at = timezone.now() - dt.timedelta(hours=2)
    cluster.heartbeat_interval_seconds = 30
    cluster.agent_key_hash = _hash("k")
    cluster.save(
        update_fields=[
            "last_heartbeat_at",
            "heartbeat_interval_seconds",
            "agent_key_hash",
        ]
    )
    with _ctx(org):
        state = ClustersQuery().astrolift_cluster_live_state(_info(), GUID(str(cluster.guid)))
    assert state is not None
    assert state.status == "offline"
    assert state.agent_provisioned is True


def test_live_state_none_on_unknown_guid(org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with _ctx(org):
        state = ClustersQuery().astrolift_cluster_live_state(_info(), GUID(str(uuid.uuid4())))
    assert state is None


def test_live_state_denied_without_permission(cluster, org, permission_resolver):
    """The live-state read is permission-gated (cluster.register) and
    tenant-scoped — a caller without the grant is denied."""
    from core.permissions import PermissionDenied

    with _ctx(org), pytest.raises(PermissionDenied):
        ClustersQuery().astrolift_cluster_live_state(_info(), GUID(str(cluster.guid)))


def test_live_state_isolated_across_tenants(org, plugin, permission_resolver):
    """A caller scoped to org A must not read the live state of org B's
    cluster — the org-scoped lookup makes it read as None (identical to
    a nonexistent guid)."""
    other_org = Organization.objects.create(name="Gamma", slug=f"gamma-hb-{uuid.uuid4().hex[:6]}")
    other_cluster = _make_cluster(other_org, plugin, last_heartbeat_at=timezone.now())
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with _ctx(org):
        state = ClustersQuery().astrolift_cluster_live_state(_info(), GUID(str(other_cluster.guid)))
    assert state is None


def test_live_state_platform_cluster_visible_to_any_org(plugin, permission_resolver):
    """A platform-shared cluster (organization=None) is visible to any
    org's live-state read — that's the shared-cluster carve-out."""
    viewer_org = Organization.objects.create(name="Delta", slug=f"delta-hb-{uuid.uuid4().hex[:6]}")
    shared = _make_cluster(None, plugin, last_heartbeat_at=timezone.now())
    permission_resolver.grant(Permission.CLUSTER_REGISTER)
    with _ctx(viewer_org):
        state = ClustersQuery().astrolift_cluster_live_state(_info(), GUID(str(shared.guid)))
    assert state is not None
    assert state.status == "connected"
