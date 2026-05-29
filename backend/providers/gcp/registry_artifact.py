"""GCP Artifact Registry ImageRegistryDriver (#40)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
from _sdk.registry import ImageRegistryDriver, Repo, SecretSpec, Tag
from gcp._errors import NotFoundError, map_api_error


@dataclass(frozen=True)
class ArtifactRegistryConfig:
    project_id: str
    location: str
    """GCP region or 'multi-region' (e.g. us, eu, asia)."""

    repository_id: str
    """Artifact Registry repo IDs are flat — multiple Astrolift
    repos live as paths within one Artifact Registry repository.
    Distinct from ECR where each app gets its own ECR repo."""

    immutable_tags: bool = True
    encryption_kms_key_name: str | None = None
    """Customer-managed KMS key. Falls back to Google-managed."""

    client: Any | None = None


class ArtifactRegistryDriver(ImageRegistryDriver):
    def __init__(self, *, config: ArtifactRegistryConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from google.cloud import artifactregistry_v1

            self._client = artifactregistry_v1.ArtifactRegistryClient()

    @driver_op(cloud="gcp", driver="registry")
    def ensure_repo(self, name: str) -> Repo:
        """Artifact Registry repos are flat at the AR level; the
        Astrolift repo name maps to a path WITHIN AR. The driver
        creates the AR repository on first call (operator-style)
        and returns the per-app URI."""
        ar_path = (
            f"projects/{self._config.project_id}"
            f"/locations/{self._config.location}"
            f"/repositories/{self._config.repository_id}"
        )
        try:
            self._client.get_repository(name=ar_path)
        except Exception as exc:
            # GCP NotFound = "Repository not found"; create it
            if type(exc).__name__ == "NotFound":
                self._create_artifact_registry_repo(
                    parent=(f"projects/{self._config.project_id}" f"/locations/{self._config.location}")
                )
            else:
                raise map_api_error(exc) from exc

        uri = (
            f"{self._config.location}-docker.pkg.dev"
            f"/{self._config.project_id}"
            f"/{self._config.repository_id}/{name}"
        )
        return Repo(name=name, uri=uri)

    @driver_op(cloud="gcp", driver="registry", audit=True, sensitive_kind="registry.delete")
    def delete_repo(self, name: str, *, archive: bool = True) -> None:
        """Artifact Registry doesn't have per-image-path deletion
        without listing tags first. archive=True is a no-op
        (repo path stays; access controlled via IAM elsewhere).
        archive=False would require listing + deleting all tags."""
        if archive:
            return
        # #614 -- previously raised bare NotImplementedError, which the
        # CI no-stub gate didn't distinguish from real partial-stub bugs.
        # UnsupportedOperationError lets the resolver layer translate to a
        # "force-delete not supported on this backend" user-facing message.
        raise UnsupportedOperationError(
            f"registry.delete_repo({name=}, archive=False) not supported on "
            "GCP Artifact Registry -- force-delete requires per-tag listing "
            "+ deletion; out of scope for this driver",
        )

    @driver_op(cloud="gcp", driver="registry", audit=True, sensitive_kind="registry.get_pull_secret")
    def get_pull_secret(
        self,
        cluster: str,
        namespace: str,
    ) -> SecretSpec:
        """For GKE, the recommended path is Workload Identity (no
        pull secret needed — node SA grants pull access). For
        non-GKE clusters reading from AR, a service-account JSON
        key is the fallback. This driver returns the GKE-friendly
        marker; operators wanting JSON-key auth must wire that
        out of band."""
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "astrolift-gcr-credentials",
                "namespace": namespace,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/credential-source": "artifact_registry",
                },
                "annotations": {
                    "astrolift.io/note": (
                        "GKE clusters use Workload Identity for pull "
                        "auth — no Secret needed. This Secret is a "
                        "marker; non-GKE pulls require operator-"
                        "supplied SA JSON key."
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

    @driver_op(cloud="gcp", driver="registry")
    def push(self, local_image: str, repo: str, tag: str) -> str:
        if not local_image:
            raise ValueError("local_image is required")
        ensured = self.ensure_repo(repo)
        return f"{ensured.uri}:{tag}"

    @driver_op(cloud="gcp", driver="registry")
    def list_tags(self, repo: str) -> list[Tag]:
        package_path = (
            f"projects/{self._config.project_id}"
            f"/locations/{self._config.location}"
            f"/repositories/{self._config.repository_id}"
            f"/packages/{repo.replace('/', '%2F')}"
        )
        try:
            response = self._client.list_tags(parent=package_path)
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                raise NotFoundError(f"package {repo} not found") from exc
            raise map_api_error(exc) from exc
        out: list[Tag] = []
        for tag_obj in response:
            tag_name = tag_obj.name.rsplit("/", 1)[-1]
            version = getattr(tag_obj, "version", "") or ""
            out.append(
                Tag(
                    name=tag_name,
                    digest=version.rsplit("/", 1)[-1] if version else "",
                    pushed_at=None,
                )
            )
        return out

    def _create_artifact_registry_repo(self, *, parent: str) -> None:
        from google.cloud.artifactregistry_v1 import Repository

        repo = Repository(
            format_=Repository.Format.DOCKER,
        )
        if self._config.immutable_tags:
            repo.docker_config = Repository.DockerRepositoryConfig(
                immutable_tags=True,
            )
        if self._config.encryption_kms_key_name:
            repo.kms_key_name = self._config.encryption_kms_key_name
        try:
            operation = self._client.create_repository(
                parent=parent,
                repository=repo,
                repository_id=self._config.repository_id,
            )
            operation.result()  # Wait for completion
        except Exception as exc:
            raise map_api_error(exc) from exc
