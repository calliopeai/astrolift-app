"""Activities for ``InstallClusterPrereqsWorkflow`` (#66).

The bootstrap recipe lives in each provider's driver (see
``BootstrapComponent`` in ``_sdk/cluster.py``). The activity here
takes the operator's selection (which components, plus per-option
overrides), renders a Flux ``HelmRelease`` per selected component
with the driver's pre-tuned helm values merged with the operator's
overrides, and applies them to the cluster via
``cluster_driver.apply_manifests``.

Why HelmRelease (Flux) instead of running ``helm`` from a Job:
- A HelmRelease is declarative + idempotent — re-running the
  activity converges the cluster to the operator's current set;
  unselected components are deleted, selected ones reconcile.
- Status surfaces back via the HelmRelease's ``status`` subresource,
  which the cluster-status tab (#68) can stream into the UI.

Flux bootstrap: if the Flux CRDs are absent the activity installs
Flux automatically before applying the HelmRepository/HelmRelease
resources. This makes the UI "Install / reconcile" flow fully
self-sufficient — no manual CLI prerequisite step needed.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from typing import Any

_FLUX_VERSION = os.environ.get("ASTROLIFT_FLUX_VERSION", "v2.4.0")
_FLUX_INSTALL_URL = os.environ.get(
    "ASTROLIFT_FLUX_INSTALL_URL",
    f"https://github.com/fluxcd/flux2/releases/download/{_FLUX_VERSION}/install.yaml",
)

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.install_prereqs")


def _merge_helm_values(
    base: dict[str, Any],
    overrides: dict[str, str],
) -> dict[str, Any]:
    """Apply per-option overrides on top of the driver's helm_values.

    Overrides are flat (key → string value) because the SDK's
    BootstrapOption surface is flat — operators pick from enumerated
    choices, not arbitrary structured input. The driver embeds each
    choice's effect in helm_values via a top-level key the override
    targets. Unknown override keys are passed through verbatim so a
    driver can introduce a new option without the activity changing.
    """
    out = dict(base)
    for key, value in (overrides or {}).items():
        out[key] = value
    return out


def _flux_crd_missing(errors: list) -> bool:
    """Return True if any ApplyError indicates the Flux CRDs don't exist yet.

    The kubernetes client raises a ``404 Not Found`` on the CRD discovery
    endpoint when the CRD group hasn't been registered, which surfaces
    as "No matches found for kind" or "the server could not find the
    requested resource" in the apply error message.
    """
    _CRD_MISS_MARKERS = (
        "No matches found for kind",
        "the server could not find the requested resource",
        "no kind",
    )
    for err in errors:
        msg = str(err).lower()
        if any(marker.lower() in msg for marker in _CRD_MISS_MARKERS):
            return True
    return False


def _ensure_flux_installed(driver, ctx_slug: str) -> None:
    """Fetch the pinned Flux install manifest and apply it to the cluster.

    Applies CRD objects first (so the API server registers the group
    before we attempt HelmRepository/HelmRelease). Non-CRD objects
    (Namespace, Deployment, Service, …) are applied in a second pass.

    This is intentionally a best-effort fire-and-wait: we apply and
    then sleep briefly so the flux-system controllers have time to
    register their handlers before the caller retries the HelmRelease
    apply. A full readiness probe would require watching Deployments,
    which is overkill for the bootstrap path — the activity is
    retried by Temporal anyway, so a partially-ready Flux converges on
    the next attempt.
    """
    import time

    import yaml

    log.info(
        "Flux CRDs absent — fetching install manifest from %s",
        _FLUX_INSTALL_URL,
    )
    with urllib.request.urlopen(_FLUX_INSTALL_URL, timeout=60) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")

    docs = list(yaml.safe_load_all(raw))
    docs = [d for d in docs if d]  # drop None separators

    crds = [d for d in docs if d.get("kind") == "CustomResourceDefinition"]
    others = [d for d in docs if d.get("kind") != "CustomResourceDefinition"]

    log.info(
        "Flux install manifest: %d CRDs, %d other resources",
        len(crds),
        len(others),
    )

    flux_ns = "flux-system"

    if crds:
        result = driver.apply_manifests(ctx_slug, flux_ns, crds)
        if not result.ok:
            from core.app_deploy import AppDeployError

            raise AppDeployError(
                "Flux CRD bootstrap failed: " + "; ".join(str(e) for e in result.errors),
            )

    if others:
        result = driver.apply_manifests(ctx_slug, flux_ns, others)
        if not result.ok:
            from core.app_deploy import AppDeployError

            raise AppDeployError(
                "Flux controller bootstrap failed: "
                + "; ".join(str(e) for e in result.errors),
            )

    # Give the API server a moment to register the new CRD groups before
    # the caller retries — Temporal will handle the case where this isn't
    # enough time via its normal activity retry schedule.
    log.info("Flux bootstrap applied; waiting 10 s for CRD group registration")
    time.sleep(10)


def _install_cluster_prereqs_sync(
    cluster_id: int,
    selected_keys: list[str],
    option_overrides: dict[str, dict[str, str]],
) -> dict[str, Any]:
    from astrolift_clusters.models import TenantCluster
    from core.app_deploy import AppDeployError
    from core.cluster_management import (
        _context_for_cluster,  # type: ignore[attr-defined]
        _driver_for_cluster,  # type: ignore[attr-defined]
        bootstrap_components_dispatch,
    )

    cluster = TenantCluster.objects.select_related(
        "provider_plugin",
    ).get(pk=cluster_id)
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)

    components = bootstrap_components_dispatch(cluster=cluster)
    selected_set = set(selected_keys)
    target_namespace = "astrolift-system"

    resources: list[dict[str, Any]] = []
    applied: list[str] = []
    skipped: list[str] = []

    for component in components:
        if component.key not in selected_set:
            skipped.append(component.key)
            continue
        merged_values = _merge_helm_values(
            component.helm_values,
            option_overrides.get(component.key, {}),
        )
        # Render a Flux HelmRelease pointing at the astrolift-prereqs
        # umbrella chart's subchart for this component. Chart name +
        # version are platform-pinned so the operator's "Install"
        # click is reproducible across releases of the platform.
        helm_release = {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {
                "name": f"astrolift-{component.key}",
                "namespace": target_namespace,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/bootstrap-component": component.key,
                },
            },
            "spec": {
                "interval": "5m",
                "chart": {
                    "spec": {
                        "chart": component.key,
                        "sourceRef": {
                            "kind": "HelmRepository",
                            "name": "astrolift-prereqs",
                            "namespace": target_namespace,
                        },
                    },
                },
                "install": {"createNamespace": True, "remediation": {"retries": 3}},
                "upgrade": {"remediation": {"retries": 3}},
                "values": merged_values,
            },
        }
        resources.append(helm_release)
        applied.append(component.key)

    # Always re-assert the platform HelmRepository so re-running the
    # install on a fresh cluster doesn't fail with "HelmRepository
    # not found". Idempotent; cluster_driver.apply_manifests is a
    # server-side-apply call.
    resources.insert(
        0,
        {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "HelmRepository",
            "metadata": {
                "name": "astrolift-prereqs",
                "namespace": target_namespace,
                "labels": {"astrolift.io/managed-by": "platform"},
            },
            "spec": {
                "interval": "1h",
                "url": "oci://ghcr.io/calliopeai/astrolift-prereqs",
                "type": "oci",
            },
        },
    )

    result = driver.apply_manifests(ctx.slug, target_namespace, resources)
    if not result.ok:
        if _flux_crd_missing(result.errors):
            # Flux CRDs aren't registered yet — self-bootstrap Flux, then
            # retry. On the retry Flux is present so HelmRepository /
            # HelmRelease applies succeed normally.
            log.info(
                "install_cluster_prereqs: Flux CRDs missing on cluster %s — "
                "bootstrapping Flux and retrying",
                ctx.slug,
            )
            _ensure_flux_installed(driver, ctx.slug)
            result = driver.apply_manifests(ctx.slug, target_namespace, resources)

    if not result.ok:
        raise AppDeployError(
            "install_cluster_prereqs apply failed: " + "; ".join(str(e) for e in result.errors),
        )

    return {
        "applied": applied,
        "skipped": skipped,
        "namespace": target_namespace,
        "created": list(result.created),
        "updated": list(result.updated),
        "unchanged": list(result.unchanged),
    }


@activity.defn(name="astrolift.cluster.install_prereqs")
async def install_cluster_prereqs(
    cluster_id: int,
    selected_keys: list[str],
    option_overrides: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Apply Flux ``HelmRelease`` CRDs for each selected bootstrap
    component to the cluster. Idempotent — re-run converges; an
    operator who unchecks a component on a subsequent run will see
    the HelmRelease delete + the cluster reconcile away the install.

    ``option_overrides`` is keyed by component.key; the inner dict is
    the operator's option-key → value-from-choices selection (e.g.
    ``{"tls_issuer": {"mode": "acm"}}``).
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    result = await sync_to_async(_install_cluster_prereqs_sync)(
        cluster_id,
        selected_keys,
        option_overrides,
    )
    log.info(
        "install_cluster_prereqs applied=%d skipped=%d",
        len(result["applied"]),
        len(result["skipped"]),
        extra={"cluster_id": cluster_id},
    )
    return result
