"""Lifecycle GraphQL subscriptions — currently just ``onAppLog``.

Pattern mirrors ``core.schema.subscriptions.Subscription``:
- ``@strawberry.subscription`` async generator
- Reads the WS-resolved tenant from ``info.context._ws_tenant``,
  pins it on the contextvar so ``@tenant_scoped`` + the cluster
  resolution code see the right org.
- Permission check (``Permission.APP_READ_LOGS``) happens
  *inside* the generator body — strawberry doesn't run decorator
  chains across async-generator boundaries the way it does for
  fields, so we call ``check_permission`` ourselves.

The actual log byte stream lives in :mod:`core.k8s.logs`; this
module is the wire mapping + tenant + permission glue.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import strawberry
from strawberry.types import Info

from astrolift_lifecycle.schema.types import AppLogLineType


@strawberry.type
class LifecycleSubscription:
    @strawberry.subscription
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
          - APP_READ_LOGS isn't granted for the tenant
          - the app has no cluster wired
          - the backend signals EOF

        On consumer disconnect, the generator is cancelled and the
        backend's ``CancelledError`` path runs — the kubernetes
        urllib3 connection is released back to the pool."""
        from asgiref.sync import sync_to_async

        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_registry.models import RegisteredApp
        from core.k8s.logs import stream_app_logs
        from core.permissions import (
            Permission,
            PermissionDenied,
            check_permission,
        )
        from core.tenancy import (
            TenantContext,
            get_current_tenant,
            set_current_tenant,
        )

        # The WS handshake stashed the resolved tenant on the
        # context object. Pin it on the contextvar so the rest of
        # the tenant-aware code (managers, permission resolver)
        # sees it.
        ws_tenant: TenantContext | None = getattr(info.context, "_ws_tenant", None)
        if ws_tenant is not None:
            set_current_tenant(ws_tenant)

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return

        try:
            check_permission(Permission.APP_READ_LOGS)
        except PermissionDenied:
            return

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
            from core.k8s.client import namespace_for_app

            namespace = namespace_for_app(app)
            return app, cluster, namespace

        resolved = await sync_to_async(_resolve)()
        if resolved is None:
            return
        app, cluster, namespace = resolved
        if app is None or cluster is None:
            return

        async for line in stream_app_logs(
            cluster=cluster,
            namespace=namespace,
            pod_name=pod_name,
            container=container,
            tail_lines=tail_lines,
            follow=follow,
        ):
            yield AppLogLineType(
                pod_name=line.pod_name,
                container=line.container,
                timestamp=line.timestamp,
                message=line.message,
                stream=line.stream,
            )
