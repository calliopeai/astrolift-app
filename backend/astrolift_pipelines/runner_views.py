"""Self-hosted runner REST API — runner agent protocol (#83).

These endpoints are called by the self-hosted runner agent process.
The runner is a long-running process on the operator's machine that:
1. Registers with the Controller (POST /runners/register/)
2. Sends heartbeats (POST /runners/heartbeat/)
3. Claims jobs (GET /runners/claim/) — long-poll for next JobRun
4. Reports completion (POST /runners/<runner_id>/jobs/<job_run_id>/complete/)

Auth: Bearer <api_key> issued at registration (same pattern as Dispatch Service).

Security: All endpoints validate the runner's scoped API key. The key
is tied to a specific Runner record and cannot be used to access other
runners' data or org-level operations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_pipelines.models import JobRun, Runner

logger = logging.getLogger(__name__)

_API_KEY_BYTES = 32
_REGISTRATION_TOKEN_SETTING = "RUNNER_REGISTRATION_TOKEN"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _get_runner_from_request(request: HttpRequest) -> Runner | None:
    """Authenticate a runner API request via Bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    raw_key = auth[len("Bearer ") :]
    key_hash = _hash(raw_key)
    return Runner.objects.filter(
        api_key_hash=key_hash,
        status__in=["idle", "active", "offline"],
        deleted_at__isnull=True,
    ).first()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def runner_register(request: HttpRequest) -> JsonResponse:
    """Register a self-hosted runner.

    Auth: Bearer <registration_token> (set in settings as RUNNER_REGISTRATION_TOKEN)
    Body: {org_slug, name, os, arch, labels, version_string}
    Returns: {runner_id, api_key}
    """
    from django.conf import settings

    reg_token = getattr(settings, _REGISTRATION_TOKEN_SETTING, None)
    # Fail closed (#1183): registration mints a runner API key bound to the
    # org named by the client's ``org_slug``. With no token configured the
    # check used to be skipped entirely, letting any caller register a runner
    # under any org's slug. An unset token now disables registration rather
    # than opening it to everyone.
    if not reg_token:
        return JsonResponse({"error": "runner registration is not enabled"}, status=403)
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {reg_token}":
        return JsonResponse({"error": "invalid registration token"}, status=401)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    from astrolift_identity.models import Organization

    org_slug = body.get("org_slug", "")
    org = Organization.objects.filter(slug=org_slug, deleted_at__isnull=True).first()
    if not org:
        return JsonResponse({"error": f"org '{org_slug}' not found"}, status=404)

    name = (body.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "name is required"}, status=400)

    raw_key = secrets.token_hex(_API_KEY_BYTES)
    key_hash = _hash(raw_key)

    slug = f"{org_slug}-{name}".lower().replace(" ", "-")[:200]
    runner, _ = Runner.objects.update_or_create(
        organization=org,
        slug=slug,
        defaults={
            "name": name,
            "api_key_hash": key_hash,
            "os": body.get("os", "linux"),
            "arch": body.get("arch", "amd64"),
            "labels": body.get("labels") or [],
            "version_string": body.get("version_string", ""),
            "status": Runner.Status.OFFLINE,
        },
    )

    logger.info("runner.register: registered runner %s for org %s", name, org_slug)
    return JsonResponse({"runner_id": str(runner.guid), "api_key": raw_key}, status=201)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def runner_heartbeat(request: HttpRequest) -> JsonResponse:
    """Update runner last_heartbeat_at. Transitions OFFLINE → IDLE on first beat."""
    runner = _get_runner_from_request(request)
    if not runner:
        return JsonResponse({"error": "unauthorized"}, status=401)

    now = timezone.now()
    update_fields = ["last_heartbeat_at", "updated_at", "version"]
    runner.last_heartbeat_at = now

    if runner.status == Runner.Status.OFFLINE:
        runner.status = Runner.Status.IDLE
        update_fields.append("status")

    runner.save(update_fields=update_fields)
    return JsonResponse({"ok": True, "status": runner.status})


# ---------------------------------------------------------------------------
# Job claim (long-poll)
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["GET"])
def runner_claim_job(request: HttpRequest) -> JsonResponse:
    """Return the next pending job for this runner to execute.

    Matches against runner.os, runner.arch, runner.labels (runs_on config).
    Returns 204 when no matching job is pending.
    """
    runner = _get_runner_from_request(request)
    if not runner:
        return JsonResponse({"error": "unauthorized"}, status=401)

    if not runner.is_available():
        return JsonResponse({"error": f"runner not available (status={runner.status})"}, status=409)

    # Find a PENDING JobRun whose Job.runs_on matches this runner
    job_run = _find_matching_job_run(runner)
    if not job_run:
        return JsonResponse({}, status=204)

    # Claim the job
    runner.status = Runner.Status.ACTIVE
    runner.current_job_run = job_run
    runner.save(update_fields=["status", "current_job_run", "updated_at", "version"])

    job_run.status = "running"
    job_run.started_at = timezone.now()
    job_run.save(update_fields=["status", "started_at", "updated_at", "version"])

    job = job_run.job
    return JsonResponse(
        {
            "job_run_id": str(job_run.guid),
            "job_id": job.job_id,
            "job_name": job.name,
            "container_image": job.container_image or "ghcr.io/calliopeai/astrolift-builder:latest",
            "env": {},  # populated from pipeline context evaluator
            "toml_path": job_run.pipeline_run.pipeline.toml_path,
        }
    )


def _find_matching_job_run(runner: Runner) -> JobRun | None:
    """Find the oldest PENDING JobRun that matches this runner's capabilities.

    Constrained to the runner's own organization: a runner authenticates as
    an org-scoped Runner record, so it must only ever claim (and thereby
    receive the config/secrets of) jobs owned by that same org (#1183)."""

    pending = (
        JobRun.objects.filter(
            status="pending",
            pipeline_run__pipeline__organization_id=runner.organization_id,
        )
        .select_related("job", "pipeline_run__pipeline")
        .order_by("created_at")
    )

    for job_run in pending[:50]:  # check up to 50 to find a match
        runs_on = job_run.job.runs_on or "astrolift/default"
        if _runs_on_matches_runner(runs_on, runner):
            return job_run
    return None


def _runs_on_matches_runner(runs_on: str, runner: Runner) -> bool:
    """Return True if the runs_on value is satisfied by this runner."""
    if runs_on in ("astrolift/default", "self-hosted"):
        return True
    if runs_on.startswith("runner:"):
        name = runs_on[len("runner:") :]
        return runner.slug.endswith(name) or runner.name == name
    if runs_on.startswith("labels:"):
        try:
            import json

            required = json.loads(runs_on[len("labels:") :])
            runner_labels = {lbl.split("=")[0]: lbl.split("=")[1] for lbl in runner.labels if "=" in lbl}
            return all(runner_labels.get(k) == v for k, v in required.items())
        except Exception:  # noqa: BLE001
            return False
    return False


# ---------------------------------------------------------------------------
# Job completion
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def runner_complete_job(request: HttpRequest, runner_guid: str, job_run_guid: str) -> JsonResponse:
    """Mark a job run as complete after runner finishes execution.

    Body: {status: "success"|"failure"|"cancelled", exit_code?}
    """
    runner = _get_runner_from_request(request)
    if not runner:
        return JsonResponse({"error": "unauthorized"}, status=401)

    if str(runner.guid) != runner_guid:
        return JsonResponse({"error": "runner mismatch"}, status=403)

    # Scope to the runner's own org AND to the job it actually claimed: a
    # runner may only complete the run it holds (current_job_run), never a
    # foreign org's run or a sibling runner's in-flight job (#1183). Both a
    # cross-org guid and an unclaimed same-org guid answer 404 identically so
    # the response never confirms a foreign job's existence.
    job_run = JobRun.objects.filter(
        guid=job_run_guid,
        pipeline_run__pipeline__organization_id=runner.organization_id,
    ).first()
    if not job_run or runner.current_job_run_id != job_run.pk:
        return JsonResponse({"error": "job run not found"}, status=404)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid JSON"}, status=400)

    new_status = body.get("status")
    if new_status not in {"success", "failure", "cancelled"}:
        return JsonResponse({"error": f"invalid status: {new_status!r}"}, status=400)

    from astrolift_pipelines.state_machine import InvalidTransition, transition_job_run

    try:
        transition_job_run(job_run, new_status, actor_display=f"runner:{runner.slug}")
    except InvalidTransition as exc:
        return JsonResponse({"error": str(exc)}, status=409)

    # Release the runner back to idle
    runner.status = Runner.Status.IDLE
    runner.current_job_run = None
    runner.save(update_fields=["status", "current_job_run", "updated_at", "version"])

    return JsonResponse({"ok": True, "status": job_run.status})
