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
from _sdk.cloud_credentials import CredentialedConfig
from _sdk.registry import CiPushRole, ImageRegistryDriver, Repo, SecretSpec, Tag
from aws._errors import NotFoundError, map_client_error
from aws.session import aws_client

# GitHub's OIDC issuer — present in the trust policy of the per-app
# push role, and the `aud` claim our IAM trust enforces on the JWT
# the CI runner presents at AssumeRoleWithWebIdentity time.
_GITHUB_OIDC_PROVIDER_HOST = "token.actions.githubusercontent.com"

# ECR severity labels -> the platform's four policy severities. ECR also
# emits INFORMATIONAL and UNDEFINED, which carry no policy meaning and
# are dropped rather than folded into LOW.
_SCAN_SEVERITIES = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}


@dataclass(frozen=True)
class ECRConfig(CredentialedConfig):
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
            self._client = aws_client("ecr", region=config.region, credential=config.credential)

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

    @driver_op(cloud="aws", driver="registry")
    def get_scan_findings(self, *, repo: str, digest: str) -> dict[str, Any]:
        """Read back the vulnerability findings ECR produced for one image.

        ``ensure_repo`` turns basic scanning on (``image_scanning_enabled``,
        default True), so ECR has been scanning every pushed image while
        nothing in the platform ever read a finding. This is that read,
        mapped onto a provider-neutral shape the deploy gate consumes:

            {"status": "COMPLETE" | "IN_PROGRESS" | "NOT_FOUND" | "FAILED",
             "description": "<ECR's own status text>",
             "completed_at": "<iso-8601>" | "",
             "severity_counts": {"critical": 0, "high": 0, "medium": 0, "low": 0},
             "findings": [{"cve_id", "severity", "package_name",
                           "package_version", "fixed_in_version",
                           "description"}]}

        ``status`` is reported rather than swallowed: an image that has
        never been scanned yields ``NOT_FOUND`` with an empty finding set,
        and the caller must not read that as "clean". Findings are paged to
        exhaustion because a policy that walks the finding list would
        under-count a truncated page.

        Enhanced (Inspector) scanning reports through ``enhancedFindings``
        instead; this driver only enables basic scanning, so only
        ``findings`` is mapped.
        """
        image_id = {"imageDigest": digest}
        severity_counts = dict.fromkeys(_SCAN_SEVERITIES.values(), 0)
        findings: list[dict[str, Any]] = []
        status = ""
        description = ""
        completed_at = ""
        next_token: str | None = None
        try:
            while True:
                kwargs: dict[str, Any] = {"repositoryName": repo, "imageId": image_id}
                if next_token:
                    kwargs["nextToken"] = next_token
                response = self._client.describe_image_scan_findings(**kwargs)
                scan_status = response.get("imageScanStatus") or {}
                status = scan_status.get("status") or status
                description = scan_status.get("description") or description
                block = response.get("imageScanFindings") or {}
                if not next_token:
                    # Counts are whole-image totals, identical on every
                    # page - take them from the first response only.
                    raw_counts = block.get("findingSeverityCounts") or {}
                    for ecr_severity, key in _SCAN_SEVERITIES.items():
                        severity_counts[key] = int(raw_counts.get(ecr_severity, 0) or 0)
                    completed_raw = block.get("imageScanCompletedAt")
                    completed_at = completed_raw.isoformat() if completed_raw else ""
                for finding in block.get("findings") or []:
                    severity = _SCAN_SEVERITIES.get((finding.get("severity") or "").upper())
                    if severity is None:
                        # INFORMATIONAL / UNDEFINED carry no policy meaning.
                        continue
                    attributes = {
                        attribute.get("key", ""): attribute.get("value", "")
                        for attribute in finding.get("attributes") or []
                    }
                    findings.append(
                        {
                            "cve_id": finding.get("name", ""),
                            "severity": severity,
                            "package_name": attributes.get("package_name", ""),
                            "package_version": attributes.get("package_version", ""),
                            "fixed_in_version": attributes.get("fixed_in_version", ""),
                            "description": finding.get("description", ""),
                        }
                    )
                next_token = response.get("nextToken")
                if not next_token:
                    break
        except self._client.exceptions.ScanNotFoundException as exc:
            # The image exists but carries no scan - a real answer, not an
            # error: scanning may have been off when it was pushed.
            return {
                "status": "NOT_FOUND",
                "description": str(exc),
                "completed_at": "",
                "severity_counts": dict.fromkeys(_SCAN_SEVERITIES.values(), 0),
                "findings": [],
            }
        except self._client.exceptions.ImageNotFoundException as exc:
            raise NotFoundError(f"image {repo}@{digest} not found") from exc
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {repo} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

        return {
            "status": status or "UNKNOWN",
            "description": description,
            "completed_at": completed_at,
            "severity_counts": severity_counts,
            "findings": findings,
        }

    @driver_op(cloud="aws", driver="registry", audit=True, sensitive_kind="registry.create_ci_push_role")
    def ensure_ci_push_role(
        self,
        *,
        repo: str,
        scm_provider: str,
        scm_repo_full_name: str,
        scm_repo_numeric_ids: tuple[int, int] | None = None,
    ) -> CiPushRole:
        """Provision (or refresh) an IAM role assumable by GitHub Actions
        via OIDC, scoped to pushing into ``repo`` only.

        Naming: ``astrolift-<repo>-ecr-push`` (truncated to fit IAM's
        64-char role-name limit).  Trust policy enforces both the
        repo scope (``sub`` claim) AND the GitHub ``aud`` claim so
        another tenant's CI in the same GitHub org can't assume this
        role.  The ``sub`` match lists the login-based pattern plus,
        when ``scm_repo_numeric_ids`` is given, the ID-stamped pattern
        (``repo:org@OWNER_ID/name@REPO_ID:*``) that newly created
        GitHub repos present — without it their tokens never match and
        the assume fails with a bare "Not authorized" (#1532).  Inline policy grants only the five ECR push actions
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
            self._iam = aws_client("iam", region=self._config.region, credential=self._config.credential)

        account_id = self._config.account_id
        # ``repo`` is the ECR repo name ``<org>/<app>`` — the literal '/' is
        # illegal in an IAM RoleName (and a naive [:64] slice never fixed the
        # charset, only the length). iam_role_name sanitizes '/' → '-' and
        # length-bounds with a stable hash suffix (#994).
        from providers.aws._naming import iam_role_name

        role_name = iam_role_name("astrolift", repo, "ecr-push")
        oidc_arn = f"arn:aws:iam::{account_id}:oidc-provider/{_GITHUB_OIDC_PROVIDER_HOST}"
        repo_arn = f"arn:aws:ecr:{self._config.region}:{account_id}:repository/{repo}"

        sub_patterns = [f"repo:{scm_repo_full_name}:*"]
        if scm_repo_numeric_ids is not None:
            owner_login, repo_name_only = scm_repo_full_name.split("/", 1)
            owner_id, repo_numeric_id = scm_repo_numeric_ids
            sub_patterns.append(
                f"repo:{owner_login}@{owner_id}/{repo_name_only}@{repo_numeric_id}:*",
            )
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
                            f"{_GITHUB_OIDC_PROVIDER_HOST}:sub": sub_patterns,
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
                        # The CI workflow's skip-if-built probe (#1220):
                        # without DescribeImages the probe's AccessDenied
                        # reads as "not built" and a re-run on a built SHA
                        # dies on the repo's immutable tags.
                        "ecr:DescribeImages",
                    ],
                    "Resource": repo_arn,
                },
            ],
        }

        try:
            response = self._iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                # ASCII-only: IAM rejects an AssumeRolePolicy/role Description
                # outside [\\u0009\\u000A\\u000D\\u0020-\\u007E\\u00A1-\\u00FF], so
                # no unicode arrows/dashes here (a "→" failed CreateRole, #1026).
                Description=f"Astrolift ECR push role for {scm_repo_full_name} -> {repo}",
                # Tag like every other platform-minted role so the orphan scan
                # (#995) can reap it as a backstop if teardown is interrupted.
                Tags=[{"Key": "astrolift.io/managed-by", "Value": "platform"}],
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
