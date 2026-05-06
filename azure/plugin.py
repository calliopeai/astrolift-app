"""Azure provider plugin manifest.

This plugin will implement drivers for AKS, Azure Application Gateway
ingress, Azure DNS, Azure-managed certificates, Azure Key Vault,
AKS Federated Credentials, Azure Container Registry, Azure Blob
Storage, and Azure Monitor.
"""

from _sdk.base import ProviderPlugin


class AzureProviderPlugin:
    """Azure provider plugin -- stub."""

    def __init__(self) -> None:
        raise NotImplementedError("Azure provider plugin is not yet implemented")


PLUGIN = ProviderPlugin(
    id="azure",
    display_name="Microsoft Azure",
    drivers={},
    managed_service_drivers={},
    config_schema={},
)
