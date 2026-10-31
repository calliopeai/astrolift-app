"""Action-safety gates: confirmation and fresh verification for sensitive actions (#2202).

Decisions (Leo, 2026-10-11):

* Every gate is **on by default**: the action asks for confirmation and a fresh
  step-up verification, whatever the install-wide ``REQUIRE_STEP_UP_AUTH``
  switch says. An org admin may approve their own gated action by default.
* The organization can turn a gate off, or self-approval off. Team, app and
  user rules can only add: the **strictest** requirement from any applicable
  scope wins, so a narrower scope never weakens an inherited one.
* First cut: destructive deletes, exec/console attach, and secret reveal and
  change. Second cut (Leo, 2026-10-12): deploying to and rolling back a
  production environment (named ``production`` or ``prod``). Approval for
  deploys stays with the environment's own required approvals. Making an
  app reachable without the edge login (``app.edge.make_public``, #2504);
  making it private again is never gated.
* Approval is off by default; any scope can add it to a destructive delete
  (``APPROVABLE``), and the largest quorum asked for wins. The mutation then
  records an ``ActionApprovalRequest`` and refuses with ``APPROVAL_REQUIRED``;
  once approvers have decided, the requester runs it again and the approved
  request is consumed exactly once. Approval binds to the action, target and
  requester, and expires. API tokens do not skip it: approval is a second
  person, not a fresher sign-in. Secret changes already have their own quorum
  flow (the app's secret-approval setting), so rules do not add a second one.

Exec attach is a WebSocket, which has no envelope to carry a refusal: the
relay closes with 4428 and the console's terminal opens the same step-up
prompt, then reconnects (``core.schema.exec_ws``).

Enforcement is server-side: ``gate`` returns the same ``STEP_UP_REQUIRED``
envelope ``@requires_elevation`` does, which the web console already turns
into a re-authentication prompt with a retry. An API-token call passes, as it
does for step-up: issuing the token was the authentication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

#: The built-in default: on (Leo, 2026-10-11). A module attribute rather than a
#: setting so the wider test suite can drive these mutations without stepping
#: up (see the root conftest) without going through Django's settings wrapper.
DEFAULT_ON = True

#: The gated actions and how the prompt names them.
ACTIONS: dict[str, str] = {
    "app.deregister": "Deregister an app",
    "cluster.decommission": "Decommission a cluster",
    "managed_service.deprovision": "Deprovision a managed service",
    "exec.attach": "Open a console in a workload",
    "secret.reveal": "Reveal a secret",
    "secret.change": "Change a secret",
    "deploy.production": "Deploy to production",
    "rollback.production": "Roll back production",
    "brain.share": "Share a semantic layer",
    "brain.delete": "Delete a semantic layer",
    "app.expiry.clear": "Make a temporary app permanent",
    "app.edge.make_public": "Make an app reachable without the edge login",
}

#: The actions an agent on the MCP surface is refused unless the organization
#: allows agents (#2424). Agents cannot step up, so the person who would have
#: been asked is the organization's rule.
AGENT_GATED: frozenset[str] = frozenset(
    {"brain.share", "brain.delete", "app.expiry.clear", "app.edge.make_public"}
)

#: The actions a rule can add approval to, and the kind of target each binds to.
APPROVABLE: dict[str, str] = {
    "app.deregister": "app",
    "cluster.decommission": "cluster",
    "managed_service.deprovision": "managed_service",
    "brain.delete": "app",
}

#: How long a request stays open, pending or approved, before it expires.
APPROVAL_TTL = timedelta(hours=24)
MAX_APPROVALS = 5


@dataclass(frozen=True)
class Requirements:
    action: str
    enabled: bool = True
    confirmation: bool = True
    verification: bool = True
    allow_self_approval: bool = True
    approval: bool = False
    minimum_approvals: int = 1
    #: Whether an agent may run it at all (``AGENT_GATED`` actions only).
    allow_agents: bool = False
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
    rows = _rows(organization_id, action, scopes)
    enabled, self_approval, sources = DEFAULT_ON, True, []
    agents = False
    org = next((r for r in rows if r.scope_kind == "ORG"), None)
    if org is not None:
        sources.append("ORG")
        if org.enabled is not None:
            enabled = org.enabled
        agents = action in AGENT_GATED and org.allow_agents is True
    for row in rows:
        if row.scope_kind != "ORG":
            sources.append(f"{row.scope_kind}:{row.scope_id}")
            # A narrower scope can turn a gate on, never off.
            if row.enabled:
                enabled = True
        if row.allow_self_approval is False:
            self_approval = False
        # Strictest wins: a narrower scope can turn agents back off, never on.
        if row.scope_kind != "ORG" and row.allow_agents is False:
            agents = False
    asking = [r for r in rows if r.require_approval] if action in APPROVABLE else []
    quorum = max((r.minimum_approvals or 1 for r in asking), default=1)
    return Requirements(
        action=action,
        enabled=enabled,
        confirmation=enabled,
        verification=enabled,
        allow_self_approval=self_approval,
        approval=bool(asking),
        minimum_approvals=min(quorum, MAX_APPROVALS),
        allow_agents=agents,
        sources=tuple(sources),
    )


def agent_refusal(action: str, *, app: Any = None) -> str | None:
    """Why an agent may not run ``action`` here, or ``None`` when the
    organization allows agents to (approval rules still apply then)."""
    from core.tenancy import get_current_tenant

    if action not in AGENT_GATED:
        return None
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None:
        return "no active organization"
    requirements = effective(
        tenant.organization_id,
        action,
        team_id=getattr(app, "team_id", None),
        app_id=getattr(app, "pk", None),
        user_id=tenant.actor_user_id,
    )
    if requirements.allow_agents:
        return None
    command, screen = {
        "brain.share": ("astro brain share", "the Brains screen"),
        "brain.delete": ("astro brain delete", "the Brains screen"),
        "app.expiry.clear": ("astro app expiry --clear", "the app's page"),
        "app.edge.make_public": ("astro app public on", "the app's Access tab"),
    }[action]
    return f"this action needs a person: run `{command}` or use {screen}"


def session_needs_step_up(
    organization_id: int,
    action: str,
    *,
    session: Any,
    app: Any = None,
    user_id: int | None = None,
) -> bool:
    """For a path with no GraphQL envelope (the exec WebSocket): True when
    ``action`` asks for verification here and ``session`` is not freshly elevated."""
    from astrolift_identity.session_elevation import get_status

    requirements = effective(
        organization_id,
        action,
        team_id=getattr(app, "team_id", None),
        app_id=getattr(app, "pk", None),
        user_id=user_id,
    )
    return requirements.verification and not get_status(session).elevated


def gate(
    info: Any, action: str, *, app: Any = None, target: Any = None, resolver_name: str = ""
) -> Any | None:
    """``None`` when the caller may go ahead, else the ``STEP_UP_REQUIRED`` or
    ``APPROVAL_REQUIRED`` envelope. ``target`` is the row an approvable action
    is about (it needs a ``guid``)."""
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
    if requirements.verification:
        deny = check_elevation(
            info,
            action_label=ACTIONS[action],
            resolver_name=resolver_name or action,
            required=True,
        )
        if deny is not None:
            return deny
    if requirements.approval and target is not None:
        return _approved_or_requested(tenant, action, requirements, app=app, target=target)
    return None


def _label(target: Any) -> str:
    return str(getattr(target, "name", "") or getattr(target, "slug", "") or getattr(target, "guid", ""))[
        :255
    ]


def audit(action: str, row: Any, *, actor_user_id: int | None, **extra: Any) -> None:
    from core.mutations import AuditEntry, emit_audit

    emit_audit(
        AuditEntry(
            actor_user_id=actor_user_id,
            organization_id=row.organization_id,
            action=action,
            decision="ALLOW",
            target_kind="action_approval_request",
            target_id=str(row.guid),
            duration_ms=0,
            permissions=(),
            extra={
                "action": row.action,
                "target_kind": row.target_kind,
                "target_guid": row.target_guid,
                "status": row.status,
                **extra,
            },
        )
    )


def _approved_or_requested(tenant: Any, action: str, requirements: Requirements, *, app: Any, target: Any):
    """Consume this requester's approved request for exactly this action and
    target, or open (or return the already open) one and refuse."""
    from django.db import transaction
    from django.utils import timezone

    from astrolift_graphql import failure as gql_failure
    from astrolift_identity.models import ActionApprovalRequest
    from core.mutations import ErrorCode

    Status = ActionApprovalRequest.Status
    now = timezone.now()
    key = {
        "organization_id": tenant.organization_id,
        "action": action,
        "target_kind": APPROVABLE[action],
        "target_guid": str(target.guid),
        "requester_id": tenant.actor_user_id,
    }
    with transaction.atomic():
        row = (
            ActionApprovalRequest.objects.select_for_update()
            .filter(**key, status__in=[Status.PENDING, Status.APPROVED], deleted_at__isnull=True)
            .first()
        )
        if row is not None and row.expires_at <= now:
            row.transition_to(Status.EXPIRED)
            row.save(update_fields=["status", "decided_at", "updated_at", "version"])
            audit("action_safety.approval.expire", row, actor_user_id=tenant.actor_user_id)
            row = None
        if row is not None and row.status == Status.APPROVED:
            row.transition_to(Status.CONSUMED)
            row.save(update_fields=["status", "consumed_at", "updated_at", "version"])
            audit("action_safety.approval.consume", row, actor_user_id=tenant.actor_user_id)
            return None
        if row is None:
            row = ActionApprovalRequest.objects.create(
                **key,
                target_label=_label(target),
                registered_app=app,
                required_approvals=requirements.minimum_approvals,
                allow_self_approval=requirements.allow_self_approval,
                expires_at=now + APPROVAL_TTL,
                created_by_id=tenant.actor_user_id,
                updated_by_id=tenant.actor_user_id,
            )
            transaction.on_commit(lambda: notify_approvers(row))
            audit(
                "action_safety.approval.request",
                row,
                actor_user_id=tenant.actor_user_id,
                required_approvals=row.required_approvals,
                sources=list(requirements.sources),
            )
    n = row.required_approvals
    return gql_failure(
        ErrorCode.APPROVAL_REQUIRED.value,
        f"{ACTIONS[action]} needs approval from {n} approver{'s' if n != 1 else ''}; "
        "run it again once it is approved.",
        approval_request_id=str(row.guid),
    )


#: Where approval requests are decided; notifications link here.
APPROVALS_PATH = "/administration/permissions/safety"
#: An upper bound on members checked for approval authority per request.
MAX_NOTIFIED_MEMBERS = 500


def approver_permission(row: Any):
    """What deciding ``row`` asks of the approver: the authority the action itself needs."""
    from core.permissions import Permission, PermissionScope, ScopeKind

    org = PermissionScope(kind=ScopeKind.ORG, id=row.organization_id)
    app = PermissionScope(kind=ScopeKind.APP, id=row.registered_app_id) if row.registered_app_id else None
    if row.action in ("app.deregister", "brain.delete"):
        return Permission.APP_DELETE, app or org
    if row.action == "cluster.decommission":
        return Permission.CLUSTER_UNREGISTER, org
    return (Permission.APP_UPDATE, app) if app else (Permission.ORG_UPDATE, org)


def notify_approvers(row: Any) -> int:
    """Tell every active member who could decide ``row``, other than the
    requester, that it is waiting. Opening the notification only links to the
    requests; it never approves anything. Returns how many were notified."""
    from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
    from astrolift_identity.models import Member
    from astrolift_operations.models import Notification
    from core.permissions import PermissionDenied, check_permission
    from core.tenancy import TenantContext, tenant_context

    permission, scope = approver_permission(row)
    members = (
        Member.objects.filter(
            scope_kind="ORG",
            scope_id=row.organization_id,
            is_active=True,
            lifecycle="active",
            user__is_active=True,
        )
        .exclude(user_id=row.requester_id)
        .select_related("user")[:MAX_NOTIFIED_MEMBERS]
    )
    requester = getattr(row.requester, "email", "") or "Someone"
    sent = 0
    marker = set_current_api_token(None)
    try:
        for member in members:
            with tenant_context(
                TenantContext(organization_id=row.organization_id, actor_user_id=member.user_id)
            ):
                try:
                    check_permission(permission, scope=scope)
                except PermissionDenied:
                    continue
            Notification.objects.create(
                organization_id=row.organization_id,
                user=member.user,
                kind=Notification.Kind.ACTION_APPROVAL,
                title=f"Approval requested: {ACTIONS.get(row.action, row.action)} {row.target_label}"[:255],
                body=(
                    f"{requester} asked to {ACTIONS.get(row.action, row.action).lower()} "
                    f"{row.target_label}. It needs {row.required_approvals} approval(s) "
                    f"and expires at {row.expires_at:%Y-%m-%d %H:%M} UTC."
                ),
                link=APPROVALS_PATH,
            )
            sent += 1
    finally:
        reset_current_api_token(marker)
    return sent


def notify_requester(row: Any) -> None:
    """Tell the requester their request was approved (run it again) or rejected."""
    from astrolift_operations.models import Notification

    if row.requester_id is None:
        return
    label = f"{ACTIONS.get(row.action, row.action)} {row.target_label}"
    approved = row.status == "approved"
    Notification.objects.create(
        organization_id=row.organization_id,
        user_id=row.requester_id,
        kind=Notification.Kind.ACTION_APPROVAL,
        title=(f"Approved: {label}" if approved else f"Rejected: {label}")[:255],
        body=(
            "Run it again to go ahead; the approval is spent on that run."
            if approved
            else "An approver rejected this request. Nothing was run."
        ),
        link=APPROVALS_PATH,
    )
