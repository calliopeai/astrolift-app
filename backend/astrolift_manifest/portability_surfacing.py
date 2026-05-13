"""
Portability surfacing policy (#65, spec 20 §11-12).

Pure-Python policy. The UI's app-detail page and the
``astro app portability`` CLI both consult this module for:

* **Portability badge** — Portable / Mixed / Pinned with
  attribution to the most-pinned plugin (color hint for UI).
* **Resolution table** — per-kind source attribution
  (manifest pin > org default > cluster default > plugin default)
  and the variant the platform would resolve to.
* **'Make portable' diff** — actionable suggestions for converting
  pinned blocks to abstract kinds, with caveats about when the
  swap is unsafe.
* **'Where can I deploy this?'** — cluster compatibility filtering.

Pairs with #51 (portability mode validation) and #55 (variant
resolution drivers). This module is the read side; mutations
go through the manifest + cluster modules.
"""

from __future__ import annotations

import collections
import dataclasses
from collections.abc import Sequence
from enum import StrEnum


class PortabilitySurfaceError(ValueError):
    pass


# ---- portability badge ---------------------------------------------


class BadgeKind(StrEnum):
    """Spec §11: three states for the UI badge."""

    PORTABLE = "portable"
    """All blocks resolve to abstract kinds. Color: green."""

    MIXED = "mixed"
    """Some pinned, some abstract. Color: yellow.
    Often the reasonable default — operator pinned a few critical
    services but kept the rest portable."""

    PINNED = "pinned"
    """Every block pinned to a specific plugin. Color: orange.
    UI surfaces the dominant plugin name (\"Pinned to aws\")."""


@dataclasses.dataclass(frozen=True, slots=True)
class PortabilityBadge:
    kind: BadgeKind
    plugin_label: str = ""
    """For PINNED: the plugin name. For MIXED: the most-frequent
    plugin (so 'Pinned to aws' is more useful than 'Mixed')."""

    pinned_count: int = 0
    """How many blocks are pinned. UI renders as 'N pinned'."""

    portable_count: int = 0


def _plugin_of(*, pin: str) -> str:
    """Extract '<plugin>' from '<plugin>/<variant>' pin string."""
    return pin.split("/", 1)[0] if pin else ""


@dataclasses.dataclass(frozen=True, slots=True)
class BlockResolution:
    """Per-block summary the surfacing layer produces from
    raw manifest + resolution data."""

    kind: str
    """Block kind (postgres, redis, ingress, ...)."""

    name: str
    """Manifest block name (e.g. 'main_db')."""

    pin: str
    """Resolved pin (\"<plugin>/<variant>\") or empty if abstract."""


def compute_badge(
    *,
    blocks: Sequence[BlockResolution],
) -> PortabilityBadge:
    """Spec §11: classify the manifest's portability for the UI.

    Empty manifest → PORTABLE (degenerate but legal — an app
    without managed services is trivially portable).
    """
    pinned = [b for b in blocks if b.pin]
    portable = [b for b in blocks if not b.pin]
    if not pinned:
        return PortabilityBadge(
            kind=BadgeKind.PORTABLE,
            plugin_label="",
            pinned_count=0,
            portable_count=len(portable),
        )

    plugins = collections.Counter(_plugin_of(pin=b.pin) for b in pinned)
    dominant = plugins.most_common(1)[0][0]

    if not portable:
        return PortabilityBadge(
            kind=BadgeKind.PINNED,
            plugin_label=dominant,
            pinned_count=len(pinned),
            portable_count=0,
        )

    return PortabilityBadge(
        kind=BadgeKind.MIXED,
        plugin_label=dominant,
        pinned_count=len(pinned),
        portable_count=len(portable),
    )


# ---- resolution table ----------------------------------------------


class ResolutionSource(StrEnum):
    """Where the resolved variant came from. Order matters —
    manifest pin > org default > cluster default > plugin default."""

    MANIFEST_PIN = "manifest_pin"
    """Operator wrote ``variant = '<plugin>/<x>'`` in the manifest.
    Highest precedence."""

    ORG_DEFAULT = "org_default"
    """Org admin set a default plugin per kind."""

    CLUSTER_DEFAULT = "cluster_default"
    """Cluster declares 'on this cluster, postgres → aws/rds'."""

    PLUGIN_DEFAULT = "plugin_default"
    """Lowest precedence: the plugin's own self-declared default
    when no other source attributed."""


@dataclasses.dataclass(frozen=True, slots=True)
class ResolutionRow:
    """One row of the variant-resolution table."""

    kind: str
    name: str
    resolved_variant: str
    source: ResolutionSource


def build_resolution_table(
    *,
    blocks: Sequence[BlockResolution],
    org_defaults: dict[str, str],
    cluster_defaults: dict[str, str],
    plugin_defaults: dict[str, str],
) -> tuple[ResolutionRow, ...]:
    """Walk blocks; pick highest-precedence source that resolves.

    All ``defaults`` maps are kind→variant. Empty value means 'no
    default of this tier'.
    """
    rows: list[ResolutionRow] = []
    for b in blocks:
        if b.pin:
            rows.append(
                ResolutionRow(
                    kind=b.kind,
                    name=b.name,
                    resolved_variant=b.pin,
                    source=ResolutionSource.MANIFEST_PIN,
                )
            )
            continue

        org = org_defaults.get(b.kind, "")
        if org:
            rows.append(
                ResolutionRow(
                    kind=b.kind,
                    name=b.name,
                    resolved_variant=org,
                    source=ResolutionSource.ORG_DEFAULT,
                )
            )
            continue

        cluster = cluster_defaults.get(b.kind, "")
        if cluster:
            rows.append(
                ResolutionRow(
                    kind=b.kind,
                    name=b.name,
                    resolved_variant=cluster,
                    source=ResolutionSource.CLUSTER_DEFAULT,
                )
            )
            continue

        plugin = plugin_defaults.get(b.kind, "")
        if plugin:
            rows.append(
                ResolutionRow(
                    kind=b.kind,
                    name=b.name,
                    resolved_variant=plugin,
                    source=ResolutionSource.PLUGIN_DEFAULT,
                )
            )
            continue

        raise PortabilitySurfaceError(
            f"block {b.kind}:{b.name!r} can't be resolved — no pin and no default at any tier"
        )
    return tuple(rows)


# ---- 'make portable' diff ------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class MakePortableSuggestion:
    """One actionable change the UI / CLI shows the operator."""

    kind: str
    name: str
    current_pin: str
    suggested_change: str
    """Human-readable: 'remove `variant` line; rely on auto-resolution'."""

    caveat: str = ""
    """Reason this might be unsafe. UI surfaces in a tooltip."""


def make_portable_suggestions(
    *,
    blocks: Sequence[BlockResolution],
    has_portable_variant: dict[str, bool],
    plugin_specific_features: dict[str, tuple[str, ...]] = None,
) -> tuple[MakePortableSuggestion, ...]:
    """Spec §11: actionable diff for converting pinned blocks to
    abstract kinds.

    ``has_portable_variant``: per kind, whether a portable
    fallback exists in the catalog. False means 'no portable
    swap possible — would have to re-architect'.

    ``plugin_specific_features``: per pinned block, features it's
    using that the portable variant doesn't expose. UI uses these
    in the caveat string so the operator knows what they'd lose.
    """
    plugin_specific_features = plugin_specific_features or {}
    out: list[MakePortableSuggestion] = []
    for b in blocks:
        if not b.pin:
            continue
        if not has_portable_variant.get(b.kind, False):
            out.append(
                MakePortableSuggestion(
                    kind=b.kind,
                    name=b.name,
                    current_pin=b.pin,
                    suggested_change=(
                        f"no portable variant exists for {b.kind!r}; this kind is unavoidably plugin-specific"
                    ),
                    caveat="cannot swap without re-architecting",
                )
            )
            continue

        features = plugin_specific_features.get(f"{b.kind}:{b.name}", ())
        caveat = ""
        if features:
            caveat = "the portable variant doesn't expose: " + ", ".join(features)

        out.append(
            MakePortableSuggestion(
                kind=b.kind,
                name=b.name,
                current_pin=b.pin,
                suggested_change=(
                    f"remove the variant pin from {b.kind}:{b.name!r} to "
                    "let the platform auto-resolve (would resolve to a "
                    "portable variant when available)"
                ),
                caveat=caveat,
            )
        )
    return tuple(out)


# ---- 'where can I deploy' filter -----------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ClusterCompat:
    cluster_id: int
    cluster_slug: str
    available_variants: frozenset[str]
    """All ``<plugin>/<variant>`` strings the cluster's plugins
    expose. The set is computed by the catalog query layer."""


def deployable_clusters(
    *,
    blocks: Sequence[BlockResolution],
    candidates: Sequence[ClusterCompat],
    org_defaults: dict[str, str],
    plugin_defaults: dict[str, str],
) -> tuple[ClusterCompat, ...]:
    """Spec §11: 'Where can I deploy this?' Filter clusters that
    could resolve every block.

    Cluster-default tier isn't applicable here (we're asking
    about candidate clusters, not a chosen one); the relevant
    chain is manifest pin → org default → plugin default.
    """
    out: list[ClusterCompat] = []
    for c in candidates:
        compatible = True
        for b in blocks:
            need = b.pin or org_defaults.get(b.kind, "") or plugin_defaults.get(b.kind, "")
            if not need:
                # No way to resolve this block at all without a
                # cluster default; skip — not deployable anywhere
                # without per-cluster config.
                compatible = False
                break
            if need not in c.available_variants:
                compatible = False
                break
        if compatible:
            out.append(c)
    return tuple(out)


# ---- promote-check (env A → env B) ---------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PromoteCheckResult:
    """Output of the ``portability promote-check`` CLI command."""

    promotable: bool
    issues: tuple[str, ...]
    """Human-readable list of what's blocking promotion."""


def evaluate_promote(
    *,
    blocks: Sequence[BlockResolution],
    source_cluster: ClusterCompat,
    target_cluster: ClusterCompat,
) -> PromoteCheckResult:
    """Spec §11: can the manifest as-resolved-on-source-cluster
    be deployed on target-cluster without changes?

    Triggers refusal when source pinned to a plugin target doesn't
    have. Doesn't try to suggest fixes — that's `make-portable`'s
    job.
    """
    issues: list[str] = []
    for b in blocks:
        if not b.pin:
            # Auto-resolved; target picks its own variant.
            continue
        if b.pin not in source_cluster.available_variants:
            issues.append(
                f"{b.kind}:{b.name} pinned to {b.pin}, not "
                f"available on source cluster "
                f"{source_cluster.cluster_slug}"
            )
        if b.pin not in target_cluster.available_variants:
            issues.append(
                f"{b.kind}:{b.name} pinned to {b.pin}, not "
                f"available on target cluster "
                f"{target_cluster.cluster_slug}"
            )
    return PromoteCheckResult(
        promotable=not issues,
        issues=tuple(issues),
    )
