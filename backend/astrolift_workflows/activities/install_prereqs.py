"""Activities for ``InstallClusterPrereqsWorkflow`` (#66).

The bootstrap recipe lives in each provider's driver (see
``BootstrapComponent`` in ``_sdk/cluster.py``). The activity here
takes the operator's selection (which components, plus per-option
overrides), renders a Flux ``HelmRelease`` per selected component (plus a
``HelmRepository`` per unique upstream chart registry) with the
driver's pre-tuned helm values merged with the operator's overrides,
and applies them to the cluster via ``cluster_driver.apply_manifests``.
Each cloud driver provides its own chart coordinates (chart name,
upstream Helm repo URL, pinned version) so EKS, GKE, AKS, and vanilla
k8s all install from the right upstream registries.

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
        "no matches found for",  # kubernetes-client: CRD group not registered
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

    log.info("Flux bootstrap applied — caller should let Temporal retry for CRD registration")


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

    # Collect HelmRepository manifests first (one per unique repo URL,
    # deduplicated so multiple components sharing a registry emit only
    # one HelmRepository resource). We build them in a dict keyed by
    # repo URL, then prepend them to the apply list so Flux can resolve
    # the source reference before the HelmRelease objects land.
    repo_manifests: dict[str, dict[str, Any]] = {}

    for component in components:
        if component.key not in selected_set:
            skipped.append(component.key)
            continue
        if not component.chart_repo_url:
            # No chart for this component (e.g. cloud-native annotation-based
            # TLS where ACM / GKE-managed / AppGW handles certs without an
            # in-cluster controller). Record in applied so the UI reflects
            # the operator's choice; no HelmRelease emitted.
            log.info(
                "install_cluster_prereqs: component %s has no chart — "
                "skipping HelmRelease (cloud-native path)",
                component.key,
            )
            applied.append(component.key)
            continue

        merged_values = _merge_helm_values(
            component.helm_values,
            option_overrides.get(component.key, {}),
        )

        # Slug the repo URL into a valid K8s resource name:
        # strip scheme, replace non-alphanumeric with '-', truncate to 52 chars
        # (HelmRepository name limit is 63; "helmrepo-" prefix + 52 = 61).
        import re

        repo_slug = re.sub(r"[^a-z0-9]+", "-", component.chart_repo_url.lower().split("//")[-1].rstrip("/"))
        repo_slug = repo_slug.strip("-")[:52]
        repo_name = f"helmrepo-{repo_slug}"

        if component.chart_repo_url not in repo_manifests:
            repo_spec: dict[str, Any] = {
                "interval": "1h",
                "url": component.chart_repo_url,
            }
            if component.chart_repo_type == "oci":
                repo_spec["type"] = "oci"
            repo_manifests[component.chart_repo_url] = {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "HelmRepository",
                "metadata": {
                    "name": repo_name,
                    "namespace": target_namespace,
                    "labels": {"astrolift.io/managed-by": "platform"},
                },
                "spec": repo_spec,
            }

        chart_spec: dict[str, Any] = {
            "chart": component.chart_name,
            "sourceRef": {
                "kind": "HelmRepository",
                "name": repo_name,
                "namespace": target_namespace,
            },
        }
        if component.chart_version:
            chart_spec["version"] = component.chart_version

        helm_release = {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {
                "name": f"astrolift-{component.key.replace('_', '-')}",
                "namespace": target_namespace,
                "labels": {
                    "astrolift.io/managed-by": "platform",
                    "astrolift.io/bootstrap-component": component.key,
                },
            },
            "spec": {
                "interval": "5m",
                "chart": {"spec": chart_spec},
                "install": {"createNamespace": True, "remediation": {"retries": 3}},
                "upgrade": {"remediation": {"retries": 3}},
                "values": merged_values,
            },
        }
        resources.append(helm_release)
        applied.append(component.key)

    # Prepend HelmRepository manifests so Flux registers the chart
    # sources before the HelmRelease objects that reference them.
    resources = list(repo_manifests.values()) + resources

    result = driver.apply_manifests(ctx.slug, target_namespace, resources)
    if not result.ok:
        if _flux_crd_missing(result.errors):
            # Flux CRDs aren't registered yet — bootstrap Flux and then let
            # Temporal retry the activity. We don't retry inline because CRD
            # group registration is async and takes 20-60 s after apply; a
            # naive in-process sleep would either be too short (same failure)
            # or waste the activity heartbeat budget. Instead we raise a
            # retriable ApplicationError so Temporal reschedules the attempt
            # after its configured retry delay; by then Flux is fully ready.
            log.info(
                "install_cluster_prereqs: Flux CRDs missing on cluster %s — "
                "bootstrapping Flux; Temporal will retry",
                ctx.slug,
            )
            _ensure_flux_installed(driver, ctx.slug)
            from temporalio.exceptions import ApplicationError

            raise ApplicationError(
                f"Flux bootstrapped on cluster {ctx.slug!r}; "
                "waiting for CRD group registration — Temporal will retry",
                non_retryable=False,
            )

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


def _record_bootstrap_run_sync(
    cluster_id: int,
    actor_user_id: int | None,
    status: str,
    applied: list[str],
    error_message: str,
    started_at_iso: str,
    ended_at_iso: str,
) -> None:
    """Persist a ``ClusterBootstrapRun`` row for a UI-triggered install.

    Mirror of what ``recordClusterBootstrapRun`` (called by the CLI)
    writes, so the "Last bootstrap" panel populates regardless of how
    the operator initiated the install. Best-effort — callers should
    catch exceptions and not let a write failure mask the real outcome.
    """
    from datetime import datetime

    from astrolift_clusters.models import ClusterBootstrapRun, TenantCluster

    cluster = TenantCluster.objects.get(pk=cluster_id)
    user = None
    if actor_user_id is not None:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.filter(pk=actor_user_id).first()

    ClusterBootstrapRun.objects.create(
        tenant_cluster=cluster,
        triggered_by=user,
        status=status,
        installed_releases=[{"name": key} for key in applied],
        chart_version="",  # Flux manages chart versions; not known at apply time
        cli_version="",
        host_info={"triggered_via": "ui"},
        error_message=error_message,
        started_at=datetime.fromisoformat(started_at_iso),
        ended_at=datetime.fromisoformat(ended_at_iso),
    )


@activity.defn(name="astrolift.cluster.record_bootstrap_run")
async def record_cluster_bootstrap_run(
    cluster_id: int,
    actor_user_id: int | None,
    status: str,
    applied: list[str],
    error_message: str,
    started_at_iso: str,
    ended_at_iso: str,
) -> None:
    """Write a ``ClusterBootstrapRun`` record so the UI 'Last bootstrap'
    panel reflects UI-triggered installs, not just CLI runs."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    await sync_to_async(_record_bootstrap_run_sync)(
        cluster_id,
        actor_user_id,
        status,
        applied,
        error_message,
        started_at_iso,
        ended_at_iso,
    )
    log.info(
        "record_cluster_bootstrap_run cluster_id=%d status=%s applied=%d",
        cluster_id,
        status,
        len(applied),
    )


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
