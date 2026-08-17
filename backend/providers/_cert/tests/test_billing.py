"""Tests for the certification spend gate (#1417 decision 2).

This gate is the only thing standing between "run the free tranche" and a
surprise bill, so the cases that matter are the refusals.
"""

from __future__ import annotations

import pytest
from providers._cert import billing
from providers._sdk.availability import MATRIX


def _aws_previews() -> list[tuple[str, str]]:
    return [
        (e.kind, e.variant) for e in MATRIX.managed_services if e.status == "preview" and e.plugin_id.startswith("aws")
    ]


# ---- coverage against the matrix --------------------------------------------


def test_every_aws_preview_has_a_billing_class():
    """The campaign's whole input list. An entry added to the matrix without a
    classification would otherwise be invisible until someone ran it."""
    unclassified = []
    for kind, variant in _aws_previews():
        try:
            billing.classify("aws", kind, variant)
        except billing.UnclassifiedVariant:
            unclassified.append(f"{kind}:{variant}")

    assert not unclassified, (
        "AWS preview variants with no billing class: "
        + ", ".join(sorted(unclassified))
        + ". Add them to providers/_cert/billing.py."
    )


def test_the_classification_table_has_no_entries_the_matrix_lost():
    """A variant renamed or removed in the matrix leaves a dead row here, and a
    dead row is indistinguishable from a classified one when reading the table."""
    known = set(_aws_previews())
    ga_and_other = {(e.kind, e.variant) for e in MATRIX.managed_services if e.plugin_id.startswith("aws")}
    stale = [
        f"{kind}:{variant}"
        for (kind, variant) in billing.CLASSIFICATIONS["aws"]
        if (kind, variant) not in known and (kind, variant) not in ga_and_other
    ]

    assert not stale, "classified variants that no longer exist in the matrix: " + ", ".join(sorted(stale))


# ---- the refusals ------------------------------------------------------------


def test_an_unclassified_variant_is_refused_not_assumed_free():
    """The failure mode this exists for: a new entry swept into a class A batch."""
    with pytest.raises(billing.UnclassifiedVariant, match="not assumed free"):
        billing.check_may_run("aws", "quantum_db", "brand_new")


def test_an_unknown_provider_is_refused():
    with pytest.raises(billing.UnclassifiedVariant, match="no billing classification table"):
        billing.check_may_run("gcp", "topic", "pubsub")


def test_class_d_is_refused_even_with_an_estimate():
    """Decision 4 is not a spend gate, it is a decision not to certify. An
    estimate does not buy past it."""
    with pytest.raises(billing.NotCertifiable, match="decision 4"):
        billing.check_may_run("aws", "mssql", "rds_sqlserver_enterprise", estimate_logged=True)


def test_class_c_is_refused_without_an_estimate():
    with pytest.raises(billing.EstimateRequired, match="pre-flight cost estimate"):
        billing.check_may_run("aws", "postgres", "aurora_postgres")


def test_class_c_runs_once_an_estimate_is_logged():
    classification = billing.check_may_run("aws", "postgres", "aurora_postgres", estimate_logged=True)

    assert classification.billing_class is billing.BillingClass.HOURLY_CAPACITY_FLOOR


@pytest.mark.parametrize(
    ("kind", "variant"),
    [("topic", "sns_standard"), ("event_bus", "eventbridge"), ("encryption_key", "kms")],
)
def test_classes_a_and_b_run_without_an_estimate(kind, variant):
    assert billing.check_may_run("aws", kind, variant).runs_freely


def test_the_refusal_says_where_estimates_come_from():
    """A hard-coded price table is the thing decision 2 rules out; the error is
    where someone will look for what to do instead."""
    with pytest.raises(billing.EstimateRequired, match="live pricing API"):
        billing.check_may_run("aws", "warehouse", "redshift")


# ---- the shape of the tranches -----------------------------------------------


def test_serverless_is_not_treated_as_free():
    """The trap in this catalogue. Aurora Serverless v2, OpenSearch Serverless
    and ElastiCache Serverless all bill a capacity floor while idle, and the
    word in the variant name suggests otherwise."""
    for kind, variant in [
        ("postgres", "aurora_postgres_serverless_v2"),
        ("search", "opensearch_serverless"),
        ("redis", "elasticache_serverless_redis"),
        ("warehouse", "redshift_serverless"),
    ]:
        assert not billing.classify("aws", kind, variant).runs_freely, f"{kind}:{variant}"


def test_the_free_tranche_is_the_expected_size():
    """Pins the batch an operator gets from "run everything that runs freely",
    so growing it is a deliberate edit rather than a side effect."""
    freely = [
        (kind, variant)
        for (kind, variant) in billing.CLASSIFICATIONS["aws"]
        if billing.classify("aws", kind, variant).runs_freely
    ]

    assert len(freely) == 15, sorted(f"{k}:{v}" for k, v in freely)
