"""Postgres extension allow-lists + validation (#14, #77).

Each Postgres-variant has its own extension support set. RDS doesn't
ship the same extensions as Aurora; CNPG has the broadest list (it
runs on operator-provisioned containers, so any extension that
ships in the Postgres image works).

Allow-listing serves three purposes:
1. Reject app declarations early — if the app asks for `pg_cron` on
   RDS but RDS doesn't support it on the chosen instance class, fail
   the bind, not the deploy.
2. Document the supported set per variant for operator + AI tooling.
3. Enforce least-privilege at provision time — the platform creates
   the extension via SUPERUSER, and only on the allow-list.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtensionEntry:
    name: str
    """Postgres extension name as accepted by CREATE EXTENSION."""

    description: str = ""
    requires_shared_preload_libraries: bool = False
    """If True, the extension must be in shared_preload_libraries +
    a restart is required. Drivers translate this to the per-variant
    parameter-group / configuration patch."""

    minimum_postgres_version: int = 13
    """Major version. e.g. 13, 14, 15, 16."""


@dataclass(frozen=True)
class VariantExtensionPolicy:
    plugin_id: str
    variant: str
    """e.g. 'rds', 'aurora', 'cloudsql', 'flexible_server', 'cnpg'."""

    allow: tuple[ExtensionEntry, ...] = ()
    deny: tuple[str, ...] = ()
    """Extensions that exist in `allow` but should be rejected in
    a particular fleet (security policy override). Drivers can
    surface this so operators see *why* an extension is rejected."""

    def supports(self, name: str) -> bool:
        if name in self.deny:
            return False
        return any(e.name == name for e in self.allow)

    def get(self, name: str) -> ExtensionEntry | None:
        for e in self.allow:
            if e.name == name:
                return e
        return None


# Common extensions — referenced from each variant's allow list.
PGCRYPTO = ExtensionEntry(
    name="pgcrypto",
    description="Cryptographic primitives (gen_random_uuid, crypt)",
)
UUID_OSSP = ExtensionEntry(
    name="uuid-ossp",
    description="UUID generation (legacy uuid_generate_v4)",
)
PG_TRGM = ExtensionEntry(
    name="pg_trgm",
    description="Trigram-based string similarity + GIN indexes",
)
HSTORE = ExtensionEntry(
    name="hstore",
    description="Key-value pair storage type",
)
CITEXT = ExtensionEntry(
    name="citext",
    description="Case-insensitive text type",
)
PG_STAT_STATEMENTS = ExtensionEntry(
    name="pg_stat_statements",
    description="Query-level statistics view",
    requires_shared_preload_libraries=True,
)
PG_CRON = ExtensionEntry(
    name="pg_cron",
    description="Cron-style scheduled jobs in Postgres",
    requires_shared_preload_libraries=True,
)
PG_PARTMAN = ExtensionEntry(
    name="pg_partman",
    description="Time/serial-range partition management",
)
PGVECTOR = ExtensionEntry(
    name="vector",
    description="pgvector — embeddings + ANN search",
    minimum_postgres_version=14,
)
POSTGIS = ExtensionEntry(
    name="postgis",
    description="Spatial / geographic data types",
)
TIMESCALEDB = ExtensionEntry(
    name="timescaledb",
    description="Time-series hypertables + continuous aggregates",
    requires_shared_preload_libraries=True,
)


# Per-variant policies. The k8s_native (CNPG) entry is the most
# permissive because it runs custom Postgres images; cloud-managed
# variants gate on what the cloud's parameter-group surface allows.
POLICIES: tuple[VariantExtensionPolicy, ...] = (
    VariantExtensionPolicy(
        plugin_id="aws",
        variant="rds",
        allow=(
            PGCRYPTO,
            UUID_OSSP,
            PG_TRGM,
            HSTORE,
            CITEXT,
            PG_STAT_STATEMENTS,
            PG_PARTMAN,
            PGVECTOR,
            POSTGIS,
        ),
    ),
    VariantExtensionPolicy(
        plugin_id="aws",
        variant="aurora",
        allow=(
            PGCRYPTO,
            UUID_OSSP,
            PG_TRGM,
            HSTORE,
            CITEXT,
            PG_STAT_STATEMENTS,
            PG_PARTMAN,
            PGVECTOR,
            POSTGIS,
        ),
    ),
    VariantExtensionPolicy(
        plugin_id="gcp",
        variant="cloudsql",
        allow=(
            PGCRYPTO,
            UUID_OSSP,
            PG_TRGM,
            HSTORE,
            CITEXT,
            PG_STAT_STATEMENTS,
            PG_PARTMAN,
            PGVECTOR,
            POSTGIS,
        ),
    ),
    VariantExtensionPolicy(
        plugin_id="gcp",
        variant="alloydb",
        allow=(
            PGCRYPTO,
            UUID_OSSP,
            PG_TRGM,
            HSTORE,
            CITEXT,
            PG_STAT_STATEMENTS,
            PGVECTOR,
            POSTGIS,
        ),
    ),
    VariantExtensionPolicy(
        plugin_id="azure",
        variant="flexible_server",
        allow=(
            PGCRYPTO,
            UUID_OSSP,
            PG_TRGM,
            HSTORE,
            CITEXT,
            PG_STAT_STATEMENTS,
            PG_PARTMAN,
            PGVECTOR,
            POSTGIS,
            PG_CRON,
        ),
    ),
    VariantExtensionPolicy(
        plugin_id="k8s_native",
        variant="cnpg",
        # CNPG runs operator-provisioned containers — anything the
        # image ships works. Allow the full common set.
        allow=(
            PGCRYPTO,
            UUID_OSSP,
            PG_TRGM,
            HSTORE,
            CITEXT,
            PG_STAT_STATEMENTS,
            PG_CRON,
            PG_PARTMAN,
            PGVECTOR,
            POSTGIS,
            TIMESCALEDB,
        ),
    ),
)


@dataclass(frozen=True)
class ExtensionValidationResult:
    ok: bool
    rejected: list[str] = field(default_factory=list)
    needs_restart: list[str] = field(default_factory=list)
    """Extensions in shared_preload_libraries — provisioner must
    schedule a restart after CREATE EXTENSION."""


def policy_for(
    *,
    plugin_id: str,
    variant: str,
) -> VariantExtensionPolicy | None:
    for p in POLICIES:
        if p.plugin_id == plugin_id and p.variant == variant:
            return p
    return None


def validate_extensions(
    *,
    plugin_id: str,
    variant: str,
    requested: list[str],
) -> ExtensionValidationResult:
    """Check that every requested extension is on the variant's
    allow list. Returns the rejected names + the subset that needs
    a Postgres restart after CREATE EXTENSION."""
    policy = policy_for(plugin_id=plugin_id, variant=variant)
    if policy is None:
        # Unknown variant — fail closed: reject everything so
        # operator notices the missing policy.
        return ExtensionValidationResult(
            ok=False,
            rejected=list(requested),
        )
    rejected: list[str] = []
    needs_restart: list[str] = []
    for name in requested:
        if not policy.supports(name):
            rejected.append(name)
            continue
        entry = policy.get(name)
        if entry is not None and entry.requires_shared_preload_libraries:
            needs_restart.append(name)
    return ExtensionValidationResult(
        ok=not rejected,
        rejected=rejected,
        needs_restart=needs_restart,
    )
