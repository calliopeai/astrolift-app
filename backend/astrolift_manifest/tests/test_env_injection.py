"""Tests for env-injection precedence + provenance (#117)."""

from __future__ import annotations

from astrolift_manifest.env_injection import (
    SOURCE_APP_LITERAL,
    SOURCE_APP_SECRET_BUNDLE,
    SOURCE_CONTAINER_ENV_FROM,
    SOURCE_CONTAINER_LITERAL,
    SOURCE_MANAGED_SERVICE,
    SOURCE_WORKLOAD_LITERAL,
    EnvFromRef,
    ManagedServiceBinding,
    envelope_keys_for,
    merge_env,
)

# ---- precedence -------------------------------------------------------


def test_later_source_overrides_earlier_for_same_key():
    """Spec rule: each layer can override the previous. Container
    literal beats app literal beats nothing."""
    out = merge_env(
        app_env={"K": "from-app", "ONLY_APP": "x"},
        container_env={"K": "from-container"},
    )
    assert out.values["K"] == "from-container"
    assert out.values["ONLY_APP"] == "x"
    assert out.provenance["K"] == SOURCE_CONTAINER_LITERAL
    assert out.provenance["ONLY_APP"] == SOURCE_APP_LITERAL


def test_full_precedence_chain():
    """Walk every layer to make sure each one beats the previous,
    and the final winning source ends up in provenance."""
    out = merge_env(
        app_env={"K": "1-app"},
        app_secret_bundles=[{"K": "2-bundle"}],
        managed_service_bindings=[
            ManagedServiceBinding(
                kind="redis",
                name="cache",
                connection_secret={"REDIS_URL": "redis://x", "K": "3-msvc"},
            ),
        ],
        workload_env={"K": "4-workload"},
        container_env={"K": "5-container"},
        container_env_from=[EnvFromRef(values={"K": "6-envfrom"})],
    )
    assert out.values["K"] == "6-envfrom"
    assert out.provenance["K"] == SOURCE_CONTAINER_ENV_FROM


def test_each_layer_sets_correct_provenance():
    """Pull a sentinel key from each source and confirm provenance
    matches. Catches a bug where the merger writes the wrong label."""
    out = merge_env(
        app_env={"FROM_APP": "1"},
        app_secret_bundles=[{"FROM_BUNDLE": "2"}],
        managed_service_bindings=[
            ManagedServiceBinding(
                kind="redis", name="cache",
                connection_secret={"REDIS_URL": "redis://x"},
            ),
        ],
        workload_env={"FROM_WORKLOAD": "4"},
        container_env={"FROM_CONTAINER": "5"},
        container_env_from=[EnvFromRef(values={"FROM_ENVFROM": "6"})],
    )
    assert out.provenance["FROM_APP"] == SOURCE_APP_LITERAL
    assert out.provenance["FROM_BUNDLE"] == SOURCE_APP_SECRET_BUNDLE
    assert out.provenance["REDIS_URL"] == SOURCE_MANAGED_SERVICE
    assert out.provenance["FROM_WORKLOAD"] == SOURCE_WORKLOAD_LITERAL
    assert out.provenance["FROM_CONTAINER"] == SOURCE_CONTAINER_LITERAL
    assert out.provenance["FROM_ENVFROM"] == SOURCE_CONTAINER_ENV_FROM


# ---- multiple secret bundles ------------------------------------------


def test_secret_bundles_apply_in_order():
    """Multiple bundles are layered in the order given — later wins."""
    out = merge_env(
        app_secret_bundles=[
            {"K": "first"},
            {"K": "second"},
        ]
    )
    assert out.values["K"] == "second"


# ---- managed service envelopes ----------------------------------------


def test_envelope_keys_for_known_kinds():
    assert "DATABASE_URL" in envelope_keys_for("postgres")
    assert "REDIS_URL" in envelope_keys_for("redis")
    assert envelope_keys_for("totally-unknown") == ()


def test_envelope_catalog_covers_full_spec_11_set():
    """Spec 11 §6.1-§6.16 — every kind in the catalogue must
    expose its declared envelope. Lock-test so additions stay in
    sync with the spec."""
    expected_kinds = {
        "postgres", "mysql", "redis", "mq", "queue", "topic",
        "kv_store", "document_db", "search", "vector_index",
        "time_series", "object_store", "nfs", "cdn", "email", "sms",
    }
    for kind in expected_kinds:
        keys = envelope_keys_for(kind)
        assert keys, f"kind {kind!r} has no envelope keys"


def test_postgres_envelope_includes_ssl_mode_and_master_secret_ref():
    """Spec 11 §6.1 — POSTGRES_SSL_MODE + POSTGRES_MASTER_SECRET_REF
    are part of the canonical postgres envelope."""
    keys = envelope_keys_for("postgres")
    assert "POSTGRES_SSL_MODE" in keys
    assert "POSTGRES_MASTER_SECRET_REF" in keys


def test_kafka_mq_envelope_carries_sasl_keys():
    keys = envelope_keys_for("mq")
    assert "KAFKA_BOOTSTRAP_SERVERS" in keys
    assert "KAFKA_SASL_MECHANISM" in keys
    assert "KAFKA_SASL_USERNAME" in keys
    assert "KAFKA_TOPIC_PREFIX" in keys


def test_vector_index_envelope_includes_namespace():
    keys = envelope_keys_for("vector_index")
    assert "VECTOR_INDEX_NAME" in keys
    assert "VECTOR_NAMESPACE" in keys


def test_object_store_envelope_includes_prefix():
    keys = envelope_keys_for("object_store")
    assert "BUCKET_PREFIX" in keys


def test_email_and_sms_envelopes_split_provider_from_creds():
    email = envelope_keys_for("email")
    assert {"EMAIL_PROVIDER", "EMAIL_API_KEY", "EMAIL_DOMAIN", "EMAIL_FROM"} <= set(email)
    sms = envelope_keys_for("sms")
    assert {"SMS_PROVIDER", "SMS_API_KEY", "SMS_FROM"} <= set(sms)


def test_managed_service_keys_pull_from_connection_secret():
    out = merge_env(
        managed_service_bindings=[
            ManagedServiceBinding(
                kind="postgres",
                name="main",
                connection_secret={
                    "POSTGRES_HOST": "db.svc",
                    "POSTGRES_PORT": "5432",
                    "DATABASE_URL": "postgres://...",
                },
            ),
        ],
    )
    assert out.values["POSTGRES_HOST"] == "db.svc"
    assert out.values["DATABASE_URL"] == "postgres://..."
    assert out.provenance["POSTGRES_HOST"] == SOURCE_MANAGED_SERVICE


def test_managed_service_skips_keys_not_in_secret():
    """If the binding only wired up some keys, we skip the missing
    ones rather than emit empty strings (which would mask the
    missing binding bug)."""
    out = merge_env(
        managed_service_bindings=[
            ManagedServiceBinding(
                kind="postgres",
                name="main",
                connection_secret={"POSTGRES_HOST": "db.svc"},
            ),
        ],
    )
    assert "POSTGRES_HOST" in out.values
    assert "DATABASE_URL" not in out.values


def test_two_bindings_same_kind_second_gets_name_prefix():
    """Two postgres bindings → DATABASE_URL from the first wins for
    the bare key, the second's keys land prefixed by its name."""
    out = merge_env(
        managed_service_bindings=[
            ManagedServiceBinding(
                kind="postgres",
                name="main",
                connection_secret={
                    "POSTGRES_HOST": "main.db",
                    "DATABASE_URL": "postgres://main",
                },
            ),
            ManagedServiceBinding(
                kind="postgres",
                name="analytics",
                connection_secret={
                    "POSTGRES_HOST": "analytics.db",
                    "DATABASE_URL": "postgres://analytics",
                },
            ),
        ],
    )
    assert out.values["POSTGRES_HOST"] == "main.db"
    assert out.values["DATABASE_URL"] == "postgres://main"
    assert out.values["ANALYTICS_POSTGRES_HOST"] == "analytics.db"
    assert out.values["ANALYTICS_DATABASE_URL"] == "postgres://analytics"


def test_two_bindings_different_kinds_no_prefixing():
    """Different kinds don't collide; each emits its bare envelope."""
    out = merge_env(
        managed_service_bindings=[
            ManagedServiceBinding(
                kind="postgres",
                name="db",
                connection_secret={"DATABASE_URL": "postgres://x"},
            ),
            ManagedServiceBinding(
                kind="redis",
                name="cache",
                connection_secret={"REDIS_URL": "redis://x"},
            ),
        ],
    )
    assert "DATABASE_URL" in out.values
    assert "REDIS_URL" in out.values
    assert "CACHE_REDIS_URL" not in out.values


# ---- env_from prefix -------------------------------------------------


def test_env_from_prefix_concatenates():
    """k8s envFrom semantic: prefix prepends to each key."""
    out = merge_env(
        container_env_from=[
            EnvFromRef(
                values={"USER": "admin", "PASS": "x"},
                prefix="DB_",
            ),
        ],
    )
    assert out.values == {"DB_USER": "admin", "DB_PASS": "x"}


def test_env_from_no_prefix_passes_through():
    out = merge_env(
        container_env_from=[
            EnvFromRef(values={"FOO": "bar"}),
        ],
    )
    assert out.values == {"FOO": "bar"}


def test_multiple_env_from_refs_layer_in_order():
    out = merge_env(
        container_env_from=[
            EnvFromRef(values={"K": "first"}),
            EnvFromRef(values={"K": "second"}),
        ],
    )
    assert out.values["K"] == "second"


# ---- empty inputs -----------------------------------------------------


def test_empty_inputs_return_empty_result():
    out = merge_env()
    assert out.values == {}
    assert out.provenance == {}


def test_explicit_none_inputs_skipped():
    """None passed for any layer must not blow up."""
    out = merge_env(
        app_env=None,
        workload_env=None,
        container_env=None,
        container_env_from=(),
    )
    assert out.values == {}
