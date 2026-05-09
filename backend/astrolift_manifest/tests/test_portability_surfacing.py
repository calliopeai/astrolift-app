"""Tests for portability surfacing policy (#65, spec 20 §11-12)."""

from __future__ import annotations

import pytest

from astrolift_manifest.portability_surfacing import (
    BadgeKind,
    BlockResolution,
    ClusterCompat,
    PortabilitySurfaceError,
    ResolutionSource,
    build_resolution_table,
    compute_badge,
    deployable_clusters,
    evaluate_promote,
    make_portable_suggestions,
)

# ---- badge ---------------------------------------------------------


def test_badge_all_portable():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin=""),
        BlockResolution(kind="redis", name="cache", pin=""),
    )
    badge = compute_badge(blocks=blocks)
    assert badge.kind == BadgeKind.PORTABLE
    assert badge.pinned_count == 0
    assert badge.portable_count == 2


def test_badge_all_pinned():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="aws/rds"),
        BlockResolution(kind="redis", name="cache", pin="aws/elasticache"),
    )
    badge = compute_badge(blocks=blocks)
    assert badge.kind == BadgeKind.PINNED
    assert badge.plugin_label == "aws"
    assert badge.pinned_count == 2


def test_badge_mixed():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="aws/rds"),
        BlockResolution(kind="redis", name="cache", pin=""),
    )
    badge = compute_badge(blocks=blocks)
    assert badge.kind == BadgeKind.MIXED
    assert badge.plugin_label == "aws"
    assert badge.pinned_count == 1
    assert badge.portable_count == 1


def test_badge_dominant_plugin_when_mixed_plugins():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="aws/rds"),
        BlockResolution(kind="redis", name="cache", pin="aws/elasticache"),
        BlockResolution(kind="ingress", name="main", pin="gcp/glb"),
    )
    badge = compute_badge(blocks=blocks)
    assert badge.kind == BadgeKind.PINNED
    assert badge.plugin_label == "aws"  # 2 aws vs 1 gcp


def test_badge_empty_manifest_is_portable():
    """Degenerate case: no managed services. Manifest is
    trivially portable."""
    badge = compute_badge(blocks=())
    assert badge.kind == BadgeKind.PORTABLE


# ---- resolution table ----------------------------------------------


def test_resolution_picks_manifest_pin_over_others():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="aws/rds"),
    )
    rows = build_resolution_table(
        blocks=blocks,
        org_defaults={"postgres": "gcp/cloudsql"},
        cluster_defaults={"postgres": "aws/aurora"},
        plugin_defaults={"postgres": "portable/postgres-15"},
    )
    assert len(rows) == 1
    assert rows[0].resolved_variant == "aws/rds"
    assert rows[0].source == ResolutionSource.MANIFEST_PIN


def test_resolution_falls_back_to_org_default():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin=""),
    )
    rows = build_resolution_table(
        blocks=blocks,
        org_defaults={"postgres": "gcp/cloudsql"},
        cluster_defaults={"postgres": "aws/aurora"},
        plugin_defaults={"postgres": "portable/postgres-15"},
    )
    assert rows[0].source == ResolutionSource.ORG_DEFAULT
    assert rows[0].resolved_variant == "gcp/cloudsql"


def test_resolution_falls_back_to_cluster_default():
    blocks = (BlockResolution(kind="redis", name="cache", pin=""),)
    rows = build_resolution_table(
        blocks=blocks,
        org_defaults={},
        cluster_defaults={"redis": "aws/elasticache"},
        plugin_defaults={"redis": "portable/redis-7"},
    )
    assert rows[0].source == ResolutionSource.CLUSTER_DEFAULT
    assert rows[0].resolved_variant == "aws/elasticache"


def test_resolution_falls_back_to_plugin_default():
    blocks = (BlockResolution(kind="redis", name="cache", pin=""),)
    rows = build_resolution_table(
        blocks=blocks,
        org_defaults={},
        cluster_defaults={},
        plugin_defaults={"redis": "portable/redis-7"},
    )
    assert rows[0].source == ResolutionSource.PLUGIN_DEFAULT
    assert rows[0].resolved_variant == "portable/redis-7"


def test_resolution_unresolvable_raises():
    """No pin + no defaults at any tier → can't resolve, refuse
    loudly so the operator knows the platform can't ship this."""
    blocks = (BlockResolution(kind="weird-kind", name="x", pin=""),)
    with pytest.raises(PortabilitySurfaceError):
        build_resolution_table(
            blocks=blocks,
            org_defaults={}, cluster_defaults={}, plugin_defaults={},
        )


# ---- make portable suggestions -------------------------------------


def test_make_portable_swappable():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="aws/rds"),
    )
    suggestions = make_portable_suggestions(
        blocks=blocks,
        has_portable_variant={"postgres": True},
    )
    assert len(suggestions) == 1
    assert "remove the variant pin" in suggestions[0].suggested_change


def test_make_portable_unswappable_kind():
    """Some kinds (e.g. an AWS-specific service) have no portable
    variant in the catalog. Suggestion explains the limitation."""
    blocks = (
        BlockResolution(kind="aws-eventbridge", name="events", pin="aws/eventbridge"),
    )
    suggestions = make_portable_suggestions(
        blocks=blocks,
        has_portable_variant={"aws-eventbridge": False},
    )
    assert "no portable variant" in suggestions[0].suggested_change.lower()
    assert "re-architecting" in suggestions[0].caveat


def test_make_portable_with_caveats():
    """When the pinned plugin uses features the portable variant
    doesn't have (e.g. RDS Multi-AZ), surface those features in
    the caveat so operator knows what they'd lose."""
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="aws/rds"),
    )
    suggestions = make_portable_suggestions(
        blocks=blocks,
        has_portable_variant={"postgres": True},
        plugin_specific_features={
            "postgres:db": ("multi-AZ failover", "performance insights"),
        },
    )
    assert "multi-AZ failover" in suggestions[0].caveat
    assert "performance insights" in suggestions[0].caveat


def test_make_portable_skips_already_portable():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin=""),
    )
    suggestions = make_portable_suggestions(
        blocks=blocks,
        has_portable_variant={"postgres": True},
    )
    assert suggestions == ()


# ---- 'where can I deploy this' -------------------------------------


def test_deployable_clusters_all_compatible():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin=""),
        BlockResolution(kind="redis", name="cache", pin=""),
    )
    clusters = (
        ClusterCompat(
            cluster_id=1, cluster_slug="aws-prod",
            available_variants=frozenset({
                "portable/postgres-15", "portable/redis-7",
            }),
        ),
    )
    out = deployable_clusters(
        blocks=blocks, candidates=clusters,
        org_defaults={},
        plugin_defaults={
            "postgres": "portable/postgres-15",
            "redis": "portable/redis-7",
        },
    )
    assert len(out) == 1
    assert out[0].cluster_slug == "aws-prod"


def test_deployable_clusters_filters_incompatible():
    """Manifest pinned to aws/rds; gcp cluster doesn't have it."""
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="aws/rds"),
    )
    clusters = (
        ClusterCompat(
            cluster_id=1, cluster_slug="aws",
            available_variants=frozenset({"aws/rds"}),
        ),
        ClusterCompat(
            cluster_id=2, cluster_slug="gcp",
            available_variants=frozenset({"gcp/cloudsql"}),
        ),
    )
    out = deployable_clusters(
        blocks=blocks, candidates=clusters,
        org_defaults={}, plugin_defaults={},
    )
    assert {c.cluster_slug for c in out} == {"aws"}


def test_deployable_clusters_unresolvable_block_drops_cluster():
    """Block has no pin and no default → cluster can't deploy
    this without per-cluster config; skip."""
    blocks = (
        BlockResolution(kind="postgres", name="db", pin=""),
    )
    clusters = (
        ClusterCompat(
            cluster_id=1, cluster_slug="aws",
            available_variants=frozenset({"aws/rds"}),
        ),
    )
    out = deployable_clusters(
        blocks=blocks, candidates=clusters,
        org_defaults={}, plugin_defaults={},
    )
    assert out == ()


# ---- promote-check -------------------------------------------------


def test_promote_check_promotable():
    """Both clusters have aws/rds; pinned manifest promotes."""
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="aws/rds"),
    )
    src = ClusterCompat(
        cluster_id=1, cluster_slug="aws-staging",
        available_variants=frozenset({"aws/rds"}),
    )
    tgt = ClusterCompat(
        cluster_id=2, cluster_slug="aws-prod",
        available_variants=frozenset({"aws/rds"}),
    )
    result = evaluate_promote(blocks=blocks, source_cluster=src, target_cluster=tgt)
    assert result.promotable is True
    assert result.issues == ()


def test_promote_check_target_missing_variant():
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="aws/rds"),
    )
    src = ClusterCompat(
        cluster_id=1, cluster_slug="aws-staging",
        available_variants=frozenset({"aws/rds"}),
    )
    tgt = ClusterCompat(
        cluster_id=2, cluster_slug="gcp-prod",
        available_variants=frozenset({"gcp/cloudsql"}),
    )
    result = evaluate_promote(blocks=blocks, source_cluster=src, target_cluster=tgt)
    assert result.promotable is False
    assert len(result.issues) == 1
    assert "gcp-prod" in result.issues[0]


def test_promote_check_skips_abstract_blocks():
    """Auto-resolved blocks promote freely — target picks its
    own variant."""
    blocks = (
        BlockResolution(kind="postgres", name="db", pin=""),
    )
    src = ClusterCompat(
        cluster_id=1, cluster_slug="x",
        available_variants=frozenset({"aws/rds"}),
    )
    tgt = ClusterCompat(
        cluster_id=2, cluster_slug="y",
        available_variants=frozenset({"gcp/cloudsql"}),
    )
    result = evaluate_promote(blocks=blocks, source_cluster=src, target_cluster=tgt)
    assert result.promotable is True


def test_promote_check_both_missing():
    """Pinned to a plugin neither cluster has."""
    blocks = (
        BlockResolution(kind="postgres", name="db", pin="exotic/specific"),
    )
    src = ClusterCompat(
        cluster_id=1, cluster_slug="x",
        available_variants=frozenset({"aws/rds"}),
    )
    tgt = ClusterCompat(
        cluster_id=2, cluster_slug="y",
        available_variants=frozenset({"gcp/cloudsql"}),
    )
    result = evaluate_promote(blocks=blocks, source_cluster=src, target_cluster=tgt)
    assert result.promotable is False
    # Both source AND target listed as missing the variant
    assert len(result.issues) == 2
