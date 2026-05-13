"""
Capability pre-check at bind + promotion validation (#59,
spec 20 §5-6).

Two related validators:

* **Bind pre-check** — runs when a RegisteredApp is first bound to
  a TenantCluster. Walks the manifest and verifies the cluster's
  plugins can satisfy every abstract reference. Catches the
  portability-vs-capability mismatch at registration time, not at
  the first deploy.
* **Promotion validation** — runs when promoting a Deployment from
  a source env to a target env (often a different cluster). Walks
  the source's resolved variants and verifies the target cluster
  exposes every one. Rejects cross-cloud promotion of pinned
  variants because a variant pinned to ``aws-rds/aurora-15`` can't
  satisfy on a GCP cluster.

Both validators return structured error lists so the operator sees
every blocker at once (UX rule: don't make them bisect).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

# ---- bind pre-check --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ManifestRequirement:
    """One abstract reference from a manifest that must resolve to
    a concrete plugin variant on the target cluster.

    ``kind`` is what the manifest declares (postgres / redis / queue
    / ingress / etc.). ``name`` identifies the block for error
    messages. ``variant_pin`` carries the operator's pin if any.
    """

    kind: str
    name: str
    variant_pin: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class ClusterCapabilities:
    """Projection of a TenantCluster's plugin catalog the validators
    consume. Caller flattens the cluster's plugin manifests into
    these primitives so the validators stay pure."""

    cluster_id: int
    cluster_slug: str
    cloud_provider: str  # 'aws' / 'gcp' / 'azure' / 'onprem'
    supported_kinds: frozenset[str]
    supported_pins: frozenset[str]
    """Concrete ``<plugin>/<variant>`` strings the cluster's plugins
    publish. Used for variant-pin lookups during bind + promotion."""


@dataclasses.dataclass(frozen=True, slots=True)
class CheckIssue:
    """One unresolvable reference."""

    kind: str
    name: str
    code: str
    detail: str


@dataclasses.dataclass(frozen=True, slots=True)
class CheckReport:
    issues: tuple[CheckIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def bind_precheck(
    *,
    requirements: Sequence[ManifestRequirement],
    cluster: ClusterCapabilities,
) -> CheckReport:
    """Walk ``requirements`` and verify the cluster can satisfy each.

    Two failure modes per requirement:

      - ``unsupported_kind``: cluster's plugins don't expose this
        kind at all (no Postgres plugin loaded, etc.).
      - ``unsupported_pin``: kind is exposed but the operator's
        pin doesn't match a concrete variant the cluster carries.
    """
    issues: list[CheckIssue] = []
    for req in requirements:
        if req.kind not in cluster.supported_kinds:
            issues.append(
                CheckIssue(
                    kind=req.kind,
                    name=req.name,
                    code="unsupported_kind",
                    detail=(f"cluster {cluster.cluster_slug!r} has no plugin for kind {req.kind!r}"),
                )
            )
            continue
        if req.variant_pin and req.variant_pin not in cluster.supported_pins:
            issues.append(
                CheckIssue(
                    kind=req.kind,
                    name=req.name,
                    code="unsupported_pin",
                    detail=(
                        f"cluster {cluster.cluster_slug!r} ({cluster.cloud_provider}) "
                        f"does not expose variant {req.variant_pin!r}"
                    ),
                )
            )
    return CheckReport(issues=tuple(issues))


# ---- promotion validation -------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedVariantSnapshot:
    """The bind/deploy-time resolution recorded on a Deployment.

    Promotion validation reads from a source ``Deployment``'s
    ``config_snapshot`` and verifies every resolved variant is
    available on the target cluster.
    """

    kind: str
    name: str
    pin: str  # '<plugin>/<variant>'


def promotion_check(
    *,
    source_resolutions: Sequence[ResolvedVariantSnapshot],
    target_cluster: ClusterCapabilities,
    source_cloud_provider: str,
) -> CheckReport:
    """Verify the target cluster can satisfy every resolved variant
    from the source.

    Cross-cloud promotion is allowed *only* when none of the source
    resolutions are cloud-specific pins. We approximate
    'cloud-specific' as: the pin's plugin slug starts with the
    source provider name (e.g. 'aws-rds', 'gcp-cloudsql'). Pins like
    'postgres-portable/15' don't trip this rule and promote freely.
    """
    issues: list[CheckIssue] = []
    cross_cloud = (
        source_cloud_provider != target_cluster.cloud_provider
        and source_cloud_provider
        and target_cluster.cloud_provider
    )

    for r in source_resolutions:
        if r.pin not in target_cluster.supported_pins:
            issues.append(
                CheckIssue(
                    kind=r.kind,
                    name=r.name,
                    code="missing_variant_on_target",
                    detail=(
                        f"kind {r.kind!r} was resolved to {r.pin!r} on the "
                        f"source env but target cluster "
                        f"{target_cluster.cluster_slug!r} "
                        f"({target_cluster.cloud_provider}) does not expose "
                        "that variant"
                    ),
                )
            )
            continue

        if cross_cloud and _is_cloud_specific(r.pin, source_cloud_provider):
            issues.append(
                CheckIssue(
                    kind=r.kind,
                    name=r.name,
                    code="cross_cloud_pinned_variant",
                    detail=(
                        f"variant {r.pin!r} is {source_cloud_provider}-specific; "
                        f"cannot promote to a {target_cluster.cloud_provider} "
                        "cluster (declare portability='portable' or use "
                        "abstract kinds in the source manifest)"
                    ),
                )
            )
    return CheckReport(issues=tuple(issues))


def _is_cloud_specific(pin: str, cloud_provider: str) -> bool:
    """A pin like ``aws-rds/aurora-15`` is cloud-specific when the
    plugin slug starts with the cloud provider name. The convention
    in the plugin catalog is ``<cloud>-<service>`` for cloud-bound
    plugins and a bare name for portable ones."""
    if not pin or not cloud_provider:
        return False
    plugin = pin.split("/", 1)[0]
    return plugin.startswith(f"{cloud_provider}-") or plugin == cloud_provider
