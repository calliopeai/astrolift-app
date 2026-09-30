"""GraphQL types for AppEnvironment, Deployment, DeploymentLog,
PreviewEnvironment, IngressRule, CustomDomain."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID
from core.schema.enums import ObservabilityPanelReason

JSON = strawberry.scalars.JSON


@strawberry.type(name="AstroliftReleaseNotesCommit")
class ReleaseNotesCommitType:
    sha: str
    subject: str
    author: str
    is_merge: bool


@strawberry.type(name="AstroliftReleaseNotesPR")
class ReleaseNotesPRType:
    number: int
    title: str
    body: str
    author: str
    merged_at: str | None
    pr_url: str


@strawberry.type(name="AstroliftReleaseNotes")
class ReleaseNotesType:
    base_sha: str
    head_sha: str
    commits: list[ReleaseNotesCommitType]
    pull_requests: list[ReleaseNotesPRType]
    compare_url: str


def release_notes_to_type(rn) -> ReleaseNotesType:
    return ReleaseNotesType(
        base_sha=rn.base_sha,
        head_sha=rn.head_sha,
        commits=[
            ReleaseNotesCommitType(
                sha=c.sha,
                subject=c.subject,
                author=c.author,
                is_merge=c.is_merge,
            )
            for c in rn.commits
        ],
        pull_requests=[
            ReleaseNotesPRType(
                number=p.number,
                title=p.title,
                body=p.body,
                author=p.author,
                merged_at=p.merged_at,
                pr_url=p.pr_url,
            )
            for p in rn.pull_requests
        ],
        compare_url=rn.compare_url,
    )


@strawberry.type(name="AstroliftManifestDiffEntry")
class ManifestDiffEntryType:
    """One changed resource in a ``compareDeployments`` manifest diff.

    ``op`` mirrors JSON Patch (#737): ``add`` / ``remove`` / ``replace``.
    ``path`` is a dotted key path from the manifest root.
    ``before`` / ``after`` hold the serialised JSON values (null when not
    applicable — e.g. ``after`` is null for ``remove``)."""

    op: str
    path: str
    before: JSON
    after: JSON


@strawberry.type(name="AstroliftDeploymentComparison")
class DeploymentComparisonType:
    """Result of ``compareDeployments(idA, idB)`` (#737).

    All three sections are best-effort: the commit range and compare URL
    require both deployments to have ``commit_sha`` set and the app to
    have a GitHub App install.  The manifest diff requires both to have
    ``rendered_manifest_snapshot`` (null on pre-#737 deploys — empty diff
    returned).  The image diff is best-effort registry introspection."""

    deployment_a_id: GUID
    deployment_b_id: GUID
    base_sha: str
    head_sha: str
    compare_url: str
    manifest_diff: list[ManifestDiffEntryType]
    image_diff_summary: str


@strawberry.type(name="AstroliftEnvironmentSetting")
class EnvironmentSettingType:
    id: GUID
    key: str
    value: str


def env_setting_to_type(s) -> EnvironmentSettingType:
    return EnvironmentSettingType(id=GUID(str(s.guid)), key=s.key, value=s.value)


@strawberry.type(name="AstroliftAppEnvironment")
class AppEnvironmentType:
    id: GUID
    name: str
    url: str
    deploys_paused: bool
    ingress_paused: bool
    required_approvals: int
    registered_app_slug: str
    cluster_slug: str | None
    cluster_id: GUID | None
    cluster_provider_plugin_slug: str | None
    domain_zone: str | None
    created_at: dt.datetime
    settings: list[EnvironmentSettingType] = strawberry.field(default_factory=list)
    # The Environments list's columns (#2155). ``kind`` is derived from the
    # name (see ``environment_kind``); ``region`` is the bound cluster's; the
    # owner is whoever created the environment, else the app's creator, and
    # null when neither was recorded.
    kind: str = strawberry.field(default="other", description="production, preview or other.")
    region: str = strawberry.field(default="", description="The bound cluster's region; empty when unset.")
    owner_user_id: str | None = strawberry.field(
        default=None, description="The owner's user pk: the environment's creator, else the app's."
    )
    owned_by_me: bool = False


@strawberry.type(name="AstroliftDeploymentApprover")
class DeploymentApproverType:
    """One approver entry rendered on the quorum widget (#420).

    Surfaced in two slots on ``AstroliftDeployment``:

    * ``approvedBy`` — users who already voted approve (``approvedAt``
      is set to the audit-event occurrence).
    * ``awaitingApprovers`` — eligible org/team members who haven't
      approved yet (``approvedAt`` is null).

    ``mailtoUrl`` is a pre-built ``mailto:`` link so the UI can render
    a one-click nudge without rebuilding the subject line per locale.
    Empty when the approver has no recorded email."""

    user_id: str
    display_name: str
    email: str
    approved_at: dt.datetime | None
    mailto_url: str


@strawberry.type(name="AstroliftDeployment")
class DeploymentType:
    id: GUID
    version: int = strawberry.field(
        default=0, description="Version of this deployment snapshot for rollback and redeploy preconditions."
    )
    registered_app_slug: str
    environment_name: str
    workload_slug: str | None
    trigger_kind: str
    strategy: str
    """Rollout strategy captured at deploy-creation time (#736).
    One of ``rolling | blue_green | canary | recreate | unknown``.
    Back-fill rows return ``unknown`` so the FE can render a neutral
    pill instead of erroring on a missing value."""
    status: str
    image_tag: str
    image_digest: str
    cluster_revision: str
    approvals_required: int
    approvals_received: int
    required_approver_count: int
    """Alias of ``approvals_required`` exposed under the quorum
    vocabulary (#420). Kept distinct from ``approvals_required`` so the
    quorum-widget query can switch to the new name without touching
    callers that still read the older one (badges on the queue page,
    per-app pending row, deployment metrics tile)."""

    approved_by: list[DeploymentApproverType]
    """Users who already voted approve on this deployment. Drawn from
    ``AuditEvent`` rows whose action is ``deployment.approve`` and
    whose target matches the deployment guid."""

    awaiting_approvers: list[DeploymentApproverType]
    """Eligible approvers who haven't voted yet. Resolution order:
    ``app.approver_users`` when non-empty, else members of
    ``app.approver_team`` when set, else holders of
    ``app.approve_deploy`` at the org scope. The triggerer is filtered
    out when self-approval is disabled (Constance flag
    ``ALLOW_SELF_APPROVE_DEPLOYS``)."""

    manifest_resync_status: str
    """Outcome of the deploy-time manifest resync (#1553). ``applied`` /
    ``in_sync`` mean this deploy rendered the repo's current manifest;
    ``diverged`` (an unpushed staged draft blocked the refresh) and
    ``fetch_failed`` mean it rendered the *stored* one, so a repo-side fix
    may not be in this rollout. Empty on deploys predating the field and on
    apps with no source repo."""

    manifest_resync_error: str
    """Why the resync did not apply, verbatim from the sync service — the
    branch and path it looked at, or the reason a staged draft blocked it.
    Empty when the resync succeeded."""

    build_error: str
    """Why the image build failed, with the tail of the build pod's own
    output (#1686). The Job condition alone reads the same for every
    cause, and ``abortedReason`` keeps a single line, so this is where an
    operator finds what the builder actually said. Empty unless a
    platform build failed on this deploy."""

    started_at: dt.datetime | None
    succeeded_at: dt.datetime | None
    failed_at: dt.datetime | None
    ended_at: dt.datetime | None
    duration_seconds: int | None
    created_at: dt.datetime
    # CI / VCS provenance (#166)
    ci_actor_kind: str
    commit_sha: str
    branch: str
    ci_run_url: str
    ci_provider: str
    # Approval decision context (#419)
    commit_message: str
    commit_author: str
    repo_url: str
    # GitHub provenance (#722) — surfaces on the deployments table
    # (#651). When the deploy was not triggered from a PR these come
    # back as 0/"" and the FE renders a dash placeholder.
    pr_number: int
    pr_url: str
    """Convenience: ``{source_url}/pull/{pr_number}`` when both are
    known, else "". Derived at serialization time using
    ``_build_pr_url`` — matches the preview-environment pattern so
    GitLab MR / Bitbucket links land here when those integrations
    grow a per-provider mapper."""
    commit_author_avatar_url: str
    """Public source-repo URL the operator can deep-link into for the
    full commit/diff. Resolved from ``RegisteredApp.source_url`` at
    serialization time so we don't denormalize it onto every row."""

    aborted_reason: str
    """Non-empty when an operator rejected/aborted this deploy. Set by
    the ``abort_deployment`` mutation (which now requires a non-empty
    ``reason`` at the boundary)."""

    triggered_by_user_id: str | None
    """Surface-only echo of the row's ``triggered_by_user`` so the FE
    can compute ``viewer == triggerer`` without a separate ``me`` join.
    Null for token / system / CI-bot triggers."""

    triggered_by_me: bool
    """Convenience: True when the current request's authenticated user
    is the row's ``triggered_by_user``. The approval CTA hides on this
    so a deployer can't approve their own deploy from the UI."""

    @strawberry.field
    def phases(self) -> list[DeploymentPhaseType]:
        from django.db.models import Max, Min

        from astrolift_lifecycle.models import DeploymentLog

        rows = DeploymentLog.objects.filter(deployment__guid=str(self.id)).exclude(phase="")
        observations = rows.values("phase", "event").annotate(
            at=Min("occurred_at"), latest=Max("occurred_at")
        )
        phases: dict[str, dict[str, dt.datetime]] = {}
        for row in observations:
            phases.setdefault(row["phase"], {})[row["event"]] = (
                row["at"] if row["event"] == "started" else row["latest"]
            )
        return [
            DeploymentPhaseType(
                name=name,
                started_at=events.get("started"),
                completed_at=events.get("completed"),
                failed_at=events.get("failed"),
                healthy_at=events.get("healthy"),
            )
            for name in ("build", "push", "apply", "rollout", "health", "failed")
            if (events := phases.get(name))
        ]

    status_reason: str = ""
    """One line saying why the row is where it is (#2123): what a failed
    deploy died of, or what a pending one is waiting on. Empty for a deploy
    that is running or has succeeded. The full text stays in
    ``abortedReason``, ``buildError`` and ``manifestResyncError``."""


@strawberry.type(name="AstroliftDeploymentPhase")
class DeploymentPhaseType:
    name: str
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    failed_at: dt.datetime | None
    healthy_at: dt.datetime | None


@strawberry.type(name="AstroliftDeploymentLogEntry")
class DeploymentLogEntryType:
    id: GUID
    deployment_id: str
    status: str
    message: str
    detail: JSON
    occurred_at: dt.datetime
    phase: str = ""
    event: str = ""


@strawberry.type(name="AstroliftDeploymentRunLogPage")
class DeploymentRunLogPageType:
    items: list[DeploymentLogEntryType]
    next_cursor: str | None
    has_more: bool
    page_size: int


@strawberry.type(name="AstroliftDeploymentRunLogDownload")
class DeploymentRunLogDownloadType:
    filename: str
    content: str
    content_type: str = "text/plain; charset=utf-8"


@strawberry.type(name="AstroliftDeploymentApprovalHistoryEntry")
class DeploymentApprovalHistoryEntryType:
    """One row of the approval-decision timeline for a deployment (#419).

    Sourced from ``AuditEvent`` rows whose ``action`` matches one of the
    deployment lifecycle actions (``deployment.start``,
    ``deployment.approve``, ``deployment.approve_by_token``,
    ``deployment.reject``, ``deployment.reject_by_token``,
    ``deployment.abort``). The history panel renders these
    chronologically so an approver can see who approved or rejected
    sibling deploys in the same env before deciding.
    """

    id: GUID
    action: str
    """Dotted audit action — ``deployment.approve`` / ``.reject`` / ``.abort``."""

    decision: str
    """``ALLOW`` | ``DENY`` | ``UNKNOWN`` — mirrors ``AuditEvent.decision``."""

    actor_kind: str
    """``user`` | ``token`` | ``system``."""

    actor_id: str
    """Stringified user id (or token id) — empty for ``system`` actors."""

    actor_display: str
    """Best-effort human label; UI falls back to ``actor_kind`` when empty."""

    occurred_at: dt.datetime
    reason: str
    """Free-form rejection/abort reason when present; empty otherwise."""


@strawberry.type(name="AstroliftPreviewAggregateResources")
class PreviewAggregateResourcesType:
    """Sum of CPU + memory requests across every container in every
    running pod in the preview's namespace (#431).

    ``cpu_cores`` is fractional cores (``0.5`` for half a vCPU).
    ``memory_bytes`` is raw bytes — the frontend formats it (Mi / Gi)
    so display rounding stays consistent across the platform.
    ``pod_count`` is the number of non-terminal pods that contributed
    to the sum; rendered as part of the tooltip so operators can spot
    a single pod that's blowing up the budget vs many small pods.

    Surfaced even when ``estimated_daily_cost_usd`` is null — the
    resource numbers still help operators spot heavy previews."""

    cpu_cores: float
    memory_bytes: float
    pod_count: int


@strawberry.type(name="AstroliftPreviewEnvironment")
class PreviewEnvironmentType:
    id: GUID
    registered_app_slug: str
    pr_number: int
    """PR number that opened this preview. ``0`` when the preview was
    created via ``createPreviewEnvironment`` (manual branch spin-up) —
    pair with ``is_manual`` to distinguish 'no PR' from a real PR #0.
    The FE renders 0 as ``—`` to mirror the manual-deploy treatment."""

    is_manual: bool
    """True when the preview was created via the
    ``createPreviewEnvironment`` mutation (#751) rather than auto-
    triggered from a PR webhook. The FE uses this to swap the PR-link
    cell for a branch chip and hide PR-specific actions (re-comment,
    re-validate webhook)."""

    branch: str
    commit_sha: str
    status: str
    hostname: str
    namespace: str
    last_deployed_at: dt.datetime | None
    torn_down_at: dt.datetime | None
    ttl_until: dt.datetime
    """Auto-teardown target (#431). Defaults to created_at + 7d; the
    UI surfaces a live countdown and offers 1/7/30-day extend buttons
    (capped at +30d from now)."""

    is_pinned: bool
    """Operator pin (#1399). Pinned previews are exempt from *both*
    GC rules — the TTL sweep skips them and max-active eviction never
    picks them as a candidate — so this outranks ``ttl_until``, which
    only bounds the TTL axis and only in capped increments. Set via
    ``setPreviewPinned`` / ``astro app previews pin``."""

    pinned_at: dt.datetime | None
    """When the pin currently in force was placed. Null when the
    preview isn't pinned — unpinning clears the whole audit trail so
    stale "pinned by X" copy can't outlive the pin it describes."""

    pinned_by_email: str | None
    """Who placed the pin. Null for a system pin, for an unpinned
    preview, or when the user row has since been deleted (the FK is
    ``SET_NULL``) — the UI renders "unknown" rather than a blank."""

    pin_reason: str
    """Why the preview is pinned, free text, empty when unpinned or
    when the operator gave no reason. A pin is open-ended, so the
    justification is the only thing standing between a parked demo
    and an abandoned cost leak."""

    source_url: str
    """Public source-repo URL the operator can deep-link into to
    cross-reference the PR. Resolved from ``RegisteredApp.source_url``
    at serialization time so the row stays denormalised in the DB."""

    pr_url: str
    """Convenience: ``{source_url}/pull/{pr_number}`` when source_url
    is set, empty otherwise. Lets the FE render the PR cell as a
    single link without re-implementing the join client-side. Only
    GitHub-style URLs (``…/pull/N``) are emitted; GitLab merge-request
    URLs would need a separate field — open question for the
    GitLab integration work."""

    aggregate_resources: PreviewAggregateResourcesType
    """CPU + memory + pod-count rollup pulled from the runtime cluster
    when reachable; zeros when the cluster is unwired or the listing
    failed (which the FE renders as "—" rather than "0")."""

    estimated_daily_cost_usd: float | None
    # What the driver said about its own number. Every caveat it attaches
    # used to be dropped before it reached anyone (#1509): the list-price
    # note on all three clouds, and on GCP the APPROXIMATE label on the
    # variants that sum every SKU in a service.
    estimated_cost_notes: list[str]
    # True when the figure is known to be wrong in a stated direction, so
    # a surface can render it differently rather than as a considered one.
    estimated_cost_approximate: bool
    """Daily $ estimate from the cluster driver's live cost API.
    ``None`` (not zero) when the driver doesn't implement the cost
    capability, doesn't recognise compute pricing, or the pricing API
    is unreachable — workspace rule forbids hard-coded fallbacks."""

    # Who opened it and why it failed (#2155). ``opened_by_login`` is the
    # pull request author's SCM login on a PR preview and the platform
    # username on a manual one; ``opened_by_user_id`` is set only when a
    # platform user created it. Empty on rows from before either was kept.
    opened_by_login: str = ""
    opened_by_user_id: str | None = None
    opened_by_me: bool = False
    failure_reason: str = strawberry.field(
        default="",
        description=(
            "Why a failed preview failed, in one line: the build's recorded reason, else its "
            "latest deployment's. Empty unless status is failed."
        ),
    )


@strawberry.type(name="AstroliftPreviewEnvironmentCounts")
class PreviewEnvironmentCountsType:
    """Preview totals per status over the same app and search as the page (#2155)."""

    total: int
    building: int
    running: int
    failed: int
    torn_down: int


#: Environment names read as production, compared case-insensitively.
PRODUCTION_ENVIRONMENT_NAMES = ("production", "prod")


def environment_kind(name: str) -> str:
    """``production``, ``preview`` or ``other``, from an environment's name.

    Previews are named ``preview-...`` by both creation paths
    (``PREVIEW_ENV_PREFIX``). There is no production flag on the model, so
    production is the name the platform bootstraps (``production``) and its
    short form. ``list_contract.environment_kind_expr`` is the same rule in SQL.
    """
    from astrolift_lifecycle.services.preview_lineage import PREVIEW_ENV_PREFIX

    name = name or ""
    if name.startswith(PREVIEW_ENV_PREFIX):
        return "preview"
    if name.lower() in PRODUCTION_ENVIRONMENT_NAMES:
        return "production"
    return "other"


def _env_url(env) -> str:
    """Compute the public URL for an AppEnvironment.

    Prefers the managed-domain-derived hostname when the environment has
    a ManagedDomain bound (set by backfill or at creation). Falls back to
    the stored url field for environments that pre-date the managed domain
    setup.
    """
    if env.managed_domain_id and env.managed_domain:
        app = env.registered_app
        subdomain = (app.subdomain or app.slug or "").strip()
        if subdomain:
            return f"https://{subdomain}.{env.managed_domain.zone}"
    return env.url or ""


def app_env_to_type(env, *, keys: list[str] | None = None) -> AppEnvironmentType:
    raw = list(env.settings.filter(deleted_at__isnull=True))
    if keys is not None:
        raw = [s for s in raw if s.key in keys]
    owner_id = getattr(env, "created_by_id", None) or getattr(env.registered_app, "created_by_id", None)
    return AppEnvironmentType(
        id=GUID(str(env.guid)),
        name=env.name,
        url=_env_url(env),
        deploys_paused=env.deploys_paused,
        ingress_paused=env.ingress_paused,
        required_approvals=env.required_approvals,
        registered_app_slug=env.registered_app.slug,
        cluster_slug=env.tenant_cluster.slug if env.tenant_cluster_id else None,
        # #858 — the SNI cert picker on the Domains page needs the bound
        # cluster's guid + provider to fetch clusterCertificates. Both
        # come off the already-loaded tenant_cluster FK; null when the
        # env isn't bound to a cluster yet.
        cluster_id=(GUID(str(env.tenant_cluster.guid)) if env.tenant_cluster_id else None),
        cluster_provider_plugin_slug=(
            env.tenant_cluster.provider_plugin.slug if env.tenant_cluster_id else None
        ),
        domain_zone=env.managed_domain.zone if env.managed_domain_id else None,
        created_at=env.created_at,
        settings=[env_setting_to_type(s) for s in raw],
        kind=environment_kind(env.name),
        region=(env.tenant_cluster.region or "") if env.tenant_cluster_id else "",
        owner_user_id=str(owner_id) if owner_id else None,
        owned_by_me=_viewer_started(owner_id),
    )


def deployment_to_type(d, *, viewer_user_id: int | None = None) -> DeploymentType:
    """Serialize a ``Deployment`` row into the GraphQL type.

    ``viewer_user_id`` is the authenticated request's user id, used to
    compute ``triggered_by_me`` for the self-approval guard (#419).
    Callers can omit it (defaults to None) — the field then surfaces as
    False so unauthenticated / system call sites don't accidentally
    claim ownership of a row.

    Quorum surface (#420):
    ``approved_by`` + ``awaiting_approvers`` resolve from the audit
    log + the app's approver policy. Both lists default to empty when
    the row isn't gated (``approvals_required == 0``) — that's the
    cheap path and keeps list queries (``astrolift_deployments``) from
    fanning out into per-row identity joins.
    """
    triggered_by_user_id = d.triggered_by_user_id
    triggered_by_me = bool(viewer_user_id is not None and triggered_by_user_id == viewer_user_id)
    repo_url = ""
    if d.registered_app_id:
        repo_url = (
            getattr(d.registered_app, "source_url", "") or getattr(d.registered_app, "source_repo", "") or ""
        )

    approved_by, awaiting = _resolve_quorum_lists(d)
    pr_number = int(getattr(d, "pr_number", 0) or 0)
    pr_url = _build_pr_url(repo_url, pr_number) if (repo_url and pr_number) else ""
    return DeploymentType(
        id=GUID(str(d.guid)),
        version=int(d.version or 0),
        registered_app_slug=d.registered_app.slug,
        environment_name=d.app_environment.name,
        workload_slug=d.workload.slug if d.workload_id else None,
        trigger_kind=d.trigger_kind,
        strategy=getattr(d, "strategy", "") or "unknown",
        status=d.status,
        image_tag=d.image_tag or "",
        image_digest=d.image_digest or "",
        cluster_revision=d.cluster_revision or "",
        approvals_required=d.approvals_required,
        approvals_received=d.approvals_received,
        required_approver_count=d.approvals_required,
        approved_by=approved_by,
        awaiting_approvers=awaiting,
        manifest_resync_status=getattr(d, "manifest_resync_status", "") or "",
        manifest_resync_error=getattr(d, "manifest_resync_error", "") or "",
        build_error=getattr(d, "build_error", "") or "",
        started_at=d.started_at,
        succeeded_at=d.succeeded_at,
        failed_at=d.failed_at,
        ended_at=d.ended_at,
        duration_seconds=d.duration_seconds,
        created_at=d.created_at,
        ci_actor_kind=d.ci_actor_kind or "",
        commit_sha=d.commit_sha or "",
        branch=d.branch or "",
        ci_run_url=d.ci_run_url or "",
        ci_provider=d.ci_provider or "",
        commit_message=getattr(d, "commit_message", "") or "",
        commit_author=getattr(d, "commit_author", "") or "",
        pr_number=pr_number,
        pr_url=pr_url,
        commit_author_avatar_url=getattr(d, "commit_author_avatar_url", "") or "",
        repo_url=repo_url,
        aborted_reason=getattr(d, "aborted_reason", "") or "",
        triggered_by_user_id=(str(triggered_by_user_id) if triggered_by_user_id is not None else None),
        triggered_by_me=triggered_by_me,
        status_reason=deployment_status_reason(d),
    )


# A pending deploy that has not started this long after it was created has
# probably lost its worker, not merely queued.
_STUCK_AFTER_SECONDS = 10 * 60


def _first_line(text: str, limit: int = 240) -> str:
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return line if len(line) <= limit else line[: limit - 1] + "…"


def deployment_status_reason(d) -> str:
    """Why a deploy is failed or still pending, in one line (#2123)."""
    from django.utils import timezone

    from astrolift_lifecycle.models import Deployment

    status = d.status
    if status == Deployment.Status.FAILED:
        for text in (d.aborted_reason, d.build_error, d.manifest_resync_error):
            if text:
                return _first_line(text)
        return "Failed with no recorded reason. Check the deployment log."
    if status == Deployment.Status.PENDING_APPROVAL:
        return f"Waiting for approval: {d.approvals_received} of {d.approvals_required}."
    if status != Deployment.Status.PENDING:
        return ""
    ahead = (
        Deployment.objects.filter(
            registered_app_id=d.registered_app_id,
            app_environment_id=d.app_environment_id,
            status__in=(Deployment.Status.DEPLOYING, Deployment.Status.REDEPLOYING),
            created_at__lt=d.created_at,
            deleted_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if ahead is not None:
        label = ahead.image_tag or str(ahead.guid)[:8]
        return f"Queued behind deploy {label}, still {ahead.status}."
    waited = (timezone.now() - d.created_at).total_seconds()
    if waited > _STUCK_AFTER_SECONDS:
        return (
            f"Not started after {int(waited // 60)} minutes and nothing ahead of it. The deploy "
            "workflow may not be running: check the worker, then redeploy."
        )
    return "Waiting for the deploy workflow to start."


# ---------------------------------------------------------------------------
# Quorum resolution (#420)
# ---------------------------------------------------------------------------


def _resolve_quorum_lists(
    deployment,
) -> tuple[list[DeploymentApproverType], list[DeploymentApproverType]]:
    """Return ``(approved_by, awaiting_approvers)`` for a deployment.

    Both lists are empty when the deployment isn't gated
    (``approvals_required == 0``) or the deployment has already moved
    past the pending state (terminal/in-flight rows show their final
    audit trail via the approval-history panel instead).

    Eligibility chain (matches ``_is_eligible_approver`` in mutations):
    explicit ``approver_users`` > ``approver_team`` members > org-scope
    holders of the ``app.approve_deploy`` permission. Triggerer is
    filtered out when self-approval is disabled.
    """
    if deployment is None or deployment.approvals_required <= 0:
        return [], []

    from astrolift_operations.models import AuditEvent

    app = deployment.registered_app
    deployment_guid = str(deployment.guid)

    eligible_user_ids = _eligible_approver_user_ids(app)

    triggerer_id = deployment.triggered_by_user_id
    if triggerer_id is not None and not _self_approve_allowed_safe():
        eligible_user_ids = [uid for uid in eligible_user_ids if uid != triggerer_id]
    eligible = set(eligible_user_ids)

    # An approve attempt leaves an audit row whether or not it counted, so
    # only an ALLOW row from an eligible approver lists its actor (#1955).
    # The org filter keeps out rows filed under another org; the decision
    # drops refusals raised as PermissionDenied; eligibility drops the rest,
    # since a refusal returned as an envelope is recorded as ALLOW (#1968).
    approved_events = list(
        AuditEvent.objects.filter(
            action__in=("deployment.approve", "deployment.approve_by_token"),
            target_id=deployment_guid,
            actor_kind="user",
            organization_id=app.organization_id,
            decision=AuditEvent.Decision.ALLOW,
        )
        .exclude(actor_id="")
        .order_by("occurred_at")
    )
    approved_user_ids: list[int] = []
    approved_at_by_user: dict[int, dt.datetime] = {}
    for e in approved_events:
        try:
            uid = int(e.actor_id)
        except (TypeError, ValueError):
            continue
        if uid in approved_at_by_user or uid not in eligible:
            continue
        approved_user_ids.append(uid)
        approved_at_by_user[uid] = e.occurred_at

    awaiting_user_ids = [uid for uid in eligible_user_ids if uid not in approved_at_by_user]

    users_by_id = _hydrate_users(set(approved_user_ids) | set(awaiting_user_ids))

    approved_by: list[DeploymentApproverType] = []
    for uid in approved_user_ids:
        user = users_by_id.get(uid)
        if user is None:
            continue
        approved_by.append(_approver_entry(user, approved_at=approved_at_by_user[uid], app_slug=app.slug))

    awaiting: list[DeploymentApproverType] = []
    for uid in awaiting_user_ids:
        user = users_by_id.get(uid)
        if user is None:
            continue
        awaiting.append(_approver_entry(user, approved_at=None, app_slug=app.slug))

    return approved_by, awaiting


def _eligible_approver_user_ids(app) -> list[int]:
    """Resolve the eligible approver user-id set for an app.

    Mirrors ``_is_eligible_approver`` in
    ``astrolift_lifecycle.schema.mutations``: explicit ``approver_users``
    when non-empty, then ``approver_team`` members, then org-scope
    RoleBinding holders of ``app.approve_deploy``. Returns a
    deduplicated, deterministically-ordered list.
    """
    from astrolift_identity.models import Member

    if not app.requires_approval and not app.approver_users.exists() and app.approver_team_id is None:
        # No explicit approver policy. Fall back to org-scope holders of
        # the permission so the awaiting set is non-empty for env-level
        # required_approvals deploys.
        return _org_approver_user_ids(app)

    explicit_user_ids = list(app.approver_users.filter(is_active=True).values_list("pk", flat=True))
    team_user_ids: list[int] = []
    if app.approver_team_id is not None:
        team_user_ids = list(
            Member.objects.filter(
                scope_kind=Member.ScopeKind.TEAM,
                scope_id=app.approver_team_id,
                is_active=True,
                deleted_at__isnull=True,
            ).values_list("user_id", flat=True)
        )

    combined = explicit_user_ids + team_user_ids
    if combined:
        return _dedup_preserve_order(combined)

    # ``requires_approval`` is True but no explicit set was configured
    # — surface the org-scope permission holders so the widget isn't
    # blank.
    return _org_approver_user_ids(app)


def _org_approver_user_ids(app) -> list[int]:
    """Resolve org-scope users who hold ``app.approve_deploy``.

    Uses ``RoleBinding`` directly so we don't fan out into the request-
    time permission resolver per user. Group bindings (SCIM) are
    intentionally skipped — they don't expand to individual users at
    rest, and the widget would render an opaque "Group: …" entry
    which doesn't help approver nudges.
    """
    from astrolift_identity.models import Role, RoleBinding

    org_id = app.organization_id
    if org_id is None:
        return []

    role_ids = list(
        Role.objects.filter(
            permissions__contains=["app.approve_deploy"],
        ).values_list("pk", flat=True)
    )
    if not role_ids:
        return []
    user_ids = list(
        RoleBinding.objects.filter(
            user__isnull=False,
            role_id__in=role_ids,
            scope_kind=RoleBinding.ScopeKind.ORG,
            scope_id=org_id,
            deleted_at__isnull=True,
        ).values_list("user_id", flat=True)
    )
    return _dedup_preserve_order(user_ids)


def _hydrate_users(user_ids: set[int]) -> dict[int, object]:
    """One bulk fetch instead of N per-row lookups in the quorum
    widget. Returns the resolved users keyed by pk."""
    if not user_ids:
        return {}
    from django.contrib.auth import get_user_model

    User = get_user_model()
    rows = User.objects.filter(pk__in=user_ids)
    return {u.pk: u for u in rows}


def _approver_entry(user, *, approved_at: dt.datetime | None, app_slug: str) -> DeploymentApproverType:
    display = (
        (getattr(user, "first_name", "") + " " + getattr(user, "last_name", "")).strip()
        or getattr(user, "username", "")
        or getattr(user, "email", "")
    )
    email = (getattr(user, "email", "") or "").strip()
    mailto_url = ""
    if email:
        # Subject is intentionally English — the approval-request email
        # is operator-facing and the deployment row already carries the
        # app slug. The FE locale handles button labels; this URL is the
        # raw mailto: scheme.
        mailto_url = "mailto:" + email + "?subject=Approval%20requested%20for%20" + _urlquote(app_slug)
    return DeploymentApproverType(
        user_id=str(user.pk),
        display_name=display or email or "approver",
        email=email,
        approved_at=approved_at,
        mailto_url=mailto_url,
    )


def _urlquote(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


def _dedup_preserve_order(ids: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for uid in ids:
        if uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def _self_approve_allowed_safe() -> bool:
    """Mirror of ``_self_approve_allowed`` in mutations.py — duplicated
    here so the serializer doesn't import the mutations module (the
    resolver path is the import root for this module)."""
    try:
        from constance import config as constance_config

        return bool(getattr(constance_config, "ALLOW_SELF_APPROVE_DEPLOYS", False))
    except Exception:
        return False


def deployment_log_to_type(entry) -> DeploymentLogEntryType:
    return DeploymentLogEntryType(
        id=GUID(str(entry.guid)),
        deployment_id=str(entry.deployment_id),
        status=entry.status,
        message=entry.message or "",
        detail=entry.detail or {},
        occurred_at=entry.occurred_at,
        phase=entry.phase,
        event=entry.event,
    )


@strawberry.type(name="AstroliftDeploymentMetrics")
class DeploymentMetricsType:
    window_days: int
    total: int
    succeeded: int
    failed: int
    rolled_back: int
    in_flight: int
    success_rate: float  # 0.0–1.0; -1 when total==0
    mean_duration_seconds: float | None
    p95_duration_seconds: float | None
    # Per-day series over the window, oldest → newest, length == window_days.
    # Rollout counts zero-fill empty days; the duration mean is null on a day
    # with no rollouts (0.0 would read as an instant deploy). Buckets share the
    # aggregates' UTC cutoff, so sum(daily_succeeded) == succeeded, etc.
    daily_succeeded: list[int]
    daily_failed: list[int]
    daily_mean_duration_seconds: list[float | None]


@strawberry.type(name="AstroliftAppHealthSummary")
class AppHealthSummaryType:
    app_slug: str
    app_name: str
    primitive_kind: str
    environment_count: int
    latest_deployment_status: str | None
    latest_image_tag: str
    last_deployed_at: dt.datetime | None
    has_recent_failure: bool


# Max lines surfaced on the ``output`` field of a run row (#427).
# The full tail still lives behind the per-app logs surface; this is
# the "did it work" inline view the operator scans without leaving
# the jobs table.
_RUN_OUTPUT_LINES = 200


def _last_n_lines(text: str, n: int = _RUN_OUTPUT_LINES) -> str:
    """Return the last ``n`` lines of ``text``.

    ``log_excerpt`` is the raw capture from the cluster — possibly
    thousands of lines for chatty jobs. Truncating server-side caps
    the wire payload + keeps the row-expand snappy. The UI footer
    tells the operator when content was trimmed."""
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "\n".join(lines[-n:])


@strawberry.type(name="AstroliftScheduledJobRun")
class ScheduledJobRunType:
    id: GUID
    registered_app_slug: str
    environment_name: str
    workload_slug: str
    k8s_job_name: str
    status: str
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    duration_seconds: int | None
    exit_code: int | None
    log_excerpt: str
    output: str
    """Last 200 lines of ``log_excerpt`` (#427). Powers the inline
    row-expand surface on the jobs table so operators can confirm a
    run worked without leaving the page. The full tail lives behind
    the per-app logs surface; the UI footer flags truncation."""
    created_at: dt.datetime
    # Who and what started the run (#2152): the job's own trigger word
    # (``scheduled`` or ``manual``) and the initiator's user pk, null for a
    # scheduled fire.
    trigger_kind: str = "scheduled"
    triggered_by_user_id: str | None = None
    triggered_by_me: bool = False


@strawberry.type(name="AstroliftCommandRun")
class CommandRunType:
    id: GUID
    registered_app_slug: str
    workload_slug: str | None
    invoked_by_username: str | None
    command: JSON
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    exit_code: int | None
    log_excerpt: str
    output: str
    """Last 200 lines of ``log_excerpt`` (#427). Mirrors the
    ``ScheduledJobRun.output`` surface so the FE's shared row-expand
    component works against both run kinds."""
    created_at: dt.datetime
    # Who ran it (#2155), as the job run and deployment types spell it.
    invoked_by_user_id: str | None = None
    invoked_by_me: bool = False


def scheduled_job_run_to_type(r) -> ScheduledJobRunType:
    log_excerpt = r.log_excerpt or ""
    return ScheduledJobRunType(
        id=GUID(str(r.guid)),
        registered_app_slug=r.workload.registered_app.slug,
        environment_name=r.app_environment.name,
        workload_slug=r.workload.slug,
        k8s_job_name=r.k8s_job_name or "",
        status=r.status,
        started_at=r.started_at,
        ended_at=r.ended_at,
        duration_seconds=r.duration_seconds,
        exit_code=r.exit_code,
        log_excerpt=log_excerpt,
        output=_last_n_lines(log_excerpt),
        created_at=r.created_at,
        trigger_kind=r.trigger_kind,
        triggered_by_user_id=str(r.triggered_by_id) if r.triggered_by_id else None,
        triggered_by_me=_viewer_started(r.triggered_by_id),
    )


def _viewer_started(user_id: int | None) -> bool:
    """Whether ``user_id`` is the caller, for a run's ``triggeredByMe``."""
    from core.tenancy import get_current_tenant

    tenant = get_current_tenant()
    return user_id is not None and tenant is not None and tenant.actor_user_id == user_id


def command_run_to_type(r) -> CommandRunType:
    log_excerpt = r.log_excerpt or ""
    return CommandRunType(
        id=GUID(str(r.guid)),
        registered_app_slug=r.registered_app.slug,
        workload_slug=r.workload.slug if r.workload_id else None,
        invoked_by_username=r.invoked_by.username if r.invoked_by_id else None,
        command=r.command or [],
        started_at=r.started_at,
        ended_at=r.ended_at,
        exit_code=r.exit_code,
        log_excerpt=log_excerpt,
        output=_last_n_lines(log_excerpt),
        created_at=r.created_at,
        invoked_by_user_id=str(r.invoked_by_id) if r.invoked_by_id else None,
        invoked_by_me=_viewer_started(r.invoked_by_id),
    )


@strawberry.type(name="AstroliftTaskRun")
class TaskRunType:
    """One operator-initiated execution of a ``kind: task`` workload (#801)."""

    id: GUID
    registered_app_slug: str
    workload_slug: str
    trigger_kind: str
    triggered_by_username: str | None
    command: JSON
    status: str
    exit_code: int | None
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    duration_seconds: int | None
    k8s_job_name: str
    created_at: dt.datetime


@strawberry.type(name="AstroliftTaskRunPayload")
class TaskRunPayloadType:
    """Mutation payload — the created TaskRun row (#801)."""

    id: GUID
    registered_app_slug: str
    workload_slug: str
    status: str
    created_at: dt.datetime


def task_run_to_type(r) -> TaskRunType:
    return TaskRunType(
        id=GUID(str(r.guid)),
        registered_app_slug=r.workload.registered_app.slug,
        workload_slug=r.workload.slug,
        trigger_kind=r.trigger_kind,
        triggered_by_username=(r.triggered_by_user.username if r.triggered_by_user_id else None),
        command=r.command or [],
        status=r.status,
        exit_code=r.exit_code,
        started_at=r.started_at,
        ended_at=r.ended_at,
        duration_seconds=r.duration_seconds,
        k8s_job_name=r.k8s_job_name or "",
        created_at=r.created_at,
    )


def task_run_to_payload(r) -> TaskRunPayloadType:
    return TaskRunPayloadType(
        id=GUID(str(r.guid)),
        registered_app_slug=r.workload.registered_app.slug,
        workload_slug=r.workload.slug,
        status=r.status,
        created_at=r.created_at,
    )


@strawberry.type(name="AstroliftAgentRun")
class AgentRunType:
    """One dispatch execution of a ``kind: agent`` workload (#798)."""

    id: GUID
    registered_app_slug: str
    workload_slug: str
    trigger_kind: str
    triggered_by_username: str | None
    status: str
    input: JSON | None
    output: JSON | None
    reasoning_trace_url: str
    tool_calls_count: int
    retry_count: int
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    duration_seconds: int | None
    k8s_pod_name: str
    result_ttl_hours: int
    created_at: dt.datetime


def agent_run_to_type(r) -> AgentRunType:
    return AgentRunType(
        id=GUID(str(r.guid)),
        registered_app_slug=r.workload.registered_app.slug,
        workload_slug=r.workload.slug,
        trigger_kind=r.trigger_kind,
        triggered_by_username=(r.triggered_by_user.username if r.triggered_by_user_id else None),
        status=r.status,
        input=r.input,
        output=r.output,
        reasoning_trace_url=r.reasoning_trace_url or "",
        tool_calls_count=r.tool_calls_count,
        retry_count=r.retry_count,
        started_at=r.started_at,
        ended_at=r.ended_at,
        duration_seconds=r.duration_seconds,
        k8s_pod_name=r.k8s_pod_name or "",
        result_ttl_hours=r.result_ttl_hours,
        created_at=r.created_at,
    )


def _pinned_by_email(p) -> str | None:
    """Resolve the display email for whoever pinned the preview (#1399).

    Reads the FK id first so an unpinned row (the common case) never
    triggers the join. Returns None when no actor is captured — a
    system pin, an unpinned preview, or a user row that has since been
    deleted (``on_delete=SET_NULL`` keeps the pin but drops the actor).
    Mirrors ``astrolift_registry.schema.types._paused_by_email``.
    """
    user_id = getattr(p, "pinned_by_id", None)
    if user_id is None:
        return None
    user = p.pinned_by
    if user is None:
        return None
    email = getattr(user, "email", "") or getattr(user, "username", "")
    return email or None


def preview_to_type(
    p,
    *,
    aggregate_resources: PreviewAggregateResourcesType | None = None,
    estimated_daily_cost_usd: float | None = None,
    estimated_cost_notes: list[str] | None = None,
    estimated_cost_approximate: bool = False,
    failure_reason: str | None = None,
) -> PreviewEnvironmentType:
    """Serialize a ``PreviewEnvironment`` row into the GraphQL type.

    ``failure_reason`` overrides the row's own recorded reason; the list
    resolver passes the latest deployment's reason for failed rows that
    recorded none.

    ``aggregate_resources`` + ``estimated_daily_cost_usd`` are injected
    by the resolver (rather than computed here) so the cluster + cost
    API call surface stays at the resolver boundary — keeping
    serialization pure means tests + admin / shell call sites don't
    drag in the runtime-cluster lookup.

    Defaults: zero resources + null cost. Mirrors the "cluster unwired"
    UX — the FE renders empty/dash rather than fabricating numbers.
    """
    source_url = (
        getattr(p.registered_app, "source_url", "") or getattr(p.registered_app, "source_repo", "") or ""
    )
    pr_url = ""
    if source_url and p.pr_number:
        pr_url = _build_pr_url(source_url, p.pr_number)
    if aggregate_resources is None:
        aggregate_resources = PreviewAggregateResourcesType(
            cpu_cores=0.0,
            memory_bytes=0.0,
            pod_count=0,
        )
    opener_id = getattr(p, "created_by_id", None)
    # Manual previews (#751) have ``pr_number=NULL``; the GraphQL type
    # surfaces a non-nullable ``int`` (no contract change), so coerce
    # to 0 here. The FE renders 0 as "—" (the same fallback used for
    # manual deploys without PR provenance).
    return PreviewEnvironmentType(
        id=GUID(str(p.guid)),
        registered_app_slug=p.registered_app.slug,
        pr_number=p.pr_number or 0,
        is_manual=bool(getattr(p, "is_manual", False)),
        branch=p.branch,
        commit_sha=p.commit_sha or "",
        status=p.status,
        hostname=p.hostname,
        namespace=p.namespace,
        last_deployed_at=p.last_deployed_at,
        torn_down_at=p.torn_down_at,
        ttl_until=p.ttl_until,
        is_pinned=bool(getattr(p, "is_pinned", False)),
        pinned_at=getattr(p, "pinned_at", None),
        pinned_by_email=_pinned_by_email(p),
        pin_reason=getattr(p, "pin_reason", "") or "",
        source_url=source_url,
        pr_url=pr_url,
        aggregate_resources=aggregate_resources,
        estimated_daily_cost_usd=estimated_daily_cost_usd,
        estimated_cost_notes=list(estimated_cost_notes or []),
        estimated_cost_approximate=estimated_cost_approximate,
        opened_by_login=getattr(p, "opened_by_login", "") or "",
        opened_by_user_id=str(opener_id) if opener_id else None,
        opened_by_me=_viewer_started(opener_id),
        failure_reason=(
            _first_line(failure_reason if failure_reason is not None else getattr(p, "failure_reason", ""))
            if p.status == "failed"
            else ""
        ),
    )


def _build_pr_url(source_url: str, pr_number: int) -> str:
    """Construct ``{source_url}/pull/{n}`` for GitHub-style repos.

    The platform's source-provider connection guarantees
    ``source_url`` is a GitHub HTTPS repo URL today; GitLab MR /
    Bitbucket equivalents will need a per-provider mapper when those
    integrations land. Until then, a non-GitHub ``source_url`` still
    produces a sensible-looking link (``/pull/N`` 404s on GitLab but
    that's a better outcome than no link at all)."""
    base = source_url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    return f"{base}/pull/{pr_number}"


@strawberry.type(name="AstroliftAppDomainRequiredRecord")
class AppDomainRequiredRecordType:
    """One DNS record the operator must add (or that the platform
    will create on their behalf when the parent zone is managed).

    ``propagated`` flips True once the validation workflow sees the
    record at the authoritative nameserver. The UI renders a per-row
    status badge.
    """

    kind: str
    name: str
    value: str
    ttl: int
    propagated: bool
    last_checked_at: str | None
    message: str


@strawberry.type(name="AstroliftDomainPathRoute")
class DomainPathRouteType:
    """One path-prefix routing rule on an ``AstroliftAppDomain`` (#740).

    Backs the path routing sub-section under Domains (#686). The full set
    is replaced atomically via ``setDomainPathRoutes``."""

    id: GUID
    path_prefix: str
    target_workload_slug: str
    target_port: int
    strip_prefix: bool
    priority: int


def domain_path_route_to_type(r) -> DomainPathRouteType:
    return DomainPathRouteType(
        id=GUID(str(r.guid)),
        path_prefix=r.path_prefix,
        target_workload_slug=r.target_workload_slug,
        target_port=int(r.target_port),
        strip_prefix=bool(r.strip_prefix),
        priority=int(r.priority),
    )


@strawberry.type(name="AstroliftDomainRedirectRule")
class DomainRedirectRuleType:
    """One redirect rule on an ``AstroliftAppDomain`` (#742).

    Backs the Redirects sub-section under Domains (#685). The FE renders
    rows ordered by ``priority`` (low → high; first match wins) and the
    full set is replaced atomically via ``setDomainRedirects`` — there
    are no per-row mutations.

    ``kind`` is one of ``http_to_https | apex_to_www | www_to_apex |
    alias | custom`` (mirrors ``DomainRedirectRule.Kind``).
    ``source_pattern`` is empty for the well-known kinds (the renderer
    derives the source from the parent domain's hostname) and a regex /
    path-prefix string for ``custom``.  ``destination_url`` is the full
    target URL the cluster's ingress redirects to. ``http_status`` is
    one of 301/302/307/308.  ``preserve_query_string`` makes the
    renderer append the inbound query to the redirect target."""

    id: GUID
    kind: str
    source_pattern: str
    destination_url: str
    http_status: int
    preserve_query_string: bool
    priority: int


def domain_redirect_rule_to_type(r) -> DomainRedirectRuleType:
    return DomainRedirectRuleType(
        id=GUID(str(r.guid)),
        kind=r.kind,
        source_pattern=r.source_pattern or "",
        destination_url=r.destination_url or "",
        http_status=int(r.http_status),
        preserve_query_string=bool(r.preserve_query_string),
        priority=int(r.priority),
    )


@strawberry.type(name="AstroliftAppDomain")
class AppDomainType:
    """A custom domain bound to a registered app. ``cert_state``
    reflects the current ACME / cloud-cert validation state."""

    id: GUID
    hostname: str
    cert_state: str
    """pending | validating | validated | failed (mirrors
    ``CustomDomain.ValidationStatus``)."""

    validation_method: str
    validation_token: str
    last_checked_at: dt.datetime | None
    is_active: bool
    registered_app_slug: str
    created_at: dt.datetime

    # ---- handshake surface (#397) ---------------------------------

    txt_challenge_token: str
    expected_cname_target: str
    required_dns_records: list[AppDomainRequiredRecordType]
    is_platform_managed_zone: bool
    last_validation_error: str

    # ---- cert lifecycle (#397 unhappy-path surface) ----------------
    # cert_state above is the DNS validation status. The fields below
    # are the certificate's own state — separate axis so the UI can
    # render "DNS validated but cert issuance failed" distinctly.

    certificate_state: str
    """not_requested | issuing | active | failed | byo. Mirrors
    ``CustomDomain.CertificateState``. Surfaces post-validation cert
    progression so the UI can render a spinner while ACME / cloud-cert
    is provisioning and an actionable error + BYO upload affordance
    when issuance fails."""

    last_certificate_error: str
    byo_certificate_uploaded_at: dt.datetime | None

    # ---- cert observability metadata (#731) -----------------------
    # Cached snapshot of the TLS driver's ``CertificateInfo``.  All
    # fields are nullable / empty by default because a fresh domain
    # has no certificate yet — the FE hides the chip until the first
    # refresh lands.

    cert_expires_at: dt.datetime | None
    """ACM / Let's Encrypt ``not_after`` carried from the TLS
    driver.  Null when the cert hasn't been issued or the driver
    declined to surface a value.  FE renders 'expires in N days'
    off this field; warning when < 14 days."""

    cert_issuer_serial: str
    """Stable identifier for the current cert across renewals (ACM
    ARN, LE serial).  Empty string until the first refresh.  Lets
    the FE distinguish a renewal landing (serial changes) from the
    same cert lingering past its issued date."""

    cert_observability_status: str
    """Driver-reported renewal status — ``auto`` / ``manual`` /
    ``failed`` / ``unknown``.  Empty string until first refresh; FE
    renders the chip neutral in that case.  Distinct from
    ``certificate_state`` above: that's the platform's own state
    machine; this is what the cloud cert lifecycle says about the
    cert today."""

    redirect_rules: list[DomainRedirectRuleType] = strawberry.field(default_factory=list)
    """Active redirect rules on the domain (#742), ordered by
    ``priority`` ascending.  Empty when the operator hasn't configured
    any rules.  The full set is replaced atomically via
    ``setDomainRedirects`` — there are no per-row mutations."""

    path_routes: list[DomainPathRouteType] = strawberry.field(default_factory=list)
    """Active path-prefix routing rules on the domain (#740), ordered by
    ``priority`` ascending.  Empty when no path routing is configured.
    The full set is replaced atomically via ``setDomainPathRoutes``."""

    # ---- wildcard + SNI (#753) ------------------------------------

    is_wildcard: bool = False
    """True when this domain covers ``*.hostname`` (wildcard TLS).
    Created via ``addWildcardDomain``; the issued certificate
    carries both the apex and the ``*.<hostname>`` SAN. Wildcard
    domains always validate via DNS-01."""

    sni_cert_ref: str = ""
    """Provider-specific certificate identifier the renderer pins
    for SNI on this hostname (ACM ARN, GCP managed-cert resource
    name, Azure Key Vault cert URI). Empty when the platform
    auto-picks. Operator-set for multi-cert SNI scenarios."""

    # ---- edge auth (#1621) ----------------------------------------

    edge_auth_state: str = "no_gate"
    """Whether this domain sits behind the cluster's edge auth gate.

    ``no_gate`` — the cluster has no edge auth at all, so nothing is
    being bypassed. ``gated`` — the domain is covered by the gate.
    ``ungated`` — the cluster HAS a gate and the app's managed
    subdomain is behind it, but this hostname is not: the same backend
    is reachable here without a login.

    ``ungated`` is not a misconfiguration the operator can fix by
    toggling something. The central auth host's session cookie is
    scoped to its own parent zone and cannot be set for an unrelated
    domain, so an external custom domain is outside the gate by
    construction. Surfaced rather than silently accepted (#1621) —
    before this, the only way to learn it was to open the URL.
    """


def app_domain_to_type(d, cluster=None) -> AppDomainType:
    """Convert a ``CustomDomain`` row to its GraphQL type.

    ``cluster`` is optional and only feeds ``edge_auth_state`` (#1621).
    Callers that already hold the app's cluster pass it; the domain row
    carries no FK to one, and resolving it here would be a query per
    domain. Omitted, the field reports ``no_gate`` — the same answer as
    a cluster with no gate, and the conservative one for a caller that
    could not establish otherwise.
    """
    from core.app_deploy import custom_domain_edge_auth_state

    return AppDomainType(
        edge_auth_state=custom_domain_edge_auth_state(
            cluster,
            d.hostname,
            opted_in=bool(getattr(d, "edge_auth_enabled", False)),
        ),
        id=GUID(str(d.guid)),
        hostname=d.hostname,
        cert_state=d.validation_status,
        validation_method=d.validation_method,
        validation_token=d.validation_value or "",
        last_checked_at=d.last_checked_at or d.updated_at,
        is_active=d.is_active,
        registered_app_slug=d.registered_app.slug,
        created_at=d.created_at,
        txt_challenge_token=d.txt_challenge_token or "",
        expected_cname_target=d.expected_cname_target or "",
        required_dns_records=[
            AppDomainRequiredRecordType(
                kind=r.get("kind", ""),
                name=r.get("name", ""),
                value=r.get("value", ""),
                ttl=int(r.get("ttl", 300)),
                propagated=bool(r.get("propagated", False)),
                last_checked_at=r.get("last_checked_at"),
                message=r.get("message", ""),
            )
            for r in (d.required_dns_records or [])
        ],
        is_platform_managed_zone=bool(d.is_platform_managed_zone),
        last_validation_error=d.last_validation_error or "",
        certificate_state=d.certificate_state or "not_requested",
        last_certificate_error=d.last_certificate_error or "",
        byo_certificate_uploaded_at=d.byo_certificate_uploaded_at,
        cert_expires_at=d.cert_expires_at,
        cert_issuer_serial=d.cert_issuer_serial or "",
        cert_observability_status=d.cert_observability_status or "",
        redirect_rules=[
            domain_redirect_rule_to_type(r)
            for r in d.redirect_rules.filter(deleted_at__isnull=True).order_by("priority")
        ],
        path_routes=[
            domain_path_route_to_type(r)
            for r in d.path_routes.filter(deleted_at__isnull=True).order_by("priority")
        ],
        is_wildcard=bool(getattr(d, "is_wildcard", False)),
        sni_cert_ref=getattr(d, "sni_cert_ref", "") or "",
    )


@strawberry.type(name="AstroliftDeployTokenRotationMetadata")
class DeployTokenRotationMetadataType:
    """Validated current rotation window; contains no credential or raw config."""

    rotation_grace_seconds: int


@strawberry.type(name="AstroliftDeployToken")
class DeployTokenType:
    """Bearer credential bound to one app, scoped narrowly. The
    plaintext token is only returned on creation/rotation — at any
    other time, only ``last_4`` is exposed.

    ``last_used_ip`` + ``last_used_agent`` are stamped by the deploy-
    token middleware on every successful ``alft_dt_`` bearer auth
    (#425), giving operators a forensic anchor when investigating
    a leaked token: which CI runner / IP last exercised it.
    """

    id: GUID
    name: str
    last_4: str
    scopes: list[str]
    expires_at: dt.datetime | None
    last_used_at: dt.datetime | None
    last_used_ip: str
    last_used_agent: str
    is_revoked: bool
    last_rotated_at: dt.datetime | None
    registered_app_slug: str
    created_at: dt.datetime


def deploy_token_to_type(t) -> DeployTokenType:
    return DeployTokenType(
        id=GUID(str(t.guid)),
        name=t.name,
        last_4=t.token_last_4 or "",
        scopes=list(t.scopes or []),
        expires_at=t.expires_at,
        last_used_at=t.last_used_at,
        last_used_ip=t.last_used_ip or "",
        last_used_agent=t.last_used_agent or "",
        is_revoked=t.is_revoked,
        last_rotated_at=t.last_rotated_at,
        registered_app_slug=t.registered_app.slug,
        created_at=t.created_at,
    )


# ---- Pod state (runtime cluster) ------------------------------------


@strawberry.type(name="AstroliftContainerResources")
class ContainerResourcesType:
    """Per-container CPU + memory requests / limits as reported by
    the pod spec. Each field is a raw kubernetes resource-quantity
    string (``"100m"``, ``"512Mi"``) — empty string means the
    manifest left that knob unset.

    Surfaced on every container slot so the workload-detail page
    (#429) can show istio-proxy / linkerd-proxy sidecars' resource
    cost separately from the primary container's budget."""

    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str


@strawberry.type(name="AstroliftContainerStatus")
class ContainerStatusType:
    """One container slot's runtime state.

    ``state`` is ``running`` | ``waiting`` | ``terminated`` |
    ``unknown``. ``waiting_reason`` / ``terminated_reason`` carry
    the K8s reason string — that's where actionable diagnostics
    live (``CrashLoopBackOff``, ``ImagePullBackOff``, …).

    ``kind`` is ``init`` | ``primary`` | ``sidecar`` so the UI can
    bucket containers without re-deriving the classification on the
    client (#429).

    ``last_restart_reasons`` carries up to three reason strings for
    recent restarts (most-recent first). The kubernetes API only
    reports ``lastState`` (one history slot per container) so the
    list is best-effort — surfaces the latest ``OOMKilled`` /
    ``Error`` / probe-failure for incident response.

    ``last_restart_at`` is the timestamp of the most recent restart;
    used by the UI to flag flapping pods (count > 5 in the past
    hour)."""

    name: str
    ready: bool
    restarts: int
    image: str
    state: str
    waiting_reason: str
    terminated_reason: str
    kind: str
    last_restart_reasons: list[str]
    last_restart_at: dt.datetime | None
    resources: ContainerResourcesType


@strawberry.type(name="AstroliftAppPodEvent")
class AppPodEventType:
    """Most-recent Kubernetes Warning event surfaced for a pod (#666).

    Lifted from ``ClusterEvent`` so the FE has a structured shape to
    render rather than a free-form message blob.  ``reason`` is the
    short K8s event reason (``ImagePullBackOff``, ``CrashLoopBackOff``,
    ``OOMKilled``, ``FailedScheduling``) — the FE switches on that
    to colour the badge.  ``message`` is the long-form text the
    operator reads to triage."""

    reason: str
    message: str
    type: str
    """``Warning`` / ``Normal`` — events surfaced on this field are
    Warning by default but the type is carried so the FE can choose
    to render Normal events differently if the cluster's noise floor
    is high."""

    count: int
    last_seen: str
    """RFC 3339 timestamp from the K8s API.  String rather than
    datetime so we don't re-parse on a flaky timestamp."""


@strawberry.type(name="AstroliftAppPod")
class AppPodType:
    """A single pod from the runtime cluster, namespace-scoped to
    the app.

    ``status`` is the rolled-up surface status — the worst of the
    raw ``phase`` and any container waiting/terminated reasons.
    ``phase`` is preserved separately so the UI can show both when
    they diverge (e.g. phase=Running but a sidecar is in
    CrashLoopBackOff)."""

    name: str
    workload: str
    status: str
    phase: str
    ready: bool
    restarts: int
    age: dt.datetime | None
    node: str
    container_statuses: list[ContainerStatusType]

    recent_error_event: AppPodEventType | None = None
    """Most-recent Warning event for this pod (#666).  Null when no
    Warning events exist OR the cluster driver couldn't list events
    (best-effort; failure degrades the chip but never the row).  FE
    renders an inline error chip when populated."""


def container_status_to_type(c) -> ContainerStatusType:
    """Project an SDK ``ContainerStatusInfo`` onto the GraphQL type.

    ``resources`` and ``kind`` were added in #429; the SDK provides
    sane defaults (empty strings, ``primary``) so older test fixtures
    that build ``ContainerStatusInfo`` positionally keep working.
    ``last_restart_reasons`` defaults to ``[]`` for the same reason.
    """
    resources = getattr(c, "resources", None)
    return ContainerStatusType(
        name=c.name,
        ready=c.ready,
        restarts=c.restart_count,
        image=c.image,
        state=c.state,
        waiting_reason=c.waiting_reason,
        terminated_reason=c.terminated_reason,
        kind=getattr(c, "kind", "primary") or "primary",
        last_restart_reasons=list(getattr(c, "last_restart_reasons", []) or []),
        last_restart_at=getattr(c, "last_restart_at", None),
        resources=ContainerResourcesType(
            cpu_request=getattr(resources, "cpu_request", "") if resources else "",
            cpu_limit=getattr(resources, "cpu_limit", "") if resources else "",
            memory_request=getattr(resources, "memory_request", "") if resources else "",
            memory_limit=getattr(resources, "memory_limit", "") if resources else "",
        ),
    )


def pod_info_to_type(p, *, recent_error_event: AppPodEventType | None = None) -> AppPodType:
    return AppPodType(
        name=p.name,
        workload=p.workload,
        status=p.status,
        phase=p.phase,
        ready=p.ready,
        restarts=p.restarts,
        age=p.age,
        node=p.node,
        container_statuses=[container_status_to_type(c) for c in p.container_statuses],
        recent_error_event=recent_error_event,
    )


# ---- #429 — Workload pod status breakdown -----------------------------


@strawberry.type(name="AstroliftWorkloadPodSummary")
class WorkloadPodSummaryType:
    """A pod stub used by the status-grid expander.

    Only the fields the UI needs to render a click-to-logs row —
    ``name`` for the link target, ``age`` for the relative-time
    label, ``ready`` to dim the row when the pod isn't serving."""

    name: str
    age: dt.datetime | None
    ready: bool


@strawberry.type(name="AstroliftWorkloadPodStatusBucket")
class WorkloadPodStatusBucketType:
    """One row of the pod status grid on the workload detail page
    (#429).

    ``status`` is the same rolled-up surface label the ``AppPod``
    type uses (``Running`` / ``Pending`` / ``CrashLoopBackOff`` /
    ``ImagePullBackOff`` / ``Terminating`` / ``Unknown`` / …).
    ``percent`` is 0-100 rounded to one decimal place — the resolver
    pre-computes so every client renders the same number."""

    status: str
    count: int
    percent: float
    pods: list[WorkloadPodSummaryType]


@strawberry.type(name="AstroliftAppLogLine")
class AppLogLineType:
    """One log line from a pod/container in the runtime cluster.

    ``stream`` is ``stdout`` | ``stderr``. The default
    kubernetes-client API doesn't separate the two on the wire — all
    container output arrives interleaved — so the streaming backend
    flags everything as ``stdout`` unless an alternative backend
    (test fakes, future per-container stderr support) emits
    otherwise."""

    pod_name: str
    container: str
    timestamp: dt.datetime
    message: str
    stream: str


# ---------------------------------------------------------------------------
# #377 — observability cards (DNS / TLS / Workload identity)
# ---------------------------------------------------------------------------
#
# These types mirror the SDK dataclasses
# (``_sdk.dns.DnsRecord``, ``_sdk.tls.CertificateInfo``,
# ``_sdk.identity.IdentityBinding``) — see ``astrolift-providers/_sdk/``.
# The strawberry layer auto-converts snake_case → camelCase so the FE
# sees ``propagationStatus`` etc. ``not_after`` is a string (ISO-8601)
# rather than a datetime so the schema doesn't depend on the cloud
# returning timezone-aware values.


@strawberry.type(name="AstroliftAppDnsRecord")
class AppDnsRecordType:
    name: str
    type: str
    value: str
    ttl: int
    propagation_status: str


@strawberry.type(name="AstroliftAppCertificate")
class AppCertificateType:
    id: str
    hostname: str
    issuer: str
    not_after: str
    days_until_expiry: int
    renewal_status: str


@strawberry.type(name="AstroliftAppIdentityBinding")
class AppIdentityBindingType:
    kind: str
    role_arn_or_principal: str
    trust_policy_summary: str
    last_used_at: str | None


def dns_record_to_type(r) -> AppDnsRecordType:
    return AppDnsRecordType(
        name=r.name,
        type=r.type,
        value=r.value,
        ttl=r.ttl,
        propagation_status=r.propagation_status,
    )


def certificate_info_to_type(c) -> AppCertificateType:
    return AppCertificateType(
        id=c.id,
        hostname=c.hostname,
        issuer=c.issuer,
        not_after=c.not_after,
        days_until_expiry=c.days_until_expiry,
        renewal_status=c.renewal_status,
    )


def identity_binding_to_type(b) -> AppIdentityBindingType:
    return AppIdentityBindingType(
        kind=b.kind,
        role_arn_or_principal=b.role_arn_or_principal,
        trust_policy_summary=b.trust_policy_summary,
        last_used_at=b.last_used_at,
    )


# ---- #1111 — reason-discriminated envelopes for the DNS / TLS /
# workload-identity cards. Each card used to return a bare list / null,
# collapsing "not configured", "provider doesn't implement it", "no
# data yet", and "error" into one indistinguishable empty state. The
# envelope carries ``reason`` so the FE renders one honest message.


@strawberry.type(name="AstroliftAppDnsRecordsResult")
class AppDnsRecordsResult:
    reason: ObservabilityPanelReason
    records: list[AppDnsRecordType]


@strawberry.type(name="AstroliftAppCertificatesResult")
class AppCertificatesResult:
    reason: ObservabilityPanelReason
    certificates: list[AppCertificateType]


@strawberry.type(name="AstroliftAppIdentityBindingResult")
class AppIdentityBindingResult:
    reason: ObservabilityPanelReason
    binding: AppIdentityBindingType | None


# ---------------------------------------------------------------------------
# #436 — destructive-flow preview types (blast-radius + force-redeploy
# in-flight preview). Each surfaces a structured read of what an
# irreversible operation is about to destroy so the operator can audit
# before confirming the click.
# ---------------------------------------------------------------------------


@strawberry.type(name="AstroliftDeregisterPreviewK8sObject")
class DeregisterPreviewK8sObjectType:
    """One Kubernetes object the deregister will cascade-delete.

    The cluster slug + namespace pin the object to the (env, cluster)
    pair so the FE can group by destination. ``kind`` mirrors the
    apiVersion / kind tuple in the renderer; ``name`` is the canonical
    per-workload name (``<app-slug>-<workload-slug>``) plus the bare
    slug fallback the force-redeploy delete path also targets."""

    cluster_slug: str
    namespace: str
    api_version: str
    kind: str
    name: str


@strawberry.type(name="AstroliftDeregisterPreviewManagedService")
class DeregisterPreviewManagedServiceType:
    id: GUID
    name: str
    kind: str
    variant: str
    environment_name: str
    status: str


@strawberry.type(name="AstroliftDeregisterPreviewSecretRef")
class DeregisterPreviewSecretRefType:
    id: GUID
    bundle_slug: str
    environment_name: str
    cluster_slug: str | None
    prefix: str


@strawberry.type(name="AstroliftDeregisterPreviewDeployToken")
class DeregisterPreviewDeployTokenType:
    id: GUID
    name: str
    last4: str
    environment_name: str | None


@strawberry.type(name="AstroliftDeregisterPreviewSourceWebhook")
class DeregisterPreviewSourceWebhookType:
    """Push-event webhook the source-host integration installed for the
    app. ``installed`` is False when the app has no recorded hook id
    (GitHub-App install delivery or never-installed) — the deregister
    path still clears the bookkeeping but no host DELETE fires."""

    installed: bool
    repo: str
    hook_id: str


@strawberry.type(name="AstroliftDeregisterPreviewIdentityRole")
class DeregisterPreviewIdentityRoleType:
    """IRSA / WI / FI role bound to the app's ServiceAccount."""

    cluster_slug: str
    kind: str
    role_arn_or_principal: str


@strawberry.type(name="AstroliftDeregisterPreview")
class DeregisterPreviewType:
    """Blast-radius read for a deregister-app click (#436 A).

    Resolved on modal-open so the operator audits the actual object
    names — not generic resource-class labels — before typing the
    confirm token. Counts are denormalized so the FE can render the
    trigger-button badge without traversing the grouped lists."""

    app_slug: str
    app_name: str
    k8s_objects: list[DeregisterPreviewK8sObjectType]
    managed_services: list[DeregisterPreviewManagedServiceType]
    secret_refs: list[DeregisterPreviewSecretRefType]
    deploy_tokens: list[DeregisterPreviewDeployTokenType]
    source_webhook: DeregisterPreviewSourceWebhookType | None
    identity_roles: list[DeregisterPreviewIdentityRoleType]
    registry_repo_uri: str
    total_resource_count: int


@strawberry.type(name="AstroliftForceRedeployPreviewDeployment")
class ForceRedeployPreviewDeploymentType:
    """One in-flight ``Deployment`` row that a force-redeploy will
    transition to FAILED. Surfaces just enough provenance (who, when,
    what image) for the operator to weigh "let this finish" vs. "blow
    it away"."""

    id: GUID
    environment_name: str
    workload_slug: str | None
    status: str
    image_tag: str
    started_at: dt.datetime | None
    created_at: dt.datetime
    trigger_kind: str
    triggered_by_display: str
    ci_actor_kind: str
    ci_run_url: str


@strawberry.type(name="AstroliftForceRedeployPreview")
class ForceRedeployPreviewType:
    """Pre-confirm read for the force-redeploy CTA (#436 D).

    ``in_flight_deployments`` is the exact list the recovery path's
    ``_cancel_in_flight_deploys_sync`` will transition to FAILED — the
    operator sees what they're about to interrupt."""

    app_slug: str
    environment_name: str | None
    in_flight_deployments: list[ForceRedeployPreviewDeploymentType]
