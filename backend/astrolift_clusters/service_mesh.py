"""
Service mesh integration policy (#75, spec 13 §8).

Pure-Python policy. The cluster + manifest renderers consult this
module for:

* **Mesh detection** — does the cluster have Istio or Linkerd?
* **Namespace annotation rendering** — per-mesh injection flags.
* **Mesh-aware manifest validation** — mTLS mode, traffic policy,
  canary splits, with refusal of mesh-only features when no
  mesh is present (loud rather than silent ignore).
* **Non-mesh fallback** — manifest's ``[workloads.mesh]`` block is
  silently dropped when mesh isn't enabled (the spec's
  'apps must work without one' rule).

Pairs with #70 (TLS / mTLS modes) and #156 (mesh-edge cross-checks).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import Enum


class MeshError(ValueError):
    pass


class MeshProvider(str, Enum):
    """Spec 13 §8: supported mesh providers."""

    NONE = "none"
    """Cluster has no mesh installed. Manifest mesh blocks are
    silently dropped (per spec 'apps must work without one')."""

    ISTIO = "istio"
    LINKERD = "linkerd"


# ---- namespace injection annotations -------------------------------


def namespace_injection_annotations(
    *,
    provider: MeshProvider,
) -> dict[str, str]:
    """Render the per-namespace annotation/label that triggers
    sidecar injection. Each mesh has its own conventions:

    * Istio uses the LABEL ``istio-injection=enabled``.
    * Linkerd uses the ANNOTATION ``linkerd.io/inject=enabled``.

    Caller is responsible for applying the result to the right
    metadata field; this module returns the key/value contract
    only. Callers compose multiple meshes into the right shape
    (Istio: labels, Linkerd: annotations).
    """
    if provider == MeshProvider.ISTIO:
        return {"istio-injection": "enabled"}
    if provider == MeshProvider.LINKERD:
        return {"linkerd.io/inject": "enabled"}
    if provider == MeshProvider.NONE:
        return {}
    raise MeshError(f"unknown mesh provider {provider!r}")


# ---- per-workload mesh config validation ---------------------------


class MtlsMode(str, Enum):
    """Spec 13 §8: per-workload mesh mTLS."""

    OFF = "off"
    PERMISSIVE = "permissive"
    """Accept both mTLS and plaintext. Used during rollout
    (workloads being added to mesh while others aren't yet)."""

    STRICT = "strict"
    """Require mTLS. Refuses non-mTLS connections."""


class TrafficPolicy(str, Enum):
    """Spec 13 §8: traffic distribution within a mesh service."""

    ROUND_ROBIN = "round_robin"
    LEAST_REQUEST = "least_request"
    CONSISTENT_HASH = "consistent_hash"


@dataclasses.dataclass(frozen=True, slots=True)
class CanarySplit:
    """Spec 13 §8: traffic split between versions during canary."""

    version_label: str
    """Pod label value the mesh routes to (e.g. ``v1``, ``v2``)."""

    weight: int
    """Percent weight (0-100). Splits sum to 100."""

    def __post_init__(self) -> None:
        if not self.version_label:
            raise MeshError("canary split requires version_label")
        if self.weight < 0 or self.weight > 100:
            raise MeshError(f"canary split weight {self.weight} not in [0, 100]")


@dataclasses.dataclass(frozen=True, slots=True)
class MeshConfig:
    """Spec 13 §8: ``[workloads.<name>.mesh]`` block."""

    enabled: bool = False
    mtls: MtlsMode = MtlsMode.OFF
    traffic_policy: TrafficPolicy = TrafficPolicy.ROUND_ROBIN
    canary_splits: tuple[CanarySplit, ...] = ()

    def __post_init__(self) -> None:
        if not self.enabled:
            # Disabled mesh blocks shouldn't carry config; refuse
            # so operators don't think their settings are active.
            if self.mtls != MtlsMode.OFF:
                raise MeshError(
                    "mesh.enabled=False but mtls is set; remove the " "mtls line or set enabled=true"
                )
            if self.canary_splits:
                raise MeshError("mesh.enabled=False but canary_splits is set")

        if self.canary_splits:
            total = sum(s.weight for s in self.canary_splits)
            if total != 100:
                raise MeshError(f"canary split weights sum to {total}, must be 100")


def parse_mesh_config(raw: dict) -> MeshConfig:
    """Project a TOML ``[workloads.<name>.mesh]`` block onto
    MeshConfig. Validates field types + closed-vocab values."""
    if raw is None:
        return MeshConfig()

    splits_raw = raw.get("canary_splits", []) or []
    splits = tuple(
        CanarySplit(
            version_label=str(s["version_label"]),
            weight=int(s["weight"]),
        )
        for s in splits_raw
    )

    try:
        mtls = MtlsMode(raw.get("mtls", "off"))
    except ValueError as exc:
        raise MeshError(
            f"mesh.mtls {raw.get('mtls')!r} not one of " f"{[m.value for m in MtlsMode]}"
        ) from exc

    try:
        policy = TrafficPolicy(
            raw.get("traffic_policy", "round_robin"),
        )
    except ValueError as exc:
        raise MeshError(
            f"mesh.traffic_policy {raw.get('traffic_policy')!r} "
            f"not one of {[p.value for p in TrafficPolicy]}"
        ) from exc

    return MeshConfig(
        enabled=bool(raw.get("enabled", False)),
        mtls=mtls,
        traffic_policy=policy,
        canary_splits=splits,
    )


# ---- mesh / cluster compatibility ----------------------------------


def effective_mesh_config(
    *,
    manifest_config: MeshConfig,
    cluster_provider: MeshProvider,
) -> MeshConfig:
    """Spec 13 §8: 'apps must work without a mesh.'

    Apply the non-mesh fallback rule:
      - Manifest declares mesh.enabled=true + cluster has NO mesh:
        log + drop (return disabled config). Doesn't fail the
        deploy. Cluster operator can install mesh later and
        re-deploy without manifest changes.
      - Manifest declares mesh.enabled=false: pass through.
      - Manifest declares mesh.enabled=true + cluster HAS mesh:
        pass through (ready to render).
    """
    if not manifest_config.enabled:
        return manifest_config
    if cluster_provider == MeshProvider.NONE:
        return MeshConfig(enabled=False)
    return manifest_config


def must_warn_about_dropped_config(
    *,
    manifest_config: MeshConfig,
    cluster_provider: MeshProvider,
) -> bool:
    """When effective_mesh_config drops mesh config because the
    cluster has no mesh, the deploy log should warn about it
    (silent drop violates 'apps work without mesh' but operator
    may not realize their canary config is being ignored)."""
    return manifest_config.enabled and cluster_provider == MeshProvider.NONE


# ---- canary helpers ------------------------------------------------


def normalize_canary_splits(
    *,
    splits: Sequence[CanarySplit],
) -> tuple[CanarySplit, ...]:
    """Stable order: sort by version_label so the rendered mesh
    config is byte-for-byte stable between runs (avoids spurious
    diffs in GitOps PRs)."""
    return tuple(sorted(splits, key=lambda s: s.version_label))


def is_canary_active(*, config: MeshConfig) -> bool:
    """True when traffic is split across more than one version.
    Used by the UI to render the 'canary in progress' badge."""
    if not config.enabled or not config.canary_splits:
        return False
    return len(config.canary_splits) > 1
