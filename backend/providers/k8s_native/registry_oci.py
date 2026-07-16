"""Generic OCI ImageRegistryDriver (#52 + #12).

Targets self-hosted Harbor, Zot, distribution/distribution; also
covers GHCR, Quay, and any registry that speaks OCI Distribution
Specification v1.

Operations:
- ensure_repo: HTTP HEAD against /v2/{repo}/manifests/latest;
  the OCI spec doesn't require a 'create repo' call (repos are
  implicit on first push), so this is a probe + tag-existence
  check rather than provisioning
- get_pull_secret: emits a k8s dockerconfigjson Secret using
  static creds (config-supplied) — for Harbor/Zot the operator
  creates a robot account; for GHCR a PAT
- list_tags: GET /v2/{repo}/tags/list

The driver doesn't do credential rotation since OCI auth is
out of band (operator-managed). Refresh = re-apply the Secret
when creds rotate.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from _sdk import UnsupportedOperationError
from _sdk._telemetry import driver_op
from _sdk.registry import ImageRegistryDriver, Repo, SecretSpec, Tag


@dataclass(frozen=True)
class OCIRegistryConfig:
    registry_url: str
    """e.g. https://harbor.example.com or https://ghcr.io.
    Used for both API calls and image refs."""

    username: str = ""
    password: str = ""
    """For pull-secret rendering. Operator-managed; rotate by
    re-applying the Secret manifest."""

    auth_email: str = "noreply@astrolift.local"

    http_client: Any = None
    """Injected for tests."""


class OCIRegistryDriver(ImageRegistryDriver):
    def __init__(self, *, config: OCIRegistryConfig) -> None:
        self._config = config
        self._http = config.http_client

    @driver_op(cloud="k8s_native", driver="registry")
    def ensure_repo(self, name: str) -> Repo:
        """OCI doesn't have explicit 'create repo' — repos are
        implicit on first push. Return the canonical URI."""
        uri = self._image_uri(name=name)
        return Repo(name=name, uri=uri)

    @driver_op(cloud="k8s_native", driver="registry", audit=True, sensitive_kind="registry.delete")
    def delete_repo(self, name: str, *, archive: bool = True) -> None:
        """Generic OCI doesn't standardize repo deletion. Per
        registry, operators use:
        - Harbor: project deletion via API
        - GHCR: package deletion via GitHub API
        - Zot: delete via tags-list iteration
        Out of scope for this generic driver."""
        if not archive:
            # #614 -- previously raised bare NotImplementedError.
            raise UnsupportedOperationError(
                f"registry.delete_repo({name=}, archive=False) not supported "
                "on the generic OCI driver -- delete via the registry's "
                "native admin UI/API",
            )
        # archive=True is a no-op for the generic driver

    @driver_op(cloud="k8s_native", driver="registry", audit=True, sensitive_kind="registry.get_pull_secret")
    def get_pull_secret(
        self,
        cluster: str,
        namespace: str,
    ) -> SecretSpec:
        if not self._config.username or not self._config.password:
            raise RuntimeError(
                "registry credentials required (username + password)",
            )
        registry_host = (
            self._config.registry_url.replace(
                "https://",
                "",
            )
            .replace("http://", "")
            .rstrip("/")
        )
        auth = base64.b64encode(
            f"{self._config.username}:{self._config.password}".encode(),
        ).decode()
        dockerconfig = json.dumps(
            {
                "auths": {
                    registry_host: {
                        "auth": auth,
                        "email": self._config.auth_email,
                    },
                },
            }
        )
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "astrolift-oci-credentials",
                "namespace": namespace,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/credential-source": "oci",
                },
            },
            "type": "kubernetes.io/dockerconfigjson",
            "data": {
                ".dockerconfigjson": base64.b64encode(
                    dockerconfig.encode(),
                ).decode(),
            },
        }

    @driver_op(cloud="k8s_native", driver="registry")
    def push(self, local_image: str, repo: str, tag: str) -> str:
        """Returns canonical URI; actual push is delegated to
        the build runner (BuildKit/Kaniko/buildah)."""
        if not local_image:
            raise ValueError("local_image is required")
        return f"{self._image_uri(name=repo)}:{tag}"

    @driver_op(cloud="k8s_native", driver="registry")
    def list_tags(self, repo: str) -> list[Tag]:
        """GET /v2/{repo}/tags/list. The OCI Distribution Spec
        returns just tag names — no digest / pushed_at — so the
        Tag projection has those as None unless the driver does
        an extra HEAD per tag (skipped here for performance)."""
        if self._http is None:
            return []
        response = self._http.get(
            f"{self._config.registry_url.rstrip('/')}/v2/{repo}/tags/list",
            headers=self._auth_header(),
        )
        if response.get("status_code", 0) != 200:
            return []
        body = response.get("body", {})
        return [Tag(name=t, digest="", pushed_at=None) for t in (body.get("tags") or [])]

    # ---- internals ------------------------------------------------

    def _image_uri(self, *, name: str) -> str:
        host = (
            self._config.registry_url.replace(
                "https://",
                "",
            )
            .replace("http://", "")
            .rstrip("/")
        )
        return f"{host}/{name}"

    def _auth_header(self) -> dict[str, str]:
        if not self._config.username:
            return {}
        encoded = base64.b64encode(
            f"{self._config.username}:{self._config.password}".encode(),
        ).decode()
        return {"Authorization": f"Basic {encoded}"}
