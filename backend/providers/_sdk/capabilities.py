"""Capability negotiation and app-to-cluster binding validation (#18).

When the control plane binds an app to a tenant cluster, it must
verify two things:
1. The cluster's plugin (or its delegation graph) supplies every
   role in REQUIRED_ROLES — without these, basic deploy can't work.
2. Every managed-service the app declares as a dependency exists
   on the cluster's plugin (or has a delegated provider).

This module returns a structured `BindingValidation` so the API
layer can emit precise per-issue feedback (which role is missing,
which managed-service kind+variant isn't shipped) instead of a
single boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from _sdk.availability import (
    AvailabilityMatrix,
    REQUIRED_ROLES,
)
from _sdk.base import ProviderPlugin
from _sdk.composition import CompositionRegistry


@dataclass(frozen=True)
class ServiceDependency:
    """An app's declared dependency on a managed-service kind."""

    kind: str
    variant: str
    handle_hint: str = ""
    required: bool = True


@dataclass(frozen=True)
class BindingIssue:
    code: str
    message: str
    role: str | None = None
    kind: str | None = None
    variant: str | None = None


@dataclass(frozen=True)
class BindingValidation:
    ok: bool
    issues: list[BindingIssue] = field(default_factory=list)
    """Empty when ok=True. Otherwise enumerates every gap."""


def validate_cluster_binding(
    *,
    plugin: ProviderPlugin,
    matrix: AvailabilityMatrix,
    composition: CompositionRegistry | None = None,
    service_deps: list[ServiceDependency] | None = None,
) -> BindingValidation:
    """Validate that `plugin` can host an app with the given
    `service_deps`. The matrix is the source of truth for what
    each plugin advertises; the live PLUGIN.drivers / .managed_*
    are checked too in case manifests + matrix drift.

    `composition` lets a plugin delegate roles or managed-service
    variants to another plugin's drivers (e.g., AWS-on-EKS routing
    ('postgres', 'cnpg') to k8s_native CNPGPostgresDriver).
    """
    issues: list[BindingIssue] = []
    deps = list(service_deps or [])

    for role in REQUIRED_ROLES:
        if plugin.has_driver(role):
            continue
        if (
            composition is not None
            and composition.resolve_driver(
                target_plugin_id=plugin.id, role=role,
            ) is not None
        ):
            continue
        if matrix.has_role(plugin_id=plugin.id, role=role):
            # Matrix advertises it but plugin manifest doesn't —
            # surface as a manifest drift bug.
            issues.append(BindingIssue(
                code="manifest_matrix_drift",
                role=role,
                message=(
                    f"matrix lists role={role!r} for plugin "
                    f"{plugin.id!r} but the manifest doesn't "
                    f"register a driver"
                ),
            ))
            continue
        issues.append(BindingIssue(
            code="missing_required_role",
            role=role,
            message=(
                f"plugin {plugin.id!r} doesn't ship a driver for "
                f"required role {role!r} and no composition "
                f"delegation is registered"
            ),
        ))

    for dep in deps:
        if plugin.get_managed_service_driver(dep.kind, dep.variant) is not None:
            continue
        if (
            composition is not None
            and composition.resolve_managed_service(
                target_plugin_id=plugin.id,
                kind=dep.kind,
                variant=dep.variant,
            ) is not None
        ):
            continue
        if dep.required:
            issues.append(BindingIssue(
                code="missing_managed_service",
                kind=dep.kind,
                variant=dep.variant,
                message=(
                    f"app requires {dep.kind}/{dep.variant} but "
                    f"plugin {plugin.id!r} doesn't ship that "
                    f"variant and no composition delegation is "
                    f"registered"
                ),
            ))

    return BindingValidation(ok=not issues, issues=issues)


def negotiate_variant(
    *,
    plugin: ProviderPlugin,
    matrix: AvailabilityMatrix,
    kind: str,
    preferred_variants: list[str],
) -> str | None:
    """Pick the first variant from `preferred_variants` that the
    plugin actually ships. Returns None if no preference matches —
    callers fall back to the plugin's default or fail the bind."""
    for variant in preferred_variants:
        if plugin.get_managed_service_driver(kind, variant) is not None:
            return variant
        if matrix.has_managed(plugin_id=plugin.id, kind=kind, variant=variant):
            return variant
    return None
