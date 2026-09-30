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

from astrolift_lifecycle.schema.types import AppLogLineType
from astrolift_lifecycle.scopes import live_app_scope
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.schema.ws_auth import ws_identity


@strawberry.type
class LifecycleSubscription:
    @strawberry.subscription
    @ws_identity
    @require_permission(Permission.APP_READ_LOGS, scope=live_app_scope("app_slug"))
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

        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_registry.models import RegisteredApp
        from core.cluster_observability import stream_app_logs
        from core.tenancy import get_current_tenant

        org_id = get_current_tenant().organization_id

        # Look up app + cluster off the main thread — Django ORM
        # is sync.
        def _resolve():
            app = (
                RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
                .filter(
                    slug=app_slug,
                    organization_id=org_id,
                    deleted_at__isnull=True,
                )
                .first()
            )
            if app is None:
                return None, None, None
            cluster = None
            if workload_slug:
                # workload_slug is informational — the pod name
                # already encodes the workload — but we use it as
                # a hint to pick the right environment cluster
                # when an app has more than one (prefer the env
                # whose workload matches).
                env = (
                    AppEnvironment.objects.select_related("tenant_cluster")
                    .filter(
                        registered_app=app,
                        deleted_at__isnull=True,
                    )
                    .order_by("created_at")
                    .first()
                )
                if env and env.tenant_cluster_id:
                    cluster = env.tenant_cluster
            if cluster is None:
                cluster = app.default_tenant_cluster
            if cluster is None or not getattr(cluster, "is_active", True):
                return None, None, None
            from core.cluster_observability import namespace_for_app

            namespace = namespace_for_app(app)
            return app, cluster, namespace

        resolved = await sync_to_async(_resolve)()
        if resolved is None:
            return
        app, cluster, namespace = resolved
        if app is None or cluster is None:
            return

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
        )
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
    @require_permission(Permission.APP_READ_LOGS, scope=live_app_scope("app_slug"))
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

        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_registry.models import RegisteredApp
        from core.cluster_observability import (
            namespace_for_app,
            namespace_for_environment,
            stream_app_logs_multi,
        )
        from core.tenancy import get_current_tenant

        org_id = get_current_tenant().organization_id

        def _resolve():
            app = (
                RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
                .filter(
                    slug=app_slug,
                    organization_id=org_id,
                    deleted_at__isnull=True,
                )
                .first()
            )
            if app is None:
                return None
            cluster = None
            namespace = namespace_for_app(app)
            if environment_name:
                env = (
                    AppEnvironment.objects.select_related("tenant_cluster")
                    .filter(
                        registered_app=app,
                        name=environment_name,
                        deleted_at__isnull=True,
                    )
                    .first()
                )
                if env and env.tenant_cluster_id:
                    cluster = env.tenant_cluster
                    # The named environment's own namespace when it has
                    # one (#1922).
                    namespace = namespace_for_environment(env)
            if cluster is None:
                cluster = app.default_tenant_cluster
            if cluster is None or not getattr(cluster, "is_active", True):
                return None
            return app, cluster, namespace

        resolved = await sync_to_async(_resolve)()
        if resolved is None:
            return
        app, cluster, namespace = resolved

        inner = stream_app_logs_multi(
            cluster=cluster,
            namespace=namespace,
            app_slug=app.slug,
            workload_slug=workload_slug,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        )
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
