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

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
from _sdk.registry import CiPushRole, ImageRegistryDriver, Repo, SecretSpec, Tag
from aws._errors import NotFoundError, map_client_error

# GitHub's OIDC issuer — present in the trust policy of the per-app
# push role, and the `aud` claim our IAM trust enforces on the JWT
# the CI runner presents at AssumeRoleWithWebIdentity time.
_GITHUB_OIDC_PROVIDER_HOST = "token.actions.githubusercontent.com"


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

    def __init__(
        self,
        *,
        config: ECRConfig,
        client: Any | None = None,
        iam_client: Any | None = None,
    ) -> None:
        self._config = config
        self._iam = iam_client  # lazily created when ensure_ci_push_role is called
        if client is not None:
            self._client = client
        else:
            import boto3

            self._client = boto3.client("ecr", region_name=config.region)

    @driver_op(cloud="aws", driver="registry")
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
        except Exception as exc:
            raise map_client_error(exc) from exc

    @driver_op(cloud="aws", driver="registry", audit=True, sensitive_kind="registry.delete")
    def delete_repo(self, name: str, *, archive: bool = True) -> None:
        """ECR has no archive — when ``archive=True`` we leave the repo
        in place but block new image pushes via the policy. ``archive=False``
        force-deletes (must use ``force=True`` to delete repos with images).
        """
        if archive:
            try:
                self._block_push(name=name)
            except NotFoundError:
                # Repo already absent — block-push (archive) is a no-op; the
                # desired end state (no pushable repo) is met. Idempotent so a
                # re-fired teardown completes instead of halting on a missing
                # repo (#1007, extends #998's idempotency to the archive path).
                return
            return
        try:
            self._client.delete_repository(repositoryName=name, force=True)
        except self._client.exceptions.RepositoryNotFoundException:
            # Idempotent delete: the repo is already absent, which is the
            # desired end state. Treat as success so teardown completes (and
            # re-runs are safe) instead of halting on a missing repo (#998).
            return
        except Exception as exc:
            raise map_client_error(exc) from exc

    @driver_op(cloud="aws", driver="registry", audit=True, sensitive_kind="registry.get_pull_secret")
    def get_pull_secret(self, cluster: str, namespace: str) -> SecretSpec:
        """Return a Kubernetes Secret spec dict the workflow layer
        applies into the cluster. The token expires every 12 hours
        (ECR limit); refresh is the platform's responsibility via
        a scheduled workflow.
        """
        try:
            response = self._client.get_authorization_token()
        except Exception as exc:
            raise map_client_error(exc) from exc

        auth_data = response["authorizationData"][0]
        token_b64 = auth_data["authorizationToken"]
        endpoint = auth_data["proxyEndpoint"]

        # ECR returns base64(AWS:<password>); k8s wants a Docker
        # config with the base64-encoded "user:pass" verbatim.
        decoded = base64.b64decode(token_b64).decode("utf-8")
        # Decoded form is "AWS:<password>"; we re-encode the
        # exact string for the dockerconfigjson auth field.
        auth_field = base64.b64encode(decoded.encode("utf-8")).decode("utf-8")
        registry = endpoint.replace("https://", "")

        # Build dockerconfigjson
        import json

        dockerconfig = json.dumps(
            {
                "auths": {
                    registry: {
                        "auth": auth_field,
                        "email": "noreply@astrolift.local",
                    },
                },
            }
        )
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

    @driver_op(cloud="aws", driver="registry")
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

    @driver_op(cloud="aws", driver="registry")
    def list_tags(self, repo: str) -> list[Tag]:
        try:
            paginator = self._client.get_paginator("describe_images")
            tags: list[Tag] = []
            for page in paginator.paginate(repositoryName=repo):
                for image in page.get("imageDetails", []):
                    digest = image.get("imageDigest", "")
                    pushed_raw = image.get("imagePushedAt")
                    pushed_at = pushed_raw.isoformat() if pushed_raw else None
                    for tag_name in image.get("imageTags", []) or []:
                        tags.append(
                            Tag(
                                name=tag_name,
                                digest=digest,
                                pushed_at=pushed_at,
                            )
                        )
            return tags
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {repo} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

    @driver_op(cloud="aws", driver="registry", audit=True, sensitive_kind="registry.create_ci_push_role")
    def ensure_ci_push_role(
        self,
        *,
        repo: str,
        scm_provider: str,
        scm_repo_full_name: str,
    ) -> CiPushRole:
        """Provision (or refresh) an IAM role assumable by GitHub Actions
        via OIDC, scoped to pushing into ``repo`` only.

        Naming: ``astrolift-<repo>-ecr-push`` (truncated to fit IAM's
        64-char role-name limit).  Trust policy enforces both the
        repo scope (``sub`` claim) AND the GitHub ``aud`` claim so
        another tenant's CI in the same GitHub org can't assume this
        role.  Inline policy grants only the five ECR push actions
        scoped to this repo's ARN.  Idempotent — re-running refreshes
        the trust + inline policies in place.

        Mirrors the pattern proven against AWS in production reference
        Django apps.  Pre-req: the AWS account must have the GitHub
        OIDC provider registered (one-time setup via
        ``aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com``).
        """
        if scm_provider != "github":
            raise UnsupportedOperationError(
                f"ECRDriver only supports scm_provider='github' today; got {scm_provider!r}",
            )
        if "/" not in scm_repo_full_name:
            raise ValueError(
                f"scm_repo_full_name must be 'owner/repo'; got {scm_repo_full_name!r}",
            )
        import json

        if self._iam is None:
            import boto3

            self._iam = boto3.client("iam", region_name=self._config.region)

        account_id = self._config.account_id
        # ``repo`` is the ECR repo name ``<org>/<app>`` — the literal '/' is
        # illegal in an IAM RoleName (and a naive [:64] slice never fixed the
        # charset, only the length). iam_role_name sanitizes '/' → '-' and
        # length-bounds with a stable hash suffix (#994).
        from providers.aws._naming import iam_role_name

        role_name = iam_role_name("astrolift", repo, "ecr-push")
        oidc_arn = f"arn:aws:iam::{account_id}:oidc-provider/{_GITHUB_OIDC_PROVIDER_HOST}"
        repo_arn = f"arn:aws:ecr:{self._config.region}:{account_id}:repository/{repo}"

        trust_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Federated": oidc_arn},
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            f"{_GITHUB_OIDC_PROVIDER_HOST}:aud": "sts.amazonaws.com",
                        },
                        "StringLike": {
                            f"{_GITHUB_OIDC_PROVIDER_HOST}:sub": f"repo:{scm_repo_full_name}:*",
                        },
                    },
                },
            ],
        }
        push_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "ecr:GetAuthorizationToken",
                    "Resource": "*",
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:PutImage",
                        "ecr:InitiateLayerUpload",
                        "ecr:UploadLayerPart",
                        "ecr:CompleteLayerUpload",
                        "ecr:BatchGetImage",
                    ],
                    "Resource": repo_arn,
                },
            ],
        }

        try:
            response = self._iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description=f"Astrolift ECR push role for {scm_repo_full_name} → {repo}",
            )
            role_arn = response["Role"]["Arn"]
        except self._iam.exceptions.EntityAlreadyExistsException:
            # Refresh the trust policy in case the SCM repo or scope
            # changed.  ``update_assume_role_policy`` is the idempotent
            # path; ``put_role_policy`` below is also idempotent.
            self._iam.update_assume_role_policy(
                RoleName=role_name,
                PolicyDocument=json.dumps(trust_policy),
            )
            role_arn = self._iam.get_role(RoleName=role_name)["Role"]["Arn"]

        self._iam.put_role_policy(
            RoleName=role_name,
            PolicyName="ecr-push",
            PolicyDocument=json.dumps(push_policy),
        )
        return CiPushRole(role_ref=role_arn, scm_provider="github")

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
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _block_push(self, *, name: str) -> None:
        """Apply a repository policy that denies push. Read remains
        allowed so deployed pods can still pull the existing image
        until they're torn down."""
        import json

        policy = json.dumps(
            {
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
            }
        )
        try:
            self._client.set_repository_policy(
                repositoryName=name,
                policyText=policy,
            )
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {name} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc
