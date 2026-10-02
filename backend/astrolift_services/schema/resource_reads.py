"""Bounded credential-free project/service reads (#2207).

These projections deliberately do not call the legacy service serializer: even
an id-only selection must not load provider config, connection material or grants.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid

import strawberry
from django.core import signing
from django.db.models import Q
from graphql import GraphQLError
from strawberry.types import Info

from astrolift_graphql import GUID, PageType, search_q
from astrolift_graphql.pagination import KeysetPage, clamp_limit, keyset_page
from astrolift_identity.api_tokens import get_current_api_token
from astrolift_identity.operation_context import UNKNOWN, OperationContext, environment_context
from astrolift_identity.operation_visibility import _operation_policies, visible_operation_rows
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import live_app_owners
from astrolift_registry.visibility import visible_registry_apps
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.scopes import (
    _credential_scope,
    services_org_scope,
    services_project_scope_by_guid,
)
from astrolift_services.visibility import credential_managed_services
from core.decorators import tenant_scoped
from core.permissions import Permission, PermissionScope, ScopeKind, require_permission
from core.scope_args import read_guid
from core.tenancy import get_current_tenant

_CURSOR_SALT = "astrolift-managed-resource-pages-2207"


@strawberry.type(name="AstroliftManagedServiceContext")
class ManagedServiceContext:
    id: GUID
    context_revision: str
    version: int
    name: str
    kind: str
    variant: str
    status: str
    owner_scope: str
    organization_id: GUID
    project_id: GUID | None
    project_slug: str
    registered_app_id: GUID | None
    registered_app_slug: str
    cluster_id: GUID
    cluster_slug: str
    cluster_version: int
    environment_id: GUID | None
    environment_name: str
    environment_version: int | None
    created_at: dt.datetime
    updated_at: dt.datetime
    operation_kind: str
    operation_workflow_id: str
    operation_run_id: str
    operation_started_at: dt.datetime | None
    operation_completed_at: dt.datetime | None


@strawberry.type(name="AstroliftManagedServiceAttachmentContext")
class ManagedServiceAttachmentContext:
    id: GUID
    version: int
    managed_service_id: GUID
    consumer_kind: str
    consumer_id: GUID
    consumer_slug: str
    registered_app_id: GUID | None
    environment_id: GUID | None
    environment_name: str
    cluster_id: GUID
    created_at: dt.datetime


def _rows():
    return credential_managed_services(
        ManagedService.objects.filter(
            Q(registered_app__isnull=False, project__isnull=True, organization__isnull=True)
            | Q(
                project__isnull=False,
                registered_app__isnull=True,
                app_environment__isnull=True,
                organization__isnull=True,
            ),
        )
        .select_related(
            "project__organization",
            "registered_app__organization",
            "registered_app__project",
            "app_environment__tenant_cluster",
            "tenant_cluster",
        )
        .only(
            "guid",
            "name",
            "kind",
            "variant",
            "status",
            "version",
            "created_at",
            "updated_at",
            "operation_kind",
            "operation_workflow_id",
            "operation_run_id",
            "operation_started_at",
            "operation_completed_at",
            "environment_name",
            "project__guid",
            "project__slug",
            "project__version",
            "project__organization__guid",
            "registered_app__guid",
            "registered_app__slug",
            "registered_app__version",
            "registered_app__organization__guid",
            "registered_app__project__guid",
            "registered_app__project__slug",
            "registered_app__project__version",
            "app_environment__guid",
            "app_environment__name",
            "app_environment__version",
            "app_environment__tenant_cluster__guid",
            "app_environment__tenant_cluster__slug",
            "app_environment__tenant_cluster__version",
            "app_environment__tenant_cluster__region",
            "tenant_cluster__guid",
            "tenant_cluster__slug",
            "tenant_cluster__version",
            "tenant_cluster__region",
        )
    )


def _guid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def context_row(id, *, project_id=None):
    guid = _guid(id)
    if guid is None:
        return None
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    rows = _rows().filter(
        Q(project__organization_id=org_id) | Q(registered_app__organization_id=org_id), guid=guid
    )
    if project_id is not None:
        project_guid = _guid(project_id)
        rows = rows.filter(project__guid=project_guid, registered_app__isnull=True)
    return rows.first()


def resource_scope(field):
    """Resolve the exact live owner without loading service or provider secrets."""

    def scope(args):
        row = context_row(read_guid(args, field))
        resolved = services_org_scope()
        if row:
            resolved = PermissionScope(
                ScopeKind.APP if row.registered_app_id else ScopeKind.PROJECT,
                row.registered_app_id or row.project_id,
            )
        return _credential_scope(resolved, (Permission.APP_READ,))

    return scope


def resource_operation(field):
    def load(args):
        row = context_row(read_guid(args, field))
        if row is None:
            return UNKNOWN
        if row.app_environment_id:
            return (environment_context(row.app_environment),)
        return (
            OperationContext(
                environment=row.effective_environment_name,
                region=row.tenant_cluster.region or None,
                approvals=0,
            ),
        )

    return load


def context_revision(row) -> str:
    app = row.registered_app
    project = row.project if row.project_id else (app.project if app and app.project_id else None)
    cluster = row.effective_cluster
    env = row.app_environment
    facts = [str(row.guid), row.version]
    for item in (project, app, cluster, env):
        facts.append([str(item.guid), item.version] if item is not None else None)
    facts.append(row.effective_environment_name)
    return hashlib.sha256(json.dumps(facts, separators=(",", ":")).encode()).hexdigest()


def check_context_revision(row, expected: str | None) -> None:
    if expected is not None and expected != context_revision(row):
        raise GraphQLError(
            "Managed resource context changed; review its exact GUID again",
            extensions={"code": "STALE_TARGET"},
        )


def to_context(row) -> ManagedServiceContext:
    app = row.registered_app
    project = row.project if row.project_id else (app.project if app and app.project_id else None)
    org = row.project.organization if row.project_id else app.organization
    cluster = row.effective_cluster
    env = row.app_environment
    return ManagedServiceContext(
        id=GUID(str(row.guid)),
        context_revision=context_revision(row),
        version=row.version,
        name=row.name,
        kind=row.kind,
        variant=row.variant or "",
        status=row.status,
        owner_scope=row.owner_scope,
        organization_id=GUID(str(org.guid)),
        project_id=GUID(str(project.guid)) if project else None,
        project_slug=project.slug if project else "",
        registered_app_id=GUID(str(app.guid)) if app else None,
        registered_app_slug=app.slug if app else "",
        cluster_id=GUID(str(cluster.guid)),
        cluster_slug=cluster.slug,
        cluster_version=cluster.version,
        environment_id=GUID(str(env.guid)) if env else None,
        environment_name=row.effective_environment_name,
        environment_version=env.version if env else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
        operation_kind=row.operation_kind or "",
        operation_workflow_id=row.operation_workflow_id or "",
        operation_run_id=row.operation_run_id or "",
        operation_started_at=row.operation_started_at,
        operation_completed_at=row.operation_completed_at,
    )


def _page(qs, *, scope, limit, after, convert):
    tenant = get_current_tenant()
    token = get_current_api_token()
    context = [
        scope,
        tenant.organization_id if tenant else None,
        tenant.actor_user_id if tenant else None,
        tenant.team_id if tenant else None,
        str(token.guid) if token else None,
    ]
    scope_key = hashlib.sha256(json.dumps(context, separators=(",", ":")).encode()).hexdigest()
    cursor = None
    if after is not None:
        try:
            if not after or len(after) > 2048:
                raise ValueError
            decoded = signing.loads(after, salt=_CURSOR_SALT)
            if (
                not isinstance(decoded, dict)
                or set(decoded) != {"scope", "id", "created"}
                or decoded["scope"] != scope_key
            ):
                raise ValueError
            guid = uuid.UUID(decoded["id"])
            created = dt.datetime.fromisoformat(decoded["created"])
            if created.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError, KeyError, signing.BadSignature) as exc:
            raise GraphQLError(
                "Resource page cursor is invalid for this scope", extensions={"code": "INVALID_CURSOR"}
            ) from exc
        if not qs.filter(guid=guid, created_at=created).exists():
            raise GraphQLError(
                "Resource page changed; restart from the first page", extensions={"code": "STALE_CURSOR"}
            )
        from astrolift_graphql.pagination import encode_cursor

        cursor = encode_cursor(created, guid)
    page: KeysetPage[ManagedService | ManagedServiceAttachment] = keyset_page(
        qs, cursor=cursor, limit=clamp_limit(limit)
    )
    next_cursor = None
    if page.next_cursor and page.rows:
        anchor = page.rows[-1]
        next_cursor = signing.dumps(
            {"scope": scope_key, "id": str(anchor.guid), "created": anchor.created_at.isoformat()},
            salt=_CURSOR_SALT,
            compress=True,
        )
    return PageType(
        items=[convert(row) for row in page.rows], total_count=page.total_count, next_cursor=next_cursor
    )


def _project_rows(project_id):
    return _rows().filter(project__guid=_guid(project_id), registered_app__isnull=True)


def _attachment_rows(service):
    """Only live consumers still attached to the reviewed owner and cluster."""
    cluster = service.effective_cluster
    apps = live_app_owners(visible_registry_apps(RegisteredApp.objects.all(), Permission.APP_READ))
    app_rows = ManagedServiceAttachment.objects.filter(
        managed_service=service,
        app_environment__registered_app__in=apps,
        app_environment__deleted_at__isnull=True,
        app_environment__tenant_cluster=cluster,
    )
    if service.project_id:
        app_rows = app_rows.filter(app_environment__registered_app__project_id=service.project_id)
    else:
        app_rows = app_rows.filter(app_environment_id=service.app_environment_id)
    # The app subquery already applies live actor and bearer visibility. Avoid
    # nesting the same scope joins twice when there are no operation policies.
    if _operation_policies(get_current_tenant(), Permission.APP_READ):
        app_rows = visible_operation_rows(
            app_rows,
            Permission.APP_READ,
            app_path="app_environment__registered_app",
            environment_path="app_environment",
        )
    rows = app_rows
    if service.project_id:
        from astrolift_agents.services.agent_cluster import NoAgentClusterError, resolve_agent_cluster
        from astrolift_agents.visibility import environment_specs
        from astrolift_registry.models import Workload
        from workflows.models import WorkflowStage

        try:
            current_cluster = resolve_agent_cluster(service.project.organization)
        except NoAgentClusterError:
            current_cluster = None
        if current_cluster and current_cluster.pk == cluster.pk:
            specs = environment_specs(service.project.organization_id, Permission.AGENT_ENV_SPEC_READ)
            workloads = Workload.objects.filter(
                registered_app__project_id=service.project_id,
                registered_app__in=apps,
                kind=Workload.Kind.AGENT,
            ).values("slug")
            stages = WorkflowStage.objects.filter(
                definition__project_id=service.project_id,
                definition__organization_id=service.project.organization_id,
                definition__deleted_at__isnull=True,
            ).values("environment_spec_slug")
            specs = specs.filter(Q(slug__in=workloads) | Q(slug__in=stages))
            agent_rows = ManagedServiceAttachment.objects.filter(
                managed_service=service, agent_environment_spec__in=specs
            )
            rows = ManagedServiceAttachment.objects.filter(
                pk__in=app_rows.order_by().values("pk").union(agent_rows.order_by().values("pk"))
            )
    return rows.select_related(
        "app_environment__registered_app",
        "agent_environment_spec",
    ).only(
        "guid",
        "version",
        "created_at",
        "managed_service_id",
        "app_environment__guid",
        "app_environment__name",
        "app_environment__registered_app__guid",
        "app_environment__registered_app__slug",
        "agent_environment_spec__guid",
        "agent_environment_spec__slug",
    )


def _attachments(service, *, expected_context_revision, limit, after):
    check_context_revision(service, expected_context_revision)

    def convert(row):
        env = row.app_environment
        spec = row.agent_environment_spec
        app = env.registered_app if env else None
        return ManagedServiceAttachmentContext(
            id=GUID(str(row.guid)),
            version=row.version,
            managed_service_id=GUID(str(service.guid)),
            consumer_kind="app_environment" if env else "agent_environment_spec",
            consumer_id=GUID(str(env.guid if env else spec.guid)),
            consumer_slug=app.slug if app else spec.slug,
            registered_app_id=GUID(str(app.guid)) if app else None,
            environment_id=GUID(str(env.guid)) if env else None,
            environment_name=env.name if env else "",
            cluster_id=GUID(str(service.effective_cluster.guid)),
            created_at=row.created_at,
        )

    return _page(
        _attachment_rows(service),
        scope=["attachments", str(service.guid), context_revision(service)],
        limit=limit,
        after=after,
        convert=convert,
    )


@strawberry.type
class ManagedResourceReadsQuery:
    @strawberry.field
    @require_permission(
        Permission.PROJECT_READ,
        scope=services_project_scope_by_guid("project_id", permissions=(Permission.PROJECT_READ,)),
    )
    @tenant_scoped()
    def astrolift_project_managed_service_attachment_owner(
        self, info: Info, project_id: GUID, attachment_id: GUID
    ) -> ManagedServiceContext | None:
        """Exact visible attachment owner for compatible, reviewed detach (#2207).

        Neither attachment credentials nor provider configuration are loaded.
        A missing/deleted/foreign/inaccessible attachment has no owner fallback.
        """
        tenant = get_current_tenant()
        guid = _guid(attachment_id)
        if guid is None or tenant is None:
            return None
        owner = (
            _project_rows(project_id)
            .filter(project__organization_id=tenant.organization_id, attachments__guid=guid)
            .first()
        )
        if (
            owner is None
            or not _attachment_rows(owner)
            .filter(guid=guid, managed_service__project__organization_id=tenant.organization_id)
            .exists()
        ):
            return None
        return to_context(owner)

    @strawberry.field
    @require_permission(
        Permission.PROJECT_READ,
        scope=services_project_scope_by_guid("project_id", permissions=(Permission.PROJECT_READ,)),
    )
    @tenant_scoped()
    def astrolift_project_managed_services_page(
        self,
        info: Info,
        project_id: GUID,
        search: str | None = None,
        name: str | None = None,
        kinds: list[str] | None = None,
        statuses: list[str] | None = None,
        environment_name: str | None = None,
        cluster_id: GUID | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[ManagedServiceContext]:
        rows = _project_rows(project_id)
        if search:
            rows = rows.filter(
                search_q(
                    search.strip(),
                    "name",
                    "kind",
                    "variant",
                    "status",
                    "environment_name",
                    "tenant_cluster__slug",
                )
            )
        if name is not None:
            rows = rows.filter(name=name)
        if kinds:
            rows = rows.filter(kind__in=kinds)
        if statuses:
            rows = rows.filter(status__in=statuses)
        if environment_name is not None:
            rows = rows.filter(environment_name=environment_name)
        if cluster_id is not None:
            rows = rows.filter(tenant_cluster__guid=_guid(cluster_id))
        scope = [
            "project",
            str(project_id),
            (search or "").strip(),
            name,
            sorted(set(kinds or [])),
            sorted(set(statuses or [])),
            environment_name,
            str(cluster_id) if cluster_id else None,
        ]
        return _page(rows, scope=scope, limit=limit, after=after, convert=to_context)

    @strawberry.field
    @require_permission(
        Permission.PROJECT_READ,
        scope=services_project_scope_by_guid("project_id", permissions=(Permission.PROJECT_READ,)),
    )
    @tenant_scoped()
    def astrolift_project_managed_service(
        self, info: Info, project_id: GUID, id: GUID, expected_context_revision: str | None = None
    ) -> ManagedServiceContext | None:
        row = context_row(id, project_id=project_id)
        if row is None:
            return None
        check_context_revision(row, expected_context_revision)
        return to_context(row)

    @strawberry.field
    @require_permission(
        Permission.APP_READ,
        scope=resource_scope("id"),
        operation=resource_operation("id"),
    )
    @tenant_scoped()
    def astrolift_managed_service(
        self, info: Info, id: GUID, expected_context_revision: str | None = None
    ) -> ManagedServiceContext | None:
        row = context_row(id)
        if row is None:
            return None
        check_context_revision(row, expected_context_revision)
        return to_context(row)

    @strawberry.field
    @require_permission(
        Permission.PROJECT_READ,
        scope=services_project_scope_by_guid("project_id", permissions=(Permission.PROJECT_READ,)),
    )
    @tenant_scoped()
    def astrolift_project_managed_service_attachments_page(
        self,
        info: Info,
        project_id: GUID,
        managed_service_id: GUID,
        expected_context_revision: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[ManagedServiceAttachmentContext] | None:
        row = context_row(managed_service_id, project_id=project_id)
        if row is None:
            raise GraphQLError("Managed resource is unavailable", extensions={"code": "TARGET_UNAVAILABLE"})
        return _attachments(
            row, expected_context_revision=expected_context_revision, limit=limit, after=after
        )

    @strawberry.field
    @require_permission(
        Permission.APP_READ,
        scope=resource_scope("managed_service_id"),
        operation=resource_operation("managed_service_id"),
    )
    @tenant_scoped()
    def astrolift_managed_service_attachments_page(
        self,
        info: Info,
        managed_service_id: GUID,
        expected_context_revision: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[ManagedServiceAttachmentContext] | None:
        row = context_row(managed_service_id)
        if row is None:
            raise GraphQLError("Managed resource is unavailable", extensions={"code": "TARGET_UNAVAILABLE"})
        return _attachments(
            row, expected_context_revision=expected_context_revision, limit=limit, after=after
        )
