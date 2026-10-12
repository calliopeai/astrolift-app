"""Action-safety gates: confirmation and fresh verification for sensitive actions (#2202).

Decisions (Leo, 2026-10-11):

* Every gate is **on by default**: the action asks for confirmation and a fresh
  step-up verification, whatever the install-wide ``REQUIRE_STEP_UP_AUTH``
  switch says. An org admin may approve their own gated action by default.
* The organization can turn a gate off, or self-approval off. Team, app and
  user rules can only add: the **strictest** requirement from any applicable
  scope wins, so a narrower scope never weakens an inherited one.
* First cut: destructive deletes and secret reveal and change. Exec/console
  attach runs over a WebSocket and joins with the approval routing in the
  next slice; ``allow_self_approval`` is resolved now so it is ready for it.

Enforcement is server-side: ``gate`` returns the same ``STEP_UP_REQUIRED``
envelope ``@requires_elevation`` does, which the web console already turns
into a re-authentication prompt with a retry. An API-token call passes, as it
does for step-up: issuing the token was the authentication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The gated actions and how the prompt names them.
ACTIONS: dict[str, str] = {
    "app.deregister": "Deregister an app",
    "cluster.decommission": "Decommission a cluster",
    "managed_service.deprovision": "Deprovision a managed service",
    "secret.reveal": "Reveal a secret",
    "secret.change": "Change a secret",
}


@dataclass(frozen=True)
class Requirements:
    action: str
    enabled: bool = True
    confirmation: bool = True
    verification: bool = True
    allow_self_approval: bool = True
    #: Which rows shaped the answer, as "ORG", "TEAM:<id>", "APP:<id>", "USER:<id>".
    sources: tuple[str, ...] = field(default_factory=tuple)


def _rows(organization_id: int, action: str, scopes: list[tuple[str, int | None]]):
    from django.db.models import Q

    from astrolift_identity.models import ActionSafetyRule

    match = Q()
    for kind, scope_id in scopes:
        match |= Q(scope_kind=kind, scope_id=scope_id)
    return list(ActionSafetyRule.objects.filter(match, organization_id=organization_id, action=action))


def effective(
    organization_id: int,
    action: str,
    *,
    team_id: int | None = None,
    app_id: int | None = None,
    user_id: int | None = None,
) -> Requirements:
    """What ``action`` asks of ``user_id`` on ``app_id`` in this organization."""
    if action not in ACTIONS:
        raise ValueError(f"unknown gated action {action!r}")
    scopes: list[tuple[str, int | None]] = [("ORG", None)]
    scopes += [("TEAM", team_id)] if team_id else []
    scopes += [("APP", app_id)] if app_id else []
    scopes += [("USER", user_id)] if user_id else []
    from django.conf import settings

    rows = _rows(organization_id, action, scopes)
    # The built-in default: on (Leo, 2026-10-11). The setting exists so the
    # wider test suite can drive these mutations without stepping up.
    default_on = bool(getattr(settings, "ASTROLIFT_ACTION_SAFETY_DEFAULT_ON", True))
    enabled, self_approval, sources = default_on, True, []
    org = next((r for r in rows if r.scope_kind == "ORG"), None)
    if org is not None:
        sources.append("ORG")
        if org.enabled is not None:
            enabled = org.enabled
        if org.allow_self_approval is False:
            self_approval = False
    for row in rows:
        if row.scope_kind == "ORG":
            continue
        sources.append(f"{row.scope_kind}:{row.scope_id}")
        # A narrower scope can turn a gate on, never off.
        if row.enabled:
            enabled = True
        if row.allow_self_approval is False:
            self_approval = False
    return Requirements(
        action=action,
        enabled=enabled,
        confirmation=enabled,
        verification=enabled,
        allow_self_approval=self_approval,
        sources=tuple(sources),
    )


def gate(info: Any, action: str, *, app: Any = None, resolver_name: str = "") -> Any | None:
    """``None`` when the caller may go ahead, or the ``STEP_UP_REQUIRED`` envelope."""
    from astrolift_identity.step_up import check_elevation
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return None
    requirements = effective(
        tenant.organization_id,
        action,
        team_id=getattr(app, "team_id", None),
        app_id=getattr(app, "pk", None),
        user_id=tenant.actor_user_id,
    )
    if not requirements.verification:
        return None
    return check_elevation(
        info,
        action_label=ACTIONS[action],
        resolver_name=resolver_name or action,
        required=True,
    )
