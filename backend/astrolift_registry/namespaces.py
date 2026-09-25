"""Guard against two apps landing in one Kubernetes namespace (#1912).

An app's namespace is ``dns_label(org_slug, app_slug)``, hyphen-joined, so
org ``acme`` app ``x-y`` and org ``acme-x`` app ``y`` both map to
``acme-x-y``; on a shared cluster the second org's workloads, Secrets and
NetworkPolicies would land in the first org's namespace. The same join can
also reach a platform namespace (org ``astrolift`` app ``agents-x`` is
``astrolift-agents-x``, org ``x``'s agent namespace). Existing apps keep the
namespace they have; a new app whose namespace is taken or reserved is
refused.
"""

from __future__ import annotations

_RESERVED_EXACT = frozenset({"default", "kube-system", "kube-public", "kube-node-lease", "astrolift-system"})
_RESERVED_PREFIXES = ("kube-", "astrolift-agents-", "astrolift-system-")


def namespace_refusal(namespace: str, *, organization_id: int) -> str | None:
    """Why a new app of ``organization_id`` may not use ``namespace``, or ``None``.

    Taken means a live app anywhere uses it, or a deleted app of another org
    does (its namespace may still exist on the cluster until teardown). A
    deleted app of the same org frees it, so re-registering works.
    """
    from django.db.models import Q

    from astrolift_registry.models import RegisteredApp

    if namespace in _RESERVED_EXACT or namespace.startswith(_RESERVED_PREFIXES):
        return f"namespace {namespace!r} is reserved for the platform; choose another app slug"
    taken = (
        RegisteredApp.all_objects.filter(k8s_namespace=namespace)
        .filter(Q(deleted_at__isnull=True) | ~Q(organization_id=organization_id))
        .exists()
    )
    if taken:
        return f"namespace {namespace!r} is already used by another app; choose another app slug"
    return None
