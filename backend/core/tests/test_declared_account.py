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
    record_verified_account,
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

    monkeypatch.setattr(creds, "record_verified_account", lambda cluster: order.append("account") or "")
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


# ---------------------------------------------------------------------------
# The cluster carries its own account (#1422)
# ---------------------------------------------------------------------------


class _SavableCluster(_Cluster):
    """A cluster that records what was written to it, without a database."""

    def __init__(self, **kw):
        verified = kw.pop("verified", "") or ""
        super().__init__(**kw)
        self.cloud_account_id = verified
        self.cloud_account_verified_at = None
        self.saved_fields: list[str] = []

    def save(self, update_fields=None, **_kw):
        self.saved_fields = list(update_fields or [])


def test_bringing_a_cluster_into_management_saves_the_account_it_is_in(monkeypatch):
    """Proved, not declared. The saved value is what ARNs are built from."""
    import aws.session as session

    monkeypatch.setattr(session, "caller_account", lambda *a, **k: "111111111111")
    cluster = _SavableCluster(account="111111111111")

    assert record_verified_account(cluster) == "111111111111"
    assert cluster.cloud_account_id == "111111111111"
    assert cluster.cloud_account_verified_at is not None
    assert "cloud_account_id" in cluster.saved_fields


def test_a_declared_account_that_disagrees_is_refused(monkeypatch):
    import aws.session as session

    monkeypatch.setattr(session, "caller_account", lambda *a, **k: "999999999999")
    cluster = _SavableCluster(account="111111111111")

    with pytest.raises(ClusterAccountMismatch):
        record_verified_account(cluster)

    assert cluster.cloud_account_id == "", "nothing should be recorded on a refusal"


def test_a_cluster_cannot_move_between_accounts(monkeypatch):
    """The invariant. Re-stamping would silently move every app bound to it.

    A row that was verified in one account and now answers with another is
    not a cluster being corrected — it is a different cluster wearing this
    row's name.
    """
    import aws.session as session

    monkeypatch.setattr(session, "caller_account", lambda *a, **k: "222222222222")
    cluster = _SavableCluster(account=None, verified="111111111111")

    with pytest.raises(ClusterAccountMismatch, match="cannot move between accounts"):
        record_verified_account(cluster)

    assert cluster.cloud_account_id == "111111111111", "the original must survive the refusal"


def test_re_verifying_the_same_account_does_not_rewrite_the_row(monkeypatch):
    import aws.session as session

    monkeypatch.setattr(session, "caller_account", lambda *a, **k: "111111111111")
    cluster = _SavableCluster(account=None, verified="111111111111")

    assert record_verified_account(cluster) == "111111111111"
    assert cluster.saved_fields == [], "an unchanged account is not a write"


@pytest.mark.parametrize("cloud", ["gcp", "azure", "k8s_native"])
def test_only_aws_records_an_account(cloud, monkeypatch):
    import aws.session as session

    called = []
    monkeypatch.setattr(session, "caller_account", lambda *a, **k: called.append(1) or "x")
    cluster = _SavableCluster(cloud=cloud)

    assert record_verified_account(cluster) == ""
    assert called == []
