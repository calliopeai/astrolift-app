"""Tests for in-cluster secret materialization (#19, spec 12 §6.2)."""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.secret_materialization import (
    APP_ENV_SECRET_NAME,
    ExternalSecretMapping,
    SecretBackend,
    env_from_ref,
    materialize,
    render_external_secret,
    render_inline_secret,
    render_pod_env_from,
    service_secret_name,
)


# ---- naming convention ---------------------------------------------


def test_app_env_secret_name_matches_spec():
    assert APP_ENV_SECRET_NAME == "astrolift-app-env"


def test_service_secret_name_format():
    assert service_secret_name("main-db") == "astrolift-svc-main-db"
    assert service_secret_name("cache") == "astrolift-svc-cache"


def test_service_secret_name_rejects_empty():
    with pytest.raises(ValueError):
        service_secret_name("")


# ---- inline secret --------------------------------------------------


def test_inline_secret_shape():
    out = render_inline_secret(
        name=APP_ENV_SECRET_NAME,
        namespace="acme-api",
        string_data={"DATABASE_URL": "postgres://...", "API_KEY": "k"},
        labels={"app": "api"},
    )
    assert out["apiVersion"] == "v1"
    assert out["kind"] == "Secret"
    assert out["metadata"]["name"] == APP_ENV_SECRET_NAME
    assert out["metadata"]["namespace"] == "acme-api"
    assert out["metadata"]["labels"] == {"app": "api"}
    assert out["type"] == "Opaque"
    assert out["stringData"]["DATABASE_URL"] == "postgres://..."


def test_inline_uses_string_data_not_data():
    """stringData lets apiserver base64 — manual encoding is a foot-gun."""
    out = render_inline_secret(
        name="x", namespace="ns", string_data={"K": "v"},
    )
    assert "stringData" in out
    assert "data" not in out


def test_inline_rejects_empty_name_or_namespace():
    with pytest.raises(ValueError):
        render_inline_secret(name="", namespace="ns", string_data={})
    with pytest.raises(ValueError):
        render_inline_secret(name="x", namespace="", string_data={})


# ---- ExternalSecret CR ---------------------------------------------


def test_external_secret_shape():
    mappings = [
        ExternalSecretMapping(
            target_key="POSTGRES_PASSWORD",
            remote_ref_key="postgres/main",
            remote_ref_property="password",
        ),
    ]
    out = render_external_secret(
        name="astrolift-svc-main",
        namespace="acme-api",
        secret_store="acme-vault",
        mappings=mappings,
    )
    assert out["apiVersion"] == "external-secrets.io/v1"
    assert out["kind"] == "ExternalSecret"
    assert out["spec"]["secretStoreRef"]["name"] == "acme-vault"
    assert out["spec"]["secretStoreRef"]["kind"] == "ClusterSecretStore"
    assert out["spec"]["refreshInterval"] == "1h"
    assert out["spec"]["target"]["name"] == "astrolift-svc-main"
    entry = out["spec"]["data"][0]
    assert entry["secretKey"] == "POSTGRES_PASSWORD"
    assert entry["remoteRef"]["key"] == "postgres/main"
    assert entry["remoteRef"]["property"] == "password"


def test_external_secret_omits_property_when_empty():
    out = render_external_secret(
        name="x", namespace="ns", secret_store="store",
        mappings=[ExternalSecretMapping(
            target_key="K", remote_ref_key="path/to/secret",
        )],
    )
    entry = out["spec"]["data"][0]
    assert "property" not in entry["remoteRef"]


def test_external_secret_rejects_empty_mappings():
    with pytest.raises(ValueError, match="mappings"):
        render_external_secret(
            name="x", namespace="ns", secret_store="s", mappings=[],
        )


def test_external_secret_rejects_missing_secret_store():
    with pytest.raises(ValueError, match="secret_store"):
        render_external_secret(
            name="x", namespace="ns", secret_store="",
            mappings=[ExternalSecretMapping(
                target_key="K", remote_ref_key="path",
            )],
        )


def test_external_mapping_rejects_empty_keys():
    with pytest.raises(ValueError):
        render_external_secret(
            name="x", namespace="ns", secret_store="s",
            mappings=[ExternalSecretMapping(target_key="", remote_ref_key="path")],
        )


# ---- pod envFrom ---------------------------------------------------


def test_env_from_ref_shape():
    out = env_from_ref("astrolift-app-env")
    assert out == {"secretRef": {"name": "astrolift-app-env"}}


def test_render_pod_env_from_orders_service_before_app():
    """Service secrets first, app env last — matches env_injection
    precedence (later entries can override earlier)."""
    out = render_pod_env_from(
        service_secret_names=["astrolift-svc-main", "astrolift-svc-cache"],
    )
    names = [e["secretRef"]["name"] for e in out]
    assert names == [
        "astrolift-svc-main",
        "astrolift-svc-cache",
        APP_ENV_SECRET_NAME,
    ]


def test_render_pod_env_from_no_services():
    out = render_pod_env_from()
    assert out == [{"secretRef": {"name": APP_ENV_SECRET_NAME}}]


# ---- materialize dispatch ------------------------------------------


def test_materialize_inline_round_trip():
    out = materialize(
        backend=SecretBackend.INLINE,
        secret_name="x", namespace="ns",
        string_data={"K": "v"},
    )
    assert out["kind"] == "Secret"
    assert out["stringData"]["K"] == "v"


def test_materialize_external_round_trip():
    out = materialize(
        backend=SecretBackend.EXTERNAL_SECRETS_OPERATOR,
        secret_name="x", namespace="ns",
        external_mappings=[ExternalSecretMapping(
            target_key="K", remote_ref_key="path",
        )],
        secret_store="vault-store",
    )
    assert out["kind"] == "ExternalSecret"


def test_materialize_inline_requires_string_data():
    with pytest.raises(ValueError, match="string_data"):
        materialize(
            backend=SecretBackend.INLINE,
            secret_name="x", namespace="ns",
        )


def test_materialize_external_requires_mappings_and_store():
    with pytest.raises(ValueError, match="external_mappings"):
        materialize(
            backend=SecretBackend.EXTERNAL_SECRETS_OPERATOR,
            secret_name="x", namespace="ns",
        )
    with pytest.raises(ValueError, match="secret_store"):
        materialize(
            backend=SecretBackend.EXTERNAL_SECRETS_OPERATOR,
            secret_name="x", namespace="ns",
            external_mappings=[ExternalSecretMapping(
                target_key="K", remote_ref_key="path",
            )],
        )
