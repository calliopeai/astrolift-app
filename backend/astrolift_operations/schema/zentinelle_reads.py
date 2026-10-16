"""Zentinelle usage, policy and audit reads, and whether Zentinelle is connected (#1888).

Each read is gated on its ``zentinelle.*`` permission at any scope, then
narrowed here to the agent workloads the caller holds that permission on:
the whole tenant only for an org-wide grant with no agent, project or team
named, otherwise just those agents' slugs, which is what Zentinelle files a
run's usage and evidence under (``deployment_id``). A slug another live
agent in the organization also uses is left out, since Zentinelle cannot
tell the two apart. The calls themselves are in
``astrolift_operations.zentinelle_reads``.

A read fails with the reason when the organization is not connected, when
Zentinelle refuses or cannot be reached, and when it does not offer the read
yet; it never answers an empty list for any of those.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta

import strawberry
from django.db.models import Q
from django.utils import timezone
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_operations import zentinelle_reads
from astrolift_operations.models import ZentinelleConnection
from astrolift_operations.scopes import org_id as _caller_org_id
from astrolift_operations.zentinelle_connect import ZentinelleConnectError
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, granted_scopes, require_permission
from core.tenancy import get_current_tenant

DEFAULT_USAGE_WINDOW = timedelta(days=30)
EXPORT_MAX_ROWS = 50_000


class _ReadError(Exception):
    def __init__(self, code: ErrorCode, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.field = code, message, field


def _graphql_error(exc: _ReadError | ZentinelleConnectError) -> GraphQLError:
    extensions = {"code": exc.code.value}
    if exc.field:
        extensions["field"] = exc.field
    return GraphQLError(exc.message, extensions=extensions)


def _agent_slugs(
    permission: Permission,
    *,
    agent_slug: str | None = None,
    project_id: str | None = None,
    team_id: str | None = None,
) -> list[str] | None:
    """The agent slugs a read may cover, or None for the whole tenant."""
    from astrolift_agents.visibility import agent_workloads
    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_registry.models import Workload

    named = [value for value in (agent_slug, project_id, team_id) if value]
    if len(named) > 1:
        raise _ReadError(ErrorCode.VALIDATION, "name one of agentSlug, projectId or teamId, not several")
    org_id = _caller_org_id()
    token = get_current_api_token()
    if (
        not named
        and granted_scopes(get_current_tenant(), permission).org
        and (token is None or token.team_id is None)
    ):
        return None
    rows = agent_workloads(org_id, permission)
    if agent_slug:
        rows = rows.filter(slug=agent_slug)
        if not rows.exists():
            raise _ReadError(ErrorCode.NOT_FOUND, f"agent {agent_slug!r} not found", field="agentSlug")
    elif project_id:
        rows = rows.filter(registered_app__project__guid=str(project_id))
    elif team_id:
        rows = rows.filter(
            Q(registered_app__team__guid=str(team_id)) | Q(registered_app__project__team__guid=str(team_id))
        )
    selected = dict(rows.values_list("pk", "slug"))
    shared = set(
        Workload.objects.filter(
            kind=Workload.Kind.AGENT,
            registered_app__organization_id=org_id,
            registered_app__deleted_at__isnull=True,
            deleted_at__isnull=True,
            slug__in=set(selected.values()),
        )
        .exclude(pk__in=selected.keys())
        .values_list("slug", flat=True)
    )
    if agent_slug and agent_slug in shared:
        raise _ReadError(
            ErrorCode.PRECONDITION,
            f"another agent in this organization is also called {agent_slug!r}; Zentinelle cannot tell them apart",
            field="agentSlug",
        )
    return sorted(set(selected.values()) - shared)


def _connection() -> ZentinelleConnection:
    return zentinelle_reads.connection_for(_caller_org_id())


# ---- types ------------------------------------------------------------------


@strawberry.type(name="AstroliftZentinelleCapability")
class ZentinelleCapabilityType:
    """What a client may offer for Zentinelle on this server, read once per session."""

    connected: bool
    """Whether the caller's organization is connected to Zentinelle and Zentinelle
    still accepts it, as far as Astrolift last heard."""


@strawberry.type(name="AstroliftZentinelleUsageRow")
class ZentinelleUsageRowType:
    agent_slug: str
    provider: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


@strawberry.type(name="AstroliftZentinelleUsage")
class ZentinelleUsageType:
    since: datetime
    rows: list[ZentinelleUsageRowType]


@strawberry.type(name="AstroliftZentinellePolicy")
class ZentinellePolicyType:
    id: str
    name: str
    policy_type: str
    enforcement: str
    scope: str
    priority: int
    description: str


@strawberry.type(name="AstroliftZentinelleAuditEvent")
class ZentinelleAuditEventType:
    id: str
    occurred_at: datetime | None
    action: str
    resource_type: str
    resource_id: str
    resource_name: str
    agent_slug: str
    agent_id: str
    actor: str


@strawberry.type(name="AstroliftZentinelleAuditPage")
class ZentinelleAuditPageType:
    events: list[ZentinelleAuditEventType]
    next_cursor: str | None


@strawberry.type(name="AstroliftZentinelleAuditExport")
class ZentinelleAuditExportType:
    filename: str
    content: str
    content_type: str
    row_count: int


@strawberry.input
class ExportZentinelleAuditInput:
    since: datetime
    until: datetime | None = None
    format: str = "ndjson"
    agent_slug: str | None = None
    project_id: GUID | None = None  # type: ignore[valid-type]
    team_id: GUID | None = None  # type: ignore[valid-type]


def _event(row: zentinelle_reads.AuditEvent) -> ZentinelleAuditEventType:
    return ZentinelleAuditEventType(
        id=row.id,
        occurred_at=row.occurred_at,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        resource_name=row.resource_name,
        agent_slug=row.agent_slug,
        agent_id=row.agent_id,
        actor=row.actor,
    )


_EXPORT_FIELDS = (
    "id",
    "occurred_at",
    "action",
    "resource_type",
    "resource_id",
    "resource_name",
    "agent_slug",
    "agent_id",
    "actor",
)


def _export_value(event: zentinelle_reads.AuditEvent, field: str) -> str:
    value = getattr(event, field)
    if isinstance(value, datetime):
        return value.isoformat()
    return "" if value is None else str(value)


def _cell(text: str) -> str:
    """A CSV cell a spreadsheet would run as a formula, quoted."""
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def _render(events: list[zentinelle_reads.AuditEvent], fmt: str) -> str:
    if fmt == "ndjson":
        return "".join(
            json.dumps({field: _export_value(e, field) for field in _EXPORT_FIELDS}) + "\n" for e in events
        )
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(_EXPORT_FIELDS)
    for event in events:
        writer.writerow([_cell(_export_value(event, field)) for field in _EXPORT_FIELDS])
    return out.getvalue()


def _export_extras(result) -> dict | None:
    if not getattr(result, "ok", False) or result.data is None:
        return None
    return {"row_count": result.data.row_count, "filename": result.data.filename}


# ---- resolvers ----------------------------------------------------------------


@strawberry.type
class ZentinelleReadsQuery:
    @strawberry.field
    @require_permission(Permission.ORG_READ, any_scope=True)
    @tenant_scoped()
    def astrolift_zentinelle_capability(self, info: Info) -> ZentinelleCapabilityType:
        """Whether Zentinelle commands and screens apply to the caller's organization.
        Answers from Astrolift's own record, without calling Zentinelle."""
        org_id = _caller_org_id()
        connected = (
            org_id is not None
            and ZentinelleConnection.objects.filter(
                organization_id=org_id, status=ZentinelleConnection.Status.CONNECTED, deleted_at__isnull=True
            ).exists()
        )
        return ZentinelleCapabilityType(connected=connected)

    @strawberry.field
    @require_permission(Permission.ZENTINELLE_USAGE_VIEW, any_scope=True)
    @tenant_scoped()
    def astrolift_zentinelle_usage(
        self,
        info: Info,
        since: datetime | None = None,
        agent_slug: str | None = None,
        project_id: GUID | None = None,  # type: ignore[valid-type]
        team_id: GUID | None = None,  # type: ignore[valid-type]
    ) -> ZentinelleUsageType:
        """Model usage and cost from Zentinelle's gateway metering since ``since``
        (default 30 days ago), per agent, provider and model, for the agents the
        caller may see: one agent, a project's, a team's, or all of them."""
        since = since or timezone.now() - DEFAULT_USAGE_WINDOW
        try:
            slugs = _agent_slugs(
                Permission.ZENTINELLE_USAGE_VIEW,
                agent_slug=agent_slug,
                project_id=project_id,
                team_id=team_id,
            )
            connection = _connection()
            rows = [] if slugs == [] else zentinelle_reads.usage(connection, since=since, agent_slugs=slugs)
        except (_ReadError, ZentinelleConnectError) as exc:
            raise _graphql_error(exc) from None
        return ZentinelleUsageType(
            since=since,
            rows=[
                ZentinelleUsageRowType(
                    agent_slug=r.agent_slug,
                    provider=r.provider,
                    model=r.model,
                    requests=r.requests,
                    input_tokens=r.input_tokens,
                    output_tokens=r.output_tokens,
                    cost_usd=r.cost_usd,
                )
                for r in rows
            ],
        )

    @strawberry.field
    @require_permission(Permission.ZENTINELLE_POLICY_VIEW, any_scope=True)
    @tenant_scoped()
    def astrolift_zentinelle_policies(
        self,
        info: Info,
        agent_slug: str | None = None,
        project_id: GUID | None = None,  # type: ignore[valid-type]
    ) -> list[ZentinellePolicyType]:
        """The Zentinelle policies in effect for an agent, a project's agents, or
        (with neither, for an org-wide viewer) the organization's tenant."""
        try:
            slugs = _agent_slugs(
                Permission.ZENTINELLE_POLICY_VIEW, agent_slug=agent_slug, project_id=project_id
            )
            connection = _connection()
            rows = [] if slugs == [] else zentinelle_reads.policies(connection, agent_slugs=slugs)
        except (_ReadError, ZentinelleConnectError) as exc:
            raise _graphql_error(exc) from None
        return [
            ZentinellePolicyType(
                id=p.id,
                name=p.name,
                policy_type=p.policy_type,
                enforcement=p.enforcement,
                scope=p.scope,
                priority=p.priority,
                description=p.description,
            )
            for p in rows
        ]

    @strawberry.field
    @require_permission(Permission.ZENTINELLE_AUDIT_VIEW, any_scope=True)
    @tenant_scoped()
    def astrolift_zentinelle_audit_page(
        self,
        info: Info,
        since: datetime | None = None,
        cursor: str | None = None,
        limit: int = 100,
        agent_slug: str | None = None,
        project_id: GUID | None = None,  # type: ignore[valid-type]
        team_id: GUID | None = None,  # type: ignore[valid-type]
    ) -> ZentinelleAuditPageType:
        """One page of Zentinelle's evidence and audit stream, oldest first, for
        the agents the caller may see. Pass ``nextCursor`` back for the next page;
        it is null at the end of the stream so far."""
        try:
            slugs = _agent_slugs(
                Permission.ZENTINELLE_AUDIT_VIEW,
                agent_slug=agent_slug,
                project_id=project_id,
                team_id=team_id,
            )
            connection = _connection()
            if slugs == []:
                events, next_cursor = [], None
            else:
                events, next_cursor = zentinelle_reads.audit_page(
                    connection, since=since, cursor=cursor, limit=limit, agent_slugs=slugs
                )
        except (_ReadError, ZentinelleConnectError) as exc:
            raise _graphql_error(exc) from None
        return ZentinelleAuditPageType(events=[_event(e) for e in events], next_cursor=next_cursor)


@strawberry.type
class ZentinelleReadsMutations:
    @strawberry.field
    @mutation_audit(action="zentinelle.audit.export", extras=_export_extras)
    @require_permission(Permission.ZENTINELLE_AUDIT_EXPORT, any_scope=True)
    @tenant_scoped()
    def export_zentinelle_audit(
        self, info: Info, input: ExportZentinelleAuditInput
    ) -> MutationResultType[ZentinelleAuditExportType]:
        """Zentinelle's evidence and audit stream between ``since`` and ``until``
        for the agents the caller may see, as one NDJSON or CSV document. Audited;
        refused past 50,000 events, to be narrowed by time or agent."""
        fmt = (input.format or "").strip().lower()
        if fmt not in ("ndjson", "csv"):
            return gql_failure(ErrorCode.VALIDATION.value, "format must be ndjson or csv", field="format")
        if input.until is not None and input.until < input.since:
            return gql_failure(ErrorCode.VALIDATION.value, "until is before since", field="until")
        try:
            slugs = _agent_slugs(
                Permission.ZENTINELLE_AUDIT_EXPORT,
                agent_slug=input.agent_slug,
                project_id=input.project_id,
                team_id=input.team_id,
            )
            connection = _connection()
            events: list[zentinelle_reads.AuditEvent] = []
            cursor = None
            while slugs != []:
                page, cursor = zentinelle_reads.audit_page(
                    connection,
                    since=input.since,
                    until=input.until,
                    cursor=cursor,
                    limit=zentinelle_reads.AUDIT_PAGE_MAX,
                    agent_slugs=slugs,
                )
                events.extend(page)
                if len(events) > EXPORT_MAX_ROWS:
                    return gql_failure(
                        ErrorCode.PRECONDITION.value,
                        f"the export would hold more than {EXPORT_MAX_ROWS} events; narrow it by time or agent",
                    )
                if cursor is None:
                    break
        except (_ReadError, ZentinelleConnectError) as exc:
            return gql_failure(exc.code.value, exc.message, field=exc.field)
        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        return gql_success(
            ZentinelleAuditExportType(
                filename=f"zentinelle-audit-{stamp}.{fmt}",
                content=_render(events, fmt),
                content_type="application/x-ndjson" if fmt == "ndjson" else "text/csv; charset=utf-8",
                row_count=len(events),
            )
        )
