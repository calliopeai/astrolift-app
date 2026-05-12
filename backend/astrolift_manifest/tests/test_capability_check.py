"""Tests for capability pre-check + promotion validation (#59)."""

from __future__ import annotations

from astrolift_manifest.capability_check import (
    ClusterCapabilities,
    ManifestRequirement,
    ResolvedVariantSnapshot,
    bind_precheck,
    promotion_check,
)


def _aws_cluster(**kw) -> ClusterCapabilities:
    base = {
        "cluster_id": 1,
        "cluster_slug": "acme-prod",
        "cloud_provider": "aws",
        "supported_kinds": frozenset({"postgres", "redis", "queue", "ingress"}),
        "supported_pins": frozenset(
            {
                "aws-rds/aurora-15",
                "aws-rds/postgres-15",
                "aws-elasticache/redis-7",
                "aws-sqs/standard",
                "ingress-nginx/v1",
            }
        ),
    }
    base.update(kw)
    return ClusterCapabilities(**base)


def _gcp_cluster() -> ClusterCapabilities:
    return ClusterCapabilities(
        cluster_id=2,
        cluster_slug="acme-eu",
        cloud_provider="gcp",
        supported_kinds=frozenset({"postgres", "redis", "ingress"}),
        supported_pins=frozenset(
            {
                "gcp-cloudsql/postgres-15",
                "gcp-memorystore/redis-7",
                "ingress-nginx/v1",
            }
        ),
    )


# ---- bind pre-check --------------------------------------------------


def test_bind_passes_when_all_kinds_supported_no_pins():
    report = bind_precheck(
        requirements=[
            ManifestRequirement(kind="postgres", name="main"),
            ManifestRequirement(kind="redis", name="cache"),
        ],
        cluster=_aws_cluster(),
    )
    assert report.ok is True


def test_bind_passes_with_supported_pins():
    report = bind_precheck(
        requirements=[
            ManifestRequirement(kind="postgres", name="main", variant_pin="aws-rds/aurora-15"),
        ],
        cluster=_aws_cluster(),
    )
    assert report.ok is True


def test_bind_rejects_unsupported_kind():
    report = bind_precheck(
        requirements=[ManifestRequirement(kind="kafka", name="events")],
        cluster=_aws_cluster(),
    )
    assert report.ok is False
    assert report.issues[0].code == "unsupported_kind"
    assert "kafka" in report.issues[0].detail
    assert "acme-prod" in report.issues[0].detail


def test_bind_rejects_unsupported_pin():
    report = bind_precheck(
        requirements=[
            ManifestRequirement(kind="postgres", name="main", variant_pin="gcp-cloudsql/postgres-15"),
        ],
        cluster=_aws_cluster(),
    )
    assert report.ok is False
    assert report.issues[0].code == "unsupported_pin"
    assert "gcp-cloudsql/postgres-15" in report.issues[0].detail


def test_bind_collects_every_blocker_at_once():
    """Operator sees all bugs in one error."""
    report = bind_precheck(
        requirements=[
            ManifestRequirement(kind="kafka", name="events"),
            ManifestRequirement(
                kind="postgres",
                name="main",
                variant_pin="gcp-cloudsql/postgres-15",
            ),
            ManifestRequirement(kind="redis", name="cache"),  # OK
        ],
        cluster=_aws_cluster(),
    )
    codes = {i.code for i in report.issues}
    assert codes == {"unsupported_kind", "unsupported_pin"}
    assert len(report.issues) == 2


# ---- promotion validation -------------------------------------------


def test_promotion_within_same_cloud_passes():
    """Same-cloud promotion of pinned variants is allowed."""
    report = promotion_check(
        source_resolutions=[
            ResolvedVariantSnapshot(kind="postgres", name="main", pin="aws-rds/aurora-15"),
        ],
        target_cluster=_aws_cluster(cluster_slug="acme-prod-2"),
        source_cloud_provider="aws",
    )
    assert report.ok is True


def test_promotion_rejects_missing_variant_on_target():
    target = _aws_cluster(
        supported_pins=frozenset({"aws-rds/postgres-15"})  # no aurora-15
    )
    report = promotion_check(
        source_resolutions=[
            ResolvedVariantSnapshot(kind="postgres", name="main", pin="aws-rds/aurora-15"),
        ],
        target_cluster=target,
        source_cloud_provider="aws",
    )
    codes = {i.code for i in report.issues}
    assert "missing_variant_on_target" in codes


def test_promotion_cross_cloud_with_pinned_variant_rejected():
    """aws-pinned source can't promote to a gcp cluster — even
    if the kind is supported there, the pin won't match."""
    report = promotion_check(
        source_resolutions=[
            ResolvedVariantSnapshot(kind="postgres", name="main", pin="aws-rds/aurora-15"),
        ],
        target_cluster=_gcp_cluster(),
        source_cloud_provider="aws",
    )
    codes = {i.code for i in report.issues}
    # Both reasons surface — variant missing AND cross-cloud rule.
    # We report missing_variant_on_target first (cheaper signal).
    assert "missing_variant_on_target" in codes


def test_promotion_cross_cloud_with_portable_pin_accepted():
    """Portable plugins (no cloud prefix) cross clouds freely."""
    target = _gcp_cluster()
    target_w_portable = ClusterCapabilities(
        cluster_id=target.cluster_id,
        cluster_slug=target.cluster_slug,
        cloud_provider=target.cloud_provider,
        supported_kinds=target.supported_kinds,
        supported_pins=target.supported_pins | {"postgres-portable/15"},
    )
    report = promotion_check(
        source_resolutions=[
            ResolvedVariantSnapshot(kind="postgres", name="main", pin="postgres-portable/15"),
        ],
        target_cluster=target_w_portable,
        source_cloud_provider="aws",
    )
    assert report.ok is True


def test_promotion_cross_cloud_pinned_message_actionable():
    """Error guides operator to declare portable mode."""
    # Trick: target has the pin so we hit the cross-cloud check
    aws_pin = "aws-rds/aurora-15"
    target = ClusterCapabilities(
        cluster_id=2,
        cluster_slug="acme-eu",
        cloud_provider="gcp",
        supported_kinds=frozenset({"postgres"}),
        supported_pins=frozenset({aws_pin}),  # contrived but isolates the rule
    )
    report = promotion_check(
        source_resolutions=[
            ResolvedVariantSnapshot(kind="postgres", name="main", pin=aws_pin),
        ],
        target_cluster=target,
        source_cloud_provider="aws",
    )
    codes = {i.code for i in report.issues}
    assert "cross_cloud_pinned_variant" in codes
    msg = next(i.detail for i in report.issues if i.code == "cross_cloud_pinned_variant")
    assert "portable" in msg


def test_promotion_no_cloud_provider_skips_cross_cloud_rule():
    """When provider info is missing on either side, we don't apply
    the cross-cloud rule — only the variant-presence check fires."""
    target = _aws_cluster()
    report = promotion_check(
        source_resolutions=[
            ResolvedVariantSnapshot(kind="postgres", name="main", pin="aws-rds/aurora-15"),
        ],
        target_cluster=target,
        source_cloud_provider="",  # unknown
    )
    assert report.ok is True


def test_promotion_collects_blockers():
    """Multiple problems → multiple issues in the report."""
    target = _gcp_cluster()
    report = promotion_check(
        source_resolutions=[
            ResolvedVariantSnapshot(kind="postgres", name="main", pin="aws-rds/aurora-15"),
            ResolvedVariantSnapshot(kind="redis", name="cache", pin="aws-elasticache/redis-7"),
        ],
        target_cluster=target,
        source_cloud_provider="aws",
    )
    assert len(report.issues) == 2
