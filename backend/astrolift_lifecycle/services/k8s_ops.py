"""Direct Kubernetes operations on an app's workload.

First-line incident-response actions (#388): rolling-restart and
replica-scale. Both speak directly to the workload's bound cluster via
the ``ClusterDriver`` resolved by
:func:`core.cluster_management._driver_for_cluster` — the same shape
the secret-rotation bounce (#365) and observability paths use.

Why these don't go through Temporal:
the operator wants an immediate, single-shot patch. The Deployment's
own rollout controller drives the rest. Wrapping a 1-RTT patch in a
workflow just adds latency and a row in the runs table. Failures
surface synchronously through the ``MutationResult`` envelope.

Replica bounds policy
=====================

V1 policy: ``0 <= replicas <= min(20, env.max_replicas_or_default)``.

The platform default upper bound is :data:`DEFAULT_MAX_REPLICAS` (20).
An environment can carry a tighter ceiling in
``AppEnvironment.deploy_config["max_replicas"]`` (per-env override —
the same JSON field used for other env-scoped policy). Out-of-band
scaling (cluster HPA, an operator using kubectl directly) is not
clamped by this code; it's only the platform-mediated path.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import Workload

log = logging.getLogger(__name__)

DEFAULT_MAX_REPLICAS = 20
"""Hard platform ceiling for V1. Tightened per-env via
``AppEnvironment.deploy_config['max_replicas']``."""


class K8sOpError(Exception):
    """Raised for any structured failure the mutation layer should
    translate to ``MutationResult.errors``. Carries an
    :class:`ErrorCode`-shaped string the resolver maps verbatim."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclasses.dataclass(slots=True)
class RestartResult:
    ok: bool
    new_revision: int | None
    error: str | None = None


@dataclasses.dataclass(slots=True)
class ScaleResult:
    ok: bool
    current_replicas: int | None
    ready_replicas: int | None
    error: str | None = None


@dataclasses.dataclass(slots=True)
class WorkloadStatusResult:
    """Live Deployment status for a Service-family agent. ``deployed`` is
    False (with null replica counts) when the Deployment doesn't exist yet,
    so the caller can say "deploy the agent first" instead of erroring."""

    ok: bool
    deployed: bool
    desired_replicas: int | None = None
    ready_replicas: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def resolve_replica_bounds(env: AppEnvironment | None) -> tuple[int, int]:
    """Return ``(lower, upper)`` inclusive replica bounds for ``env``.

    Lower is always 0 (scale-to-zero is supported). Upper is the
    smaller of :data:`DEFAULT_MAX_REPLICAS` and the env-scoped
    ``max_replicas`` override (when set + positive).
    """
    upper = DEFAULT_MAX_REPLICAS
    if env is not None:
        raw = (env.deploy_config or {}).get("max_replicas")
        if isinstance(raw, int) and raw > 0:
            upper = min(upper, raw)
    return 0, upper


def _primary_environment_for_workload(workload: Workload) -> AppEnvironment | None:
    """Pick the env to drive the patch against.

    A workload belongs to a ``RegisteredApp`` which has 1..N
    environments. V1 targets the app's first active env — the same
    "primary cluster" the renderer would target for a single-workload
    app. Multi-env apps will need an explicit environment_id input on
    the mutation; for V1 the spec only carries ``workload_id``.

    Returns None when the app has no active environments (the caller
    raises a structured NOT_FOUND / PRECONDITION).
    """
    from astrolift_lifecycle.models import AppEnvironment

    return (
        AppEnvironment.objects.filter(
            registered_app=workload.registered_app,
            deleted_at__isnull=True,
        )
        .select_related("tenant_cluster", "registered_app")
        .order_by("id")
        .first()
    )


def _resolve_driver_and_namespace(workload: Workload) -> tuple[Any, str, str]:
    """Return ``(driver, namespace, cluster_slug)`` for ``workload``.

    Mirrors the cluster-management resolution path so an injected
    test backend (``set_management_backend_for_tests``) routes through
    the same fallback. Raises :class:`K8sOpError` when the workload
    can't be bound to a deploy target.
    """
    from core.app_deploy import namespace_for_app
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    env = _primary_environment_for_workload(workload)
    if env is None or env.tenant_cluster_id is None:
        raise K8sOpError(
            "PRECONDITION",
            f"workload {workload.slug!r} has no active environment bound to a cluster",
        )
    cluster = env.tenant_cluster
    namespace = namespace_for_app(workload.registered_app)
    driver = _driver_for_cluster(cluster)
    ctx = _context_for_cluster(cluster)
    return driver, namespace, ctx.slug


def _patch_workload_or_raise(
    driver: Any,
    *,
    cluster_slug: str,
    namespace: str,
    name: str,
    patch: dict,
) -> dict[str, Any]:
    """Call ``driver.patch_workload`` with structured-error mapping.

    Returns the driver's response payload (dict). Raises
    :class:`K8sOpError` on missing-method, not-found, or any other
    driver-side exception."""
    patch_workload = getattr(driver, "patch_workload", None)
    if not callable(patch_workload):
        raise K8sOpError(
            "PRECONDITION",
            f"cluster {cluster_slug!r} driver has no patch_workload; cannot apply live op",
        )
    try:
        return patch_workload(cluster_slug, namespace, "Deployment", name, patch) or {}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or exc.__class__.__name__
        # Heuristic — drivers commonly raise a NotFound-shaped error
        # when the Deployment isn't there yet; surface as NOT_FOUND so
        # the UI can hint "deploy first".
        lower = msg.lower()
        if "not found" in lower or "404" in lower or "notfound" in lower:
            raise K8sOpError(
                "NOT_FOUND",
                f"Deployment/{name} not found in {namespace} on cluster {cluster_slug!r}",
            ) from exc
        raise K8sOpError("INTERNAL", f"patch_workload failed: {msg}") from exc


# ---------------------------------------------------------------------------
# Rolling restart
# ---------------------------------------------------------------------------


def rollout_restart_workload(workload: Workload) -> RestartResult:
    """Trigger a rolling restart on ``workload``'s Deployment.

    Same shape as ``kubectl rollout restart``: patch
    ``spec.template.metadata.annotations.kubectl.kubernetes.io/restartedAt``
    with the current UTC ISO timestamp. Kubernetes' deployment
    controller picks up the template hash change and rolls a fresh
    ReplicaSet — preserving the image, the strategy, and the rollout
    history.

    Returns a :class:`RestartResult`. New revision (when the driver
    surfaces it) is the post-patch ``status.observedGeneration``;
    drivers that don't return that information leave it None and the
    UI just falls back to "restart issued".
    """
    driver, namespace, cluster_slug = _resolve_driver_and_namespace(workload)
    now_iso = datetime.now(UTC).isoformat()
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": now_iso,
                        "astrolift.io/restarted-by": "platform-controls",
                    },
                },
            },
        },
    }
    response = _patch_workload_or_raise(
        driver,
        cluster_slug=cluster_slug,
        namespace=namespace,
        name=workload.slug,
        patch=patch,
    )
    new_revision = _extract_int(response, ("status", "observedGeneration"))
    log.info(
        "rollout_restart_workload cluster=%s ns=%s name=%s revision=%s",
        cluster_slug,
        namespace,
        workload.slug,
        new_revision,
    )
    return RestartResult(ok=True, new_revision=new_revision, error=None)


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------


def scale_workload(
    workload: Workload,
    replicas: int,
    *,
    policy: dict[str, Any] | None = None,
) -> ScaleResult:
    """Patch ``workload``'s Deployment ``spec.replicas`` to ``replicas``.

    Bounds are clamped by :func:`resolve_replica_bounds` — out-of-range
    values raise :class:`K8sOpError` with ``code='VALIDATION'`` and the
    bounds embedded in the message. ``policy`` is reserved for future
    overrides (passing ``{"max_replicas": N}`` tightens the cap beyond
    the env-derived value); empty dict / None means "use env policy".

    Returns the driver's read-back ``current_replicas`` and
    ``ready_replicas`` when surfaced; otherwise echoes the desired
    count for ``current_replicas`` and leaves ``ready_replicas`` None.
    """
    env = _primary_environment_for_workload(workload)
    lower, upper = resolve_replica_bounds(env)
    if policy:
        override = policy.get("max_replicas")
        if isinstance(override, int) and override >= 0:
            upper = min(upper, override)
    if not lower <= replicas <= upper:
        raise K8sOpError(
            "VALIDATION",
            f"replicas {replicas} outside bounds [{lower}, {upper}]",
        )
    driver, namespace, cluster_slug = _resolve_driver_and_namespace(workload)
    patch = {"spec": {"replicas": int(replicas)}}
    response = _patch_workload_or_raise(
        driver,
        cluster_slug=cluster_slug,
        namespace=namespace,
        name=workload.slug,
        patch=patch,
    )
    current = _extract_int(response, ("spec", "replicas"))
    if current is None:
        current = int(replicas)
    ready = _extract_int(response, ("status", "readyReplicas"))
    log.info(
        "scale_workload cluster=%s ns=%s name=%s desired=%d current=%s ready=%s",
        cluster_slug,
        namespace,
        workload.slug,
        replicas,
        current,
        ready,
    )
    return ScaleResult(ok=True, current_replicas=current, ready_replicas=ready, error=None)


def read_workload_status(workload: Workload) -> WorkloadStatusResult:
    """Read the live Deployment status (desired/ready replicas) for a
    Service-family agent ``workload``.

    The Deployment IS the run for a Service agent (#1012), so its live
    replica counts are the run's status. Returns ``deployed=False`` when the
    Deployment isn't there yet (NOT_FOUND from the driver), letting the
    observe surface distinguish "not deployed" from "deployed, 0 ready".
    """
    driver, namespace, cluster_slug = _resolve_driver_and_namespace(workload)
    get_status = getattr(driver, "get_workload_status", None)
    if not callable(get_status):
        raise K8sOpError(
            "PRECONDITION",
            f"cluster {cluster_slug!r} driver has no get_workload_status; cannot read status",
        )
    try:
        status = get_status(cluster_slug, namespace, "Deployment", workload.slug)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or exc.__class__.__name__
        lower = msg.lower()
        if "not found" in lower or "404" in lower or "notfound" in lower:
            return WorkloadStatusResult(ok=True, deployed=False)
        raise K8sOpError("INTERNAL", f"get_workload_status failed: {msg}") from exc
    desired = int(getattr(status, "desired_replicas", 0) or 0)
    ready = int(getattr(status, "ready_replicas", 0) or 0)
    return WorkloadStatusResult(
        ok=True,
        deployed=True,
        desired_replicas=desired,
        ready_replicas=ready,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _extract_int(payload: Any, path: tuple[str, ...]) -> int | None:
    """Walk ``payload`` (a dict/object graph) along ``path`` and
    return the leaf as an int when possible. Returns None on any
    miss, type mismatch, or non-coercible value — the read-back is
    best-effort and drivers vary in shape."""
    cursor: Any = payload
    for key in path:
        if isinstance(cursor, dict):
            cursor = cursor.get(key)
        else:
            cursor = getattr(cursor, key, None)
        if cursor is None:
            return None
    try:
        return int(cursor)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_MAX_REPLICAS",
    "K8sOpError",
    "RestartResult",
    "ScaleResult",
    "resolve_replica_bounds",
    "rollout_restart_workload",
    "scale_workload",
]
