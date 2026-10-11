"""Hosted brain discovery: which brains can this caller read (#2320).

A brain is an app of a brain kind (``[app] kind = "mimwell"``), served by its
own workloads. Discovery is the app visibility question filtered to those
kinds, so the same tenancy, team shares (``AppTeamAccess`` viewer and up) and
API token ceiling apply to the UI, GraphQL and MCP alike. Nothing here grants
access; a brain is shared by sharing its app.
"""

from __future__ import annotations

from typing import Any

from astrolift_manifest.app_kinds import BRAIN_KINDS
from astrolift_registry.models import AppTeamAccess, RegisteredApp
from astrolift_registry.visibility import visible_registry_apps
from core.permissions import Permission


def visible_brains(*, search: str = ""):
    """The caller's readable brain apps, newest first."""
    qs = RegisteredApp.objects.filter(app_kind__in=BRAIN_KINDS, archived_at__isnull=True)
    term = search.strip()
    if term:
        from django.db.models import Q

        qs = qs.filter(Q(name__icontains=term) | Q(slug__icontains=term))
    return (
        visible_registry_apps(qs, Permission.APP_READ)
        .select_related("team", "project")
        .order_by("-created_at", "pk")
    )


def describe_brain(app: RegisteredApp) -> dict[str, Any]:
    """What a client needs to list and connect to one brain; no credentials."""
    from astrolift_lifecycle.models import AppEnvironment

    environments = [
        {"name": env.name, "url": env.url}
        for env in AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True)
        .exclude(url="")
        .order_by("name")
    ]
    shared = [
        {"team": row.team.slug, "access": row.access_level}
        for row in AppTeamAccess.objects.filter(
            registered_app=app, deleted_at__isnull=True, team__deleted_at__isnull=True
        )
        .select_related("team")
        .order_by("team__slug")
    ]
    return {
        "id": str(app.guid),
        "slug": app.slug,
        "name": app.name,
        "kind": app.app_kind,
        "team": app.team.slug if app.team_id else "",
        "project": app.project.slug if app.project_id else "",
        "environments": environments,
        "shared_with": shared,
    }
