"""Which environment a preview is a preview of (#1578 feature 2).

The gap this closes: a preview `AppEnvironment` had no link to a primary,
so there was no declared source for what a preview should inherit. Feature
2 of three on #1578, and the one that needs no decision -- it is the
relationship itself, not what flows across it.

Deliberately *only* the relationship. Nothing here reads or copies managed
services: that is feature 3's slice question (whether a preview carves a
slice inside the main instance or always gets a dedicated one), which is
still open. Landing the link now means feature 3 has something to resolve
against whichever way that goes, rather than the two being blocked on each
other.
"""

from __future__ import annotations

from typing import Any

# Preview environments are named `preview-pr-<n>` (`env_slug_for_preview`)
# or `preview-<branch>` for manual ones. Matched by prefix rather than by
# joining to PreviewEnvironment so the resolver stays usable from paths that
# hold only an AppEnvironment.
PREVIEW_ENV_PREFIX = "preview-"

# Names checked, in order, when picking a primary. First match wins.
PRIMARY_NAME_PREFERENCE = ("production", "prod", "main", "staging")


def is_preview_environment(env: Any) -> bool:
    return (getattr(env, "name", "") or "").startswith(PREVIEW_ENV_PREFIX)


def resolve_previewed_environment(app: Any, cluster: Any) -> Any | None:
    """The environment a new preview on ``cluster`` should point at.

    The rule, in order:

    1. non-preview environments of ``app`` on the same ``tenant_cluster``
    2. preferring `production`, then `prod`, `main`, `staging` by name
    3. otherwise the oldest remaining one

    Same cluster is a hard filter rather than a preference. An environment
    on another cluster has its managed services in another cluster's
    namespaces, so inheriting from it would name resources the preview
    cannot reach -- a link that looks right and resolves to nothing.

    Oldest as the tiebreak because it is stable: newest would move the
    lineage of every existing preview the moment someone adds an
    environment, and a preview silently changing what it is a preview of is
    worse than an arbitrary-but-fixed answer.

    Returns None when the app has no non-preview environment on the
    cluster. That is a real state -- the auto-PR path can create a preview
    before any normal environment exists -- and it is left null rather than
    guessed at, because a wrong lineage is worse than an absent one.
    """
    from astrolift_lifecycle.models import AppEnvironment

    candidates = list(
        AppEnvironment.objects.filter(
            registered_app=app,
            tenant_cluster=cluster,
            deleted_at__isnull=True,
        )
        .exclude(name__startswith=PREVIEW_ENV_PREFIX)
        .order_by("created_at", "pk")
    )
    if not candidates:
        return None

    by_name = {(env.name or "").lower(): env for env in candidates}
    for preferred in PRIMARY_NAME_PREFERENCE:
        if preferred in by_name:
            return by_name[preferred]
    return candidates[0]
