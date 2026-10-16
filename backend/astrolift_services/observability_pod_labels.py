"""Route a bound workload's container logs into its Cloud Operations bundle (#2099).

A GCP Cloud Operations bundle takes the GKE container entries of pods carrying
its pod label into its own log bucket, and keeps them out of ``_Default``. The
workload holds no Cloud Logging write role, so the label is its whole write
path: whoever sets it chooses the bucket a pod's stdout and stderr land in.

The platform stamps it on the pods of each workload a log-writing bundle is
bound to, from the bundle's recorded handle and config, and nothing else may
set it. A rendered pod or controller that already carries the key, in any
spelling GKE folds onto the same log label, is refused rather than overwritten,
so a value a manifest supplied never reaches a cluster.
"""

from __future__ import annotations

from typing import Any

#: The workload kinds whose pods the renderer emits through a pod template.
_POD_TEMPLATE_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet", "Job"})


class ObservabilityLabelError(ValueError):
    """A render the platform will not label, and so will not apply."""


def _reserved(key: str) -> bool:
    """Whether ``key`` reaches a GKE container entry as the bundle label.

    GKE folds each ``.`` of a pod label key to ``_`` when it copies the label
    onto the entry, so ``astrolift.io.observability.bundle`` lands on the same
    log label as the platform's key.
    """
    from gcp.managed.observability_cloud_operations import BUNDLE_POD_LABEL

    return key.replace(".", "_") == BUNDLE_POD_LABEL


def _pod_template(resource: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(resource.get("kind") or "")
    spec = resource.get("spec") or {}
    if kind == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    elif kind == "Service":
        if not str(resource.get("apiVersion") or "").startswith("serving.knative.dev/"):
            return None
    elif kind not in _POD_TEMPLATE_KINDS:
        return None
    template = spec.get("template")
    return template if isinstance(template, dict) else None


def _label_maps(resource: dict[str, Any]) -> list[dict[str, Any]]:
    """Every label map of ``resource`` a pod or its controller carries."""
    maps = [(resource.get("metadata") or {}).get("labels") or {}]
    if resource.get("kind") == "CronJob":
        job = (resource.get("spec") or {}).get("jobTemplate") or {}
        maps.append((job.get("metadata") or {}).get("labels") or {})
    template = _pod_template(resource)
    if template is not None:
        maps.append((template.get("metadata") or {}).get("labels") or {})
    return maps


def _refuse_supplied_labels(resources: list[dict[str, Any]]) -> None:
    for resource in resources:
        for labels in _label_maps(resource):
            supplied = sorted(str(key) for key in labels if _reserved(str(key)))
            if supplied:
                name = (resource.get("metadata") or {}).get("name") or "?"
                raise ObservabilityLabelError(
                    f"{resource.get('kind')} {name!r} sets the label {supplied[0]!r}. It decides which "
                    "Cloud Operations bundle a pod's logs land in, so only Astrolift sets it, from the "
                    "bundles bound to the workload; remove it from the manifest",
                )


def _log_writing_bundles(app_environment: Any) -> list[tuple[tuple[str, ...], str, str]]:
    """``(workload selectors, label value, service name)`` per bound bundle that collects logs."""
    from gcp.managed.observability_cloud_operations import (
        BUNDLE_POD_LABEL,
        KIND,
        VARIANT,
        CloudOperationsError,
        bundle_pod_labels,
    )

    from astrolift_workflows.activities.app_lifecycle import (
        _binding_workloads_for_service,
        _managed_services_for_environment,
    )

    services = (
        _managed_services_for_environment(app_environment)
        .filter(kind=KIND, variant=VARIANT)
        .exclude(backend_ref="")
        .order_by("pk")
    )
    bundles: list[tuple[tuple[str, ...], str, str]] = []
    for service in services:
        name = service.name or service.kind
        try:
            labels = bundle_pod_labels(service.backend_ref, service.config)
        except CloudOperationsError as exc:
            raise ObservabilityLabelError(f"Cloud Operations bundle {name!r}: {exc}") from None
        if labels:
            selectors = _binding_workloads_for_service(service, app_environment)
            bundles.append((selectors, labels[BUNDLE_POD_LABEL], name))
    return bundles


def stamp_observability_pod_labels(resources: list[dict[str, Any]], app_environment: Any) -> None:
    """Label the pods of every workload a log-writing Cloud Operations bundle is bound to.

    In place, on the pod templates only; the label map is replaced rather than
    updated, because the renderer shares one map between a workload's
    controller, its pods and its Service. A pod can land its logs in one
    bucket, so a workload bound to two bundles that both collect its logs is
    refused rather than given one of them silently.
    """
    _refuse_supplied_labels(resources)
    bundles = _log_writing_bundles(app_environment)
    if not bundles:
        return
    from gcp.managed.observability_cloud_operations import BUNDLE_POD_LABEL

    for resource in resources:
        template = _pod_template(resource)
        workload = ((resource.get("metadata") or {}).get("labels") or {}).get("astrolift.dev/workload")
        if template is None or not workload:
            continue
        bound = {
            value: name for selectors, value, name in bundles if "*" in selectors or workload in selectors
        }
        if not bound:
            continue
        if len(bound) > 1:
            raise ObservabilityLabelError(
                f"workload {workload!r} is bound to {len(bound)} Cloud Operations bundles that collect its "
                f"logs ({', '.join(sorted(bound.values()))}), and a pod's logs land in one bucket. Set "
                "access_mode to read on all but one of them, or bind them to different workloads",
            )
        metadata = template.setdefault("metadata", {})
        metadata["labels"] = {**(metadata.get("labels") or {}), BUNDLE_POD_LABEL: next(iter(bound))}
