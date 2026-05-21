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


@dataclass(frozen=True)
class CiPushRole:
    """A cloud-side identity that the SCM provider's CI runner can assume
    to push to the platform-owned registry repo.

    ``role_ref`` is opaque to the platform (the CI workflow embeds it
    verbatim — IAM role ARN for AWS / Workload Identity Pool resource
    name for GCP / Federated Identity Credential resource id for Azure).
    ``scm_provider`` is the SCM the role's trust policy is bound to
    (``github`` today; ``gitlab``/``bitbucket`` later when those SCM
    drivers grow OIDC support).
    """

    role_ref: str
    scm_provider: str


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

    def ensure_ci_push_role(
        self,
        *,
        repo: str,
        scm_provider: str,
        scm_repo_full_name: str,
    ) -> CiPushRole:
        """Provision (or refresh) a cloud-side identity assumable by the
        SCM provider's CI runner via OIDC, scoped to push images into
        ``repo`` only.

        Drivers that don't (yet) implement OIDC-based CI push must raise
        :class:`_sdk.UnsupportedOperationError` so the lifecycle layer
        can fall back to long-lived access keys or surface an operator-
        facing TODO.
        """
        ...
