"""Resource authorization preserves reviewed variants and fails closed without prices."""

import pytest

from _cert import resource_policy as policy
from _sdk.availability import MATRIX


@pytest.mark.parametrize("provider", ["aws", "gcp", "azure"])
def test_every_cloud_preview_has_an_explicit_resource_review(provider):
    for row in MATRIX.managed_services:
        if row.plugin_id == provider and row.status == "preview":
            assert (row.kind, row.variant) in policy.REVIEWED_VARIANTS[provider]
            policy.classify(provider, row.kind, row.variant)


@pytest.mark.parametrize("provider", ["aws", "gcp", "azure"])
def test_reviewed_resources_still_exist_in_the_availability_matrix(provider):
    known = {(row.kind, row.variant) for row in MATRIX.managed_services if row.plugin_id == provider}
    assert policy.REVIEWED_VARIANTS[provider] <= known
    assert policy.CAPACITY_VARIANTS[provider] <= policy.REVIEWED_VARIANTS[provider]
    assert policy.UNSUPPORTED_VARIANTS[provider] <= policy.REVIEWED_VARIANTS[provider]


@pytest.mark.parametrize("provider,kind,variant", [("aws", "quantum_db", "new"), ("oracle", "topic", "streams")])
def test_unknown_variants_are_not_assumed_safe_even_with_capacity_authorization(provider, kind, variant):
    with pytest.raises(policy.UnreviewedVariant):
        policy.check_may_run(provider, kind, variant, capacity_resources_authorized=True)


def test_unsupported_lifecycle_stays_refused_even_with_capacity_authorization():
    with pytest.raises(policy.NotCertifiable, match="unsupported certification lifecycle"):
        policy.check_may_run("aws", "mssql", "rds_sqlserver_enterprise", capacity_resources_authorized=True)


@pytest.mark.parametrize(
    "kind,variant",
    [
        ("postgres", "aurora_postgres_serverless_v2"),
        ("search", "opensearch_serverless"),
        ("redis", "elasticache_serverless_redis"),
        ("warehouse", "redshift_serverless"),
    ],
)
def test_reviewed_capacity_requires_operator_authorization_without_estimate(kind, variant):
    with pytest.raises(policy.CapacityReviewRequired, match="--allow-capacity-resources"):
        policy.check_may_run("aws", kind, variant)
    result = policy.check_may_run("aws", kind, variant, capacity_resources_authorized=True)
    assert result.supported and result.capacity_review_required


def test_previously_reviewed_noncapacity_selection_is_not_expanded():
    assert (
        sum(
            policy.classify("aws", *key).supported and not policy.classify("aws", *key).capacity_review_required
            for key in policy.REVIEWED_VARIANTS["aws"]
        )
        == 15
    )
