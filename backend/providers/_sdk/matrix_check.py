"""CI matrix consistency check (#26).

Walks every PLUGIN registered via the `astrolift.providers`
entry-point and confirms the availability matrix has a matching
entry for each driver + managed-service. The reverse direction
also holds: every matrix entry must have a real PLUGIN behind it.

Drift is a deploy-time bug — silent omission of a matrix entry
hides a capability from the bind validator; a stale matrix entry
makes the validator green-light bindings that fail at runtime.

Designed as a pure function so test_matrix_check.py can run it
against the live registry without touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points

from _sdk.availability import MATRIX, AvailabilityMatrix
from _sdk.base import ProviderPlugin


@dataclass(frozen=True)
class DriftIssue:
    code: str
    plugin_id: str
    detail: str


@dataclass(frozen=True)
class MatrixCheckReport:
    ok: bool
    issues: list[DriftIssue] = field(default_factory=list)


def load_plugins() -> list[ProviderPlugin]:
    """Discover all registered plugins. Returns the live PLUGIN
    constants in the order entry-points yield them."""
    eps = entry_points(group="astrolift.providers")
    out: list[ProviderPlugin] = []
    for ep in eps:
        loaded = ep.load()
        if isinstance(loaded, ProviderPlugin):
            out.append(loaded)
    return out


def check_matrix(
    *,
    plugins: list[ProviderPlugin] | None = None,
    matrix: AvailabilityMatrix = MATRIX,
) -> MatrixCheckReport:
    if plugins is None:
        plugins = load_plugins()
    issues: list[DriftIssue] = []

    # forward direction: every PLUGIN driver/managed-service must
    # appear in the matrix
    for plugin in plugins:
        for role in plugin.drivers:
            if not matrix.has_role(plugin_id=plugin.id, role=role):
                issues.append(
                    DriftIssue(
                        code="manifest_not_in_matrix",
                        plugin_id=plugin.id,
                        detail=(f"plugin manifest registers role={role!r} but the matrix has no entry"),
                    )
                )
        for kind, variant in plugin.managed_service_drivers:
            if not matrix.has_managed(
                plugin_id=plugin.id,
                kind=kind,
                variant=variant,
            ):
                issues.append(
                    DriftIssue(
                        code="manifest_not_in_matrix",
                        plugin_id=plugin.id,
                        detail=(
                            f"plugin manifest registers "
                            f"managed_service ({kind!r}, {variant!r}) "
                            f"but the matrix has no entry"
                        ),
                    )
                )

    # reverse direction: every matrix entry must back a real PLUGIN
    plugin_index = {p.id: p for p in plugins}
    for entry in matrix.drivers:
        plugin = plugin_index.get(entry.plugin_id)
        if plugin is None:
            issues.append(
                DriftIssue(
                    code="matrix_unknown_plugin",
                    plugin_id=entry.plugin_id,
                    detail=(
                        f"matrix lists role={entry.role!r} under plugin "
                        f"{entry.plugin_id!r} but no such plugin is "
                        f"registered via the entry-point group"
                    ),
                )
            )
            continue
        if entry.role not in plugin.drivers:
            issues.append(
                DriftIssue(
                    code="matrix_not_in_manifest",
                    plugin_id=entry.plugin_id,
                    detail=(
                        f"matrix lists role={entry.role!r} for plugin "
                        f"{entry.plugin_id!r} but the manifest doesn't "
                        f"register a driver for it"
                    ),
                )
            )
    for entry in matrix.managed_services:
        plugin = plugin_index.get(entry.plugin_id)
        if plugin is None:
            issues.append(
                DriftIssue(
                    code="matrix_unknown_plugin",
                    plugin_id=entry.plugin_id,
                    detail=(
                        f"matrix lists ({entry.kind!r}, {entry.variant!r}) "
                        f"under plugin {entry.plugin_id!r} but no such "
                        f"plugin is registered via the entry-point group"
                    ),
                )
            )
            continue
        if (
            entry.kind,
            entry.variant,
        ) not in plugin.managed_service_drivers:
            issues.append(
                DriftIssue(
                    code="matrix_not_in_manifest",
                    plugin_id=entry.plugin_id,
                    detail=(
                        f"matrix lists managed_service "
                        f"({entry.kind!r}, {entry.variant!r}) for "
                        f"plugin {entry.plugin_id!r} but the manifest "
                        f"doesn't register it"
                    ),
                )
            )

    return MatrixCheckReport(ok=not issues, issues=issues)
