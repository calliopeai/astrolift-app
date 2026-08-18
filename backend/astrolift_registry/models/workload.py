"""
Workload — a runtime kind under a RegisteredApp.

A registered app has one or more workloads (deployment, statefulset,
job, cronjob). The workload row is the *static* description (replicas,
resource asks, HPA targets); the runtime state lives on Deployment.

``is_public`` selects which workload(s) get an ingress.
"""

from __future__ import annotations

from django.db import models

from core.models.base import NamedBaseCoreModel


class Workload(NamedBaseCoreModel):
    class Kind(models.TextChoices):
        DEPLOYMENT = "deployment"
        STATEFULSET = "statefulset"
        JOB = "job"
        CRONJOB = "cronjob"
        # One-shot ``batch/v1 Job`` (runs once, no schedule). Distinct
        # from the legacy ``JOB`` choice — ``task`` is the kind the
        # manifest parser / renderer support end-to-end.
        TASK = "task"
        # Long-running AI agent (#795). Renders to the same K8s shape as
        # ``DEPLOYMENT`` (Deployment + Service + HPA) but carries agent
        # dispatch tuning and an ``astrolift.dev/workload-kind: agent``
        # pod annotation; per-dispatch results land on AgentRun (#804).
        AGENT = "agent"
        # Temporal workflow worker (#796). Renders to the same K8s shape
        # as ``DEPLOYMENT`` (Deployment + Service + HPA) but carries an
        # ``astrolift.dev/workload-kind: workflow`` pod annotation and
        # injected ``ASTROLIFT_WORKFLOW_TYPE`` / ``ASTROLIFT_TASK_QUEUE`` /
        # ``TEMPORAL_NAMESPACE`` env so the worker registers against the
        # platform Temporal cluster. Distinct from ``WorkflowDefinition``
        # (the BUILD-concern state-machine model) — this is the worker
        # process deployment. The DB migration for all new Kind values
        # (TASK/AGENT/WORKFLOW/FUNCTION) lands together in #805.
        WORKFLOW = "workflow"
        FUNCTION = "function"

    class ConcurrencyPolicy(models.TextChoices):
        # Mirrors Kubernetes ``CronJob.spec.concurrencyPolicy``:
        #   forbid  → ``Forbid``  (skip the next firing while one runs)
        #   queue   → ``Allow``   (let overlapping runs stack)
        #   replace → ``Replace`` (kill the in-flight run, start fresh)
        # Only meaningful when ``kind == CRONJOB``; deployment / job /
        # statefulset rows ignore the field. Default ``forbid`` matches
        # the platform's manifest renderer pre-#427 behaviour so the
        # migration is a no-op for every existing cronjob.
        FORBID = "forbid"
        QUEUE = "queue"
        REPLACE = "replace"

    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="workloads",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.DEPLOYMENT)
    is_public = models.BooleanField(default=False)
    schedule = models.CharField(max_length=64, blank=True, default="")
    concurrency_policy = models.CharField(
        max_length=16,
        choices=ConcurrencyPolicy.choices,
        default=ConcurrencyPolicy.FORBID,
    )
    notify_on_failure = models.BooleanField(default=True)

    replicas = models.PositiveIntegerField(default=1)
    # Replica count snapshotted when the app is archived (#743) so
    # restoreApp can bring workloads back to their pre-archive state.
    # Null means this workload was never archived.
    pre_archive_replicas = models.PositiveIntegerField(null=True, blank=True)
    cpu_request = models.CharField(max_length=32, blank=True, default="")
    cpu_limit = models.CharField(max_length=32, blank=True, default="")
    memory_request = models.CharField(max_length=64, blank=True, default="")
    memory_limit = models.CharField(max_length=64, blank=True, default="")

    hpa_min_replicas = models.PositiveIntegerField(null=True, blank=True)
    hpa_max_replicas = models.PositiveIntegerField(null=True, blank=True)
    hpa_target_cpu_pct = models.PositiveIntegerField(default=80)

    storage_class = models.CharField(max_length=64, blank=True, default="")
    storage_size = models.CharField(max_length=32, blank=True, default="")

    # Volume declarations from the manifest (#739). Stored as the raw
    # list of volume-config dicts so the workload-detail page can render
    # volume cards without re-parsing the TOML. Each dict mirrors the
    # TOML ``[[workloads.<name>.volumes]]`` shape.
    volumes = models.JSONField(default=list, blank=True)

    class AgentVariant(models.TextChoices):
        # Matches the three canonical agent base images (#40).
        # ``headless`` — no display, full CLI toolchain.
        # ``terminal_novnc`` — headless + noVNC terminal UI.
        # ``terminal_novnc_browser`` — headless + noVNC + Chromium.
        HEADLESS = "headless"
        TERMINAL_NOVNC = "terminal_novnc"
        TERMINAL_NOVNC_BROWSER = "terminal_novnc_browser"

    class AgentRuntime(models.TextChoices):
        # Second axis: the underlying LLM runtime the agent is tuned for.
        CLAUDE = "claude"
        GEMINI = "gemini"
        CODEX = "codex"
        UNIVERSAL = "universal"

    # Agent dispatch tuning (#795). Only meaningful when
    # ``kind == AGENT``; other kinds carry the defaults and ignore them.
    # ``max_retries`` / ``tool_timeout_seconds`` are injected into the
    # agent pod as ``ASTROLIFT_MAX_RETRIES`` / ``ASTROLIFT_TOOL_TIMEOUT``
    # env vars by the renderer; ``result_ttl_hours`` governs how long an
    # AgentRun result is retained (#804). Defaults mirror the manifest
    # spec, so a deployment/task/cronjob row keeps them at the no-op
    # values and the migration is a no-op for existing rows.
    max_retries = models.PositiveIntegerField(default=5)
    tool_timeout_seconds = models.PositiveIntegerField(default=300)
    result_ttl_hours = models.PositiveIntegerField(default=72)

    # Agent image variant (#40). Selects the canonical base image for the
    # workload's container. Null means the caller supplies image_ref
    # directly. Only meaningful when ``kind == AGENT``.
    agent_variant = models.CharField(
        max_length=32,
        choices=AgentVariant.choices,
        blank=True,
        default="",
    )
    # Agent LLM runtime (#40). Second axis — selects the runtime preset
    # injected into the pod. Null means no preset; the container image
    # carries its own defaults.
    agent_runtime = models.CharField(
        max_length=32,
        choices=AgentRuntime.choices,
        blank=True,
        default="",
    )

    # Definitional Brief for an ``agent`` workload (spec 38, Phase 2).
    # The assembled, content-hashed package the agent fetches at boot;
    # set at registration in Phase 3. SET_NULL so revoking/deleting a
    # Brief leaves the workload registered (it can be re-assembled).
    # Only meaningful when ``kind == AGENT``.
    brief = models.ForeignKey(
        "astrolift_agents.Brief",
        related_name="agent_workloads",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # ---- Run spec (spec 33, PR-1) ------------------------------------
    # How an ``agent`` workload runs. Only meaningful when
    # ``kind == AGENT``; every other kind carries the defaults and
    # ignores them (the defaults — task / once — are a no-op for an
    # existing deployment/job/cronjob row, so the migration is additive
    # and non-breaking). PR-1 wires only Once dispatch end-to-end; the
    # other modes (Loop/Schedule/Trigger) + Service replicas + scheduled
    # scaling hang off these same fields in later PRs so the schema is
    # stable and no further agent run-spec migration is needed.

    class RunFamily(models.TextChoices):
        # The platform's native Job-vs-Deployment split for agents:
        #   task    → runs to completion (backed by a k8s Job — the
        #             existing dispatch pipeline / ``execute_agent_stage``).
        #   service → always-on (backed by a k8s Deployment — the app-
        #             deploy path pointed at the agent image), scaled by
        #             ``replicas`` (reuses the shared field above).
        TASK = "task"
        SERVICE = "service"

    class RunMode(models.TextChoices):
        # The Task-family trigger mode (ignored when family == service).
        #   once     → manual ``Dispatch now`` (the only mode wired in PR-1).
        #   loop     → continuous re-dispatch with a concurrency cap (PR-6).
        #   schedule → cron-driven (PR-4, reads ``run_cron_expression``).
        #   trigger  → bound to a webhook / event / condition (PR-6).
        # And the mode that is not a trigger at all:
        #   persistent → nothing dispatches it. The agent runs as a warm
        #             pod holding a tmux session so a human or the relay can
        #             attach to it (#128). Distinct from ``service``: a
        #             service runs the agent as its entrypoint and serves
        #             traffic, whereas a box runs nothing in particular and
        #             exists to be exec'd into. Instances are ``AgentBox``
        #             rows, not ``AgentTask`` rows, because a box has no
        #             completion to report.
        ONCE = "once"
        LOOP = "loop"
        SCHEDULE = "schedule"
        TRIGGER = "trigger"
        PERSISTENT = "persistent"

    run_family = models.CharField(
        max_length=16,
        choices=RunFamily.choices,
        default=RunFamily.TASK,
    )
    run_mode = models.CharField(
        max_length=16,
        choices=RunMode.choices,
        default=RunMode.ONCE,
    )
    # 5-field cron for ``run_mode == SCHEDULE`` (PR-4). Validated against
    # the platform cron parser at the mutation boundary; stored verbatim.
    run_cron_expression = models.CharField(max_length=128, blank=True, default="")
    # Operator kill-switch: pauses scheduled / loop / trigger dispatch
    # without unregistering the agent (PR-4/PR-6 honour it). Once dispatch
    # is manual and ignores it.
    run_paused = models.BooleanField(default=False)
    # Task concurrency cap — the max number of in-flight runs for this
    # agent (consumed by the PR-6 Loop controller). Null means uncapped.
    # Distinct from ``concurrency_policy`` above, which is the CronJob
    # overlap policy (forbid/queue/replace) and only applies to ``cronjob``.
    run_max_parallel = models.PositiveSmallIntegerField(null=True, blank=True)

    # ---- Scheduled scaling placeholders (spec 33, PR-5) --------------
    # Declared null here so PR-5 (cron → replicas X/0 for a Service-family
    # agent) needs no further migration. PR-1 does NOT read or write these
    # — they are inert until the scheduled-scaling tick + editor land.
    # ``scheduled_scale_to`` is the replica count the up-cron scales to;
    # the down-cron always scales to 0 (business-hours-up / off-hours-zero).
    scheduled_scale_to = models.PositiveIntegerField(null=True, blank=True)
    scale_up_cron = models.CharField(max_length=128, blank=True, default="")
    scale_down_cron = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="workload_slug_unique_active_per_app",
            ),
        ]
        indexes = [
            models.Index(fields=["registered_app", "kind"], name="workload_app_kind_idx"),
        ]
