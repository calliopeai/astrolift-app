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
from aws._errors import NotFoundError, ProviderError, map_client_error
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

    permissions_boundary_arn: str = ""
    """Permissions boundary to attach to the CI push role this driver
    mints (#1906). Empty on an admin-provisioned (push mode) install,
    where no boundary exists. Non-empty on an agent-installed (pull
    mode) one, where the installer agent's own boundary carries a
    ``DenyRoleCreationWithoutThisBoundary`` statement refusing any
    ``iam:CreateRole`` that does not attach that same boundary — so
    leaving this empty there means ``ensure_ci_push_role`` is denied.
    Mirrors ``IRSAConfig.permissions_boundary_arn``."""


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
                "tags": [{"Key": "astrolift.io/ecr-retention", "Value": "enabled"}],
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
            # Repo exists — clear any DenyPushArchived policy a prior
            # deprovision left in place (#1819) before returning its URI, so
            # re-registering an app under the same slug restores pushability
            # instead of silently inheriting the old deny.
            self._clear_archive_policy(name=name)
            try:
                existing = self._client.describe_repositories(repositoryNames=[name])["repositories"][0]
                self._client.tag_resource(
                    resourceArn=existing["repositoryArn"],
                    tags=[{"Key": "astrolift.io/ecr-retention", "Value": "enabled"}],
                )
                return Repo(name=name, uri=existing["repositoryUri"])
            except Exception as exc:
                raise map_client_error(exc) from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

    @driver_op(cloud="aws", driver="registry", audit=True)
    def retain_deployment_images(self, refs: list[str], *, environment: str, deployment: str) -> list[dict[str, str]]:
        """Pin this rollout's images before apply; return tags for later retirement.

        Pins are immutable and unique per environment/deployment/digest, so
        concurrent rollouts cannot move another deployment's protection tag.
        External registries are outside this driver's ownership.
        """
        from uuid import UUID

        prefix = f"retain-astrolift-{UUID(environment).hex}-{UUID(deployment).hex}-"
        registry = self._registry_uri() + "/"
        pins = []
        for ref in sorted(set(refs)):
            if not ref.startswith(registry):
                continue
            path = ref[len(registry) :]
            if "@" in path:
                repo, digest = path.rsplit("@", 1)
                repo = repo.split(":", 1)[0]
                image_id = {"imageDigest": digest}
            elif ":" in path:
                repo, tag = path.rsplit(":", 1)
                image_id = {"imageTag": tag}
            else:
                repo, image_id = path, {"imageTag": "latest"}
            try:
                response = self._client.batch_get_image(repositoryName=repo, imageIds=[image_id])
                images = response.get("images", [])
                if response.get("failures") or len(images) != 1:
                    raise ProviderError(f"cannot retain deployment image {ref}: image unavailable")
                image = images[0]
                digest = image["imageId"]["imageDigest"]
                tag = prefix + digest.removeprefix("sha256:")
                kwargs = {
                    "repositoryName": repo,
                    "imageManifest": image["imageManifest"],
                    "imageTag": tag,
                }
                if image.get("imageManifestMediaType"):
                    kwargs["imageManifestMediaType"] = image["imageManifestMediaType"]
                try:
                    self._client.put_image(**kwargs)
                except (
                    self._client.exceptions.ImageAlreadyExistsException,
                    self._client.exceptions.ImageTagAlreadyExistsException,
                ):
                    existing = self._client.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])
                    if existing["imageDetails"][0]["imageDigest"] != digest:
                        raise ProviderError(f"retention tag collision in {repo}: {tag}") from None
                pins.append(
                    {
                        "repository": repo,
                        "tag": tag,
                        "digest": digest,
                        "source_ref": ref,
                        "pinned_ref": f"{registry}{repo}@{digest}",
                    }
                )
            except Exception as exc:
                raise map_client_error(exc) from exc
        return pins

    @driver_op(cloud="aws", driver="registry", audit=True)
    def release_deployment_images(self, pins: list[dict[str, str]], *, environment: str, deployment: str) -> None:
        """Remove only this deployment's protection tags, never its last tag."""
        from uuid import UUID

        prefix = f"retain-astrolift-{UUID(environment).hex}-{UUID(deployment).hex}-"
        for pin in pins:
            if not pin["tag"].startswith(prefix):
                raise ProviderError("refusing to release another deployment's retention tag")
            try:
                try:
                    response = self._client.describe_images(
                        repositoryName=pin["repository"],
                        imageIds=[{"imageTag": pin["tag"]}],
                    )
                except self._client.exceptions.ImageNotFoundException:
                    continue
                image = response["imageDetails"][0]
                if image["imageDigest"] != pin["digest"]:
                    raise ProviderError("retention tag no longer points to the recorded digest")
                # ECR deletes an image when its final tag is removed. Leave that
                # case pinned rather than bypassing the janitor's age/count gate.
                if len(image.get("imageTags", [])) <= 1:
                    continue
                result = self._client.batch_delete_image(
                    repositoryName=pin["repository"],
                    imageIds=[{"imageTag": pin["tag"]}],
                )
                if any(f["failureCode"] != "ImageNotFound" for f in result.get("failures", [])):
                    raise ProviderError("could not release deployment retention tag")
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

        create_kwargs: dict[str, Any] = {
            "RoleName": role_name,
            "AssumeRolePolicyDocument": json.dumps(trust_policy),
            # ASCII-only: IAM rejects an AssumeRolePolicy/role Description
            # outside [\\u0009\\u000A\\u000D\\u0020-\\u007E\\u00A1-\\u00FF], so
            # no unicode arrows/dashes here (a "→" failed CreateRole, #1026).
            "Description": f"Astrolift ECR push role for {scm_repo_full_name} -> {repo}",
            # Tag like every other platform-minted role so the orphan scan
            # (#995) can reap it as a backstop if teardown is interrupted.
            "Tags": [{"Key": "astrolift.io/managed-by", "Value": "platform"}],
        }
        # Pull-mode installs require every minted role to carry this
        # boundary (#1906), or the agent's own DenyRoleCreationWithoutThis-
        # Boundary refuses the CreateRole. Omit it when unset: IAM rejects
        # an empty PermissionsBoundary, and push-mode installs legitimately
        # have none.
        if self._config.permissions_boundary_arn:
            create_kwargs["PermissionsBoundary"] = self._config.permissions_boundary_arn

        try:
            response = self._iam.create_role(**create_kwargs)
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
        """Add a statement denying push, on top of whatever other
        statements are already on the repo policy. Read remains allowed
        so deployed pods can still pull the existing image until they're
        torn down.

        An earlier version of this method called ``set_repository_policy``
        with ONLY the deny statement, silently replacing (destroying) any
        policy an operator had set directly — e.g. a cross-account pull
        grant for another AWS account (#1819 review). Statement-scoped now:
        read the current document, drop any stale ``DenyPushArchived`` of
        ours (so re-archiving is idempotent, not additive), append a fresh
        one, and write the merged document back — ``Version``/``Id``/
        anything else on the document untouched.
        """
        import json

        document, _ = self._policy_without_archive_statement(name=name)
        document["Statement"].append(
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
            }
        )
        try:
            self._client.set_repository_policy(
                repositoryName=name,
                policyText=json.dumps(document),
            )
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {name} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _clear_archive_policy(self, *, name: str) -> None:
        """Remove only the ``DenyPushArchived`` statement ``_block_push``
        may have left (#1819), keeping any other statement — and the
        document's own ``Version``/``Id``/etc — an operator set directly
        (e.g. cross-account pull). An earlier version called
        ``delete_repository_policy`` unconditionally, destroying those too.

        No-ops without writing anything when the repo never carried a
        ``DenyPushArchived`` statement to begin with (no policy at all, or
        an operator-only policy that was never archived) — nothing here is
        this driver's to rewrite. Otherwise writes back whatever else
        remains, or drops the policy entirely (never an empty-``Statement``
        document, which AWS rejects) once nothing does.
        """
        import json

        document, removed = self._policy_without_archive_statement(name=name)
        if not removed:
            return
        if document["Statement"]:
            try:
                self._client.set_repository_policy(
                    repositoryName=name,
                    policyText=json.dumps(document),
                )
            except self._client.exceptions.RepositoryNotFoundException as exc:
                raise NotFoundError(f"repository {name} not found") from exc
            except Exception as exc:
                raise map_client_error(exc) from exc
            return
        try:
            self._client.delete_repository_policy(repositoryName=name)
        except self._client.exceptions.RepositoryPolicyNotFoundException:
            # Already gone — another caller cleared it between our read and
            # this delete. Idempotent, so treat as the desired end state.
            return
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {name} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

    def _policy_without_archive_statement(self, *, name: str) -> tuple[dict[str, Any], bool]:
        """``name``'s repository policy document with our own
        ``DenyPushArchived`` statement removed, everything else — every
        other statement an operator may have set directly (e.g.
        cross-account pull), ``Version``, ``Id``, any other top-level key —
        untouched (#1819 review). ``({"Version": "2012-10-17", "Statement":
        []}, False)`` when the repo carries no policy yet.

        IAM allows ``Statement`` to be a single object or a list; both are
        normalized to a list on the way out so callers can always index or
        append. The second return value is whether a ``DenyPushArchived``
        statement was actually found and dropped, so a caller that only
        wants to clear one can tell "nothing to clear" apart from "cleared
        down to zero statements."
        """
        import json

        try:
            response = self._client.get_repository_policy(repositoryName=name)
        except self._client.exceptions.RepositoryPolicyNotFoundException:
            return {"Version": "2012-10-17", "Statement": []}, False
        except self._client.exceptions.RepositoryNotFoundException as exc:
            raise NotFoundError(f"repository {name} not found") from exc
        except Exception as exc:
            raise map_client_error(exc) from exc

        document = json.loads(response["policyText"])
        raw_statements = document.get("Statement", [])
        if isinstance(raw_statements, dict):
            statements = [raw_statements]
        elif isinstance(raw_statements, list):
            statements = raw_statements
        else:
            raise ProviderError(
                f"repository {name}: policy Statement must be an object or a list, got {type(raw_statements).__name__}"
            )
        kept = [s for s in statements if s.get("Sid") != "DenyPushArchived"]
        document["Statement"] = kept
        return document, len(kept) != len(statements)
