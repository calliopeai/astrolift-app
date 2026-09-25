"""GraphQL types for RegisteredApp, Workload, Container, Template."""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
from collections.abc import Iterable

import strawberry
from _sdk.k8s_naming import app_namespace
from django.db import models
from strawberry.types import Info

from astrolift_graphql import GUID

JSON = strawberry.scalars.JSON


@strawberry.enum
class AstroliftAppListStatusFilter(enum.Enum):
    """Status axis for the apps-list filter pills (#481).

    Mirrors the health-pulse buckets surfaced on each row (#405) plus a
    synthetic ``never_deployed`` value that matches "no deploys yet"
    rows. ``all`` is the no-op pass-through; resolver short-circuits
    the filter entirely when this is chosen.

    Distinct from the raw ``provisioning_status`` axis because the
    user-facing badge on each card already shows the pulse, not the
    provision state — keeping the filter taxonomy aligned with what
    operators see avoids the "I clicked Failed but Foo is missing"
    surprise where a ready-but-degraded app would be hidden.
    """

    ALL = "all"
    OK = "ok"
    DEGRADED = "degraded"
    STALE = "stale"
    NEVER_DEPLOYED = "never_deployed"


@strawberry.enum
class AstroliftAppSourceKindFilter(enum.Enum):
    """Source-host axis for the apps-list filter (#481).

    Mirrors :class:`RegisteredApp.SourceKind` plus ``all`` for the
    no-op pass-through. Resolver maps to the row's ``source_kind``
    string with an exact match.
    """

    ALL = "all"
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    GITEA = "gitea"
    GIT_URL = "git_url"


@strawberry.enum
class AppsListSortKey(enum.Enum):
    """Sort axis for ``astroliftAppsPage`` / ``astroliftMyAppsPage`` (#729).

    ``CREATED_DESC`` is the legacy default — identical to the pre-#729
    behaviour.  ``DEPLOYED_DESC`` annotates each row with its most
    recent *successful* deploy timestamp and sorts on that, NULLs last.
    ``NAME_ASC`` is a case-insensitive alphabetical sort on the app name.

    Cursor tokens are keyed to the active sort so seek-pagination stays
    consistent.  Presenting a cursor whose embedded sort key doesn't
    match the current request restarts the walk from page 1 rather than
    producing a corrupt page.
    """

    CREATED_DESC = "created_desc"
    DEPLOYED_DESC = "deployed_desc"
    NAME_ASC = "name_asc"


@strawberry.enum
class AstroliftAppHealthPulseStatus(enum.Enum):
    """Coarse freshness signal for the apps list (#405).

    Derived from the app's most recent deployment; the FE renders one
    coloured dot per row so operators can spot the bad apples without
    drilling into each detail page:

    - ``ok``       — a successful (``running``) deploy within the last 7d
    - ``degraded`` — the most recent deploy is ``failed``
    - ``stale``    — no deploy in the last 30d (even if it succeeded once)
    - ``never``    — the app has no deployments at all yet
    """

    OK = "ok"
    DEGRADED = "degraded"
    STALE = "stale"
    NEVER = "never"


@strawberry.type(name="AstroliftAppHealthPulse")
class AppHealthPulseType:
    """Per-app freshness rollup surfaced on the apps list (#405).

    ``age_seconds`` is the age of the signal used to derive ``status``:
    the most recent deploy's ``created_at`` for ``ok`` / ``degraded`` /
    ``stale``, and ``None`` for ``never`` (there's nothing to age).
    ``message`` is a short, FE-renderable hint operators see in the
    badge tooltip (e.g. ``"latest deploy failed 5m ago"``).
    """

    status: AstroliftAppHealthPulseStatus
    age_seconds: int | None
    message: str


@strawberry.type(name="AstroliftAppDeploymentSummary")
class AppDeploymentSummaryType:
    """Slimmed deployment shape carried inline on each app row (#405).

    A flat scalar projection — no nested user / env types — so the
    apps-list query stays cheap. Powers the row's "last deployed"
    badge and the "Failed" deep-link.
    """

    id: GUID
    status: str
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    created_at: dt.datetime
    environment_name: str
    triggered_by: str
    """Best-effort display label for who/what kicked the deploy:
    the triggering user's email if known, otherwise the trigger
    kind (``push`` / ``ci`` / ``manual`` / …). Empty string when
    neither is available."""

    image_tag: str
    commit_sha: str


@strawberry.type(name="AstroliftAppConfigDrift")
class AppConfigDriftType:
    """Per-app config-drift rollup surfaced on the overview (#407 C).

    Compares the most recently applied ``Deployment.config_snapshot``'s
    ``manifest_hash`` against the live ``RegisteredApp.manifest_hash``
    and, when the platform has a known repo-side hash, against
    ``last_synced_hash`` so an unsynced repo push also reads as drift.

    ``fields`` is a short list of the field-path strings that diverge
    (``manifest_hash``, ``repo_unsynced``, etc.) — the UI renders one
    bullet per entry under the banner so the operator can see *what*
    drifted without opening the rendered manifest tab.

    ``environment_name`` is the env whose deploy snapshot drove the
    comparison (the most recent successful deploy across every env);
    empty string when the app has no qualifying deploy yet, in which
    case ``has_drift`` is False because the platform has nothing to
    compare against.

    ``last_checked`` is the resolver-call timestamp — the comparison
    is computed live (no cron), so this is always "now" from the
    operator's perspective. Surfaced so the FE can render a chip like
    "checked 2s ago" without rolling its own clock state.
    """

    has_drift: bool
    fields: list[str]
    environment_name: str
    last_checked: dt.datetime


@strawberry.type(name="AstroliftAppReprovisionState")
class AppReprovisionStateType:
    """Reprovision-callout payload for the overview (#407 A).

    Derived from ``provisioning_status`` plus the post-ready gap the
    issue calls out (``ready && !registry_repo_uri``). The FE renders
    a state-appropriate banner above the deploy-activity strip when
    ``needs_reprovision`` is True and wires the CTA to the existing
    ``forceAstroliftRedeploy`` mutation.

    ``reason`` is a short human one-liner the banner uses verbatim —
    operator-facing copy lives backend-side so every shell renders
    the same words and we don't drift between locales (i18n on the
    FE still wraps the formatted strings; this is the raw signal).

    ``state`` mirrors ``provisioning_status`` for `failed` / `pending` /
    `provisioning`, plus the synthetic ``ready_missing_registry`` for
    the ready-but-broken case. Empty string when no callout applies.

    ``elapsed_seconds`` carries the age of the last ``updated_at`` bump
    so the FE can render "started 4m ago" for in-flight provisions.
    None when the app's state doesn't warrant a callout."""

    needs_reprovision: bool
    state: str
    reason: str
    elapsed_seconds: int | None


@strawberry.type(name="AstroliftAppAutowireStatus")
class AppAutowireStatusType:
    """Autowire completeness for an app (#1108).

    Registration chains three repo-wiring steps; this rolls their last
    verified outcome up for the app detail page so an operator can see, at a
    glance, whether a git push will actually auto-deploy.

    ``connected`` — an org-level source connection capable of repo ops exists
    (App, or org OAuth/PAT). False is the "registered but not wired — connect
    for auto-deploy" state.

    ``ci_workflow`` / ``webhook`` / ``secrets`` — per-step status:
      * ``ok``       — the step is wired.
      * ``missing``  — the step never ran (autowire hasn't completed yet).
      * ``error``    — the step ran and failed (see ``detail``).
      * ``phantom``  — webhook-only: ``installed_at`` is set but nothing on
        the host actually delivers (the failed-install marker the issue
        reports). Repaired by re-running autowire.

    ``checked_at`` is when autowire last verified the app; null until it has
    run. ``detail`` is a human one-liner concatenating any step errors, empty
    when everything is ``ok``. Computed DB-only (no host round-trip) so it's
    cheap enough for the detail resolver.
    """

    connected: bool
    ci_workflow: str
    webhook: str
    secrets: str
    checked_at: dt.datetime | None
    detail: str


@strawberry.type(name="AstroliftCiWorkflowSyncStatus")
class AppCiWorkflowSyncStatusType:
    """Versioned-sync status for an app's managed CI workflow file (#1209).

    DB-only rollup for the app detail page — reads the persisted
    ``ci_workflow_state`` snapshot plus the ``ci_workflow_template_version``
    column; never hits the source host.

    ``state`` — where the managed file stands. Phase 1 records ``in_sync``
    after a successful reconcile and defaults to ``absent`` when the app has
    never been versioned-synced. (The wider drift vocabulary —
    ``template_stale`` / ``repo_drift`` / ``conflict`` — arrives with the drift
    state machine in a later phase.)

    ``synced_template_version`` is the ``TEMPLATE_VERSION`` last stamped onto
    the repo (null until the first sync); ``current_template_version`` is the
    version the platform renders today — when they differ the app's file is
    stale relative to the current template.

    ``synced_at`` is when the platform last reconciled the file; ``checked_at``
    is when it last verified the repo against the template (null in Phase 1 —
    no inbound check runs yet). ``path`` is the workflow file path, ``pr_url``
    the review link when the change landed in a PR/MR, and ``detail`` a human
    one-liner (empty in Phase 1).
    """

    state: str
    synced_template_version: int | None
    current_template_version: int
    synced_at: dt.datetime | None
    checked_at: dt.datetime | None
    path: str
    pr_url: str
    detail: str
    # The repo's own copy of the file, as of the last pull, and the text the
    # platform renders today. Both empty-string when unavailable rather than
    # null, so the UI never has to distinguish "no pull yet" from "pull
    # returned nothing" to decide whether it can show a diff.
    #
    # Before the pull existed, the only inbound action re-pointed the digests
    # at the repo file and discarded it, so "repo drift" was a badge with
    # nothing behind it: no way to see what drifted before choosing whether
    # to overwrite it.
    repo_text: str
    rendered_text: str
    repo_text_pulled_at: dt.datetime | None


@strawberry.type(name="AstroliftAppDoctorCheck")
class AppDoctorCheckType:
    """One dependency the app needs, and whether it is actually usable.

    Mirrors ``astrolift_registry.services.app_doctor.DoctorCheck``.

    ``status`` distinguishes four things the UI must not conflate:
    ``pass`` / ``fail`` are verified states, ``skip`` means the check does
    not apply to this app, and ``unknown`` means the probe itself errored --
    the dependency is *unverified*, which is not the same as broken.

    ``fix`` is the action handle the UI maps to a button
    (``resync_manifest`` / ``retry_autowire`` / ``rerun_onboarding`` /
    ``redeploy``), empty when there is nothing one click can do.
    """

    key: str
    status: str
    detail: str
    fix: str


@strawberry.type(name="AstroliftAppDoctorReport")
class AppDoctorReportType:
    """The whole verdict for an app.

    ``healthy`` is the service's own rollup rather than something the client
    recomputes, so "is this app fully wired?" has one answer. It counts
    ``skip`` as fine and ``unknown`` as not fine: an unverified dependency
    is not a green light.
    """

    healthy: bool
    checks: list[AppDoctorCheckType]


@strawberry.type(name="AstroliftSecurityPolicy")
class SecurityPolicyType:
    """Resolved supply-chain policy for an app (#313).

    Mirrors ``RegisteredApp.security_policy_resolved`` — every field
    is present even when the underlying ``security_policy`` JSON blob
    is empty, because callers always want the *effective* gate
    settings (platform defaults filled in for unspecified knobs).

    ``block_on_high_cve_threshold`` is nullable: None means "no
    count-based block on high-severity CVEs"; a non-null integer N
    means "block when the high-CVE count is >= N".
    """

    block_on_critical_cves: bool
    block_on_missing_signature: bool
    block_on_high_cve_threshold: int | None


@strawberry.type(name="AstroliftAppSettingsLastModified")
class AppSettingsLastModifiedType:
    """Per-section staleness timestamps for the app settings card grid (#454).

    One ``DateTime | None`` per Settings landing link card. ``None`` means
    the section has no underlying resources yet — the FE hides the
    "Modified N ago" caption rather than rendering a misleading
    fallback. Each timestamp is the ``max(updated_at)`` across the
    section's primary resource scoped to the parent app.

    Replaces the ``app.updatedAt`` proxy the FE was using as a fallback
    (#437 scope E) so each card reports its own freshness instead of a
    single timestamp for the whole app.

    Computed once per ``astroliftApp`` resolver call via aggregate
    sub-queries; the cost is O(sections) cheap-index scans, no per-row
    fanout. The fields are populated only on the single-app
    ``astroliftApp(slug)`` path (where the settings grid renders) —
    list-shape queries (``astroliftApps`` / ``astroliftMyApps``) leave
    the whole struct ``None`` so the cheap list path stays cheap.
    """

    # Deploy strategy lives on the RegisteredApp row + manifest_raw
    # itself (no separate resource), so this mirrors ``app.updated_at``.
    # Surfacing it here keeps every card on the same field shape and
    # lets the FE iterate uniformly.
    deploy_strategy: dt.datetime | None
    deploy_tokens: dt.datetime | None
    secrets: dt.datetime | None
    managed_services: dt.datetime | None
    domains: dt.datetime | None
    webhooks: dt.datetime | None
    members: dt.datetime | None
    observability: dt.datetime | None


@strawberry.type(name="AstroliftProvisioningProgress")
class ProvisioningProgressType:
    """Live step-tracker for an in-flight ``OnboardAppWorkflow``.

    Mirrors the dict returned by the workflow's ``provisioning_progress``
    query handler so the FE can render a per-step indicator without
    rolling its own state machine. ``current_step`` is the active step
    label (e.g. ``"provisioning:registry+namespace"``); ``completed`` is
    the ordered list of step ids that have finished; ``total_steps`` is
    the full canonical sequence the workflow walks.

    Returned ``None`` on the parent field when the app isn't actively
    provisioning, when Temporal is disabled, or when the workflow has
    already completed and the visibility query no longer resolves the
    handle — callers treat that as "no live data, fall back to the
    DB-persisted provisioning_status".
    """

    current_step: str
    completed: list[str]
    total_steps: list[str]


@strawberry.type(name="AstroliftRegisteredApp")
class RegisteredAppType:
    id: GUID
    slug: str
    name: str
    description: str

    organization_slug: str
    team_slug: str
    # ``project_slug`` is empty when the app is unassigned (#391).
    # ``project_id`` / ``project_name`` / ``team_id`` / ``team_name``
    # are the shape the Settings "Assign project" card consumes — flat
    # scalars rather than a nested AstroliftProject type so the
    # registry schema doesn't have to import / re-export the identity
    # type and risk the cross-app re-decoration class of bug.
    project_slug: str
    project_id: GUID | None
    project_name: str
    team_id: GUID | None
    team_name: str

    source_kind: str
    source_repo: str
    source_url: str
    manifest_path: str
    default_branch: str

    # Build configuration. ``build_mode`` is the string value of
    # ``RegisteredApp.BuildMode`` (``ci_pushed`` / ``platform_build`` /
    # ``none``) — kept as a plain ``str`` to match the existing
    # ``source_kind`` / ``trigger_mode`` style on this type. The
    # ``dockerfile_path`` / ``build_context`` / ``build_args`` trio is
    # only consumed under ``platform_build`` but always surfaced so the
    # Settings form can seed every field. ``build_args`` is a flat
    # string→string map serialised as JSON.
    build_mode: str
    dockerfile_path: str
    build_context: str
    build_args: JSON

    manifest_hash: str
    # ``[env]`` values are masked (keys kept) unless the viewer holds
    # ``secret.read`` and is step-up elevated; see ``app_to_type`` /
    # ``can_reveal_app_secrets`` (#1920).
    raw_manifest: str
    raw_manifest_staged: str
    last_synced_hash: str
    manifest_sync_state: str

    registry_repo_uri: str
    # ECR-side coordinates surfaced for the Settings page's CI-setup
    # section (#382). ``ecr_repo_uri`` mirrors ``registry_repo_uri``;
    # the alias is the value an operator pastes into the
    # ``ASTROLIFT_ECR_URI`` GitHub Actions secret. ``ecr_push_role_arn``
    # is the IRSA-bound IAM role the workflow assumes via OIDC and is
    # populated by the cluster-bootstrap path (#309). Stays empty
    # until provisioning lands the role ARN.
    ecr_repo_uri: str
    ecr_push_role_arn: str
    # Provider-plugin slug of the app's default tenant cluster
    # (``aws`` / ``gcp`` / ``azure`` / ``k8s_native``) — #854. Lets the
    # Settings CI-setup card render provider-correct secret names and a
    # matching reference workflow instead of the AWS-only hardcode. Empty
    # string when the app has no default cluster assigned yet (the FK is
    # nullable until provisioning binds a cluster).
    provider_plugin_slug: str
    k8s_namespace: str
    subdomain: str
    managed_hostname: str
    is_active: bool
    provisioning_status: str
    provisioning_error: str
    deploy_token_last_4: str

    log_retention_days: int
    preview_max_active: int
    preview_enabled: bool
    trigger_mode: str
    cron_expression: str
    cron_paused: bool
    deploy_branch: str
    preview_screenshot_url: str

    # Build strategy (#867) — the orthogonal "how to build" axis. "off"
    # means no platform build strategy is selected; "dockerfile" /
    # "buildpacks" / "nixpacks" trigger the build pipeline before the
    # deploy activities run. Distinct from ``build_mode`` above, which
    # decides who publishes the image.
    build_strategy: str

    # App-global webhook-deploy pause (#399). Independent of the
    # per-env axes. ``webhook_deploys_paused_by_email`` is rendered
    # by the Settings card as "Paused by <email>"; null when no actor
    # was captured (e.g. system-initiated automation). The reason
    # string is empty unless the operator supplied one on pause.
    # Archive state (#743). ``is_archived`` is True while ``archived_at`` is set;
    # ``archived_by_email`` is the audit trail actor.
    is_archived: bool
    archived_at: dt.datetime | None
    archived_by_email: str | None

    webhook_deploys_paused: bool
    webhook_deploys_paused_at: dt.datetime | None
    webhook_deploys_paused_by_email: str | None
    webhook_deploys_pause_reason: str

    # Approval policy (#291). ``approver_team_id`` is the team's GUID
    # (or null when no team gate is set); ``approver_user_ids`` are
    # Django user PKs as strings (matching ``AstroliftUser.id``).
    requires_approval: bool
    approver_team_id: GUID | None
    approver_user_ids: list[str]
    minimum_approvals: int

    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None
    # Monotonic per-row version used by the optimistic-concurrency
    # gate on update mutations (#497). Clients fetch this with every
    # read and pass it back as ``ifMatchVersion`` on subsequent writes;
    # a stale value returns a ``VERSION_MISMATCH`` envelope rather than
    # silently overwriting a concurrent edit.
    version: int
    # Last operator-initiated "Resync from source" timestamp (#386).
    # Null until the operator has clicked the button for the first
    # time; surfaces on the Settings page as relative time.
    last_resync_at: dt.datetime | None

    manifest_bootstrap_status: str
    """Outcome of the manifest bootstrap at registration (#1553). ``applied``
    means workloads were materialised; ``parse_failed`` / ``fetch_failed`` /
    ``diverged`` mean the app registered without them and needs attention;
    ``no_source`` means there was nothing to bootstrap from. Empty on apps
    registered before the field landed."""

    manifest_bootstrap_error: str
    """Why the bootstrap did not apply. Empty when it succeeded."""

    # Source-host webhook state (#385). ``installed_at`` is null
    # until the operator clicks "Install webhook" on the Settings
    # page; FE renders it as a green "installed · 5m ago" chip vs
    # an amber "not installed" chip.
    source_webhook_installed_at: dt.datetime | None

    # Supply-chain policy (#313). Always populated — the resolver
    # reads ``security_policy_resolved`` so platform defaults are
    # surfaced for any unspecified knob. The supply-chain Settings
    # card (#307) reads this directly to seed the form.
    security_policy: SecurityPolicyType

    # Per-row deployment freshness (#405). All three are nullable and
    # opt-in via the ``include_freshness`` arg on ``astroliftApps`` /
    # ``astroliftMyApps`` — when the arg is False (default), the
    # resolver leaves these null to keep the cheap list query cheap.
    # ``last_deployed_at`` is the most recent SUCCESSFUL deploy across
    # every env (so "stale" means "no successful deploy in N days",
    # not "no attempt"). ``latest_deployment`` is the most recent
    # deploy regardless of status (so "failed" can surface even if a
    # successful deploy preceded it months ago).
    latest_deployment: AppDeploymentSummaryType | None
    last_deployed_at: dt.datetime | None
    health_pulse: AppHealthPulseType | None

    # Reprovision-callout signals (#407 A). Always populated — the
    # cost is O(1) on the row, so we don't gate it behind an opt-in.
    # ``needs_reprovision`` collapses to False on a healthy ready app
    # so the FE can short-circuit the banner with a single null check.
    reprovision: AppReprovisionStateType

    # Config-drift signals (#407 C). Compares the latest applied
    # ``Deployment.config_snapshot`` against the live manifest hash;
    # opt-in via the ``include_drift`` arg on ``astroliftApp`` so the
    # list path stays cheap (one extra deploy lookup per app row).
    # Left None on the list resolvers and on detail when the arg is
    # False; populated by ``astroliftApp(slug, includeDrift: true)``.
    config_drift: AppConfigDriftType | None

    # Autowire completeness (#1108). Populated only on the single-app detail
    # resolver ``astroliftApp(slug)``; list resolvers leave this None so the
    # cheap list path stays cheap. Drives the "connect for auto-deploy" /
    # "autowire incomplete" banner on the app overview.
    autowire: AppAutowireStatusType | None

    # Managed CI-workflow versioned-sync status (#1209). Populated only on
    # the single-app detail resolver ``astroliftApp(slug)``; list resolvers
    # leave it None so the cheap list path stays cheap. DB-only rollup — see
    # ``AppCiWorkflowSyncStatusType``.
    ci_workflow_sync_status: AppCiWorkflowSyncStatusType | None

    # Per-section "Modified N ago" timestamps for the Settings landing
    # cards (#454). Populated only on the single-app detail resolver
    # ``astroliftApp(slug)``; list resolvers leave this None to keep
    # the cheap path cheap. See ``AppSettingsLastModifiedType``.
    settings_last_modified: AppSettingsLastModifiedType | None

    # Effective permission slugs the viewer holds on THIS app (#478),
    # after scope inheritance (ORG → TEAM → PROJECT → APP). Lets the
    # FE (and mobile next) render only the actions a viewer can take
    # without round-tripping a separate query per app row.
    #
    # Populated by every read path that returns ``RegisteredAppType``:
    # the single-app detail resolver computes it directly; the list
    # resolvers (``astroliftApps`` / ``astroliftMyApps``) compute it
    # in one bulk binding fetch so the field is O(1) per row instead
    # of an N+1 join. Always non-null — an empty list means "viewer
    # has no permissions on this app" (a legitimate, common state),
    # not "we couldn't resolve". See
    # :func:`astrolift_identity.permission_resolver.resolve_effective_permissions_for_apps`.
    viewer_permissions: list[str]

    # Active-preview-environment count (#730). Used by the apps list
    # card to render an "N previews" badge so operators can spot apps
    # with active PR-driven previews without drilling into Previews.
    # 0 is rendered as plain text; > 0 surfaces the badge. Computed in
    # one bulk-aggregate query per page rather than N FK fetches.
    active_preview_count: int = 0
    # Retention policy overrides per signal (#742). Populated lazily by
    # the single-app detail resolver; empty list on list resolvers.
    retention_policies: list[RetentionPolicyType] = strawberry.field(default_factory=list)

    @strawberry.field
    def provisioning_progress(self) -> ProvisioningProgressType | None:
        """Live step-tracker for an in-flight ``OnboardAppWorkflow``.

        Resolves to ``None`` for the common case (any app whose
        ``provisioning_status`` isn't ``"provisioning"``) so the FE can
        cheap-skip the live indicator without a Temporal round-trip.
        Otherwise issues a Temporal query against
        ``OnboardAppWorkflow-<app.guid>`` for the
        ``provisioning_progress`` handler. The handler return shape
        maps 1:1 onto :class:`ProvisioningProgressType` keys.

        Falls back to ``None`` whenever the query can't be answered —
        Temporal disabled, the workflow already completed and was
        archived, or the query handler raised. The FE treats ``None``
        as "no live data" and uses the persisted ``provisioning_status``
        for the static badge instead.
        """
        # Local import — mirrors the pattern in
        # ``astrolift_registry/schema/mutations.py`` and keeps the type
        # module free of the workflows-client import at parse time so
        # the cheap list resolvers don't pay for the Temporal SDK.
        from astrolift_workflows.client import query_workflow

        if (self.provisioning_status or "").strip() != "provisioning":
            return None
        guid = getattr(self, "id", None)
        if not guid:
            return None
        workflow_id = f"OnboardAppWorkflow-{guid}"
        result = query_workflow(workflow_id, "provisioning_progress")
        if result is None:
            return None
        if isinstance(result, dict):
            return ProvisioningProgressType(
                current_step=str(result.get("current_step", "")),
                completed=list(result.get("completed", []) or []),
                total_steps=list(result.get("total_steps", []) or []),
            )
        return None


@strawberry.type(name="AstroliftAppTeamAccess")
class AppTeamAccessType:
    """A team's access grant to an app.

    Joins ``RegisteredApp`` and ``Team`` with an ``access_level`` of
    ``viewer`` / ``deployer`` / ``owner``. Returned by
    ``astroliftAppTeamAccesses(appSlug)`` so the FE can render the
    Teams card on the app-detail page.

    ``is_home`` flags the row that mirrors ``RegisteredApp.team`` —
    the app's primary / home team. Frontend uses this to render the
    home-team affordance separately and to refuse revoking the
    home-team grant directly (callers must move the app to a
    different home team first).
    """

    id: GUID
    app_id: GUID
    app_slug: str
    team_id: GUID
    team_slug: str
    team_name: str
    access_level: str
    is_home: bool
    created_at: dt.datetime
    updated_at: dt.datetime


def app_team_access_to_type(access, *, home_team_id: int) -> AppTeamAccessType:
    return AppTeamAccessType(
        id=GUID(str(access.guid)),
        app_id=GUID(str(access.registered_app.guid)),
        app_slug=access.registered_app.slug,
        team_id=GUID(str(access.team.guid)),
        team_slug=access.team.slug,
        team_name=access.team.name,
        access_level=access.access_level,
        is_home=access.team_id == home_team_id,
        created_at=access.created_at,
        updated_at=access.updated_at,
    )


@strawberry.type(name="AstroliftWorkload")
class WorkloadType:
    id: GUID
    slug: str
    name: str
    kind: str
    is_public: bool
    schedule: str
    # CronJob concurrency policy (#427): ``forbid`` | ``queue`` |
    # ``replace``. Only meaningful when ``kind == "cronjob"``; always
    # present so the FE doesn't have to branch on null. Defaults to
    # ``forbid`` for non-cronjob rows so the badge component never
    # renders garbage.
    concurrency_policy: str
    replicas: int
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    hpa_min_replicas: int | None
    hpa_max_replicas: int | None
    hpa_target_cpu_pct: int
    storage_class: str
    storage_size: str
    registered_app_slug: str
    # The DNS name in-cluster callers use to reach this workload's
    # ClusterIP Service — ``<workloadSlug>.<namespace>.svc.cluster.local``
    # (#429). Same shape kubernetes' default DNS surfaces; the
    # ``namespace`` half mirrors ``namespace_for_app`` (explicit
    # ``app.k8s_namespace`` override → renderer default
    # ``<org>-<app>``). Empty string when neither the workload nor
    # the app has enough state to compute one (e.g. an app row
    # without an organization, which only happens in malformed
    # fixtures).
    in_cluster_service_fqdn: str
    # Volume declarations from the manifest (#739). Each item mirrors
    # the TOML ``[[workloads.<name>.volumes]]`` shape as parsed and
    # stored on the row. Empty list when no volumes are declared.
    volumes: JSON


@strawberry.type(name="AstroliftContainer")
class ContainerType:
    id: GUID
    name: str
    is_primary: bool
    image_ref: str
    dockerfile_path: str
    build_context: str
    port: int
    command: list[str]
    args: list[str]
    # Values masked (keys kept) unless the viewer holds ``secret.read`` and
    # is step-up elevated; see ``container_to_type`` (#1948).
    env: JSON
    healthcheck_kind: str
    healthcheck_value: str
    healthcheck_port: int | None
    workload_slug: str
    # Kubernetes probe configs (#739). Stored as arbitrary JSON matching
    # the K8s probe spec shape so the UI can render them without a
    # typed schema on the platform side. Null when not configured.
    startup_probe: JSON | None = None
    readiness_probe: JSON | None = None
    liveness_probe: JSON | None = None


# Pulse thresholds (#405). Pulled out as module-level constants so
# the resolver, the test cases, and any future "ok / degraded / stale"
# alerter agree on the same windows. Days; converted to a real
# timedelta in the resolver.
HEALTHY_DEPLOY_WINDOW_DAYS = 7
STALE_DEPLOY_WINDOW_DAYS = 30


@dataclasses.dataclass(frozen=True)
class AppFreshness:
    """Resolver-side freshness payload for one app row (#405).

    Built once per ``astroliftApps`` call from a bulk deployment
    query, then handed to ``app_to_type`` so the per-row serialiser
    doesn't need its own DB roundtrip — that's the N+1 guardrail."""

    latest_deployment: AppDeploymentSummaryType | None
    last_deployed_at: dt.datetime | None
    pulse: AppHealthPulseType


def _deployment_to_summary(deployment) -> AppDeploymentSummaryType:
    """Project one ``Deployment`` row into the slim summary type."""
    triggered = ""
    user = getattr(deployment, "triggered_by_user", None)
    if user is not None:
        triggered = getattr(user, "email", "") or getattr(user, "username", "") or ""
    if not triggered:
        triggered = deployment.trigger_kind or ""
    env_name = deployment.app_environment.name if deployment.app_environment_id else ""
    return AppDeploymentSummaryType(
        id=GUID(str(deployment.guid)),
        status=deployment.status,
        started_at=deployment.started_at,
        ended_at=deployment.ended_at,
        created_at=deployment.created_at,
        environment_name=env_name,
        triggered_by=triggered,
        image_tag=deployment.image_tag or "",
        commit_sha=deployment.commit_sha or "",
    )


def build_app_freshness(
    *,
    latest_deployment,
    last_success_at: dt.datetime | None,
    now: dt.datetime,
) -> AppFreshness:
    """Derive the health-pulse rollup from one app's deploy history.

    ``latest_deployment`` is the most recent ``Deployment`` row across
    every env (None when the app has never deployed). ``last_success_at``
    is the ``created_at`` of the most recent ``running`` deploy across
    every env (None when the app never reached running). ``now`` is
    passed in so tests can pin the window without monkey-patching
    ``timezone.now``.

    Pulse rules mirror the docstring on
    :class:`AstroliftAppHealthPulseStatus`. Order matters: a failed
    *latest* deploy always reads as ``degraded`` even if a successful
    deploy is still inside the 7-day window — operators want the most
    recent state surfaced, not the rosiest one.
    """

    if latest_deployment is None:
        return AppFreshness(
            latest_deployment=None,
            last_deployed_at=None,
            pulse=AppHealthPulseType(
                status=AstroliftAppHealthPulseStatus.NEVER,
                age_seconds=None,
                message="no deploys yet",
            ),
        )

    summary = _deployment_to_summary(latest_deployment)
    latest_age = int((now - latest_deployment.created_at).total_seconds())

    if latest_deployment.status == "failed":
        return AppFreshness(
            latest_deployment=summary,
            last_deployed_at=last_success_at,
            pulse=AppHealthPulseType(
                status=AstroliftAppHealthPulseStatus.DEGRADED,
                age_seconds=latest_age,
                message=f"latest deploy failed in {summary.environment_name or 'unknown'}",
            ),
        )

    if last_success_at is None:
        # Latest exists but isn't ``running`` (e.g. it's still
        # pending / deploying or it was superseded). Treat as
        # ``stale`` when older than the stale window, else ``ok`` so
        # in-flight deploys still read healthy. We use the latest
        # row's age — the only deploy we've got.
        if latest_age >= STALE_DEPLOY_WINDOW_DAYS * 86400:
            status = AstroliftAppHealthPulseStatus.STALE
            message = f"no successful deploy in {STALE_DEPLOY_WINDOW_DAYS}d"
        else:
            status = AstroliftAppHealthPulseStatus.OK
            message = f"latest deploy {latest_deployment.status}"
        return AppFreshness(
            latest_deployment=summary,
            last_deployed_at=None,
            pulse=AppHealthPulseType(
                status=status,
                age_seconds=latest_age,
                message=message,
            ),
        )

    success_age = int((now - last_success_at).total_seconds())
    if success_age <= HEALTHY_DEPLOY_WINDOW_DAYS * 86400:
        status = AstroliftAppHealthPulseStatus.OK
        message = "healthy"
    elif success_age >= STALE_DEPLOY_WINDOW_DAYS * 86400:
        status = AstroliftAppHealthPulseStatus.STALE
        message = f"no successful deploy in {STALE_DEPLOY_WINDOW_DAYS}d"
    else:
        # Between 7d and 30d — not fresh, not yet stale. Surface as
        # ``ok`` with the actual age so the FE can decide to soften
        # the badge (or not) without a third state.
        status = AstroliftAppHealthPulseStatus.OK
        message = "healthy"

    return AppFreshness(
        latest_deployment=summary,
        last_deployed_at=last_success_at,
        pulse=AppHealthPulseType(
            status=status,
            age_seconds=success_age,
            message=message,
        ),
    )


def _compute_managed_hostname(app) -> str:
    """Return the full platform-managed hostname for the app.

    Pattern: ``<subdomain>.<zone>`` where zone comes from the platform
    or org-scoped ManagedDomain. Returns "" when no domain is configured.
    """
    try:
        from astrolift_clusters.models import resolve_managed_domain

        org = getattr(app, "organization", None)
        domain = resolve_managed_domain(org, for_preview=False)
        if domain is None:
            return ""
        subdomain = (app.subdomain or app.slug or "").strip()
        if not subdomain:
            return ""
        return f"{subdomain}.{domain.zone}"
    except Exception:
        return ""


def _provider_plugin_slug(app) -> str:
    """Slug of the provider plugin backing the app's default cluster (#854).

    Walks ``default_tenant_cluster -> provider_plugin -> slug``. The
    cluster FK is nullable (an app may not have a default cluster bound
    yet), so we return "" when it's unset; ``TenantCluster.provider_plugin``
    is itself non-null, so once the cluster resolves the slug always does.
    """
    cluster = app.default_tenant_cluster if app.default_tenant_cluster_id else None
    if cluster is None:
        return ""
    return cluster.provider_plugin.slug


def app_to_type(
    app,
    *,
    info: Info,
    freshness: AppFreshness | None = None,
    drift: AppConfigDriftType | None = None,
    autowire: AppAutowireStatusType | None = None,
    ci_workflow_sync_status: AppCiWorkflowSyncStatusType | None = None,
    settings_last_modified: AppSettingsLastModifiedType | None = None,
    viewer_permissions: Iterable[str] | None = None,
    active_preview_count: int | None = None,
    managed_hostname: str | None = None,
    include_retention_policies: bool = False,
) -> RegisteredAppType:
    from astrolift_manifest.sync_state import (
        SyncSnapshot,
        classify_state,
    )

    sync_state = classify_state(
        SyncSnapshot(
            db_hash=app.manifest_hash or "",
            repo_hash=_repo_hash_for(app),
            last_synced_hash=app.last_synced_hash or "",
        )
    )
    reprovision = build_reprovision_state(app)
    project = app.project if app.project_id else None
    # #1920: ``raw_manifest`` / ``raw_manifest_staged`` carry the app's
    # ``[env]`` table in the clear. Anyone with ``app.read`` can reach
    # this type, but those are the values ``revealAppSecret`` requires
    # ``secret.read`` + a fresh step-up elevation for; apply the identical
    # gate here or that mutation's guard is decorative.
    from astrolift_services.secret_visibility import can_reveal_app_secrets

    raw_manifest = app.manifest_raw or ""
    raw_manifest_staged = app.manifest_raw_staged or ""
    if (raw_manifest or raw_manifest_staged) and not can_reveal_app_secrets(
        info, app=app, known_permissions=viewer_permissions
    ):
        from astrolift_manifest.env_edit import redact_env_values

        raw_manifest = redact_env_values(raw_manifest)
        raw_manifest_staged = redact_env_values(raw_manifest_staged)
    return RegisteredAppType(
        id=GUID(str(app.guid)),
        slug=app.slug,
        name=app.name,
        description=app.description or "",
        organization_slug=app.organization.slug,
        team_slug=app.team.slug,
        project_slug=project.slug if project else "",
        project_id=GUID(str(project.guid)) if project else None,
        project_name=project.name if project else "",
        team_id=GUID(str(app.team.guid)),
        team_name=app.team.name,
        source_kind=app.source_kind,
        source_repo=app.source_repo,
        source_url=app.source_url,
        manifest_path=app.manifest_path,
        default_branch=app.default_branch,
        build_mode=app.build_mode,
        build_strategy=app.build_strategy,
        dockerfile_path=app.dockerfile_path or "",
        build_context=app.build_context or "",
        build_args=dict(app.build_args or {}),
        manifest_hash=app.manifest_hash,
        raw_manifest=raw_manifest,
        raw_manifest_staged=raw_manifest_staged,
        last_synced_hash=app.last_synced_hash or "",
        manifest_sync_state=sync_state.value,
        registry_repo_uri=app.registry_repo_uri,
        ecr_repo_uri=app.registry_repo_uri,
        ecr_push_role_arn=app.push_role_ref or "",
        provider_plugin_slug=_provider_plugin_slug(app),
        k8s_namespace=app.k8s_namespace,
        subdomain=app.subdomain,
        managed_hostname=(
            managed_hostname if managed_hostname is not None else _compute_managed_hostname(app)
        ),
        is_active=app.is_active,
        provisioning_status=app.provisioning_status,
        provisioning_error=app.provisioning_error,
        deploy_token_last_4=app.deploy_token_last_4,
        log_retention_days=app.log_retention_days,
        preview_max_active=app.preview_max_active,
        preview_enabled=app.preview_enabled,
        trigger_mode=app.trigger_mode,
        cron_expression=app.cron_expression or "",
        cron_paused=bool(app.cron_paused),
        deploy_branch=app.deploy_branch,
        preview_screenshot_url=app.preview_screenshot_url or "",
        is_archived=bool(app.archived_at),
        archived_at=app.archived_at,
        archived_by_email=_archived_by_email(app),
        webhook_deploys_paused=bool(app.webhook_deploys_paused),
        webhook_deploys_paused_at=app.webhook_deploys_paused_at,
        webhook_deploys_paused_by_email=_paused_by_email(app),
        webhook_deploys_pause_reason=app.webhook_deploys_pause_reason or "",
        requires_approval=bool(app.requires_approval),
        approver_team_id=(GUID(str(app.approver_team.guid)) if app.approver_team_id else None),
        # ``.all()`` (vs ``.values_list``) uses the prefetch cache when a
        # caller pre-fetched ``approver_users`` — keeps the apps-list
        # resolvers (#481) from re-issuing one M2M query per row.
        approver_user_ids=[str(u.pk) for u in app.approver_users.all()],
        minimum_approvals=int(app.minimum_approvals or 1),
        created_at=app.created_at,
        updated_at=app.updated_at,
        deleted_at=app.deleted_at,
        version=int(app.version or 0),
        last_resync_at=app.last_resync_at,
        manifest_bootstrap_status=app.manifest_bootstrap_status,
        manifest_bootstrap_error=app.manifest_bootstrap_error,
        source_webhook_installed_at=app.source_webhook_installed_at,
        security_policy=_security_policy_to_type(app),
        latest_deployment=(freshness.latest_deployment if freshness else None),
        last_deployed_at=(freshness.last_deployed_at if freshness else None),
        health_pulse=(freshness.pulse if freshness else None),
        reprovision=reprovision,
        config_drift=drift,
        autowire=autowire,
        ci_workflow_sync_status=ci_workflow_sync_status,
        settings_last_modified=settings_last_modified,
        viewer_permissions=sorted(viewer_permissions) if viewer_permissions is not None else [],
        active_preview_count=(
            int(active_preview_count) if active_preview_count is not None else _fallback_preview_count(app)
        ),
        retention_policies=(
            [retention_policy_to_type(rp) for rp in app.retention_policies.filter(deleted_at__isnull=True)]
            if include_retention_policies
            else []
        ),
    )


def _fallback_preview_count(app) -> int:
    """Per-row preview count for callers that didn't pre-aggregate.

    The list resolvers compute this in one bulk query and pass it in;
    the single-app resolver (and any code path that didn't pre-aggregate)
    falls through here and pays one extra query per app. Cheap on the
    detail page; the list path is the one that has to stay O(1) per
    row, and that path always passes ``active_preview_count`` explicitly."""
    from astrolift_lifecycle.models import PreviewEnvironment

    return PreviewEnvironment.objects.filter(
        registered_app_id=app.pk,
        torn_down_at__isnull=True,
        deleted_at__isnull=True,
    ).count()


def _archived_by_email(app) -> str | None:
    user_id = getattr(app, "archived_by_id", None)
    if user_id is None:
        return None
    user = app.archived_by
    if user is None:
        return None
    email = getattr(user, "email", "") or getattr(user, "username", "")
    return email or None


def _paused_by_email(app) -> str | None:
    """Resolve the display email for the user who flipped
    ``webhook_deploys_paused`` to True (#399).

    Returns None when no actor is captured on the row — system-fired
    pauses, or the column is null because the flag has never been set.
    The User FK is set to NULL on user deletion (``on_delete=SET_NULL``)
    so historic pauses don't go stale; we surface the empty case as
    None and let the UI render "system" / "unknown" copy.
    """
    user_id = getattr(app, "webhook_deploys_paused_by_id", None)
    if user_id is None:
        return None
    user = app.webhook_deploys_paused_by
    if user is None:
        return None
    email = getattr(user, "email", "") or getattr(user, "username", "")
    return email or None


_REPROVISION_STATE_READY_MISSING_REGISTRY = "ready_missing_registry"
_REPROVISION_REASONS = {
    "failed": "Provisioning failed — retry to rebuild the registry repo and namespace.",
    "pending": "Provisioning hasn't started yet — kick it off to bring the app online.",
    "provisioning": "Provisioning is in progress — re-run only if it stalls.",
    _REPROVISION_STATE_READY_MISSING_REGISTRY: (
        "App is ready but the container registry coordinates are missing — "
        "reprovision to rebuild the ECR repo and push role."
    ),
}


def _app_builds_an_image(app) -> bool:
    """Whether the platform ever needs an ECR repo + push role for this app.

    Thin delegate to ``RegisteredApp.builds_an_image``, which is where the rule
    lives now: the provisioning activity needs the same predicate and cannot
    import this schema module (#1682). Kept as a function so the duck-typed
    callers in this module's tests keep working.
    """
    from astrolift_registry.models import RegisteredApp

    build_mode = (getattr(app, "build_mode", "") or "").strip()
    source_kind = (getattr(app, "source_kind", "") or "").strip()
    if build_mode == RegisteredApp.BuildMode.NONE.value:
        return False
    return source_kind != RegisteredApp.SourceKind.DIRECT_UPLOAD.value


def build_reprovision_state(app) -> AppReprovisionStateType:
    """Derive the reprovision-callout payload for one app (#407 A).

    The platform never persists a "needs reprovision" boolean — the
    UI computes the call from the state machine here so the rule
    lives in one place. ``elapsed_seconds`` is the age of the last
    ``updated_at`` bump on the app row (transitions advance it via
    ``transition_provisioning``), which is what the FE renders as
    "started X min ago" while a provisioning attempt is in flight.

    The synthetic ``ready_missing_registry`` state catches the post-
    ready gap where ``provisioning_status == 'ready'`` but
    ``registry_repo_uri`` is empty — that combination means the
    bring-up loop landed the manifest + DNS but lost the ECR setup,
    and a deploy will silently no-op until reprovisioned. This gap only
    matters for apps that actually build an image; a pre-built-image app
    (``build_mode == none`` or ``source_kind == direct_upload``) never
    pushes to ECR, so a missing registry URI is the normal steady state
    for it and must NOT raise the callout.
    """

    status = (app.provisioning_status or "").strip()
    elapsed: int | None = None
    updated_at = getattr(app, "updated_at", None)
    if updated_at is not None:
        elapsed = max(0, int((dt.datetime.now(dt.UTC) - updated_at).total_seconds()))

    if status == "ready":
        if _app_builds_an_image(app) and not (app.registry_repo_uri or "").strip():
            return AppReprovisionStateType(
                needs_reprovision=True,
                state=_REPROVISION_STATE_READY_MISSING_REGISTRY,
                reason=_REPROVISION_REASONS[_REPROVISION_STATE_READY_MISSING_REGISTRY],
                elapsed_seconds=elapsed,
            )
        return AppReprovisionStateType(
            needs_reprovision=False,
            state="",
            reason="",
            elapsed_seconds=None,
        )

    if status in _REPROVISION_REASONS:
        return AppReprovisionStateType(
            needs_reprovision=True,
            state=status,
            reason=_REPROVISION_REASONS[status],
            elapsed_seconds=elapsed,
        )

    # Defensive: an unknown / empty status string still surfaces a
    # banner so an operator isn't left guessing. Falls back to a
    # generic message keyed on the raw status so support can ask
    # "what state is the row in?" without a DB shell.
    return AppReprovisionStateType(
        needs_reprovision=True,
        state=status or "unknown",
        reason=f"Unknown provisioning state {status!r} — reprovision to recover.",
        elapsed_seconds=elapsed,
    )


def build_config_drift(app, *, now: dt.datetime | None = None) -> AppConfigDriftType:
    """Compare the latest applied deploy snapshot against the live
    manifest hash (#407 C).

    Drift is True when any of:

    * The DB manifest hash diverges from the snapshot the last
      successful deploy applied — the operator edited the manifest
      after the deploy and hasn't redeployed yet.
    * The repo-side hash is ahead of the DB (``manifest_sync_state``
      reads ``repo_ahead`` or ``diverged``) — a developer pushed a
      manifest update that the platform hasn't picked up.

    Both checks live here so the FE renders one banner that covers
    both authoring sources (UI edits + repo pushes) instead of two
    competing surfaces. ``fields`` is a stable list of field-path
    strings the FE bullets under the headline.

    Returns an "in sync" payload (``has_drift=False``) when the app
    has no qualifying deploy snapshot — we don't surface drift
    against nothing, that's noise.
    """
    # Local imports keep the registry types module free of the
    # lifecycle / manifest imports for the cheap list resolvers.
    from astrolift_lifecycle.models import Deployment
    from astrolift_manifest.sync_state import SyncSnapshot, SyncState, classify_state

    when = now or dt.datetime.now(dt.UTC)

    latest = (
        Deployment.objects.filter(
            registered_app_id=app.pk,
            deleted_at__isnull=True,
        )
        .exclude(status=Deployment.Status.PENDING_APPROVAL.value)
        .exclude(status=Deployment.Status.PENDING.value)
        .order_by("-created_at")
        .select_related("app_environment")
        .first()
    )

    fields: list[str] = []
    env_name = ""
    snapshot_hash = ""
    snapshot_image_tag = ""

    if latest is not None:
        env_name = latest.app_environment.name if latest.app_environment_id else ""
        snapshot = latest.config_snapshot or {}
        snapshot_hash = str(snapshot.get("manifest_hash", "")).strip()
        snapshot_image_tag = str(snapshot.get("image_tag", "")).strip()
        current_hash = (app.manifest_hash or "").strip()
        if snapshot_hash and current_hash and snapshot_hash != current_hash:
            fields.append("manifest_hash")
        if snapshot_image_tag and latest.image_tag and snapshot_image_tag != latest.image_tag:
            # Different image tag between what the snapshot captured
            # and what the deploy row records — narrow but real:
            # surfaces when a manual cluster touch overrode the tag.
            fields.append("image_tag")

    sync_state = classify_state(
        SyncSnapshot(
            db_hash=app.manifest_hash or "",
            repo_hash=_repo_hash_for(app),
            last_synced_hash=app.last_synced_hash or "",
        )
    )
    if sync_state in (SyncState.REPO_AHEAD, SyncState.DIVERGED):
        fields.append("repo_unsynced")

    return AppConfigDriftType(
        has_drift=bool(fields),
        fields=fields,
        environment_name=env_name,
        last_checked=when,
    )


def _autowire_org_connection_available(app) -> bool:
    """DB-only: does an org connection capable of repo ops exist? (#1108)"""
    from astrolift_scm.services.connection_resolver import (
        ORG_REPO_WRITE,
        ConnectionResolutionError,
        resolve_connection,
    )

    try:
        resolve_connection(app.organization_id, purpose=ORG_REPO_WRITE, source_kind=app.source_kind)
        return True
    except ConnectionResolutionError:
        return False


def _autowire_webhook_status(app, state: dict) -> str:
    """Webhook sub-status for the autowire rollup (#1108).

    Prefer the last verified verdict: after an autowire run the webhook is
    never left "phantom" — the reconcile either repaired it to a real hook /
    app_delivers (``ok``) or the install honestly failed (``error``). Fall
    back to deriving from the columns for a legacy app that never ran the
    chain: a recorded ``source_webhook_id`` reads ``ok``; ``installed_at``
    set with an EMPTY id is the phantom the issue reports (a failed install
    that advanced the timestamp) — surface it as ``phantom`` so the banner
    prompts a repair. A legacy App-delivers app (empty id, genuinely covered)
    also lands here as ``phantom``; that's the conservative call — a retry
    re-verifies coverage and flips it to ``ok`` — rather than trusting an
    unverified empty marker.
    """
    from astrolift_scm.services.autowire import MISSING

    verdict = state.get("webhook")
    if verdict:
        return verdict
    if app.source_webhook_id:
        return "ok"
    if app.source_webhook_installed_at is not None:
        return "phantom"
    return MISSING


def build_autowire_status(app) -> AppAutowireStatusType:
    """Roll up the app's autowire completeness for the detail page (#1108).

    DB-only — reads the persisted ``autowire_state`` snapshot plus the webhook
    columns; never hits the host. ``connected`` is a live ORM check so the
    "connect for auto-deploy" callout reflects the org's current connections
    even if autowire hasn't re-run since one was added.
    """
    from astrolift_scm.services.autowire import MISSING

    state = app.autowire_state or {}
    errors = state.get("errors") or {}

    checked_raw = state.get("checked_at")
    checked_at = None
    if checked_raw:
        try:
            checked_at = dt.datetime.fromisoformat(checked_raw)
        except (TypeError, ValueError):
            checked_at = None

    detail = "; ".join(f"{step.replace('_', ' ')}: {msg}" for step, msg in errors.items())

    return AppAutowireStatusType(
        connected=_autowire_org_connection_available(app),
        ci_workflow=state.get("ci_workflow") or MISSING,
        webhook=_autowire_webhook_status(app, state),
        secrets=state.get("secrets") or MISSING,
        checked_at=checked_at,
        detail=detail,
    )


# Never-synced default for the managed CI-workflow read status (#1209). The
# fuller drift vocabulary (in_sync / template_stale / repo_drift / conflict)
# lands with the drift state machine in a later phase; Phase 1 only ever
# persists "in_sync", so an app with no sync record reads as "absent" — from
# the DB's point of view the file was never versioned-synced.
_CI_WORKFLOW_STATE_ABSENT = "absent"


def _rendered_ci_workflow_or_empty(app) -> str:
    """The workflow text the platform would push for ``app`` right now.

    Pure render (string substitution over a template), no network. Returns
    "" for an app the renderer cannot serve -- an unsupported source host,
    or a missing field the template needs -- because this feeds a diff view
    that must not fail the whole app query.
    """
    try:
        from astrolift_scm.services.workflow_sync import _render_and_path

        body, _path = _render_and_path(app)
    except Exception:  # noqa: BLE001 - a render failure is "no preview", not an error
        return ""
    return body or ""


def build_ci_workflow_sync_status(app) -> AppCiWorkflowSyncStatusType:
    """Roll up the managed CI-workflow versioned-sync status (#1209).

    DB-only — reads the persisted ``ci_workflow_state`` blob and the
    ``ci_workflow_template_version`` column; never hits the source host. Cheap
    enough for the detail resolver (both are columns already loaded on ``app``).
    """
    from astrolift_scm.ci_templates import TEMPLATE_VERSION

    state = app.ci_workflow_state or {}

    def _iso_or_none(raw) -> dt.datetime | None:
        if not raw:
            return None
        try:
            return dt.datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None

    return AppCiWorkflowSyncStatusType(
        state=state.get("state") or _CI_WORKFLOW_STATE_ABSENT,
        synced_template_version=app.ci_workflow_template_version,
        current_template_version=TEMPLATE_VERSION,
        synced_at=_iso_or_none(state.get("synced_at")),
        checked_at=_iso_or_none(state.get("checked_at")),
        path=state.get("path") or "",
        pr_url=state.get("pr_url") or "",
        detail=state.get("detail") or "",
        repo_text=state.get("repo_text") or "",
        rendered_text=_rendered_ci_workflow_or_empty(app),
        repo_text_pulled_at=_iso_or_none(state.get("repo_text_pulled_at")),
    )


def build_app_doctor_report(app) -> AppDoctorReportType:
    """Run the doctor and shape it for GraphQL.

    Thin on purpose: every judgement lives in the service, which promises
    never to raise, so this resolver has nothing to catch and no policy of
    its own.

    The ``resolve`` parameter is gone (#1550). The doctor no longer resolves
    hostnames -- it reads a cached probe refreshed on a schedule -- so there
    is no resolver for a caller to inject, and this resolver now makes no
    network call at all. That is the property that makes it safe on the
    app-detail page.
    """
    from astrolift_registry.services.app_doctor import run_app_doctor

    report = run_app_doctor(app)
    return AppDoctorReportType(
        healthy=report.healthy,
        checks=[
            AppDoctorCheckType(key=c.key, status=c.status, detail=c.detail, fix=c.fix) for c in report.checks
        ],
    )


def build_settings_last_modified(app) -> AppSettingsLastModifiedType:
    """Derive the per-section "Modified N ago" timestamps for the
    Settings landing card grid (#454).

    One ``MAX(updated_at)`` per section's primary resource, scoped to
    the parent app and excluding soft-deleted rows. Returns ``None``
    for any section that has no rows yet so the FE can hide the
    caption rather than render a misleading default. The fields map
    1:1 onto the ``LINK_SECTIONS`` array consumed by
    ``settings-client.tsx``.

    Cost: 8 cheap aggregates against indexed FKs. Used only on the
    single-app detail resolver (``astroliftApp(slug)``) — the list
    paths leave the wrapper ``None`` so the cheap O(rows) path stays
    cheap.
    """
    # Local imports keep the registry types module decoupled from the
    # lifecycle / services / operations / identity domains for the
    # cheap list resolvers — these are only walked on the detail path.
    from astrolift_identity.models.role_binding import RoleBinding
    from astrolift_lifecycle.models.deploy_token import DeployToken
    from astrolift_lifecycle.models.ingress import CustomDomain
    from astrolift_operations.models.alert import AlertRule
    from astrolift_operations.models.webhook_subscription import WebhookSubscription
    from astrolift_services.models.managed_service import ManagedServiceBinding
    from astrolift_services.models.secret_bundle import AppSecretBundleRef

    def _max_updated_at(qs) -> dt.datetime | None:
        return qs.filter(deleted_at__isnull=True).aggregate(v=models.Max("updated_at"))["v"]

    deploy_tokens = _max_updated_at(DeployToken.objects.filter(registered_app_id=app.pk))
    secrets = _max_updated_at(AppSecretBundleRef.objects.filter(registered_app_id=app.pk))
    # Bindings are the user-visible mutation surface for managed
    # services (env-var wiring); service rows themselves churn on
    # background status updates and would mis-report freshness if used.
    managed_services = _max_updated_at(
        ManagedServiceBinding.objects.filter(managed_service__registered_app_id=app.pk)
    )
    domains = _max_updated_at(CustomDomain.objects.filter(registered_app_id=app.pk))
    webhooks = _max_updated_at(WebhookSubscription.objects.filter(registered_app_id=app.pk))
    members = _max_updated_at(
        RoleBinding.objects.filter(
            scope_kind=RoleBinding.ScopeKind.APP,
            scope_id=app.pk,
        )
    )
    # AlertRule.target_id is the slug or guid of the targeted object;
    # for ``target=app`` the slug is the canonical key (used elsewhere
    # in the alert pipeline). Org-scoped global rules don't surface on
    # the per-app card.
    observability = _max_updated_at(
        AlertRule.objects.filter(
            target=AlertRule.Target.APP,
            target_id=app.slug,
        )
    )
    # Deploy strategy lives on the RegisteredApp row + manifest_raw
    # itself; there's no separate resource to aggregate so the app's
    # own ``updated_at`` is the freshness signal.
    deploy_strategy = app.updated_at

    return AppSettingsLastModifiedType(
        deploy_strategy=deploy_strategy,
        deploy_tokens=deploy_tokens,
        secrets=secrets,
        managed_services=managed_services,
        domains=domains,
        webhooks=webhooks,
        members=members,
        observability=observability,
    )


def _security_policy_to_type(app) -> SecurityPolicyType:
    resolved = app.security_policy_resolved
    threshold = resolved["block_on_high_cve_threshold"]
    return SecurityPolicyType(
        block_on_critical_cves=bool(resolved["block_on_critical_cves"]),
        block_on_missing_signature=bool(resolved["block_on_missing_signature"]),
        block_on_high_cve_threshold=int(threshold) if threshold is not None else None,
    )


def _namespace_for_workload(workload) -> str:
    """Resolve the namespace for ``workload.registered_app``.

    Mirrors :func:`core.cluster_observability.namespace_for_app`
    (explicit row-level override → renderer default
    ``<orgSlug>-<appSlug>``) but stays local to avoid pulling
    ``core.cluster_observability`` (and its driver-registry imports)
    into the registry schema module."""
    app = workload.registered_app
    explicit = (getattr(app, "k8s_namespace", "") or "").strip()
    if explicit:
        return explicit
    org_slug = (getattr(getattr(app, "organization", None), "slug", "") or "").strip()
    if not org_slug:
        return ""
    return app_namespace(organization_slug=org_slug, app_slug=str(app.slug))


def _in_cluster_service_fqdn(workload) -> str:
    """``<workloadSlug>.<namespace>.svc.cluster.local`` — empty when
    we can't build a namespace half (no org on the app, etc.)."""
    namespace = _namespace_for_workload(workload)
    if not namespace or not workload.slug:
        return ""
    return f"{workload.slug}.{namespace}.svc.cluster.local"


@strawberry.type(name="AstroliftWorkloadScalingStatus")
class WorkloadScalingStatus:
    """Live + configured scaling status for one workload (#430).

    Composes the manifest-side HPA configuration (``hpa_min_replicas``,
    ``hpa_max_replicas``, ``hpa_target_cpu_pct`` from the Workload row)
    with the live Deployment status read from the cluster
    (``current_replicas`` = ``status.readyReplicas``,
    ``desired_replicas`` = ``spec.replicas``).

    ``hpa_enabled`` is True when the manifest declares both an HPA min
    and an HPA max — the renderer emits a HorizontalPodAutoscaler
    resource only in that case, so this flag is the single source of
    truth for "is auto-scaling on?".

    ``is_scaling`` is the resolver-computed delta — True when
    ``current != desired``. The FE renders the "Scaling…" indicator on
    this signal.

    ``replica_upper_bound`` is the maximum the manual-scale slider
    should allow. It mirrors :func:`resolve_replica_bounds` so the
    slider can't propose a value the mutation would reject.

    Live fields fall back to manifest values when the driver doesn't
    surface them (cluster unreachable / unwired); the FE renders the
    same gauge in either case but the "Scaling…" indicator stays off.
    """

    hpa_enabled: bool
    hpa_min_replicas: int | None
    hpa_max_replicas: int | None
    hpa_target_cpu_pct: int
    current_replicas: int
    desired_replicas: int
    is_scaling: bool
    replica_lower_bound: int
    replica_upper_bound: int
    sourced_at: dt.datetime


def workload_to_type(workload) -> WorkloadType:
    return WorkloadType(
        id=GUID(str(workload.guid)),
        slug=workload.slug,
        name=workload.name,
        kind=workload.kind,
        is_public=workload.is_public,
        schedule=workload.schedule or "",
        concurrency_policy=workload.concurrency_policy or "forbid",
        replicas=workload.replicas,
        cpu_request=workload.cpu_request or "",
        cpu_limit=workload.cpu_limit or "",
        memory_request=workload.memory_request or "",
        memory_limit=workload.memory_limit or "",
        hpa_min_replicas=workload.hpa_min_replicas,
        hpa_max_replicas=workload.hpa_max_replicas,
        hpa_target_cpu_pct=workload.hpa_target_cpu_pct,
        storage_class=workload.storage_class or "",
        storage_size=workload.storage_size or "",
        registered_app_slug=workload.registered_app.slug,
        in_cluster_service_fqdn=_in_cluster_service_fqdn(workload),
        volumes=list(workload.volumes or []),
    )


def _repo_hash_for(app) -> str:
    """Best-known repo-side manifest hash. The DB doesn't track the
    repo-side hash on the app row directly; the SCM sync workflow
    populates `last_synced_hash` after a successful pull. Until a
    real fetch lands, use last_synced_hash as the floor — it's the
    last hash we know was on the repo. The `syncManifestFromRepo`
    mutation refreshes this."""
    return app.last_synced_hash or app.manifest_hash or ""


@strawberry.type(name="AstroliftWorkloadManifest")
class WorkloadManifestType:
    """Rendered Kubernetes resources scoped to one workload (#430).

    Mirrors :class:`RenderedManifestType` but filters ``resources`` to
    those whose ``metadata.labels['astrolift.dev/workload']`` matches
    the requested workload slug — the per-workload subset of the
    app-wide manifest the deploy activity would apply.

    ``previous_image_tag`` is populated from the most-recent prior
    deployment for the same (app, environment) so the FE can render a
    diff against the previously deployed image without needing a
    second round-trip. Empty string when no prior deployment exists.

    ``previous_deployment_id`` is the GUID of that prior deployment
    (so the FE can link to it). Empty string when there's no prior.

    The error fields mirror the app-level resolver — parse / normalize
    errors carry a human-readable message and (where available) a
    1-based source position so the editor can squiggle.
    """

    app_slug: str
    workload_slug: str
    environment_name: str
    image_tag: str
    namespace: str
    resources: JSON
    previous_image_tag: str
    previous_deployment_id: str
    resources_previous: JSON
    error: str | None
    error_path: str | None
    error_line: int | None
    error_column: int | None


@strawberry.type(name="AstroliftRenderedManifest")
class RenderedManifestType:
    """The output of running the platform renderer against the
    stored TOML for a given (app, environment) pair.

    ``resources`` is a JSON list of Kubernetes resource dicts
    (apiVersion/kind/metadata/spec). The ``error`` field carries a
    human-readable message when parsing or normalization failed —
    callers render either the resources or the error, never both.

    ``error_line`` and ``error_column`` are 1-based source positions
    that point to the offending TOML line so editors can show a red
    squiggle. Both are ``None`` when the position couldn't be
    located (e.g. a missing required key has no source position)."""

    app_slug: str
    environment_name: str
    image_tag: str
    namespace: str
    resources: JSON
    error: str | None
    error_path: str | None
    error_line: int | None
    error_column: int | None


@strawberry.type(name="AstroliftRegisteredAppPage")
class RegisteredAppPageType:
    """Cursor-paginated slice of registered apps (#481).

    Replaces the flat-list shape on the new ``astroliftAppsPage`` /
    ``astroliftMyAppsPage`` queries. ``next_cursor`` is null when the
    caller has reached the end of the result. ``total_count`` is the
    filtered total (not the table total) so the FE can render
    "Showing N of M" without a separate aggregate query.

    Cursor format: base64-JSON of ``[created_at_iso, guid_str]`` over
    the ``(-created_at, -guid)`` seek key — stable across deletes
    because the seek key never reuses values (guid is a UUIDv4 / v7
    so the secondary sort is also globally unique). Garbage cursors
    decode to ``None`` and restart from the top; we prefer "restart
    UX" over "hard error" so a stale share-link doesn't strand the
    operator.
    """

    items: list[RegisteredAppType]
    next_cursor: str | None
    total_count: int


def container_to_type(container, *, env_revealed: bool) -> ContainerType:
    """``env_revealed`` is ``can_reveal_app_secrets`` for the container's
    app: env holds the same literals the manifest text masks, so a caller
    who cannot reveal them gets the keys with masked values (#1948)."""
    from astrolift_manifest.env_edit import REDACTED_ENV_VALUE

    env = container.env or {}
    return ContainerType(
        id=GUID(str(container.guid)),
        name=container.name,
        is_primary=container.is_primary,
        image_ref=container.image_ref or "",
        dockerfile_path=container.dockerfile_path,
        build_context=container.build_context,
        port=container.port,
        command=list(container.command or []),
        args=list(container.args or []),
        env=env if env_revealed else dict.fromkeys(env, REDACTED_ENV_VALUE),
        healthcheck_kind=container.healthcheck_kind,
        healthcheck_value=container.healthcheck_value or "",
        healthcheck_port=container.healthcheck_port,
        workload_slug=container.workload.slug,
        startup_probe=container.startup_probe,
        readiness_probe=container.readiness_probe,
        liveness_probe=container.liveness_probe,
    )


@strawberry.type(name="AstroliftRetentionPolicy")
class RetentionPolicyType:
    id: GUID
    signal: str
    retention_days: int
    registered_app_slug: str
    created_at: dt.datetime


def retention_policy_to_type(rp) -> RetentionPolicyType:
    return RetentionPolicyType(
        id=GUID(str(rp.guid)),
        signal=rp.signal,
        retention_days=rp.retention_days,
        registered_app_slug=rp.registered_app.slug,
        created_at=rp.created_at,
    )
