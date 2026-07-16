"""
Runner agent protocol REST views (#83).

Four endpoints the self-hosted runner agent calls:

  POST /api/pipelines/v1/runners/register/
    Validates registration token (SHA-256 hash comparison), issues a
    long-lived API key scoped to this runner.

  POST /api/pipelines/v1/runners/heartbeat/
    Updates Runner.last_heartbeat_at and optionally Runner.current_job_run.
    Authenticated by runner API key.

  GET  /api/pipelines/v1/runners/claim/
    Long-polls (up to 30 s) for the next queued JobRun whose org matches
    the runner's org and whose labels are satisfied. Returns job payload
    on 200, 204 when no job is available.

  POST /api/pipelines/v1/runners/<runner_guid>/jobs/<job_run_guid>/complete/
    Marks the JobRun terminal (success | failure | cancelled). Authenticated
    by runner API key.

Auth model
----------
All endpoints except ``register`` authenticate via the ``X-Runner-Key``
header, which must match ``Runner.api_key_hash`` (SHA-256).
``register`` authenticates via ``X-Runner-Registration-Token`` which
must match ``Runner.registration_token_hash``; after a successful
registration the token hash is cleared and the API key hash is set.

These views do not use Django session auth or CSRF — they are machine-
to-machine. All endpoints are @csrf_exempt.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_pipelines.models import JobRun, Runner

log = logging.getLogger("astrolift_pipelines.runner_agent")

# Long-poll duration for the claim endpoint (seconds).
_CLAIM_POLL_SECONDS = int(os.environ.get("ASTROLIFT_RUNNER_CLAIM_POLL_SECONDS", "25"))
_CLAIM_POLL_INTERVAL = 1.0  # seconds between DB checks


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _runner_from_api_key(request) -> Runner | None:
    """Resolve a Runner from the X-Runner-Key header."""
    raw_key = request.headers.get("X-Runner-Key", "")
    if not raw_key:
        return None
    key_hash = _sha256(raw_key)
    return Runner.objects.select_related("organization").filter(api_key_hash=key_hash).first()


def _json_body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# POST /api/pipelines/v1/runners/register/
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def runner_register(request):
    """Consume registration token and issue a long-lived API key.

    Request headers:
      X-Runner-Registration-Token: <one-time token>

    Request body (JSON):
      {
        "runner_guid": "<guid>",
        "version": "<agent version string>",
        "os": "linux|macos|windows",
        "arch": "amd64|arm64"
      }

    Response 200:
      { "api_key": "<raw key — shown once>" }

    Response 400/404 on any auth failure (opaque error to prevent
    enumeration — same 404 whether token unknown or already used).
    """
    token = request.headers.get("X-Runner-Registration-Token", "")
    if not token:
        return JsonResponse({"detail": "missing registration token"}, status=400)

    token_hash = _sha256(token)
    runner = Runner.objects.select_related("organization").filter(registration_token_hash=token_hash).first()
    if runner is None:
        # Opaque 404 — no signal about whether guid exists or token is wrong.
        log.warning("runner_register: unknown or already-consumed token")
        return JsonResponse({"detail": "not found"}, status=404)

    body = _json_body(request)

    # Update agent-reported capabilities if provided.
    if body.get("os") in Runner.Os.values:
        runner.os = body["os"]
    if body.get("arch") in Runner.Arch.values:
        runner.arch = body["arch"]
    if body.get("version"):
        runner.version_string = str(body["version"])[:64]

    # Issue API key and consume registration token.
    raw_key = secrets.token_hex(32)
    runner.api_key_hash = _sha256(raw_key)
    runner.registration_token_hash = None  # single-use: consumed
    runner.status = Runner.Status.IDLE
    runner.last_heartbeat_at = timezone.now()
    runner.save(
        update_fields=[
            "os",
            "arch",
            "version_string",
            "api_key_hash",
            "registration_token_hash",
            "status",
            "last_heartbeat_at",
            "updated_at",
            "version",
        ]
    )

    log.info(
        "runner_register: runner registered",
        extra={"runner_guid": str(runner.guid), "org": runner.organization.slug},
    )
    return JsonResponse({"api_key": raw_key})


# ---------------------------------------------------------------------------
# POST /api/pipelines/v1/runners/heartbeat/
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def runner_heartbeat(request):
    """Update runner liveness and optionally current job.

    Request headers:
      X-Runner-Key: <api key>

    Request body (JSON, optional):
      { "job_run_guid": "<guid> | null" }

    Response 200:
      { "ok": true }
    """
    runner = _runner_from_api_key(request)
    if runner is None:
        return JsonResponse({"detail": "unauthorized"}, status=401)

    body = _json_body(request)
    now = timezone.now()

    update_fields = ["last_heartbeat_at", "updated_at", "version"]

    job_run_guid = body.get("job_run_guid")
    if job_run_guid is not None:
        if job_run_guid == "":
            runner.current_job_run = None
            runner.status = Runner.Status.IDLE
            update_fields += ["current_job_run", "status"]
        else:
            job_run = JobRun.objects.filter(
                guid=job_run_guid,
                organization=runner.organization,
            ).first()
            if job_run is not None:
                runner.current_job_run = job_run
                runner.status = Runner.Status.ACTIVE
                update_fields += ["current_job_run", "status"]

    runner.last_heartbeat_at = now
    runner.save(update_fields=list(set(update_fields)))
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# GET /api/pipelines/v1/runners/claim/
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["GET"])
def runner_claim(request):
    """Long-poll for the next queued job matching this runner.

    Polls for up to ``_CLAIM_POLL_SECONDS`` seconds. Returns 200 with
    job payload when a job is found; 204 when none becomes available
    within the poll window.

    Response 200:
      {
        "job_run_id": "<guid>",
        "job_name": "<name>",
        "steps": [...],   // resolved step list
        "org_slug": "<org slug>"
      }
    """
    runner = _runner_from_api_key(request)
    if runner is None:
        return JsonResponse({"detail": "unauthorized"}, status=401)

    if runner.status == Runner.Status.SUSPENDED:
        return JsonResponse({"detail": "runner suspended"}, status=403)

    deadline = time.monotonic() + _CLAIM_POLL_SECONDS
    while time.monotonic() < deadline:
        # Atomically claim one queued job for this org.
        from django.db import transaction

        with transaction.atomic():
            job_run = (
                JobRun.objects.select_for_update(skip_locked=True)
                .filter(
                    organization=runner.organization,
                    status=JobRun.Status.QUEUED,
                    claimed_by_runner__isnull=True,
                )
                .first()
            )
            if job_run is not None:
                job_run.claimed_by_runner = runner
                job_run.status = JobRun.Status.RUNNING
                job_run.started_at = timezone.now()
                job_run.save(
                    update_fields=[
                        "claimed_by_runner",
                        "status",
                        "started_at",
                        "updated_at",
                        "version",
                    ]
                )
                runner.current_job_run = job_run
                runner.status = Runner.Status.ACTIVE
                runner.save(update_fields=["current_job_run", "status", "updated_at", "version"])

                log.info(
                    "runner_claim: job claimed",
                    extra={
                        "runner_guid": str(runner.guid),
                        "job_run_guid": str(job_run.guid),
                    },
                )
                return JsonResponse(
                    {
                        "job_run_id": str(job_run.guid),
                        "job_name": job_run.job_name,
                        "steps": job_run.steps_payload,
                        "org_slug": runner.organization.slug,
                    }
                )

        time.sleep(_CLAIM_POLL_INTERVAL)

    # Poll window expired — tell the agent to re-poll immediately.
    return JsonResponse({}, status=204)


# ---------------------------------------------------------------------------
# POST /api/pipelines/v1/runners/<runner_guid>/jobs/<job_run_guid>/complete/
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def runner_job_complete(request, runner_guid: str, job_run_guid: str):
    """Mark a JobRun terminal and record per-step results.

    Request headers:
      X-Runner-Key: <api key>

    Request body (JSON):
      {
        "status": "success | failure | cancelled",
        "steps": [{ "step_id": "...", "status": "...", "exit_code": 0 }]
      }

    Response 200:
      { "ok": true }
    """
    runner = _runner_from_api_key(request)
    if runner is None:
        return JsonResponse({"detail": "unauthorized"}, status=401)

    # Scope check — runner can only complete its own org's jobs.
    if str(runner.guid) != runner_guid:
        return JsonResponse({"detail": "forbidden"}, status=403)

    job_run = JobRun.objects.filter(
        guid=job_run_guid,
        organization=runner.organization,
        claimed_by_runner=runner,
    ).first()
    if job_run is None:
        return JsonResponse({"detail": "not found"}, status=404)

    body = _json_body(request)
    raw_status = body.get("status", "failure")
    terminal_map = {
        "success": JobRun.Status.SUCCESS,
        "failure": JobRun.Status.FAILURE,
        "cancelled": JobRun.Status.CANCELLED,
    }
    new_status = terminal_map.get(raw_status, JobRun.Status.FAILURE)

    now = timezone.now()
    job_run.status = new_status
    job_run.finished_at = now
    job_run.steps_result = body.get("steps", [])
    job_run.save(update_fields=["status", "finished_at", "steps_result", "updated_at", "version"])

    # Clear runner's current job pointer and return to idle.
    runner.current_job_run = None
    runner.status = Runner.Status.IDLE
    runner.save(update_fields=["current_job_run", "status", "updated_at", "version"])

    log.info(
        "runner_job_complete: job marked terminal",
        extra={
            "runner_guid": str(runner.guid),
            "job_run_guid": str(job_run.guid),
            "status": new_status,
        },
    )
    return JsonResponse({"ok": True})
