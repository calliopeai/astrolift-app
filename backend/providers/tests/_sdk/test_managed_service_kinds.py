"""Tests for managed-service kind catalog + binding-env validator (#16)."""

from __future__ import annotations

from _sdk.managed_service_kinds import (
    KINDS,
    KindCatalog,
    ManagedServiceKind,
    validate_binding_envs,
)


def test_canonical_catalog_has_core_kinds() -> None:
    names = {k.name for k in KINDS.kinds}
    assert {"postgres", "redis", "cache", "object_store", "queue"} <= names


def test_canonical_catalog_has_extended_kinds() -> None:
    """Extended catalog (#75 + #79 + #55) — every new kind must
    appear here so plugin authors can target it."""
    names = {k.name for k in KINDS.kinds}
    assert "mysql" in names
    assert "document_db" in names
    assert "kv_store" in names
    assert "vector_index" in names
    assert "search" in names
    assert "time_series" in names
    assert "filesystem" in names
    assert "email" in names
    assert "model_endpoint" in names
    assert "event_stream" in names


def test_catalog_covers_every_platform_resource_kind() -> None:
    names = {kind.name for kind in KINDS.kinds}
    assert {
        "api_gateway",
        "cdn",
        "database_proxy",
        "encryption_key",
        "event_bus",
        "faas",
        "graph_db",
        "mq",
        "mssql",
        "observability",
        "private_endpoint",
        "sms",
        "stream",
        "topic",
        "warehouse",
        "wide_column",
        "workflow_engine",
    } <= names


def test_email_kind_uses_provider_name_env() -> None:
    email = KINDS.get("email")
    assert email is not None
    # Apps need to know which provider's SDK to load
    assert "EMAIL_PROVIDER" in email.binding_envs_required
    # Workload-identity providers such as SES need no API key, while SaaS
    # variants may emit one.
    assert "EMAIL_API_KEY" in email.binding_envs_optional


def test_model_endpoint_kind_separate_from_search() -> None:
    """LLM endpoints are a distinct kind from search — different
    pricing model, different SDK, different cost-estimation path."""
    assert KINDS.get("model_endpoint") is not None
    assert KINDS.get("search") is not None
    assert KINDS.get("model_endpoint") is not KINDS.get("search")


def test_postgres_required_envs() -> None:
    pg = KINDS.get("postgres")
    assert pg is not None
    assert "POSTGRES_HOST" in pg.binding_envs_required
    assert "POSTGRES_PORT" in pg.binding_envs_required
    assert "POSTGRES_PASSWORD" in pg.binding_envs_required


def test_validate_binding_envs_returns_missing() -> None:
    missing = validate_binding_envs(
        kind="postgres",
        emitted_envs=["POSTGRES_HOST", "POSTGRES_PORT"],
    )
    assert "POSTGRES_DB" in missing
    assert "POSTGRES_USER" in missing
    assert "POSTGRES_PASSWORD" in missing


def test_validate_binding_envs_complete_returns_empty() -> None:
    missing = validate_binding_envs(
        kind="postgres",
        emitted_envs=[
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_DB",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
        ],
    )
    assert missing == []


def test_database_proxy_declares_portable_tls_mode() -> None:
    proxy = KINDS.get("database_proxy")

    assert proxy is not None
    assert "DATABASE_PROXY_TLS" in proxy.binding_envs_optional


def test_cache_is_distinct_from_redis_and_has_protocol_envelope() -> None:
    cache = KINDS.get("cache")

    assert cache is not None
    assert cache is not KINDS.get("redis")
    assert "CACHE_PROTOCOL" in cache.binding_envs_required
    assert "CACHE_NODES" in cache.binding_envs_optional


def test_document_database_contract_includes_tls_and_resource_identity() -> None:
    document_db = KINDS.get("document_db")

    assert document_db is not None
    assert "DOCDB_TLS" in document_db.binding_envs_optional
    assert "DOCDB_RESOURCE_ARN" in document_db.binding_envs_optional


def test_wide_column_contract_carries_table_auth_and_port() -> None:
    wide_column = KINDS.get("wide_column")

    assert wide_column is not None
    assert "WIDE_COLUMN_TABLE" in wide_column.binding_envs_optional
    assert "WIDE_COLUMN_PORT" in wide_column.binding_envs_optional
    assert "WIDE_COLUMN_AUTH_MODE" in wide_column.binding_envs_optional


def test_graph_database_contract_carries_reader_auth_and_resource_identity() -> None:
    graph_db = KINDS.get("graph_db")

    assert graph_db is not None
    assert "GRAPH_DB_READER_URL" in graph_db.binding_envs_optional
    assert "GRAPH_DB_AUTH_MODE" in graph_db.binding_envs_optional
    assert "GRAPH_DB_RESOURCE_ARN" in graph_db.binding_envs_optional


def test_warehouse_contract_carries_modern_iam_and_deployment_identity() -> None:
    warehouse = KINDS.get("warehouse")

    assert warehouse is not None
    assert "WAREHOUSE_PORT" in warehouse.binding_envs_optional
    assert "WAREHOUSE_AUTH_MODE" in warehouse.binding_envs_optional
    assert "WAREHOUSE_DEPLOYMENT" in warehouse.binding_envs_optional
    assert "WAREHOUSE_RESOURCE_ARN" in warehouse.binding_envs_optional


def test_messaging_contracts_use_portable_provider_neutral_identity() -> None:
    queue = KINDS.get("queue")
    topic = KINDS.get("topic")
    event_bus = KINDS.get("event_bus")
    stream = KINDS.get("stream")

    assert queue is not None and topic is not None and event_bus is not None and stream is not None
    assert queue.binding_envs_required == ("QUEUE_URL",)
    assert "QUEUE_ARN_OR_ID" in queue.binding_envs_optional
    assert topic.binding_envs_required == ("TOPIC_ARN_OR_ID", "TOPIC_NAME")
    assert event_bus.binding_envs_required == ("EVENT_BUS_NAME",)
    assert "EVENT_BUS_ARN" in event_bus.binding_envs_optional
    assert stream.binding_envs_required == ("STREAM_NAME",)
    assert "STREAM_ARN" in stream.binding_envs_optional


def test_validate_unknown_kind_returns_empty() -> None:
    """Unknown kinds skip validation (operator-defined kinds may
    not yet be in the canonical catalog)."""
    missing = validate_binding_envs(
        kind="not_a_real_kind",
        emitted_envs=[],
    )
    assert missing == []


def test_kind_catalog_get_returns_none_for_missing() -> None:
    catalog = KindCatalog()
    assert catalog.get("nonexistent") is None


def test_custom_catalog_extension() -> None:
    """Plugin authors can extend the catalog at import time."""
    custom = KindCatalog(
        kinds=(
            ManagedServiceKind(
                name="vector_index",
                description="Vector similarity search store",
                binding_envs_required=("VECTOR_DB_URL", "VECTOR_DB_TOKEN"),
            ),
        )
    )
    missing = validate_binding_envs(
        kind="vector_index",
        emitted_envs=["VECTOR_DB_URL"],
        catalog=custom,
    )
    assert missing == ["VECTOR_DB_TOKEN"]
