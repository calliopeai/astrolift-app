"""DomainMutations — split from the monolithic mutations module."""

from __future__ import annotations

from datetime import UTC

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_lifecycle.models import (
    CustomDomain,
    DomainPathRoute,
    DomainRedirectRule,
)
from astrolift_lifecycle.schema.mutations.helpers import (
    _kick_validate_custom_domain,
)
from astrolift_lifecycle.schema.mutations.types import (
    AddAppDomainInput,
    AddWildcardDomainInput,
    RecheckDomainValidationInput,
    RemoveAppDomainInput,
    SetDomainPathRoutesInput,
    SetDomainRedirectsInput,
    UploadCustomDomainCertificateInput,
    _AppDomainRemovedPayload,
)
from astrolift_lifecycle.schema.types import (
    AppDomainType,
    app_domain_to_type,
)
from astrolift_registry.models import RegisteredApp
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class DomainMutations:
    # ---- Custom domain (#281) -----------------------------------

    @strawberry.field
    @mutation_audit(action="app.domain.add")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def add_app_domain(
        self,
        info: Info,
        input: AddAppDomainInput,
    ) -> MutationResultType[AppDomainType]:
        # Org-scope the app lookup to the caller's tenant before creating a
        # CustomDomain + firing the validation workflow. Slugs are unique
        # only within an org. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        host = (input.hostname or "").strip().lower()
        if not host or "." not in host:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "hostname must be a fully-qualified domain",
                field="hostname",
            )
        method = (input.validation_method or "dns_txt").lower()
        valid_methods = {"dns_txt", "http_01", "dns_01"}
        if method not in valid_methods:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"validation_method must be one of {sorted(valid_methods)}",
                field="validationMethod",
            )
        # Idempotent re-add: an active row with the same hostname is
        # treated as success rather than a 409.
        existing = CustomDomain.objects.filter(hostname=host, deleted_at__isnull=True).first()
        if existing is not None:
            if existing.registered_app_id != app.id:
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    f"domain {host!r} already bound to another app",
                    field="hostname",
                )
            return gql_success(app_domain_to_type(existing))

        # Build the handshake — challenge token + the per-record list
        # the operator must add to their authoritative DNS (or that
        # the platform will create itself when the parent zone is
        # managed). The validation workflow consumes
        # ``required_dns_records`` to know what to probe.
        from astrolift_clusters.models import ManagedDomain
        from astrolift_lifecycle.custom_domain_handshake import (
            build_handshake,
            hostname_parent_zone,
            resolve_cluster_ingress_target,
        )

        parent_zone = hostname_parent_zone(host)
        managed_zone = ManagedDomain.objects.filter(
            zone=parent_zone,
            deleted_at__isnull=True,
        ).first()
        # Best-effort cluster pick: prefer the app's default tenant
        # cluster; fall back to whatever cluster the managed-zone
        # row binds. Either way the operator-facing CNAME target is
        # stable.
        cluster = getattr(app, "default_tenant_cluster", None)
        cluster_slug = cluster.slug if cluster is not None else "default"
        cname_target = resolve_cluster_ingress_target(
            cluster_slug=cluster_slug,
            managed_domain_zone=managed_zone.zone if managed_zone else None,
        )
        handshake = build_handshake(
            hostname=host,
            cluster_ingress_target=cname_target,
            is_platform_managed_zone=managed_zone is not None,
            validation_method=method,
        )

        domain = CustomDomain.objects.create(
            registered_app=app,
            hostname=host,
            validation_method=method,
            txt_challenge_token=handshake.txt_challenge_token,
            expected_cname_target=handshake.expected_cname_target,
            required_dns_records=[
                {
                    "kind": r.kind,
                    "name": r.name,
                    "value": r.value,
                    "ttl": r.ttl,
                    "propagated": r.propagated,
                    "last_checked_at": r.last_checked_at,
                    "message": r.message,
                }
                for r in handshake.required_records
            ],
            is_platform_managed_zone=handshake.is_platform_managed_zone,
        )
        # Fire the validation workflow on creation so platform-managed
        # zones auto-create their records + first DNS probe runs
        # without the operator having to click Recheck.
        _kick_validate_custom_domain(domain)
        return gql_success(app_domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="app.domain.add_wildcard")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def add_wildcard_domain(
        self,
        info: Info,
        input: AddWildcardDomainInput,
    ) -> MutationResultType[AppDomainType]:
        """Add a wildcard custom domain (``*.hostname``) for an app (#753).

        Same handshake shape as ``addAppDomain`` but pins the row as
        ``is_wildcard=True``, forces ``validation_method=dns_01`` (CA
        policy on wildcards), and persists an optional
        ``sni_cert_ref`` so the renderer can pick a specific cert
        for SNI on this hostname.

        Idempotent on hostname: an existing active row for the same
        apex bound to the same app is returned as success (matches
        ``addAppDomain``). A row bound to a different app returns
        CONFLICT — wildcard ownership is exclusive per apex.
        """
        # Org-scope the app lookup to the caller's tenant before creating a
        # CustomDomain + firing the validation workflow. Slugs are unique
        # only within an org. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        host = (input.hostname or "").strip().lower()
        if not host or "." not in host:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "hostname must be a fully-qualified domain (apex; the platform adds the ``*.`` prefix)",
                field="hostname",
            )
        # Defensive: refuse a leading ``*.`` — the apex is what we
        # store, and the wildcard SAN is implied. Otherwise we'd end
        # up persisting ``*.example.com`` as the hostname and the
        # renderer would emit ``*.*.example.com`` SANs.
        if host.startswith("*."):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "hostname must be the apex (e.g. ``example.com``); the wildcard ``*.`` prefix is implied",
                field="hostname",
            )

        method = (input.validation_method or "dns_01").lower()
        if method != "dns_01":
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "wildcard domains must use dns_01 validation (CA policy); HTTP-01 / DNS-TXT can't authorize wildcards",
                field="validationMethod",
            )

        sni_cert_ref = (input.sni_cert_ref or "").strip()

        existing = CustomDomain.objects.filter(hostname=host, deleted_at__isnull=True).first()
        if existing is not None:
            if existing.registered_app_id != app.id:
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    f"domain {host!r} already bound to another app",
                    field="hostname",
                )
            # Idempotent re-add: same app, same hostname. Promote to
            # wildcard if the existing row was a single-host (so a
            # caller upgrading from ``addAppDomain`` → ``addWildcard...``
            # converges on the wildcard contract). sni_cert_ref is
            # rewritten when the caller passes a non-empty value;
            # empty leaves the previous pin untouched (the resolver
            # has no surface for clearing a pin — that's intentional;
            # operators clear via ``removeAppDomain`` + re-add).
            update_fields: list[str] = []
            if not existing.is_wildcard:
                existing.is_wildcard = True
                update_fields.append("is_wildcard")
            if existing.validation_method != method:
                existing.validation_method = method
                update_fields.append("validation_method")
            if sni_cert_ref and existing.sni_cert_ref != sni_cert_ref:
                existing.sni_cert_ref = sni_cert_ref
                update_fields.append("sni_cert_ref")
            if update_fields:
                update_fields.extend(["updated_at", "version"])
                existing.save(update_fields=update_fields)
            return gql_success(app_domain_to_type(existing))

        from astrolift_clusters.models import ManagedDomain
        from astrolift_lifecycle.custom_domain_handshake import (
            build_handshake,
            hostname_parent_zone,
            resolve_cluster_ingress_target,
        )

        parent_zone = hostname_parent_zone(host)
        managed_zone = ManagedDomain.objects.filter(
            zone=parent_zone,
            deleted_at__isnull=True,
        ).first()
        cluster = getattr(app, "default_tenant_cluster", None)
        cluster_slug = cluster.slug if cluster is not None else "default"
        cname_target = resolve_cluster_ingress_target(
            cluster_slug=cluster_slug,
            managed_domain_zone=managed_zone.zone if managed_zone else None,
        )
        handshake = build_handshake(
            hostname=host,
            cluster_ingress_target=cname_target,
            is_platform_managed_zone=managed_zone is not None,
            validation_method=method,
        )

        domain = CustomDomain.objects.create(
            registered_app=app,
            hostname=host,
            validation_method=method,
            txt_challenge_token=handshake.txt_challenge_token,
            expected_cname_target=handshake.expected_cname_target,
            required_dns_records=[
                {
                    "kind": r.kind,
                    "name": r.name,
                    "value": r.value,
                    "ttl": r.ttl,
                    "propagated": r.propagated,
                    "last_checked_at": r.last_checked_at,
                    "message": r.message,
                }
                for r in handshake.required_records
            ],
            is_platform_managed_zone=handshake.is_platform_managed_zone,
            is_wildcard=True,
            sni_cert_ref=sni_cert_ref,
        )
        _kick_validate_custom_domain(domain)
        return gql_success(app_domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="app.domain.remove")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def remove_app_domain(
        self,
        info: Info,
        input: RemoveAppDomainInput,
    ) -> MutationResultType[_AppDomainRemovedPayload]:
        # Org-scope the by-guid lookup before the soft-delete side effect:
        # CustomDomain reaches the org via registered_app. Fails closed
        # (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        domain = CustomDomain.objects.filter(
            guid=str(input.id), deleted_at__isnull=True, registered_app__organization_id=org_id
        ).first()
        if domain is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "domain not found",
            )
        domain.soft_delete()
        return gql_success(
            _AppDomainRemovedPayload(
                id=input.id,
                deleted=True,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.domain.recheck")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def recheck_domain_validation(
        self,
        info: Info,
        input: RecheckDomainValidationInput,
    ) -> MutationResultType[AppDomainType]:
        """Fire ``ValidateCustomDomainWorkflow`` for the row (#397).

        Idempotent: re-firing the same workflow id joins the existing
        run rather than starting a parallel one. The workflow probes
        the authoritative nameservers, updates per-record propagation
        state on ``required_dns_records``, and transitions
        ``validation_status`` to ``validated`` or ``failed``.
        """
        # Org-scope the by-guid lookup before firing the validation
        # workflow: CustomDomain reaches the org via registered_app. Fails
        # closed (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        domain = CustomDomain.objects.filter(
            guid=str(input.id),
            deleted_at__isnull=True,
            registered_app__organization_id=org_id,
        ).first()
        if domain is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "domain not found",
            )
        _kick_validate_custom_domain(domain)
        return gql_success(app_domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="app.domain.upload_certificate")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def upload_custom_domain_certificate(
        self,
        info: Info,
        input: UploadCustomDomainCertificateInput,
    ) -> MutationResultType[AppDomainType]:
        """BYO-cert path for the unhappy-path UX (#397).

        When auto-issuance can't reach the cert (externally-managed
        AWS zone, LE rate-limited zone, custom CA), the operator
        pastes their PEM chain + key here. We store both, flip
        ``certificate_state`` to ``byo``, and the renderer picks up
        the uploaded PEM instead of an issued cert id.

        Basic shape validation only — we accept any PEM-looking
        payload at this layer; the renderer rejects malformed chains
        at apply time and reports back on the deployment status.
        """
        from datetime import datetime as _dt

        # Org-scope the by-guid lookup before writing the BYO cert bundle:
        # CustomDomain reaches the org via registered_app. Fails closed
        # (NOT_FOUND) when org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        domain = CustomDomain.objects.filter(
            guid=str(input.id),
            deleted_at__isnull=True,
            registered_app__organization_id=org_id,
        ).first()
        if domain is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "domain not found",
            )
        pem = (input.certificate_pem or "").strip()
        key = (input.private_key_pem or "").strip()
        if "-----BEGIN CERTIFICATE-----" not in pem:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "certificate_pem must contain at least one PEM CERTIFICATE block",
                field="certificatePem",
            )
        if "PRIVATE KEY" not in key:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "private_key_pem must be a PEM-encoded private key",
                field="privateKeyPem",
            )
        # Stash the PEM bundle (chain + key concatenated) onto the
        # row. The renderer reads ``byo_certificate_pem`` and emits a
        # platform-issued Secret/SecretSet to the runtime cluster.
        domain.byo_certificate_pem = pem + "\n" + key
        domain.byo_certificate_uploaded_at = _dt.now(tz=UTC)
        domain.certificate_state = CustomDomain.CertificateState.BYO
        domain.last_certificate_error = ""
        # Clear any auto-issued cert id — the BYO cert supersedes it.
        domain.certificate_id = ""
        domain.save(
            update_fields=[
                "byo_certificate_pem",
                "byo_certificate_uploaded_at",
                "certificate_state",
                "last_certificate_error",
                "certificate_id",
                "updated_at",
                "version",
            ],
        )
        return gql_success(app_domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="app.domain.redirects_updated")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_domain_redirects(
        self,
        info: Info,
        input: SetDomainRedirectsInput,
    ) -> MutationResultType[AppDomainType]:
        """Replace the redirect-rule set on a custom domain (#742).

        The FE renders the rules table as a single editable list; this
        mutation accepts the new full set and atomically:

        1. Soft-deletes every active row on the domain (preserves the
           audit trail via ``deleted_at``).
        2. Bulk-creates the new rows from ``input.rules``.

        The renderer reads the live set the next time the cluster's
        ingress is reconciled — the per-driver wiring (nginx
        ``server-snippet``, traefik middleware, ALB redirect-action)
        translates each rule into the cloud's redirect primitive.
        """
        valid_kinds = {k.value for k in DomainRedirectRule.Kind}
        valid_statuses = {s.value for s in DomainRedirectRule.HttpStatus}
        for idx, row in enumerate(input.rules):
            if row.kind not in valid_kinds:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"rules[{idx}].kind must be one of {sorted(valid_kinds)}",
                    field="rules",
                )
            if int(row.http_status) not in valid_statuses:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"rules[{idx}].httpStatus must be one of {sorted(valid_statuses)}",
                    field="rules",
                )
            # ``custom`` and ``alias`` rules need an explicit destination
            # — the renderer has nothing to derive from for these kinds.
            # ``http_to_https`` / ``apex_to_www`` / ``www_to_apex`` are
            # well-known and the renderer derives the target from the
            # parent domain's hostname.
            if (
                row.kind
                in (
                    DomainRedirectRule.Kind.CUSTOM.value,
                    DomainRedirectRule.Kind.ALIAS.value,
                )
                and not (row.destination_url or "").strip()
            ):
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"rules[{idx}].destinationUrl is required for kind={row.kind!r}",
                    field="rules",
                )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Fail closed without a tenant org (#1192): the domain is org-owned
        # via ``registered_app``, so a None org must not fall through to an
        # unscoped by-guid fetch — mirror the uniformly fail-closed #1183 shape.
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "domain not found")
        domain = (
            CustomDomain.objects.filter(
                guid=str(input.domain_id),
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
        )
        if domain is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "domain not found",
            )

        now = timezone.now()
        with transaction.atomic():
            DomainRedirectRule.objects.filter(
                custom_domain=domain,
                deleted_at__isnull=True,
            ).update(
                deleted_at=now,
                updated_at=now,
            )
            DomainRedirectRule.objects.bulk_create(
                [
                    DomainRedirectRule(
                        custom_domain=domain,
                        kind=row.kind,
                        source_pattern=(row.source_pattern or "").strip(),
                        destination_url=(row.destination_url or "").strip(),
                        http_status=int(row.http_status),
                        preserve_query_string=bool(row.preserve_query_string),
                        priority=int(row.priority),
                    )
                    for row in input.rules
                ],
            )
        return gql_success(app_domain_to_type(domain))

    @strawberry.field
    @mutation_audit(action="app.domain.path_routes_updated")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_domain_path_routes(
        self,
        info: Info,
        input: SetDomainPathRoutesInput,
    ) -> MutationResultType[AppDomainType]:
        """Replace the path-routing rule set on a custom domain (#740).

        Accepts the new full route set and atomically:

        1. Soft-deletes every active row on the domain.
        2. Bulk-creates the new rows from ``input.routes``.

        ``path_prefix`` must start with ``/``.  ``target_port`` must be
        in [1, 65535].  ``target_workload_slug`` is validated for
        non-emptiness here; workload existence is checked by the renderer
        at reconcile time.
        """
        for idx, row in enumerate(input.routes):
            if not (row.path_prefix or "").startswith("/"):
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"routes[{idx}].pathPrefix must start with '/'",
                    field="routes",
                )
            if not (1 <= int(row.target_port) <= 65535):
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"routes[{idx}].targetPort must be in [1, 65535]",
                    field="routes",
                )
            if not (row.target_workload_slug or "").strip():
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"routes[{idx}].targetWorkloadSlug is required",
                    field="routes",
                )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        # Fail closed without a tenant org (#1192): the domain is org-owned
        # via ``registered_app``, so a None org must not fall through to an
        # unscoped by-guid fetch — mirror the uniformly fail-closed #1183 shape.
        if org_id is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "domain not found")
        domain = (
            CustomDomain.objects.filter(
                guid=str(input.domain_id),
                registered_app__organization_id=org_id,
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
        )
        if domain is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "domain not found")

        now = timezone.now()
        with transaction.atomic():
            DomainPathRoute.objects.filter(
                custom_domain=domain,
                deleted_at__isnull=True,
            ).update(
                deleted_at=now,
                updated_at=now,
            )
            DomainPathRoute.objects.bulk_create(
                [
                    DomainPathRoute(
                        custom_domain=domain,
                        path_prefix=row.path_prefix.strip(),
                        target_workload_slug=row.target_workload_slug.strip(),
                        target_port=int(row.target_port),
                        strip_prefix=bool(row.strip_prefix),
                        priority=int(row.priority),
                    )
                    for row in input.routes
                ],
            )
        return gql_success(app_domain_to_type(domain))
