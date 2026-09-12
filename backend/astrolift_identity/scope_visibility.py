"""
Row filters that match the RBAC gate a collection resolver ran (#1717).

``require_permission(..., any_scope=True)`` lets a caller through on a
binding held anywhere in the active org. That is the only gate a list
can run -- there is no single target to check -- but on its own it would
hand a team-scoped user every row in the org. These helpers close that
half: they narrow a queryset to the scopes the caller's bindings
actually cover, so the gate and the result agree.

Coverage follows the same downward inheritance the resolver walks:
ORG covers everything, TEAM covers that team's projects and apps,
PROJECT covers that project's apps. It never runs upward -- holding
``team.read`` on one project does not reveal its parent team.
"""

from __future__ import annotations

from core.permissions import Permission, granted_scopes
from core.tenancy import get_current_tenant


def _scopes(permission: Permission):
    return granted_scopes(get_current_tenant(), permission)


def visible_teams(qs, permission: Permission):
    scopes = _scopes(permission)
    if scopes.org:
        return qs
    return qs.filter(pk__in=scopes.team_ids)


def visible_projects(qs, permission: Permission):
    from django.db.models import Q

    scopes = _scopes(permission)
    if scopes.org:
        return qs
    return qs.filter(Q(pk__in=scopes.project_ids) | Q(team_id__in=scopes.team_ids))


def visible_apps(qs, permission: Permission):
    from django.db.models import Q

    scopes = _scopes(permission)
    if scopes.org:
        return qs
    return qs.filter(
        Q(pk__in=scopes.app_ids) | Q(project_id__in=scopes.project_ids) | Q(team_id__in=scopes.team_ids)
    )
