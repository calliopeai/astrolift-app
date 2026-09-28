"""Managing the edge identity provider's users over GraphQL (#2131)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from _sdk.identity_users import IdentityUser

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.auth_users import (
    ClusterAuthUserRefInput,
    ClusterAuthUsersMutation,
    ClusterAuthUsersQuery,
    CreateClusterAuthUserInput,
)
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.schema.audit import redact_operation_variables
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

COGNITO = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_xU96Y7DAg/.well-known/openid-configuration"


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


class FakeDriver:
    provider = "Amazon Cognito"

    def __init__(self):
        self.created = []
        self.deleted = []

    def list_users(self, *, search="", limit=60):
        return [
            IdentityUser(
                username="u1", email="a@example.com", enabled=True, status="CONFIRMED", groups=("veruus",)
            )
        ]

    def list_groups(self):
        return ["veruus"]

    def create_user(self, *, email, password, permanent, groups=()):
        self.created.append((email, password, permanent, groups))
        return IdentityUser(
            username="u2", email=email, enabled=True, status="FORCE_CHANGE_PASSWORD", groups=groups
        )

    def delete_user(self, *, username):
        self.deleted.append(username)


@pytest.fixture
def driver(monkeypatch):
    fake = FakeDriver()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda cluster, capability: fake)
    return fake


def _cluster(*, shared=False, discovery=COGNITO):
    org = Organization.objects.create(name="Veruus", slug=f"v-{uuid.uuid4().hex[:8]}")
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}}
    )
    cluster = TenantCluster.objects.create(
        organization=None if shared else org,
        slug=f"c-{uuid.uuid4().hex[:8]}",
        name="c",
        provider_plugin=plugin,
        provider_config={},
        region="us-west-2",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        ingress_class="envoy",
        oidc_auth_config={"discovery_url": discovery, "client_id": "c", "auth_proxy_host": "auth.x"},
    )
    return cluster, org


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user), user=user))


def test_listing_returns_users_groups_and_what_a_user_can_reach(permission_resolver, driver):
    permission_resolver.grant(Permission.CLUSTER_USERS)
    cluster, org = _cluster()

    with tenant_context(TenantContext(organization_id=org.pk)):
        result = ClusterAuthUsersQuery().astrolift_cluster_auth_users(_info(), GUID(str(cluster.guid)))

    assert result.supported is True
    assert result.provider == "Amazon Cognito"
    assert [u.email for u in result.users] == ["a@example.com"]
    assert result.groups == ["veruus"]
    assert "every app" in result.reach_note


def test_an_external_provider_is_reported_unsupported_not_an_error(permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_USERS)
    cluster, org = _cluster(discovery="https://login.example.net/oidc")

    with tenant_context(TenantContext(organization_id=org.pk)):
        result = ClusterAuthUsersQuery().astrolift_cluster_auth_users(_info(), GUID(str(cluster.guid)))

    assert result.supported is False
    assert "Cognito" in result.reason


def test_creating_a_user_never_returns_the_password(permission_resolver, driver):
    permission_resolver.grant(Permission.CLUSTER_USERS)
    cluster, org = _cluster()

    with tenant_context(TenantContext(organization_id=org.pk)):
        result = ClusterAuthUsersMutation().create_cluster_auth_user(
            _info(),
            CreateClusterAuthUserInput(
                cluster_id=GUID(str(cluster.guid)),
                email="veruus-user@example.com",
                password="Pw-123456!",
                permanent=True,
                groups=["veruus"],
            ),
        )

    assert result.ok is True, result.errors
    assert driver.created == [("veruus-user@example.com", "Pw-123456!", True, ("veruus",))]
    assert "Pw-123456!" not in repr(result)


def test_a_bad_email_is_refused_before_the_provider(permission_resolver, driver):
    permission_resolver.grant(Permission.CLUSTER_USERS)
    cluster, org = _cluster()

    with tenant_context(TenantContext(organization_id=org.pk)):
        result = ClusterAuthUsersMutation().create_cluster_auth_user(
            _info(), CreateClusterAuthUserInput(cluster_id=GUID(str(cluster.guid)), email="not-an-email")
        )

    assert result.ok is False
    assert driver.created == []


def test_another_orgs_cluster_is_not_found(permission_resolver, driver):
    permission_resolver.grant(Permission.CLUSTER_USERS)
    cluster, _org = _cluster()
    other = Organization.objects.create(name="Other", slug=f"o-{uuid.uuid4().hex[:8]}")

    with tenant_context(TenantContext(organization_id=other.pk)):
        result = ClusterAuthUsersMutation().delete_cluster_auth_user(
            _info(), ClusterAuthUserRefInput(cluster_id=GUID(str(cluster.guid)), username="u1")
        )

    assert result.ok is False
    assert driver.deleted == []


def test_a_shared_clusters_pool_is_the_platform_operators_to_read(permission_resolver, driver):
    """Every org's logins are in it."""
    from core.permissions import PermissionDenied

    permission_resolver.grant(Permission.CLUSTER_USERS)
    cluster, org = _cluster(shared=True)
    tenant_user = SimpleNamespace(is_authenticated=True, is_superuser=False, is_staff=False, pk=1)

    with tenant_context(TenantContext(organization_id=org.pk)), pytest.raises((PermissionDenied, Exception)):
        ClusterAuthUsersQuery().astrolift_cluster_auth_users(_info(tenant_user), GUID(str(cluster.guid)))


@pytest.mark.parametrize(
    ("query", "variables"),
    [
        (
            "mutation($in: CreateClusterAuthUserInput!) { createClusterAuthUser(input: $in) { ok } }",
            {
                "in": {
                    "clusterId": "00000000-0000-0000-0000-000000000001",
                    "email": "a@example.com",
                    "password": "Pw-123456!",
                }
            },
        ),
        (
            "mutation($p: String!) { setClusterAuthUserPassword(input: "
            '{clusterId: "00000000-0000-0000-0000-000000000001", username: "u", password: $p}) { ok } }',
            {"p": "Pw-123456!"},
        ),
    ],
    ids=["create", "set-password"],
)
def test_the_audit_log_masks_the_password(query, variables):
    import json

    from graphql import parse

    from config.schema import schema

    redacted, sensitive = redact_operation_variables(variables, document=parse(query), schema=schema._schema)

    assert "Pw-123456!" not in json.dumps(redacted)
    assert sensitive is True
