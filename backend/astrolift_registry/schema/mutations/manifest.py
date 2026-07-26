"""ManifestMutations — split from the monolithic mutations module."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations.types import (
    PushManifestToRepoInput,
    SyncManifestFromRepoInput,
    UpdateManifestInput,
    _ManifestPushPayload,
    _ManifestStagePayload,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class ManifestMutations:
    @strawberry.field
    @mutation_audit(action="app.update_manifest")
    @require_permission(Permission.APP_UPDATE)
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
    @mutation_audit(action="app.sync_manifest_from_repo")
    @require_permission(Permission.APP_UPDATE)
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
    @require_permission(Permission.APP_UPDATE)
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
