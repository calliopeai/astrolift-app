"""Tests for service mesh policy (#75, spec 13 §8)."""

from __future__ import annotations

import pytest

from astrolift_clusters.service_mesh import (
    CanarySplit,
    MeshConfig,
    MeshError,
    MeshProvider,
    MtlsMode,
    TrafficPolicy,
    effective_mesh_config,
    is_canary_active,
    must_warn_about_dropped_config,
    namespace_injection_annotations,
    normalize_canary_splits,
    parse_mesh_config,
)

# ---- namespace injection -------------------------------------------


def test_namespace_injection_istio():
    """Istio uses the LABEL ``istio-injection=enabled``."""
    assert namespace_injection_annotations(
        provider=MeshProvider.ISTIO,
    ) == {"istio-injection": "enabled"}


def test_namespace_injection_linkerd():
    """Linkerd uses the ANNOTATION ``linkerd.io/inject=enabled``."""
    assert namespace_injection_annotations(
        provider=MeshProvider.LINKERD,
    ) == {"linkerd.io/inject": "enabled"}


def test_namespace_injection_none():
    """No mesh installed = no injection annotation."""
    assert (
        namespace_injection_annotations(
            provider=MeshProvider.NONE,
        )
        == {}
    )


# ---- canary split validation ---------------------------------------


def test_canary_split_basic():
    s = CanarySplit(version_label="v1", weight=50)
    assert s.weight == 50


def test_canary_split_requires_version_label():
    with pytest.raises(MeshError, match="version_label"):
        CanarySplit(version_label="", weight=50)


@pytest.mark.parametrize("weight", [-1, 101, 200])
def test_canary_split_weight_bounds(weight):
    with pytest.raises(MeshError, match="weight"):
        CanarySplit(version_label="v1", weight=weight)


# ---- mesh config validation ----------------------------------------


def test_mesh_config_disabled_default():
    cfg = MeshConfig()
    assert cfg.enabled is False
    assert cfg.mtls == MtlsMode.OFF


def test_mesh_config_enabled_with_strict_mtls():
    cfg = MeshConfig(enabled=True, mtls=MtlsMode.STRICT)
    assert cfg.mtls == MtlsMode.STRICT


def test_mesh_config_disabled_with_mtls_rejected():
    """Operator wrote mtls but disabled mesh — refuse so they
    don't think the mtls setting is active."""
    with pytest.raises(MeshError, match="mtls"):
        MeshConfig(enabled=False, mtls=MtlsMode.STRICT)


def test_mesh_config_disabled_with_canary_rejected():
    """Operator wrote canary_splits but disabled mesh — refuse."""
    with pytest.raises(MeshError, match="canary"):
        MeshConfig(
            enabled=False,
            canary_splits=(CanarySplit(version_label="v1", weight=100),),
        )


def test_mesh_config_canary_splits_must_sum_to_100():
    with pytest.raises(MeshError, match="100"):
        MeshConfig(
            enabled=True,
            canary_splits=(
                CanarySplit(version_label="v1", weight=50),
                CanarySplit(version_label="v2", weight=30),
            ),
        )


def test_mesh_config_canary_splits_valid_sum():
    cfg = MeshConfig(
        enabled=True,
        canary_splits=(
            CanarySplit(version_label="v1", weight=70),
            CanarySplit(version_label="v2", weight=30),
        ),
    )
    assert sum(s.weight for s in cfg.canary_splits) == 100


# ---- parse_mesh_config ---------------------------------------------


def test_parse_mesh_config_default():
    """Spec: defaults are mesh-off, mtls=off, round_robin."""
    cfg = parse_mesh_config({})
    assert cfg.enabled is False
    assert cfg.mtls == MtlsMode.OFF
    assert cfg.traffic_policy == TrafficPolicy.ROUND_ROBIN


def test_parse_mesh_config_full():
    cfg = parse_mesh_config(
        {
            "enabled": True,
            "mtls": "strict",
            "traffic_policy": "consistent_hash",
            "canary_splits": [
                {"version_label": "v1", "weight": 80},
                {"version_label": "v2", "weight": 20},
            ],
        }
    )
    assert cfg.enabled is True
    assert cfg.mtls == MtlsMode.STRICT
    assert cfg.traffic_policy == TrafficPolicy.CONSISTENT_HASH
    assert len(cfg.canary_splits) == 2


def test_parse_mesh_config_unknown_mtls():
    with pytest.raises(MeshError, match="mtls"):
        parse_mesh_config({"enabled": True, "mtls": "always"})


def test_parse_mesh_config_unknown_traffic_policy():
    with pytest.raises(MeshError, match="traffic_policy"):
        parse_mesh_config(
            {
                "enabled": True,
                "traffic_policy": "ip_hash",
            }
        )


def test_parse_mesh_config_none_returns_disabled():
    """TOML missing the [mesh] block entirely: parser passes None."""
    cfg = parse_mesh_config(None)  # type: ignore[arg-type]
    assert cfg.enabled is False


# ---- effective config (cluster fallback) ---------------------------


def test_effective_drops_mesh_when_cluster_has_none():
    """Spec: 'apps must work without a mesh.' Manifest with
    mesh.enabled=true on a no-mesh cluster: drop config, deploy
    proceeds."""
    manifest = MeshConfig(enabled=True, mtls=MtlsMode.STRICT)
    effective = effective_mesh_config(
        manifest_config=manifest,
        cluster_provider=MeshProvider.NONE,
    )
    assert effective.enabled is False


def test_effective_passes_through_when_mesh_present():
    manifest = MeshConfig(enabled=True, mtls=MtlsMode.STRICT)
    effective = effective_mesh_config(
        manifest_config=manifest,
        cluster_provider=MeshProvider.ISTIO,
    )
    assert effective.enabled is True
    assert effective.mtls == MtlsMode.STRICT


def test_effective_disabled_passes_through():
    manifest = MeshConfig()
    effective = effective_mesh_config(
        manifest_config=manifest,
        cluster_provider=MeshProvider.ISTIO,
    )
    assert effective == manifest


def test_must_warn_when_dropped():
    """Operator should learn their canary config was ignored."""
    assert (
        must_warn_about_dropped_config(
            manifest_config=MeshConfig(enabled=True),
            cluster_provider=MeshProvider.NONE,
        )
        is True
    )


def test_no_warn_when_not_dropped():
    assert (
        must_warn_about_dropped_config(
            manifest_config=MeshConfig(enabled=True),
            cluster_provider=MeshProvider.ISTIO,
        )
        is False
    )
    assert (
        must_warn_about_dropped_config(
            manifest_config=MeshConfig(),
            cluster_provider=MeshProvider.NONE,
        )
        is False
    )


# ---- canary helpers ------------------------------------------------


def test_normalize_canary_splits_sorts_by_version_label():
    """GitOps stability: same input → same rendered manifest."""
    splits = (
        CanarySplit(version_label="v3", weight=30),
        CanarySplit(version_label="v1", weight=40),
        CanarySplit(version_label="v2", weight=30),
    )
    out = normalize_canary_splits(splits=splits)
    assert [s.version_label for s in out] == ["v1", "v2", "v3"]


def test_canary_active_two_versions():
    cfg = MeshConfig(
        enabled=True,
        canary_splits=(
            CanarySplit(version_label="v1", weight=80),
            CanarySplit(version_label="v2", weight=20),
        ),
    )
    assert is_canary_active(config=cfg) is True


def test_canary_inactive_single_version():
    """Single version with weight=100 is not really a canary —
    it's just a normal deploy. UI shouldn't render the badge."""
    cfg = MeshConfig(
        enabled=True,
        canary_splits=(CanarySplit(version_label="v1", weight=100),),
    )
    assert is_canary_active(config=cfg) is False


def test_canary_inactive_disabled_mesh():
    cfg = MeshConfig()
    assert is_canary_active(config=cfg) is False
