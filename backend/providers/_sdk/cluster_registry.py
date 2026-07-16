"""TenantCluster registration + connectivity validation (#17).

The control plane keeps a registry of tenant clusters: their
provider plugin id, location, declared capabilities, and last
successful connectivity probe. Apps bind to entries in this
registry by id, not by raw plugin config.

The probe runs at registration + on a schedule. It exercises the
plugin's ClusterDriver.get_namespace on a sentinel namespace
('astrolift-system') so the probe touches the same auth path
as a real workload deploy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from _sdk.availability import (
    MATRIX,
    REQUIRED_ROLES,
    AvailabilityMatrix,
)

if TYPE_CHECKING:
    from _sdk.base import ProviderPlugin
    from _sdk.composition import CompositionRegistry


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    last_probed_at: str
    """ISO-8601 UTC timestamp of the most recent probe."""

    message: str
    capabilities: list[str] = field(default_factory=list)
    """Driver roles confirmed reachable. Empty when ok=False."""


@dataclass(frozen=True)
class TenantClusterRecord:
    cluster_id: str
    plugin_id: str
    display_name: str
    location: str
    config: dict[str, Any] = field(default_factory=dict)
    """Plugin-specific config (region, account_id, etc.). The
    schema validates at register-time against PLUGIN.config_schema."""

    composition: CompositionRegistry | None = None
    last_probe: ProbeResult | None = None


@dataclass
class TenantClusterRegistry:
    """In-memory registry. Production wraps a Postgres table; the
    semantics are the same."""

    records: dict[str, TenantClusterRecord] = field(default_factory=dict)

    def register(
        self,
        *,
        cluster_id: str,
        plugin: ProviderPlugin,
        display_name: str,
        location: str,
        config: dict[str, Any] | None = None,
        composition: CompositionRegistry | None = None,
        matrix: AvailabilityMatrix = MATRIX,
    ) -> TenantClusterRecord:
        """Register a tenant cluster. Validates that the plugin
        + composition supplies every REQUIRED_ROLES driver — fails
        fast if a missing role would block any future bind."""
        missing: list[str] = []
        for role in REQUIRED_ROLES:
            if plugin.has_driver(role):
                continue
            if (
                composition is not None
                and composition.resolve_driver(
                    target_plugin_id=plugin.id,
                    role=role,
                )
                is not None
            ):
                continue
            if matrix.has_role(plugin_id=plugin.id, role=role):
                # Manifest drift — let the registration through
                # but flag it on probe.
                continue
            missing.append(role)
        if missing:
            raise ValueError(
                f"plugin {plugin.id!r} missing required roles "
                f"{sorted(missing)}; register a CompositionRegistry "
                f"that delegates them or pick a different plugin",
            )
        record = TenantClusterRecord(
            cluster_id=cluster_id,
            plugin_id=plugin.id,
            display_name=display_name,
            location=location,
            config=dict(config or {}),
            composition=composition,
        )
        self.records[cluster_id] = record
        return record

    def deregister(self, cluster_id: str) -> None:
        self.records.pop(cluster_id, None)

    def get(self, cluster_id: str) -> TenantClusterRecord | None:
        return self.records.get(cluster_id)

    def list(self) -> list[TenantClusterRecord]:
        return list(self.records.values())


def probe_connectivity(
    *,
    record: TenantClusterRecord,
    cluster_driver: Any,
    sentinel_namespace: str = "astrolift-system",
    now_iso: str | None = None,
) -> ProbeResult:
    """Run a connectivity probe against a registered cluster's
    apiserver via its ClusterDriver. The probe deliberately calls
    a read-only operation (get_namespace) so a stale credential
    doesn't accidentally mutate state.

    `now_iso` is injectable for tests; production passes
    datetime.now(tz=UTC).isoformat()."""
    from datetime import UTC, datetime

    timestamp = now_iso or datetime.now(tz=UTC).isoformat()
    try:
        cluster_driver.get_namespace(record.cluster_id, sentinel_namespace)
    except Exception as exc:
        return ProbeResult(
            ok=False,
            last_probed_at=timestamp,
            message=f"connectivity probe failed: {exc}",
        )
    return ProbeResult(
        ok=True,
        last_probed_at=timestamp,
        message="apiserver reachable",
        capabilities=list(REQUIRED_ROLES),
    )
