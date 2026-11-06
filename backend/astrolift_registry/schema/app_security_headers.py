"""The per-app security headers switch, over GraphQL.

While on, the app's hosts answer with a fixed set of security headers added at
the ingress-nginx edge (``core.edge_security_headers``). Off by default. It
sits on the access tab and reads and writes through ``AstroliftAppAccess``.
"""

from __future__ import annotations

import functools
import inspect
import logging

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_registry.schema.app_access import AppAccessType, _app, _app_target, _to_type
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission

log = logging.getLogger(__name__)


@strawberry.input
class SetAppSecurityHeadersInput:
    app_slug: str
    enabled: bool


def _reapply_app(app, info) -> None:
    """Re-apply the app's environments so the headers land now, not on the next deploy.

    Best effort: the flag is saved, and the next deploy applies it if this cannot start.
    """
    from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
    from astrolift_lifecycle.reprovision import reprovision
    from astrolift_lifecycle.schema.mutations.helpers import _actor_from_request, _deploy_pipeline_disabled
    from astrolift_lifecycle.visibility import live_lifecycle_rows

    if _deploy_pipeline_disabled():
        return
    try:
        actor = _actor_from_request(info)
        names = [
            env.name
            for env in live_lifecycle_rows(AppEnvironment.objects.all())
            .filter(registered_app=app, previewed_environment__isnull=True)
            .order_by("name")
            if not PreviewEnvironment.all_objects.filter(app_environment=env).exists()
        ]
        for name in names:
            try:
                for result in reprovision(
                    app, environment_name=name, actor=actor, organization_id=app.organization_id
                ):
                    if result.error:
                        log.warning(
                            "security headers: re-apply of %s/%s failed: %s",
                            app.slug,
                            result.environment_name,
                            result.error,
                        )
            except Exception:  # noqa: BLE001 - the flag is saved; the next deploy applies it
                log.warning("security headers: could not re-apply %s/%s", app.slug, name, exc_info=True)
    except Exception:  # noqa: BLE001
        log.warning("security headers: could not re-apply %s", app.slug, exc_info=True)


def _audited_by_state(fn):
    """Audit as ``app.security_headers.enable`` or ``.disable`` by the requested state."""
    audited = {
        state: mutation_audit(action=f"app.security_headers.{state}", target=_app_target)(fn)
        for state in ("enable", "disable")
    }

    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        enabled = signature.bind(*args, **kwargs).arguments["input"].enabled
        return audited["enable" if enabled else "disable"](*args, **kwargs)

    wrapper.__signature__ = signature  # type: ignore[attr-defined]
    return wrapper


@strawberry.type
class AppSecurityHeadersMutation:
    @strawberry.field(
        description=(
            "Add a fixed set of security headers to every response from the app's hosts at the "
            "ingress-nginx edge, or stop (off by default). Needs app.access. Takes effect on the "
            "app's live environments without a deploy; other ingress classes do not honour it. "
            "Audited as app.security_headers.enable or app.security_headers.disable."
        )
    )
    @_audited_by_state
    @require_permission(
        Permission.APP_ACCESS, scope=app_scope_by_slug("input.app_slug", permission=Permission.APP_ACCESS)
    )
    @tenant_scoped()
    def set_app_security_headers(
        self, info: Info, input: SetAppSecurityHeadersInput
    ) -> MutationResultType[AppAccessType]:
        app = _app(input.app_slug)
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")
        if app.security_headers != input.enabled:
            app.security_headers = input.enabled
            app.save(update_fields=["security_headers", "updated_at", "version"])
            _reapply_app(app, info)
        return gql_success(_to_type(app))
