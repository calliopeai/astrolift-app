"""ExportMutations — split from the monolithic mutations module."""

from __future__ import annotations

import datetime as dt

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from astrolift_operations.models import AuditExport
from astrolift_operations.schema.audit_list import audit_events_qs
from astrolift_operations.schema.mutations.helpers import (
    _build_app_log_export_url,
    _build_audit_export_url,
    _materialize_app_log_lines,
)
from astrolift_operations.schema.mutations.types import (
    ExportAppLogsInput,
    ExportAuditEventsInput,
)
from astrolift_operations.schema.types import (
    AppLogExportType,
    AuditExportType,
    app_log_export_to_type,
    audit_export_to_type,
)
from astrolift_registry.scopes import app_scope_by_slug
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


def _filter_snapshot(filter_input) -> dict:
    """The export's ``filter`` input as JSON for ``filters_snapshot``."""
    from astrolift_graphql import filter_values

    return {
        key: value.isoformat() if isinstance(value, dt.datetime) else value
        for key, value in filter_values(filter_input).items()
    }


@strawberry.type
class ExportMutations:
    @strawberry.field
    @mutation_audit(action="audit_log.export")
    @require_permission(Permission.AUDIT_LOG_EXPORT)
    @tenant_scoped()
    def export_audit_events(
        self, info: Info, input: ExportAuditEventsInput
    ) -> MutationResultType[AuditExportType]:
        """Stream the matching audit slice into a token-gated download
        (#433). The mutation persists an :class:`AuditExport` row and
        returns the pre-signed URL + TTL + integrity hash. Honours the
        same filters as ``astroliftAuditEventsPage`` so the download
        matches what the operator sees on screen."""
        from datetime import timedelta

        from constance import config as constance_config
        from django.utils import timezone

        from astrolift_operations.audit_export import (
            hash_token,
            mint_token,
            write_artifact,
        )

        fmt = (input.format or "").strip().lower()
        if fmt not in {"csv", "ndjson"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "format must be CSV or NDJSON",
                field="format",
            )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        # Build the queryset under the exact same filter contract as
        # the page query. Order ascending so the export reads
        # naturally for an auditor (oldest -> newest).
        #
        # Scope the exported rows to the caller's org (#1183). The
        # AuditExport row below stamps organization=org, but WITHOUT
        # this clause on the source queryset the streamed artifact
        # contained every tenant's audit trail (bulk cross-org PII
        # exfil). org_id is non-None here (guarded above). Mirrors the
        # org scope on astroliftAuditEventsPage.
        qs = audit_events_qs(
            org_id,
            action=input.action,
            decision=input.decision,
            actor_id=input.actor_id,
            created_at_gte=input.created_at_gte,
            created_at_lte=input.created_at_lte,
            search=input.search,
            target_kind=input.target_kind,
            target_id=input.target_id,
            subject_user_id=input.subject_user_id,
            filter=input.filter,
            viewer_id=tenant.actor_user_id if tenant else None,
        ).order_by("occurred_at", "guid")

        max_rows = max(1, int(getattr(constance_config, "AUDIT_EXPORT_MAX_ROWS", 100000)))
        candidate_count = qs.count()
        if candidate_count > max_rows:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"export would produce {candidate_count} rows, exceeding the "
                    f"AUDIT_EXPORT_MAX_ROWS cap ({max_rows}); narrow the date range "
                    "or actor/action filter and retry"
                ),
            )

        # UUIDv7 default fills in on save, but the artifact filename
        # needs the guid before we hit the DB — generate explicitly so
        # the file path + DB row agree.
        from core.fields.uuid_v7 import uuid7

        export_guid = uuid7()

        artifact = write_artifact(
            qs.iterator(chunk_size=500),
            format=fmt,
            guid=str(export_guid),
        )

        plaintext_token, token_hash = mint_token()
        ttl_seconds = max(
            60,
            int(getattr(constance_config, "AUDIT_EXPORT_DOWNLOAD_TTL_SECONDS", 3600)),
        )
        expires_at = timezone.now() + timedelta(seconds=ttl_seconds)

        actor_user_id = tenant.actor_user_id if tenant else None
        from django.contrib.auth import get_user_model

        requested_by = None
        if actor_user_id is not None:
            requested_by = get_user_model().objects.filter(pk=actor_user_id).first()

        export = AuditExport.objects.create(
            guid=export_guid,
            organization=org,
            requested_by=requested_by,
            format=fmt,
            row_count=artifact.row_count,
            byte_count=artifact.byte_count,
            sha256=artifact.sha256,
            relative_path=artifact.relative_path,
            token_hash=token_hash,
            filters_snapshot={
                "action": input.action or "",
                "decision": (input.decision or "").upper() or "",
                "actor_id": input.actor_id or "",
                "created_at_gte": (input.created_at_gte.isoformat() if input.created_at_gte else ""),
                "created_at_lte": (input.created_at_lte.isoformat() if input.created_at_lte else ""),
                "search": input.search or "",
                "target_kind": input.target_kind or "",
                "target_id": input.target_id or "",
                "subject_user_id": input.subject_user_id or "",
                "filter": _filter_snapshot(input.filter),
            },
            expires_at=expires_at,
        )
        # ``token_hash`` here is computed from the plaintext so a later
        # download-view check matches.
        assert export.token_hash == hash_token(plaintext_token)

        download_url = _build_audit_export_url(
            info=info,
            guid=str(export.guid),
            token=plaintext_token,
        )
        return gql_success(audit_export_to_type(export, download_url=download_url))

    @strawberry.field
    @mutation_audit(action="app.log_export")
    @require_permission(Permission.APP_LOG_EXPORT, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def export_astrolift_app_logs(
        self, info: Info, input: ExportAppLogsInput
    ) -> MutationResultType[AppLogExportType]:
        """Stream the matching app-log slice into a token-gated
        download (#483). Same artifact + single-use token shape as
        ``exportAuditEvents``; pulls runtime container logs through
        the cluster ``ClusterDriver`` (the same path the ``onAppLog``
        subscription uses) so what the operator sees on screen is
        what lands in the file."""
        import re

        from constance import config as constance_config
        from django.utils import timezone

        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_operations import app_log_export as app_log_helpers
        from astrolift_operations.models import AppLogExport
        from astrolift_registry.models import RegisteredApp
        from core.cluster_observability import (
            ClusterObservabilityError,
            namespace_for_app,
            namespace_for_environment,
        )

        fmt = (input.format or "").strip().lower()
        if fmt not in {"csv", "ndjson", "txt"}:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "format must be CSV, NDJSON, or TXT",
                field="format",
            )

        if not (input.app_slug or "").strip():
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "appSlug is required",
                field="appSlug",
            )

        if not (input.pod_name or "").strip():
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "podName is required",
                field="podName",
            )

        if input.regex:
            try:
                re.compile(input.regex)
            except re.error as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"invalid regex: {exc}",
                    field="regex",
                )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        app = (
            RegisteredApp.objects.select_related("organization", "default_tenant_cluster")
            .filter(
                slug=input.app_slug,
                organization_id=org_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if app is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"app {input.app_slug!r} not found",
                field="appSlug",
            )

        # Environment lookup: when the operator pinned a name, that
        # env's cluster wins; otherwise we fall back to the app's
        # default cluster (matches the subscription's resolution
        # contract). An unknown env name fails loudly so a typo
        # doesn't silently land logs from the wrong cluster.
        cluster = None
        namespace = namespace_for_app(app)
        if input.environment_name:
            env = (
                AppEnvironment.objects.select_related("tenant_cluster")
                .filter(
                    registered_app=app,
                    name=input.environment_name,
                    deleted_at__isnull=True,
                )
                .first()
            )
            if env is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"environment {input.environment_name!r} not found on app {app.slug!r}",
                    field="environmentName",
                )
            cluster = env.tenant_cluster
            # The pinned environment's own namespace when it has one (#1922).
            namespace = namespace_for_environment(env)
        if cluster is None:
            cluster = app.default_tenant_cluster
        if cluster is None or not getattr(cluster, "is_active", True):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"app {app.slug!r} has no active cluster wired",
            )

        max_lines = max(
            1,
            int(getattr(constance_config, "APP_LOG_EXPORT_MAX_LINES", 100000)),
        )

        # Pull from the cluster driver with ``follow=False`` so the
        # generator terminates at the current tail. ``tail_lines``
        # is capped at the configured max so the driver doesn't ship
        # us more than the export will ever serialize.
        try:
            line_source = _materialize_app_log_lines(
                cluster=cluster,
                namespace=namespace,
                pod_name=input.pod_name,
                container=input.container,
                tail_lines=max_lines,
                since=input.since,
                until=input.until,
            )
        except ClusterObservabilityError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"cluster log backend unavailable: {exc}",
            )

        from core.fields.uuid_v7 import uuid7

        export_guid = uuid7()

        try:
            artifact = app_log_helpers.write_artifact(
                line_source,
                format=fmt,
                guid=str(export_guid),
                max_lines=max_lines,
                level=input.level,
                regex=input.regex,
            )
        except ValueError as exc:
            return gql_failure(ErrorCode.VALIDATION.value, str(exc), field="format")

        plaintext_token, token_hash = app_log_helpers.mint_token()
        ttl_seconds = max(
            60,
            int(getattr(constance_config, "APP_LOG_EXPORT_DOWNLOAD_TTL_SECONDS", 3600)),
        )
        expires_at = timezone.now() + dt.timedelta(seconds=ttl_seconds)

        actor_user_id = tenant.actor_user_id if tenant else None
        from django.contrib.auth import get_user_model

        requested_by = None
        if actor_user_id is not None:
            requested_by = get_user_model().objects.filter(pk=actor_user_id).first()

        export = AppLogExport.objects.create(
            guid=export_guid,
            organization=org,
            registered_app=app,
            environment_name=input.environment_name or "",
            pod_name=input.pod_name or "",
            workload_name=input.workload_slug or "",
            container=input.container or "",
            requested_by=requested_by,
            format=fmt,
            status=AppLogExport.Status.READY,
            row_count=artifact.row_count,
            byte_count=artifact.byte_count,
            sha256=artifact.sha256,
            relative_path=artifact.relative_path,
            token_hash=token_hash,
            filters_snapshot={
                "since": (input.since.isoformat() if input.since else ""),
                "until": (input.until.isoformat() if input.until else ""),
                "level": input.level or "",
                "regex": input.regex or "",
                "container": input.container or "",
                "workload_slug": input.workload_slug or "",
                "truncated": artifact.truncated,
            },
            expires_at=expires_at,
        )
        assert export.token_hash == app_log_helpers.hash_token(plaintext_token)

        download_url = _build_app_log_export_url(
            info=info,
            guid=str(export.guid),
            token=plaintext_token,
        )
        return gql_success(app_log_export_to_type(export, download_url=download_url))
