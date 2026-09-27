"""``oidc_auth_config.client_secret`` goes in and never comes back out (#2055).

The install writes the auth host's credentials Secret from the cluster row,
so the row now carries the OIDC client secret beside the cookie secret. Every
path that reads the row back is audited here: the GraphQL cluster type, the
bootstrap plan, the mutation audit log, the GitOps write-back, and the
registration command that rebuilds the config on every container start.
"""

from __future__ import annotations

import json
import uuid
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.management import call_command
from graphql import parse

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_clusters.schema.mutations import ClustersMutation, UpdateTenantClusterInput
from astrolift_clusters.schema.types import bootstrap_plan_to_type, cluster_to_type, redact_oidc_auth_config
from astrolift_clusters.services.toml_writeback import oidc_auth_section_from_db
from astrolift_graphql import GUID
from astrolift_identity.models import Organization
from core.permissions import Permission
from core.schema.audit import MutationAuditLog, redact_operation_variables
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

CLIENT_SECRET = "client-secret-2055-read-path"
COOKIE_SECRET = "cookie-secret-2055-read-path-32b"
OIDC = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration",
    "client_id": "central-client-id",
    "auth_proxy_host": "auth.apps.example.net",
    "cookie_secret": COOKIE_SECRET,
    "client_secret": CLIENT_SECRET,
}


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))


@pytest.fixture
def cluster():
    org = Organization.objects.create(name="Edge", slug=f"edge-{uuid.uuid4().hex[:8]}")
    plugin = ProviderPlugin.objects.create(
        name="aws", slug=f"aws-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
    )
    return TenantCluster.objects.create(
        organization=org,
        slug=f"edge-{uuid.uuid4().hex[:8]}",
        name="edge",
        provider_plugin=plugin,
        provider_config={},
        region="us-west-2",
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        is_active=True,
        ingress_class="alb",
    )


def _update(cluster, **kwargs):
    info = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=None), user=None))
    with tenant_context(TenantContext(organization_id=cluster.organization_id)):
        return ClustersMutation().update_tenant_cluster(
            info, UpdateTenantClusterInput(id=GUID(str(cluster.guid)), **kwargs)
        )


# ---- the mutation takes it; the read side reports only that it is set ----


def test_update_stores_the_client_secret_and_returns_only_that_it_is_set(cluster, permission_resolver):
    permission_resolver.grant(Permission.CLUSTER_UPDATE)

    result = _update(cluster, oidc_auth_config=OIDC)

    assert result.ok is True, result.errors
    cluster.refresh_from_db()
    assert cluster.oidc_auth_config["client_secret"] == CLIENT_SECRET
    view = result.data.oidc_auth_config
    assert view["client_secret_set"] is True
    assert view["cookie_secret_set"] is True
    assert "client_secret" not in view
    for secret in (CLIENT_SECRET, COOKIE_SECRET):
        assert secret not in repr(result.data)


def test_the_cluster_type_never_returns_the_client_secret(cluster):
    cluster.oidc_auth_config = OIDC
    cluster.save()
    rendered = repr(cluster_to_type(cluster))
    assert CLIENT_SECRET not in rendered
    assert COOKIE_SECRET not in rendered


def test_a_config_without_a_client_secret_reports_it_unset():
    view = redact_oidc_auth_config({k: v for k, v in OIDC.items() if k != "client_secret"})
    assert view["client_secret_set"] is False


def test_the_acme_contact_reads_back():
    """Not a credential; the operator checks what the issuer will use."""
    view = redact_oidc_auth_config({**OIDC, "acme_email": "ops@example.net"})
    assert view["acme_email"] == "ops@example.net"


def test_a_malformed_acme_contact_is_refused_and_not_saved(cluster, permission_resolver):
    """A contact Let's Encrypt rejects fails the account, and every
    certificate on the edge with it."""
    permission_resolver.grant(Permission.CLUSTER_UPDATE)

    result = _update(cluster, oidc_auth_config={**OIDC, "acme_email": "ops at example"})

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "oidcAuthConfig"
    assert "ops at example" not in result.errors[0].message
    cluster.refresh_from_db()
    assert cluster.oidc_auth_config is None


# ---- the bootstrap plan ---------------------------------------------


def test_the_bootstrap_plan_never_serves_the_secrets(cluster):
    """The plan is what an operator reads before installing. The EKS recipe
    for a configured cluster, converted exactly as the query converts it."""
    from _sdk.cluster import ClusterContext
    from aws.cluster_eks import EKSClusterDriver, EKSConfig

    cluster.oidc_auth_config = OIDC
    cluster.save()
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    eks = MagicMock()
    eks.describe_cluster.return_value = {"cluster": {"resourcesVpcConfig": {"vpcId": "vpc-1"}}}
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="edge"),
        eks_client=eks,
        sts_client=sts,
        ec2_client=ec2,
        k8s_client_factory=lambda **kw: MagicMock(),
    )
    components = driver.bootstrap_components(
        ClusterContext(
            slug=cluster.slug, auth_method="exec_plugin", oidc_auth_config=cluster.oidc_auth_config
        )
    )
    assert {"ingress-nginx", "oauth2-proxy"} <= {c.key for c in components}

    plan = bootstrap_plan_to_type(cluster, components)
    rendered = repr(plan) + json.dumps([c.helm_values for c in plan.components], default=str)
    assert CLIENT_SECRET not in rendered
    assert COOKIE_SECRET not in rendered


# ---- the mutation audit log -----------------------------------------


def _graphql_schema():
    from config.schema import schema

    return schema._schema


@pytest.mark.parametrize(
    ("query", "variables"),
    [
        (
            "mutation($in: UpdateTenantClusterInput!) { updateTenantCluster(input: $in) { ok } }",
            {"in": {"id": "00000000-0000-0000-0000-000000000001", "oidcAuthConfig": OIDC}},
        ),
        (
            "mutation($c: JSON) { updateTenantCluster(input: "
            '{id: "00000000-0000-0000-0000-000000000001", oidcAuthConfig: $c}) { ok } }',
            {"c": OIDC},
        ),
        (
            "mutation($s: String!) { updateTenantCluster(input: "
            '{id: "00000000-0000-0000-0000-000000000001", oidcAuthConfig: {client_secret: $s}}) { ok } }',
            {"s": CLIENT_SECRET},
        ),
    ],
    ids=["input-object", "json-variable", "variable-inside-a-json-literal"],
)
def test_the_audit_log_masks_the_client_secret(query, variables):
    redacted, sensitive = redact_operation_variables(
        variables, document=parse(query), schema=_graphql_schema()
    )
    assert CLIENT_SECRET not in json.dumps(redacted)
    assert COOKIE_SECRET not in json.dumps(redacted)
    assert sensitive is True


def test_the_stored_audit_row_masks_the_client_secret():
    from config.schema import schema

    schema.execute_sync(
        "mutation($in: UpdateTenantClusterInput!) { updateTenantCluster(input: $in) { ok } }",
        variable_values={"in": {"id": "00000000-0000-0000-0000-000000000001", "oidcAuthConfig": OIDC}},
        context_value=SimpleNamespace(user=None, request=None),
    )
    row = MutationAuditLog.objects.order_by("-pk").first()
    assert row is not None
    stored = json.dumps(row.variables) + json.dumps(row.errors)
    assert CLIENT_SECRET not in stored
    assert COOKIE_SECRET not in stored
    # The row is the one this test wrote, and it kept what is not secret.
    assert OIDC["client_id"] in stored


# ---- GitOps write-back ----------------------------------------------


def test_the_write_back_never_commits_the_client_secret():
    """``[ingress.auth]`` is committed to the app's own repository."""
    section = oidc_auth_section_from_db(OIDC)
    assert section is not None
    assert CLIENT_SECRET not in repr(section)
    assert COOKIE_SECRET not in repr(section)


# ---- the registration command ---------------------------------------


REGISTER_ENV = {
    "ASTROLIFT_CLUSTER_OIDC_DISCOVERY_URL": OIDC["discovery_url"],
    "ASTROLIFT_CLUSTER_OIDC_CLIENT_ID": OIDC["client_id"],
    "ASTROLIFT_CLUSTER_OIDC_COOKIE_SECRET": COOKIE_SECRET,
    "ASTROLIFT_CLUSTER_OIDC_AUTH_PROXY_HOST": OIDC["auth_proxy_host"],
}


@pytest.fixture
def aws_plugin():
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}}
    )
    return plugin


def _register(slug, monkeypatch, **extra) -> str:
    monkeypatch.delenv("ASTROLIFT_CLUSTER_OIDC_CLIENT_SECRET", raising=False)
    for key, value in {**REGISTER_ENV, **extra}.items():
        monkeypatch.setenv(key, value)
    out = StringIO()
    call_command("register_tenant_cluster", slug=slug, plugin_slug="aws", stdout=out)
    return out.getvalue()


def test_a_restart_keeps_the_client_secret_and_the_acme_contact(aws_plugin, monkeypatch):
    """The command runs on every container start and rebuilds the config
    from the environment. Losing the client secret there leaves the next
    install unable to write the auth host's Secret."""
    slug = f"reg-{uuid.uuid4().hex[:6]}"
    _register(slug, monkeypatch)
    row = TenantCluster.all_objects.get(slug=slug)
    row.oidc_auth_config = {
        **row.oidc_auth_config,
        "client_secret": CLIENT_SECRET,
        "acme_email": "ops@example.net",
    }
    row.save(update_fields=["oidc_auth_config"])

    output = _register(slug, monkeypatch)

    row.refresh_from_db()
    assert row.oidc_auth_config["client_secret"] == CLIENT_SECRET
    assert row.oidc_auth_config["acme_email"] == "ops@example.net"
    assert CLIENT_SECRET not in output
    assert COOKIE_SECRET not in output


def test_the_environment_can_declare_the_client_secret(aws_plugin, monkeypatch):
    slug = f"reg-{uuid.uuid4().hex[:6]}"
    _register(slug, monkeypatch, ASTROLIFT_CLUSTER_OIDC_CLIENT_SECRET="declared-client-secret-2055")
    row = TenantCluster.all_objects.get(slug=slug)
    assert row.oidc_auth_config["client_secret"] == "declared-client-secret-2055"


def test_no_client_secret_anywhere_leaves_the_key_absent(aws_plugin, monkeypatch):
    """Absent, not blank: the install reads a blank as missing anyway, and
    the read view reports it unset."""
    slug = f"reg-{uuid.uuid4().hex[:6]}"
    _register(slug, monkeypatch)
    row = TenantCluster.all_objects.get(slug=slug)
    assert "client_secret" not in row.oidc_auth_config
