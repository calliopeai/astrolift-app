"""
CommandRunWorkflow + RunScheduledJobWorkflow policy
(#142, spec 06 §4.20 + §4.20a).

Pure-Python policy. The two Temporal workflows
(``CommandRunWorkflow``, ``RunScheduledJobWorkflow``) consult
this for timeout enforcement, K8s Job spec rendering, and
classification of failure modes (OOM vs deadline vs image-pull).

Both workflows share the same job-execution + log-streaming
spine; ``RunScheduledJobWorkflow`` adds a concurrency guard so
the operator's 'Run Now' button doesn't pile up parallel runs.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from enum import Enum

# Spec 06 §4.20 timeout bounds.
DEFAULT_COMMAND_TIMEOUT_SECONDS = 5 * 60
MAX_COMMAND_TIMEOUT_SECONDS = 60 * 60


class CommandRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_TIMEOUT = "failed_timeout"
    FAILED_OOM = "failed_oom"
    FAILED_IMAGE_PULL = "failed_image_pull"
    FAILED = "failed"


class CommandRunError(ValueError):
    pass


def normalize_timeout(timeout_seconds: int | None) -> int:
    """Apply spec defaults + caps. Caller passes the manifest /
    user-supplied timeout; this normalizes it.

    None -> default 5min. Caller-supplied beyond the 1hr cap is
    clamped (with a warning surfaced upstream); negative is
    rejected.
    """
    if timeout_seconds is None:
        return DEFAULT_COMMAND_TIMEOUT_SECONDS
    if timeout_seconds <= 0:
        raise CommandRunError(f"timeout_seconds must be positive, got {timeout_seconds}")
    if timeout_seconds > MAX_COMMAND_TIMEOUT_SECONDS:
        return MAX_COMMAND_TIMEOUT_SECONDS
    return timeout_seconds


# ---- K8s Job spec rendering ----------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class JobSpecPlan:
    """The fields the workflow hands to the cluster driver to
    create a one-shot Job. Caller renders to a k8s Job manifest;
    we keep this layer pure so tests don't need the cluster."""

    namespace: str
    job_name: str
    image: str
    command: tuple[str, ...]
    env: Mapping[str, str]
    timeout_seconds: int
    """Translated to ``activeDeadlineSeconds`` on the Job spec."""

    backoff_limit: int = 0
    """0 retries — one-shot. CronJob's RunNow gets the same
    treatment: if the user hit 'Run Now', they want it to
    complete or fail visibly, not silently retry."""

    cleanup_after_seconds: int = 600
    """``ttlSecondsAfterFinished`` so completed Jobs don't
    accumulate. 10 min is enough time for the workflow to capture
    logs + delete."""


def plan_job_spec(
    *,
    app_slug: str,
    run_id: str,
    namespace: str,
    image: str,
    command: Sequence[str],
    env: Mapping[str, str],
    timeout_seconds: int | None = None,
) -> JobSpecPlan:
    """Compose the Job plan. Job name format:
    ``cmd-<app>-<run_id>`` so an operator can find it via kubectl
    by run id."""
    if not app_slug or not namespace or not image:
        raise CommandRunError("app_slug, namespace, image are all required")
    if not command:
        raise CommandRunError("command must not be empty")
    if not run_id:
        raise CommandRunError("run_id is required")

    return JobSpecPlan(
        namespace=namespace,
        job_name=f"cmd-{app_slug}-{run_id}",
        image=image,
        command=tuple(command),
        env=dict(env),
        timeout_seconds=normalize_timeout(timeout_seconds),
    )


# ---- failure-mode classification -----------------------------------


def classify_pod_failure(
    *,
    exit_code: int | None,
    pod_phase: str,
    reason: str,
    deadline_exceeded: bool,
) -> CommandRunStatus:
    """Translate observed pod state to a CommandRunStatus.

    The cluster driver's `pod_status` activity returns these
    fields; this module decides which CommandRunStatus to write
    on the CommandRun row.

    Order of detection:
      1. deadline_exceeded -> FAILED_TIMEOUT
      2. reason='OOMKilled' -> FAILED_OOM
      3. reason in image-pull set -> FAILED_IMAGE_PULL
      4. pod_phase='Succeeded' / exit_code 0 -> SUCCEEDED
      5. otherwise FAILED (generic)
    """
    if deadline_exceeded:
        return CommandRunStatus.FAILED_TIMEOUT
    if reason == "OOMKilled":
        return CommandRunStatus.FAILED_OOM
    if reason in ("ImagePullBackOff", "ErrImagePull", "InvalidImageName"):
        return CommandRunStatus.FAILED_IMAGE_PULL
    if pod_phase == "Succeeded" or exit_code == 0:
        return CommandRunStatus.SUCCEEDED
    return CommandRunStatus.FAILED


# ---- scheduled-job concurrency guard -------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ScheduledJobRun:
    """Minimum projection of a ScheduledJobRun row the
    concurrency guard consumes."""

    run_id: str
    status: CommandRunStatus
    started_at_unix: int


class ConcurrencyError(CommandRunError):
    """A 'Run Now' was attempted while another run is in
    flight. Workflow returns this to the caller; UI surfaces
    'a run is already in progress'."""


def assert_no_concurrent_run(
    *,
    cron_job_name: str,
    in_flight_runs: Sequence[ScheduledJobRun],
    allow_concurrent: bool = False,
) -> None:
    """Spec 06 §4.20a: prevent concurrent manual runs unless the
    operator opts in via ``allow_concurrent=True`` on the
    CronJob.

    'In flight' = status PENDING or RUNNING. Completed runs
    don't block."""
    if allow_concurrent:
        return
    in_flight = [
        r for r in in_flight_runs if r.status in (CommandRunStatus.PENDING, CommandRunStatus.RUNNING)
    ]
    if in_flight:
        raise ConcurrencyError(
            f"cronjob {cron_job_name!r} has {len(in_flight)} "
            f"in-flight manual run(s); set allow_concurrent=True "
            "on the CronJob if you want parallel runs"
        )
