"""Managed-service plugin SDK -- adding new kinds (#16).

The set of kinds shipped today (postgres, redis, object_store,
queue) covers the MVP. New kinds (vector_db, search, kafka, etc.)
land via this SDK — a plugin declares the kind's required binding
envs + runtime expectations, and the matrix-check picks up new
entries on the next CI run.

This file deliberately keeps the kind definitions in code (typed,
immutable) rather than data; that lets the matrix check (#26)
verify shape and the binding validator (#18) call out missing
envs at validate time.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ManagedServiceKind:
    name: str
    """The kind identifier (e.g. 'postgres', 'queue', 'vector_db')."""

    description: str
    binding_envs_required: tuple[str, ...] = ()
    """Env-var keys every variant of this kind MUST set. Example:
    every postgres variant must produce POSTGRES_HOST + POSTGRES_PORT
    + POSTGRES_DB + POSTGRES_USER + POSTGRES_PASSWORD."""

    binding_envs_optional: tuple[str, ...] = ()
    iam_action_pattern: str = ""
    """Free-text description of expected IAM grant. Used in docs +
    least-privilege audit."""

    snapshot_supported: bool = False
    cross_region_replicate_supported: bool = False


@dataclass(frozen=True)
class KindCatalog:
    kinds: tuple[ManagedServiceKind, ...] = ()

    def get(self, name: str) -> ManagedServiceKind | None:
        for kind in self.kinds:
            if kind.name == name:
                return kind
        return None


# Canonical kind catalog. New kinds land here at the same time as
# their first variant's plugin registration.
KINDS = KindCatalog(kinds=(
    ManagedServiceKind(
        name="postgres",
        description="Relational database (Postgres-compatible)",
        binding_envs_required=(
            "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
            "POSTGRES_USER", "POSTGRES_PASSWORD",
        ),
        binding_envs_optional=("POSTGRES_SSLMODE",),
        snapshot_supported=True,
        cross_region_replicate_supported=True,
    ),
    ManagedServiceKind(
        name="redis",
        description="Key-value store (Redis-compatible)",
        binding_envs_required=("REDIS_HOST", "REDIS_PORT"),
        binding_envs_optional=("REDIS_PASSWORD", "REDIS_TLS"),
        snapshot_supported=True,
    ),
    ManagedServiceKind(
        name="object_store",
        description="Blob / object storage (bucket-shaped)",
        snapshot_supported=True,
        cross_region_replicate_supported=True,
    ),
    ManagedServiceKind(
        name="queue",
        description="Message queue (FIFO or best-effort)",
    ),
))


def validate_binding_envs(
    *,
    kind: str,
    emitted_envs: list[str],
    catalog: KindCatalog = KINDS,
) -> list[str]:
    """Returns the list of MISSING required envs for this kind.
    Empty list means the binding meets the kind contract."""
    record = catalog.get(kind)
    if record is None:
        return []
    return [
        env for env in record.binding_envs_required
        if env not in emitted_envs
    ]
