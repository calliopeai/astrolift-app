"""
Secret materialization for in-cluster pods (#19, spec 12 §6.2).

Pure-Python rendering of the k8s Secret / ExternalSecret CR shapes
the deploy workflow applies.

Two modes:

* **In-cluster Secret** — default. Workflow writes Secret objects
  with the data inline (already encrypted at rest via the cluster's
  KMS provider). Pods mount via ``envFrom: - secretRef``.
* **External Secrets Operator** — when the backend is
  ``external_secrets_operator``, render an ``ExternalSecret`` CR
  pointing at the per-org ``SecretStore`` instead. ESO syncs from
  the source-of-truth secret backend (Vault, AWS Secrets Manager,
  …) into a generated k8s Secret.

Naming convention (spec 12 §6.2):
  - ``astrolift-app-env``         — literal app env + bundle vars
  - ``astrolift-svc-<service>``   — managed-service binding envs

Returns plain dicts; the worker calls into the cluster driver's
``apply`` method with these.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import Enum

# Spec 12 §6.2 naming.
APP_ENV_SECRET_NAME = "astrolift-app-env"


def service_secret_name(service_name: str) -> str:
    """``astrolift-svc-<service-name>`` per spec. ``service-name`` is
    the binding's ``name`` from ``[[managed_services]]``."""
    if not service_name:
        raise ValueError("service_name is required")
    return f"astrolift-svc-{service_name}"


class SecretBackend(str, Enum):
    """Which mechanism populates the in-pod Secret."""

    INLINE = "inline"
    """Workflow writes a k8s Secret object directly with the
    plaintext (cluster-side at-rest encryption applies via the
    apiserver's KMSEncryptionConfiguration)."""

    EXTERNAL_SECRETS_OPERATOR = "external_secrets_operator"
    """Workflow writes an ExternalSecret CR; ESO syncs values from
    the upstream backend (Vault / AWS SM / GCP SM) into a
    generated k8s Secret."""


# ---- inline Secret rendering ----------------------------------------


def render_inline_secret(
    *,
    name: str,
    namespace: str,
    string_data: Mapping[str, str],
    labels: Mapping[str, str] | None = None,
) -> dict:
    """Render a k8s Secret object as a plain dict.

    ``string_data`` is preferred over ``data`` (no manual base64;
    apiserver does it). Caller's responsibility to keep the values
    out of logs — this module never logs them.
    """
    if not name:
        raise ValueError("name is required")
    if not namespace:
        raise ValueError("namespace is required")
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": dict(labels or {}),
        },
        "type": "Opaque",
        "stringData": dict(string_data),
    }


# ---- ExternalSecret CR rendering -----------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ExternalSecretMapping:
    """One key in the generated Secret + where to fetch it from."""

    target_key: str
    """Key in the generated k8s Secret (e.g. POSTGRES_PASSWORD)."""

    remote_ref_key: str
    """Path/name in the upstream backend (e.g. 'postgres/main')."""

    remote_ref_property: str = ""
    """Optional sub-key when the upstream stores a JSON blob."""


def render_external_secret(
    *,
    name: str,
    namespace: str,
    secret_store: str,
    mappings: list[ExternalSecretMapping],
    refresh_interval: str = "1h",
    labels: Mapping[str, str] | None = None,
) -> dict:
    """Render an ExternalSecret CR (external-secrets.io/v1).

    ``secret_store`` references a ClusterSecretStore the platform
    pre-creates per org; ESO uses it to authenticate to the
    upstream backend.

    ``refresh_interval`` is how often ESO re-fetches. 1h is the
    default — short enough that rotated upstream values reach pods
    within an hour; long enough not to hammer the backend.
    """
    if not mappings:
        raise ValueError("mappings cannot be empty")
    if not secret_store:
        raise ValueError("secret_store is required")

    data_entries = []
    for m in mappings:
        if not m.target_key or not m.remote_ref_key:
            raise ValueError("ExternalSecretMapping requires target_key and remote_ref_key")
        ref = {"key": m.remote_ref_key}
        if m.remote_ref_property:
            ref["property"] = m.remote_ref_property
        data_entries.append(
            {
                "secretKey": m.target_key,
                "remoteRef": ref,
            }
        )

    return {
        "apiVersion": "external-secrets.io/v1",
        "kind": "ExternalSecret",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": dict(labels or {}),
        },
        "spec": {
            "refreshInterval": refresh_interval,
            "secretStoreRef": {
                "name": secret_store,
                "kind": "ClusterSecretStore",
            },
            "target": {"name": name, "creationPolicy": "Owner"},
            "data": data_entries,
        },
    }


# ---- workload mount rendering --------------------------------------


def env_from_ref(secret_name: str) -> dict:
    """Render an ``envFrom: - secretRef`` entry for a container's
    PodSpec. Caller appends to the workload's ``envFrom`` list."""
    return {"secretRef": {"name": secret_name}}


def render_pod_env_from(
    *,
    app_env_secret: str = APP_ENV_SECRET_NAME,
    service_secret_names: list[str] | None = None,
) -> list[dict]:
    """Compose the full ``envFrom`` list for a workload container.

    Order matters in k8s: later entries override earlier ones for
    the same key. Service secrets are listed before the app env so
    an explicit app env literal can override a default the binding
    provides (matches the env_injection precedence from #117).
    """
    out: list[dict] = []
    for name in service_secret_names or []:
        out.append(env_from_ref(name))
    out.append(env_from_ref(app_env_secret))
    return out


# ---- backend dispatch ----------------------------------------------


def materialize(
    *,
    backend: SecretBackend,
    secret_name: str,
    namespace: str,
    string_data: Mapping[str, str] | None = None,
    external_mappings: list[ExternalSecretMapping] | None = None,
    secret_store: str = "",
    labels: Mapping[str, str] | None = None,
) -> dict:
    """Dispatch to the right rendering for the configured backend.

    INLINE requires ``string_data``; EXTERNAL_SECRETS_OPERATOR
    requires ``external_mappings`` + ``secret_store``.
    """
    if backend == SecretBackend.INLINE:
        if string_data is None:
            raise ValueError("INLINE backend requires string_data")
        return render_inline_secret(
            name=secret_name,
            namespace=namespace,
            string_data=string_data,
            labels=labels,
        )
    if backend == SecretBackend.EXTERNAL_SECRETS_OPERATOR:
        if not external_mappings:
            raise ValueError("EXTERNAL_SECRETS_OPERATOR backend requires external_mappings")
        if not secret_store:
            raise ValueError("EXTERNAL_SECRETS_OPERATOR backend requires secret_store")
        return render_external_secret(
            name=secret_name,
            namespace=namespace,
            secret_store=secret_store,
            mappings=external_mappings,
            labels=labels,
        )
    raise ValueError(f"unsupported backend {backend!r}")
