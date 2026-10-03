"""Lifecycle GraphQL subscriptions — the app log streams.

Each is a ``@strawberry.subscription`` async generator gated like a field:
``@ws_identity`` pins the tenant and API token the WS handshake resolved,
then ``@require_permission(Permission.APP_READ_LOGS)`` checks at the app's
own scope and ``@tenant_scoped`` asserts the org, both when the stream is
first iterated (#1866). A refused or tenantless subscriber gets a stream
that completes without an event.

The actual log byte stream lives in
:mod:`core.cluster_observability`, which dispatches through the
``astrolift-providers`` ClusterDriver for the row's plugin (#299).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID
from astrolift_identity.operation_context import OperationContext, environment_context
from astrolift_lifecycle.preview_log_access import (
    PreviewLogAuthority,
    guarded_log_lines,
    refuse_unreviewed_preview_route,
    resolve_preview_log_target,
)
from astrolift_lifecycle.schema.types import AppLogLineType
from astrolift_lifecycle.scopes import live_app_scope
from core.decorators import tenant_scoped
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    check_permission,
    require_permission,
)
from core.schema.ws_auth import ws_identity


def _log_target(app_slug, *, plural=False, environment_name=None, workload_slug=None, **proof):
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp
    from core.cluster_observability import namespace_for_app, namespace_for_environment
    from core.tenancy import get_current_tenant

    reviewed = resolve_preview_log_target(
        app_slug, permission=Permission.APP_READ_LOGS, environment_name=environment_name, **proof
    )
    if reviewed is not None:
        preview, target = reviewed
        return (
            preview.registered_app,
            preview.app_environment.tenant_cluster,
            target.namespace,
            environment_context(preview.app_environment),
            target,
        )
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    app = (
        RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
        .filter(slug=app_slug, organization_id=org_id, deleted_at__isnull=True)
        .first()
    )
    if app is None:
        return None
    environments = list(
        AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True)
        .select_related("tenant_cluster", "registered_app__organization")
        .order_by("created_at")
    )
    cluster, namespace = app.default_tenant_cluster, namespace_for_app(app)
    verified_env = None
    if plural and environment_name:
        verified_env = next((env for env in environments if env.name == environment_name), None)
        if verified_env is None:
            return None
        cluster, namespace = verified_env.tenant_cluster, namespace_for_environment(verified_env)
    elif not plural and workload_slug and environments:
        cluster = environments[0].tenant_cluster
    if (
        cluster is None
        or not getattr(cluster, "is_active", True)
        or cluster.deleted_at is not None
        or cluster.organization_id not in (None, org_id)
    ):
        return None
    refuse_unreviewed_preview_route(app, cluster, namespace)
    if verified_env is None:
        matches = [
            env
            for env in environments
            if env.tenant_cluster_id == cluster.pk and namespace_for_environment(env) == namespace
        ]
        # Legacy pod logs name no environment. Only an unambiguous persisted
        # cluster/namespace pair proves one; a caller's workload hint does not.
        verified_env = matches[0] if len(matches) == 1 else None
    facts = (
        environment_context(verified_env)
        if verified_env is not None
        else OperationContext(region=cluster.region or None)
    )
    return app, cluster, namespace, facts, None


def _log_operation(*, plural=False):
    def load(args):
        target = _log_target(
            args["app_slug"],
            plural=plural,
            environment_name=args.get("environment_name"),
            workload_slug=args.get("workload_slug"),
            **{
                key: args.get(key)
                for key in (
                    "preview_id",
                    "expected_environment_id",
                    "if_match_preview_version",
                    "if_match_environment_version",
                )
            },
        )
        return (target[3] if target is not None else OperationContext(),)

    return load


def _admitted_log_target(app_slug, **kwargs):
    from astrolift_identity.abac import operation_attributes

    target = _log_target(app_slug, **kwargs)
    if target is None:
        return None
    app, cluster, namespace, facts, reviewed = target
    try:
        with operation_attributes(**facts.attributes()):
            check_permission(Permission.APP_READ_LOGS, scope=PermissionScope(kind=ScopeKind.APP, id=app.pk))
    except PermissionDenied:
        return None
    return app, cluster, namespace, reviewed


@strawberry.type
class LifecycleSubscription:
    @strawberry.subscription
    @ws_identity
    @require_permission(
        Permission.APP_READ_LOGS,
        scope=live_app_scope("app_slug", permission=Permission.APP_READ_LOGS),
        operation=_log_operation(),
    )
    @tenant_scoped()
    async def astrolift_on_app_log(
        self,
        info: Info,
        app_slug: str,
        pod_name: str,
        workload_slug: str | None = None,
        container: str | None = None,
        follow: bool = True,
        tail_lines: int = 100,
        preview_id: GUID | None = None,
        expected_environment_id: GUID | None = None,
        if_match_preview_version: int | None = None,
        if_match_environment_version: int | None = None,
    ) -> AsyncGenerator[AppLogLineType, None]:
        """Stream log lines from a single pod (and optional
        container) in the runtime cluster.

        Required args: ``app_slug`` + ``pod_name``. The pod is
        discovered via the existing ``astroliftAppPods`` query;
        the UI hands its name back to this subscription for the
        actual byte stream.

        Yields ``AstroliftAppLogLine`` events; completes silently
        when:
          - the WS tenant can't be resolved (anonymous connect)
          - APP_READ_LOGS isn't granted at the app's scope
          - the app has no cluster wired
          - the backend signals EOF

        On consumer disconnect, the generator is cancelled and the
        backend's ``CancelledError`` path runs — the kubernetes
        urllib3 connection is released back to the pool."""
        from asgiref.sync import sync_to_async

        from core.cluster_observability import stream_app_logs

        proof = {
            "preview_id": preview_id,
            "expected_environment_id": expected_environment_id,
            "if_match_preview_version": if_match_preview_version,
            "if_match_environment_version": if_match_environment_version,
        }
        resolved = await sync_to_async(_admitted_log_target)(app_slug, workload_slug=workload_slug, **proof)
        if resolved is None:
            return
        app, cluster, namespace, reviewed = resolved
        if app is None or cluster is None:
            return

        authority = (
            await sync_to_async(PreviewLogAuthority.capture)(reviewed, Permission.APP_READ_LOGS)
            if reviewed is not None
            else None
        )
        if authority is not None:
            current = await sync_to_async(authority.check)()
            cluster, namespace = current.app_environment.tenant_cluster, authority.target.namespace
            await sync_to_async(authority.check_pod)(
                pod_name, workload_slug=workload_slug, container=container
            )

        # Explicit iterate + finally so a consumer disconnect
        # (``aclose()`` on this generator throwing GeneratorExit at
        # the yield below) tears down the inner backend generator
        # before this frame unwinds. ``async for`` alone does NOT
        # call ``aclose()`` on its iterator under cancellation —
        # the inner gen would stay alive until GC, which means a
        # urllib3 connection leak on the production path.
        inner = stream_app_logs(
            cluster=cluster,
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
            **(
                {
                    "validate": authority.check,
                    "validate_pod": lambda name: authority.check_pod(
                        name, workload_slug=workload_slug, container=container
                    ),
                }
                if authority is not None
                else {}
            ),
        )
        if authority is not None:
            inner = guarded_log_lines(inner, authority)
        try:
            async for line in inner:
                yield AppLogLineType(
                    pod_name=line.pod_name,
                    container=line.container,
                    timestamp=line.timestamp,
                    message=line.message,
                    stream=line.stream,
                )
        finally:
            await inner.aclose()

    @strawberry.subscription
    @ws_identity
    @require_permission(
        Permission.APP_READ_LOGS,
        scope=live_app_scope("app_slug", permission=Permission.APP_READ_LOGS),
        operation=_log_operation(plural=True),
    )
    @tenant_scoped()
    async def astrolift_on_app_logs(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
        workload_slug: str | None = None,
        container: str | None = None,
        follow: bool = True,
        tail_lines: int = 100,
        preview_id: GUID | None = None,
        expected_environment_id: GUID | None = None,
        if_match_preview_version: int | None = None,
        if_match_environment_version: int | None = None,
    ) -> AsyncGenerator[AppLogLineType, None]:
        """Multi-pod plural log subscription (#482).

        Streams log lines from every replica of ``app_slug`` (filtered
        to ``workload_slug`` when given) over a single WebSocket. Each
        line carries its source ``pod_name`` so the FE can colorize +
        group per replica.

        New pods that appear during the subscription are auto-tailed.
        This is the mobile / aggregate-view answer to the question
        "show me what's happening across all replicas right now"
        without forcing the operator to know which pod to pick first.

        Permission gate, tenant-resolution, and cluster lookup mirror
        the per-pod ``astrolift_on_app_log`` subscription — same
        contract, just a different shape of inner stream.
        """
        from asgiref.sync import sync_to_async

        from core.cluster_observability import stream_app_logs_multi

        proof = {
            "preview_id": preview_id,
            "expected_environment_id": expected_environment_id,
            "if_match_preview_version": if_match_preview_version,
            "if_match_environment_version": if_match_environment_version,
        }
        resolved = await sync_to_async(_admitted_log_target)(
            app_slug, plural=True, environment_name=environment_name, workload_slug=workload_slug, **proof
        )
        if resolved is None:
            return
        app, cluster, namespace, reviewed = resolved

        authority = (
            await sync_to_async(PreviewLogAuthority.capture)(reviewed, Permission.APP_READ_LOGS)
            if reviewed is not None
            else None
        )
        if authority is not None:
            current = await sync_to_async(authority.check)()
            cluster, namespace = current.app_environment.tenant_cluster, authority.target.namespace

        inner = stream_app_logs_multi(
            cluster=cluster,
            namespace=namespace,
            app_slug=app.slug,
            workload_slug=workload_slug,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
            **(
                {
                    "validate": authority.check,
                    "validate_pod": lambda name: authority.check_pod(
                        name, workload_slug=workload_slug, container=container
                    ),
                }
                if authority is not None
                else {}
            ),
        )
        if authority is not None:
            inner = guarded_log_lines(inner, authority)
        try:
            async for line in inner:
                yield AppLogLineType(
                    pod_name=line.pod_name,
                    container=line.container,
                    timestamp=line.timestamp,
                    message=line.message,
                    stream=line.stream,
                )
        finally:
            await inner.aclose()
