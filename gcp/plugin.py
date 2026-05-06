"""GCP provider plugin manifest.

This plugin will implement drivers for GKE, GCE ingress, Cloud DNS,
Google-managed certificates, Secret Manager, GKE Workload Identity,
Artifact Registry, GCS, and Cloud Logging/Monitoring.
"""

from _sdk.base import ProviderPlugin


class GCPProviderPlugin:
    """GCP provider plugin -- stub."""

    def __init__(self) -> None:
        raise NotImplementedError("GCP provider plugin is not yet implemented")


PLUGIN = ProviderPlugin(
    id="gcp",
    display_name="Google Cloud Platform",
    drivers={},
    managed_service_drivers={},
    config_schema={},
)
