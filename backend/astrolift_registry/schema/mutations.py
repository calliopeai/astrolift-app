"""Mutations for the registry app: register/update/soft-delete app."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Project
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import RegisteredAppType, app_to_type
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.input
class RegisterAppInput:
    project_id: GUID
    name: str
    slug: str
    description: str | None = None
    source_kind: str = "github"
    source_repo: str
    source_url: str | None = None
    manifest_path: str | None = None
    default_branch: str | None = None
    deploy_branch: str | None = None
    trigger_mode: str | None = None


@strawberry.input
class UpdateAppInput:
    id: GUID
    name: str | None = None
    description: str | None = None
    source_url: str | None = None
    manifest_path: str | None = None
    default_branch: str | None = None
    deploy_branch: str | None = None
    trigger_mode: str | None = None
    preview_enabled: bool | None = None
    is_active: bool | None = None


@strawberry.input
class SetAppSubdomainInput:
    id: GUID
    subdomain: str


@strawberry.input
class SoftDeleteAppInput:
    id: GUID


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


@strawberry.input
class UpdateManifestInput:
    """Stage an edit to the source astrolift.toml.

    Writes to ``manifest_raw_staged`` rather than ``manifest_raw`` —
    the editor is a draft buffer until ``pushManifestToRepo`` (which
    opens a PR) or ``syncManifestFromRepo`` (which discards the
    draft) is called.
    """

    id: GUID
    raw_manifest: str


@strawberry.input
class SyncManifestFromRepoInput:
    """Re-fetch ``astrolift.toml`` from the source repo's default
    branch, overwriting both ``manifest_raw`` AND any unsaved
    ``manifest_raw_staged`` draft."""

    id: GUID


@strawberry.input
class PushManifestToRepoInput:
    """Open a PR against the source repo with the staged TOML.

    No-op (returns ok + 'nothing_to_push' note) when there's no
    pending staged change."""

    id: GUID
    pr_title: str | None = None
    pr_body: str | None = None
    branch_name: str | None = None


@strawberry.type
class _ManifestStagePayload:
    id: GUID
    sync_state: str
    raw_manifest: str
    raw_manifest_staged: str


@strawberry.type
class _ManifestPushPayload:
    id: GUID
    pr_url: str
    branch_name: str
    note: str


def _actor():
    tenant = get_current_tenant()
    actor_id = tenant.actor_user_id if tenant else None
    if actor_id is None:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=actor_id).first()


@strawberry.type
class RegistryMutation:
    @strawberry.field
    @mutation_audit(action="app.create")
    @require_permission(Permission.APP_CREATE)
    @tenant_scoped()
    def register_app(self, info: Info, input: RegisterAppInput) -> MutationResultType[RegisteredAppType]:
        project = (
            Project.objects.select_related("organization", "team").filter(guid=str(input.project_id)).first()
        )
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found", field="projectId")

        if RegisteredApp.objects.filter(organization=project.organization, slug=input.slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"app with slug {input.slug!r} already exists in this organization",
                field="slug",
            )

        if input.source_repo and input.manifest_path:
            if RegisteredApp.objects.filter(
                source_repo=input.source_repo,
                manifest_path=input.manifest_path,
            ).exists():
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    "this repo + manifest path is already registered",
                    field="sourceRepo",
                )

        app = RegisteredApp.objects.create(
            organization=project.organization,
            team=project.team,
            project=project,
            name=input.name.strip(),
            slug=input.slug,
            description=input.description or "",
            source_kind=input.source_kind or "github",
            source_repo=input.source_repo or "",
            source_url=input.source_url or "",
            manifest_path=input.manifest_path or "astrolift.toml",
            default_branch=input.default_branch or "main",
            deploy_branch=input.deploy_branch or input.default_branch or "main",
            trigger_mode=input.trigger_mode or "auto_on_push",
            k8s_namespace=f"{project.organization.slug}-{input.slug}",
            subdomain=input.slug,
        )
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.update")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_app(self, info: Info, input: UpdateAppInput) -> MutationResultType[RegisteredAppType]:
        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        for field in (
            "name",
            "description",
            "source_url",
            "manifest_path",
            "default_branch",
            "deploy_branch",
            "trigger_mode",
            "preview_enabled",
            "is_active",
        ):
            new_value = getattr(input, field)
            if new_value is not None:
                setattr(app, field, new_value)
        app.save()
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.set_subdomain")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def set_app_subdomain(
        self, info: Info, input: SetAppSubdomainInput
    ) -> MutationResultType[RegisteredAppType]:
        """Edit a registered app's subdomain without redeploy.

        The platform's hostname computation re-derives from
        ``app.subdomain`` on the next render — for live ingress
        traffic this needs an Ingress patch (handled by the
        SyncAppDomainWorkflow, separately tracked). This mutation is
        the source-of-truth update + collision check.

        Validation:
        - DNS label rules (lowercase letters, digits, hyphens)
        - Reserved name check (api / admin / etc — see hostname.py)
        - Within-org collision check (no two active apps in the same
          org may claim the same subdomain)

        Per spec 13 §6.
        """
        from astrolift_manifest.hostname import validate_subdomain_label

        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        try:
            new_subdomain = validate_subdomain_label(input.subdomain)
        except ValueError as exc:
            return gql_failure(
                ErrorCode.VALIDATION.value, str(exc), field="subdomain"
            )

        # Within-org collision: another active app already owning
        # this subdomain is a footgun (DNS would race for the same
        # label). Refuse with CONFLICT.
        clash = (
            RegisteredApp.objects.filter(
                organization_id=app.organization_id,
                subdomain=new_subdomain,
                deleted_at__isnull=True,
            )
            .exclude(pk=app.pk)
            .first()
        )
        if clash is not None:
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"another app in this org already uses {new_subdomain!r}",
                field="subdomain",
            )

        if app.subdomain != new_subdomain:
            app.subdomain = new_subdomain
            app.save(update_fields=["subdomain", "updated_at", "version"])
        return gql_success(app_to_type(app))

    @strawberry.field
    @mutation_audit(action="app.delete")
    @require_permission(Permission.APP_DELETE)
    @tenant_scoped()
    def soft_delete_app(
        self, info: Info, input: SoftDeleteAppInput
    ) -> MutationResultType[_SoftDeletePayload]:
        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")
        app.soft_delete(by=_actor())
        return gql_success(_SoftDeletePayload(id=input.id, deleted=True))

    @strawberry.field
    @mutation_audit(action="app.update_manifest")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def update_manifest(
        self, info: Info, input: UpdateManifestInput,
    ) -> MutationResultType[_ManifestStagePayload]:
        """Stage a manifest edit. Writes to ``manifest_raw_staged``.

        Validates the TOML parses before staging — bad TOML never
        lands in the buffer. Empty input clears the staging buffer.
        """
        from astrolift_manifest.parser import ManifestError, parse_raw
        from astrolift_manifest.sync_state import (
            SyncSnapshot, classify_state,
        )

        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
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
        app.save(update_fields=[
            "manifest_raw_staged", "updated_at", "version",
        ])

        sync_state = classify_state(SyncSnapshot(
            db_hash=app.manifest_hash or "",
            repo_hash=app.last_synced_hash or app.manifest_hash or "",
            last_synced_hash=app.last_synced_hash or "",
        ))
        return gql_success(_ManifestStagePayload(
            id=input.id,
            sync_state=sync_state.value,
            raw_manifest=app.manifest_raw or "",
            raw_manifest_staged=app.manifest_raw_staged or "",
        ))

    @strawberry.field
    @mutation_audit(action="app.sync_manifest_from_repo")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def sync_manifest_from_repo(
        self, info: Info, input: SyncManifestFromRepoInput,
    ) -> MutationResultType[_ManifestStagePayload]:
        """Re-fetch the manifest from the source repo + recompute
        the hash anchor.

        Discards any staged edits — sync is destructive on purpose,
        the UI is expected to confirm before calling.

        Production wires this into the SCM provider's read-file path
        (GitHub Contents API, GitLab files, etc.). Until that flow is
        connected at this resolver, the mutation simply re-anchors
        ``last_synced_hash`` to the current ``manifest_hash`` so the
        sync_state classifier reads as IN_SYNC.
        """
        from astrolift_manifest.sync_state import (
            SyncSnapshot, classify_state,
        )

        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        # TODO: wire SCM provider .read_file(source_repo, manifest_path)
        # via astrolift_scm.providers when the SCM activity is exposed.
        # For now we drop the staging buffer + reset the anchor so the
        # UI's sync state is consistent.
        app.manifest_raw_staged = ""
        app.last_synced_hash = app.manifest_hash or ""
        app.save(update_fields=[
            "manifest_raw_staged", "last_synced_hash",
            "updated_at", "version",
        ])

        sync_state = classify_state(SyncSnapshot(
            db_hash=app.manifest_hash or "",
            repo_hash=app.last_synced_hash or "",
            last_synced_hash=app.last_synced_hash or "",
        ))
        return gql_success(_ManifestStagePayload(
            id=input.id,
            sync_state=sync_state.value,
            raw_manifest=app.manifest_raw or "",
            raw_manifest_staged="",
        ))

    @strawberry.field
    @mutation_audit(action="app.push_manifest_to_repo")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def push_manifest_to_repo(
        self, info: Info, input: PushManifestToRepoInput,
    ) -> MutationResultType[_ManifestPushPayload]:
        """Open a PR with the staged manifest.

        Returns ok + ``note='nothing_to_push'`` when there's no
        staged change. The actual PR creation goes through the SCM
        provider; until that flow is wired here, returns
        ``note='scm_pending'`` with an empty pr_url so the UI can
        show 'PR opening...' state without exploding.
        """
        app = RegisteredApp.objects.filter(guid=str(input.id)).first()
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found")

        staged = app.manifest_raw_staged or ""
        if not staged or staged == (app.manifest_raw or ""):
            return gql_success(_ManifestPushPayload(
                id=input.id,
                pr_url="",
                branch_name="",
                note="nothing_to_push",
            ))

        branch = input.branch_name or f"astrolift/manifest-{app.slug}"
        # TODO: wire astrolift_scm.providers.<source_kind>.open_pull_request
        # to take (source_repo, branch, base=default_branch, file_changes,
        # title, body) and return the PR URL. The SCM-side abstraction
        # already exists for status posts; PR creation is a sibling.
        return gql_success(_ManifestPushPayload(
            id=input.id,
            pr_url="",
            branch_name=branch,
            note="scm_pending",
        ))
