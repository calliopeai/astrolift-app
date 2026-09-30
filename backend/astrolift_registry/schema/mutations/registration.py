"""RegistrationMutations — split from the monolithic mutations module."""

from __future__ import annotations

import logging

import strawberry
from _sdk.k8s_naming import app_namespace
from django.db.models import Q
from strawberry.types import Info

from astrolift_clusters.models import TenantCluster
from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Project
from astrolift_manifest.persist import actor_may_attach_project_services, allow_project_attach
from astrolift_registry.cron import CronValidationError, validate_cron_expression
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations.helpers import (
    _bootstrap_app_environments,
    _ensure_owner_access,
    _generate_unique_app_slug,
    _normalize_build_args,
    _project_service_attach_denial,
    _resolve_approval_inputs,
    _validate_build_mode,
    _validate_build_path_field,
    _validate_build_strategy,
    _validate_effective_approval_policy,
)
from astrolift_registry.schema.mutations.types import (
    RegisterAgentRepoInput,
    RegisterAgentRepoResultType,
    RegisterAppInput,
    RegisterAppRepoInput,
    RegisterAppRepoResultType,
    RegisteredAgentType,
    RegisteredAppEntryType,
)
from astrolift_registry.schema.types import (
    RegisteredAppType,
    app_to_type,
)
from astrolift_registry.scopes import registration_project_scope
from astrolift_scm.org_repo_policy import rejection_reason
from core.decorators import tenant_scoped
from core.friendly_name import friendly_name_from_slug
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.tenancy import get_current_tenant


@strawberry.type
class RegistrationMutations:
    @strawberry.field
    @mutation_audit(action="app.create")
    @require_permission(
        Permission.APP_CREATE,
        scope=registration_project_scope("input.project_id", permission=Permission.APP_CREATE),
    )
    @tenant_scoped()
    def register_app(self, info: Info, input: RegisterAppInput) -> MutationResultType[RegisteredAppType]:
        # Org-scope the project lookup to the caller's tenant. Everything
        # downstream keys the new app's organization off ``project.organization``,
        # so an unscoped project guid would let a caller create apps inside a
        # sibling org's project. Fails closed (NOT_FOUND) when org_id is
        # None or the project belongs to another org (#1183).
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        project = (
            Project.objects.select_related("organization", "team").filter(guid=str(input.project_id)).first()
        )
        if project is None or project.organization_id != org_id:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found", field="projectId")

        # Friendly-name default (#friendly-name): a caller-supplied slug is
        # honored verbatim (and still conflict-checked below); a blank slug is
        # auto-generated unique within the org. A blank name is derived from
        # the effective slug so the app is never nameless.
        raw_slug = (input.slug or "").strip()
        eff_slug = raw_slug or _generate_unique_app_slug(project.organization)
        eff_name = (input.name or "").strip() or friendly_name_from_slug(eff_slug)

        # An app is a deployment target — without a managed cluster
        # the platform has nowhere to roll the workload to and the
        # downstream deploy fails with an opaque "no cluster available"
        # error. Reject up front instead. Soft-deleted, inactive, and
        # not-yet-managed clusters don't count: only ``lifecycle =
        # "managed"`` rows (#316) have platform RBAC + a passing
        # preflight and can actually accept a deploy.
        #
        # TenantCluster.organization is nullable: null means shared
        # (available to all orgs). Mirror the deploy-time cluster picker
        # (``_resolve_app_cluster`` below + builder_views) and accept an
        # org-scoped cluster OR a shared one — otherwise an org whose only
        # managed cluster is shared is wrongly told "no managed cluster"
        # even though deploy would happily use it.
        cluster_count = TenantCluster.objects.filter(
            Q(organization=project.organization) | Q(organization__isnull=True),
            deleted_at__isnull=True,
            is_active=True,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        ).count()
        if cluster_count == 0:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "No managed cluster connected. Visit /clusters and finish bringing a cluster into management before adding apps.",
                field=None,
            )

        if RegisteredApp.objects.filter(organization=project.organization, slug=eff_slug).exists():
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"app with slug {eff_slug!r} already exists in this organization",
                field="slug",
            )

        # The org-only source policy, when the install has it on (#1543).
        # Checked before the conflict lookups because a repo this install
        # will not accept should be refused for that reason, not for
        # colliding with something.
        if input.source_repo:
            refusal = rejection_reason(input.source_repo, project.organization)
            if refusal:
                return gql_failure(ErrorCode.VALIDATION.value, refusal, field="sourceRepo")

        if input.source_repo and input.manifest_path:
            if RegisteredApp.objects.filter(
                organization=project.organization,
                source_repo=input.source_repo,
                manifest_path=input.manifest_path,
            ).exists():
                return gql_failure(
                    ErrorCode.CONFLICT.value,
                    "this repo + manifest path is already registered",
                    field="sourceRepo",
                )

        trigger_mode = input.trigger_mode or "auto_on_push"
        cron_expression = ""
        if trigger_mode == RegisteredApp.TriggerMode.CRON.value:
            raw_cron = (input.cron_expression or "").strip()
            if not raw_cron:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    "cron expression is required when triggerMode is 'cron'",
                    field="cronExpression",
                )
            try:
                cron_expression = validate_cron_expression(raw_cron)
            except CronValidationError as exc:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    str(exc),
                    field="cronExpression",
                )

        approval, err = _resolve_approval_inputs(
            organization=project.organization,
            requires_approval=input.requires_approval,
            approver_team_id=input.approver_team_id,
            approver_user_ids=input.approver_user_ids,
            minimum_approvals=input.minimum_approvals,
        )
        if err is not None:
            return err

        eff_requires_approval = (
            bool(approval["requires_approval"]) if approval["requires_approval"] is not None else False
        )
        eff_team = approval["team"]
        eff_user_ids = approval["user_ids"] or ()
        eff_minimum_approvals = (
            approval["minimum_approvals"] if approval["minimum_approvals"] is not None else 1
        )
        cross_err = _validate_effective_approval_policy(
            requires_approval=eff_requires_approval,
            effective_team=eff_team,
            effective_user_ids=eff_user_ids,
            effective_minimum_approvals=eff_minimum_approvals,
        )
        if cross_err is not None:
            return cross_err

        build_mode, build_mode_err = _validate_build_mode(input.build_mode)
        if build_mode_err is not None:
            return build_mode_err
        build_strategy, build_strategy_err = _validate_build_strategy(input.build_strategy)
        if build_strategy_err is not None:
            return build_strategy_err
        build_args, build_args_err = _normalize_build_args(input.build_args)
        if build_args_err is not None:
            return build_args_err
        dockerfile_path, dockerfile_path_err = _validate_build_path_field(
            input.dockerfile_path, field="dockerfilePath"
        )
        if dockerfile_path_err is not None:
            return dockerfile_path_err
        build_context, build_context_err = _validate_build_path_field(
            input.build_context, field="buildContext"
        )
        if build_context_err is not None:
            return build_context_err
        # Before the app exists: a refusal must leave nothing half-registered.
        attach_denial = _project_service_attach_denial(input.manifest_raw or "", project)
        if attach_denial is not None:
            return attach_denial

        # manifestRaw can be copied from a masked read of another app. A new
        # app has no stored value to put back, so a masked placeholder would
        # become the secret itself (#1920).
        from astrolift_manifest.env_edit import resolve_masked_env_values

        eff_manifest_raw, unresolved_keys = resolve_masked_env_values(
            input.manifest_raw or "", fallback_text=""
        )
        if unresolved_keys:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "manifestRaw contains a masked placeholder for key(s) with no value to "
                f"restore (a new app has nothing to fall back to): {', '.join(unresolved_keys)}",
                field="manifestRaw",
            )

        from astrolift_registry.namespaces import namespace_refusal

        _ns_refusal = namespace_refusal(
            app_namespace(organization_slug=project.organization.slug, app_slug=eff_slug),
            organization_id=project.organization_id,
        )
        if _ns_refusal is not None:
            return gql_failure(ErrorCode.VALIDATION.value, _ns_refusal, field="slug")
        from astrolift_registry.hostname_claims import hostname_label_refusal

        _host_refusal = hostname_label_refusal(eff_slug, organization=project.organization)
        if _host_refusal is not None:
            return gql_failure(ErrorCode.CONFLICT.value, _host_refusal, field="slug")
        app = RegisteredApp.objects.create(
            organization=project.organization,
            team=project.team,
            project=project,
            name=eff_name,
            slug=eff_slug,
            description=input.description or "",
            source_kind=input.source_kind or "github",
            source_repo=input.source_repo or "",
            source_url=input.source_url or "",
            manifest_path=input.manifest_path or "astrolift.toml",
            manifest_raw=eff_manifest_raw,
            default_branch=input.default_branch or "main",
            deploy_branch=input.deploy_branch or input.default_branch or "main",
            build_mode=build_mode or RegisteredApp.BuildMode.CI_PUSHED.value,
            build_strategy=build_strategy or RegisteredApp.BuildStrategy.OFF.value,
            dockerfile_path=dockerfile_path or "Dockerfile",
            build_context=build_context or ".",
            build_args=build_args,
            trigger_mode=trigger_mode,
            cron_expression=cron_expression,
            k8s_namespace=app_namespace(
                organization_slug=project.organization.slug,
                app_slug=eff_slug,
            ),
            subdomain=eff_slug,
            requires_approval=bool(approval["requires_approval"])
            if approval["requires_approval"] is not None
            else False,
            approver_team=approval["team"],
            minimum_approvals=approval["minimum_approvals"]
            if approval["minimum_approvals"] is not None
            else 1,
            # Bind to the first managed cluster so downstream activities
            # (provision_registry_repo, provision_namespace) can resolve
            # the registry driver without an additional lookup step.
            # organization is nullable on TenantCluster: null = shared.
            default_tenant_cluster=TenantCluster.objects.filter(
                Q(organization=project.organization) | Q(organization__isnull=True),
                deleted_at__isnull=True,
                is_active=True,
                lifecycle=TenantCluster.Lifecycle.MANAGED.value,
            )
            .order_by("pk")
            .first(),
        )
        _ensure_owner_access(app, app.team_id)
        if approval["user_ids"] is not None:
            app.approver_users.set(approval["user_ids"])

        # Bootstrap the default environment + kick off OnboardAppWorkflow
        # straight from registration. Previously the env + onboard were only
        # created by a later resync/scan, which re-fetches the repo — so an
        # app registered with an inline ``manifest_raw`` (a repo the platform
        # can't fetch) never onboarded, and resync would wipe the inline
        # manifest on the failed fetch. ``_bootstrap_app_environments`` is
        # fetch-free (creates AppEnvironment + starts the idempotent
        # OnboardAppWorkflow), never touches ``manifest_raw``, and no-ops when
        # no managed cluster is bound. The manifest declares no environments,
        # so pass ``[]`` — ``_bootstrap`` defaults to a ``production`` env.
        # Materialize the manifest's Workload + Container rows (#1014) so a
        # manifest-declared workload — notably a ``kind=agent`` — is
        # MANAGEABLE (run-spec, scale, live status, the agent fleet all key
        # on Workload rows), not merely deployable (the renderer reads
        # manifest_raw directly). Reuses the same persist path the SCM /
        # registerAgentRepo flow already uses, so direct-upload apps reach
        # parity. Best-effort + idempotent: a malformed manifest does not fail
        # registration (the raw is stored and validated again at deploy time).
        # #1553: record the outcome, and cover the case where there is no
        # inline manifest at all. Previously this whole block was skipped when
        # ``manifest_raw`` was empty, so an app registered straight from a repo
        # got no manifest fetch, no workloads, and no signal — the operator
        # then spent the next hour convinced the platform was caching a
        # manifest it had in fact never read. Still best-effort: registration
        # must survive a bad manifest. The difference is that it now says so.
        _bootstrap_status = "no_source"
        _bootstrap_error = ""
        if eff_manifest_raw.strip():
            try:
                from astrolift_manifest.normalize import (
                    NormalizationDefaults,
                    normalize,
                )
                from astrolift_manifest.parser import ManifestError, parse_raw
                from astrolift_manifest.persist import persist_manifest

                _manifest = normalize(
                    parse_raw(eff_manifest_raw),
                    defaults=NormalizationDefaults(),
                )
                with allow_project_attach(actor_may_attach_project_services(app)):
                    persist_manifest(app, _manifest, raw_text=eff_manifest_raw)
                _bootstrap_status = "applied"
            except Exception as exc:  # noqa: BLE001 — registration must survive
                _bootstrap_status = "parse_failed"
                # Only a ManifestError's text is known to be free of [env]
                # values. Anything else (a database error's "Failing row
                # contains ..." detail, say) can quote the manifest, and this
                # string is shown to app.read callers and logged, so it keeps
                # the exception type and no traceback (#1920).
                if isinstance(exc, ManifestError):
                    _bootstrap_error = str(exc) or exc.__class__.__name__
                else:
                    _bootstrap_error = exc.__class__.__name__
                logging.getLogger(__name__).warning(
                    "register_app: manifest workload persist failed for %s: %s",
                    app.slug,
                    _bootstrap_error,
                )
        elif app.source_repo:
            # Fetch it from the repo instead of leaving the app manifest-less.
            # ``resync_app_manifest_from_repo`` is the one blessed path: it
            # fetches, parses, and materialises workloads via persist_manifest,
            # and never raises.
            try:
                from astrolift_registry.services.manifest_sync import (
                    resync_app_manifest_from_repo,
                )

                with allow_project_attach(actor_may_attach_project_services(app)):
                    _resync = resync_app_manifest_from_repo(app)
                _bootstrap_status = _resync.status
                _bootstrap_error = _resync.error or ""
            except Exception as exc:  # noqa: BLE001 — registration must survive
                _bootstrap_status = "fetch_failed"
                _bootstrap_error = str(exc) or exc.__class__.__name__
                logging.getLogger(__name__).exception(
                    "register_app: manifest bootstrap fetch failed for %s",
                    app.slug,
                )

        app.manifest_bootstrap_status = _bootstrap_status
        app.manifest_bootstrap_error = _bootstrap_error
        app.save(
            update_fields=[
                "manifest_bootstrap_status",
                "manifest_bootstrap_error",
                "updated_at",
                "version",
            ]
        )

        with allow_project_attach(actor_may_attach_project_services(app)):
            _bootstrap_app_environments(app, [])

        # Seed the default alert rule set (spec 08 §10.2) for the new app.
        # There is no ``app.created`` platform Event to subscribe to, so we
        # seed at the single-app registration completion point — the least
        # invasive idempotent hook. ``seed_default_alert_rules`` is idempotent
        # (skips rules whose org-unique name already exists) and best-effort:
        # a seeding failure must never fail registration. Kept separate from
        # ``_bootstrap_app_environments`` (which starts a Temporal workflow and
        # is stubbed in tests) so seeding runs even when onboarding is stubbed.
        try:
            from astrolift_operations.alert_seed import seed_default_alert_rules

            seed_default_alert_rules(app)
        except Exception:
            logging.getLogger(__name__).exception(
                "register_app: default alert-rule seeding failed for %s",
                app.slug,
            )

        # Complete the autowire straight from registration (#1108): push the
        # CI workflow, install the source webhook, and push the deploy-token
        # secret so a git push auto-deploys with zero extra clicks. Fully
        # best-effort — a wiring failure (or no org connection yet) must never
        # fail the registration; ``run_autowire`` records a per-step outcome
        # on ``app.autowire_state`` and the app detail page surfaces which
        # step, if any, still needs attention. When no org source connection
        # exists this no-ops into the "connect for auto-deploy" state.
        try:
            from astrolift_scm.services.autowire import run_autowire

            request = getattr(info.context, "request", None)
            actor = getattr(request, "user", None) if request else None
            run_autowire(app, actor=actor)
        except Exception:
            logging.getLogger(__name__).exception(
                "register_app: autowire orchestration failed for %s",
                app.slug,
            )

        return gql_success(app_to_type(app, info=info))

    @strawberry.field
    @mutation_audit(
        action="app.register_agent_repo",
        target=lambda self, info, input: ("repo", input.source_repo),
    )
    @require_permission(
        Permission.AGENT_CREATE,
        scope=registration_project_scope("input.project_id", permission=Permission.AGENT_CREATE),
    )
    @tenant_scoped()
    def register_agent_repo(
        self, info: Info, input: RegisterAgentRepoInput
    ) -> MutationResultType[RegisterAgentRepoResultType]:
        """Register every agent manifest in a repo as an agent Workload (spec 33 PR-3).

        Scans ``source_repo`` for agent manifests (monorepo
        ``agents/*/astrolift.toml`` + root ``astrolift.toml``) and registers
        each as an agent ``Workload`` under its own ``RegisteredApp``,
        reusing the same per-manifest persist path as ``register_app`` and
        the PR-1 run-spec defaults (a fresh agent ``Workload`` carries
        ``run_family=task`` / ``run_mode=once``). Idempotent on
        ``(source_repo, manifest_path)`` so re-running picks up only
        newly-added agents without duplicating the existing ones.

        Org-scoped exactly like ``register_app``: the target project is
        resolved by GUID and must belong to the caller's active tenant
        (``@tenant_scoped`` establishes the context; the project's
        organization is the boundary). The repo is fetched through the
        org's own source connection, so a caller can neither register into a
        foreign org nor scan with another tenant's credentials.
        """
        from astrolift_registry.services.manifest_sync import register_agent_repo as _register_agent_repo

        tenant = get_current_tenant()
        active_org_id = tenant.organization_id if tenant else None
        if active_org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        project = (
            Project.objects.select_related("organization", "team")
            .filter(guid=str(input.project_id), deleted_at__isnull=True)
            .first()
        )
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found", field="projectId")
        # The project's org must be the caller's active tenant — otherwise a
        # caller could register agents into another org by passing its
        # project GUID (the decorator only asserts a context exists).
        if project.organization_id != active_org_id:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "project belongs to another organization",
                field="projectId",
            )

        if not (input.source_repo or "").strip():
            return gql_failure(ErrorCode.VALIDATION.value, "sourceRepo is required", field="sourceRepo")

        # Bind the org's first managed cluster best-effort (agents are
        # dispatched on demand; the dispatch path enforces the managed-
        # cluster requirement, so registration does not reject when none
        # exists yet — unlike register_app which is a deploy target).
        default_cluster = (
            TenantCluster.objects.filter(
                Q(organization=project.organization) | Q(organization__isnull=True),
                deleted_at__isnull=True,
                is_active=True,
                lifecycle=TenantCluster.Lifecycle.MANAGED.value,
            )
            .order_by("pk")
            .first()
        )

        result = _register_agent_repo(
            project=project,
            source_kind=input.source_kind or "github",
            source_repo=input.source_repo,
            ref=input.ref or "main",
            source_url=input.source_url or "",
            default_branch=input.default_branch or "main",
            deploy_branch=input.deploy_branch or "",
            default_cluster=default_cluster,
            manifest_paths=input.manifest_paths or None,
        )

        if result.status == "fetch_failed":
            return gql_failure(ErrorCode.PRECONDITION.value, result.error or "repo fetch failed")
        if result.status == "no_match":
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                result.error or "none of the requested manifestPaths matched a discovered agent manifest",
                field="manifestPaths",
            )
        if result.status == "no_agents":
            message = (
                "no agent or workflow manifests found in this repo "
                "(looked for agents/*/astrolift.toml, a root astrolift.toml, "
                "and workflows/**/*.toml)"
            )
            if result.skipped:
                # A manifest that declares an agent and was not kept is the
                # answer to "why did it find nothing", and without it the
                # operator has only the negative (#1697).
                message += ". Skipped: " + "; ".join(result.skipped)
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                message,
                field="sourceRepo",
            )
        if result.status != "ok":
            return gql_failure(ErrorCode.INTERNAL.value, result.error or "registration failed")

        return gql_success(
            RegisterAgentRepoResultType(
                agents=[
                    RegisteredAgentType(
                        manifest_path=a.manifest_path,
                        slug=a.slug,
                        app_id=GUID(str(a.app_guid)),
                        workload_slug=a.workload_slug,
                        created=a.created,
                        skill_notes=list(a.skill_notes),
                    )
                    for a in result.agents
                ],
                workflows=[workflow.slug for workflow in getattr(result, "workflows", [])],
            )
        )

    @strawberry.field
    @mutation_audit(
        action="app.register_app_repo",
        target=lambda self, info, input: ("repo", input.source_repo),
    )
    @require_permission(
        Permission.APP_CREATE,
        scope=registration_project_scope("input.project_id", permission=Permission.APP_CREATE),
    )
    @tenant_scoped()
    def register_app_repo(
        self, info: Info, input: RegisterAppRepoInput
    ) -> MutationResultType[RegisterAppRepoResultType]:
        """Register every app manifest in a repo as its own app (#979).

        Scans ``source_repo`` for app manifests (monorepo
        ``apps/*/astrolift.toml`` + root ``astrolift.toml``) and registers each
        deployable (non-agent) manifest as its own ``RegisteredApp`` building
        from its own subdir, reusing the same per-manifest persist path as
        ``register_app``. Idempotent on ``(source_repo, manifest_path)`` so
        re-running picks up only newly-added services.

        Org-scoped exactly like ``register_app``: the target project is
        resolved by GUID and must belong to the caller's active tenant. Apps
        are deploy targets, so a managed cluster is required up front (mirrors
        ``register_app``); each created app is bound to it and bootstrapped
        (default environment + OnboardAppWorkflow) so it is deployable.
        """
        from astrolift_registry.services.manifest_sync import register_app_repo as _register_app_repo

        tenant = get_current_tenant()
        active_org_id = tenant.organization_id if tenant else None
        if active_org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no active organization")

        project = (
            Project.objects.select_related("organization", "team")
            .filter(guid=str(input.project_id), deleted_at__isnull=True)
            .first()
        )
        if project is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "project not found", field="projectId")
        if project.organization_id != active_org_id:
            return gql_failure(
                ErrorCode.PERMISSION_DENIED.value,
                "project belongs to another organization",
                field="projectId",
            )

        if not (input.source_repo or "").strip():
            return gql_failure(ErrorCode.VALIDATION.value, "sourceRepo is required", field="sourceRepo")

        # Same policy as register_app (#1543). This path registers every
        # manifest in a repo at once, so letting it through would onboard a
        # personal repo as many apps in one call.
        refusal = rejection_reason(input.source_repo, project.organization)
        if refusal:
            return gql_failure(ErrorCode.VALIDATION.value, refusal, field="sourceRepo")

        # Apps are deploy targets — require a managed cluster up front (mirror
        # register_app). organization is nullable on TenantCluster: null =
        # shared (available to all orgs).
        managed_cluster = (
            TenantCluster.objects.filter(
                Q(organization=project.organization) | Q(organization__isnull=True),
                deleted_at__isnull=True,
                is_active=True,
                lifecycle=TenantCluster.Lifecycle.MANAGED.value,
            )
            .order_by("pk")
            .first()
        )
        if managed_cluster is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "No managed cluster connected. Visit /clusters and finish bringing a cluster into management before adding apps.",
                field=None,
            )

        with allow_project_attach(actor_may_attach_project_services(project_id=project.pk)):
            result = _register_app_repo(
                project=project,
                source_kind=input.source_kind or "github",
                source_repo=input.source_repo,
                ref=input.ref or "main",
                source_url=input.source_url or "",
                default_branch=input.default_branch or "main",
                deploy_branch=input.deploy_branch or "",
                default_cluster=managed_cluster,
            )

        if result.status == "fetch_failed":
            return gql_failure(ErrorCode.PRECONDITION.value, result.error or "repo fetch failed")
        if result.status == "no_apps":
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "no app manifests found in this repo (looked for apps/*/astrolift.toml and a root astrolift.toml)",
                field="sourceRepo",
            )
        if result.status != "ok":
            return gql_failure(ErrorCode.INTERNAL.value, result.error or "registration failed")

        # Bootstrap each newly-created app (default environment +
        # OnboardAppWorkflow) so it is deployable, at parity with register_app.
        # Best-effort + per-app: one app's bootstrap failure does not unwind
        # the registration of the rest.
        for entry in result.apps:
            if not entry.created:
                continue
            app = RegisteredApp.objects.filter(guid=str(entry.app_guid)).first()
            if app is None:
                continue
            try:
                with allow_project_attach(actor_may_attach_project_services(app)):
                    _bootstrap_app_environments(app, [])
            except Exception:  # noqa: BLE001 — one app's bootstrap must not unwind the rest
                import logging

                logging.getLogger(__name__).exception(
                    "register_app_repo: environment bootstrap failed for %s",
                    app.slug,
                )

        return gql_success(
            RegisterAppRepoResultType(
                apps=[
                    RegisteredAppEntryType(
                        manifest_path=a.manifest_path,
                        slug=a.slug,
                        app_id=GUID(str(a.app_guid)),
                        build_context=a.build_context,
                        created=a.created,
                    )
                    for a in result.apps
                ]
            )
        )
