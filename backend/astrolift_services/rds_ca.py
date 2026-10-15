"""Trust the Amazon RDS CA in workloads bound to an RDS-family service (#2404).

AWS managed database URLs carry ``sslmode=require``. libpq reads that as
"encrypt, do not verify", but node-postgres 8.x reads it as verify-full, and
Node does not ship the Amazon RDS CA, so every Node app on an RDS binding
failed with ``SELF_SIGNED_CERT_IN_CHAIN``. The URL is left alone
(``uselibpqcompat`` breaks libpq clients, ``no-verify`` drops verification);
instead the workload is handed the CA: the bundle in a ConfigMap mounted
read-only, with ``NODE_EXTRA_CA_CERTS`` and ``PGSSLROOTCERT`` pointing at it.

The two env vars come from a second ConfigMap placed first in ``envFrom``.
Kubernetes lets later ``envFrom`` sources and every explicit ``env`` entry
win, so a value the app sets itself (manifest ``env``, a literal secret, a
bundle, a binding) is never overridden.

The bundle is vendored at ``data/rds-global-bundle.pem`` so a deploy never
reaches out to the internet. Source:
https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
Refresh it when AWS publishes a new region or rotates a CA::

    curl -sSfo backend/astrolift_services/data/rds-global-bundle.pem \\
        https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from core.install_restrictions import RDS_BACKED

#: Variants whose server certificates chain to the RDS CA. RDS Proxy and
#: Neptune present ACM certificates under Amazon's public roots, which Node
#: and libpq already trust.
RDS_CA_VARIANTS: frozenset[tuple[str, str]] = RDS_BACKED - {
    ("database_proxy", "rds_proxy"),
    ("graph_db", "neptune"),
    ("graph_db", "neptune_serverless"),
}

BUNDLE_KEY = "rds-global-bundle.pem"
MOUNT_DIR = "/etc/astrolift/rds-ca"
BUNDLE_PATH = f"{MOUNT_DIR}/{BUNDLE_KEY}"
VOLUME_NAME = "astrolift-rds-ca"

_BUNDLE_FILE = Path(__file__).parent / "data" / BUNDLE_KEY


@cache
def bundle_pem() -> str:
    return _BUNDLE_FILE.read_text(encoding="ascii")


def bundle_configmap_name(app_slug: str) -> str:
    from _sdk.k8s_naming import dns_label

    return dns_label("astrolift", "rds-ca", app_slug)


def env_configmap_name(app_slug: str) -> str:
    from _sdk.k8s_naming import dns_label

    return dns_label("astrolift", "rds-ca-env", app_slug)


def rds_binding_secret_names(app_environment) -> set[str]:
    """Bindings Secret names that carry at least one RDS-family service."""
    from astrolift_workflows.activities.app_lifecycle import (
        _binding_workloads_for_service,
        _bindings_secret_name,
        _managed_services_for_environment,
    )

    app_slug = app_environment.registered_app.slug
    names: set[str] = set()
    for svc in _managed_services_for_environment(app_environment).prefetch_related("attachments"):
        if (str(svc.kind), str(svc.variant or "")) not in RDS_CA_VARIANTS:
            continue
        selectors = _binding_workloads_for_service(svc, app_environment)
        if "*" in selectors:
            names.add(_bindings_secret_name(app_slug))
        else:
            names.update(_bindings_secret_name(app_slug, name) for name in selectors)
    return names


def _pod_spec(resource: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(resource.get("kind") or "")
    spec = resource.get("spec") or {}
    if kind == "CronJob":
        spec = ((spec.get("jobTemplate") or {}).get("spec")) or {}
    elif kind == "Service" and not str(resource.get("apiVersion") or "").startswith("serving.knative.dev/"):
        return None
    elif kind not in {"Deployment", "StatefulSet", "DaemonSet", "Job", "Service"}:
        return None
    return (spec.get("template") or {}).get("spec")


def inject_rds_ca(
    resources: list[dict[str, Any]],
    *,
    app_slug: str,
    namespace: str,
    secret_names: set[str],
) -> list[dict[str, Any]]:
    """Mount the RDS CA into every container fed by one of ``secret_names``.

    Returns ``resources`` unchanged when no container is; otherwise adds the
    two ConfigMaps the injected containers read.
    """
    if not secret_names:
        return resources
    env_name = env_configmap_name(app_slug)
    bundle_name = bundle_configmap_name(app_slug)
    injected = False
    for resource in resources:
        pod_spec = _pod_spec(resource)
        if pod_spec is None:
            continue
        for container in [*(pod_spec.get("initContainers") or []), *(pod_spec.get("containers") or [])]:
            refs = {(row.get("secretRef") or {}).get("name") for row in container.get("envFrom") or [] if row}
            if not refs & secret_names:
                continue
            if not any(
                (row.get("configMapRef") or {}).get("name") == env_name for row in container["envFrom"]
            ):
                container["envFrom"].insert(0, {"configMapRef": {"name": env_name}})
            mounts = container.setdefault("volumeMounts", [])
            if not any(m.get("name") == VOLUME_NAME for m in mounts):
                mounts.append({"name": VOLUME_NAME, "mountPath": MOUNT_DIR, "readOnly": True})
            volumes = pod_spec.setdefault("volumes", [])
            if not any(v.get("name") == VOLUME_NAME for v in volumes):
                volumes.append({"name": VOLUME_NAME, "configMap": {"name": bundle_name}})
            injected = True
    if not injected:
        return resources
    labels = {"astrolift.io/managed-by": "astrolift", "astrolift.io/rds-ca-for": app_slug}
    configmaps = [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": bundle_name, "namespace": namespace, "labels": labels},
            "data": {BUNDLE_KEY: bundle_pem()},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": env_name, "namespace": namespace, "labels": labels},
            "data": {"NODE_EXTRA_CA_CERTS": BUNDLE_PATH, "PGSSLROOTCERT": BUNDLE_PATH},
        },
    ]
    return sorted(
        [*resources, *configmaps],
        key=lambda r: (str(r.get("kind", "")), str((r.get("metadata") or {}).get("name", ""))),
    )
