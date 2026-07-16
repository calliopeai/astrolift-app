"""GCP Artifact Registry ImageRegistryDriver (#40)."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
from _sdk.registry import CiPushRole, ImageRegistryDriver, Repo, SecretSpec, Tag
from gcp._errors import NotFoundError, map_api_error

# GitHub Actions OIDC issuer; used in the WIF provider config.
_GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"

# One pool per project — shared across every per-repo SA the driver
# provisions.  Pool IDs must match [a-z][a-z0-9-]{5,31}.
_WIP_POOL_ID = "astrolift-github-actions"
_WIP_POOL_DISPLAY = "Astrolift GitHub Actions"
_WIP_PROVIDER_ID = "github-oidc"

# GCP SA IDs: 6-30 chars, [a-z][a-z0-9-]*.
_SA_ID_MIN = 6
_SA_ID_MAX = 30
_SA_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")


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
    iam_client: Any | None = None
    """``iam_admin_v1.IAMClient`` (or a fake in tests). Lazily
    constructed on first call to ``ensure_ci_push_role``."""

    wip_client: Any | None = None
    """An ``AuthorizedSession``-like client used for the Workload
    Identity Federation REST endpoints (``iam_admin_v1`` does not
    expose WIF pool / provider CRUD). The shim only needs ``.get``,
    ``.post``, and response objects whose ``.json()`` returns the
    REST payload. Lazily constructed via ``google.auth.default()``
    when not supplied."""


class ArtifactRegistryDriver(ImageRegistryDriver):
    def __init__(self, *, config: ArtifactRegistryConfig) -> None:
        self._config = config
        if config.client is not None:
            self._client = config.client
        else:
            from google.cloud import artifactregistry_v1

            self._client = artifactregistry_v1.ArtifactRegistryClient()
        # IAM clients are lazily constructed in ``ensure_ci_push_role``
        # so the driver stays usable for environments that never call
        # it (the import + auth handshake is non-trivial).
        self._iam = config.iam_client
        self._wip = config.wip_client
        self._project_number: str | None = None

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
                    parent=(f"projects/{self._config.project_id}/locations/{self._config.location}")
                )
            else:
                raise map_api_error(exc) from exc

        uri = f"{self._config.location}-docker.pkg.dev/{self._config.project_id}/{self._config.repository_id}/{name}"
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

    @driver_op(cloud="gcp", driver="registry", audit=True, sensitive_kind="registry.create_ci_push_role")
    def ensure_ci_push_role(
        self,
        *,
        repo: str,
        scm_provider: str,
        scm_repo_full_name: str,
    ) -> CiPushRole:
        """Provision (or refresh) GCP Workload Identity Federation so
        GitHub Actions can push to this Artifact Registry repo path
        without long-lived service-account keys.

        Resources:
        * Workload Identity Pool ``astrolift-github-actions`` (one per
          project, shared across repos).
        * OIDC provider ``github-oidc`` inside the pool, trusting
          ``https://token.actions.githubusercontent.com`` with attribute
          mapping ``google.subject=assertion.sub`` /
          ``attribute.repository=assertion.repository``.
        * Per-repo service account ``astrolift-<repo[:20]>-ar-push``
          (truncated to fit GCP's 30-char SA id limit).
        * ``roles/artifactregistry.writer`` binding on the AR repo
          resource granting the SA push.
        * ``roles/iam.workloadIdentityUser`` on the SA letting the WIF
          provider principal impersonate it, scoped to
          ``attribute.repository == <owner>/<repo>`` so another tenant's
          CI in the same project cannot assume it.

        Returns ``CiPushRole(role_ref=<json>, scm_provider='github')``
        where ``role_ref`` is a JSON blob of
        ``{"wip_provider": <full_provider_resource_name>, "sa_email": <email>}``
        that the published CI workflow feeds to
        ``google-github-actions/auth@v2``.

        Idempotent: every step checks for existence (REST 404 / IAM
        ``AlreadyExists`` / binding-member lookup) before mutating.
        """
        if scm_provider != "github":
            raise UnsupportedOperationError(
                "ArtifactRegistryDriver.ensure_ci_push_role only supports "
                f"scm_provider='github' today; got {scm_provider!r}",
            )
        if "/" not in scm_repo_full_name:
            raise ValueError(
                f"scm_repo_full_name must be 'owner/repo'; got {scm_repo_full_name!r}",
            )

        self._ensure_iam_clients()
        project_id = self._config.project_id
        project_number = self._get_project_number()
        sa_id = self._sa_id_for_repo(repo)
        sa_email = f"{sa_id}@{project_id}.iam.gserviceaccount.com"
        sa_resource = f"projects/-/serviceAccounts/{sa_email}"

        self._ensure_wip_pool(project_id=project_id)
        provider_resource = self._ensure_wip_provider(project_id=project_id)
        self._ensure_service_account(project_id=project_id, sa_id=sa_id)
        self._ensure_ar_writer_binding(sa_email=sa_email)
        self._ensure_wif_impersonation(
            project_number=project_number,
            sa_resource=sa_resource,
            scm_repo_full_name=scm_repo_full_name,
        )

        return CiPushRole(
            role_ref=json.dumps(
                {"wip_provider": provider_resource, "sa_email": sa_email},
                separators=(",", ":"),
                sort_keys=True,
            ),
            scm_provider="github",
        )

    # ---- internals ------------------------------------------------

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

    def _ensure_iam_clients(self) -> None:
        if self._iam is None:
            from google.cloud import iam_admin_v1

            self._iam = iam_admin_v1.IAMClient()
        if self._wip is None:
            import google.auth
            import google.auth.transport.requests

            credentials, _ = google.auth.default()
            self._wip = google.auth.transport.requests.AuthorizedSession(
                credentials,
            )

    def _get_project_number(self) -> str:
        """GCP IAM principal sets require the numeric project number,
        not the project id slug.  Resolved once and cached."""
        if self._project_number is not None:
            return self._project_number
        from google.cloud import resourcemanager_v3

        client = resourcemanager_v3.ProjectsClient()
        try:
            project = client.get_project(
                name=f"projects/{self._config.project_id}",
            )
        except Exception as exc:
            raise map_api_error(exc) from exc
        # ``project.name`` is ``projects/<number>``; ``project_id`` is
        # the slug.  Older client versions return only ``name``.
        name = getattr(project, "name", "") or ""
        if "/" not in name:
            raise map_api_error(
                RuntimeError(f"unexpected project resource shape: {name!r}"),
            )
        number = name.split("/", 1)[1]
        self._project_number = number
        return number

    def _sa_id_for_repo(self, repo: str) -> str:
        """Build the SA id for a repo's push role.

        Format: ``astrolift-<repo[:20]>-ar-push``.  GCP SA ids must
        match ``[a-z][a-z0-9-]*`` and be 6-30 chars.  Repo slugs
        already conform; we truncate from the repo segment so the
        fixed prefix/suffix survive name collisions across long
        repo names.
        """
        # ``astrolift-`` = 10, ``-ar-push`` = 8 → 18 fixed chars.
        # Reserve 30 - 18 = 12 for the repo portion to stay under the
        # SA id cap; truncate the spec's documented 20 down to 12.
        repo_slug = repo[:12].rstrip("-")
        sa_id = f"astrolift-{repo_slug}-ar-push"
        if len(sa_id) < _SA_ID_MIN or len(sa_id) > _SA_ID_MAX:
            raise ValueError(
                f"derived SA id {sa_id!r} violates GCP length bounds ({_SA_ID_MIN}-{_SA_ID_MAX} chars)",
            )
        if not _SA_ID_RE.match(sa_id):
            raise ValueError(
                f"derived SA id {sa_id!r} violates GCP charset ({_SA_ID_RE.pattern})",
            )
        return sa_id

    def _wip_pool_resource(self, *, project_id: str) -> str:
        return f"projects/{project_id}/locations/global/workloadIdentityPools/{_WIP_POOL_ID}"

    def _wip_provider_resource(self, *, project_id: str) -> str:
        return f"{self._wip_pool_resource(project_id=project_id)}/providers/{_WIP_PROVIDER_ID}"

    def _ensure_wip_pool(self, *, project_id: str) -> None:
        base = "https://iam.googleapis.com/v1"
        get_url = f"{base}/projects/{project_id}/locations/global/workloadIdentityPools/{_WIP_POOL_ID}"
        get_resp = self._wip.get(get_url)
        status = getattr(get_resp, "status_code", 200)
        if status == 200:
            return
        if status != 404:
            raise map_api_error(
                RuntimeError(
                    f"WIF pool lookup failed: HTTP {status} {get_resp.text!r}"
                    if hasattr(get_resp, "text")
                    else f"WIF pool lookup failed: HTTP {status}",
                ),
            )
        create_url = (
            f"{base}/projects/{project_id}/locations/global/workloadIdentityPools?workloadIdentityPoolId={_WIP_POOL_ID}"
        )
        create_resp = self._wip.post(
            create_url,
            json={
                "displayName": _WIP_POOL_DISPLAY,
                "description": (
                    "Astrolift-managed pool federating GitHub Actions OIDC tokens for image-registry push."
                ),
            },
        )
        create_status = getattr(create_resp, "status_code", 200)
        if create_status >= 400 and create_status != 409:
            raise map_api_error(
                RuntimeError(
                    f"WIF pool create failed: HTTP {create_status} {getattr(create_resp, 'text', '')!r}",
                ),
            )

    def _ensure_wip_provider(self, *, project_id: str) -> str:
        base = "https://iam.googleapis.com/v1"
        pool_path = f"projects/{project_id}/locations/global/workloadIdentityPools/{_WIP_POOL_ID}"
        provider_resource = self._wip_provider_resource(project_id=project_id)
        get_url = f"{base}/{pool_path}/providers/{_WIP_PROVIDER_ID}"
        get_resp = self._wip.get(get_url)
        status = getattr(get_resp, "status_code", 200)
        if status == 200:
            return provider_resource
        if status != 404:
            raise map_api_error(
                RuntimeError(
                    f"WIF provider lookup failed: HTTP {status}",
                ),
            )
        create_url = f"{base}/{pool_path}/providers?workloadIdentityPoolProviderId={_WIP_PROVIDER_ID}"
        create_resp = self._wip.post(
            create_url,
            json={
                "displayName": "GitHub Actions OIDC",
                "description": (
                    "Astrolift-managed provider trusting GitHub Actions OIDC "
                    "tokens; per-SA impersonation scoped via "
                    "attribute.repository."
                ),
                "attributeMapping": {
                    "google.subject": "assertion.sub",
                    "attribute.repository": "assertion.repository",
                },
                "oidc": {"issuerUri": _GITHUB_OIDC_ISSUER},
            },
        )
        create_status = getattr(create_resp, "status_code", 200)
        if create_status >= 400 and create_status != 409:
            raise map_api_error(
                RuntimeError(
                    f"WIF provider create failed: HTTP {create_status}",
                ),
            )
        return provider_resource

    def _ensure_service_account(self, *, project_id: str, sa_id: str) -> None:
        try:
            self._iam.create_service_account(
                name=f"projects/{project_id}",
                account_id=sa_id,
                service_account={
                    "display_name": f"astrolift {sa_id}",
                    "description": (
                        "Astrolift Artifact Registry push role for GitHub Actions CI (managed by ImageRegistryDriver)."
                    ),
                },
            )
        except Exception as exc:
            if type(exc).__name__ == "AlreadyExists":
                return
            raise map_api_error(exc) from exc

    def _ar_repo_resource(self) -> str:
        return (
            f"projects/{self._config.project_id}"
            f"/locations/{self._config.location}"
            f"/repositories/{self._config.repository_id}"
        )

    def _ensure_ar_writer_binding(self, *, sa_email: str) -> None:
        resource = self._ar_repo_resource()
        member = f"serviceAccount:{sa_email}"
        role = "roles/artifactregistry.writer"
        try:
            policy = self._client.get_iam_policy(resource=resource)
        except Exception as exc:
            raise map_api_error(exc) from exc

        bindings = getattr(policy, "bindings", None)
        if bindings is None:
            raise map_api_error(
                RuntimeError("AR IAM policy missing bindings collection"),
            )

        binding = next((b for b in bindings if b.role == role), None)
        if binding is None:
            if hasattr(bindings, "add"):
                bindings.add(role=role, members=[member])
            else:
                bindings.append(_NewBinding(role=role, members=[member]))
        else:
            if member in binding.members:
                return  # already wired; skip the set call entirely
            binding.members.append(member)

        try:
            self._client.set_iam_policy(resource=resource, policy=policy)
        except Exception as exc:
            raise map_api_error(exc) from exc

    def _ensure_wif_impersonation(
        self,
        *,
        project_number: str,
        sa_resource: str,
        scm_repo_full_name: str,
    ) -> None:
        member = (
            f"principalSet://iam.googleapis.com/projects/{project_number}"
            f"/locations/global/workloadIdentityPools/{_WIP_POOL_ID}"
            f"/attribute.repository/{scm_repo_full_name}"
        )
        role = "roles/iam.workloadIdentityUser"
        try:
            policy = self._iam.get_iam_policy(resource=sa_resource)
        except Exception as exc:
            raise map_api_error(exc) from exc

        bindings = getattr(policy, "bindings", None)
        if bindings is None:
            raise map_api_error(
                RuntimeError("SA IAM policy missing bindings collection"),
            )

        binding = next((b for b in bindings if b.role == role), None)
        if binding is None:
            if hasattr(bindings, "add"):
                bindings.add(role=role, members=[member])
            else:
                bindings.append(_NewBinding(role=role, members=[member]))
        else:
            if member in binding.members:
                return
            binding.members.append(member)

        try:
            self._iam.set_iam_policy(resource=sa_resource, policy=policy)
        except Exception as exc:
            raise map_api_error(exc) from exc


@dataclass
class _NewBinding:
    """Fallback shape when the live IAM policy object's bindings
    collection isn't a Google ``RepeatedComposite`` (no ``.add``).
    Real protobufs do support ``.add``; test fakes here use plain
    lists, in which case we append a struct shaped like the
    protobuf message."""

    role: str
    members: list[str]
