"""A cluster cannot name one AWS account and be driven in another (#1422).

`account_id` is `required` in the AWS plugin's `config_schema` and sits on
every AWS cluster row. Nothing verified it. Every use of it in
`providers/aws/` interpolates it into an ARN string — `identity_irsa`,
`registry_ecr`, `api_gateway_rest`, `redshift` — so a row declaring account
B while the process authenticates into account A provisions into A and
hands back ARNs naming B. Every IRSA trust policy and cross-service grant
built from those is wrong, and it surfaces much later as a permissions
failure with no obvious cause.

This is the check, at the point a cluster is first brought into
management. It is deliberately not per-operation: that would be a
`GetCallerIdentity` on every driver call, and the answer does not change
between them.
"""

from __future__ import annotations

import pytest

from core.cluster_credentials import (
    ClusterAccountMismatch,
    assert_declared_account,
)


class _Plugin:
    def __init__(self, slug: str) -> None:
        self.slug = slug


class _Cluster:
    """Enough of a TenantCluster for the credential funnel."""

    def __init__(self, *, cloud: str = "aws", account: str | None = "111111111111"):
        self.slug = "prod"
        self.provider_plugin = _Plugin(cloud)
        self.provider_plugin_id = cloud
        self.provider_config = {"region": "us-west-2"}
        if account is not None:
            self.provider_config["account_id"] = account
        self.auth_config = {}


def test_a_matching_account_passes(monkeypatch):
    import aws.session as session

    monkeypatch.setattr(session, "caller_account", lambda *a, **k: "111111111111")

    # Returns None; what matters is that it does not raise.
    assert_declared_account(_Cluster(account="111111111111"))


def test_a_mismatch_is_refused_before_anything_is_provisioned(monkeypatch):
    """The whole point. The message has to name both accounts, because the
    fix is to correct one of them and the operator has to know which."""
    import aws.session as session

    monkeypatch.setattr(session, "caller_account", lambda *a, **k: "999999999999")

    with pytest.raises(ClusterAccountMismatch) as exc:
        assert_declared_account(_Cluster(account="111111111111"))

    message = str(exc.value)
    assert "111111111111" in message
    assert "999999999999" in message
    assert "prod" in message


def test_an_undeclared_account_is_unverifiable_not_wrong(monkeypatch):
    # Most rows predate the field being load-bearing. Refusing them would
    # break every existing cluster to catch a mistake they have not made.
    import aws.session as session

    called = []
    monkeypatch.setattr(session, "caller_account", lambda *a, **k: called.append(1) or "x")

    assert_declared_account(_Cluster(account=None))
    assert called == [], "an undeclared account should cost no STS call"


@pytest.mark.parametrize("cloud", ["gcp", "azure", "k8s_native"])
def test_only_aws_is_checked(cloud, monkeypatch):
    """GCP puts `project_id` and Azure `subscription_id` into the request
    itself, so a row naming the wrong one fails at the call. AWS has
    nowhere in a request to put an account — it is a property of the
    credential alone, which is why it is the one cloud where the row and
    the reality can disagree in silence."""
    import aws.session as session

    called = []
    monkeypatch.setattr(session, "caller_account", lambda *a, **k: called.append(1) or "x")

    assert_declared_account(_Cluster(cloud=cloud))
    assert called == []


@pytest.mark.django_db
def test_bring_into_management_runs_the_check_before_the_probe(monkeypatch):
    """Placement matters as much as the check.

    Before the probe, so a cluster naming the wrong account is refused
    while nothing has been applied into it yet.
    """
    from astrolift_workflows.activities import cluster_management as mod

    order: list[str] = []

    import core.cluster_credentials as creds

    monkeypatch.setattr(creds, "assert_declared_account", lambda cluster: order.append("account"))
    monkeypatch.setattr(
        "core.cluster_management.probe_cluster_capabilities_dispatch",
        lambda **kw: order.append("probe"),
    )

    from astrolift_clusters.models import ProviderPlugin, TenantCluster
    from astrolift_identity.models import Organization

    org = Organization.objects.create(name="Acme", slug="acme-acct")
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="AWS",
                slug="aws",
                plugin_version="1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="prod-acct",
        name="Prod",
        provider_plugin=ProviderPlugin.objects.get(slug="aws"),
        endpoint="https://eks.example.com",
    )

    mod._verify_reachability_sync(cluster.pk)

    assert order == ["account", "probe"]
