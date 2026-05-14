"""Authenticated ``kubernetes`` client factory for a TenantCluster row.

Each ``TenantCluster`` row carries an ``auth_method`` and an
``auth_config`` blob that names the credential material. The mapping
to a usable ``kubernetes.client.ApiClient`` is:

  ``kubeconfig``
      ``auth_config = {"kubeconfig": "<full yaml blob>",
                       "context": "<optional context name>"}``
      The blob is fed to ``kubernetes.config.load_kube_config_from_dict``.

  ``service_account_token``
      ``auth_config = {"token": "<bearer>", "ca_cert": "<pem optional>"}``
      A ``Configuration`` is built from the cluster's ``endpoint`` +
      ``ca_cert`` (or per-row override) with the bearer token attached.

  ``exec_plugin``
      Not supported at this layer — exec plugins need a real kubeconfig
      file on disk plus a binary in ``$PATH`` (gke-gcloud-auth-plugin,
      aws-iam-authenticator, etc.). The provider plugin driver mints
      these clients with a proper temp-file kubeconfig; the resolver
      surface gets a clear error so the UI renders something useful
      instead of a stack trace.

The ``kubernetes`` package is imported lazily so unit tests + the
schema-export command don't need it installed.
"""

from __future__ import annotations

import base64
import tempfile
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster


class ClusterClientError(Exception):
    """Raised when a ``TenantCluster`` row can't be turned into a
    usable ``kubernetes.client.ApiClient``. The resolver layer maps
    this to an empty pod list + an explanatory error so the UI
    doesn't blow up on misconfigured clusters."""


@dataclass(frozen=True, slots=True)
class ClusterClient:
    """An authenticated ``kubernetes.client.ApiClient`` bundled with
    the bits a resolver needs to reason about it.

    The ``namespace`` field is the prefix the cluster uses for app
    namespaces. The actual per-app namespace name is computed by
    the caller (``f"{org_slug}-{app_slug}"`` or
    ``app.k8s_namespace`` when explicitly set)."""

    api_client: Any
    """``kubernetes.client.ApiClient``."""

    cluster_slug: str
    namespace_prefix: str


def client_for_cluster(cluster: TenantCluster) -> ClusterClient:
    """Build an authenticated ``kubernetes`` client for ``cluster``.

    The resolver layer is responsible for catching
    :class:`ClusterClientError` and turning it into a friendly
    empty-state for the UI."""
    try:
        from kubernetes import client, config
    except ImportError as exc:  # pragma: no cover — packaging guard
        raise ClusterClientError(
            "kubernetes python client is not installed; add it to the backend Pipfile",
        ) from exc

    method = cluster.auth_method
    auth = cluster.auth_config or {}

    if method == "kubeconfig":
        kubeconfig_blob = (auth.get("kubeconfig") or "").strip()
        if not kubeconfig_blob:
            raise ClusterClientError(
                f"cluster {cluster.slug}: auth_method=kubeconfig but auth_config.kubeconfig is empty",
            )
        # ``load_kube_config_from_dict`` accepts a parsed dict; some
        # rows hold raw YAML. Parse on the side of caution.
        import yaml  # PyYAML is a Django transitive dep

        try:
            kubeconfig_dict = yaml.safe_load(kubeconfig_blob)
        except yaml.YAMLError as exc:
            raise ClusterClientError(
                f"cluster {cluster.slug}: kubeconfig is not valid YAML",
            ) from exc
        cfg = client.Configuration()
        context = auth.get("context") or None
        try:
            config.load_kube_config_from_dict(
                kubeconfig_dict,
                context=context,
                client_configuration=cfg,
            )
        except Exception as exc:  # noqa: BLE001 — many subtypes from k8s
            raise ClusterClientError(
                f"cluster {cluster.slug}: kubeconfig load failed: {exc}",
            ) from exc
        api_client = client.ApiClient(configuration=cfg)

    elif method == "service_account_token":
        token = (auth.get("token") or "").strip()
        if not token:
            raise ClusterClientError(
                f"cluster {cluster.slug}: auth_method=service_account_token but auth_config.token is empty",
            )
        endpoint = (cluster.endpoint or "").rstrip("/")
        if not endpoint:
            raise ClusterClientError(
                f"cluster {cluster.slug}: endpoint is required for service_account_token auth",
            )
        cfg = client.Configuration()
        cfg.host = endpoint
        cfg.api_key = {"authorization": f"Bearer {token}"}
        ca_cert = (auth.get("ca_cert") or cluster.ca_cert or "").strip()
        if ca_cert:
            # Some operators paste base64'd CAs (kubeconfig format).
            # Best-effort decode; on failure assume the value is
            # already PEM.
            if "BEGIN CERTIFICATE" not in ca_cert:
                try:
                    ca_cert = base64.b64decode(ca_cert).decode("utf-8")
                except Exception:  # noqa: BLE001
                    pass
            ca_file = tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False)
            ca_file.write(ca_cert)
            ca_file.flush()
            cfg.ssl_ca_cert = ca_file.name
            cfg.verify_ssl = True
        else:
            # No CA on the row — skip verification rather than break
            # the surface. Production rows should always carry the CA.
            cfg.verify_ssl = False
        api_client = client.ApiClient(configuration=cfg)

    elif method == "exec_plugin":
        raise ClusterClientError(
            f"cluster {cluster.slug}: exec_plugin auth is not "
            "supported by the control-plane k8s helper; the provider "
            "plugin driver mints clients for this auth method",
        )
    else:
        raise ClusterClientError(
            f"cluster {cluster.slug}: unknown auth_method {method!r}",
        )

    return ClusterClient(
        api_client=api_client,
        cluster_slug=cluster.slug,
        namespace_prefix=cluster.default_namespace_prefix or "",
    )


def namespace_for_app(app: Any) -> str:
    """Per-app namespace, mirroring what the manifest renderer uses.

    Resolution order:
      1. ``app.k8s_namespace`` (explicit override on the row)
      2. ``f"{org_slug}-{app_slug}"`` (renderer default)

    The renderer's choice lives in ``astrolift_registry/schema/queries.py``
    — we replicate it here so the observability surface stays in sync
    when an operator hasn't explicitly pinned a namespace."""
    explicit = (getattr(app, "k8s_namespace", "") or "").strip()
    if explicit:
        return explicit
    org_slug = getattr(getattr(app, "organization", None), "slug", "") or ""
    return f"{org_slug}-{app.slug}"
