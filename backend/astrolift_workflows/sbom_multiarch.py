"""
SBOM emission + multi-arch build policy (#165, spec 14 §10, §12).

Pure-Python policy. The build workflow consults this for:

* **SBOM format selection** — CycloneDX vs SPDX based on org
  preference / consumer requirements.
* **OCI artifact reference** — SBOM is pushed alongside the
  image; the artifact reference is deterministic so vulnerability
  scanners can find it.
* **Multi-arch decision** — given a target cluster's node
  architectures, decide which architectures to build. Avoids
  paying the multi-arch tax when the cluster is single-arch.
* **Architecture validation** — refuse arch values not in the
  OCI list.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import Enum


class BuildPolicyError(ValueError):
    pass


# ---- SBOM format ---------------------------------------------------


class SbomFormat(str, Enum):
    """Spec 14 §10: two industry-standard formats."""

    CYCLONE_DX = "cyclonedx"
    """OWASP-led; common in dependency-vuln tooling."""

    SPDX = "spdx"
    """Linux Foundation; common in license-compliance tooling."""


def sbom_artifact_tag(*, image_digest: str, fmt: SbomFormat) -> str:
    """Spec §10: SBOM is pushed as an OCI artifact tagged
    deterministically so consumers (Trivy, Grype, etc.) can
    discover it without metadata lookup.

    Convention: ``<image>-sbom-<format>`` referenced by the
    image's content-addressed digest stripped of the algorithm
    prefix.
    """
    if not image_digest.startswith("sha256:"):
        raise BuildPolicyError(f"image_digest {image_digest!r} must start with 'sha256:'")
    short = image_digest[len("sha256:") :][:12]
    return f"sbom-{fmt.value}-{short}"


def sbom_media_type(*, fmt: SbomFormat) -> str:
    r"""OCI media type the SBOM blob carries. Used in the OCI
    manifest's \`config.mediaType\` so registries that
    differentiate by media type can handle correctly."""
    if fmt == SbomFormat.CYCLONE_DX:
        return "application/vnd.cyclonedx+json"
    if fmt == SbomFormat.SPDX:
        return "application/spdx+json"
    raise BuildPolicyError(f"unknown SBOM format {fmt!r}")


# ---- multi-arch ----------------------------------------------------


class Arch(str, Enum):
    """OCI architecture vocabulary the platform supports.
    Limited to amd64 + arm64; ppc64le / s390x are out of scope
    until an org actually needs them (the CI matrix expands
    quickly with each added arch)."""

    AMD64 = "amd64"
    ARM64 = "arm64"


# OCI also accepts these aliases. Manifest lists from the wild
# may use either; normalize on read.
_ARCH_ALIASES = {
    "amd64": Arch.AMD64,
    "x86_64": Arch.AMD64,
    "x86-64": Arch.AMD64,
    "arm64": Arch.ARM64,
    "aarch64": Arch.ARM64,
}


def normalize_arch(*, raw: str) -> Arch:
    """Tolerate caller variation; always emit canonical form."""
    canonical = _ARCH_ALIASES.get(raw.lower().strip())
    if canonical is None:
        raise BuildPolicyError(
            f"unsupported architecture {raw!r}; supported: "
            f"{[a.value for a in Arch]} (with aliases x86_64, aarch64)"
        )
    return canonical


@dataclasses.dataclass(frozen=True, slots=True)
class BuildArchPlan:
    """The set of architectures the build workflow should target."""

    architectures: tuple[Arch, ...]
    is_multi_arch: bool
    r"""Convenience: \`len(architectures) > 1\`."""

    def __post_init__(self) -> None:
        if not self.architectures:
            raise BuildPolicyError("build plan must target at least one architecture")


def plan_architectures(
    *,
    cluster_node_architectures: Sequence[str],
    manifest_arch_preference: str = "auto",
) -> BuildArchPlan:
    """Spec §12: pick build architectures.

    ``cluster_node_architectures``: the unique architectures of
    nodes in the target cluster (extracted from
    ``node.status.nodeInfo.architecture``). Determines what the
    pods can run on.

    ``manifest_arch_preference``: app-level opt-in:
    - ``auto``: build only what the cluster needs (default;
      cheapest)
    - ``amd64``: force single-arch amd64 (legacy / specific
      compatibility needs)
    - ``arm64``: force single-arch arm64
    - ``multi``: always build both, regardless of cluster
      (useful for shared images, future-proofing migrations)
    """
    if not cluster_node_architectures:
        raise BuildPolicyError("cluster_node_architectures cannot be empty — " "where would the pods run?")

    cluster_archs: list[Arch] = []
    seen: set[Arch] = set()
    for raw in cluster_node_architectures:
        arch = normalize_arch(raw=raw)
        if arch not in seen:
            seen.add(arch)
            cluster_archs.append(arch)

    pref = manifest_arch_preference.lower().strip()

    if pref == "auto":
        target = tuple(cluster_archs)
    elif pref in ("amd64", "x86_64"):
        if Arch.AMD64 not in seen:
            raise BuildPolicyError("manifest pinned to amd64 but cluster has no " "amd64 nodes")
        target = (Arch.AMD64,)
    elif pref in ("arm64", "aarch64"):
        if Arch.ARM64 not in seen:
            raise BuildPolicyError("manifest pinned to arm64 but cluster has no " "arm64 nodes")
        target = (Arch.ARM64,)
    elif pref == "multi":
        target = (Arch.AMD64, Arch.ARM64)
    else:
        raise BuildPolicyError(
            f"unknown manifest_arch_preference {pref!r}; " "supported: auto, amd64, arm64, multi"
        )

    return BuildArchPlan(
        architectures=target,
        is_multi_arch=len(target) > 1,
    )


# ---- build-time estimation ----------------------------------------


# Approximate multi-arch tax. Multi-arch builds run per-arch
# stages serially (without QEMU+buildx parallelism) or in parallel
# on multi-arch runners; in practice 1.6-1.8x single-arch wall
# time. The estimator surfaces this for the UI's 'this build will
# take longer' warning, NOT a hard policy decision.
_MULTI_ARCH_TIME_FACTOR = 1.7


def estimated_build_time_factor(*, plan: BuildArchPlan) -> float:
    """Multiplier vs. single-arch baseline. UI uses this to set
    a 'this build will take ~Nx longer' indicator before the
    operator commits."""
    if plan.is_multi_arch:
        return _MULTI_ARCH_TIME_FACTOR
    return 1.0


# ---- SBOM presence on Deployment ----------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class DeploymentSbomState:
    """Spec §10: tracked on Deployment record. Queryable by
    'apps with/without SBOM' filter."""

    deployment_id: int
    sbom_present: bool
    sbom_format: SbomFormat | None
    sbom_artifact_ref: str
    r"""Full OCI ref (e.g. \`registry/app:sbom-cyclonedx-abc123\`)
    for the cross-reference query into vuln databases."""


def has_required_sbom(
    *,
    state: DeploymentSbomState,
    required: bool,
) -> bool:
    """For the compliance evidence collector (#271) — orgs that
    require SBOM should fail compliance when a deployment lacks
    one."""
    if not required:
        return True
    return state.sbom_present
