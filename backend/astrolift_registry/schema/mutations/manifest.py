"""ManifestMutations — split from the monolithic mutations module."""

from __future__ import annotations

import logging

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_manifest.persist import actor_may_attach_project_services, allow_project_attach
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations.types import (
    ApplyStagedManifestInput,
    PushManifestToRepoInput,
    SyncManifestFromRepoInput,
    UpdateManifestInput,
    _ManifestPushPayload,
    _ManifestStagePayload,
)
from astrolift_registry.scopes import app_scope_by_guid
from core.decorators import tenant_scoped
from core.mutations import AuditEntry, ErrorCode, emit_audit, mutation_audit
from core.permissions import (
    Permission,
    PermissionDenied,
    PermissionScope,
    ScopeKind,
    check_permission,
    require_permission,
)
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


def _audit_secret_change(
    app: RegisteredApp,
    *,
    decision: str,
    changed_keys: list[str],
    managed_service_changes: list[str] | None = None,
    unapproved_keys: list[str] | None = None,
    proposal_ids: list[str] | None = None,
    action: str = "app.manifest.apply_secret_change",
) -> None:
    """Sibling audit entry for a secret change through ``applyStagedManifest``
    (#1759 adversarial review, H1): env keys and managed-service bindings.

    ``@mutation_audit`` (on the resolver) already records one entry per
    call, but it doesn't know about manifest-specific detail -- same
    reason ``app.secret.reveal.disclosure`` and ``_emit_deny_audit`` emit
    their own sibling entry rather than stretching the generic one.
    Carries **names** only, never values and no digest of the manifest
    text either: an unkeyed hash of text that holds a secret lets anyone
    who can read the audit log confirm a guess at it offline.
    ``unapproved_keys`` / ``proposal_ids`` say why an app that requires
    secret approval was refused, or which applied proposals allowed it.
    """
    extra: dict[str, list[str]] = {"changed_keys": changed_keys}
    for key, value in (
        ("managed_service_changes", managed_service_changes),
        ("unapproved_keys", unapproved_keys),
        ("proposal_ids", proposal_ids),
    ):
        if value is not None:
            extra[key] = value
    tenant = get_current_tenant()
    try:
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action=action,
                decision=decision,
                target_kind="RegisteredApp",
                target_id=str(app.guid),
                duration_ms=0,
                permissions=(Permission.APP_UPDATE.value,),
                extra=extra,
            )
        )
    except Exception:  # noqa: BLE001 -- audit emission must never break the caller
        log.exception("apply_staged_manifest: audit emit failed for app=%s", app.guid)


class _RollBack(Exception):
    """Unwinds a savepoint whose writes must not survive, carrying its result out."""

    def __init__(self, value=None):
        super().__init__()
        self.value = value


def _dry_run_persist(app: RegisteredApp, manifest, *, raw_text: str):
    """What ``persist_manifest`` would change, with every write rolled back.

    Managed-service effects depend on database state the manifest text
    does not show: which environments exist, which attachments a project
    admin detached (reconcile re-creates a declared one). So the gate
    reads them off a real reconcile in a savepoint rather than a text
    diff. ``on_commit`` callbacks queued inside the savepoint (provision /
    deprovision workflow starts) are discarded with it.
    """
    from django.db import transaction

    from astrolift_manifest.persist import persist_manifest

    try:
        with transaction.atomic():
            with allow_project_attach(True):
                raise _RollBack(persist_manifest(app, manifest, raw_text=raw_text))
    except _RollBack as rolled_back:
        # persist_manifest updated the in-memory row too.
        app.refresh_from_db()
        return rolled_back.value


def _gate_secret_change(info: Info, app: RegisteredApp, input, env, persisted):
    """``(refusal, allow_audit)`` for a staged edit (#1759).

    A refusal envelope when it may not apply. Otherwise the fields of the
    ALLOW audit entry, which the caller writes once the apply has actually
    landed, or None when the edit changes nothing secret.

    A secret change is an env change (``env``, from
    ``astrolift_manifest.env_diff``), a managed-service binding the
    reconcile would create, change or release
    (``persisted.managed_service_changes``), or a managed service the app
    declares but can't reconcile yet because it has no environment
    (``persisted.managed_services_deferred``). Registration's environment
    bootstrap reconciles those later with nobody to check, so they are
    gated now as if they were being attached. Every secret change needs
    the ``expectedStagedHash`` the caller reviewed and an audit entry, and:

    * creating or rebinding an attachment to a project managed service,
      or declaring one on an app with no environment, needs project.update
      on the app's project, the permission attachProjectManagedService
      checks. APP_UPDATE alone would otherwise put a shared project
      database's credentials in a pod whose command the caller controls;
    * when the app requires secret approval, each changed ``[env]`` key
      needs an applied proposal that produced exactly that change from the
      value the key still has in ``manifest_raw`` (#1758's base stamp, via
      ``secret_literals.approved_literal_changes``, the rule the deploy
      path applies to staged literals). Container env, any
      managed-service change that isn't a release (remove / detach) and
      any deferred declaration are refused, since no proposal can carry
      them and every one of them can put a service's credentials in front
      of a workload. A release only takes credentials away, so it goes
      through with elevation;
    * otherwise the change needs a fresh session elevation.
    """
    from astrolift_identity.step_up import check_elevation
    from astrolift_manifest.persist import RELEASE_ACTIONS
    from astrolift_services.secret_literals import approved_literal_changes

    services = [f"{action} {target}" for action, target in persisted.managed_service_changes]
    declared = [
        f"declare {service.owner_scope}:{service.kind}/{(service.name or service.kind).strip()}"
        f"@{service.environment}"
        for service in persisted.managed_services_deferred
    ]
    if not env and not services and not declared:
        return None, None
    audit: dict = {"changed_keys": [change.label for change in env]}
    if services or declared:
        audit["managed_service_changes"] = services + declared

    # The hash pins the apply to the exact buffer the caller reviewed; for
    # a secret change that can't be optional.
    if not input.expected_staged_hash:
        return (
            gql_failure(
                ErrorCode.VALIDATION.value,
                "this edit changes env or managed-service bindings -- pass the "
                "rawManifestStagedHash you reviewed as expectedStagedHash",
                field="expectedStagedHash",
            ),
            None,
        )

    if (
        persisted.managed_service_attachments_created
        or persisted.managed_service_attachments_updated
        or any(service.owner_scope == "project" for service in persisted.managed_services_deferred)
    ):
        try:
            if app.project_id is None:
                raise PermissionDenied(Permission.PROJECT_UPDATE, None, "the app belongs to no project")
            check_permission(
                Permission.PROJECT_UPDATE,
                scope=PermissionScope(kind=ScopeKind.PROJECT, id=app.project_id),
            )
        except PermissionDenied:
            _audit_secret_change(app, decision="DENY", **audit)
            return (
                gql_failure(
                    ErrorCode.PERMISSION_DENIED.value,
                    "attaching a project managed service to this app needs project.update on "
                    "its project -- a project admin can attach it with attachProjectManagedService",
                ),
                None,
            )

    needs_elevation = True
    if app.requires_secret_approval:
        match = approved_literal_changes(
            app,
            {change.key: change.after for change in env if change.app_wide},
        )
        unapproved = (
            match.unapproved
            + [change.label for change in env if not change.app_wide]
            + [
                f"{action} {target}"
                for action, target in persisted.managed_service_changes
                if action not in RELEASE_ACTIONS
            ]
            + declared
        )
        if unapproved:
            _audit_secret_change(app, decision="DENY", unapproved_keys=unapproved, **audit)
            return (
                gql_failure(
                    "SECRET_APPROVAL_REQUIRED",
                    "this app requires secret approval and nothing approved these changes: "
                    f"{', '.join(unapproved)} -- change [env] through setAppSecret/rotateAppSecret/"
                    "deleteAppSecret and apply once approved; container, job and task env and "
                    "managed-service bindings can't be approved through a proposal",
                ),
                None,
            )
        audit["proposal_ids"] = match.proposal_ids
        # What is left of the managed-service changes only releases
        # bindings; nothing approves those, so they need elevation.
        needs_elevation = bool(services)

    if needs_elevation:
        deny = check_elevation(
            info,
            action_label="app.manifest.apply_secret_change",
            resolver_name="ManifestMutations.apply_staged_manifest",
        )
        if deny is not None:
            _audit_secret_change(app, decision="DENY", **audit)
            return deny, None
    return None, audit


@strawberry.type
class ManifestMutations:
    @strawberry.field
    @mutation_audit(action="app.update_manifest")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_guid("input.id"))
    @tenant_scoped()
    def update_manifest(
        self,
        info: Info,
        input: UpdateManifestInput,
    ) -> MutationResultType[_ManifestStagePayload]:
        """Stage a manifest edit. Writes to ``manifest_raw_staged``.

        Validates the TOML parses before staging — bad TOML never
        lands in the buffer. Empty input clears the staging buffer.

        An ``[env]`` edit staged here creates no secret-change proposal,
        even when the app requires secret approval. The deploy path
        (``astrolift_services.secret_literals``) is what keeps such an
        edit out of workloads until an applied proposal matches it
        (#1758). Without secret approval the staged value is what deploys,
        so changing one needs a fresh step-up, as setAppSecret does (#1976).
        """
        from astrolift_manifest.env_edit import redact_env_values, resolve_masked_env_values
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.sync_state import (
            SyncSnapshot,
            classify_state,
        )
        from astrolift_services.secret_visibility import can_reveal_app_secrets, redacted_manifest_text

        # Org-scope the by-guid lookup to the caller's tenant before staging
        # the manifest edit. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        # A caller without secret.read + elevation reads [env] masked and the
        # editor saves the whole document back, so every masked value must be
        # put back from the text that read came from (staged, else raw) or the
        # save would replace each secret with the placeholder (#1920).
        text, unresolved_keys = resolve_masked_env_values(
            input.raw_manifest or "",
            fallback_text=app.manifest_raw_staged or app.manifest_raw or "",
        )
        if unresolved_keys:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "rawManifest contains a masked placeholder for key(s) with no stored value "
                f"to restore: {', '.join(unresolved_keys)}",
                field="rawManifest",
            )
        if text.strip():
            try:
                parse_raw(text)
            except ManifestError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"manifest parse failed: {exc}",
                    field="rawManifest",
                )

        # Identity: if the staged content matches the synced content,
        # clear the staging buffer rather than carrying a redundant
        # copy. A caller who cannot reveal secrets must not learn
        # anything from whether this clears: comparing the *restored*
        # text (real [env] literals) against manifest_raw would confirm
        # a guessed value the instant it happened to match the stored
        # one, even though the response itself is masked either way.
        # Such a caller only gets the shortcut when their own
        # submission -- before restoration -- is byte-identical to
        # their masked view of the synced manifest; that proves nothing
        # changed without ever comparing a guess to a real value (#1944).
        if can_reveal_app_secrets(info, app=app):
            identical = text == (app.manifest_raw or "")
        else:
            identical = (input.raw_manifest or "") == redact_env_values(app.manifest_raw or "")
        staged = "" if identical else text

        # Without secret approval a deploy takes [env] literals straight from
        # the staged buffer (secret_literals._deployable_literals), so staging
        # a changed value changes what the next deploy puts in front of a
        # workload. That is the change setAppSecret/rotateAppSecret gate on
        # step-up; so does this (#1976). With approval on, the deploy path
        # already keeps an unapproved staged value out.
        if not app.requires_secret_approval:
            from astrolift_identity.step_up import check_elevation
            from astrolift_manifest.env_diff import env_changes

            deployable_before = app.manifest_raw_staged or app.manifest_raw or ""
            deployable_after = staged or app.manifest_raw or ""
            changed = [
                change.label for change in env_changes(deployable_before, deployable_after) if change.app_wide
            ]
            if changed:
                deny = check_elevation(
                    info,
                    action_label="app.manifest.stage_secret_change",
                    resolver_name="ManifestMutations.update_manifest",
                )
                if deny is not None:
                    _audit_secret_change(
                        app, decision="DENY", changed_keys=changed, action="app.manifest.stage_secret_change"
                    )
                    return deny
                _audit_secret_change(
                    app, decision="ALLOW", changed_keys=changed, action="app.manifest.stage_secret_change"
                )
        app.manifest_raw_staged = staged
        app.save(
            update_fields=[
                "manifest_raw_staged",
                "updated_at",
                "version",
            ]
        )

        sync_state = classify_state(
            SyncSnapshot(
                db_hash=app.manifest_hash or "",
                repo_hash=app.last_synced_hash or app.manifest_hash or "",
                last_synced_hash=app.last_synced_hash or "",
            )
        )
        return gql_success(
            _ManifestStagePayload(
                id=input.id,
                sync_state=sync_state.value,
                raw_manifest=redacted_manifest_text(info, app=app, raw_text=app.manifest_raw or ""),
                raw_manifest_staged=redacted_manifest_text(
                    info, app=app, raw_text=app.manifest_raw_staged or ""
                ),
            )
        )

    @strawberry.field
    @mutation_audit(action="app.apply_staged_manifest")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_guid("input.id"))
    @tenant_scoped()
    def apply_staged_manifest(
        self,
        info: Info,
        input: ApplyStagedManifestInput,
    ) -> MutationResultType[_ManifestStagePayload]:
        """Apply ``manifest_raw_staged`` straight to ``manifest_raw`` (#1759).

        ``updateManifest`` only ever writes the staging buffer --
        ``pushManifestToRepo`` (open a PR) and ``syncManifestFromRepo``
        (pull + apply) are the only paths that move a draft into
        ``manifest_raw``. A repo-backed app always goes through one of
        those for review, whatever its connection health (adversarial
        review, #1759); this mutation is reachable only for an app with
        no ``source_repo`` at all -- the ``--manifest-raw`` registration
        shape, which otherwise had no way to ever change its manifest
        after the first staged edit (the only way out was deregister,
        which tears down the namespace + registry repo, and register
        again).

        An edit that changes env or a managed-service binding is gated the
        same way ``setAppSecret`` gates a direct secret write (adversarial
        review; ``_gate_secret_change`` has the rules). "Env" is the
        top-level ``[env]`` table and every container, job and task env
        table, since container env reaches the pod too and outranks
        ``[env]`` there (``astrolift_manifest.env_diff``). A binding is any
        managed service or project attachment the reconcile would create,
        change or release, read off a rolled-back dry run; attaching a
        project managed service also needs project.update on the project.
        Without this, this mutation would be a permission-only bypass of
        the review every other secret-literal write path sits behind, and
        of the permission ``attachProjectManagedService`` checks.

        Runs under ``select_for_update()`` so a concurrent
        ``updateManifest`` / ``applyStagedManifest`` on the same app
        can't interleave with this read-modify-write.
        ``expected_staged_hash`` detects a stale read of the staged
        buffer and refuses with ``CONFLICT`` rather than applying a draft
        the caller never actually reviewed. It is required when the edit
        changes env and optional otherwise.

        Reuses the same parse + ``persist_manifest`` path ``registerApp``
        takes for an inline ``manifest_raw`` -- one apply implementation,
        whether it runs at registration or from the editor.
        """
        from django.db import transaction
        from django.utils.crypto import constant_time_compare

        from astrolift_manifest.env_diff import env_changes
        from astrolift_manifest.normalize import NormalizationDefaults, normalize
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.persist import persist_manifest
        from astrolift_manifest.sync_state import SyncSnapshot, classify_state
        from astrolift_registry.services.staged_manifest import staged_manifest_hash
        from astrolift_services.secret_visibility import redacted_manifest_text

        def _result(current: RegisteredApp) -> MutationResultType[_ManifestStagePayload]:
            sync_state = classify_state(
                SyncSnapshot(
                    db_hash=current.manifest_hash or "",
                    repo_hash=current.last_synced_hash or current.manifest_hash or "",
                    last_synced_hash=current.last_synced_hash or "",
                )
            )
            # Masked for a caller who can't reveal secrets, like every other
            # manifest-text response: APP_UPDATE alone runs this mutation,
            # and the echo must not hand back the [env] values (#1920).
            return gql_success(
                _ManifestStagePayload(
                    id=input.id,
                    sync_state=sync_state.value,
                    raw_manifest=redacted_manifest_text(
                        info, app=current, raw_text=current.manifest_raw or ""
                    ),
                    raw_manifest_staged=redacted_manifest_text(
                        info, app=current, raw_text=current.manifest_raw_staged or ""
                    ),
                )
            )

        # Org-scope the by-guid lookup to the caller's tenant before applying
        # the staged manifest. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        with transaction.atomic():
            app = (
                RegisteredApp.objects.select_for_update()
                .filter(guid=str(input.id), organization_id=org_id)
                .first()
            )
            if app is None:
                return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

            if app.archived_at is not None:
                return gql_failure(
                    "APP_ARCHIVED",
                    "app is archived -- unarchive it before applying a manifest change",
                )

            # H2 (adversarial review): a repo-backed app always pushes
            # through pushManifestToRepo for review, whatever its
            # connection health -- only an app with no source_repo at
            # all has nothing else to apply the staged edit through.
            if app.source_repo:
                return gql_failure(
                    "SCM_REPO_CONFIGURED",
                    "this app pushes changes through its source repo -- use "
                    "pushManifestToRepo so the change goes through review",
                )

            staged = app.manifest_raw_staged or ""

            if input.expected_staged_hash and not constant_time_compare(
                input.expected_staged_hash, staged_manifest_hash(app)
            ):
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    "the staged manifest changed since you loaded it -- refresh and try again",
                )

            if not staged.strip():
                # Nothing staged -- a no-op success, same spirit as
                # pushManifestToRepo's 'nothing_to_push'.
                return _result(app)

            if staged == (app.manifest_raw or ""):
                # The staged text already matches what's applied -- clear
                # the redundant copy (same convention updateManifest uses
                # for an edit that converges back to the synced content)
                # instead of leaving it to read as a perpetual "staged,
                # not applied".
                app.manifest_raw_staged = ""
                app.save(update_fields=["manifest_raw_staged", "updated_at", "version"])
                return _result(app)

            try:
                manifest = normalize(parse_raw(staged), defaults=NormalizationDefaults())
            except ManifestError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"manifest parse failed: {exc}",
                    field="rawManifest",
                )

            # H1 (adversarial review): env values and managed-service
            # bindings are secret changes, wherever in the manifest they sit.
            env = env_changes(app.manifest_raw or "", staged)
            try:
                dry_run = _dry_run_persist(app, manifest, raw_text=staged)
            except ValueError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"managed services: {exc}",
                    field="rawManifest",
                )
            refusal, allowed = _gate_secret_change(info, app, input, env, dry_run)
            if refusal is not None:
                return refusal

            try:
                with transaction.atomic():
                    with allow_project_attach(True):
                        persisted = persist_manifest(app, manifest, raw_text=staged)
                    # The gate cleared the dry run's effects; a write that
                    # landed in between must not widen them unchecked.
                    if (
                        sorted(persisted.managed_service_changes) != sorted(dry_run.managed_service_changes)
                        or persisted.managed_services_deferred != dry_run.managed_services_deferred
                    ):
                        raise _RollBack()
            except _RollBack:
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    "managed services changed while this apply ran -- refresh and try again",
                )
            except ValueError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"managed services: {exc}",
                    field="rawManifest",
                )
            app.manifest_raw_staged = ""
            # There is no repo anchor for a connection-less app -- there
            # is no repo at all, so last_synced_hash is left alone rather
            # than set to a hash no repo ever had (adversarial review).
            # _result()'s "or manifest_hash" fallback (mirroring
            # ``astrolift_registry.schema.types._repo_hash_for``'s own
            # convention for "no known repo hash") already reads this as
            # in_sync.
            app.save(update_fields=["manifest_raw_staged", "updated_at", "version"])
            # Only now: an ALLOW entry for an apply that then failed would
            # record a secret change that never happened.
            if allowed is not None:
                _audit_secret_change(app, decision="ALLOW", **allowed)

            return _result(app)

    @strawberry.field
    @mutation_audit(action="app.sync_manifest_from_repo")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_guid("input.id"))
    @tenant_scoped()
    def sync_manifest_from_repo(
        self,
        info: Info,
        input: SyncManifestFromRepoInput,
    ) -> MutationResultType[_ManifestStagePayload]:
        """Re-fetch the manifest from the source repo + recompute
        the hash anchor.

        Discards any staged edits — sync is destructive on purpose,
        the UI is expected to confirm before calling.

        Delegates the SCM-side fetch + parse + apply to
        :func:`resync_app_manifest_from_repo` so the diff-and-apply
        path stays a single implementation. The service already
        handles the four outcome shapes (``in_sync`` / ``applied`` /
        ``diverged`` / ``fetch_failed``); this resolver maps them
        onto the GraphQL envelope.
        """
        from astrolift_manifest.sync_state import (
            SyncSnapshot,
            classify_state,
        )
        from astrolift_registry.services.manifest_sync import (
            resync_app_manifest_from_repo,
        )
        from astrolift_services.secret_visibility import redacted_manifest_text

        # Org-scope the by-guid lookup to the caller's tenant before the
        # repo re-fetch + apply (SCM call). Fails closed (NOT_FOUND) when
        # org_id is None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        # Old behaviour: drop staged buffer + reset the anchor without
        # re-reading the repo. Replaced with a real fetch via the SCM
        # provider so the manifest_raw + hash actually mirror the
        # repo's content. Issue #536.
        with allow_project_attach(actor_may_attach_project_services(app)):
            result = resync_app_manifest_from_repo(app)
        if result.status == "fetch_failed":
            return gql_failure(
                "SCM_FETCH_FAILED",
                result.error or "couldn't reach the source repo",
            )
        if result.status == "diverged":
            return gql_failure(
                "SCM_DIVERGED",
                result.error or "staged drafts would be clobbered by repo content; push or discard first",
            )

        # ``applied`` / ``in_sync`` both leave the DB in a coherent
        # state. ``resync_app_manifest_from_repo`` already cleared the
        # staging buffer where appropriate; mirror its final view.
        app.refresh_from_db()
        sync_state = classify_state(
            SyncSnapshot(
                db_hash=app.manifest_hash or "",
                repo_hash=app.last_synced_hash or "",
                last_synced_hash=app.last_synced_hash or "",
            )
        )
        return gql_success(
            _ManifestStagePayload(
                id=input.id,
                sync_state=sync_state.value,
                raw_manifest=redacted_manifest_text(info, app=app, raw_text=app.manifest_raw or ""),
                raw_manifest_staged=redacted_manifest_text(
                    info, app=app, raw_text=app.manifest_raw_staged or ""
                ),
            )
        )

    @strawberry.field
    @mutation_audit(action="app.push_manifest_to_repo")
    @require_permission(Permission.APP_UPDATE, scope=app_scope_by_guid("input.id"))
    @tenant_scoped()
    def push_manifest_to_repo(
        self,
        info: Info,
        input: PushManifestToRepoInput,
    ) -> MutationResultType[_ManifestPushPayload]:
        """Open a PR with the staged manifest.

        Returns ok + ``note='nothing_to_push'`` when there's no
        staged change. On success, writes the staged TOML to a fresh
        branch on the source repo and opens a PR / MR against the
        base branch; the payload carries the operator-clickable URL
        + the host-side identifier.

        Never raises; SCM failures are translated to a
        ``SCM_PUSH_FAILED`` envelope per the MutationResult contract.
        Issue #535.
        """
        from astrolift_registry.services.manifest_sync import (
            _pick_source_connection,
        )
        from astrolift_scm.providers import (
            ProviderError,
            open_pull_request,
            put_file,
        )

        # Org-scope the by-guid lookup to the caller's tenant before opening
        # the PR (SCM write). Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        staged = app.manifest_raw_staged or ""
        if not staged or staged == (app.manifest_raw or ""):
            return gql_success(
                _ManifestPushPayload(
                    id=input.id,
                    pr_url="",
                    branch_name="",
                    note="nothing_to_push",
                )
            )

        if not app.source_repo:
            return gql_failure(
                "SCM_PUSH_FAILED",
                "app has no source_repo configured; can't open a PR",
            )

        connection = _pick_source_connection(app)
        if connection is None:
            return gql_failure(
                "SCM_PUSH_FAILED",
                "no active source connection found for this organization — "
                "reconnect the source host under Settings -> Source connections",
            )

        head_branch = input.branch_name or f"astrolift/manifest-{app.slug}"
        base_branch = app.default_branch or "main"
        manifest_path = app.manifest_path or "astrolift.toml"
        commit_message = input.pr_title or f"Astrolift: update manifest for {app.slug}"
        pr_title = input.pr_title or commit_message
        pr_body = input.pr_body or (
            f"Update `{manifest_path}` for **{app.name or app.slug}**.\n\n"
            "Opened by the Astrolift manifest editor."
        )

        try:
            put_file(
                connection,
                repo_full_name=app.source_repo,
                path=manifest_path,
                branch=head_branch,
                content=staged,
                commit_message=commit_message,
            )
            pr = open_pull_request(
                connection,
                repo_full_name=app.source_repo,
                head_branch=head_branch,
                base_branch=base_branch,
                title=pr_title,
                body=pr_body,
            )
        except ProviderError as exc:
            return gql_failure(
                "SCM_PUSH_FAILED",
                f"{exc.code}: {exc.message}",
            )

        return gql_success(
            _ManifestPushPayload(
                id=input.id,
                pr_url=pr.url,
                branch_name=head_branch,
                note="pushed",
            )
        )
