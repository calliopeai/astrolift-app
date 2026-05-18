"""ImageRegistryDriver protocol -- provision registry repos and manage pull credentials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Repo:
    name: str
    uri: str


@dataclass(frozen=True)
class Tag:
    name: str
    digest: str
    pushed_at: str | None


# A Kubernetes Secret spec dict suitable for creating an imagePullSecret.
SecretSpec = dict[str, Any]


class ImageRegistryDriver(Protocol):
    """Protocol for managing container image registry repositories and credentials.

    Implementations: ecr, gcr, artifact_registry, acr, ghcr, quay,
    dockerhub, harbor, generic_oci.
    """

    def ensure_repo(self, name: str) -> Repo: ...

    def delete_repo(self, name: str, *, archive: bool = True) -> None: ...

    def get_pull_secret(self, cluster: str, namespace: str) -> SecretSpec: ...

    def push(self, local_image: str, repo: str, tag: str) -> str: ...

    def list_tags(self, repo: str) -> list[Tag]: ...
