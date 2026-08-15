"""GraphQL queries for app secrets, secret bundles, managed services."""

from __future__ import annotations

import strawberry
from django.db.models import Q
from strawberry.types import Info

from astrolift_clusters.models import TenantCluster
from astrolift_clusters.schema.types import TenantClusterType, cluster_to_type
from astrolift_graphql import GUID, PageType, keyset_page, search_q
from astrolift_identity.models import Project
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.models.preview_environment import PreviewEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_manifest.env_injection import envelope_keys_for
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    AppSecretBundleRef,
    AppSecretMetadata,
    ManagedService,
    SecretBundle,
    SecretChangeProposal,
)
from astrolift_services.schema.types import (
    AppSecretBundleAttachmentType,
    AppSecretType,
    EmailAccountStatusType,
    EmailDkimTokenType,
    EmailDnsAuthCheckType,
    EmailDnsAuthStatusType,
    EmailEngagementMetricsType,
    EmailIdentityVerificationType,
    EmailMessageType,
    EmailSendQuotaType,
    EmailServiceDetailType,
    EmailSuppressionEntryType,
    EmailTemplateType,
    ManagedServiceCatalogEntryType,
    ManagedServiceObjectsType,
    ManagedServiceObjectType,
    ManagedServiceQueueDepthType,
    ManagedServiceType,
    SecretBundleType,
    SecretChangeProposalType,
    SecretHistoryActorType,
    SecretHistoryEntryType,
    TemplateSendStatPointType,
    attachment_to_type,
    managed_service_catalog_entry_to_type,
    managed_service_to_type,
    secret_bundle_to_type,
    secret_change_proposal_to_type,
    secret_editor_from_user,
)
from core.decorators import tenant_scoped
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


def _caller_org_id() -> int | None:
    """Current tenant's organization id, or None when there's no tenant
    context. Read resolvers over org-owned rows MUST treat None as
    deny-by-default ("no rows" / not-found), never as "all rows" (#1042).

    ``@tenant_scoped()`` only asserts a tenant context exists; it does
    NOT filter any queryset. Every resolver/helper that fetches by slug
    or guid has to add the org constraint itself or it leaks cross-org.
    """
    tenant = get_current_tenant()
    return tenant.organization_id if tenant is not None else None


def _secret_id(*, source: str, key: str, env: str) -> str:
    return f"{source}:{env}:{key}"


def _build_email_detail(
    *,
    managed_service_id: GUID,
    plugin_slug: str,
    region: str,
    identity: str,
) -> EmailServiceDetailType:
    """Drive every read-only email-detail call against the cloud's
    driver, accumulating ``unsupported_notes`` for any operation the
    backend doesn't implement so the UI can show why a tile is empty.

    Each driver call is wrapped in its own try/except so one cloud-side
    transient failure (network, permissions) doesn't bring down the
    whole page — operators see the tiles that loaded plus a hint for
    the ones that didn't.
    """
    from _sdk import UnsupportedOperationError

    from astrolift_services.email_observability import driver_for_plugin_slug

    unsupported: list[str] = []
    quota = None
    account_status = None
    identity_verification = None
    dns_auth_status = None
    suppression_entries: list[EmailSuppressionEntryType] = []

    try:
        driver = driver_for_plugin_slug(
            plugin_slug=plugin_slug,
            region=region,
        )
    except LookupError as exc:
        return EmailServiceDetailType(
            managed_service_id=managed_service_id,
            plugin_slug=plugin_slug,
            region=region,
            identity=identity,
            unsupported_notes=[str(exc)],
        )

    try:
        sq = driver.get_send_quota()
        quota = EmailSendQuotaType(
            max_send_rate=sq.max_send_rate,
            max_24_hour_send=sq.max_24_hour_send,
            sent_last_24h=sq.sent_last_24h,
        )
    except UnsupportedOperationError as exc:
        unsupported.append(f"quota: {exc}")
    except Exception as exc:  # noqa: BLE001 -- defensive: cloud-side transients
        unsupported.append(f"quota: {type(exc).__name__}: {exc}")

    try:
        ass = driver.get_account_send_status()
        account_status = EmailAccountStatusType(
            sending_enabled=ass.sending_enabled,
            production_access=ass.production_access,
            reputation_score=ass.reputation_score,
            bounce_rate_pct=ass.bounce_rate_pct,
            complaint_rate_pct=ass.complaint_rate_pct,
        )
    except UnsupportedOperationError as exc:
        unsupported.append(f"account_status: {exc}")
    except Exception as exc:  # noqa: BLE001
        unsupported.append(f"account_status: {type(exc).__name__}: {exc}")

    if identity:
        try:
            iv = driver.get_identity_verification_details(identity)
            identity_verification = EmailIdentityVerificationType(
                identity=iv.identity,
                is_domain=iv.is_domain,
                status=iv.status,
                verification_token=iv.verification_token,
                dkim_tokens=[
                    EmailDkimTokenType(
                        token=t.token,
                        cname_host=t.cname_host,
                        cname_target=t.cname_target,
                    )
                    for t in iv.dkim_tokens
                ],
            )
        except UnsupportedOperationError as exc:
            unsupported.append(f"identity_verification: {exc}")
        except Exception as exc:  # noqa: BLE001
            unsupported.append(
                f"identity_verification: {type(exc).__name__}: {exc}",
            )

        try:
            dns = driver.verify_dns_authentication(identity)
            dns_auth_status = EmailDnsAuthStatusType(
                identity=dns.identity,
                checked_at=dns.checked_at,
                overall=dns.overall.value,
                dkim=EmailDnsAuthCheckType(
                    protocol=dns.dkim.protocol,
                    outcome=dns.dkim.outcome.value,
                    records=list(dns.dkim.records),
                    message=dns.dkim.message,
                ),
                spf=EmailDnsAuthCheckType(
                    protocol=dns.spf.protocol,
                    outcome=dns.spf.outcome.value,
                    records=list(dns.spf.records),
                    message=dns.spf.message,
                ),
                dmarc=EmailDnsAuthCheckType(
                    protocol=dns.dmarc.protocol,
                    outcome=dns.dmarc.outcome.value,
                    records=list(dns.dmarc.records),
                    message=dns.dmarc.message,
                ),
            )
        except UnsupportedOperationError as exc:
            unsupported.append(f"dns_auth_status: {exc}")
        except Exception as exc:  # noqa: BLE001
            unsupported.append(
                f"dns_auth_status: {type(exc).__name__}: {exc}",
            )

    try:
        for entry in driver.list_suppression_entries(page_size=100):
            suppression_entries.append(
                EmailSuppressionEntryType(
                    address=entry.address,
                    reason=entry.reason.value,
                    suppressed_at=entry.suppressed_at,
                    detail=entry.detail,
                )
            )
    except UnsupportedOperationError as exc:
        unsupported.append(f"suppression_entries: {exc}")
    except Exception as exc:  # noqa: BLE001
        unsupported.append(
            f"suppression_entries: {type(exc).__name__}: {exc}",
        )

    return EmailServiceDetailType(
        managed_service_id=managed_service_id,
        plugin_slug=plugin_slug,
        region=region,
        identity=identity,
        quota=quota,
        account_status=account_status,
        identity_verification=identity_verification,
        dns_auth_status=dns_auth_status,
        suppression_entries=suppression_entries,
        unsupported_notes=unsupported,
    )


def _resolve_email_driver(managed_service_id):
    """Walk the ``ManagedService`` FK chain to a populated
    ``EmailObservabilityDriver`` instance + the email identity (#635,
    #628).

    Returns ``(driver, identity, error_message)``. On any failure the
    first two slots are ``None`` and the third carries a UI-facing
    string the resolver wraps in an empty list — the email-template
    surfaces are read-mostly, so a missing driver renders as "no
    templates" rather than a 500.

    Co-located with the queries module so the two new template
    resolvers + the existing detail resolver stay one cohesive surface.
    """
    from astrolift_services.email_observability import driver_for_plugin_slug
    from astrolift_services.models import ManagedService

    # Org-scope the fetch (#1042): a cross-org / no-tenant caller must
    # not be able to resolve another tenant's email driver by guessing
    # its managed-service guid. ManagedService has no direct org column,
    # so scope through the owning app. org_id None → IS NULL → no match
    # (RegisteredApp.organization is non-null) → deny-by-default.
    org_id = _caller_org_id()
    svc = (
        ManagedService.objects.select_related(
            "app_environment",
            "app_environment__tenant_cluster",
            "app_environment__tenant_cluster__provider_plugin",
        )
        .filter(
            guid=str(managed_service_id),
            registered_app__organization_id=org_id,
            deleted_at__isnull=True,
        )
        .first()
    )
    if svc is None or svc.kind != ManagedService.Kind.EMAIL:
        return None, "", "managed service not found or not an email service"
    plugin_slug = svc.app_environment.tenant_cluster.provider_plugin.slug
    region = svc.app_environment.tenant_cluster.region or ""
    config = svc.config or {}
    identity = (
        config.get("identity") or config.get("email_identity") or config.get("EMAIL_FROM_ADDRESS") or ""
    )
    try:
        driver = driver_for_plugin_slug(plugin_slug=plugin_slug, region=region)
    except LookupError as exc:
        return None, identity, str(exc)
    return driver, identity, ""


def _bundle_key_count(bundle) -> int:
    """Count of keys the bundle is expected to project (#441).

    Backed by ``SecretBundle.last_known_keys`` -- a snapshot the
    rotation activity refreshes on every materialise, plus a lazy
    on-read refresh when the cache is older than 1 h.  When the cache
    has never been populated (newly created bundle, scheduled refresh
    hasn't run yet, secrets backend unreachable) returns 0 and the UI
    shows '?' to signal "unknown" rather than "confirmed zero".
    """
    from astrolift_services.bundle_keys import known_key_count

    return known_key_count(bundle)


def _allowed_scopes_for_env(env_name: str, preview_env_branches: dict[str, str]) -> frozenset[str]:
    """Compute the set of scope values that a secret must have to be
    visible in ``env_name``.

    Preview envs accept ``all``, ``preview``, and ``preview:<branch>``.
    All other envs (production, staging, …) accept ``all`` and
    ``production``.
    """
    if env_name in preview_env_branches:
        branch = preview_env_branches[env_name]
        return frozenset({"all", "preview", f"preview:{branch}"})
    return frozenset({"all", "production"})


def _list_app_secrets(*, app, env_names: list[str]) -> list[AppSecretType]:
    """Compose the merged secret view across:
    - manifest [env] literals (app-wide → repeated per env)
    - per-env AppSecretBundleRef key prefix (we don't fetch values
      from the secrets backend; we surface the bundle slug + prefix
      so the UI shows 'attached')
    - per-env ManagedService bindings (envelope keys per binding)

    Each literal row is decorated with the operator-facing metadata
    from ``AppSecretMetadata`` when present (#677 / #678 / #752).
    Metadata is looked up by ``(env_name, key)`` with empty env_name as
    a fallback for the 'applies to every env' default — a per-env row
    wins over the wildcard.  Missing metadata defaults to no expiry +
    ``set_via='web'`` + ``scope='all'`` so older rows render identically.

    Secrets whose ``scope`` doesn't match the queried env are filtered
    out (#752).  Preview envs accept ``all``, ``preview``, and
    ``preview:<branch>``; all other envs accept ``all`` and
    ``production``.
    """
    out: list[AppSecretType] = []

    # 1. Literals from the staged-or-source manifest. Reads from
    # manifest_raw_staged when set so the UI reflects unsaved
    # edits; otherwise from manifest_raw.
    raw_text = app.manifest_raw_staged or app.manifest_raw or ""
    literals = read_app_env(raw_text)
    literal_editor = secret_editor_from_user(app.updated_by)

    # Pre-fetch metadata for this app's literal keys so the resolver
    # is one round-trip rather than N.  The lookup table maps
    # ``(env_name, key)`` → row.  An empty env_name acts as the
    # wildcard fallback when no per-env row exists.
    meta_qs = AppSecretMetadata.objects.filter(
        registered_app=app,
        key__in=list(literals.keys()) or [""],
        deleted_at__isnull=True,
    )
    meta_index: dict[tuple[str, str], AppSecretMetadata] = {}
    for m in meta_qs:
        meta_index[(m.environment_name, m.key)] = m

    # Build preview-env → branch mapping for scope filtering (#752).
    # One extra query per _list_app_secrets call; avoids N+1 over envs.
    preview_env_branches: dict[str, str] = dict(
        PreviewEnvironment.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
            status__in=(
                PreviewEnvironment.Status.BUILDING,
                PreviewEnvironment.Status.RUNNING,
            ),
        ).values_list("app_environment__name", "branch")
    )

    for env_name in env_names:
        allowed = _allowed_scopes_for_env(env_name, preview_env_branches)
        for key in sorted(literals):
            meta = meta_index.get((env_name, key)) or meta_index.get(("", key))
            secret_scope = meta.scope if meta else "all"
            if secret_scope not in allowed:
                continue
            out.append(
                AppSecretType(
                    id=_secret_id(source="literal", key=key, env=env_name),
                    key=key,
                    environment_name=env_name,
                    source="literal",
                    bundle_slug="",
                    managed_service_kind="",
                    is_masked=True,
                    last_edited_at=app.updated_at,
                    last_edited_by=literal_editor,
                    expires_at=meta.expires_at if meta else None,
                    set_via=(meta.source if meta else "web"),
                    scope=secret_scope,
                )
            )

    # 2. Bundle attachments (per env)
    refs = AppSecretBundleRef.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
        app_environment__name__in=env_names,
    ).select_related("secret_bundle", "app_environment", "updated_by")
    for ref in refs:
        # Bundle keys live in the secrets backend; we only know the
        # bundle attachment exists. Surface a single placeholder row
        # per attachment so the UI can show 'bundle: stripe-prod'
        # without us fetching values out-of-cluster.
        out.append(
            AppSecretType(
                id=_secret_id(
                    source="bundle",
                    key=ref.secret_bundle.slug,
                    env=ref.app_environment.name,
                ),
                key=f"{ref.prefix or ''}*" if ref.prefix else "*",
                environment_name=ref.app_environment.name,
                source="bundle",
                bundle_slug=ref.secret_bundle.slug,
                managed_service_kind="",
                is_masked=True,
                last_edited_at=ref.updated_at,
                last_edited_by=secret_editor_from_user(ref.updated_by),
                expires_at=None,
                set_via="bundle",
            )
        )

    # 3. Managed-service envelopes (per binding)
    services = ManagedService.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
        app_environment__name__in=env_names,
    ).select_related("app_environment", "updated_by")
    for svc in services:
        env_name = svc.app_environment.name
        svc_editor = secret_editor_from_user(svc.updated_by)
        for key in envelope_keys_for(svc.kind):
            out.append(
                AppSecretType(
                    id=_secret_id(
                        source="managed_service",
                        key=f"{svc.name}.{key}",
                        env=env_name,
                    ),
                    key=key,
                    environment_name=env_name,
                    source="managed_service",
                    bundle_slug="",
                    managed_service_kind=svc.kind,
                    is_masked=True,
                    last_edited_at=svc.updated_at,
                    last_edited_by=svc_editor,
                    expires_at=None,
                    set_via="managed_service",
                )
            )

    return out


def _managed_services_qs(
    *,
    app_slug: str,
    environment_name: str | None,
    search: str | None = None,
):
    """Filtered, unordered managed-service stream for one app.

    Shared by ``astrolift_managed_services`` and its paginated sibling so
    the two can never disagree about what a row is. Ordering is
    deliberately not applied here — ``keyset_page`` imposes it from the
    seek key.

    Module-level rather than a method on ``ServicesQuery``: Strawberry
    binds ``self`` on a ROOT field resolver to the schema's root value,
    and the Django view leaves that ``None`` — ``self._helper()`` inside
    a root resolver raises ``AttributeError`` at execution time. Same
    trap ``_agent_list_rows`` documents in ``astrolift_agents``.

    ManagedService carries no org column of its own; it reaches the
    tenant through the owning app. A ``None`` org id yields ``.none()``
    rather than an unscoped queryset — deny-by-default (#1042 / #1183).
    """
    org_id = _caller_org_id()
    if org_id is None:
        return ManagedService.objects.none()
    qs = (
        ManagedService.objects.select_related("registered_app", "app_environment")
        .prefetch_related("attachments", "volume_bindings")
        .filter(
            registered_app__slug=app_slug,
            registered_app__organization_id=org_id,
            deleted_at__isnull=True,
        )
    )
    if environment_name:
        qs = qs.filter(app_environment__name=environment_name)
    if search:
        qs = qs.filter(
            search_q(
                search,
                "name",
                "kind",
                "variant",
                "status",
                "app_environment__name",
            )
        )
    return qs


@strawberry.type
class ServicesQuery:
    @strawberry.field
    @require_permission(Permission.PROJECT_READ)
    @tenant_scoped()
    def astrolift_project_resource_clusters(
        self,
        info: Info,
        project_id: GUID,
    ) -> list[TenantClusterType]:
        """Managed clusters a project member may target for shared resources."""

        org_id = _caller_org_id()
        project_exists = Project.objects.filter(
            guid=str(project_id),
            organization_id=org_id,
            deleted_at__isnull=True,
        ).exists()
        if not project_exists:
            return []
        rows = (
            TenantCluster.objects.filter(
                Q(organization_id=org_id) | Q(organization_id__isnull=True),
                deleted_at__isnull=True,
                is_active=True,
                lifecycle=TenantCluster.Lifecycle.MANAGED.value,
            )
            .select_related("organization", "provider_plugin")
            .order_by("name", "guid")
        )
        return [cluster_to_type(row) for row in rows]

    @strawberry.field
    @require_permission(Permission.PROJECT_READ)
    @tenant_scoped()
    def astrolift_project_managed_service_catalog(
        self,
        info: Info,
        project_id: GUID,
        cluster_id: GUID,
    ) -> list[ManagedServiceCatalogEntryType]:
        """All executable and planned variants for the selected cluster."""

        org_id = _caller_org_id()
        if not Project.objects.filter(
            guid=str(project_id),
            organization_id=org_id,
            deleted_at__isnull=True,
        ).exists():
            return []
        cluster = (
            TenantCluster.objects.select_related("provider_plugin")
            .filter(
                Q(organization_id=org_id) | Q(organization_id__isnull=True),
                guid=str(cluster_id),
                deleted_at__isnull=True,
                is_active=True,
                lifecycle=TenantCluster.Lifecycle.MANAGED.value,
            )
            .first()
        )
        if cluster is None:
            return []
        from astrolift_services.managed_service_catalog import list_catalog

        return [
            managed_service_catalog_entry_to_type(row) for row in list_catalog(cluster.provider_plugin.slug)
        ]

    @strawberry.field
    @require_permission(Permission.PROJECT_READ)
    @tenant_scoped()
    def astrolift_project_managed_services(
        self,
        info: Info,
        project_id: GUID,
    ) -> list[ManagedServiceType]:
        """Project-owned shared infrastructure and its workload attachments."""

        org_id = _caller_org_id()
        project = Project.objects.filter(
            guid=str(project_id),
            organization_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if project is None:
            return []
        rows = (
            ManagedService.objects.select_related(
                "project",
                "tenant_cluster__provider_plugin",
            )
            .prefetch_related(
                "attachments__agent_environment_spec",
                "attachments__app_environment__registered_app",
                "volume_bindings",
            )
            .filter(project=project, deleted_at__isnull=True)
            .order_by("kind", "name", "guid")
        )
        return [managed_service_to_type(row) for row in rows]

    @strawberry.field
    @require_permission(Permission.PROJECT_READ)
    @tenant_scoped()
    def astrolift_project_secret_bundles(
        self,
        info: Info,
        project_id: GUID,
    ) -> list[SecretBundleType]:
        org_id = _caller_org_id()
        project = Project.objects.filter(
            guid=str(project_id),
            organization_id=org_id,
            deleted_at__isnull=True,
        ).first()
        if project is None:
            return []
        rows = (
            SecretBundle.objects.select_related("organization", "team", "project", "tenant_cluster")
            .prefetch_related(
                "agent_refs__environment_spec",
                "app_refs__registered_app",
                "app_refs__app_environment",
            )
            .filter(project=project, deleted_at__isnull=True)
            .order_by("name", "guid")
        )
        return [secret_bundle_to_type(row) for row in rows]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_secrets(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> list[AppSecretType]:
        """List secret references visible to the app at runtime.

        ``environment_name`` filter narrows to one env; omitted
        returns the union across all envs."""
        org_id = _caller_org_id()
        app = (
            RegisteredApp.objects.select_related("updated_by")
            .filter(slug=app_slug, organization_id=org_id)
            .first()
        )
        if app is None:
            return []
        if environment_name:
            env_names = [environment_name]
        else:
            env_names = list(
                AppEnvironment.objects.filter(
                    registered_app=app,
                    deleted_at__isnull=True,
                ).values_list("name", flat=True),
            )
            if not env_names:
                env_names = ["preview"]
        return _list_app_secrets(app=app, env_names=env_names)

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_secret_history(
        self,
        info: Info,
        app_slug: str,
        key: str,
    ) -> list[SecretHistoryEntryType]:
        """Per-key audit timeline for one app's secrets (#725).

        Returns the 50 most-recent ``AuditEvent`` rows whose ``action``
        is one of ``app.secret.set`` / ``app.secret.delete`` /
        ``app.secret.rotate`` and whose ``target_id`` encodes
        ``<app_slug>:<key>``. The plaintext value never appears — the
        audit row doesn't carry it.

        Tenant-scoped via ``@tenant_scoped`` + ``APP_READ``. An app the
        caller can't see produces an empty list (rather than a NOT_FOUND
        envelope) because queries don't carry the MutationResult shape;
        the FE renders 'no history' indistinguishably from 'app gone'.
        """
        from astrolift_operations.models import AuditEvent

        org_id = _caller_org_id()
        app = RegisteredApp.objects.filter(slug=app_slug, organization_id=org_id).only("id", "slug").first()
        if app is None:
            return []

        # Compose the same composite target id the mutation writer
        # stores. Older audit rows pre-date this targeting and won't
        # carry it — they were untargetable per-key in the first place,
        # so this query intentionally omits them rather than returning
        # rows from a different (app, key) pair.
        target_id = f"{app.slug}:{key}"

        # Scope the audit query by org too (#1042): target_id is
        # "<slug>:<key>" and app slugs recur across orgs, so without the
        # org filter a same-slug app in another tenant would surface that
        # tenant's secret-history rows. AuditEvent.organization is stamped
        # from the tenant context at write time, so org_id matches here.
        rows = list(
            AuditEvent.objects.filter(
                action__in=(
                    "app.secret.set",
                    "app.secret.delete",
                    "app.secret.rotate",
                ),
                target_kind="AppSecret",
                target_id=target_id,
                organization_id=org_id,
            ).order_by("-occurred_at")[:50]
        )

        # Hydrate actor user-rows in one round trip (rather than N
        # FK fetches inside the loop). Actor ids on AuditEvent are
        # stringified pks; system/api-token actors won't parse to int.
        actor_ids: list[int] = []
        for row in rows:
            if row.actor_kind != "user" or not row.actor_id:
                continue
            try:
                actor_ids.append(int(row.actor_id))
            except (TypeError, ValueError):
                continue
        users_by_id: dict[int, object] = {}
        if actor_ids:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            for u in User.objects.filter(pk__in=actor_ids).only("pk", "username"):
                users_by_id[u.pk] = u

        out: list[SecretHistoryEntryType] = []
        for row in rows:
            data = row.data or {}
            error_code = (data.get("error_code") or "") if isinstance(data, dict) else ""
            # AuditEvent stores the full dotted action; surface only the
            # trailing segment so the FE switches on a stable token.
            short_action = row.action.rsplit(".", 1)[-1]

            # Resolve actor. ``actor_id`` is a stringified pk for user
            # actors; system actors (e.g. cron, workflow) carry an
            # empty actor_id — surface them as id='0' / username=''.
            actor_id_str = row.actor_id or "0"
            username = ""
            if row.actor_kind == "user" and row.actor_id:
                try:
                    user = users_by_id.get(int(row.actor_id))
                except (TypeError, ValueError):
                    user = None
                if user is not None:
                    username = getattr(user, "username", "") or ""

            out.append(
                SecretHistoryEntryType(
                    timestamp=row.occurred_at,
                    actor=SecretHistoryActorType(id=actor_id_str, username=username),
                    action=short_action,
                    success=row.decision != "DENY" and not error_code,
                    error_code=error_code,
                    source_ip=row.request_ip or "",
                )
            )
        return out

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_secret_bundles(
        self,
        info: Info,
    ) -> list[SecretBundleType]:
        """All secret bundles in the calling tenant."""
        org_id = _caller_org_id()
        if org_id is None:
            return []
        qs = (
            SecretBundle.objects.select_related("organization", "team")
            .filter(organization_id=org_id, deleted_at__isnull=True)
            .order_by("-created_at")[:200]
        )
        return [secret_bundle_to_type(b) for b in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_app_secret_bundle_attachments(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> list[AppSecretBundleAttachmentType]:
        """Bundles attached to the app, grouped by env.

        Merge order is creation-order within an (app, env) group
        (lowest-first). App-local literals always win on collision
        regardless of merge_order — this column is just a debugging
        aid for the operator to read precedence at a glance."""
        org_id = _caller_org_id()
        if org_id is None:
            return []
        qs = (
            AppSecretBundleRef.objects.select_related(
                "registered_app",
                "secret_bundle",
                "secret_bundle__team",
                "app_environment",
            )
            .filter(
                registered_app__slug=app_slug,
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .order_by("app_environment__name", "created_at")
        )
        if environment_name:
            qs = qs.filter(app_environment__name=environment_name)
        out: list[AppSecretBundleAttachmentType] = []
        env_seq: dict[str, int] = {}
        for ref in qs:
            env_name = ref.app_environment.name
            order = env_seq.get(env_name, 0)
            env_seq[env_name] = order + 1
            out.append(
                attachment_to_type(
                    ref,
                    key_count=_bundle_key_count(ref.secret_bundle),
                    merge_order=order,
                )
            )
        return out

    @strawberry.field(
        deprecation_reason=(
            "Unbounded, and applies no ordering at all — row order is whatever "
            "Postgres returns. Use astroliftManagedServicesPage."
        )
    )
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_managed_services(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
    ) -> list[ManagedServiceType]:
        qs = _managed_services_qs(app_slug=app_slug, environment_name=environment_name).order_by(
            "-created_at", "-guid"
        )
        return [managed_service_to_type(s) for s in qs]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_managed_services_page(
        self,
        info: Info,
        app_slug: str,
        environment_name: str | None = None,
        search: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> PageType[ManagedServiceType]:
        """Cursor-paginated managed services for one app (#1235).

        ``astroliftManagedServices`` returns every service the app owns in
        one response and never orders them, so a long-lived app hands the
        UI an unbounded, unstably-ordered list. This walks the same rows
        newest-first on a ``(-created_at, -guid)`` seek key.

        ``search`` matches the name, kind, variant, status, and
        environment — the columns the services table renders, which is
        what an operator types when hunting one binding.
        """
        page = keyset_page(
            _managed_services_qs(
                app_slug=app_slug,
                environment_name=environment_name,
                search=search,
            ),
            cursor=after,
            limit=limit,
        )
        return page.map(managed_service_to_type)

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_managed_service_objects(
        self,
        info: Info,
        managed_service_id: GUID,
        limit: int = 10,
    ) -> ManagedServiceObjectsType | None:
        """Top-N entries from the recent-objects cache for an
        `object_store` kind managed service (#401).

        Sourced from `config['recent_objects']` which the workflow loop
        is expected to refresh on a schedule.  A live cloud list call
        needs the driver SDK to expose `list_recent_objects`, filed as
        a backend gap follow-up — until then the field returns whatever
        the cache contains (often empty for freshly-provisioned rows;
        UI surfaces a friendly hint in that case).
        """
        if limit < 1:
            limit = 10
        if limit > 100:
            limit = 100

        org_id = _caller_org_id()
        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(
                guid=str(managed_service_id),
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return None
        if svc.kind != ManagedService.Kind.OBJECT_STORE:
            return ManagedServiceObjectsType(
                managed_service_id=managed_service_id,
                kind=svc.kind,
                name=svc.name,
                objects=[],
                truncated=False,
                cache_age_seconds=None,
            )

        config = svc.config or {}
        raw_objects = config.get("recent_objects") or []
        sampled_at = config.get("recent_objects_sampled_at")
        cache_age = None
        if sampled_at:
            try:
                # Stored as ISO 8601 string by the workflow refresh.
                from datetime import datetime

                if isinstance(sampled_at, str):
                    parsed = datetime.fromisoformat(sampled_at.replace("Z", "+00:00"))
                else:
                    parsed = sampled_at
                from django.utils import timezone

                now = timezone.now()
                # Use timezone-aware diff; treat naive snapshots as UTC.
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=now.tzinfo)
                cache_age = max(int((now - parsed).total_seconds()), 0)
            except (ValueError, TypeError):
                cache_age = None

        objects: list[ManagedServiceObjectType] = []
        for entry in raw_objects[:limit]:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key") or entry.get("name") or ""
            if not key:
                continue
            size = entry.get("size_bytes") or entry.get("size") or 0
            last_modified_raw = entry.get("last_modified")
            last_modified = None
            if last_modified_raw:
                try:
                    from datetime import datetime

                    if isinstance(last_modified_raw, str):
                        last_modified = datetime.fromisoformat(last_modified_raw.replace("Z", "+00:00"))
                    else:
                        last_modified = last_modified_raw
                except (ValueError, TypeError):
                    last_modified = None
            objects.append(
                ManagedServiceObjectType(
                    key=str(key),
                    size_bytes=int(size) if isinstance(size, (int, float)) else 0,
                    last_modified=last_modified,
                )
            )

        return ManagedServiceObjectsType(
            managed_service_id=managed_service_id,
            kind=svc.kind,
            name=svc.name,
            objects=objects,
            truncated=len(raw_objects) > limit,
            cache_age_seconds=cache_age,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_managed_service_queue_depth(
        self,
        info: Info,
        managed_service_id: GUID,
    ) -> ManagedServiceQueueDepthType | None:
        """Cached depth snapshot for a `queue` / `topic` managed
        service (#401).

        Reads `config['depth_snapshot']` — populated by the workflow's
        scheduled refresh.  Live depth needs `queue_depth` on the driver
        SDK (backend gap follow-up); until then this field returns the
        cached values plus a `sampled_at` timestamp so operators can see
        how stale the reading is.
        """
        org_id = _caller_org_id()
        svc = (
            ManagedService.objects.select_related("app_environment", "registered_app")
            .filter(
                guid=str(managed_service_id),
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return None
        if svc.kind not in (ManagedService.Kind.QUEUE, ManagedService.Kind.TOPIC):
            return ManagedServiceQueueDepthType(
                managed_service_id=managed_service_id,
                kind=svc.kind,
                name=svc.name,
                depth=0,
                in_flight=0,
                sampled_at=None,
            )

        config = svc.config or {}
        snapshot = config.get("depth_snapshot") or {}
        sampled_at_raw = snapshot.get("sampled_at")
        sampled_at = None
        if sampled_at_raw:
            try:
                from datetime import datetime

                if isinstance(sampled_at_raw, str):
                    sampled_at = datetime.fromisoformat(sampled_at_raw.replace("Z", "+00:00"))
                else:
                    sampled_at = sampled_at_raw
            except (ValueError, TypeError):
                sampled_at = None

        depth = snapshot.get("depth") or 0
        in_flight = snapshot.get("in_flight") or 0
        return ManagedServiceQueueDepthType(
            managed_service_id=managed_service_id,
            kind=svc.kind,
            name=svc.name,
            depth=int(depth) if isinstance(depth, (int, float)) else 0,
            in_flight=int(in_flight) if isinstance(in_flight, (int, float)) else 0,
            sampled_at=sampled_at,
        )

    # ---- Email observability (#629, #631, #632, #633, #634) ----------

    @strawberry.field
    @require_permission(Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def astrolift_email_service_detail(
        self,
        info: Info,
        managed_service_id: GUID,
    ) -> EmailServiceDetailType | None:
        """Composite read-surface for the email-detail page.

        One round-trip carries the quota, account status, identity
        verification, DNS auth status, and a slice of the suppression
        list. Each field is independently nullable so a partial failure
        in one driver call doesn't take the whole page down — the UI
        renders the available tiles + an unsupported-notes list for the
        rest.

        Permission gate stacks ``app.read`` (caller can see the app) with
        ``managed_service.update`` (the email-detail surface contains
        operator-actionable rows like the suppression list).
        """
        org_id = _caller_org_id()
        svc = (
            ManagedService.objects.select_related(
                "app_environment",
                "app_environment__tenant_cluster",
                "app_environment__tenant_cluster__provider_plugin",
                "registered_app",
            )
            .filter(
                guid=str(managed_service_id),
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if svc is None:
            return None
        if svc.kind != ManagedService.Kind.EMAIL:
            return None

        plugin = svc.app_environment.tenant_cluster.provider_plugin
        plugin_slug = plugin.slug
        region = svc.app_environment.tenant_cluster.region or ""
        # Identity lives on the driver config blob the lifecycle driver
        # stamped at provision time; fall back to the binding's
        # configured identity / from-address when present.
        config = svc.config or {}
        identity = (
            config.get("identity") or config.get("email_identity") or config.get("EMAIL_FROM_ADDRESS") or ""
        )
        # The handle the lifecycle driver returned carries the identity
        # too — use that when nothing's in config (handle format is
        # ``email/<identity>``).
        if not identity and svc.connection_secret_ref:
            handle_parts = svc.connection_secret_ref.split("/", 1)
            if len(handle_parts) == 2 and handle_parts[0] == "email":
                identity = handle_parts[1]
        return _build_email_detail(
            managed_service_id=managed_service_id,
            plugin_slug=plugin_slug,
            region=region,
            identity=identity,
        )

    # ---- Email templates (#635, #628) --------------------------------

    @strawberry.field
    @require_permission(Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def astrolift_email_templates(
        self,
        info: Info,
        managed_service_id: GUID,
    ) -> list[EmailTemplateType]:
        """List transactional-email templates for one email managed
        service (#635).

        Backed by the per-cloud :class:`EmailObservabilityDriver`. On a
        missing managed service / unsupported backend / cloud-side
        transient failure, returns an empty list so the
        template-management UI renders the empty state with the
        "Create template" CTA rather than a 500."""
        from _sdk import UnsupportedOperationError

        driver, _identity, err = _resolve_email_driver(managed_service_id)
        if driver is None or err:
            return []
        try:
            templates = driver.list_templates()
        except UnsupportedOperationError:
            return []
        except Exception:  # noqa: BLE001 -- cloud-side transient
            return []
        return [
            EmailTemplateType(
                name=t.name,
                subject=t.subject,
                html_body=t.html_body,
                text_body=t.text_body,
                created_at=t.created_at,
            )
            for t in templates
        ]

    @strawberry.field
    @require_permission(Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def astrolift_email_template(
        self,
        info: Info,
        managed_service_id: GUID,
        name: str,
    ) -> EmailTemplateType | None:
        """Single template detail by name (#635). Returns ``None`` when
        the template doesn't exist on the backend (UI renders a 404
        page rather than a partial)."""
        from _sdk import UnsupportedOperationError

        driver, _identity, err = _resolve_email_driver(managed_service_id)
        if driver is None or err:
            return None
        try:
            template = driver.get_template(name=name)
        except UnsupportedOperationError:
            return None
        except Exception:  # noqa: BLE001 -- cloud-side / not found
            return None
        return EmailTemplateType(
            name=template.name,
            subject=template.subject,
            html_body=template.html_body,
            text_body=template.text_body,
            created_at=template.created_at,
        )

    @strawberry.field
    @require_permission(Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def astrolift_email_template_stats(
        self,
        info: Info,
        managed_service_id: GUID,
        name: str,
        days: int = 14,
    ) -> list[TemplateSendStatPointType]:
        """Per-template send/delivery/bounce/complaint sparkline data
        (#628).

        Empty list when the cloud has no metrics for this template
        yet (zero sends in the window, or the configuration set
        doesn't publish per-template counters). 15-minute granularity,
        oldest-first; ``days`` caps the lookback window (default 14)."""
        from _sdk import UnsupportedOperationError

        driver, _identity, err = _resolve_email_driver(managed_service_id)
        if driver is None or err:
            return []
        try:
            points = driver.get_template_send_statistics(name=name, days=days)
        except UnsupportedOperationError:
            return []
        except Exception:  # noqa: BLE001 -- cloud-side transient
            return []
        return [
            TemplateSendStatPointType(
                timestamp=p.timestamp,
                sends=p.sends,
                deliveries=p.deliveries,
                bounces=p.bounces,
                complaints=p.complaints,
            )
            for p in points
        ]

    # ---- Per-message SES event log (#756, unblocks #624/#626) -------

    @strawberry.field
    @require_permission(Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def astrolift_email_messages(
        self,
        info: Info,
        managed_service_id: GUID,
        limit: int = 50,
        event_kind: str | None = None,
        recipient: str | None = None,
    ) -> list[EmailMessageType]:
        """Per-message SES event log scoped to one managed service (#624).

        Backed by ``EmailEvent`` rows the SNS webhook receiver appends
        as SES publishes notifications. Each call returns the
        ``limit`` newest rows (default 50, max 500), filtered by
        ``event_kind`` and/or substring-matched ``recipient`` when
        supplied. Returns ``[]`` when the service can't be resolved or
        is the wrong kind — same posture as the rest of the
        email-detail surfaces.
        """
        from astrolift_services.models import EmailEvent, EmailEventKind

        if limit < 1:
            limit = 1
        if limit > 500:
            limit = 500

        org_id = _caller_org_id()
        svc = (
            ManagedService.objects.filter(
                guid=str(managed_service_id),
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .only("id", "kind")
            .first()
        )
        if svc is None or svc.kind != ManagedService.Kind.EMAIL:
            return []

        qs = EmailEvent.objects.filter(managed_service=svc)
        if event_kind:
            # Reject unknown kinds rather than silently returning
            # everything — typo in the FE filter would otherwise look
            # like "all rows".
            valid = {k.value for k in EmailEventKind}
            kind_lc = event_kind.lower()
            if kind_lc not in valid:
                return []
            qs = qs.filter(event_kind=kind_lc)
        if recipient:
            qs = qs.filter(recipient__icontains=recipient)

        return [
            EmailMessageType(
                id=GUID(str(ev.guid)),
                message_id=ev.message_id,
                recipient=ev.recipient,
                subject=ev.subject,
                event_kind=ev.event_kind,
                occurred_at=ev.occurred_at,
                metadata=ev.metadata or {},
            )
            for ev in qs[:limit]
        ]

    @strawberry.field
    @require_permission(Permission.APP_READ, Permission.MANAGED_SERVICE_UPDATE)
    @tenant_scoped()
    def astrolift_email_engagement_metrics(
        self,
        info: Info,
        managed_service_id: GUID,
        days: int = 30,
    ) -> EmailEngagementMetricsType | None:
        """Engagement aggregate over the last ``days`` (default 30) for
        one email managed service (#626).

        Computes the six raw counters + four derived percentages from
        the ``EmailEvent`` rows whose ``occurred_at`` falls in the
        window. Bounce/complaint rates are scaled to ``total_sends``;
        open/click rates are scaled to ``total_deliveries``. Returns
        ``None`` when the service can't be resolved or is the wrong
        kind."""
        import datetime as dt

        from django.db.models import Count

        from astrolift_services.models import EmailEvent

        if days < 1:
            days = 1
        if days > 365:
            days = 365

        org_id = _caller_org_id()
        svc = (
            ManagedService.objects.filter(
                guid=str(managed_service_id),
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .only("id", "kind")
            .first()
        )
        if svc is None or svc.kind != ManagedService.Kind.EMAIL:
            return None

        from django.utils import timezone as _tz

        since = _tz.now() - dt.timedelta(days=days)
        rows = (
            EmailEvent.objects.filter(
                managed_service=svc,
                occurred_at__gte=since,
            )
            .values("event_kind")
            .annotate(n=Count("id"))
        )
        counts: dict[str, int] = {row["event_kind"]: int(row["n"]) for row in rows}

        sends = counts.get("send", 0)
        deliveries = counts.get("delivery", 0)
        bounces = counts.get("bounce", 0)
        complaints = counts.get("complaint", 0)
        opens = counts.get("open", 0)
        clicks = counts.get("click", 0)

        def _pct(num: int, denom: int) -> float:
            if denom <= 0:
                return 0.0
            return round((num / denom) * 100, 2)

        return EmailEngagementMetricsType(
            total_sends=sends,
            total_deliveries=deliveries,
            total_bounces=bounces,
            total_complaints=complaints,
            total_opens=opens,
            total_clicks=clicks,
            bounce_rate_pct=_pct(bounces, sends),
            complaint_rate_pct=_pct(complaints, sends),
            open_rate_pct=_pct(opens, deliveries),
            click_rate_pct=_pct(clicks, deliveries),
            window_days=days,
        )

    # ---- Secret-change proposals (#488) ------------------------------

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_secret_change_proposals(
        self,
        info: Info,
        app_slug: str | None = None,
        status: str | None = None,
    ) -> list[SecretChangeProposalType]:
        """List secret-change proposals for the calling tenant (#488).

        Filtered by app slug + lifecycle status when provided.  Default
        view (no filters) returns every proposal across every app the
        caller can read; the global ``/approvals`` page consumes this
        to render the mixed queue alongside deployment approvals.
        """
        org_id = _caller_org_id()
        if org_id is None:
            return []
        qs = (
            SecretChangeProposal.objects.select_related(
                "registered_app",
                "app_environment",
                "proposer",
            )
            .filter(registered_app__organization_id=org_id, deleted_at__isnull=True)
            .order_by("-created_at")
        )
        if app_slug:
            qs = qs.filter(registered_app__slug=app_slug)
        if status:
            valid = {s.value for s in SecretChangeProposal.Status}
            if status in valid:
                qs = qs.filter(status=status)
        # Hard cap so a runaway tenant can't page-of-everything us.
        return [secret_change_proposal_to_type(p) for p in qs[:200]]

    @strawberry.field
    @require_permission(Permission.APP_READ)
    @tenant_scoped()
    def astrolift_secret_change_proposal(
        self,
        info: Info,
        id: GUID,
    ) -> SecretChangeProposalType | None:
        """Single proposal detail for the proposal-detail page."""
        org_id = _caller_org_id()
        proposal = (
            SecretChangeProposal.objects.select_related(
                "registered_app",
                "app_environment",
                "proposer",
            )
            .filter(
                guid=str(id),
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .first()
        )
        if proposal is None:
            return None
        return secret_change_proposal_to_type(proposal)
