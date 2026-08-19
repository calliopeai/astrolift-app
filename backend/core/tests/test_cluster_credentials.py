"""A declared cloud credential is honoured or refused, never ignored (#1422).

The point of the guard is that there is no third outcome. Drivers authenticate
ambiently at ~240 call sites and cannot all migrate at once, so the property
worth pinning is that an unmigrated path stops rather than quietly acting on
the control plane's own account.

Synthetic cluster rows (``SimpleNamespace``) rather than model instances,
matching ``test_k8s_managed_configs``: nothing here touches the database and
the guard reads four attributes.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.cluster_credentials import (
    ClusterCredentialInvalid,
    ClusterCredentialUnsupported,
    assert_credential_supported,
    credential_for_cluster,
)

ROLE = "arn:aws:iam::210987654321:role/astrolift-control-plane"


def _cluster(*, slug="prod-usw2", plugin="aws", provider_config=None, auth_config=None):
    return SimpleNamespace(
        slug=slug,
        provider_plugin=SimpleNamespace(slug=plugin),
        provider_config=provider_config if provider_config is not None else {"region": "us-west-2"},
        auth_config=auth_config or {},
    )


def test_a_cluster_without_a_declaration_is_ambient_everywhere():
    """Every cluster that exists today. The guard must be invisible to them or
    it is a regression rather than a safety net."""
    cluster = _cluster()

    assert credential_for_cluster(cluster).is_ambient
    for capability in ("cluster", "secrets", "dns", "managed:postgres", "log_query"):
        assert_credential_supported(cluster, capability=capability)


def test_an_unmigrated_capability_refuses_a_declared_credential():
    cluster = _cluster(
        provider_config={
            "region": "us-west-2",
            "credential": {"mode": "aws_assume_role", "role_arn": ROLE},
        }
    )

    with pytest.raises(ClusterCredentialUnsupported) as exc:
        assert_credential_supported(cluster, capability="managed:postgres")

    assert "managed:postgres" in str(exc.value)
    assert cluster.slug in str(exc.value)


def test_a_migrated_capability_accepts_a_declared_credential():
    """log_query is the one path that threads the credential to the client
    today; the set it is drawn from is what a migration PR grows."""
    cluster = _cluster(
        provider_config={
            "region": "us-west-2",
            "credential": {"mode": "aws_assume_role", "role_arn": ROLE},
        }
    )

    assert_credential_supported(cluster, capability="log_query")


def test_credential_material_on_the_row_is_refused_by_every_capability():
    """provider_config is a plaintext column. A migrated capability must not
    be a way in for a literal secret either."""
    cluster = _cluster(
        provider_config={
            "credential": {"mode": "aws_assume_role", "role_arn": ROLE, "aws_secret_access_key": "x"},
        }
    )

    for capability in ("log_query", "managed:postgres"):
        with pytest.raises(ClusterCredentialInvalid, match="aws_secret_access_key"):
            assert_credential_supported(cluster, capability=capability)


def test_a_malformed_declaration_fails_rather_than_degrading_to_ambient():
    """Degrading is the exact outcome the guard exists to prevent: it looks
    like success and acts on the wrong account."""
    cluster = _cluster(provider_config={"credential": {"mode": "assume-role-ish", "role_arn": ROLE}})

    with pytest.raises(ClusterCredentialInvalid):
        assert_credential_supported(cluster, capability="log_query")


def test_a_non_dict_provider_config_does_not_crash_the_guard():
    """Older rows and hand-edits leave nulls in JSONField columns, and the
    guard runs ahead of anything else that would have noticed."""
    cluster = SimpleNamespace(
        slug="legacy",
        provider_plugin=SimpleNamespace(slug="aws"),
        provider_config=None,
        auth_config=None,
    )

    assert credential_for_cluster(cluster).is_ambient


def test_the_declared_account_is_read_per_cloud():
    aws = _cluster(provider_config={"account_id": "123456789012"})
    gcp = _cluster(plugin="gcp", provider_config={"project_id": "calliopealpha"})
    azure = _cluster(plugin="azure", provider_config={"subscription_id": "sub-abc"})

    assert credential_for_cluster(aws).declared_account == "123456789012"
    assert credential_for_cluster(gcp).declared_account == "calliopealpha"
    assert credential_for_cluster(azure).declared_account == "sub-abc"
