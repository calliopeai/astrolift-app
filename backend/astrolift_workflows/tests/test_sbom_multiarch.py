"""Tests for SBOM + multi-arch build policy (#165, spec 14 §10, §12)."""

from __future__ import annotations

import pytest

from astrolift_workflows.sbom_multiarch import (
    Arch,
    BuildPolicyError,
    DeploymentSbomState,
    SbomFormat,
    estimated_build_time_factor,
    has_required_sbom,
    normalize_arch,
    plan_architectures,
    sbom_artifact_tag,
    sbom_media_type,
)

# ---- SBOM artifact tag --------------------------------------------


def test_sbom_artifact_tag_cyclonedx():
    tag = sbom_artifact_tag(
        image_digest="sha256:" + "a" * 64,
        fmt=SbomFormat.CYCLONE_DX,
    )
    assert tag.startswith("sbom-cyclonedx-")
    assert "aaaaaaaaaaaa" in tag


def test_sbom_artifact_tag_spdx():
    tag = sbom_artifact_tag(
        image_digest="sha256:" + "b" * 64,
        fmt=SbomFormat.SPDX,
    )
    assert tag.startswith("sbom-spdx-")


def test_sbom_artifact_tag_deterministic():
    """Same digest + format → same tag, so consumers can compute
    the SBOM artifact ref without metadata lookup."""
    digest = "sha256:" + "c" * 64
    a = sbom_artifact_tag(image_digest=digest, fmt=SbomFormat.CYCLONE_DX)
    b = sbom_artifact_tag(image_digest=digest, fmt=SbomFormat.CYCLONE_DX)
    assert a == b


def test_sbom_artifact_tag_rejects_bad_digest():
    with pytest.raises(BuildPolicyError, match="sha256:"):
        sbom_artifact_tag(
            image_digest="not-a-digest",
            fmt=SbomFormat.CYCLONE_DX,
        )


# ---- SBOM media type ----------------------------------------------


def test_cyclonedx_media_type():
    assert sbom_media_type(
        fmt=SbomFormat.CYCLONE_DX,
    ) == "application/vnd.cyclonedx+json"


def test_spdx_media_type():
    assert sbom_media_type(
        fmt=SbomFormat.SPDX,
    ) == "application/spdx+json"


# ---- arch normalization -------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("amd64", Arch.AMD64),
    ("AMD64", Arch.AMD64),
    ("x86_64", Arch.AMD64),
    ("x86-64", Arch.AMD64),
    ("arm64", Arch.ARM64),
    ("aarch64", Arch.ARM64),
    ("ARM64", Arch.ARM64),
])
def test_normalize_arch_known(raw, expected):
    assert normalize_arch(raw=raw) == expected


def test_normalize_arch_strips_whitespace():
    assert normalize_arch(raw="  amd64  ") == Arch.AMD64


def test_normalize_arch_rejects_unsupported():
    """ppc64le / s390x out of scope until needed."""
    with pytest.raises(BuildPolicyError, match="unsupported"):
        normalize_arch(raw="ppc64le")


# ---- arch planning ------------------------------------------------


def test_plan_auto_single_arch_cluster():
    """Cluster has only amd64 nodes, default 'auto' → build only
    amd64. Saves the multi-arch tax."""
    plan = plan_architectures(
        cluster_node_architectures=["amd64", "amd64", "x86_64"],
    )
    assert plan.architectures == (Arch.AMD64,)
    assert plan.is_multi_arch is False


def test_plan_auto_mixed_cluster():
    """Cluster has both archs (e.g. Graviton + x86 mix) → build
    both."""
    plan = plan_architectures(
        cluster_node_architectures=["amd64", "arm64"],
    )
    assert set(plan.architectures) == {Arch.AMD64, Arch.ARM64}
    assert plan.is_multi_arch is True


def test_plan_auto_dedupes_arch_list():
    """Multiple amd64 nodes → still single-arch build."""
    plan = plan_architectures(
        cluster_node_architectures=["amd64"] * 10,
    )
    assert plan.architectures == (Arch.AMD64,)


def test_plan_force_amd64_on_arm_cluster_rejected():
    """Manifest pins amd64 but cluster has only arm64 — refuse
    so deploy doesn't ship pods that can't schedule."""
    with pytest.raises(BuildPolicyError, match="cluster has no"):
        plan_architectures(
            cluster_node_architectures=["arm64"],
            manifest_arch_preference="amd64",
        )


def test_plan_force_arm64_on_amd64_cluster_rejected():
    with pytest.raises(BuildPolicyError, match="cluster has no"):
        plan_architectures(
            cluster_node_architectures=["amd64"],
            manifest_arch_preference="arm64",
        )


def test_plan_force_amd64():
    """Cluster has both, manifest pins amd64 → single-arch."""
    plan = plan_architectures(
        cluster_node_architectures=["amd64", "arm64"],
        manifest_arch_preference="amd64",
    )
    assert plan.architectures == (Arch.AMD64,)


def test_plan_force_multi_on_single_arch_cluster():
    """Manifest opts into multi-arch even though cluster is
    single-arch (forward-looking — preparing for migration)."""
    plan = plan_architectures(
        cluster_node_architectures=["amd64"],
        manifest_arch_preference="multi",
    )
    assert set(plan.architectures) == {Arch.AMD64, Arch.ARM64}


def test_plan_unknown_preference_rejected():
    with pytest.raises(BuildPolicyError, match="unknown"):
        plan_architectures(
            cluster_node_architectures=["amd64"],
            manifest_arch_preference="weird",
        )


def test_plan_empty_cluster_archs_rejected():
    """No nodes = nothing to deploy to — refuse."""
    with pytest.raises(BuildPolicyError, match="cannot be empty"):
        plan_architectures(cluster_node_architectures=[])


# ---- build time estimate ------------------------------------------


def test_single_arch_factor_one():
    plan = plan_architectures(
        cluster_node_architectures=["amd64"],
    )
    assert estimated_build_time_factor(plan=plan) == 1.0


def test_multi_arch_factor_above_one():
    """UI surfaces 'this build will take longer' warning."""
    plan = plan_architectures(
        cluster_node_architectures=["amd64", "arm64"],
    )
    assert estimated_build_time_factor(plan=plan) > 1.0


# ---- SBOM presence on deployment ----------------------------------


def test_has_required_sbom_present_when_required():
    state = DeploymentSbomState(
        deployment_id=1,
        sbom_present=True,
        sbom_format=SbomFormat.CYCLONE_DX,
        sbom_artifact_ref="reg/app:sbom-cyclonedx-abc",
    )
    assert has_required_sbom(state=state, required=True) is True


def test_has_required_sbom_missing_when_required():
    state = DeploymentSbomState(
        deployment_id=1, sbom_present=False,
        sbom_format=None, sbom_artifact_ref="",
    )
    assert has_required_sbom(state=state, required=True) is False


def test_sbom_not_required_passes_through():
    """Org doesn't require SBOM → presence doesn't matter."""
    state = DeploymentSbomState(
        deployment_id=1, sbom_present=False,
        sbom_format=None, sbom_artifact_ref="",
    )
    assert has_required_sbom(state=state, required=False) is True
