"""When a viewer's capabilities may have changed (#1867).

``viewerPermissions`` and ``astroliftMyPermissions`` are answers about one
moment. Clients cache them, so they need to know when to ask again. The
revision is a digest of every stored input the checker reads for this
viewer in the active org:

* roles: the viewer's bindings (their own and their IdP groups'), the roles
  those can name, the org's group mappings and app shares, and how many of
  the bindings are still unexpired;
* attributes: the viewer's IdP groups, superuser and active flags, the
  client IP, the session's sign-in time and factors;
* policies: the org's ABAC policies;
* session: the session or API token making the request.

A create, edit, soft delete or hard delete of any of these rows moves the
count, the latest ``updated_at`` or the version sum, so the revision
changes. Clients compare it with the one their cached capabilities came
with and refetch when it differs. It authorizes nothing: every request is
still checked when it runs, and a time-of-day condition can change an answer
without changing the revision.
"""

from __future__ import annotations

import hashlib
import json

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone


def _summary(qs, **extra) -> list:
    agg = qs.aggregate(rows=Count("pk"), changed=Max("updated_at"), versions=Sum("version"), **extra)
    return [agg[key] for key in sorted(agg)]


def access_revision(tenant, attrs, *, session_key: str | None = None, api_token=None) -> str:
    """Opaque digest for ``tenant``'s actor; empty without one."""
    from django.contrib.auth import get_user_model

    from astrolift_identity.models import GroupRoleMapping, Policy, Role, RoleBinding
    from astrolift_identity.permission_resolver import actor_groups
    from astrolift_registry.models import AppTeamAccess

    if tenant is None or tenant.actor_user_id is None:
        return ""
    org_id = tenant.organization_id
    groups = sorted(actor_groups(tenant))
    principal = Q(user_id=tenant.actor_user_id)
    if groups:
        principal |= Q(user__isnull=True, group_external_id__in=groups)
    live = Q(deleted_at__isnull=True) & (Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    user = (
        get_user_model()
        .objects.filter(pk=tenant.actor_user_id)
        .values_list("is_superuser", "is_active")
        .first()
    )
    authenticated_at = attrs.session_authenticated_at() if attrs is not None else None
    factors = attrs.session_factors() if attrs is not None else None
    inputs = [
        org_id,
        tenant.actor_user_id,
        user,
        groups,
        _summary(RoleBinding.all_objects.filter(principal), live=Count("pk", filter=live)),
        _summary(Role.all_objects.filter(Q(organization_id=org_id) | Q(organization__isnull=True))),
        _summary(GroupRoleMapping.all_objects.filter(organization_id=org_id)),
        _summary(AppTeamAccess.all_objects.filter(registered_app__organization_id=org_id)),
        _summary(Policy.all_objects.filter(organization_id=org_id)),
        attrs.client_ip if attrs is not None else None,
        authenticated_at,
        sorted(factors) if factors is not None else None,
        hashlib.sha256(session_key.encode()).hexdigest() if session_key else None,
        [api_token.pk, api_token.version, api_token.updated_at] if api_token is not None else None,
    ]
    return hashlib.sha256(json.dumps(inputs, default=str).encode()).hexdigest()[:32]
