"""Azure Container Registry ImageRegistryDriver (#46).

ACR is name-scoped per registry (e.g. acmeprod.azurecr.io). A single
ACR registry hosts many repository paths. The driver:
- references a pre-created registry by name
- relies on the registry's repository auto-creation (push creates path)
- emits AKS-friendly attached-identity pull (no Secret needed when
  cluster is attached via `az aks update --attach-acr`)
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from _sdk.registry import ImageRegistryDriver, Repo, SecretSpec, Tag

from azure._errors import NotFoundError, map_api_error


@dataclass(frozen=True)
class ACRConfig:
    subscription_id: str
    resource_group: str
    registry_name: str
    """ACR registry name (without the .azurecr.io suffix)."""

    location: str = "eastus"
    sku: str = "Standard"
    admin_enabled: bool = False
    """Admin auth is brittle; prefer Workload Identity / managed
    identity attachment. Default off."""

    immutable_tags: bool = True
    client: Any | None = None
    """ContainerRegistryManagementClient — injected for tests."""


class ACRDriver(ImageRegistryDriver):
    def __init__(self, *, config: ACRConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.containerregistry import (
                ContainerRegistryManagementClient,
            )

            self._client = ContainerRegistryManagementClient(
                credential=DefaultAzureCredential(),
                subscription_id=config.subscription_id,
            )

    @property
    def login_server(self) -> str:
        return f"{self._config.registry_name}.azurecr.io"

    def ensure_repo(self, name: str) -> Repo:
        """ACR registries auto-create repositories on first push, so
        ensure_repo verifies the registry exists + returns the
        per-app URI. The registry itself is operator-provisioned via
        the Astrolift install workflow."""
        try:
            self._client.registries.get(
                resource_group_name=self._config.resource_group,
                registry_name=self._config.registry_name,
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(
                    f"ACR registry {self._config.registry_name} "
                    f"not found in {self._config.resource_group}",
                ) from exc
            raise map_api_error(exc) from exc

        return Repo(
            name=name,
            uri=f"{self.login_server}/{name}",
        )

    def delete_repo(self, name: str, *, archive: bool = True) -> None:
        """ACR delete-repository is a per-repo operation via the data
        plane (ContainerRegistryClient, not the management client).
        archive=True is a no-op (path stays; access via RBAC)."""
        if archive:
            return
        raise NotImplementedError(
            "force-delete of ACR repositories requires data-plane "
            "ContainerRegistryClient; out of scope for this driver",
        )

    def get_pull_secret(self, cluster: str, namespace: str) -> SecretSpec:
        """For AKS clusters attached via `az aks update --attach-acr`,
        pull auth flows through the kubelet's managed identity — no
        Secret needed. This returns a marker Secret so callers wired
        for the imagePullSecrets pattern stay consistent across clouds."""
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "astrolift-acr-credentials",
                "namespace": namespace,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/credential-source": "acr",
                },
                "annotations": {
                    "astrolift.io/note": (
                        "AKS clusters use --attach-acr managed-"
                        "identity for pull auth — no Secret needed. "
                        "This Secret is a marker; non-AKS pulls "
                        "require operator-supplied SP credentials."
                    ),
                },
            },
            "type": "kubernetes.io/dockerconfigjson",
            "data": {
                ".dockerconfigjson": base64.b64encode(
                    json.dumps({"auths": {}}).encode(),
                ).decode(),
            },
        }

    def push(self, local_image: str, repo: str, tag: str) -> str:
        if not local_image:
            raise ValueError("local_image is required")
        ensured = self.ensure_repo(repo)
        return f"{ensured.uri}:{tag}"

    def list_tags(self, repo: str) -> list[Tag]:
        # ACR's list_tags requires the data-plane client; mirroring
        # AWS pattern, expose minimal metadata.
        try:
            response = self._client.list_tags(
                resource_group_name=self._config.resource_group,
                registry_name=self._config.registry_name,
                repository=repo,
            )
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "ResourceNotFoundError":
                raise NotFoundError(f"repo {repo} not found") from exc
            raise map_api_error(exc) from exc

        out: list[Tag] = []
        for item in response:
            out.append(Tag(
                name=getattr(item, "name", "") or "",
                digest=getattr(item, "digest", "") or "",
                pushed_at=getattr(item, "last_updated", None),
            ))
        return out
