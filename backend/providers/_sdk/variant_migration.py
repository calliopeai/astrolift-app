"""Variant migration workflow for deprecated variants (#76).

When a managed-service variant is deprecated (e.g., AWS DocumentDB
moves an app from `mongo:3.6` to `mongo:5.0`), the platform needs
a structured migration plan: source variant → target variant, the
mode of migration (cutover / rolling / blue-green / dump-restore),
the pre-flight requirements, and the rollback contract.

This module declares MigrationRecipe — the typed plan a driver or
operator-defined policy can return when asked "how do I move from
X to Y?" Concrete migration execution lives in workflows; this is
the catalog + planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


MigrationMode = Literal[
    "cutover",          # stop, snapshot, restore on new variant, restart
    "rolling",          # gradual replacement (statefulset rolling)
    "blue_green",       # parallel deploy, switch traffic, decommission
    "dump_restore",     # logical dump → load → cut over
    "in_place_upgrade", # provider performs migration; we just wait
]


@dataclass(frozen=True)
class MigrationRequirement:
    code: str
    description: str
    """e.g. 'incoming connections paused', 'pgvector ext on target',
    'volume snapshot completed within 1h'."""


@dataclass(frozen=True)
class MigrationRecipe:
    kind: str
    source_variant: str
    target_variant: str
    mode: MigrationMode
    expected_downtime_seconds: int
    """0 means zero-downtime."""

    rollback_supported: bool
    rollback_window_seconds: int = 0
    """How long after cutover the operator can roll back without
    data loss. 0 means rollback only via dump/restore from snapshot."""

    pre_flight: tuple[MigrationRequirement, ...] = ()
    post_flight: tuple[MigrationRequirement, ...] = ()
    notes: tuple[str, ...] = ()
    """Free-text caveats; surfaces in the operator preview UI."""


@dataclass(frozen=True)
class MigrationCatalog:
    recipes: tuple[MigrationRecipe, ...] = ()

    def find(
        self, *, kind: str, source: str, target: str,
    ) -> MigrationRecipe | None:
        for r in self.recipes:
            if (
                r.kind == kind and r.source_variant == source
                and r.target_variant == target
            ):
                return r
        return None

    def options_for(
        self, *, kind: str, source: str,
    ) -> list[MigrationRecipe]:
        return [
            r for r in self.recipes
            if r.kind == kind and r.source_variant == source
        ]


# Common requirements reused across recipes.
APP_TRAFFIC_PAUSED = MigrationRequirement(
    code="app_traffic_paused",
    description=(
        "App traffic must be paused (deployment scaled to 0 or "
        "ingress drained) so source DB has no in-flight writes."
    ),
)
SOURCE_BACKUP_FRESH = MigrationRequirement(
    code="source_backup_fresh",
    description=(
        "Recent (≤24h) full backup of source DB must exist so a "
        "rollback to source is possible."
    ),
)
TARGET_PROVISIONED = MigrationRequirement(
    code="target_provisioned",
    description=(
        "Target variant fully provisioned + reachable from "
        "migration runner; binding envs resolvable."
    ),
)
EXTENSIONS_ON_TARGET = MigrationRequirement(
    code="extensions_on_target",
    description=(
        "All Postgres extensions used by the app must be allow-"
        "listed on the target variant (#14, #77)."
    ),
)


# Catalog of supported migrations. New deprecations land here at
# the same time as the variant-deprecation announcement.
RECIPES = MigrationCatalog(recipes=(
    # AWS RDS Postgres → Aurora Postgres (in-place via snapshot)
    MigrationRecipe(
        kind="postgres",
        source_variant="rds",
        target_variant="aurora",
        mode="cutover",
        expected_downtime_seconds=300,
        rollback_supported=True,
        rollback_window_seconds=86_400,
        pre_flight=(
            APP_TRAFFIC_PAUSED,
            SOURCE_BACKUP_FRESH,
            TARGET_PROVISIONED,
            EXTENSIONS_ON_TARGET,
        ),
        notes=(
            "Use Aurora's create-from-RDS-snapshot path. "
            "Endpoint URL changes — bindings refresh required.",
        ),
    ),
    # CNPG (k8s) → AWS RDS (operator-driven dump/restore)
    MigrationRecipe(
        kind="postgres",
        source_variant="cnpg",
        target_variant="rds",
        mode="dump_restore",
        expected_downtime_seconds=900,
        rollback_supported=True,
        rollback_window_seconds=86_400,
        pre_flight=(
            APP_TRAFFIC_PAUSED,
            SOURCE_BACKUP_FRESH,
            TARGET_PROVISIONED,
            EXTENSIONS_ON_TARGET,
        ),
        notes=(
            "pg_dump on CNPG primary → pg_restore on RDS. "
            "Long downtime; consider logical replication for "
            "large datasets.",
        ),
    ),
    # AWS RDS → CNPG (re-platform)
    MigrationRecipe(
        kind="postgres",
        source_variant="rds",
        target_variant="cnpg",
        mode="dump_restore",
        expected_downtime_seconds=900,
        rollback_supported=True,
        rollback_window_seconds=86_400,
        pre_flight=(
            APP_TRAFFIC_PAUSED,
            SOURCE_BACKUP_FRESH,
            TARGET_PROVISIONED,
        ),
    ),
    # Redis ElastiCache → CNPG (operator-style Redis)
    MigrationRecipe(
        kind="redis",
        source_variant="elasticache",
        target_variant="operator",
        mode="cutover",
        expected_downtime_seconds=60,
        rollback_supported=False,
        notes=(
            "Redis caches typically have no canonical state; "
            "rebuild after cutover is acceptable.",
        ),
    ),
    # Object store: cross-cloud migration via dual-write
    MigrationRecipe(
        kind="object_store",
        source_variant="s3",
        target_variant="gcs",
        mode="blue_green",
        expected_downtime_seconds=0,
        rollback_supported=True,
        rollback_window_seconds=0,
        pre_flight=(TARGET_PROVISIONED,),
        notes=(
            "Cross-cloud copy via storage-transfer-service or "
            "rclone. Dual-write during transition; switch reads "
            "atomically.",
        ),
    ),
))


def variants_with_migration_path(
    *, kind: str, source: str,
    catalog: MigrationCatalog = RECIPES,
) -> list[str]:
    """List the target variants reachable from source via the
    catalog. Useful for the deprecation-banner UI ('migrate to
    one of: aurora, cnpg')."""
    return sorted({
        r.target_variant for r in catalog.options_for(
            kind=kind, source=source,
        )
    })
