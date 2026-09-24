"""SecretMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from django.db import transaction
from django.utils import timezone
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.step_up import requires_elevation
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import (
    delete_app_env_key,
    parse_dotenv,
    read_app_env,
    set_app_env_keys,
)
from astrolift_manifest.parser import ManifestError
from astrolift_registry.models import RegisteredApp
from astrolift_registry.scopes import app_scope_by_slug
from astrolift_services.models import (
    AppSecretMetadata,
    SecretChangeProposal,
)
from astrolift_services.schema.mutations.helpers import (
    _VALID_SECRET_SOURCES,
    _actor_user,
    _app_secret_target_from_input,
    _caller_org_id,
    _client_ip,
    _maybe_create_proposal_for_write,
    _metadata_fields,
    _secret_write_payload,
    _stage_manifest,
    _upsert_app_secret_metadata,
    _validate_env_key,
    _validate_scope,
)
from astrolift_services.schema.mutations.types import (
    BulkImportAppSecretsInput,
    DeleteAppSecretInput,
    RevealAppSecretInput,
    RotateAppSecretInput,
    SetAppSecretInput,
    SetAppSecretMetadataInput,
    _AppSecretMetadataPayload,
    _AppSecretWritePayload,
    _BulkImportPayload,
)
from astrolift_services.schema.types import (
    RevealedSecretType,
)
from astrolift_services.secret_metadata_ops import current_secret_scope
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.optimistic import check_version_match as _check_version_match
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class SecretMutations:
    @strawberry.field
    @mutation_audit(action="app.secret.set", target=_app_secret_target_from_input)
    @requires_elevation(action_label="app.secret.set")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def set_app_secret(
        self,
        info: Info,
        input: SetAppSecretInput,
    ) -> MutationResultType[_AppSecretWritePayload]:
        validation_msg = _validate_env_key(input.key)
        if validation_msg:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                validation_msg,
                field="key",
            )
        scope_msg = _validate_scope(input.scope)
        if scope_msg:
            return gql_failure(ErrorCode.VALIDATION.value, scope_msg, field="scope")
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        # #497 — optimistic-concurrency gate. Refuse to apply the
        # secret write when the caller's cached version is stale; the
        # FE refetches and re-prompts the operator instead of silently
        # overwriting a concurrent edit (e.g. another admin updated a
        # different setting on the same app while this form was open).
        mismatch = _check_version_match(app, if_match_version=input.if_match_version, kind="App")
        if mismatch is not None:
            return mismatch
        # #488: when the app requires secret approval the mutation
        # creates a proposal instead of applying.  Returning the
        # proposal id in the same envelope shape (with empty raw
        # manifest staged) keeps the caller code stable — they switch
        # on ``pendingProposalId`` to decide which flow they're in.
        proposal = _maybe_create_proposal_for_write(
            app=app,
            op=SecretChangeProposal.Op.SET.value,
            payload=_secret_write_payload(input),
            info=info,
        )
        if proposal is not None:
            return gql_success(
                _AppSecretWritePayload(
                    app_slug=app.slug,
                    key=input.key,
                    raw_manifest_staged=app.manifest_raw_staged or "",
                    pending_proposal_id=GUID(str(proposal.guid)),
                )
            )
        source = app.manifest_raw_staged or app.manifest_raw or ""
        new_text = set_app_env_keys(source, {input.key: input.value})
        try:
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after edit: {exc}",
                field="rawManifest",
            )
        # #677 / #678 / #752 — refresh sidecar metadata on every direct
        # write.  The metadata row is keyed at the wildcard env scope ('')
        # for set/rotate writes since the mutation itself isn't env-bound;
        # operators add per-env overrides via setAppSecretMetadata.
        _upsert_app_secret_metadata(
            app=app,
            key=input.key,
            environment_name="",
            expires_at=input.expires_at,
            set_via=input.set_via,
            scope=input.scope,
            actor=_actor_user(info),
        )
        return gql_success(
            _AppSecretWritePayload(
                app_slug=app.slug,
                key=input.key,
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.rotate", target=_app_secret_target_from_input)
    @requires_elevation(action_label="app.secret.rotate")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def rotate_app_secret(
        self,
        info: Info,
        input: RotateAppSecretInput,
    ) -> MutationResultType[_AppSecretWritePayload]:
        """Rotate one literal env value on the staged manifest (#726).

        Wire-identical to :meth:`set_app_secret`: same staging buffer,
        same validation, same optimistic-concurrency gate, same proposal
        flow when the app requires secret approval. Differs only in the
        audit action (``app.secret.rotate`` vs ``app.secret.set``) so
        SRE can distinguish credential lifecycle events from edits in
        the audit timeline.
        """
        validation_msg = _validate_env_key(input.key)
        if validation_msg:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                validation_msg,
                field="key",
            )
        scope_msg = _validate_scope(input.scope)
        if scope_msg:
            return gql_failure(ErrorCode.VALIDATION.value, scope_msg, field="scope")
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        mismatch = _check_version_match(app, if_match_version=input.if_match_version, kind="App")
        if mismatch is not None:
            return mismatch
        proposal = _maybe_create_proposal_for_write(
            app=app,
            op=SecretChangeProposal.Op.SET.value,
            payload=_secret_write_payload(input),
            info=info,
        )
        if proposal is not None:
            return gql_success(
                _AppSecretWritePayload(
                    app_slug=app.slug,
                    key=input.key,
                    raw_manifest_staged=app.manifest_raw_staged or "",
                    pending_proposal_id=GUID(str(proposal.guid)),
                )
            )
        source = app.manifest_raw_staged or app.manifest_raw or ""
        new_text = set_app_env_keys(source, {input.key: input.value})
        try:
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after rotate: {exc}",
                field="rawManifest",
            )
        # #677 / #678 / #752 — refresh sidecar metadata on rotate as well.
        _upsert_app_secret_metadata(
            app=app,
            key=input.key,
            environment_name="",
            expires_at=input.expires_at,
            set_via=input.set_via,
            scope=input.scope,
            actor=_actor_user(info),
        )
        return gql_success(
            _AppSecretWritePayload(
                app_slug=app.slug,
                key=input.key,
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.delete", target=_app_secret_target_from_input)
    @requires_elevation(action_label="app.secret.delete")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def delete_app_secret(
        self,
        info: Info,
        input: DeleteAppSecretInput,
    ) -> MutationResultType[_AppSecretWritePayload]:
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        source = app.manifest_raw_staged or app.manifest_raw or ""
        # Validate the key exists BEFORE creating a proposal — no point
        # gating a delete-of-nothing through review.
        _, removed = delete_app_env_key(source, input.key)
        if not removed:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"key {input.key!r} not present in [env]",
                field="key",
            )
        # #488: gate on approval policy after the existence check.
        proposal = _maybe_create_proposal_for_write(
            app=app,
            op=SecretChangeProposal.Op.DELETE.value,
            payload={"key": input.key},
            info=info,
        )
        if proposal is not None:
            return gql_success(
                _AppSecretWritePayload(
                    app_slug=app.slug,
                    key=input.key,
                    raw_manifest_staged=app.manifest_raw_staged or "",
                    pending_proposal_id=GUID(str(proposal.guid)),
                )
            )
        new_text, _ = delete_app_env_key(source, input.key)
        try:
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after delete: {exc}",
                field="rawManifest",
            )
        # #677 / #678 — soft-delete metadata rows for the removed key so
        # a re-add later doesn't silently re-use stale expiry / source.
        # Soft delete keeps the row available for the audit trail.
        actor = _actor_user(info)
        for row in AppSecretMetadata.objects.filter(
            registered_app=app,
            key=input.key,
            deleted_at__isnull=True,
        ):
            row.soft_delete(by=actor)
        return gql_success(
            _AppSecretWritePayload(
                app_slug=app.slug,
                key=input.key,
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.metadata.set", target=_app_secret_target_from_input)
    @requires_elevation(action_label="app.secret.metadata.set")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def set_app_secret_metadata(
        self,
        info: Info,
        input: SetAppSecretMetadataInput,
    ) -> MutationResultType[_AppSecretMetadataPayload]:
        """Set / clear operator-facing metadata for one secret literal
        (#677 / #678).

        The underlying value is never touched — this mutation only
        edits the sidecar ``AppSecretMetadata`` row.  Permission gate
        is ``app.update`` (same as set/rotate) since the metadata feeds
        the secret-row UI and an operator who can edit the app should
        be allowed to annotate its secrets.

        ``expires_at=null`` on an existing row preserves the current
        deadline.  To clear the deadline pass a value of ``None`` with
        ``set_via='clear'`` — reserved for future expansion when an
        explicit clear semantics is needed (current FE only sets +
        refreshes; it never clears).

        Scope decides which environments receive the value, so this is a
        secret write, not just an annotation: it needs a fresh elevation
        like set/rotate, and with secret approval on a scope change waits
        on a proposal (#1946)."""
        msg = _validate_env_key(input.key)
        if msg:
            return gql_failure(ErrorCode.VALIDATION.value, msg, field="key")
        scope_msg = _validate_scope(input.scope)
        if scope_msg:
            return gql_failure(ErrorCode.VALIDATION.value, scope_msg, field="scope")
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        if input.set_via is not None and input.set_via not in _VALID_SECRET_SOURCES:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown set_via value {input.set_via!r}; allowed: {sorted(_VALID_SECRET_SOURCES)}",
                field="setVia",
            )
        environment_name = input.environment_name or ""
        if (
            environment_name
            and not AppEnvironment.objects.filter(
                registered_app=app, name=environment_name, deleted_at__isnull=True
            ).exists()
        ):
            # A row for a name the app does not have would govern whatever
            # environment later takes that name, a preview included.
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {environment_name!r} not found",
                field="environmentName",
            )
        with transaction.atomic():
            # The scope check below and the write it decides must see one
            # state. An approved scope change holds this row lock while it
            # applies: a set or delete through its staged-manifest write, a
            # set_metadata in apply_proposal. Locking the key's app-wide
            # metadata row would miss a key only the repo has set, which
            # has no such row until the change creates it.
            app = RegisteredApp.objects.select_for_update(no_key=True).filter(pk=app.pk).first()
            if app is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
            if app.requires_secret_approval and input.scope is not None:
                current_scope = current_secret_scope(app, input.key, environment_name)
                if input.scope != current_scope:
                    proposal = _maybe_create_proposal_for_write(
                        app=app,
                        op=SecretChangeProposal.Op.SET_METADATA.value,
                        payload={"key": input.key, **_metadata_fields(input)},
                        environment_name=environment_name,
                        info=info,
                    )
                    existing = AppSecretMetadata.objects.filter(
                        registered_app=app,
                        environment_name=environment_name,
                        key=input.key,
                        deleted_at__isnull=True,
                    ).first()
                    return gql_success(
                        _AppSecretMetadataPayload(
                            app_slug=app.slug,
                            key=input.key,
                            environment_name=environment_name,
                            expires_at=existing.expires_at if existing else None,
                            set_via=existing.source if existing else "",
                            set_at=existing.set_at if existing else None,
                            scope=current_scope,
                            pending_proposal_id=GUID(str(proposal.guid)),
                        )
                    )
            row = _upsert_app_secret_metadata(
                app=app,
                key=input.key,
                environment_name=environment_name,
                expires_at=input.expires_at,
                set_via=input.set_via,
                scope=input.scope,
                actor=_actor_user(info),
            )
            # A per-environment row may store no scope and follow the
            # app-wide one; report the scope the key actually has here.
            scope = current_secret_scope(app, row.key, row.environment_name)
        return gql_success(
            _AppSecretMetadataPayload(
                app_slug=app.slug,
                key=row.key,
                environment_name=row.environment_name,
                expires_at=row.expires_at,
                set_via=row.source,
                set_at=row.set_at,
                scope=scope,
            )
        )

    @strawberry.field
    @mutation_audit(action="app.secret.bulk_import")
    @requires_elevation(action_label="app.secret.bulk_import")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_slug("input.app_slug"))
    @tenant_scoped()
    def bulk_import_app_secrets(
        self,
        info: Info,
        input: BulkImportAppSecretsInput,
    ) -> MutationResultType[_BulkImportPayload]:
        """Parse a .env paste and stage every key at once.

        Creates no secret-change proposal, even when the app requires
        secret approval. The deploy path
        (``astrolift_services.secret_literals``) is what keeps imported
        keys out of workloads until an applied proposal matches each
        one (#1758).
        """
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        kvs = parse_dotenv(input.dotenv_text or "")
        if not kvs:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "no valid KEY=value lines parsed from input",
                field="dotenvText",
            )
        # Validate all keys before staging so a single bad name
        # doesn't write a half-applied result.
        for key in kvs:
            msg = _validate_env_key(key)
            if msg:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    msg,
                    field="dotenvText",
                )
        source = app.manifest_raw_staged or app.manifest_raw or ""
        new_text = set_app_env_keys(source, kvs)
        try:
            staged = _stage_manifest(app, new_text, actor=_actor_user(info))
        except ManifestError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"manifest parse failed after bulk import: {exc}",
                field="rawManifest",
            )
        # #678 — tag every imported key as `env_paste` so the secret-row
        # tooltip can render "Set via .env paste on <date>".  Operators
        # then bulk-rotate via the dedicated UI; the source flips on
        # the next set/rotate.
        actor = _actor_user(info)
        for key in kvs:
            _upsert_app_secret_metadata(
                app=app,
                key=key,
                environment_name="",
                set_via=AppSecretMetadata.Source.ENV_PASTE.value,
                actor=actor,
            )
        return gql_success(
            _BulkImportPayload(
                app_slug=app.slug,
                keys_set=sorted(kvs.keys()),
                raw_manifest_staged=staged,
            )
        )

    @strawberry.field
    @mutation_audit(
        action="app.secret.reveal",
        extras=lambda result: (
            {
                "secret_id": result.data.secret_id,
                "key": result.data.key,
                "environment_name": result.data.environment_name,
            }
            if result.ok and result.data is not None
            else None
        ),
    )
    @requires_elevation(action_label="app.secret.reveal")
    @require_permission(
        Permission.APP_READ, Permission.SECRET_READ, scope=app_scope_by_slug("input.app_slug")
    )
    @tenant_scoped()
    def reveal_app_secret(
        self,
        info: Info,
        input: RevealAppSecretInput,
    ) -> MutationResultType[RevealedSecretType]:
        """Return plaintext for one secret row (#424).

        Requires both `app.read` (the caller can see the app) and
        `secret.read` (the caller can disclose the value). Plaintext
        is returned **only** for `literal`-source secrets — bundle and
        managed-service values live in the platform secrets backend
        and aren't reachable from the API surface.

        Every successful reveal lands in the audit log via the
        wrapping `@mutation_audit` decorator (actor + IP + tenant +
        target secret id). Failures audit-log too with the failure
        code so the trail captures attempted disclosures.
        """
        # Stable id format: ``{source}:{env}:{key}``. ``env`` is a
        # POSIX-shape env name (no colons); ``key`` is too for
        # literal secrets. Split on the first two colons so a key
        # that contains a colon (managed-service style ``svc.k``)
        # survives reassembly even though we won't reveal those.
        secret_id = input.secret_id or ""
        parts = secret_id.split(":", 2)
        if len(parts) != 3 or not all(parts):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "secretId must look like 'literal:<env>:<KEY>'",
                field="secretId",
            )
        source, env_name, key = parts
        if source != "literal":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                (
                    f"reveal is only supported for literal-source secrets; "
                    f"{source!r} values live in the platform secrets backend "
                    f"and aren't reachable from the API"
                ),
                field="secretId",
            )
        app = RegisteredApp.objects.filter(slug=input.app_slug, organization_id=_caller_org_id()).first()
        if app is None or app.deleted_at is not None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appSlug")
        env_exists = AppEnvironment.objects.filter(
            registered_app=app,
            name=env_name,
            deleted_at__isnull=True,
        ).exists()
        if not env_exists:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"environment {env_name!r} not found",
                field="secretId",
            )
        raw_text = app.manifest_raw_staged or app.manifest_raw or ""
        literals = read_app_env(raw_text)
        if key not in literals:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                f"no literal secret {key!r} in environment {env_name!r}",
                field="secretId",
            )
        # @mutation_audit (above) already records actor + tenant +
        # secret_id via its extras hook. Emit a sibling audit entry
        # carrying the client IP so the trail captures the disclosure
        # source — done as a separate emit_audit call so the IP isn't
        # surfaced in the GraphQL response payload (which would leak
        # the caller's IP back to a layered proxy).
        ip = _client_ip(info)
        tenant = get_current_tenant()
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="app.secret.reveal.disclosure",
                decision="ALLOW",
                target_kind="app_secret",
                target_id=secret_id,
                duration_ms=0,
                permissions=(
                    Permission.APP_READ.value,
                    Permission.SECRET_READ.value,
                ),
                extra={
                    "app_slug": input.app_slug,
                    "environment_name": env_name,
                    "key": key,
                    "client_ip": ip,
                },
            )
        )
        return gql_success(
            RevealedSecretType(
                secret_id=secret_id,
                key=key,
                environment_name=env_name,
                value=literals[key],
                revealed_at=timezone.now(),
            )
        )
