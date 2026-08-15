from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.app_deploy import AppDeployError, _config_for_capability


def _cluster(**provider_overrides: object) -> SimpleNamespace:
    provider_config = {
        "subscription_id": "00000000-1111-2222-3333-444444444444",
        "tenant_id": "11111111-2222-3333-4444-555555555555",
        "resource_group": "rg-platform",
        "location": "eastus2",
        "cluster_oidc_issuer": "https://eastus2.oic.prod-aks.azure.com/tenant/issuer/",
        "registry_name": "platformimages",
        "acr_sku": "Premium",
        "acr_admin_enabled": False,
        "acr_immutable_tags": False,
        "vault_url": "https://platform.vault.azure.net",
        "keyvault_secret_name_prefix": "smd",
        "postgres_secret_name_prefix": "managed-pg",
        "ingress_variant": "gateway_api",
        "appgw_id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Network/applicationGateways/appgw",
        "akv_secret_id_for_tls": "https://platform.vault.azure.net/secrets/wildcard",
        "managed_cert_name_prefix": "smd-cert",
        "notification_hubs_namespace": "smd-notifications",
        "notification_hub_name": "mobile",
        "notification_hubs_api_version": "2020-06",
        "notification_hubs_timeout_seconds": 17,
    }
    provider_config.update(provider_overrides)
    return SimpleNamespace(
        slug="azure-prod",
        region="westus2",
        provider_config=provider_config,
        auth_config={
            "notification_hubs_shared_access_key_name": "DefaultFullSharedAccessSignature",
            "notification_hubs_shared_access_key": "test-only-not-a-real-key",
        },
    )


def test_azure_registry_config_uses_operator_controls() -> None:
    config = _config_for_capability("azure", _cluster(), "registry")
    assert type(config).__name__ == "ACRConfig"
    assert config.registry_name == "platformimages"
    assert config.location == "eastus2"
    assert config.sku == "Premium"
    assert config.admin_enabled is False
    assert config.immutable_tags is False
    assert config.tenant_id == "11111111-2222-3333-4444-555555555555"


def test_azure_secret_config_matches_managed_binding_resolver() -> None:
    config = _config_for_capability("azure", _cluster(), "secrets")
    assert type(config).__name__ == "KeyVaultConfig"
    assert config.vault_url == "https://platform.vault.azure.net"
    assert config.secret_name_prefix == "smd"
    assert "managed-pg" in config.managed_secret_name_prefixes
    assert "astrolift-pg" in config.managed_secret_name_prefixes


def test_azure_identity_dns_tls_and_ingress_have_specific_configs() -> None:
    identity = _config_for_capability("azure", _cluster(), "identity")
    dns = _config_for_capability("azure", _cluster(), "dns")
    tls = _config_for_capability("azure", _cluster(), "tls")
    ingress = _config_for_capability("azure", _cluster(), "ingress")
    assert type(identity).__name__ == "FederatedIdentityConfig"
    assert type(dns).__name__ == "AzureDNSConfig"
    assert type(tls).__name__ == "AppGatewayTlsConfig"
    assert type(ingress).__name__ == "AppGatewayIngressConfig"
    assert identity.cluster_oidc_issuer.endswith("/issuer/")
    assert tls.vault_url == "https://platform.vault.azure.net"
    assert tls.cert_name_prefix == "smd-cert"
    assert ingress.variant == "gateway_api"
    assert ingress.akv_secret_id.endswith("/secrets/wildcard")


def test_azure_notification_config_uses_auth_config_without_repr_leak() -> None:
    config = _config_for_capability("azure", _cluster(), "notification")
    assert type(config).__name__ == "AzureNotificationHubsConfig"
    assert config.namespace == "smd-notifications"
    assert config.hub_name == "mobile"
    assert config.timeout_seconds == 17
    assert config.shared_access_key == "test-only-not-a-real-key"
    assert "test-only-not-a-real-key" not in repr(config)


def test_every_registered_non_cluster_azure_capability_has_a_config() -> None:
    from azure.plugin import PLUGIN

    for capability in sorted(set(PLUGIN.drivers) - {"cluster"}):
        config = _config_for_capability("azure", _cluster(), capability)
        assert type(config).__name__ != "AKSConfig", capability


@pytest.mark.parametrize(
    ("capability", "override", "message"),
    [
        ("registry", {"registry_name": ""}, "registry_name"),
        ("identity", {"cluster_oidc_issuer": ""}, "cluster_oidc_issuer"),
        ("secrets", {"vault_url": ""}, "vault_url"),
        ("dns", {"subscription_id": ""}, "subscription_id"),
        ("tls", {"resource_group": ""}, "resource_group"),
        ("notification", {"notification_hub_name": ""}, "notification_hub_name"),
    ],
)
def test_azure_capabilities_fail_closed_on_missing_install_config(
    capability: str,
    override: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(AppDeployError, match=message):
        _config_for_capability("azure", _cluster(**override), capability)


def test_azure_secrets_rejects_unsafe_vault_url() -> None:
    with pytest.raises(AppDeployError, match="invalid Azure Key Vault config"):
        _config_for_capability("azure", _cluster(vault_url="http://platform.vault.azure.net"), "secrets")


def test_unknown_azure_capability_fails_instead_of_using_aks_config() -> None:
    with pytest.raises(AppDeployError, match="no Azure config builder"):
        _config_for_capability("azure", _cluster(), "made-up")
