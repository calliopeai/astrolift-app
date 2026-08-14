"""
Env-var injection with precedence + provenance (#117).

The platform composes the env passed into a container from up to six
sources. Per spec 05 §10, later sources override earlier:

  1. App-wide ``[env]`` literals
  2. App-level secret bundles (``AppSecretBundleRef``)
  3. Managed-service connection envelopes (per kind, see below)
  4. Workload-level env (reserved for future overrides)
  5. Container-level ``[workloads.containers.env]`` literals
  6. Container-level ``env_from`` references (with optional prefix)

This module is the merge engine. It returns both the resolved env
dict and a per-key provenance map that we snapshot onto the
Deployment row — so when a user wonders "where did MY_VAR come
from?", the answer is one query away.

Connection envelopes per managed service kind: for each binding,
emit a stable set of keys. When two bindings share a kind, the
second is prefixed with its uppercased manifest name to avoid the
collision (e.g. ``CACHE_REDIS_HOST`` for ``name = "cache"``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping

# Source labels used in the provenance map. Values are stable so
# UI / audit log can carry them around safely; keep them sorted by
# precedence (earliest → latest, matching the merge order).
SOURCE_APP_LITERAL = "app.env"
SOURCE_APP_SECRET_BUNDLE = "app.secret_bundle"
SOURCE_MANAGED_SERVICE = "managed_service"
SOURCE_WORKLOAD_LITERAL = "workload.env"
SOURCE_CONTAINER_LITERAL = "container.env"
SOURCE_CONTAINER_ENV_FROM = "container.env_from"


# Connection envelopes the platform auto-injects per managed
# service kind. Stable contract — the values themselves come from
# the binding's ``connection_secret``, but the *key set* is fixed
# so consumer code can rely on ``DATABASE_URL`` always being set
# when there's a postgres binding.
# Spec 11 §6.1 - §6.16 — full envelope catalogue. Workloads can rely
# on these key sets being present whenever the matching kind is bound,
# regardless of which provider variant provisioned the service.
_ENVELOPES: dict[str, tuple[str, ...]] = {
    "postgres": (
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_SSL_MODE",
        "DATABASE_URL",
        "POSTGRES_MASTER_SECRET_REF",
    ),
    "mysql": (
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MYSQL_DB",
        "MYSQL_USER",
        "MYSQL_PASSWORD",
        "DATABASE_URL",
    ),
    "mssql": (
        "MSSQL_HOST",
        "MSSQL_PORT",
        "MSSQL_DB",
        "MSSQL_USER",
        "MSSQL_PASSWORD",
        "MSSQL_ENCRYPT",
        "DATABASE_URL",
    ),
    "redis": (
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_USER",
        "REDIS_PASSWORD",
        "REDIS_TLS",
        "REDIS_URL",
        "REDIS_AUTH_MODE",
        "REDIS_RESOURCE_ARN",
    ),
    "cache": (
        "CACHE_HOST",
        "CACHE_PORT",
        "CACHE_PROTOCOL",
        "CACHE_NODES",
        "CACHE_TLS",
        "CACHE_RESOURCE_ARN",
    ),
    "mq": (
        "MQ_ENDPOINT",
        "MQ_USERNAME",
        "MQ_PASSWORD",
        "MQ_PROTOCOL",
        "KAFKA_BOOTSTRAP_SERVERS",
        "KAFKA_SECURITY_PROTOCOL",
        "KAFKA_SASL_MECHANISM",
        "KAFKA_SASL_USERNAME",
        "KAFKA_SASL_PASSWORD",
        "KAFKA_TOPIC_PREFIX",
    ),
    "queue": (
        "QUEUE_URL",
        "QUEUE_ARN_OR_ID",
        "QUEUE_NAME",
        "QUEUE_REGION",
    ),
    "topic": (
        "TOPIC_ARN_OR_ID",
        "TOPIC_NAME",
        "TOPIC_REGION",
    ),
    "event_stream": (
        "EVENT_STREAM_BROKERS",
        "EVENT_STREAM_USERNAME",
        "EVENT_STREAM_PASSWORD",
        "EVENT_STREAM_TLS",
        "EVENT_STREAM_TOPIC_PREFIX",
    ),
    "kv_store": (
        "KV_TABLE_NAME",
        "KV_PARTITION_KEY",
        "KV_SORT_KEY",
        "KV_REGION",
    ),
    "document_db": (
        "DOCDB_URI",
        "DOCDB_DB",
        "DOCDB_USER",
        "DOCDB_PASSWORD",
        "DOCDB_TLS",
        "DOCDB_RESOURCE_ARN",
    ),
    "search": (
        "SEARCH_ENDPOINT",
        "SEARCH_USER",
        "SEARCH_PASSWORD",
        "SEARCH_INDEX_PREFIX",
    ),
    "vector_index": (
        "VECTOR_ENDPOINT",
        "VECTOR_API_KEY",
        "VECTOR_INDEX_NAME",
        "VECTOR_NAMESPACE",
    ),
    "time_series": (
        "TS_ENDPOINT",
        "TS_DB",
        "TS_USER",
        "TS_PASSWORD",
        "TS_TOKEN",
        "TS_ORG",
    ),
    "object_store": (
        "BUCKET_NAME",
        "BUCKET_REGION",
        "BUCKET_ENDPOINT",
        "BUCKET_PREFIX",
    ),
    "nfs": ("NFS_VOLUME",),
    "filesystem": (
        "FILESYSTEM_HANDLE",
        "FILESYSTEM_MOUNT_PATH",
        "FILESYSTEM_TLS",
    ),
    "cdn": (
        "CDN_DISTRIBUTION_ID",
        "CDN_DOMAIN_NAME",
        "CDN_INVALIDATION_ROLE",
    ),
    "email": (
        "EMAIL_PROVIDER",
        "EMAIL_API_KEY",
        "EMAIL_DOMAIN",
        "EMAIL_FROM",
    ),
    "sms": (
        "SMS_PROVIDER",
        "SMS_API_KEY",
        "SMS_FROM",
    ),
    "faas": (
        "FUNCTION_NAME",
        "FUNCTION_ARN",
        "FUNCTION_URL",
        "FUNCTION_REGION",
    ),
    "api_gateway": (
        "API_GATEWAY_URL",
        "API_GATEWAY_ID",
        "API_GATEWAY_STAGE",
    ),
    "database_proxy": (
        "DATABASE_PROXY_HOST",
        "DATABASE_PROXY_PORT",
        "DATABASE_PROXY_ARN",
        "DATABASE_PROXY_TLS",
        "DATABASE_PROXY_AUTH_MODE",
        "DATABASE_PROXY_CREDENTIALS",
        "DATABASE_PROXY_CREDENTIALS_REF",
        "DATABASE_PROXY_DB_USER",
    ),
    "graph_db": (
        "GRAPH_DB_URL",
        "GRAPH_DB_READER_URL",
        "GRAPH_DB_ENDPOINT",
        "GRAPH_DB_PORT",
        "GRAPH_DB_USER",
        "GRAPH_DB_PASSWORD",
        "GRAPH_DB_PROTOCOL",
        "GRAPH_DB_TLS",
        "GRAPH_DB_AUTH_MODE",
        "GRAPH_DB_REGION",
        "GRAPH_DB_RESOURCE_ARN",
    ),
    "wide_column": (
        "WIDE_COLUMN_ENDPOINT",
        "WIDE_COLUMN_KEYSPACE",
        "WIDE_COLUMN_TABLE",
        "WIDE_COLUMN_REGION",
        "WIDE_COLUMN_PORT",
        "WIDE_COLUMN_AUTH_MODE",
        "WIDE_COLUMN_RESOURCE_ARN",
    ),
    "warehouse": (
        "WAREHOUSE_ENDPOINT",
        "WAREHOUSE_DATABASE",
        "WAREHOUSE_USER",
        "WAREHOUSE_PASSWORD",
    ),
    "event_bus": (
        "EVENT_BUS_NAME",
        "EVENT_BUS_ARN",
        "EVENT_BUS_REGION",
    ),
    "stream": (
        "STREAM_NAME",
        "STREAM_ARN",
        "STREAM_ENDPOINT",
        "STREAM_REGION",
    ),
    "workflow_engine": (
        "WORKFLOW_ENGINE_ID",
        "WORKFLOW_ENGINE_ARN",
        "WORKFLOW_ENGINE_REGION",
    ),
    "encryption_key": (
        "ENCRYPTION_KEY_ID",
        "ENCRYPTION_KEY_ARN",
        "ENCRYPTION_KEY_ALIAS",
    ),
    "private_endpoint": (
        "PRIVATE_ENDPOINT_ID",
        "PRIVATE_ENDPOINT_DNS",
        "PRIVATE_ENDPOINT_IPS",
    ),
    "observability": (
        "OBSERVABILITY_PROVIDER",
        "LOG_GROUP",
        "METRICS_ENDPOINT",
        "DASHBOARD_URL",
    ),
    "model_endpoint": (
        "MODEL_ENDPOINT_URL",
        "MODEL_API_KEY",
        "MODEL_DEPLOYMENT_NAME",
        "MODEL_REGION",
    ),
}


def envelope_keys_for(kind: str) -> tuple[str, ...]:
    """Return the stable env-key set the platform injects for a
    managed service of ``kind``. Empty tuple for unknown kinds —
    the merger short-circuits cleanly when there's nothing to add."""
    return _ENVELOPES.get(kind, ())


@dataclasses.dataclass(frozen=True, slots=True)
class ManagedServiceBinding:
    """Inputs the merger needs from a single binding row.

    Kept as a plain dataclass so callers can synthesize one in tests
    without standing up the full ManagedServiceBinding model.
    ``connection_secret`` is the dict we pull values from — keys
    are the envelope names; the merger doesn't second-guess.
    """

    kind: str
    name: str
    connection_secret: Mapping[str, str]


@dataclasses.dataclass(frozen=True, slots=True)
class EnvFromRef:
    """Container-level ``env_from`` reference. ``prefix`` is
    optional and is concatenated with the source key
    (``prefix + key``) at merge time; this matches k8s envFrom
    semantics. The pulled values come from a SecretBundle row,
    represented here as a plain dict for testability."""

    values: Mapping[str, str]
    prefix: str = ""


@dataclasses.dataclass(slots=True)
class MergedEnv:
    """Merge result. ``values`` is the ordered final dict (insertion
    order matches first-seen, which keeps deterministic Deployment
    snapshots). ``provenance`` is per-key 'which source set this'
    using the ``SOURCE_*`` constants."""

    values: dict[str, str]
    provenance: dict[str, str]


def _bind_envelope(binding: ManagedServiceBinding, *, taken_kinds: set[str]) -> dict[str, str]:
    """Materialise a single binding's envelope as ``{key: value}``.

    When the kind has already been seen for this app, prefix every
    emitted key with the binding's name (uppercased) to avoid the
    collision: two postgres bindings → DATABASE_URL plus
    CACHE_DATABASE_URL (or whichever name).
    """
    keys = envelope_keys_for(binding.kind)
    if not keys:
        return {}
    use_prefix = binding.kind in taken_kinds
    prefix = f"{binding.name.upper().replace('-', '_')}_" if use_prefix else ""
    out: dict[str, str] = {}
    for key in keys:
        secret_value = binding.connection_secret.get(key)
        if secret_value is None:
            # Caller chose not to wire this key — fine, skip rather
            # than emit an empty string which masks a missing binding.
            continue
        out[f"{prefix}{key}"] = str(secret_value)
    return out


def merge_env(
    *,
    app_env: Mapping[str, str] | None = None,
    app_secret_bundles: Iterable[Mapping[str, str]] = (),
    managed_service_bindings: Iterable[ManagedServiceBinding] = (),
    workload_env: Mapping[str, str] | None = None,
    container_env: Mapping[str, str] | None = None,
    container_env_from: Iterable[EnvFromRef] = (),
) -> MergedEnv:
    """Merge env sources in spec order; later sources win.

    Returns ``MergedEnv(values, provenance)``. Callers persist the
    provenance dict alongside the deployment snapshot so the UI
    can show 'env DATABASE_URL came from managed_service' next to
    each entry.
    """
    values: dict[str, str] = {}
    provenance: dict[str, str] = {}

    def _apply(source: str, mapping: Mapping[str, str] | None) -> None:
        if not mapping:
            return
        for k, v in mapping.items():
            values[k] = str(v)
            provenance[k] = source

    # 1. app-wide [env]
    _apply(SOURCE_APP_LITERAL, app_env)

    # 2. app-level secret bundles (multiple allowed; later overrides
    # earlier within this layer too — same semantic as Kubernetes
    # envFrom references in declared order).
    for bundle in app_secret_bundles:
        _apply(SOURCE_APP_SECRET_BUNDLE, bundle)

    # 3. managed-service connection envelopes.
    seen_kinds: set[str] = set()
    for binding in managed_service_bindings:
        materialised = _bind_envelope(binding, taken_kinds=seen_kinds)
        seen_kinds.add(binding.kind)
        _apply(SOURCE_MANAGED_SERVICE, materialised)

    # 4. workload-level (reserved per spec; pass-through today)
    _apply(SOURCE_WORKLOAD_LITERAL, workload_env)

    # 5. container-level literals
    _apply(SOURCE_CONTAINER_LITERAL, container_env)

    # 6. container-level env_from (prefix applies per-ref)
    for ref in container_env_from:
        if not ref.values:
            continue
        prefixed = {f"{ref.prefix}{k}": v for k, v in ref.values.items()}
        _apply(SOURCE_CONTAINER_ENV_FROM, prefixed)

    return MergedEnv(values=values, provenance=provenance)
