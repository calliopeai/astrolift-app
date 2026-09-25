"""BulkOpsMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

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
from core.decorators import tenant_scoped
from core.mutations import mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class BulkOpsMutations:
    # ---- Bulk app operations (#746) --------------------------------

    @strawberry.field
    @mutation_audit(action="app.bulk.rolling_restart")
    @require_permission(Permission.APP_DEPLOY)
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
            rollout_restart_workload,
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
            app = RegisteredApp.objects.filter(
                slug=slug,
                organization_id=org_id,
                deleted_at__isnull=True,
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
                    rollout_restart_workload(wl)
                except K8sOpError as exc:
                    errors.append(f"{wl.slug}: {exc.message}")

            per_app.append(BulkAppResultItem(app_slug=slug, ok=not errors, errors=errors))

        ok_count = sum(1 for r in per_app if r.ok)
        return BulkOperationResult(
            ok_count=ok_count,
            failed_count=len(per_app) - ok_count,
            per_app=per_app,
        )

    @strawberry.field
    @mutation_audit(action="app.bulk.push_secrets")
    @require_permission(Permission.APP_UPDATE)
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
        from astrolift_services.models.secret_bundle import AppSecretBundleRef, SecretBundle

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
        bundle = SecretBundle.objects.filter(
            slug=input.bundle_slug,
            organization_id=org_id,
            deleted_at__isnull=True,
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
            app = RegisteredApp.objects.filter(
                slug=slug,
                organization_id=org_id,
                deleted_at__isnull=True,
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

            exists = AppSecretBundleRef.objects.filter(
                registered_app=app,
                app_environment=env,
                secret_bundle=bundle,
                deleted_at__isnull=True,
            ).exists()
            if not exists:
                try:
                    AppSecretBundleRef.objects.create(
                        registered_app=app,
                        app_environment=env,
                        secret_bundle=bundle,
                    )
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
    @require_permission(Permission.APP_UPDATE)
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
        from astrolift_registry.services.manifest_sync import resync_app_manifest_from_repo

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
            app = RegisteredApp.objects.filter(
                slug=slug,
                organization_id=org_id,
                deleted_at__isnull=True,
            ).first()
            if app is None:
                per_app.append(BulkAppResultItem(app_slug=slug, ok=False, errors=["app not found"]))
                continue

            try:
                with allow_project_attach(actor_may_attach_project_services(app)):
                    result = resync_app_manifest_from_repo(app)
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
