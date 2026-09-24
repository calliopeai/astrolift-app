"""ManifestMutations — split from the monolithic mutations module."""

from __future__ import annotations

import logging

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
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
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant

log = logging.getLogger(__name__)


def _audit_env_change(
    app: RegisteredApp,
    *,
    changed_keys: list[str],
    decision: str,
    unapproved_keys: list[str] | None = None,
    proposal_ids: list[str] | None = None,
) -> None:
    """Sibling audit entry for an env change through ``applyStagedManifest``
    (#1759 adversarial review, H1).

    ``@mutation_audit`` (on the resolver above) already records one entry
    per call, but it doesn't know about manifest-specific detail -- same
    reason ``app.secret.reveal.disclosure`` and ``_emit_deny_audit`` emit
    their own sibling entry rather than stretching the generic one.
    Carries key **names** only, never values and no digest of the
    manifest text either: an unkeyed hash of text that holds a secret
    lets anyone who can read the audit log confirm a guess at it offline.
    ``unapproved_keys`` / ``proposal_ids`` say why an app that requires
    secret approval was refused, or which applied proposals allowed it.
    """
    extra: dict[str, list[str]] = {"changed_keys": changed_keys}
    if unapproved_keys is not None:
        extra["unapproved_keys"] = unapproved_keys
    if proposal_ids is not None:
        extra["proposal_ids"] = proposal_ids
    tenant = get_current_tenant()
    try:
        emit_audit(
            AuditEntry(
                actor_user_id=tenant.actor_user_id if tenant else None,
                organization_id=tenant.organization_id if tenant else None,
                action="app.manifest.apply_env_change",
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
        """
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.sync_state import (
            SyncSnapshot,
            classify_state,
        )

        # Org-scope the by-guid lookup to the caller's tenant before staging
        # the manifest edit. Fails closed (NOT_FOUND) when org_id is
        # None (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        app = RegisteredApp.objects.filter(guid=str(input.id), organization_id=org_id).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        text = input.raw_manifest or ""
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
        # copy.
        if text == (app.manifest_raw or ""):
            app.manifest_raw_staged = ""
        else:
            app.manifest_raw_staged = text
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
                raw_manifest=app.manifest_raw or "",
                raw_manifest_staged=app.manifest_raw_staged or "",
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

        An edit that changes env is gated the same way ``setAppSecret``
        gates a direct secret write (adversarial review): "env" is the
        top-level ``[env]`` table and every container, job and task env
        table, since container env reaches the pod too and outranks
        ``[env]`` there (``astrolift_manifest.env_diff``). When the app
        requires secret approval, every changed ``[env]`` key must match
        an applied secret-change proposal (the approved value, or an
        approved delete) and anything else is refused, container env
        included, since no proposal can carry it; otherwise the change
        needs a fresh session elevation. Without this, this mutation
        would be a permission-only bypass of the review every other
        secret-literal write path sits behind.

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

        from astrolift_identity.step_up import check_elevation
        from astrolift_manifest.env_diff import env_changes
        from astrolift_manifest.normalize import NormalizationDefaults, normalize
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.persist import persist_manifest
        from astrolift_manifest.sync_state import SyncSnapshot, classify_state
        from astrolift_registry.services.staged_manifest import staged_manifest_hash
        from astrolift_services.secret_proposal_match import match_applied_proposals

        def _result(current: RegisteredApp) -> MutationResultType[_ManifestStagePayload]:
            sync_state = classify_state(
                SyncSnapshot(
                    db_hash=current.manifest_hash or "",
                    repo_hash=current.last_synced_hash or current.manifest_hash or "",
                    last_synced_hash=current.last_synced_hash or "",
                )
            )
            return gql_success(
                _ManifestStagePayload(
                    id=input.id,
                    sync_state=sync_state.value,
                    raw_manifest=current.manifest_raw or "",
                    raw_manifest_staged=current.manifest_raw_staged or "",
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

            # H1 (adversarial review): an env change is a secret change
            # wherever it lands, [env] or a container's own env.
            changes = env_changes(app.manifest_raw or "", staged)
            if changes:
                changed_keys = [change.label for change in changes]
                # The hash pins the apply to the exact buffer the caller
                # reviewed; for a secret change that can't be optional.
                if not input.expected_staged_hash:
                    return gql_failure(
                        ErrorCode.VALIDATION.value,
                        "this edit changes env -- pass the rawManifestStagedHash you "
                        "reviewed as expectedStagedHash",
                        field="expectedStagedHash",
                    )
                if app.requires_secret_approval:
                    match = match_applied_proposals(
                        app,
                        {change.key: change.after for change in changes if change.app_wide},
                    )
                    unapproved = match.unapproved + [
                        change.label for change in changes if not change.app_wide
                    ]
                    if unapproved:
                        _audit_env_change(
                            app,
                            changed_keys=changed_keys,
                            decision="DENY",
                            unapproved_keys=unapproved,
                        )
                        return gql_failure(
                            "SECRET_APPROVAL_REQUIRED",
                            "this app requires secret approval and no applied secret-change "
                            f"proposal covers these env changes: {', '.join(unapproved)} -- "
                            "change [env] through setAppSecret/rotateAppSecret/deleteAppSecret "
                            "and apply once approved; container, job and task env can't be "
                            "approved through a proposal",
                        )
                    _audit_env_change(
                        app,
                        changed_keys=changed_keys,
                        decision="ALLOW",
                        proposal_ids=match.proposal_ids,
                    )
                else:
                    deny = check_elevation(
                        info,
                        action_label="app.manifest.apply_secret_change",
                        resolver_name="ManifestMutations.apply_staged_manifest",
                    )
                    if deny is not None:
                        _audit_env_change(app, changed_keys=changed_keys, decision="DENY")
                        return deny
                    _audit_env_change(app, changed_keys=changed_keys, decision="ALLOW")

            persist_manifest(app, manifest, raw_text=staged)
            app.manifest_raw_staged = ""
            # There is no repo anchor for a connection-less app -- there
            # is no repo at all, so last_synced_hash is left alone rather
            # than set to a hash no repo ever had (adversarial review).
            # _result()'s "or manifest_hash" fallback (mirroring
            # ``astrolift_registry.schema.types._repo_hash_for``'s own
            # convention for "no known repo hash") already reads this as
            # in_sync.
            app.save(update_fields=["manifest_raw_staged", "updated_at", "version"])

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
                raw_manifest=app.manifest_raw or "",
                raw_manifest_staged=app.manifest_raw_staged or "",
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
