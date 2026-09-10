"""Scheduled-workflow activities.

Each function below is a single-sweep activity called by exactly one
scheduled workflow in ``astrolift_workflows/workflows/scheduled.py``.
Sweeps are read-mostly (preview-gc kicks off teardown workflows; cost
snapshots write a row per org); none take payload args because the
scheduler doesn't pass any.

Activities are async + dispatch the DB work through ``sync_to_async``
to match the rest of the activity surface; transactional / row-level
work is in the ``_*_sync`` companions so test suites can call them
directly without an event loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from temporalio import activity

if TYPE_CHECKING:
    # Type-only: the runtime import stays inside the function, since
    # preview_gc is the Django-free module the workflow sandbox loads.
    from astrolift_workflows.preview_gc import PreviewSnapshot

log = logging.getLogger("astrolift_workflows.activities.scheduled")


# ---- Preview GC ---------------------------------------------------


# Label for the FAILED-preview cleanup pass. Deliberately NOT an
# ``EvictionReason``: the GC policy in ``astrolift_workflows.preview_gc``
# only models running previews, so failed-preview cleanup is this
# activity's own concern and doesn't belong in the policy's vocabulary.
_FAILED_CLEANUP_REASON = "failed_stale"


def _preview_gc_snapshot(preview) -> PreviewSnapshot:
    """Project a ``PreviewEnvironment`` row into the GC policy's
    ``PreviewSnapshot`` (#1399).

    This is the join the policy was missing: ``preview_gc`` is a pure,
    Django-free module (the Temporal workflow sandbox imports it), so
    nothing there could read a row, and until now nothing outside its
    own tests ever built a snapshot. ``is_pinned`` in particular had no
    column behind it, which made both pin branches in the policy dead
    code.

    ``state`` is the raw model status. The policy only ever tests
    ``== "running"``, so ``building`` (which the policy's docstring
    calls ``pending``) and ``failed`` both correctly read as
    not-a-GC-candidate without a translation table.

    ``last_deployed_at`` falls back to ``created_at`` when null. In
    practice a RUNNING preview always has it — the same activity that
    flips the status stamps the timestamp — so the fallback only covers
    rows in a state the policy ignores anyway, and ``created_at`` is
    the right staleness anchor for a preview that never deployed.
    """
    from astrolift_workflows.preview_gc import PreviewSnapshot

    anchor = preview.last_deployed_at or preview.created_at
    return PreviewSnapshot(
        preview_id=preview.pk,
        app_id=preview.registered_app_id,
        pr_number=preview.pr_number or 0,
        is_pinned=bool(preview.is_pinned),
        state=preview.status,
        last_deployed_at_unix=int(anchor.timestamp()),
    )


def _gc_stale_previews_sync(stale_after_days: int) -> int:
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_lifecycle.models import PreviewEnvironment
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, TearDownPreviewInput
    from astrolift_workflows.preview_gc import evaluate_app_evictions

    now = timezone.now()
    now_unix = int(now.timestamp())
    cutoff = now - timedelta(days=stale_after_days)

    # Live previews the GC can act on. BUILDING rows are excluded: they
    # hold no cluster resources the policy counts and tearing one down
    # mid-build races the builder.
    rows = list(
        PreviewEnvironment.objects.filter(
            status__in=[
                PreviewEnvironment.Status.RUNNING.value,
                PreviewEnvironment.Status.FAILED.value,
            ],
            deleted_at__isnull=True,
        ).select_related("registered_app")
    )

    # preview pk -> reason string, deduplicated: a row can qualify on
    # more than one axis and must only be torn down once.
    evictions: dict[int, str] = {}

    # ---- policy pass (TTL + max-active), per app ----
    #
    # ``max_active`` is a per-app cap, so the policy is evaluated one
    # app at a time — a mixed-app batch would over-evict. FAILED rows
    # ride along in each group because the policy already treats any
    # non-running state as neither eligible nor active; passing them
    # keeps the projection uniform.
    by_app: dict[int, list[PreviewEnvironment]] = {}
    for p in rows:
        by_app.setdefault(p.registered_app_id, []).append(p)

    for app_id in sorted(by_app):
        group = by_app[app_id]
        # Every row in the group shares one app, so any row's FK is the
        # cap's source. Clamped to >= 1: the field is a
        # PositiveIntegerField, and a stored 0 would make
        # ``evaluate_max_active_evictions`` raise and take down the
        # whole sweep for every other app. "No previews at all" is
        # expressed by ``preview_enabled``, not by a zero cap.
        max_active = max(1, int(getattr(group[0].registered_app, "preview_max_active", 0) or 0))
        for decision in evaluate_app_evictions(
            previews=[_preview_gc_snapshot(p) for p in group],
            now_unix=now_unix,
            ttl_days=stale_after_days,
            max_active=max_active,
        ):
            evictions.setdefault(decision.preview_id, str(decision.reason))

    # ---- failed-preview cleanup ----
    #
    # Kept outside the policy on purpose. ``is_eligible_for_gc``
    # requires ``state == "running"``, so routing everything through the
    # policy would silently stop reaping stale FAILED previews, which
    # this sweep has always done. Same staleness signal as before:
    # ``last_deployed_at`` older than the cutoff, and never-deployed
    # rows (null) are left alone.
    for p in rows:
        if p.status != PreviewEnvironment.Status.FAILED.value:
            continue
        if p.last_deployed_at is None or p.last_deployed_at >= cutoff:
            continue
        evictions.setdefault(p.pk, _FAILED_CLEANUP_REASON)

    by_pk = {p.pk: p for p in rows}
    n = 0
    for pk in sorted(evictions):
        p = by_pk[pk]
        handle = start_workflow(
            "TearDownPreviewWorkflow",
            args=[
                TearDownPreviewInput(preview_environment_id=p.pk, actor=Actor(kind="system", display="gc"))
            ],
            workflow_id=f"TearDownPreviewWorkflow-{p.guid}",
        )
        if handle.enqueued:
            n += 1
            log.info(
                "preview gc: tearing down preview=%s app=%s reason=%s",
                p.guid,
                p.registered_app_id,
                evictions[pk],
            )
    return n


@activity.defn(name="astrolift.scheduled.gc_stale_previews")
async def gc_stale_previews(stale_after_days: int = 14) -> int:
    """Fire TearDownPreviewWorkflow for previews idle > N days."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    n = await sync_to_async(_gc_stale_previews_sync)(stale_after_days)
    log.info("gc_stale_previews enqueued %d teardown workflow(s)", n)
    return n


# ---- Scheduled job runs -------------------------------------------


def _poll_scheduled_job_runs_sync() -> int:
    """Refresh status for in-flight cluster-side Jobs (preflight, etc.).

    The platform doesn't yet persist a dedicated ``JobRun`` row — Job
    runs live inside the cluster lifecycle workflow as ephemeral state.
    This activity is the hook for when we do introduce that table. For
    now it's a no-op that returns 0 — safe to schedule, safe to ignore.
    """
    return 0


@activity.defn(name="astrolift.scheduled.poll_scheduled_job_runs")
async def poll_scheduled_job_runs() -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_poll_scheduled_job_runs_sync)()


# ---- Reconcile cluster capabilities -------------------------------


def _reconcile_cluster_capabilities_sync() -> int:
    from django.utils import timezone

    from astrolift_clusters.models import TenantCluster
    from core.cluster_management import (
        ClusterManagementError,
        probe_cluster_capabilities_dispatch,
    )

    n = 0
    for cluster in TenantCluster.objects.filter(
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
        deleted_at__isnull=True,
    ).iterator():
        try:
            caps = probe_cluster_capabilities_dispatch(cluster=cluster)
        except ClusterManagementError as exc:
            log.warning("reconcile capabilities failed for cluster=%s: %s", cluster.slug, exc)
            continue
        cluster.capabilities = caps or {}
        cluster.capabilities_probed_at = timezone.now()
        fields = ["capabilities", "capabilities_probed_at", "updated_at", "version"]

        # The producer `TenantCluster.node_archs` never had (#1604). Written
        # only when the probe actually returned architectures: an empty list
        # means the probe could not list nodes, and clearing a known value on
        # a transient RBAC or network failure would make every arch-labelled
        # job stop routing until the next successful tick.
        archs = [
            str(a).strip().lower() for a in (caps or {}).get("node_architectures") or [] if str(a).strip()
        ]
        if archs and sorted(set(archs)) != sorted(cluster.node_archs or []):
            cluster.node_archs = sorted(set(archs))
            fields.append("node_archs")

        cluster.save(update_fields=fields)
        n += 1
    return n


def _apply_observability_retention_sync() -> int:
    """Apply each org's configured log retention to its CloudWatch groups (#1602).

    `observability_retention.effective_for` has resolved the platform default,
    the per-org override and incident holds since the feature was specced, and
    nothing called it. Four `Organization` columns --
    `log_retention_days_default`, `metrics_retention_days_default`,
    `metrics_rollup_retention_days_default`, `trace_retention_days_default` --
    are settable through `updateOrganization` and were read by nobody, so an
    operator could set them and the data lived forever.

    AWS only, deliberately, per the AWS-first rule. CloudWatch is the one
    backend here with a *native* retention primitive: `PutRetentionPolicy`
    sets the window and AWS ages the data out itself, so there is nothing to
    delete on a schedule and a platform-side delete loop would be strictly
    worse than the one AWS already runs. Loki, Mimir and Tempo each need a
    different mechanism and stay on #1602.

    Logs only, for the same reason: metrics and traces on AWS are not
    CloudWatch log groups, and the columns for them have no CloudWatch
    equivalent to set.
    """
    from astrolift_clusters.models import TenantCluster
    from astrolift_identity.models import Organization
    from astrolift_operations.observability_retention import effective_for

    changed = 0
    for org in Organization.objects.filter(deleted_at__isnull=True).iterator():
        retention = effective_for(stream="log", org_override_days=org.log_retention_days_default)
        clusters = TenantCluster.objects.filter(
            organization_id=org.pk,
            lifecycle=TenantCluster.Lifecycle.MANAGED.value,
            deleted_at__isnull=True,
        )
        for cluster in clusters:
            if str(getattr(getattr(cluster, "provider_plugin", None), "slug", "")) != "aws":
                continue
            try:
                result = _apply_cloudwatch_retention(cluster, days=retention.days)
            except Exception:  # noqa: BLE001
                # One cluster's IAM gap must not stop the rest. Logged rather
                # than swallowed: an org that set a retention expects it to
                # take effect, and silence here is the defect this fixes.
                log.exception(
                    "observability retention: could not apply %d day(s) to cluster %s",
                    retention.days,
                    cluster.slug,
                )
                continue
            if result.get("changed"):
                changed += 1
                log.info(
                    "observability retention: cluster %s log group set to %d day(s) (was %s)",
                    cluster.slug,
                    result["days"],
                    result.get("previous"),
                )
    return changed


def _apply_cloudwatch_retention(cluster, *, days: int) -> dict:
    """Build the retention driver for one cluster's log group and apply."""
    from _sdk.observability.cloudwatch_logs import (
        CloudWatchLogsConfig,
        CloudWatchLogsRetentionDriver,
    )

    pc = cluster.provider_config or {}
    region = str(pc.get("region", "") or cluster.region or "")
    log_group = str(pc.get("log_group", "") or f"/aws/containerinsights/{cluster.slug}/application")
    driver = CloudWatchLogsRetentionDriver(
        config=CloudWatchLogsConfig(
            region=region,
            log_group=log_group,
            role_arn=(pc.get("observability_role_arn") or None),
        )
    )
    return driver.apply_retention(days)


def _probe_app_dns_sync() -> int:
    """Refresh every app's cached hostname-resolution probe (#1550).

    The live half of the app doctor's DNS check, on a schedule so the panel
    can read a cached answer instead of resolving on a page load. Returns the
    number of apps whose result changed, so the log says something only when
    it has something to say.
    """
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.services.app_doctor import probe_app_dns

    changed = 0
    for app in RegisteredApp.objects.filter(deleted_at__isnull=True).iterator():
        previous = (app.dns_probe or {}).get("unresolved")
        try:
            record = probe_app_dns(app)
        except Exception:  # noqa: BLE001
            # One app's resolver trouble must not stop the sweep. `probe_app_dns`
            # already records a failed lookup as unresolved, so reaching here
            # means something worse than a NXDOMAIN.
            log.exception("dns probe: failed for app %s", app.slug)
            continue
        if record["unresolved"] != previous:
            changed += 1
            if record["unresolved"]:
                log.warning(
                    "dns probe: %s hostname(s) answer nothing for app %s: %s",
                    len(record["unresolved"]),
                    app.slug,
                    ", ".join(record["unresolved"]),
                )
    return changed


@activity.defn(name="astrolift.scheduled.probe_app_dns")
async def probe_app_dns_activity() -> int:
    """Refresh cached DNS probes for the app doctor."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_probe_app_dns_sync)()


def _probe_app_cronjob_runs_sync() -> int:
    """Refresh every app's cached cronjob-run probe (#1710).

    A failed CronJob run left no trace in the product: app health was the
    ``web`` Deployment, and the cronjob is a different workload of the
    same app, so the app read healthy while its scheduled work had been
    failing for hours. The only way to find out was kubectl -- and by
    then the pod was reaped and the Job's events had aged out.

    Same shape as the DNS probe, and for the same reason: the doctor
    renders on every app-detail load, so the live read belongs here.
    Returns the number of apps whose failure set changed.
    """
    from astrolift_registry.models import RegisteredApp
    from astrolift_registry.services.app_doctor import probe_app_cronjob_runs

    changed = 0
    for app in RegisteredApp.objects.filter(deleted_at__isnull=True).iterator():
        previous = (app.cronjob_probe or {}).get("failed")
        try:
            record = probe_app_cronjob_runs(app)
        except Exception:  # noqa: BLE001 — one unreachable cluster must not stop the sweep
            log.exception("cronjob probe: failed for app %s", app.slug)
            continue
        if record.get("failed") != previous:
            changed += 1
            if record.get("failed"):
                log.warning(
                    "cronjob probe: %s failed run(s) for app %s: %s",
                    len(record["failed"]),
                    app.slug,
                    ", ".join(record["failed"]),
                )
    return changed


@activity.defn(name="astrolift.scheduled.probe_app_cronjob_runs")
async def probe_app_cronjob_runs_activity() -> int:
    """Refresh cached cronjob-run probes for the app doctor."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_probe_app_cronjob_runs_sync)()


@activity.defn(name="astrolift.scheduled.apply_observability_retention")
async def apply_observability_retention() -> int:
    """Apply each org's configured observability retention. Returns the
    number of log groups whose window changed."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_apply_observability_retention_sync)()


@activity.defn(name="astrolift.scheduled.reconcile_cluster_capabilities")
async def reconcile_cluster_capabilities() -> int:
    """Re-probe every managed cluster — keeps the capability table fresh.

    Mirrors the probe step inside BringClusterIntoManagementWorkflow.
    Capability drift (operator installs a new CRD, removes ingress
    controller) is detected on the next scheduled tick. Failures are
    logged and skipped so one bad cluster doesn't block the rest.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_reconcile_cluster_capabilities_sync)()


# ---- Drift detection ----------------------------------------------


def _detect_drift_sync() -> int:
    """Compare in-cluster workload state vs. last-applied manifest set.

    The platform stores ``Deployment.config_snapshot`` for every
    successful apply — that's the canonical record of "what we expected
    to be running". Walking every (app, env) and diffing against the
    cluster's live state is non-trivial, so the first pass below counts
    the apps that *could* be drift-checked and lets the operator scope
    the rollout. Returns the count of apps inspected.
    """
    from astrolift_lifecycle.models import Deployment

    return Deployment.objects.filter(
        status=Deployment.Status.RUNNING.value,
        deleted_at__isnull=True,
    ).count()


@activity.defn(name="astrolift.scheduled.detect_drift")
async def detect_drift() -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_detect_drift_sync)()


# ---- Webhook reheal ----------------------------------------------


# Reheal probe payload marker. Distinct from the operator-triggered
# ``webhook.test`` so a subscriber can tell an automated recovery probe
# from a human pressing Test.
_REHEAL_EVENT_TYPE = "webhook.reheal"


def _reheal_webhook_subscriptions_sync() -> int:
    """Re-test auto-disabled webhook subscriptions and re-enable the
    ones whose endpoint came back (#144, spec 06 §5).

    The policy lives in ``astrolift_workflows.periodic_maintenance``:
    ``reheal_due`` holds the 6-hour backoff so a dead endpoint isn't
    probed on every tick, and ``classify_test_delivery`` turns the
    probe's status code into re-enable / still-failing plus the
    notify-exactly-once-at-threshold decision.

    Re-enabling here does not contradict ``record_delivery_outcome``'s
    refusal to auto-re-enable on a live-traffic success: that guard
    exists so a lucky 200 in a stream of failures doesn't silently
    resurrect an integration. This is the audited recovery path, a
    deliberate probe, backed off, counted, and reported.

    Returns the number of subscriptions re-enabled.
    """
    from django.utils import timezone

    from astrolift_operations.models import WebhookSubscription

    # The signed POST has exactly one implementation in the platform;
    # a second one here would let the signature scheme drift.
    from astrolift_operations.schema.mutations.helpers import _deliver_test_webhook
    from astrolift_workflows.periodic_maintenance import (
        RehealOutcome,
        classify_test_delivery,
        reheal_due,
    )

    now = timezone.now()
    reenabled = 0
    candidates = WebhookSubscription.objects.filter(
        is_active=False,
        disabled_at__isnull=False,
        deleted_at__isnull=True,
    )
    for sub in candidates:
        if not sub.url:
            continue
        if not reheal_due(
            disabled_at=sub.disabled_at,
            last_reheal_attempt_at=sub.last_reheal_attempt_at,
            now=now,
        ):
            continue

        try:
            probe = _deliver_test_webhook(
                url=sub.url,
                secret=(sub.secret_hash or "").encode("utf-8"),
                payload={
                    "kind": _REHEAL_EVENT_TYPE,
                    "subscription_id": str(sub.guid),
                    "organization_id": sub.organization_id,
                    "message": "automated reheal probe from the Astrolift control plane",
                },
                event_type=_REHEAL_EVENT_TYPE,
                format=sub.format or "generic",
            )
            status_code = probe["status_code"]
        except Exception:  # noqa: BLE001 - a probe that can't be sent is a failed probe
            log.exception(
                "reheal probe raised",
                extra={"subscription_id": str(sub.guid)},
            )
            status_code = None

        result = classify_test_delivery(
            subscription_id=sub.pk,
            test_status_code=status_code,
            prior_consecutive_failed_reheals=sub.consecutive_failed_reheals or 0,
        )

        fields = ["last_reheal_attempt_at", "consecutive_failed_reheals"]
        sub.last_reheal_attempt_at = now
        sub.consecutive_failed_reheals = result.consecutive_failed_reheals
        if result.outcome == RehealOutcome.REENABLED:
            sub.is_active = True
            sub.disabled_at = None
            sub.disabled_reason = ""
            # The live-traffic failure count is what auto-disabled the
            # row; leaving it at the threshold would re-disable on the
            # next single failure rather than after another full run.
            sub.failure_count = 0
            fields += ["is_active", "disabled_at", "disabled_reason", "failure_count"]
            reenabled += 1
        sub.save(update_fields=fields + ["updated_at", "version"])

        if result.notify_operator:
            from core.events import Event

            Event.emit(
                "webhook_subscription.reheal_exhausted",
                payload={
                    "subscription_guid": str(sub.guid),
                    "url": sub.url,
                    "consecutive_failed_reheals": result.consecutive_failed_reheals,
                    "last_status_code": status_code,
                },
                resource_kind="webhook_subscription",
                resource_id=str(sub.guid),
                organization_id=sub.organization_id,
            )

    return reenabled


@activity.defn(name="astrolift.scheduled.reheal_webhook_subscriptions")
async def reheal_webhook_subscriptions() -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_reheal_webhook_subscriptions_sync)()


# ---- Audit log prune ---------------------------------------------


def _prune_audit_log_sync(retention_days: int) -> int:
    """Archive and then delete audit events past their org's retention window.

    Deletes now, for orgs that have opted into `audit_export_enabled`, and
    only after the events are safely in the blob store (#1594). Orgs without
    it still only get the count.

    The contradiction this resolves: ``migrations/0003`` binds a
    ``BEFORE UPDATE OR DELETE`` trigger to ``astrolift_operations_auditevent``
    per spec/04 §1 principle 7, while ``Organization.audit_log_retention_days``
    is editable and the UI renders "Audit events retained for N days per
    compliance policy". Both shipped; only one could hold. #1594 settled it as
    export-then-delete, so ``migrations/0023`` narrows the guarantee to
    **append-only except scheduled retention** -- the trigger honours a
    session-local gate, for DELETE only, that this sweep is the only caller
    to set. UPDATE stays refused unconditionally.

    The count this returns is still every row past its window, including
    orgs that have not opted in and rows the archive skipped, so it stays a
    true measure of exposure rather than a measure of what was deleted.

    The window is per-org, with rows whose organization was cleared (the FK
    is SET_NULL) falling back to the activity-level ``retention_days``. Those
    are counted and never archived or deleted: an event with no organization
    has no ``audit_export_enabled`` to consult and no org slug to file under.
    """
    from django.utils import timezone

    from astrolift_identity.models import Organization
    from astrolift_operations.models import AuditEvent
    from astrolift_operations.retention import RetentionPolicy, expired_queryset

    now = timezone.now()
    fallback_days = max(1, int(retention_days))

    def _count(queryset, *, days: int) -> int:
        policy = RetentionPolicy(stream="audit", retention_days=days)
        return int(expired_queryset(AuditEvent, policy, now=now).filter(pk__in=queryset.values("pk")).count())

    total = 0
    org_windows = Organization.objects.filter(deleted_at__isnull=True).values_list(
        "pk", "audit_log_retention_days"
    )
    for org_id, days in org_windows:
        window = max(1, int(days or fallback_days))
        total += _count(AuditEvent.objects.filter(organization_id=org_id), days=window)
        _archive_org(org_id, now=now, retention_days=window)
    total += _count(AuditEvent.objects.filter(organization__isnull=True), days=fallback_days)

    if total:
        log.info(
            "audit retention: %d event(s) past their org retention window; "
            "orgs with audit_export_enabled have had theirs archived and removed",
            total,
        )
    return total


def _archive_org(org_id: int, *, now, retention_days: int) -> None:
    """Archive one org's expired audit events, if it has opted in (#1594).

    Best-effort per org, deliberately: one org's blob-store misconfiguration
    must not stop the sweep reporting for the rest, and this activity's
    contract is the count it returns. The archive logs its own failures.

    Still does not delete. The append-only trigger stands and #1594 carries
    that decision; this only means an org that has opted in now has the
    events off-platform before whichever way that decision goes.
    """
    from astrolift_identity.models import Organization
    from astrolift_operations.audit_archive import (
        archive_expired_audit_events,
        prune_archived_audit_events,
    )

    try:
        org = Organization.objects.get(pk=org_id)
        if not org.audit_export_enabled:
            return
        result = archive_expired_audit_events(org, now=now, retention_days=retention_days)
        # Delete only what the archive actually wrote. If the archive was
        # skipped -- export disabled, no blob store, nothing past retention --
        # `archived_pks` is absent and nothing is deleted. Export-then-delete
        # means exactly that order, with no path where the delete runs and
        # the export did not (#1594).
        prune_archived_audit_events(result.get("archived_pks", []))
    except Exception:  # noqa: BLE001
        log.exception("audit archive: failed for organization %s", org_id)


@activity.defn(name="astrolift.scheduled.prune_audit_log")
async def prune_audit_log(retention_days: int = 365) -> int:
    """Archive then delete audit events past their org's retention window.

    Returns the count past retention, which is not the count deleted: orgs
    without ``audit_export_enabled`` are counted and left alone. See
    ``_prune_audit_log_sync`` and #1594.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_prune_audit_log_sync)(retention_days)


# ---- Cost snapshot -----------------------------------------------


# Cost actuals client factory registry, keyed by ProviderPlugin slug
# ("aws" / "gcp" / "azure"). The activity dispatches through this
# table so a deployer can wire a fake factory in tests / dev without
# patching the activity itself. The factory receives the cluster
# row (with its ``provider_config``) and returns a billing-actuals
# client OR None when the cluster isn't configured for actuals
# (e.g. k8s_native).
#
# Public surface so tests can monkey-patch one entry; the default
# bindings live in ``_default_cost_actuals_factories`` below and
# wire in lazily so an environment without the vendor providers
# package can still register the activity.
COST_ACTUALS_FACTORIES: dict[str, Any] = {}


def _default_cost_actuals_factories() -> dict[str, Any]:
    """Lazy-load the per-cloud actuals clients. Imports are inside
    the per-factory closures so an environment that doesn't ship a
    given cloud's SDK still loads the others."""

    def _aws_factory(cluster: Any) -> Any:
        from aws.cost import AWSBillingActuals, AWSBillingActualsConfig

        return AWSBillingActuals(config=AWSBillingActualsConfig())

    def _gcp_factory(cluster: Any) -> Any:
        from gcp.cost import GCPBillingActuals, GCPBillingActualsConfig

        provider_config = (getattr(cluster, "provider_config", {}) or {}).get("billing", {}) or {}
        return GCPBillingActuals(
            config=GCPBillingActualsConfig(
                project=str(provider_config.get("project", "") or ""),
                dataset=str(provider_config.get("dataset", "") or ""),
                table=str(provider_config.get("table", "") or "gcp_billing_export_resource_v1"),
            )
        )

    def _azure_factory(cluster: Any) -> Any:
        from azure.cost import AzureBillingActuals, AzureBillingActualsConfig

        provider_config = (getattr(cluster, "provider_config", {}) or {}).get("billing", {}) or {}
        return AzureBillingActuals(
            config=AzureBillingActualsConfig(scope=str(provider_config.get("scope", "") or "")),
        )

    return {
        "aws": _aws_factory,
        "gcp": _gcp_factory,
        "azure": _azure_factory,
    }


def _resolve_actuals_factory(provider_slug: str) -> Any | None:
    factory = COST_ACTUALS_FACTORIES.get(provider_slug)
    if factory is not None:
        return factory
    # Lazy-default on first lookup so the default impl can fail
    # cleanly (ImportError on vendor package) without breaking the
    # activity registration.
    try:
        defaults = _default_cost_actuals_factories()
    except Exception:  # noqa: BLE001
        return None
    return defaults.get(provider_slug)


def _capture_platform_cost_snapshot_sync() -> int:
    """Write the daily cost snapshot rows per organization (#502).

    For each org, walk its managed clusters and ask each cloud's
    billing-actuals client (providers/<cloud>/cost.py)
    for the per-service spend over the previous calendar day. Each
    row is keyed back to a ``ManagedService`` via the
    ``astrolift.io/managed_service_id`` tag stamped at provision time;
    rows whose tag is empty or doesn't match a known service land
    with ``managed_service_id = NULL`` so the UI rolls them up as
    "Shared / untagged".

    This keyed on ``astrolift.io/binding`` until #1418, which never
    attributed a single row: no driver ever stamped that tag, because
    a binding is one row per injected env var and its GUIDs are
    recreated on every envelope sync. A cloud resource belongs to
    exactly one managed service, so the service GUID is what the
    drivers can and do stamp.

    A zero-amount ``OTHER`` placeholder row per org is still emitted
    when no driver returns data (or no clusters are configured) so
    the trend chart has a continuous x-axis. Failures on a single
    cloud are logged + skipped — partial data beats no data on a
    multi-cloud install where one tenant's billing-export isn't
    enabled yet.

    Returns the count of CostSnapshot rows produced. Returns 0
    (no-op) when the billing app isn't installed, keeping the
    schedule safe to register on environments without billing
    wired up.
    """
    try:
        from astrolift_billing.models import CostSnapshot
        from astrolift_identity.models import Organization
        from astrolift_services.models import ManagedService
    except ImportError:
        return 0
    from datetime import timedelta

    from django.utils import timezone

    # Snapshots align to UTC calendar days; "yesterday's spend" is
    # the window we ask each cloud for. Today's row is written with
    # ``taken_at=today`` so re-runs the same day are idempotent at
    # the unique constraint.
    today = timezone.now().date()
    window_start = today - timedelta(days=1)
    window_end = today

    n = 0
    for org in Organization.objects.filter(deleted_at__isnull=True).iterator():
        n += _capture_org_cost_snapshot(
            org=org,
            taken_at=today,
            window_start=window_start,
            window_end=window_end,
            CostSnapshot=CostSnapshot,
            ManagedService=ManagedService,
        )
    return n


def _capture_org_cost_snapshot(
    *,
    org: Any,
    taken_at: Any,
    window_start: Any,
    window_end: Any,
    CostSnapshot: Any,
    ManagedService: Any,
) -> int:
    """Walk one org's clusters + write per-binding cost rows.

    Returns the count of CostSnapshot rows produced for this org
    (placeholder + per-binding combined). Always emits the
    placeholder OTHER row so the trend chart x-axis stays
    continuous, even when no cloud returns data.
    """
    from django.db.models import Q

    from astrolift_clusters.models import TenantCluster

    rows = 0

    # Walk clusters scoped to this org (NULL org = platform-shared
    # cluster also counted toward org billing if assigned via
    # AppEnvironment, but the per-org sweep here just keys off the
    # cluster.organization field).
    clusters = TenantCluster.objects.filter(
        organization=org,
        deleted_at__isnull=True,
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    ).select_related("provider_plugin")

    # Cache service GUIDs once per org so the per-row resolve is an
    # O(1) dict hit rather than a per-row DB query. A managed service
    # is owned by exactly one of registered_app / project (model check
    # constraint), so both ownership paths have to be in the filter —
    # scoping to registered_app alone would drop every project-owned
    # shared resource into the untagged bucket.
    services_by_guid: dict[str, Any] = {
        str(s.guid): s
        for s in ManagedService.objects.filter(
            Q(registered_app__organization=org) | Q(project__organization=org),
            deleted_at__isnull=True,
        )
        .select_related("registered_app", "project")
        .iterator()
    }

    seen_provider: set[str] = set()
    for cluster in clusters.iterator():
        provider_slug = (cluster.provider_plugin.slug or "").lower()
        if not provider_slug or provider_slug in seen_provider:
            continue
        seen_provider.add(provider_slug)
        factory = _resolve_actuals_factory(provider_slug)
        if factory is None:
            log.info(
                "cost actuals: no factory registered for provider=%s (cluster=%s) — skipping",
                provider_slug,
                cluster.slug,
            )
            continue
        try:
            client = factory(cluster)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "cost actuals: factory for provider=%s (cluster=%s) raised: %s",
                provider_slug,
                cluster.slug,
                exc,
            )
            continue
        try:
            result = client.query_actuals_by_service(
                start=window_start,
                end=window_end,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "cost actuals: query failed for provider=%s (cluster=%s): %s",
                provider_slug,
                cluster.slug,
                exc,
            )
            continue
        if not isinstance(result, list):
            # BillingActualsUnavailable — log + skip per-binding
            # writes, the OTHER placeholder still goes out below.
            log.info(
                "cost actuals: provider=%s (cluster=%s) unavailable: %s — %s",
                provider_slug,
                cluster.slug,
                getattr(result, "reason", "unknown"),
                getattr(result, "message", ""),
            )
            continue
        rows += _write_actuals_rows(
            org=org,
            taken_at=taken_at,
            provider_slug=provider_slug,
            items=result,
            services_by_guid=services_by_guid,
            CostSnapshot=CostSnapshot,
        )

    # Trend-chart x-axis continuity: always emit one OTHER
    # placeholder per org per day.
    _, created = CostSnapshot.objects.get_or_create(
        organization=org,
        taken_at=taken_at,
        registered_app=None,
        managed_service_binding=None,
        managed_service=None,
        by=CostSnapshot.CostBy.OTHER,
        source=CostSnapshot.Source.PLATFORM_METER,
        defaults={"amount_cents": 0, "currency": "USD"},
    )
    if created:
        rows += 1
    return rows


def _write_actuals_rows(
    *,
    org: Any,
    taken_at: Any,
    provider_slug: str,
    items: list[Any],
    services_by_guid: dict[str, Any],
    CostSnapshot: Any,
) -> int:
    """Persist one CostSnapshot row per actuals line item.

    Rows whose ``managed_service_guid`` resolves to a known service
    write both that FK and the owning app's; rows with an empty or
    unknown GUID write NULL for both so they roll up under the
    "Shared / untagged" bucket. Idempotent via the model's unique
    constraint on (organization, registered_app,
    managed_service_binding, managed_service, by, taken_at, source).
    """
    rows = 0
    for item in items:
        service_guid = (getattr(item, "managed_service_guid", "") or "").strip()
        service = services_by_guid.get(service_guid) if service_guid else None
        registered_app = None
        project = None
        by = CostSnapshot.CostBy.MANAGED_SERVICE if service is not None else CostSnapshot.CostBy.OTHER
        if service is not None:
            registered_app = service.registered_app
            project = service.project
            owner = registered_app or project
            # ``services_by_guid`` is already filtered to this org, so
            # a mismatch here means the filter and the FK disagree —
            # refuse the write rather than bill one tenant for
            # another's resource.
            if owner is None or owner.organization_id != org.id:
                log.warning(
                    "cost actuals: managed service %s does not belong to org %s — skipping",
                    service_guid,
                    org.id,
                )
                continue
        amount_cents = int(getattr(item, "amount_cents", 0) or 0)
        currency = getattr(item, "currency", "USD") or "USD"
        _, created = CostSnapshot.objects.get_or_create(
            organization=org,
            taken_at=taken_at,
            project=project,
            registered_app=registered_app,
            managed_service_binding=None,
            managed_service=service,
            by=by,
            source=CostSnapshot.Source.PROVIDER_ESTIMATE,
            defaults={"amount_cents": amount_cents, "currency": currency},
        )
        if created:
            rows += 1
        else:
            # An existing row from an earlier same-day run takes
            # precedence (snapshots are immutable per the model
            # docstring — corrections come as a later taken_at).
            log.debug(
                "cost actuals: snapshot already exists for org=%s service=%s — skipping",
                org.slug,
                service_guid or "<untagged>",
            )
    log.info(
        "cost actuals: wrote %d row(s) for org=%s provider=%s",
        rows,
        org.slug,
        provider_slug,
    )
    return rows


@activity.defn(name="astrolift.scheduled.capture_platform_cost_snapshot")
async def capture_platform_cost_snapshot() -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_capture_platform_cost_snapshot_sync)()


# ---- Quota usage snapshot (#1182) ---------------------------------


# Retention window for append-only quota usage snapshots. Rows past this
# age are pruned each pass so the history table stays bounded.
_QUOTA_USAGE_RETENTION_DAYS = 90


def _capture_quota_usage_snapshot_sync() -> int:
    """Append one QuotaUsageSnapshot per active quota (#1182).

    Mirrors the daily cost-snapshot collector: each pass records the
    current ``(used, limit)`` reading for every active Quota so the quota
    detail view can plot usage-vs-limit over time (``Quota.current_usage``
    is otherwise a single scalar the reconciliation overwrites in place,
    with no time dimension). Idempotent per calendar day via the model's
    ``(quota, captured_at)`` unique constraint, so a same-day re-run
    doesn't double-write. Snapshots older than the retention window are
    pruned in the same pass.

    Returns the count of snapshot rows created this pass. Returns 0
    (no-op) when the billing app isn't installed, keeping the schedule
    safe to register on environments without billing wired up.
    """
    try:
        from astrolift_billing.models import Quota, QuotaUsageSnapshot
    except ImportError:
        return 0
    from datetime import timedelta

    from django.utils import timezone

    today = timezone.now().date()

    created = 0
    for quota in Quota.objects.filter(deleted_at__isnull=True).select_related("organization").iterator():
        try:
            _, was_created = QuotaUsageSnapshot.objects.get_or_create(
                quota=quota,
                captured_at=today,
                defaults={
                    "organization_id": quota.organization_id,
                    "used": quota.current_usage,
                    "limit": quota.hard_limit,
                },
            )
        except Exception as exc:  # noqa: BLE001
            # One quota's failure never aborts the sweep.
            log.warning("quota usage snapshot: quota=%s failed: %s", quota.guid, exc)
            continue
        if was_created:
            created += 1

    # Retention: hard-delete rows past the window. QuerySet.delete() is a
    # bulk SQL delete that bypasses the per-instance append-only guard by
    # design — retention is a data-lifecycle op, not an application UPDATE.
    cutoff = today - timedelta(days=_QUOTA_USAGE_RETENTION_DAYS)
    QuotaUsageSnapshot.objects.filter(captured_at__lt=cutoff).delete()

    return created


@activity.defn(name="astrolift.scheduled.capture_quota_usage_snapshot")
async def capture_quota_usage_snapshot() -> int:
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_capture_quota_usage_snapshot_sync)()


# ---- Stale-session prune (#498) -----------------------------------


def _prune_stale_sessions_sync() -> int:
    """Soft-revoke ``AstroliftSession`` rows past the per-kind stale TTL.

    Per-kind threshold comes from
    ``STALE_SESSION_TTL_SECONDS_<KIND>`` Constance entries
    (defaults baked into ``DEFAULT_STALE_SESSION_TTL_SECONDS``).
    Rows are soft-deleted via ``revoke_session`` so the underlying
    ``django_session`` row also gets dropped — the cookie fails auth
    on the next request, not just the GraphQL listing.

    Returns the count of revoked rows so the workflow's result
    message is honest about how much it pruned.
    """
    from datetime import timedelta

    from django.utils import timezone

    from astrolift_identity.models import (
        DEFAULT_STALE_SESSION_TTL_SECONDS,
        AstroliftSession,
        ClientKind,
        RevocationReason,
    )
    from astrolift_identity.sessions import revoke_session

    try:
        from constance import config as constance_config
    except Exception:  # noqa: BLE001
        constance_config = None  # type: ignore[assignment]

    revoked = 0
    now = timezone.now()
    for kind in ClientKind.values:
        ttl_seconds = DEFAULT_STALE_SESSION_TTL_SECONDS.get(kind, 30 * 24 * 3600)
        if constance_config is not None:
            entry = getattr(constance_config, f"STALE_SESSION_TTL_SECONDS_{kind.upper()}", None)
            if isinstance(entry, int) and entry > 0:
                ttl_seconds = entry
        cutoff = now - timedelta(seconds=ttl_seconds)
        qs = AstroliftSession.objects.filter(
            client_kind=kind,
            last_seen_at__lt=cutoff,
        )
        for row in qs.iterator():
            revoke_session(
                row=row,
                actor_user_id=None,
                reason=RevocationReason.AUTO_STALE,
            )
            revoked += 1
        # Sessions that have never been seen (last_seen_at is NULL)
        # but are older than the threshold by ``created_at`` are also
        # stale — covers tokens that were issued + never used.
        qs_unseen = AstroliftSession.objects.filter(
            client_kind=kind,
            last_seen_at__isnull=True,
            created_at__lt=cutoff,
        )
        for row in qs_unseen.iterator():
            revoke_session(
                row=row,
                actor_user_id=None,
                reason=RevocationReason.AUTO_STALE,
            )
            revoked += 1
    return revoked


@activity.defn(name="astrolift.scheduled.prune_stale_sessions")
async def prune_stale_sessions() -> int:
    """Soft-revoke AstroliftSession rows past the per-kind stale TTL."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    return await sync_to_async(_prune_stale_sessions_sync)()


# ---- Approval timeout sweep (#779) --------------------------------


def _expire_pending_approval_deployments_sync() -> int:
    """Transition pending_approval Deployments past their magic-link expiry
    to failed with reason ``approval_timeout``.

    Called hourly by ``ExpirePendingApprovalDeploymentsWorkflow``. The
    approval policy (``astrolift_lifecycle.approval``) sets the magic-link
    expiry at 7 days by default; operators can configure per-environment
    approval_timeout_seconds. We use ``approval_token_expires_at`` as the
    deadline — it's set by the same policy and is already persisted.

    Returns the count of deployments transitioned.
    """
    from django.utils import timezone

    from astrolift_lifecycle.models import Deployment

    now = timezone.now()
    expired = Deployment.objects.filter(
        status=Deployment.Status.PENDING_APPROVAL.value,
        approval_token_expires_at__lt=now,
        deleted_at__isnull=True,
    )
    n = 0
    for deployment in expired.iterator():
        deployment.aborted_reason = "approval_timeout"
        deployment.save(update_fields=["aborted_reason", "updated_at", "version"])
        deployment.transition_to(Deployment.Status.FAILED)
        n += 1
    return n


@activity.defn(name="astrolift.scheduled.expire_pending_approval_deployments")
async def expire_pending_approval_deployments() -> int:
    """Auto-fail pending_approval deployments past their magic-link expiry."""
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    n = await sync_to_async(_expire_pending_approval_deployments_sync)()
    log.info("expire_pending_approval_deployments: transitioned %d deployment(s) to failed", n)
    return n


__all__ = [
    "capture_platform_cost_snapshot",
    "detect_drift",
    "expire_pending_approval_deployments",
    "gc_stale_previews",
    "poll_scheduled_job_runs",
    "prune_audit_log",
    "prune_stale_sessions",
    "reconcile_cluster_capabilities",
    "reheal_webhook_subscriptions",
]
