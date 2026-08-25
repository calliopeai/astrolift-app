"""
Scheduled workflow registration policy (#129, spec 06 §5).

Pure-Python policy. The control-plane boot routine consults this
module to register the platform's scheduled workflows in Temporal.

* **Schedule catalog** — locked-down list of (workflow_name,
  interval) tuples. Adding a schedule requires a deliberate code
  review (boot-time fan-out has cost implications).
* **Schedule ID conventions** — deterministic so re-registration
  at boot is idempotent (Temporal de-dupes by ID).
* **Interval validation** — refuses < 1 minute (would be a hot
  loop) and > 24 hours (probably operator typo).

This module owns the boot-registration *contract*; the actual
Temporal client calls live in ``workers/schedules.py``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from enum import StrEnum


class ScheduleRegistryError(ValueError):
    pass


# Per spec 06 §5: minimum 60s, maximum 24h. Lower would be a hot
# loop; higher is almost always operator typo (use cron expr if
# you really mean 'once a week').
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 24 * 60 * 60


# ---- schedule definition -------------------------------------------


class ScheduleKind(StrEnum):
    """Spec 06 §5: locked vocabulary of recurring workflows."""

    PREVIEW_GC = "preview_gc"
    """Every 15 min — preview environment teardown by TTL +
    max-active eviction (#88)."""

    UPTIME_PROBE = "uptime_probe"
    """Every 2 min — synthetic HTTP probe of each READY app's public
    health URL; emit app.down / app.recovered on transitions. Catches
    edge/routing/TLS outages the pod-health signals can't see."""

    POLL_SCHEDULED_JOB_RUNS = "poll_scheduled_job_runs"
    """Every 1 min — discover k8s Jobs created by tenant
    CronJobs (spec §4.16)."""

    RECONCILE_CLUSTER_CAPABILITIES = "reconcile_cluster_capabilities"
    """Every 10 min — refresh TenantCluster.capabilities snapshot;
    emit CLUSTER_CAPABILITY_CHANGED on drift (spec §4.18)."""

    DRIFT_DETECTION = "drift_detection"
    """Every 1 hr — compare platform DB to live cluster state for
    each active deployment; emit DRIFT_DETECTED on material diff
    (spec §4.19)."""

    REHEAL_WEBHOOK_SUBSCRIPTIONS = "reheal_webhook_subscriptions"
    """Every 5 min — re-enable backed-off webhook subs."""

    PRUNE_AUDIT_LOG = "prune_audit_log"
    """Daily 02:00 UTC — delete audit log past retention
    (per ObservabilityProfile retention bounds, #9)."""

    PROBE_APP_DNS = "probe_app_dns"
    """Every 30 min — refresh the app doctor's cached hostname-resolution
    answers so the panel reads instantly (#1550)."""

    APPLY_OBSERVABILITY_RETENTION = "apply_observability_retention"
    """Daily — set each org's configured log retention on its CloudWatch log
    groups (#1602). A policy, not a delete: AWS ages the data out."""

    CAPTURE_PLATFORM_COST_SNAPSHOT = "capture_platform_cost_snapshot"
    """Daily — platform-wide cost snapshot for billing reports."""

    CAPTURE_QUOTA_USAGE_SNAPSHOT = "capture_quota_usage_snapshot"
    """Daily — append-only per-quota usage snapshot so the quota detail
    view can plot usage-vs-limit over time (#1182). Ships HELD (opt-in)
    like the cost snapshot: only meaningful once quota reconciliation
    populates ``current_usage``."""

    CRON_DEPLOY_TICK = "cron_deploy_tick"
    """Every 1 min — re-reads RegisteredApp rows with
    trigger_mode='cron' and fires a deploy for each whose
    cron_expression matches the current minute (#296)."""

    AGENT_CRON_TICK = "agent_cron_tick"
    """Every 1 min — re-reads agent Workload rows with
    run_family='task', run_mode='schedule' and dispatches an AgentTask
    (through the runAstroliftAgent dispatch path, NOT an app deploy)
    for each whose run_cron_expression matches the current minute and
    that is not run_paused (spec 33, PR-4). Kept distinct from
    CRON_DEPLOY_TICK so agent dispatches and app deploys have separate
    run history + a separate selector that can never double-fire one as
    the other."""

    SCALE_TICK = "scale_tick"
    """Every 1 min — re-reads agent Workload rows with
    run_family='service' that carry a scale-up and/or scale-down cron
    and patches the Service's Deployment replicas: to ``scheduled_scale_to``
    at a scale-up-cron match, to 0 at a scale-down-cron match
    (business-hours-up / off-hours-zero, spec 33 PR-5). Kept distinct from
    AGENT_CRON_TICK (which dispatches Tasks) and CRON_DEPLOY_TICK (which
    fires app deploys) so its selector is mutually exclusive from both —
    it only ever touches ``run_family='service'`` agent Workloads and
    issues a replica patch, never a dispatch or a deploy."""

    LOOP_TICK = "loop_tick"
    """Every 1 min — re-reads agent Workload rows with
    run_family='task', run_mode='loop' and tops the number of in-flight
    (non-terminal) AgentTasks per agent back up to the concurrency cap
    (``run_max_parallel``; null = a default cap of 1, never unbounded),
    dispatching fresh Tasks through the same runAstroliftAgent path as the
    cron tick (spec 33, PR-6). As runs finish, a later tick re-dispatches —
    the loop is the every-minute reconcile, not a long-running workflow.
    Kept distinct from AGENT_CRON_TICK (schedule mode), SCALE_TICK (service
    family), and CRON_DEPLOY_TICK (apps) so its selector is mutually
    exclusive from all three — it only ever touches ``run_family='task',
    run_mode='loop'`` agent Workloads. The activity caps each agent's
    dispatch under a per-agent row lock so the cap holds even under
    concurrent ticks."""

    SECRET_BUNDLE_REFRESH = "secret_bundle_refresh"
    """Every 1 hr — re-applies every actively-referenced
    ``SecretBundle`` from the SecretsBackend to its bound clusters
    so long-running pods don't drift behind external rotations
    (AWS Secrets Manager scheduled rotation, Vault TTL renewal).
    Doesn't bounce workloads — too disruptive on an hourly cadence;
    operator-fired ``rotateSecretBundle`` is the path that
    restarts consumers (#365)."""

    PRUNE_STALE_SESSIONS = "prune_stale_sessions"
    """Every 1 hr — soft-revokes ``AstroliftSession`` rows whose
    ``last_seen_at`` is older than the per-kind staleness threshold
    (``STALE_SESSION_TTL_SECONDS_<KIND>`` Constance entries). Hygiene
    for stale CLI tokens, abandoned phones, dead browser-extension
    installs. Hardened to never touch the currently-active set
    because ``last_seen_at`` is bumped by the session middleware on
    every authed request (#498)."""

    EXPIRE_PENDING_APPROVAL_DEPLOYMENTS = "expire_pending_approval_deployments"
    """Every 1 hr — auto-fail pending_approval Deployments whose
    magic-link expiry (``approval_token_expires_at``) has passed
    (#779, spec §06 §4.6). Prevents deployments from dangling
    in pending_approval forever after the approval window closes."""

    ALERT_EVAL = "alert_eval"
    """Every 60 s — evaluate active AlertRules and create/resolve AlertEvent
    rows on breach, emitting alert.fired / alert.resolved through the same
    notification dispatch path the uptime probe uses. Safe-by-default: a
    predicate with no recognized ``kind`` (the seeded PromQL defaults), an
    unresolvable driver, or an unconfigured metrics backend all resolve to
    no-fire, so a fresh install with only default rules produces zero events;
    fan-out no-ops without a NotificationProfile. Kept distinct from the
    uptime probe (which observes edge reachability and emits app.down /
    app.recovered): this tick reads AlertRule rows and emits alert.fired /
    alert.resolved — a rule-predicate signal, not an HTTP probe."""

    AGENT_RECONCILE_TICK = "agent_reconcile_tick"
    """Every 5 min — re-applies the keep-alive agent manifests
    (Namespace + Deployment) to every eligible managed cluster via
    the idempotent server-side-apply path the ``deployClusterAgent``
    mutation uses (``deploy_agent_dispatch``). Self-heals a stale or
    failed agent image (e.g. after ``AGENT_IMAGE`` changes, existing
    clusters keep the old image in ImagePullBackOff until re-applied)
    and any agent-manifest drift, with no manual re-apply (#808).
    Kept distinct from the dispatch ticks (AGENT_CRON_TICK / LOOP_TICK
    dispatch Tasks, SCALE_TICK patches Service replicas, CRON_DEPLOY_TICK
    fires app deploys): this tick neither dispatches nor deploys an app —
    it reconciles the platform's own in-cluster agent Deployment. The
    cadence is 5 min rather than 60 s because re-applying to every cluster
    each minute is needless load; 5 min is well inside the self-heal
    window for an image/manifest drift."""

    RUN_STATUS_RECONCILE = "run_status_reconcile"
    """Every 60 s — reconcile non-terminal ScheduledJobRun + TaskRun rows
    against their k8s Job status, writing back status / started / ended /
    duration / exit_code (+ log_excerpt for ScheduledJobRun). Closes the
    gap where a Job that finished in the cluster left its platform run row
    stuck RUNNING/PENDING forever. Safe-by-default: per-run try/except, an
    unreachable cluster or a torn-down/GC'd Job resolves to "leave as-is",
    and terminal rows are never touched again. Kept distinct from
    POLL_SCHEDULED_JOB_RUNS (which *discovers* Jobs created by tenant
    CronJobs): this tick *reconciles the status* of runs the platform
    already recorded, and reaches the cluster read-only to do it (sibling
    to UPTIME_PROBE among the cluster-touching sweeps)."""

    AGENT_BOX_REAP = "agent_box_reap"
    """Every 60 s — settle every live AgentBox against its k8s Job (#128).

    The control-plane half of a box's idle timeout. The pod's own keep-alive
    loop is what frees the node: it ends the tmux session when nobody is
    attached and no pane has produced output for the timeout, and the Job
    completes. This tick is what *notices*, stamps the row EXPIRED, and
    deletes the per-box Secret the pod no longer needs — a plaintext-bearing
    object must not outlive the pod it was created for. Without it a box that
    reaped itself keeps claiming RUNNING, which is the platform lying about
    what an operator can attach to. Safe-by-default like its RUN_STATUS_
    RECONCILE sibling: read-only cluster reads, per-box try/except, and an
    unreachable cluster resolves to "leave as-is" rather than to a death
    certificate."""

    CERT_EXPIRY = "cert_expiry"
    PRUNE_OBSERVABILITY_DATA = "prune_observability_data"
    """Evict observability data past each org's configured retention (#1602).

    Ships **HELD**, and this is the strongest HOLD case in the catalog: it
    is the only schedule whose whole purpose is to delete tenant data. Not
    idempotent in the way the active kinds are -- a tick that deletes a
    window cannot be undone by the next one.

    Distinct from APPLY_OBSERVABILITY_RETENTION, which sets a log group's
    retention window on the provider. That one configures the policy; this
    one enforces it.

    Activated per operator via ASTROLIFT_ACTIVE_SCHEDULES, after they have
    run the sweep in dry-run and read the window counts.
    """
    """Daily — refresh each live custom domain's cached TLS-cert snapshot
    from its provider and evaluate it against the 30 / 14 / 7-day reminder
    thresholds (spec 13 §5.3, #155), emitting domain.cert_expiring on a
    threshold crossing and domain.cert_renewal_failed while a cert inside
    the 7-day window keeps failing to renew. Kept distinct from ALERT_EVAL
    (AlertRule predicates) and UPTIME_PROBE (edge reachability): a cert
    that stops renewing is invisible to both until the day TLS breaks.
    Crossings are watermarked per domain (``cert_expiry_checked_at``) so
    each reminder pages exactly once, not daily."""

    CI_WORKFLOW_RESYNC = "ci_workflow_resync"
    """Every 1 hr (HELD — opt-in) — recompute drift for every app with a
    managed CI workflow file and auto-push ONLY the SAFE states
    (``template_stale`` / ``absent``), never clobbering an operator hand-edit
    (``repo_drift`` / ``conflict``, left recorded but untouched). The per-app
    reconcile reuses the Phase 1 push + Phase 2 fetch/drift machinery; the
    whole fleet is swept inside one activity (sequential, per-app try/except,
    one app's failure never aborts the sweep) like the sibling reconcile
    sweeps (#1211). Ships INERT: deliberately LEFT OUT of
    ``PHASE_3A_ACTIVE_KINDS`` because it does outbound writes to tenant repos
    — enabled later per-operator by adding it to ``ASTROLIFT_ACTIVE_SCHEDULES``
    (a config change, no code) or fired on demand by the platform-admin
    ``resyncAllAstroliftCiWorkflows`` mutation."""


@dataclasses.dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    """One row in the schedule catalog. Boot routine walks
    DEFAULT_SCHEDULES and calls Temporal to register each."""

    kind: ScheduleKind
    workflow_name: str
    """Temporal workflow name. Boot uses this to resolve the
    registered workflow class."""

    interval_seconds: int
    """How often to fire. Subject to MIN/MAX bounds."""

    schedule_id: str
    """Deterministic Temporal schedule ID. Re-registration with
    same ID is idempotent — Temporal updates rather than creates
    a duplicate."""

    description: str

    def __post_init__(self) -> None:
        if self.interval_seconds < MIN_INTERVAL_SECONDS:
            raise ScheduleRegistryError(
                f"schedule {self.kind.value} interval "
                f"{self.interval_seconds}s below minimum "
                f"{MIN_INTERVAL_SECONDS}s"
            )
        if self.interval_seconds > MAX_INTERVAL_SECONDS:
            raise ScheduleRegistryError(
                f"schedule {self.kind.value} interval "
                f"{self.interval_seconds}s exceeds maximum "
                f"{MAX_INTERVAL_SECONDS}s"
            )
        if not self.workflow_name:
            raise ScheduleRegistryError(f"schedule {self.kind.value} requires workflow_name")
        if not self.schedule_id:
            raise ScheduleRegistryError(f"schedule {self.kind.value} requires schedule_id")


def schedule_id_for(*, kind: ScheduleKind) -> str:
    """Deterministic schedule ID. ``astro-{kind}`` namespace
    so platform-owned schedules are easy to spot in the
    Temporal UI alongside any tenant-created ones."""
    return f"astro-{kind.value}"


# Spec 06 §5: the canonical schedule list. Locked.
DEFAULT_SCHEDULES: tuple[ScheduleDefinition, ...] = (
    ScheduleDefinition(
        kind=ScheduleKind.PREVIEW_GC,
        workflow_name="PreviewGarbageCollectWorkflow",
        interval_seconds=15 * 60,
        schedule_id=schedule_id_for(kind=ScheduleKind.PREVIEW_GC),
        description="Preview env GC: TTL eviction + max-active",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.UPTIME_PROBE,
        workflow_name="UptimeProbeTickWorkflow",
        interval_seconds=120,
        schedule_id=schedule_id_for(kind=ScheduleKind.UPTIME_PROBE),
        description="Synthetic uptime probe of deployed apps",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.POLL_SCHEDULED_JOB_RUNS,
        workflow_name="PollScheduledJobRunsWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.POLL_SCHEDULED_JOB_RUNS,
        ),
        description="Discover k8s Jobs from tenant CronJobs",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.RECONCILE_CLUSTER_CAPABILITIES,
        workflow_name="ReconcileClusterCapabilitiesWorkflow",
        interval_seconds=10 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.RECONCILE_CLUSTER_CAPABILITIES,
        ),
        description="Refresh cluster capability snapshot",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.DRIFT_DETECTION,
        workflow_name="DriftDetectionWorkflow",
        interval_seconds=60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.DRIFT_DETECTION,
        ),
        description="DB vs cluster state diff per deployment",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.REHEAL_WEBHOOK_SUBSCRIPTIONS,
        workflow_name="RehealWebhookSubscriptionsWorkflow",
        interval_seconds=5 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.REHEAL_WEBHOOK_SUBSCRIPTIONS,
        ),
        description="Re-enable backed-off webhook subs",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.PRUNE_AUDIT_LOG,
        workflow_name="PruneAuditLogWorkflow",
        interval_seconds=24 * 60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.PRUNE_AUDIT_LOG,
        ),
        description="Delete audit log past retention",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.PROBE_APP_DNS,
        workflow_name="ProbeAppDnsWorkflow",
        interval_seconds=30 * 60,
        schedule_id=schedule_id_for(kind=ScheduleKind.PROBE_APP_DNS),
        description="Refresh cached hostname resolution for the app doctor",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.APPLY_OBSERVABILITY_RETENTION,
        workflow_name="ApplyObservabilityRetentionWorkflow",
        interval_seconds=24 * 60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.APPLY_OBSERVABILITY_RETENTION,
        ),
        description="Apply configured log retention to CloudWatch log groups",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.CAPTURE_PLATFORM_COST_SNAPSHOT,
        workflow_name="CapturePlatformCostSnapshotWorkflow",
        interval_seconds=24 * 60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.CAPTURE_PLATFORM_COST_SNAPSHOT,
        ),
        description="Daily platform cost snapshot for billing",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.CAPTURE_QUOTA_USAGE_SNAPSHOT,
        workflow_name="CaptureQuotaUsageSnapshotWorkflow",
        interval_seconds=24 * 60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.CAPTURE_QUOTA_USAGE_SNAPSHOT,
        ),
        description="Daily per-quota usage snapshot for the quota detail view (#1182)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.CRON_DEPLOY_TICK,
        workflow_name="CronDeployTickWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.CRON_DEPLOY_TICK,
        ),
        description="Cron-triggered deploy dispatcher tick (#296)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.AGENT_CRON_TICK,
        workflow_name="AgentCronTickWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.AGENT_CRON_TICK,
        ),
        description="Cron-triggered agent Task dispatcher tick (spec 33 PR-4)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.SCALE_TICK,
        workflow_name="AgentScaleTickWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.SCALE_TICK,
        ),
        description="Scheduled-scaling tick: cron-driven Service replica patch X/0 (spec 33 PR-5)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.LOOP_TICK,
        workflow_name="AgentLoopTickWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.LOOP_TICK,
        ),
        description="Loop-dispatch tick: refill Task agent to concurrency cap (spec 33 PR-6)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.SECRET_BUNDLE_REFRESH,
        workflow_name="SecretBundleScheduledRefreshWorkflow",
        interval_seconds=60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.SECRET_BUNDLE_REFRESH,
        ),
        description="Re-apply active SecretBundles hourly (#365)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.PRUNE_STALE_SESSIONS,
        workflow_name="PruneStaleSessionsWorkflow",
        interval_seconds=60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.PRUNE_STALE_SESSIONS,
        ),
        description="Soft-revoke stale AstroliftSession rows (#498)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.EXPIRE_PENDING_APPROVAL_DEPLOYMENTS,
        workflow_name="ExpirePendingApprovalDeploymentsWorkflow",
        interval_seconds=60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.EXPIRE_PENDING_APPROVAL_DEPLOYMENTS,
        ),
        description="Auto-fail pending_approval deployments past magic-link expiry (#779)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.AGENT_RECONCILE_TICK,
        workflow_name="AgentReconcileTickWorkflow",
        interval_seconds=5 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.AGENT_RECONCILE_TICK,
        ),
        description="Re-apply keep-alive agent manifests to managed clusters (self-heal, #808)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.ALERT_EVAL,
        workflow_name="AlertEvalTickWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.ALERT_EVAL,
        ),
        description="Evaluate active AlertRules; fire/resolve AlertEvents",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.RUN_STATUS_RECONCILE,
        workflow_name="RunStatusReconcileTickWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.RUN_STATUS_RECONCILE,
        ),
        description="Reconcile ScheduledJobRun/TaskRun status from k8s Job status",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.AGENT_BOX_REAP,
        workflow_name="AgentBoxReapTickWorkflow",
        interval_seconds=60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.AGENT_BOX_REAP,
        ),
        description="Settle live AgentBox rows against their k8s Job (#128)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.CERT_EXPIRY,
        workflow_name="CertExpiryTickWorkflow",
        interval_seconds=24 * 60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.CERT_EXPIRY,
        ),
        description="Daily TLS cert expiry check: 30/14/7-day reminders + renewal-failure escalation (#155)",
    ),
    ScheduleDefinition(
        kind=ScheduleKind.PRUNE_OBSERVABILITY_DATA,
        workflow_name="ObservabilityRetentionTickWorkflow",
        interval_seconds=24 * 60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.PRUNE_OBSERVABILITY_DATA,
        ),
        description=(
            "Daily eviction of observability data past each org's configured "
            "retention. Ships HELD -- it deletes tenant data (#1602)"
        ),
    ),
    ScheduleDefinition(
        kind=ScheduleKind.CI_WORKFLOW_RESYNC,
        workflow_name="CiWorkflowResyncTickWorkflow",
        interval_seconds=60 * 60,
        schedule_id=schedule_id_for(
            kind=ScheduleKind.CI_WORKFLOW_RESYNC,
        ),
        description="Resync managed CI workflow files across the fleet (held; #1211)",
    ),
)


def get_schedule(*, kind: ScheduleKind) -> ScheduleDefinition:
    """Look up a single schedule by kind."""
    for s in DEFAULT_SCHEDULES:
        if s.kind == kind:
            return s
    raise ScheduleRegistryError(f"no schedule definition for {kind!r}")


# ---- registration plan ---------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class RegistrationDecision:
    """Per-schedule decision the boot loop emits — what action
    to take for each catalog entry given current Temporal state."""

    schedule_id: str
    action: str
    """One of: 'create' (no existing), 'update' (existing
    differs), 'skip' (existing matches, idempotent re-boot).
    Update vs skip avoids the 'noisy boot logs' problem of
    always claiming 'created N schedules' on every restart."""


def plan_registrations(
    *,
    catalog: Sequence[ScheduleDefinition],
    existing_ids: frozenset[str],
    existing_by_id: dict[str, ScheduleDefinition],
) -> tuple[RegistrationDecision, ...]:
    """For each catalog entry, decide what the boot loop should
    do. ``existing_by_id`` is a snapshot of what's already in
    Temporal (mapped to the same ScheduleDefinition shape via the
    activity layer); we compare on workflow_name + interval to
    detect drift.
    """
    out: list[RegistrationDecision] = []
    for entry in catalog:
        if entry.schedule_id not in existing_ids:
            out.append(
                RegistrationDecision(
                    schedule_id=entry.schedule_id,
                    action="create",
                )
            )
            continue
        existing = existing_by_id.get(entry.schedule_id)
        if existing is None or (
            existing.workflow_name != entry.workflow_name
            or existing.interval_seconds != entry.interval_seconds
        ):
            out.append(
                RegistrationDecision(
                    schedule_id=entry.schedule_id,
                    action="update",
                )
            )
            continue
        out.append(
            RegistrationDecision(
                schedule_id=entry.schedule_id,
                action="skip",
            )
        )
    return tuple(out)


# ---- orphan cleanup -----------------------------------------------


def orphaned_schedule_ids(
    *,
    catalog: Sequence[ScheduleDefinition],
    existing_ids: frozenset[str],
) -> tuple[str, ...]:
    """Return platform-owned schedules in Temporal that aren't
    in the current catalog. Boot loop deletes these so removing
    a schedule from the catalog actually stops it.

    Only considers IDs prefixed ``astro-`` so tenant-created
    schedules aren't touched.
    """
    catalog_ids = {s.schedule_id for s in catalog}
    return tuple(sid for sid in sorted(existing_ids) if sid.startswith("astro-") and sid not in catalog_ids)
