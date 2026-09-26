"""Re-apply the edge auth annotations on managed-subdomain Ingresses.

Bridges "operator changed the auth gate on a cluster" to "every running
managed-subdomain Ingress on that cluster gets (or loses) the
annotations immediately" (#851). Covers both gates: the
``alb.ingress.kubernetes.io/auth-*`` keys driven by ``alb_auth_config``
on ALB clusters, and the ``nginx.ingress.kubernetes.io/auth-*`` keys
driven by ``oidc_auth_config`` on every other class (#1539).

Without this, a config change only takes effect on the next per-app
deploy — the renderer reads ``alb_auth_config`` when it renders the
Ingress (see ``core/app_deploy._render_managed_subdomain_ingress``), so a
toggle would otherwise sit dormant until each app is redeployed one by
one. The reconcile patches the live Ingresses in place so the AWS Load
Balancer Controller picks up the new annotations on its next sync.

The annotation keys produced here are kept in lock-step with
``ALBIngressDriver._render_annotations()`` — when auth is enabled they
match what a fresh render would emit; when auth is disabled the same
keys are set to ``None`` so the strategic-merge patch deletes them.

Runs in the web (Django) process: it resolves the cluster driver the
same way ``core.cluster_management.cluster_health_dispatch`` does
(``_driver_for_cluster`` → the driver's cached kubernetes client) rather
than going through Temporal. The work is a handful of in-place patches
the operator wants applied synchronously, and the result count flows
straight back into the mutation envelope.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrolift_clusters.models import TenantCluster

logger = logging.getLogger(__name__)

# The six annotation keys the ALB driver emits for Cognito auth. The
# reconcile owns exactly these keys: when auth is enabled they carry the
# rendered values, when auth is disabled they're patched to ``None`` so
# the apiserver drops them. Keep aligned with
# ``ALBIngressDriver._render_annotations()`` (#30 / #851).
AUTH_ANNOTATION_KEYS = (
    "alb.ingress.kubernetes.io/auth-type",
    "alb.ingress.kubernetes.io/auth-idp-cognito",
    "alb.ingress.kubernetes.io/auth-on-unauthenticated-request",
    "alb.ingress.kubernetes.io/auth-scope",
    "alb.ingress.kubernetes.io/auth-session-cookie",
    "alb.ingress.kubernetes.io/auth-session-timeout",
)

MANAGED_SUBDOMAIN_SELECTOR = "astrolift.dev/managed-subdomain=true"


def _auth_annotation_patch(alb_auth_config: dict[str, Any] | None) -> dict[str, str | None]:
    """Return the annotation delta to merge onto an Ingress.

    When ``alb_auth_config`` carries the three Cognito fields, the six
    auth keys are set to the same values ``ALBIngressDriver`` would
    render. Otherwise (auth disabled, or a partial/garbage config) every
    auth key is set to ``None`` so the strategic-merge patch removes it —
    flipping the gate off leaves the Ingress public, which is the
    intended "disabled" outcome.
    """
    cfg = alb_auth_config or {}
    required = ("user_pool_arn", "user_pool_client_id", "user_pool_domain")
    if not all(cfg.get(k) for k in required):
        return dict.fromkeys(AUTH_ANNOTATION_KEYS, None)
    return {
        "alb.ingress.kubernetes.io/auth-type": "cognito",
        "alb.ingress.kubernetes.io/auth-idp-cognito": json.dumps(
            {
                "UserPoolArn": cfg["user_pool_arn"],
                "UserPoolClientId": cfg["user_pool_client_id"],
                "UserPoolDomain": cfg["user_pool_domain"],
            }
        ),
        "alb.ingress.kubernetes.io/auth-on-unauthenticated-request": "authenticate",
        "alb.ingress.kubernetes.io/auth-scope": "openid email profile",
        "alb.ingress.kubernetes.io/auth-session-cookie": "AWSELBAuthSessionCookie",
        "alb.ingress.kubernetes.io/auth-session-timeout": "86400",
    }


def _oidc_annotation_patch(
    cluster: TenantCluster,
    existing_snippet: str = "",
    edge: dict | None = None,
) -> dict[str, str | None]:
    """The nginx-family half of :func:`auth_annotation_patch`.

    Mirrors the ALB behaviour on the central-auth path: a complete
    ``oidc_auth_config`` patches on the same annotations the
    renderer emits, anything less patches them to ``None`` so the
    apiserver drops them. Delegates the "is this configured" test to
    ``core.app_deploy.oidc_auth_for_cluster`` and the values to
    ``nginx_auth_annotations`` so a reconciled Ingress and a
    freshly-rendered one cannot disagree.
    """
    from core.app_deploy import oidc_auth_for_cluster
    from providers.k8s_native.ingress import (
        NGINX_AUTH_ANNOTATION_KEYS,
        compose_configuration_snippet,
        nginx_auth_annotations,
    )

    auth = oidc_auth_for_cluster(cluster)
    if auth is None:
        # Auth off: drop the keys the reconcile owns. The snippet is the
        # exception -- it is a shared annotation, so only the platform's
        # own fenced block comes out and anything the app contributes
        # stays (#1726).
        patch: dict[str, str | None] = dict.fromkeys(NGINX_AUTH_ANNOTATION_KEYS, None)
        patch[_SNIPPET_KEY] = compose_configuration_snippet(existing_snippet, "")
        return patch
    # Every key the reconcile owns has to appear in the patch, including the
    # ones this cluster does not currently populate. nginx_auth_annotations
    # omits the gateway-secret snippet when no secret is configured (#1726);
    # omitting it here too would leave a previously-stamped snippet on the live
    # Ingress after the secret was removed, so the gate would keep sending a
    # secret the platform no longer knows about.
    rendered = nginx_auth_annotations(auth, existing_snippet=existing_snippet, edge=edge)
    return {key: rendered.get(key) for key in NGINX_AUTH_ANNOTATION_KEYS}


def auth_annotation_patch(
    cluster: TenantCluster,
    existing_snippet: str = "",
    edge: dict | None = None,
) -> dict[str, str | None]:
    """Annotation delta for ``cluster``, whichever gate its class uses.

    The two ingress classes carry different annotation keys and read
    different config, so the reconcile has to pick per cluster. It used
    to assume ALB and the mutation refused every other class outright,
    which left the central-auth path with no way to push a change onto
    running Ingresses at all -- the reason Stage B of #1539 had to
    hand-annotate live Ingresses.

    Only the keys for the cluster's own class are touched. Keys
    belonging to the other class are left alone rather than cleared: a
    cluster serves one controller, and a reconcile has no business
    editing annotations no renderer on this cluster emits.
    """
    if getattr(cluster, "ingress_class", "") == "alb":
        return _auth_annotation_patch(cluster.alb_auth_config)
    return _oidc_annotation_patch(cluster, existing_snippet, edge)


def _ingress_name(ingress: Any) -> str:
    """Pull ``.metadata.name`` off a kubernetes-client V1Ingress object
    (attribute access) or a plain dict (key access) — the live client
    returns the former, test fakes return the latter."""
    meta = getattr(ingress, "metadata", None)
    if meta is None and isinstance(ingress, dict):
        meta = ingress.get("metadata")
    name = getattr(meta, "name", None)
    if name is None and isinstance(meta, dict):
        name = meta.get("name")
    return str(name) if name else ""


_SNIPPET_KEY = "nginx.ingress.kubernetes.io/configuration-snippet"


def _edge_config(app: Any) -> dict | None:
    """The app's normalized ``[edge]`` block, or ``None`` (#1733)."""
    manifest = getattr(app, "manifest_normalized", None) or {}
    edge = manifest.get("edge") if isinstance(manifest, dict) else None
    return edge if isinstance(edge, dict) else None


def _existing_snippet(ingress: Any) -> str:
    """Current ``configuration-snippet`` on a live Ingress object.

    Same attribute-or-dict tolerance as :func:`_ingress_name`: the live
    kubernetes client returns objects, test fakes return plain dicts.
    """

    meta = getattr(ingress, "metadata", None)
    if meta is None and isinstance(ingress, dict):
        meta = ingress.get("metadata")
    annotations = getattr(meta, "annotations", None)
    if annotations is None and isinstance(meta, dict):
        annotations = meta.get("annotations")
    if not isinstance(annotations, dict):
        return ""
    return str(annotations.get(_SNIPPET_KEY) or "")


def reconcile_cluster_ingresses(cluster: TenantCluster) -> dict[str, Any]:
    """Patch the auth annotations on every managed-subdomain Ingress.

    Walks each ``AppEnvironment`` bound to ``cluster`` that carries a
    ManagedDomain (those are the envs whose deploys render a
    managed-subdomain Ingress), lists the Ingresses labelled
    ``astrolift.dev/managed-subdomain=true`` in the environment's namespace, and
    strategic-merge-patches the auth annotation keys onto each — adding
    them when ``cluster.alb_auth_config`` is set, removing them when it's
    null.

    Returns ``{"reconciled": N, "skipped": N, "errors": [str, ...]}``:
      * ``reconciled`` — Ingresses successfully patched.
      * ``skipped``    — envs with no managed-subdomain Ingress found in
        their namespace (nothing to patch — not yet deployed, or the
        Ingress was hand-removed).
      * ``errors``     — per-env / per-Ingress failure strings; one bad
        namespace doesn't abort the rest of the sweep.
    """
    from astrolift_lifecycle.models import AppEnvironment
    from core.app_deploy import namespace_for_environment
    from core.cluster_management import _driver_for_cluster

    reconciled = 0
    skipped = 0
    errors: list[str] = []

    try:
        driver = _driver_for_cluster(cluster)
    except Exception as exc:  # noqa: BLE001
        return {
            "reconciled": 0,
            "skipped": 0,
            "errors": [f"could not resolve cluster driver: {exc}"],
        }

    k8s_factory = getattr(driver, "_k8s", None)
    if not callable(k8s_factory):
        return {
            "reconciled": 0,
            "skipped": 0,
            "errors": [
                f"cluster {cluster.slug}: driver has no kubernetes client; cannot reconcile ingresses"
            ],
        }
    try:
        client = k8s_factory(cluster.slug)
    except Exception as exc:  # noqa: BLE001
        return {
            "reconciled": 0,
            "skipped": 0,
            "errors": [f"cluster {cluster.slug}: could not build kubernetes client: {exc}"],
        }

    envs = (
        AppEnvironment.objects.filter(
            tenant_cluster=cluster,
            managed_domain__isnull=False,
            deleted_at__isnull=True,
            registered_app__deleted_at__isnull=True,
        )
        .select_related("registered_app__organization")
        .order_by("id")
    )

    for env in envs:
        namespace = namespace_for_environment(env)
        try:
            listing = client.list_namespaced_ingress(
                namespace,
                label_selector=MANAGED_SUBDOMAIN_SELECTOR,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{namespace}: list ingresses failed: {exc}")
            continue

        items = list(getattr(listing, "items", None) or [])
        if not items:
            skipped += 1
            continue

        for ingress in items:
            name = _ingress_name(ingress)
            if not name:
                errors.append(f"{namespace}: ingress with no metadata.name; skipped")
                continue
            try:
                client.merge_patch_ingress(
                    namespace=namespace,
                    name=name,
                    # Built per Ingress, not once for the sweep: the
                    # snippet annotation is shared with whatever the app
                    # put there, so the patch has to be composed against
                    # that Ingress's current value (#1726).
                    patch={
                        "metadata": {
                            "annotations": auth_annotation_patch(
                                cluster,
                                _existing_snippet(ingress),
                                # The app's declared edge mapping (#1733).
                                # A reconcile that dropped it would take the
                                # app's identity headers away, which is the
                                # same failure #1726 fixed for hand-applied
                                # content -- for declared content the
                                # platform can just render it again.
                                _edge_config(env.registered_app),
                            )
                        }
                    },
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{namespace}/{name}: patch failed: {exc}")
                continue
            reconciled += 1

    logger.info(
        "reconcile_cluster_ingresses cluster=%s class=%s auth=%s reconciled=%d skipped=%d errors=%d",
        cluster.slug,
        cluster.ingress_class,
        any(v is not None for v in auth_annotation_patch(cluster).values()),
        reconciled,
        skipped,
        len(errors),
    )
    return {"reconciled": reconciled, "skipped": skipped, "errors": errors}


__all__ = [
    "AUTH_ANNOTATION_KEYS",
    "MANAGED_SUBDOMAIN_SELECTOR",
    "auth_annotation_patch",
    "reconcile_cluster_ingresses",
]
