from __future__ import annotations

from types import SimpleNamespace

from k8s_native.secrets_vault import VaultConfig

from core.app_deploy import _config_for_capability


def test_k8s_native_secrets_capability_builds_vault_config_not_cluster_config() -> None:
    cluster = SimpleNamespace(
        slug="on-prem-prod",
        region="",
        provider_config={
            "vault_address": "https://vault.example.test",
            "vault_kv_mount": "platform-secrets",
            "vault_path_prefix": "steadymd",
            "vault_namespace": "tenant-a",
            "vault_auth_method": "token",
        },
        auth_config={"vault_token": "test-token"},
    )

    config = _config_for_capability("k8s_native", cluster, "secrets")

    assert isinstance(config, VaultConfig)
    assert config.address == "https://vault.example.test"
    assert config.token == "test-token"
    assert config.kv_mount == "platform-secrets"
    assert config.kv_path_prefix == "steadymd"
    assert config.namespace == "tenant-a"
