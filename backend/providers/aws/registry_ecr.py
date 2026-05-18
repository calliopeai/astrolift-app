"""AWS ECR ImageRegistryDriver implementation (#34).

Spec ref: spec 23-provider-plugin-aws + _sdk/registry.py.

ECR semantics:
- Repos are auto-namespaced per AWS account; repo name format is the
  caller's choice (we use slug-like names, e.g. ``acme/api``).
- Pull credentials use the ``ecr:GetAuthorizationToken`` token which
  expires every 12 hours; the platform schedules refresh via the
  existing scheduled-workflow registry.
- Image push is delegated to the build runner (BuildKit / Kaniko)
  rather than performed by this driver — boto3 doesn't push image
  layers. The ``push()`` method returns the canonical image ref the
  build should use.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from _sdk.registry import ImageRegistryDriver, Repo, SecretSpec, Tag

from aws._errors import ConflictError, NotFoundError, map_client_error


@dataclass(frozen=True)
class ECRConfig:
    """AWS-specific ECR config. Bound from the cluster's plugin config."""

    region: str
    account_id: str
    """12-digit AWS account ID. ECR repo URIs include this."""

    image_scanning_enabled: bool = True
    """ECR's built-in vulnerability scanning. Recommended on for any
    org running compliance frameworks (SOC2/HIPAA per #271)."""

    encryption_kms_key_id: str | None = None
    """When set, ECR uses customer-managed KMS encryption. None
    falls back to AWS-managed AES256 (still encrypted at rest)."""

    image_tag_mutability: str = "IMMUTABLE"
    """IMMUTABLE prevents tag overwrites (production hygiene; #275
    + #276 audit invariants assume immutable image refs).
    MUTABLE for legacy/dev workflows."""


class ECRDriver(ImageRegistryDriver):
    """boto3-backed ECR driver."""

    def __init__(self, *, config: ECRConfig, client: Any | None = None) -> None:
        self._config = config
        if client is not None:
            self._client = client
        else:
            import boto3

            self._client = boto3.client("ecr", region_name=config.region)

    def ensure_repo(self, name: str) -> Repo:
        """Create the repo if absent; return its URI either way."""
        try:
            kwargs: dict[str, Any] = {
                "repositoryName": name,
                "imageScanningConfiguration": {
                    "scanOnPush": self._config.image_scanning_enabled,
                },
                "imageTagMutability": self._config.image_tag_mutability,
            }
            if self._config.encryption_kms_key_id:
                kwargs["encryptionConfiguration"] = {
                    "encryptionType": "KMS",
                    "kmsKey": self._config.encryption_kms_key_id,
                }
            response = self._client.create_repository(**kwargs)
            return Repo(
                name=name,
                uri=response["repository"]["repositoryUri"],
            )
        except self._client.exceptions.RepositoryAlreadyExistsException:
            # Repo exists — fetch its URI rather than failing
            return self._describe_repo(name=name)
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

    def delete_repo(self, name: str, *, archive: bool = True) -> None:
        """ECR has no archive — when ``archive=True`` we leave the repo
        in place but block new image pushes via the policy. ``archive=False``
        force-deletes (must use ``force=True`` to delete repos with images).
        """
        if archive:
            self._block_push(name=name)
            return
        try:
            self._client.delete_repository(repositoryName=name, force=True)
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {name} not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

    def get_pull_secret(self, cluster: str, namespace: str) -> SecretSpec:
        """Return a Kubernetes Secret spec dict the workflow layer
        applies into the cluster. The token expires every 12 hours
        (ECR limit); refresh is the platform's responsibility via
        a scheduled workflow.
        """
        try:
            response = self._client.get_authorization_token()
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

        auth_data = response["authorizationData"][0]
        token_b64 = auth_data["authorizationToken"]
        endpoint = auth_data["proxyEndpoint"]

        # ECR returns base64(AWS:<password>); k8s wants a Docker
        # config with the base64-encoded "user:pass" verbatim.
        decoded = base64.b64decode(token_b64).decode("utf-8")
        # Decoded form is "AWS:<password>"; we re-encode the
        # exact string for the dockerconfigjson auth field.
        auth_field = base64.b64encode(decoded.encode("utf-8")).decode(
            "utf-8"
        )
        registry = endpoint.replace("https://", "")

        # Build dockerconfigjson
        import json

        dockerconfig = json.dumps({
            "auths": {
                registry: {
                    "auth": auth_field,
                    "email": "noreply@astrolift.local",
                },
            },
        })
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "astrolift-ecr-credentials",
                "namespace": namespace,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/credential-source": "ecr",
                },
                "annotations": {
                    "astrolift.io/expires-at-utc-hours": "12",
                },
            },
            "type": "kubernetes.io/dockerconfigjson",
            "data": {
                ".dockerconfigjson": base64.b64encode(
                    dockerconfig.encode("utf-8"),
                ).decode("utf-8"),
            },
        }

    def push(self, local_image: str, repo: str, tag: str) -> str:
        """Returns the canonical ECR image ref the build runner
        should push to. boto3 doesn't push image layers; that's
        BuildKit/Kaniko's job from the build environment."""
        if not local_image:
            raise ValueError("local_image is required")
        # Ensure the repo exists; idempotent.
        self.ensure_repo(repo)
        registry = self._registry_uri()
        return f"{registry}/{repo}:{tag}"

    def list_tags(self, repo: str) -> list[Tag]:
        try:
            paginator = self._client.get_paginator("describe_images")
            tags: list[Tag] = []
            for page in paginator.paginate(repositoryName=repo):
                for image in page.get("imageDetails", []):
                    digest = image.get("imageDigest", "")
                    pushed_raw = image.get("imagePushedAt")
                    pushed_at = (
                        pushed_raw.isoformat() if pushed_raw else None
                    )
                    for tag_name in image.get("imageTags", []) or []:
                        tags.append(Tag(
                            name=tag_name,
                            digest=digest,
                            pushed_at=pushed_at,
                        ))
            return tags
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {repo} not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

    # ---- internals ------------------------------------------------

    def _registry_uri(self) -> str:
        return f"{self._config.account_id}.dkr.ecr.{self._config.region}.amazonaws.com"

    def _describe_repo(self, *, name: str) -> Repo:
        try:
            response = self._client.describe_repositories(
                repositoryNames=[name],
            )
            data = response["repositories"][0]
            return Repo(name=name, uri=data["repositoryUri"])
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {name} not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc

    def _block_push(self, *, name: str) -> None:
        """Apply a repository policy that denies push. Read remains
        allowed so deployed pods can still pull the existing image
        until they're torn down."""
        import json

        policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "DenyPushArchived",
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": [
                        "ecr:PutImage",
                        "ecr:InitiateLayerUpload",
                        "ecr:UploadLayerPart",
                        "ecr:CompleteLayerUpload",
                    ],
                },
            ],
        })
        try:
            self._client.set_repository_policy(
                repositoryName=name, policyText=policy,
            )
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {name} not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise map_client_error(exc) from exc
