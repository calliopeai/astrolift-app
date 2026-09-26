"""Tests for the ``deployClusterAgent`` mutation (#873).

Boundaries pinned here:
* permission gate denies callers without ``cluster.manage``
* a cluster with no issued agent key returns PRECONDITION (the agent
  Secret can't exist yet, so deploying the Deployment is premature)
* an inactive cluster returns PRECONDITION
* an unknown / out-of-scope guid returns NOT_FOUND
* the happy path applies the agent manifests and clears
  ``last_management_error``
* a driver build failure (ClusterManagementError) is persisted to
  ``last_management_error`` and surfaced as INTERNAL
* a per-manifest apply failure (ApplyResult.ok False) is persisted to
  ``last_management_error`` and surfaced as INTERNAL

The driver call is stubbed by monkeypatching
``core.cluster_management.deploy_agent_dispatch`` (imported lazily inside
the resolver) so these tests don't need a real apiserver — the
unit-level manifest rendering + the driver's apply path are covered by
``build_agent_manifests`` / provider driver tests respectively.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import core.cluster_management as cluster_management
from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import ClustersMutation, DeployClusterAgentInput
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.cluster_management import ClusterManagementError
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from providers._sdk.cluster import ApplyError, ApplyResult

pytestmark = pytest.mark.django_db

_AGENT_KEY_HASH = "a" * 64


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Suppress the User -> Profile -> OpenSearch indexing chain that
    fires on every Organization create."""
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
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def plugin():
    [p] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="k8s",
                slug="k8s_native",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return p


@pytest.fixture
def cluster(org, plugin):
    """A cluster with an agent key already issued — the precondition
    deployClusterAgent enforces. Tests that exercise the missing-key
    branch clear ``agent_key_hash`` explicitly."""
    return TenantCluster.objects.create(
        organization=org,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="dev",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        agent_key_hash=_AGENT_KEY_HASH,
        last_management_error="stale error from a prior op",
    )


def _info():
    """Resolver-shaped Info. Anonymous user — the permission resolver
    fixture grants the permission directly."""
    request = SimpleNamespace(user=None)
    return SimpleNamespace(context=SimpleNamespace(request=request, user=None))


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


def _ok_apply_result():
    return ApplyResult(
        created=["Namespace/astrolift-system", "Deployment/astrolift-agent"],
        updated=[],
        unchanged=[],
        errors=[],
    )


def _failed_apply_result():
    return ApplyResult(
        created=[],
        updated=[],
        unchanged=[],
        errors=[
            ApplyError(
                kind="Deployment",
                name="astrolift-agent",
                namespace="astrolift-system",
                exception_type="ApiException",
                exception_message="forbidden: namespace is terminating",
                is_retryable=False,
            )
        ],
    )


def test_deploy_denied_without_cluster_manage_permission(cluster, org, permission_resolver):
    # Resolver default = deny; don't grant.
    with _ctx(org):
        result = ClustersMutation().deploy_cluster_agent(
            _info(),
            DeployClusterAgentInput(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PERMISSION_DENIED"


def test_deploy_requires_issued_agent_key(cluster, org, permission_resolver, monkeypatch):
    """No key issued -> PRECONDITION, and the driver is never touched
    (the agent Secret can't exist without a key)."""
    cluster.agent_key_hash = ""
    cluster.save(update_fields=["agent_key_hash"])
    permission_resolver.grant(Permission.CLUSTER_MANAGE)

    called = False

    def _boom(*, cluster):
        nonlocal called
        called = True
        raise AssertionError("dispatch must not run when no key is issued")

    monkeypatch.setattr(cluster_management, "deploy_agent_dispatch", _boom)

    with _ctx(org):
        result = ClustersMutation().deploy_cluster_agent(
            _info(),
            DeployClusterAgentInput(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PRECONDITION"
    assert called is False


def test_deploy_rejects_inactive_cluster(cluster, org, permission_resolver, monkeypatch):
    cluster.is_active = False
    cluster.save(update_fields=["is_active"])
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    monkeypatch.setattr(
        cluster_management,
        "deploy_agent_dispatch",
        lambda *, cluster: pytest.fail("dispatch must not run for an inactive cluster"),
    )

    with _ctx(org):
        result = ClustersMutation().deploy_cluster_agent(
            _info(),
            DeployClusterAgentInput(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "PRECONDITION"


def test_deploy_returns_not_found_on_unknown_guid(org, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(org):
        result = ClustersMutation().deploy_cluster_agent(
            _info(),
            DeployClusterAgentInput(cluster_id=GUID(str(uuid.uuid4()))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"


def test_deploy_out_of_scope_cluster_reads_as_not_found(cluster, plugin, permission_resolver):
    """A cluster owned by another org is invisible — same NOT_FOUND as a
    guid that doesn't exist (no cross-tenant agent deploy)."""
    other = Organization.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    with _ctx(other):
        result = ClustersMutation().deploy_cluster_agent(
            _info(),
            DeployClusterAgentInput(cluster_id=GUID(str(cluster.guid))),
        )
    assert result.ok is False
    assert result.errors and result.errors[0].code == "NOT_FOUND"


def test_deploy_applies_manifests_and_clears_error(cluster, org, permission_resolver, monkeypatch):
    """Happy path: the dispatch is called with the resolved cluster, the
    stale management error is cleared, and the returned type reflects the
    provisioned agent."""
    permission_resolver.grant(Permission.CLUSTER_MANAGE)

    seen: dict = {}

    def _dispatch(*, cluster):
        seen["slug"] = cluster.slug
        return _ok_apply_result()

    monkeypatch.setattr(cluster_management, "deploy_agent_dispatch", _dispatch)

    with _ctx(org):
        result = ClustersMutation().deploy_cluster_agent(
            _info(),
            DeployClusterAgentInput(cluster_id=GUID(str(cluster.guid))),
        )

    assert result.ok is True, result.errors
    assert seen["slug"] == cluster.slug
    assert result.data.agent_provisioned is True
    assert result.data.last_management_error == ""
    cluster.refresh_from_db()
    assert cluster.last_management_error == ""


def test_deploy_persists_driver_build_failure(cluster, org, permission_resolver, monkeypatch):
    """A ClusterManagementError (driver can't be built / no
    apply_manifests) is persisted to last_management_error and surfaced
    as INTERNAL."""
    permission_resolver.grant(Permission.CLUSTER_MANAGE)

    def _dispatch(*, cluster):
        raise ClusterManagementError("driver does not implement apply_manifests")

    monkeypatch.setattr(cluster_management, "deploy_agent_dispatch", _dispatch)

    with _ctx(org):
        result = ClustersMutation().deploy_cluster_agent(
            _info(),
            DeployClusterAgentInput(cluster_id=GUID(str(cluster.guid))),
        )

    assert result.ok is False
    assert result.errors and result.errors[0].code == "INTERNAL"
    cluster.refresh_from_db()
    assert "apply_manifests" in cluster.last_management_error


def test_deploy_persists_apply_errors(cluster, org, permission_resolver, monkeypatch):
    """A non-ok ApplyResult (per-manifest failure) is persisted to
    last_management_error and surfaced as INTERNAL — the cluster is NOT
    reported as a clean deploy."""
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    monkeypatch.setattr(
        cluster_management,
        "deploy_agent_dispatch",
        lambda *, cluster: _failed_apply_result(),
    )

    with _ctx(org):
        result = ClustersMutation().deploy_cluster_agent(
            _info(),
            DeployClusterAgentInput(cluster_id=GUID(str(cluster.guid))),
        )

    assert result.ok is False
    assert result.errors and result.errors[0].code == "INTERNAL"
    cluster.refresh_from_db()
    assert "namespace is terminating" in cluster.last_management_error


# ---- build_agent_manifests: secrets RBAC for the test-prompt relay (#2064) --


def test_agent_cluster_role_has_no_cluster_wide_secrets_access(cluster):
    """The agent's per-model Secret read for the ``testModelEndpoint``
    relay (#2064) must NOT be a cluster-wide grant -- that would let the
    agent (and so a compromised control plane naming an arbitrary Secret)
    read any Secret in any tenant namespace. The narrow grant is a
    namespaced Role + RoleBinding the vLLM driver renders per service,
    restricted by ``resourceNames`` to that one Secret -- see
    ``test_model_endpoint_vllm.py`` in providers."""
    manifests = cluster_management.build_agent_manifests(cluster)
    role = next(m for m in manifests if m["kind"] == "ClusterRole")
    assert not any(r["resources"] == ["secrets"] for r in role["rules"])
