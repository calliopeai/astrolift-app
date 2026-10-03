"""Exact preview log admission and renewable, credential-bound read authority.

Only immutable identity/version metadata is retained. Provider messages, tokens
and request bodies never become part of the source receipt or diagnostics.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from collections.abc import AsyncIterator

from graphql import GraphQLError

from astrolift_identity.abac import (
    RequestAttributes,
    current_attributes,
    operation_attributes,
    request_attributes,
)
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    session_may_act_in,
    set_current_api_token,
    with_active_org_member,
)
from astrolift_identity.operation_context import environment_context
from astrolift_lifecycle.preview_targets import PreviewTarget, check_preview_target
from astrolift_registry.scopes import app_scope_by_slug
from core.permissions import Permission, PermissionDenied, check_permission
from core.tenancy import TenantContext, get_current_tenant, tenant_context

GUARD_INTERVAL_SECONDS = 1.0
EXPORT_TIMEOUT_SECONDS = 30.0
TARGET_ERROR = "The reviewed preview environment is unavailable or has changed"
ACCESS_ERROR = "Preview log access is no longer available"


def refuse_target():
    raise GraphQLError(TARGET_ERROR, extensions={"code": "PRECONDITION"})


def resolve_preview_log_target(
    app_slug,
    *,
    permission,
    preview_id=None,
    expected_environment_id=None,
    if_match_preview_version=None,
    if_match_environment_version=None,
    environment_name=None,
):
    """Return the exact FK binding, or None for an ordinary legacy request.

    A name identifying an actual preview is never enough to authorize logs.
    The same guard also rejects single-pod legacy routing into preview namespaces.
    """
    from astrolift_lifecycle.models import PreviewEnvironment
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.scopes import live_app_owners
    from astrolift_registry.visibility import visible_registry_apps

    tenant = get_current_tenant()
    if preview_id is not None or expected_environment_id is not None:
        import uuid

        try:
            if preview_id is not None:
                uuid.UUID(str(preview_id))
            if expected_environment_id is not None:
                uuid.UUID(str(expected_environment_id))
        except (ValueError, AttributeError, TypeError):
            refuse_target()
    proof = (preview_id, expected_environment_id, if_match_preview_version, if_match_environment_version)
    if not any(value is not None for value in proof):
        if (
            environment_name
            and PreviewEnvironment.all_objects.filter(
                registered_app__organization_id=tenant.organization_id if tenant else None,
                registered_app__slug=app_slug,
                app_environment__name=environment_name,
            ).exists()
        ):
            refuse_target()
        return None
    if any(value is None for value in proof):
        refuse_target()
    preview = (
        PreviewEnvironment.objects.select_related(
            "registered_app__organization", "app_environment__tenant_cluster"
        )
        .filter(
            deleted_at__isnull=True,
            registered_app__in=visible_registry_apps(
                live_app_owners(RegisteredApp.objects.filter(slug=app_slug)), permission
            ),
        )
        .filter(
            guid=preview_id,
            registered_app__slug=app_slug,
            registered_app__organization_id=tenant.organization_id if tenant else None,
        )
        .first()
    )
    if preview is None:
        refuse_target()
    try:
        target = check_preview_target(
            preview,
            environment_id=expected_environment_id,
            preview_version=if_match_preview_version,
            environment_version=if_match_environment_version,
        )
    except ValueError:
        refuse_target()
    if environment_name is not None and environment_name != target.environment_name:
        refuse_target()
    return preview, target


def refuse_unreviewed_preview_route(app, cluster, namespace):
    from astrolift_lifecycle.models import PreviewEnvironment

    if PreviewEnvironment.all_objects.filter(
        registered_app_id=app.pk,
        app_environment__tenant_cluster_id=cluster.pk,
        app_environment__k8s_namespace=namespace,
    ).exists():
        refuse_target()


@dataclasses.dataclass(frozen=True)
class PreviewLogAuthority:
    tenant: TenantContext
    token_id: int | None
    attributes: RequestAttributes
    target: PreviewTarget
    permission: Permission
    token_team_id: int | None = None
    token_scopes: tuple[str, ...] = ()

    @classmethod
    def capture(cls, target, permission):
        tenant = get_current_tenant()
        if tenant is None or tenant.actor_user_id is None:
            raise GraphQLError(ACCESS_ERROR, extensions={"code": "PERMISSION_DENIED"})
        token = get_current_api_token()
        authority = cls(
            tenant,
            token.pk if token is not None else None,
            current_attributes() or RequestAttributes(actor_user_id=tenant.actor_user_id),
            target,
            permission,
            token.team_id if token is not None else None,
            tuple(token.scopes or []) if token is not None else (),
        )
        authority.check()
        return authority

    def check(self):
        """Re-resolve credentials, membership, grants and exact source each time."""
        from django.contrib.auth import get_user_model
        from django.db.models import Q
        from django.utils import timezone

        from astrolift_identity.models import ApiToken

        user = get_user_model().objects.filter(pk=self.tenant.actor_user_id, is_active=True).first()
        if user is None or not session_may_act_in(user, self.tenant.organization_id):
            raise GraphQLError(ACCESS_ERROR, extensions={"code": "PERMISSION_DENIED"})
        token = None
        if self.token_id is not None:
            token = with_active_org_member(
                ApiToken.objects.filter(
                    Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()),
                    pk=self.token_id,
                    organization_id=self.tenant.organization_id,
                    user_id=self.tenant.actor_user_id,
                    is_revoked=False,
                    deleted_at__isnull=True,
                ),
                user="user",
                organization="organization",
            ).first()
            from types import SimpleNamespace

            from astrolift_identity.api_tokens import token_scope_allows_permission

            if (
                token is None
                or token.team_id != self.token_team_id
                or not token_scope_allows_permission(
                    SimpleNamespace(scopes=self.token_scopes), self.permission.value
                )
            ):
                raise GraphQLError(ACCESS_ERROR, extensions={"code": "PERMISSION_DENIED"})
        marker = set_current_api_token(token)
        try:
            with (
                tenant_context(self.tenant),
                request_attributes(dataclasses.replace(self.attributes, cache={}, now=None)),
            ):
                resolved = resolve_preview_log_target(
                    self.target.app_slug,
                    permission=self.permission,
                    preview_id=self.target.preview_id,
                    expected_environment_id=self.target.environment_id,
                    if_match_preview_version=self.target.preview_version,
                    if_match_environment_version=self.target.environment_version,
                )
                if resolved is None or resolved[1] != self.target:
                    refuse_target()
                preview, _target = resolved
                with operation_attributes(**environment_context(preview.app_environment).attributes()):
                    scope = app_scope_by_slug("app_slug", permission=self.permission)(
                        {"app_slug": self.target.app_slug}
                    )
                    check_permission(self.permission, scope=scope)
                return preview
        except PermissionDenied:
            raise GraphQLError(ACCESS_ERROR, extensions={"code": "PERMISSION_DENIED"}) from None
        finally:
            reset_current_api_token(marker)

    def check_pod(self, pod_name, *, workload_slug=None, container=None):
        from core.cluster_observability import list_app_pods

        preview = self.check()
        try:
            pods = list_app_pods(
                cluster=preview.app_environment.tenant_cluster,
                namespace=self.target.namespace,
                app_slug=self.target.app_slug,
            )
        except Exception:
            raise GraphQLError(
                "Preview log backend unavailable", extensions={"code": "PRECONDITION"}
            ) from None
        pod = next((pod for pod in pods if pod.name == pod_name), None)
        if pod is None or (workload_slug and pod.workload != workload_slug):
            refuse_target()
        if container and not any(item.name == container for item in pod.container_statuses):
            refuse_target()
        self.check()

    def snapshot(self):
        return {
            "kind": "preview_logs_v1",
            "target": dataclasses.asdict(self.target),
            "tenant": dataclasses.asdict(self.tenant),
            "token_id": self.token_id,
            "token_team_id": self.token_team_id,
            "token_scopes": list(self.token_scopes),
        }


async def guarded_log_lines(inner, authority: PreviewLogAuthority) -> AsyncIterator:
    """Revalidate even while an iterator is idle, and close on refusal/cancel."""
    from asgiref.sync import sync_to_async

    pending = None
    try:
        await sync_to_async(authority.check, thread_sensitive=True)()
        while True:
            pending = asyncio.create_task(anext(inner))
            while not pending.done():
                await asyncio.wait({pending}, timeout=GUARD_INTERVAL_SECONDS)
                await sync_to_async(authority.check, thread_sensitive=True)()
            completed, pending = pending, None
            try:
                line = completed.result()
            except StopAsyncIteration:
                return
            except GraphQLError:
                raise
            except Exception:
                raise GraphQLError(
                    "Preview log backend unavailable", extensions={"code": "PRECONDITION"}
                ) from None
            await sync_to_async(authority.check, thread_sensitive=True)()
            yield line
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pending
        with contextlib.suppress(Exception):
            await inner.aclose()
