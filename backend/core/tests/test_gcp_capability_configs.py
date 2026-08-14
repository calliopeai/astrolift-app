from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.app_deploy import AppDeployError, _config_for_capability


def _cluster(**provider_overrides: object) -> SimpleNamespace:
    provider_config = {
        "project_id": "acme-prod",
        "region": "us-central1",
        "artifact_registry_location": "us",
        "artifact_registry_repo": "platform-images",
        "artifact_registry_immutable_tags": False,
        "artifact_registry_kms_key": "projects/p/locations/us/keyRings/r/cryptoKeys/ar",
        "secret_id_prefix": "smd",
        "secret_manager_kms_key": "projects/p/locations/us/keyRings/r/cryptoKeys/secrets",
        "managed_cert_name_prefix": "smd-cert",
        "ingress_variant": "gateway_api",
        "static_ip_name": "smd-global",
        "managed_cert_name": "smd-wildcard",
        "gateway_class": "gke-l7-regional-external-managed",
        "fcm_timeout_seconds": 17,
    }
    provider_config.update(provider_overrides)
    return SimpleNamespace(
        slug="gcp-prod",
        region="us-west1",
        provider_config=provider_config,
        auth_config={"fcm_access_token": "test-only-token"},
    )


def test_gcp_registry_config_uses_operator_controls() -> None:
    config = _config_for_capability("gcp", _cluster(), "registry")
    assert type(config).__name__ == "ArtifactRegistryConfig"
    assert config.project_id == "acme-prod"
    assert config.location == "us"
    assert config.repository_id == "platform-images"
    assert config.immutable_tags is False
    assert config.encryption_kms_key_name.endswith("/cryptoKeys/ar")


def test_gcp_secret_manager_config_matches_binding_resolver() -> None:
    config = _config_for_capability("gcp", _cluster(), "secrets")
    assert type(config).__name__ == "GCPSecretsConfig"
    assert config.project_id == "acme-prod"
    assert config.secret_id_prefix == "smd"
    assert config.kms_key_name.endswith("/cryptoKeys/secrets")


def test_gcp_identity_dns_and_tls_have_capability_specific_configs() -> None:
    identity = _config_for_capability("gcp", _cluster(), "identity")
    dns = _config_for_capability("gcp", _cluster(), "dns")
    tls = _config_for_capability("gcp", _cluster(), "tls")
    assert type(identity).__name__ == "GCPWIConfig"
    assert type(dns).__name__ == "CloudDNSConfig"
    assert type(tls).__name__ == "ManagedCertConfig"
    assert {identity.project_id, dns.project_id, tls.project_id} == {"acme-prod"}
    assert tls.cert_name_prefix == "smd-cert"


def test_gcp_ingress_config_preserves_provider_native_choices() -> None:
    config = _config_for_capability("gcp", _cluster(), "ingress")
    assert type(config).__name__ == "GCPIngressConfig"
    assert config.variant == "gateway_api"
    assert config.static_ip_name == "smd-global"
    assert config.managed_cert_name == "smd-wildcard"
    assert config.gateway_class == "gke-l7-regional-external-managed"


def test_gcp_notification_config_is_not_a_gke_config() -> None:
    config = _config_for_capability("gcp", _cluster(), "notification")
    assert type(config).__name__ == "FCMConfig"
    assert config.project_id == "acme-prod"
    assert config.access_token == "test-only-token"
    assert config.timeout_seconds == 17


def test_every_registered_non_cluster_gcp_capability_has_a_config() -> None:
    from gcp.plugin import PLUGIN

    for capability in sorted(set(PLUGIN.drivers) - {"cluster"}):
        config = _config_for_capability("gcp", _cluster(), capability)
        assert type(config).__name__ != "GKEConfig", capability


def test_gcp_capability_requires_project_id() -> None:
    cluster = _cluster(project_id="")
    with pytest.raises(AppDeployError, match="project_id"):
        _config_for_capability("gcp", cluster, "secrets")


def test_unknown_gcp_capability_fails_instead_of_using_gke_config() -> None:
    with pytest.raises(AppDeployError, match="no GCP config builder"):
        _config_for_capability("gcp", _cluster(), "made-up")


@pytest.mark.parametrize(
    ("kind", "variant"),
    [
        ("postgres", "cloudsql"),
        ("mysql", "cloudsql"),
        ("redis", "memorystore"),
    ],
)
def test_gcp_managed_credentials_share_cluster_secret_prefix(
    kind: str,
    variant: str,
) -> None:
    from core.cluster_observability import managed_config_for

    config = managed_config_for("gcp", _cluster(), kind=kind, variant=variant)
    assert config.secret_id_prefix == "smd"
