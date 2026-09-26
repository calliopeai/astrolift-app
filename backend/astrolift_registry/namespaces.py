"""Guard against two apps landing in one Kubernetes namespace (#1912), and
place each environment that needs a namespace of its own (#1922).

An app's namespace is ``dns_label(org_slug, app_slug)``, hyphen-joined, so
org ``acme`` app ``x-y`` and org ``acme-x`` app ``y`` both map to
``acme-x-y``; on a shared cluster the second org's workloads, Secrets and
NetworkPolicies would land in the first org's namespace. The same join can
also reach a platform namespace (org ``astrolift`` app ``agents-x`` is
``astrolift-agents-x``, org ``x``'s agent namespace). Existing apps keep the
namespace they have; a new app whose namespace is taken or reserved is
refused.

Environment namespaces (``AppEnvironment.k8s_namespace``) are built the same
way (``<app namespace>-<env>``, a preview's ``<org>-<app>-pr-<n>``) and so
collide the same way, with apps and with each other. A new one that is taken
gets a short hash suffix instead, since environment creation runs on paths
(repo resync, a PR webhook) with nobody to refuse to; a new app whose
namespace an environment holds is refused like any other taken name.
"""

from __future__ import annotations

import hashlib

_RESERVED_EXACT = frozenset({"default", "kube-system", "kube-public", "kube-node-lease", "astrolift-system"})
_RESERVED_PREFIXES = ("kube-", "astrolift-agents-", "astrolift-system-")


def _reserved(namespace: str) -> bool:
    return namespace in _RESERVED_EXACT or namespace.startswith(_RESERVED_PREFIXES)


def namespace_refusal(namespace: str, *, organization_id: int) -> str | None:
    """Why a new app of ``organization_id`` may not use ``namespace``, or ``None``.

    Taken means a live app anywhere uses it, or a deleted app of another org
    does (its namespace may still exist on the cluster until teardown). A
    deleted app of the same org frees it, so re-registering works. An
    environment's own namespace counts the same way (#1922): a live one
    anywhere, a deleted one of another org, or a live preview's.
    """
    from django.db.models import Q

    from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
    from astrolift_registry.models import RegisteredApp

    if _reserved(namespace):
        return f"namespace {namespace!r} is reserved for the platform; choose another app slug"
    taken = (
        RegisteredApp.all_objects.filter(k8s_namespace=namespace)
        .filter(Q(deleted_at__isnull=True) | ~Q(organization_id=organization_id))
        .exists()
    )
    if taken:
        return f"namespace {namespace!r} is already used by another app; choose another app slug"
    held_by_environment = (
        AppEnvironment.all_objects.filter(k8s_namespace=namespace)
        .filter(Q(deleted_at__isnull=True) | ~Q(registered_app__organization_id=organization_id))
        .exists()
        or PreviewEnvironment.objects.filter(namespace=namespace).exists()
    )
    if held_by_environment:
        return f"namespace {namespace!r} is already used by an environment of another app; choose another app slug"
    return None


def _held_by_an_app(namespace: str) -> bool:
    """An app's namespace is ``namespace``, deleted apps included: a deleted
    app's namespace can outlive it on the cluster until teardown finishes."""
    from _sdk.k8s_naming import app_namespace

    from astrolift_registry.models import RegisteredApp

    if RegisteredApp.all_objects.filter(k8s_namespace=namespace).exists():
        return True
    # Apps registered before registration recorded ``k8s_namespace`` (#1372)
    # compute theirs, so the column alone cannot answer for them.
    for app_slug, org_slug in RegisteredApp.all_objects.filter(k8s_namespace="").values_list(
        "slug", "organization__slug"
    ):
        try:
            if app_namespace(organization_slug=org_slug or "", app_slug=app_slug) == namespace:
                return True
        except ValueError:
            continue
    return False


def environment_namespace_taken(namespace: str, *, exclude_environment_id: int | None = None) -> bool:
    """Whether an environment may not take ``namespace`` (#1922).

    Taken by the platform, by any app, by another environment's recorded
    namespace, or by a preview row's namespace (which ``BuildPreviewWorkflow``
    creates on the cluster), deleted rows included for the same reason as
    ``_held_by_an_app``. ``exclude_environment_id`` is the environment being
    placed, whose own rows do not count against it.
    """
    from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment

    if _reserved(namespace) or _held_by_an_app(namespace):
        return True
    environments = AppEnvironment.all_objects.filter(k8s_namespace=namespace)
    previews = PreviewEnvironment.all_objects.filter(namespace=namespace)
    if exclude_environment_id is not None:
        environments = environments.exclude(pk=exclude_environment_id)
        previews = previews.exclude(app_environment_id=exclude_environment_id)
    return environments.exists() or previews.exists()


def unique_environment_namespace(
    preferred: str,
    *,
    seed: str,
    exclude_environment_id: int | None = None,
) -> str:
    """``preferred`` when it is free, else ``preferred`` plus a short hash of
    ``seed`` that is (#1922). ``seed`` identifies the environment, so the
    same environment asking again gets the same answer."""
    from _sdk.k8s_naming import dns_label

    candidate = preferred
    for attempt in range(100):
        if not environment_namespace_taken(candidate, exclude_environment_id=exclude_environment_id):
            return candidate
        token = hashlib.sha256(f"{seed}:{attempt}".encode()).hexdigest()[:8]
        candidate = dns_label(preferred, token)
    raise RuntimeError(f"no free Kubernetes namespace derived from {preferred!r}")


def namespace_for_new_environment(app, *, name: str, cluster) -> str:
    """The ``k8s_namespace`` a new non-preview environment of ``app`` records (#1922).

    Blank, meaning the app namespace, which is where the first environment
    of an app on a cluster renders exactly as before. When a live
    environment of ``app`` on ``cluster`` already renders there, a namespace
    of its own: ``<app namespace>-<name>``, or that plus a hash when taken.
    """
    from _sdk.k8s_naming import dns_label

    from astrolift_lifecycle.models import AppEnvironment
    from core.app_deploy import namespace_for_app

    if cluster is None:
        return ""
    shares = AppEnvironment.objects.filter(
        registered_app=app, tenant_cluster=cluster, k8s_namespace=""
    ).exists()
    if not shares:
        return ""
    return unique_environment_namespace(
        dns_label(namespace_for_app(app), name),
        seed=f"{app.guid}:{name}",
    )


def namespace_for_new_preview(app, *, name: str, preferred: str) -> str:
    """The namespace a new preview environment of ``app`` records (#1922):
    ``preferred`` (the preview naming convention) unless something holds it.

    Folded to a valid label first, since its deploys now render into it: the
    manual-preview namer can return more than 63 characters for long slugs.
    A name that is already valid is unchanged.
    """
    from _sdk.k8s_naming import dns_label

    return unique_environment_namespace(dns_label(preferred), seed=f"{app.guid}:{name}")


def adopt_preview_namespace(env) -> None:
    """Record a preview environment's own namespace when it has none (#1922).

    Both preview creation paths and migration 0041 record it. A preview that
    a server still on the previous release created during the upgrade has
    none and would go on deploying over the app namespace, so namespace
    provisioning, which every preview build and deploy runs first, records it
    here. Keeps the preview row's namespace in step when it had to move.
    """
    if env is None or (getattr(env, "k8s_namespace", "") or "").strip():
        return
    from _sdk.k8s_naming import dns_label
    from django.db.models import F

    from astrolift_lifecycle.models import PreviewEnvironment

    preview = (
        PreviewEnvironment.all_objects.filter(app_environment=env)
        .order_by(F("deleted_at").asc(nulls_first=True), "-pk")
        .first()
    )
    if preview is None or not (preview.namespace or "").strip():
        return
    namespace = unique_environment_namespace(
        dns_label(preview.namespace.strip()),
        seed=f"{env.registered_app.guid}:{env.name}",
        exclude_environment_id=env.pk,
    )
    if namespace != preview.namespace:
        PreviewEnvironment.all_objects.filter(app_environment=env, namespace=preview.namespace).update(
            namespace=namespace
        )
    env.k8s_namespace = namespace
    env.save(update_fields=["k8s_namespace", "updated_at", "version"])
