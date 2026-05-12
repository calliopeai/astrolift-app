"""
Variant resolution engine (#55, spec 20 §4).

A managed-service ``kind`` (postgres / redis / queue / …) is an
abstract reference. Each cluster's plugin exposes one or more
concrete *variants* — implementations like
``rds/aurora-postgres-15`` or ``cloudsql/postgres-15``. The
resolver picks one variant for each (manifest binding, cluster)
pair at provisioning time.

Resolution order (later sources override earlier):

  1. Manifest pin: ``[[managed_services]] variant = "<plugin>/<variant>"``
     — used as-is, reject if the cluster's plugin doesn't expose it
     (typo or wrong cluster).
  2. Org policy override:
     ``Organization.service_kind_defaults[kind]`` pins a variant for
     a kind across every cluster in the org.
  3. Cluster default for the kind:
     ``TenantCluster.provider_config["service_kind_defaults"][kind]``.
  4. Plugin default: a variant flagged ``is_default_for_kind=True``
     for that kind on the plugin's manifest.
  5. No resolution → :class:`UnresolvedVariant` with a clear message
     ("kind 'postgres' cannot be satisfied on cluster 'prod-east'
     (plugin 'aws-rds' exposes no variant)").

The resolver is pure — callers (the provisioning activity) pass in
the org/cluster/plugin shapes as plain dicts so this module doesn't
have to import from astrolift_clusters / astrolift_identity. Same
inputs → same output.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any


class UnresolvedVariant(ValueError):
    """No variant resolved for a (kind, cluster) pair. Carries the
    kind + cluster slug so the activity surfaces a useful error."""

    def __init__(self, kind: str, cluster_slug: str, plugin_slug: str = ""):
        self.kind = kind
        self.cluster_slug = cluster_slug
        self.plugin_slug = plugin_slug
        suffix = f" (plugin {plugin_slug!r} exposes no variant)" if plugin_slug else ""
        super().__init__(f"kind {kind!r} cannot be satisfied on cluster " f"{cluster_slug!r}{suffix}")


# Sentinel labels recorded alongside the resolved variant for the
# audit log + UI ('Why did we pick rds/aurora?').
SOURCE_MANIFEST_PIN = "manifest_pin"
SOURCE_ORG_POLICY = "org_policy"
SOURCE_CLUSTER_DEFAULT = "cluster_default"
SOURCE_PLUGIN_DEFAULT = "plugin_default"


@dataclasses.dataclass(frozen=True, slots=True)
class PluginVariant:
    """A concrete variant exposed by a provider plugin."""

    plugin_slug: str
    variant: str
    kind: str
    is_default_for_kind: bool = False

    @property
    def fqn(self) -> str:
        """``<plugin_slug>/<variant>`` — the manifest's pin format."""
        return f"{self.plugin_slug}/{self.variant}"


@dataclasses.dataclass(frozen=True, slots=True)
class ResolvedVariant:
    """The output of the resolver: which variant won, and why."""

    plugin_slug: str
    variant: str
    kind: str
    source: str

    @property
    def fqn(self) -> str:
        return f"{self.plugin_slug}/{self.variant}"


def _parse_pin(pin: str) -> tuple[str, str] | None:
    """``"<plugin>/<variant>"`` → (plugin, variant). None on malformed."""
    if not pin or "/" not in pin:
        return None
    plugin, _, variant = pin.partition("/")
    if not plugin or not variant:
        return None
    return plugin, variant


def _find_variant(
    plugin_variants: list[PluginVariant], plugin_slug: str, variant: str
) -> PluginVariant | None:
    for v in plugin_variants:
        if v.plugin_slug == plugin_slug and v.variant == variant:
            return v
    return None


def resolve_variant(
    *,
    kind: str,
    manifest_pin: str | None,
    org_service_defaults: Mapping[str, str] | None,
    cluster_service_defaults: Mapping[str, str] | None,
    cluster_slug: str,
    plugin_slug: str,
    plugin_variants: list[PluginVariant],
) -> ResolvedVariant:
    """Walk the priority chain and return the resolved variant.

    Raises :class:`UnresolvedVariant` when no source produces a
    variant the cluster's plugin actually exposes.

    ``plugin_variants`` is the catalogue the cluster's plugin
    publishes — the resolver validates every chosen pin against it
    so a typo in org_policy doesn't silently slip through.
    """
    # 1. Manifest pin
    if manifest_pin:
        parsed = _parse_pin(manifest_pin)
        if parsed is None:
            raise UnresolvedVariant(kind=kind, cluster_slug=cluster_slug, plugin_slug=plugin_slug)
        pinned_plugin, pinned_variant = parsed
        match = _find_variant(plugin_variants, pinned_plugin, pinned_variant)
        if match is None or match.kind != kind:
            raise UnresolvedVariant(kind=kind, cluster_slug=cluster_slug, plugin_slug=plugin_slug)
        return ResolvedVariant(
            plugin_slug=match.plugin_slug,
            variant=match.variant,
            kind=match.kind,
            source=SOURCE_MANIFEST_PIN,
        )

    # 2. Org policy override (string is "<plugin>/<variant>")
    if org_service_defaults and kind in org_service_defaults:
        parsed = _parse_pin(org_service_defaults[kind])
        if parsed is not None:
            match = _find_variant(plugin_variants, *parsed)
            if match is not None and match.kind == kind:
                return ResolvedVariant(
                    plugin_slug=match.plugin_slug,
                    variant=match.variant,
                    kind=match.kind,
                    source=SOURCE_ORG_POLICY,
                )

    # 3. Cluster default for the kind
    if cluster_service_defaults and kind in cluster_service_defaults:
        parsed = _parse_pin(cluster_service_defaults[kind])
        if parsed is not None:
            match = _find_variant(plugin_variants, *parsed)
            if match is not None and match.kind == kind:
                return ResolvedVariant(
                    plugin_slug=match.plugin_slug,
                    variant=match.variant,
                    kind=match.kind,
                    source=SOURCE_CLUSTER_DEFAULT,
                )

    # 4. Plugin default for the kind
    for v in plugin_variants:
        if v.kind == kind and v.is_default_for_kind:
            return ResolvedVariant(
                plugin_slug=v.plugin_slug,
                variant=v.variant,
                kind=v.kind,
                source=SOURCE_PLUGIN_DEFAULT,
            )

    # 5. No resolution
    raise UnresolvedVariant(kind=kind, cluster_slug=cluster_slug, plugin_slug=plugin_slug)


# ---- helper for callers wiring the platform models in ----------------


def variants_from_plugin_manifest(
    plugin_slug: str, capabilities_manifest: Mapping[str, Any]
) -> list[PluginVariant]:
    """Convert a plugin's stored ``capabilities_manifest`` into a list
    of ``PluginVariant``.

    Expected shape (per spec 20 §3):
    ``{ "variants": [ { "kind": "postgres", "variant": "aurora-15",
                        "is_default_for_kind": true }, ... ] }``

    Tolerant of missing keys — anything malformed is skipped so a
    poorly-authored manifest fails resolution at the call site
    instead of crashing the loader. Callers can iterate
    capabilities_manifest themselves to surface the bad entries.
    """
    out: list[PluginVariant] = []
    for entry in capabilities_manifest.get("variants", []) or []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        variant = entry.get("variant")
        if not kind or not variant:
            continue
        out.append(
            PluginVariant(
                plugin_slug=plugin_slug,
                variant=str(variant),
                kind=str(kind),
                is_default_for_kind=bool(entry.get("is_default_for_kind", False)),
            )
        )
    return out
