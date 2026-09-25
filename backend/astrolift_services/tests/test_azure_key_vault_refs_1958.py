"""Azure Key Vault references in managed-service config stay in the owner's namespace (#1958).

Azure resolves them with the platform's shared managed identity (Functions
``@Microsoft.KeyVault(...)`` settings, API Management ``key_vault_secret_id``),
so a free vault or secret name could read another org's secret.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from astrolift_dispatch.agent_secrets import SecretRefNamespaceError
from astrolift_services.secret_ref_config import assert_config_secret_refs_scoped, azure_key_vault_refs

G = "0192f3c4-1111-7aaa-8bbb-111111111111"
APP = "0192f3c4-3333-7eee-8fff-333333333333"
OTHER_APP = "0192f3c4-4444-7eee-8fff-444444444444"
OWNER = SimpleNamespace(guid=uuid.UUID(APP), organization=SimpleNamespace(guid=uuid.UUID(G)))
AZURE = SimpleNamespace(
    slug="aks-1958",
    region="eastus",
    provider_plugin=SimpleNamespace(slug="azure"),
    provider_config={
        "vault_url": "https://acme-kv.vault.azure.net",
        "subscription_id": "s",
        "resource_group": "rg",
    },
    auth_config={},
)
OWN = f"astrolift-services--{G}--{APP}--api-token"
FOREIGN = f"astrolift-services--{G}--{OTHER_APP}--api-token"


def _functions(value):
    return {"environment": {"TOKEN": value}}


def test_the_walker_reads_every_reference_spelling():
    config = {
        "environment": {
            "A": "@Microsoft.KeyVault(SecretUri=https://acme-kv.vault.azure.net/secrets/one/abc)",
            "B": "@Microsoft.KeyVault(VaultName=acme-kv;SecretName=two)",
        },
        "custom_domains": [{"key_vault_secret_id": "https://acme-kv.vault.azure.net/secrets/three"}],
    }
    assert [(r.path, r.vault_host, r.secret_name) for r in azure_key_vault_refs(config)] == [
        ("environment.A", "acme-kv.vault.azure.net", "one"),
        ("environment.B", "acme-kv.vault.azure.net", "two"),
        ("custom_domains[0].key_vault_secret_id", "acme-kv.vault.azure.net", "three"),
    ]


@pytest.mark.parametrize(
    "value",
    [
        f"@Microsoft.KeyVault(SecretUri=https://acme-kv.vault.azure.net/secrets/{OWN}/v1)",
        f"@Microsoft.KeyVault(VaultName=acme-kv;SecretName={OWN})",
    ],
)
def test_a_secret_in_the_owners_namespace_in_the_install_vault_passes(value):
    assert_config_secret_refs_scoped(_functions(value), owner=OWNER, cluster=AZURE)


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (_functions(f"@Microsoft.KeyVault(VaultName=acme-kv;SecretName={FOREIGN})"), "must start"),
        (_functions(f"@Microsoft.KeyVault(VaultName=victim-kv;SecretName={OWN})"), "install vault"),
        (
            _functions("@Microsoft.KeyVault(SecretUri=http://acme-kv.vault.azure.net/secrets/x)"),
            "Astrolift can verify",
        ),
        (
            {
                "custom_domains": [
                    {"key_vault_secret_id": f"https://acme-kv.vault.azure.net/secrets/{FOREIGN}"}
                ]
            },
            "must start",
        ),
    ],
)
def test_a_reference_outside_the_owners_namespace_or_vault_is_refused(config, message):
    with pytest.raises(SecretRefNamespaceError, match=message):
        assert_config_secret_refs_scoped(config, owner=OWNER, cluster=AZURE)


def test_a_key_vault_reference_on_a_non_azure_cluster_is_refused():
    aws = SimpleNamespace(**{**vars(AZURE), "provider_plugin": SimpleNamespace(slug="aws")})
    with pytest.raises(SecretRefNamespaceError, match="no Key Vault"):
        assert_config_secret_refs_scoped(
            _functions(f"@Microsoft.KeyVault(VaultName=acme-kv;SecretName={OWN})"), owner=OWNER, cluster=aws
        )
