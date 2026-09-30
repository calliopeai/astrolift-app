"""BulkOpsMutations — split from the monolithic mutations module."""

from __future__ import annotations

import functools
import inspect

import strawberry
from strawberry.types import Info

from astrolift_identity.operation_context import environment_operation, named_environment, workload_operation
from astrolift_manifest.persist import actor_may_attach_project_services, allow_project_attach
from astrolift_operations.schema.mutations.helpers import (
    _BULK_APP_CAP,
)
from astrolift_operations.schema.mutations.types import (
    BulkAppResultItem,
    BulkOperationResult,
    BulkPushSecretsInput,
    BulkResyncManifestInput,
    BulkRollingRestartInput,
)
from astrolift_operations.scopes import org_id
from astrolift_registry.scopes import app_scope_by_guid, app_scope_by_workload_guid, live_app_owners
from astrolift_services.scopes import (
    assert_provider_cluster,
    live_secret_bundles,
    secret_bundle_project_scope,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, MutationError, mutation_audit
from core.permissions import Permission, PermissionDenied, require_permission
from core.tenancy import get_current_tenant


class _BulkPermissionRefusal(BulkOperationResult):
    @property
    def ok(self):
        return False

    @property
    def errors(self):
        message = self.per_app[0].errors[0] if self.per_app else "permission denied"
        return [MutationError(code=ErrorCode.PERMISSION_DENIED, message=message)]


def _bulk_permission_result(fn):
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PermissionDenied as exc:
            bound = signature.bind(*args, **kwargs)
            slugs = list(dict.fromkeys(bound.arguments["input"].app_slugs or []))[:_BULK_APP_CAP]
            return _BulkPermissionRefusal(
                ok_count=0,
                failed_count=len(slugs),
                per_app=[BulkAppResultItem(app_slug=slug, ok=False, errors=[str(exc)]) for slug in slugs],
            )

    return wrapped


@require_permission(
    Permission.APP_DEPLOY,
    scope=app_scope_by_workload_guid("workload_id", permission=Permission.APP_DEPLOY),
    operation=workload_operation("workload_id"),
)
def _restart_workload(workload_id):
    from astrolift_lifecycle.services.k8s_ops import (
        _primary_environment_for_workload,
        rollout_restart_workload,
    )
    from astrolift_registry.models import Workload

    workload = Workload.objects.select_related("registered_app").get(
        guid=workload_id, registered_app__organization_id=org_id()
    )
    env = _primary_environment_for_workload(workload)
    if env is not None:
        assert_provider_cluster(env.tenant_cluster, permission=Permission.APP_DEPLOY)
    return rollout_restart_workload(workload)


@require_permission(
    Permission.APP_UPDATE, scope=secret_bundle_project_scope(permissions=(Permission.APP_UPDATE,))
)
def _authorize_bundle(bundle_id):
    return None


@require_permission(
    Permission.APP_UPDATE,
    scope=app_scope_by_guid(permission=Permission.APP_UPDATE),
    operation=environment_operation("environment_id"),
)
def _attach_bundle(app_id, environment_id, bundle):
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.models.secret_bundle import AppSecretBundleRef

    _authorize_bundle(bundle.guid)
    env = AppEnvironment.objects.get(
        guid=environment_id, registered_app__guid=app_id, registered_app__organization_id=org_id()
    )
    AppSecretBundleRef.objects.get_or_create(
        registered_app=env.registered_app,
        app_environment=env,
        secret_bundle=bundle,
        deleted_at__isnull=True,
    )


@require_permission(
    Permission.APP_UPDATE,
    scope=app_scope_by_guid(permission=Permission.APP_UPDATE),
    operation=named_environment("app_slug", "_absent", all_if_absent=True),
)
def _resync_manifest(app_id, app_slug):
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.services.manifest_sync import resync_app_manifest_from_repo

    app = RegisteredApp.objects.get(guid=app_id, slug=app_slug, organization_id=org_id())
    with allow_project_attach(actor_may_attach_project_services(app)):
        return resync_app_manifest_from_repo(app)


@strawberry.type
class BulkOpsMutations:
    # ---- Bulk app operations (#746) --------------------------------

    @strawberry.field
    @mutation_audit(action="app.bulk.rolling_restart")
    @_bulk_permission_result
    @require_permission(Permission.APP_DEPLOY, any_scope=True)
    @tenant_scoped()
    def bulk_rolling_restart(
        self,
        info: Info,
        input: BulkRollingRestartInput,
    ) -> BulkOperationResult:
        """Rolling-restart all workloads across a selection of apps (#746).

        Fans out ``rollout_restart_workload`` per workload per app. Capped
        at ``_BULK_APP_CAP`` apps per call. Per-app failures are collected
        rather than short-circuiting so the FE can render a
        success+failure summary in one toast."""
        from astrolift_lifecycle.services.k8s_ops import (
            K8sOpError,
        )
        from astrolift_registry.models import RegisteredApp, Workload

        slugs = list(dict.fromkeys(input.app_slugs or []))[:_BULK_APP_CAP]
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Fail closed without a tenant org (#1192): the per-slug app lookup is
        # org-scoped, so a None org must not fall through to an unscoped
        # by-slug fetch. Mirrors the fail-closed bulk_push_secrets guard.
        if org_id is None:
            return BulkOperationResult(
                ok_count=0,
                failed_count=len(slugs),
                per_app=[
                    BulkAppResultItem(app_slug=s, ok=False, errors=["no active organization"]) for s in slugs
                ],
            )

        per_app: list[BulkAppResultItem] = []
        for slug in slugs:
            app = live_app_owners(
                RegisteredApp.objects.filter(
                    slug=slug,
                    organization_id=org_id,
                    deleted_at__isnull=True,
                )
            ).first()
            if app is None:
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=["app not found"]))
                continue

            workload_qs = Workload.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
            )

            workloads = list(workload_qs.select_related("registered_app"))
            if not workloads:
                per_app.append(
                    BulkAppResultItem(app_slug=slug, ok=False, errors=["no active workloads found"])
                )
                continue

            errors: list[str] = []
            for wl in workloads:
                try:
                    _restart_workload(wl.guid)
                except K8sOpError as exc:
                    errors.append(f"{wl.slug}: {exc.message}")
                except PermissionDenied as exc:
                    errors.append(f"{wl.slug}: {exc}")

            per_app.append(BulkAppResultItem(app_slug=slug, ok=not errors, errors=errors))

        ok_count = sum(1 for r in per_app if r.ok)
        return BulkOperationResult(
            ok_count=ok_count,
            failed_count=len(per_app) - ok_count,
            per_app=per_app,
        )

    @strawberry.field
    @mutation_audit(action="app.bulk.push_secrets")
    @_bulk_permission_result
    @require_permission(Permission.APP_UPDATE, any_scope=True)
    @tenant_scoped()
    def bulk_push_secrets(
        self,
        info: Info,
        input: BulkPushSecretsInput,
    ) -> BulkOperationResult:
        """Attach a shared ``SecretBundle`` to a selection of apps (#746).

        For each app, looks up the target ``AppEnvironment`` by name (uses
        the first active environment when ``environmentName`` is null).
        If the bundle is already attached to that environment's ref, the
        existing row is left in place (idempotent). The bundle's key-value
        pairs are injected on the next deploy or manifest reconcile."""
        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_registry.models import RegisteredApp
        from astrolift_services.models.secret_bundle import SecretBundle

        slugs = list(dict.fromkeys(input.app_slugs or []))[:_BULK_APP_CAP]
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Deny-by-default without a tenant context (#1183): every lookup
        # below is org-scoped, so a None org must fail closed rather than
        # match NULL-org / cross-org rows.
        if org_id is None:
            return BulkOperationResult(
                ok_count=0,
                failed_count=len(slugs),
                per_app=[
                    BulkAppResultItem(app_slug=s, ok=False, errors=["no active organization"]) for s in slugs
                ],
            )

        # Scope the bundle to the caller's org (#1183): SecretBundle owns
        # an organization FK. An unscoped slug lookup let a caller attach
        # ANOTHER tenant's secret bundle to their own apps — cross-org
        # secret injection on the next deploy / manifest reconcile.
        bundle = live_secret_bundles(
            SecretBundle.objects.filter(
                slug=input.bundle_slug,
                organization_id=org_id,
                deleted_at__isnull=True,
            )
        ).first()
        if bundle is None:
            return BulkOperationResult(
                ok_count=0,
                failed_count=len(slugs),
                per_app=[
                    BulkAppResultItem(
                        app_slug=s,
                        ok=False,
                        errors=[f"bundle {input.bundle_slug!r} not found"],
                    )
                    for s in slugs
                ],
            )

        per_app: list[BulkAppResultItem] = []
        for slug in slugs:
            app = live_app_owners(
                RegisteredApp.objects.filter(
                    slug=slug,
                    organization_id=org_id,
                    deleted_at__isnull=True,
                )
            ).first()
            if app is None:
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=["app not found"]))
                continue

            env_qs = AppEnvironment.objects.filter(
                registered_app=app,
                deleted_at__isnull=True,
            )
            if input.environment_name:
                env_qs = env_qs.filter(name=input.environment_name)
            env = env_qs.order_by("id").first()
            if env is None:
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=["no matching environment"]))
                continue

            try:
                _attach_bundle(app.guid, env.guid, bundle)
            except Exception as exc:  # noqa: BLE001
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=[str(exc)[:256]]))
                continue

            per_app.append(BulkAppResultItem(app_slug=slug, ok=True, errors=[]))

        ok_count = sum(1 for r in per_app if r.ok)
        return BulkOperationResult(
            ok_count=ok_count,
            failed_count=len(per_app) - ok_count,
            per_app=per_app,
        )

    @strawberry.field
    @mutation_audit(action="app.bulk.resync_manifest")
    @_bulk_permission_result
    @require_permission(Permission.APP_UPDATE, any_scope=True)
    @tenant_scoped()
    def bulk_resync_manifest(
        self,
        info: Info,
        input: BulkResyncManifestInput,
    ) -> BulkOperationResult:
        """Re-fetch and apply the manifest from the source repo for a
        selection of apps (#746). Fans out
        ``resync_app_manifest_from_repo`` per app. Per-app failures are
        collected so the FE can render a summary."""
        from astrolift_registry.models import RegisteredApp

        slugs = list(dict.fromkeys(input.app_slugs or []))[:_BULK_APP_CAP]
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Fail closed without a tenant org (#1192): the per-slug app lookup is
        # org-scoped, so a None org must not fall through to an unscoped
        # by-slug fetch. Mirrors the fail-closed bulk_push_secrets guard.
        if org_id is None:
            return BulkOperationResult(
                ok_count=0,
                failed_count=len(slugs),
                per_app=[
                    BulkAppResultItem(app_slug=s, ok=False, errors=["no active organization"]) for s in slugs
                ],
            )

        per_app: list[BulkAppResultItem] = []
        for slug in slugs:
            app = live_app_owners(
                RegisteredApp.objects.filter(
                    slug=slug,
                    organization_id=org_id,
                    deleted_at__isnull=True,
                )
            ).first()
            if app is None:
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=["app not found"]))
                continue

            try:
                result = _resync_manifest(app.guid, app.slug)
            except Exception as exc:  # noqa: BLE001
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=[str(exc)[:256]]))
                continue

            if result.status in ("applied", "in_sync"):
                per_app.append(BulkAppResultItem(app_slug=slug, ok=True, errors=[]))
            else:
                per_app.append(
                    BulkAppResultItem(
                        app_slug=slug,
                        ok=False,
                        errors=[result.error or f"sync status: {result.status}"],
                    )
                )

        ok_count = sum(1 for r in per_app if r.ok)
        return BulkOperationResult(
            ok_count=ok_count,
            failed_count=len(per_app) - ok_count,
            per_app=per_app,
        )
