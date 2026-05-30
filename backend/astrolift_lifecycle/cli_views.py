"""REST views for the Astrolift CLI + CI deploy surface (#777).

Two endpoints, mounted under ``/api/cli/v1/``:

* ``POST  /api/cli/v1/apps/<slug>/deploy/``     — trigger a CI/manual deploy.
* ``GET   /api/cli/v1/deployments/<guid>/status/`` — poll deployment state.

Auth
----
Both endpoints use **deploy tokens** (``alft_dt_...`` bearer), which are
app-scoped credentials. The ``ApiTokenAuthMiddleware`` is *not* used here
because it only handles API tokens (``alft_at_``); deploy tokens are verified
inline via ``deploy_tokens.verify_token``.

The verification also checks that the token belongs to the app being
deployed — a token for ``app-a`` cannot trigger a deploy on ``app-b``.

Wire protocol
-------------
``POST /api/cli/v1/apps/<slug>/deploy/``
  Request body (JSON):
    {
      "image_tags":    {"web": "abc1234", "worker": "abc1234"},
      "commit_sha":    "abc1234abcdef...",       # ≥ 40 hex chars
      "branch":        "main",
      "environment":   "production",
      "trigger_kind":  "ci",                    # or "manual_cli"
      "idempotency_key": ""                     # optional
    }
  201 on success:
    {
      "deployment_id":   "<guid>",
      "workflow_run_id": "<temporal-run-id>",   # or "" if already in-flight
      "polling_url":     "/api/cli/v1/deployments/<guid>/status/",
      "status":          "pending"
    }
  400 for validation errors (CiDeployError), 401 for bad/missing token,
  403 for token/app mismatch or scope error.

``GET /api/cli/v1/deployments/<guid>/status/``
  201/200 with:
    {
      "deployment_id": "<guid>",
      "status":        "running",         # pending | deploying | running | failed | aborted
      "message":       "...",
      "image_tags":    {"web": "abc1234"},
      "created_at":    "2026-01-01T00:00:00Z",
      "updated_at":    "2026-01-01T00:05:00Z"
    }
  404 if not found or token/app mismatch.
"""

from __future__ import annotations

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from astrolift_lifecycle.ci_deploy import CiDeployError, parse_request
from astrolift_lifecycle.deploy_tokens import PLAINTEXT_PREFIX, verify_token

log = logging.getLogger("astrolift_lifecycle.cli_views")

_TASK_QUEUE = "astrolift-main"


# ── auth helpers ────────────────────────────────────────────────────────────


def _bearer_token(request: HttpRequest) -> str:
    """Extract the raw bearer value from the Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth or not auth.lower().startswith("bearer "):
        return ""
    return auth[7:].strip()


def _resolve_deploy_token(request: HttpRequest, app_slug: str):
    """Verify the deploy token and confirm it is scoped to ``app_slug``.

    Returns ``(deploy_token_row, None)`` on success, or
    ``(None, JsonResponse)`` with the appropriate error.
    """
    bearer = _bearer_token(request)
    if not bearer or not bearer.startswith(PLAINTEXT_PREFIX):
        return None, JsonResponse(
            {"detail": "deploy token required — send Authorization: Bearer alft_dt_..."},
            status=401,
        )

    token = verify_token(bearer)
    if token is None:
        return None, JsonResponse({"detail": "invalid or expired deploy token"}, status=401)

    # Deploy tokens are app-scoped — refuse cross-app use.
    if token.registered_app.slug != app_slug:
        return None, JsonResponse(
            {"detail": "token is not authorized for this app"},
            status=403,
        )

    # Scope check — token must carry the deploy permission.
    if token.scopes and "app.deploy" not in token.scopes:
        return None, JsonResponse(
            {"detail": "token scope does not include app.deploy"},
            status=403,
        )

    return token, None


def _resolve_deploy_token_for_deployment(request: HttpRequest, deployment_guid: str):
    """Verify the deploy token and confirm the deployment belongs to
    the token's app.

    Returns ``(deploy_token_row, deployment_row, None)`` on success, or
    ``(None, None, JsonResponse)`` on error.
    """
    from astrolift_lifecycle.models import Deployment

    bearer = _bearer_token(request)
    if not bearer or not bearer.startswith(PLAINTEXT_PREFIX):
        return (
            None,
            None,
            JsonResponse(
                {"detail": "deploy token required"},
                status=401,
            ),
        )

    token = verify_token(bearer)
    if token is None:
        return None, None, JsonResponse({"detail": "invalid or expired deploy token"}, status=401)

    deployment = (
        Deployment.all_objects.select_related("registered_app", "app_environment")
        .filter(guid=deployment_guid, deleted_at__isnull=True)
        .first()
    )
    if deployment is None:
        return None, None, JsonResponse({"detail": "deployment not found"}, status=404)

    # Scope: the token's app must own this deployment.
    if deployment.registered_app_id != token.registered_app_id:
        return None, None, JsonResponse({"detail": "deployment not found"}, status=404)

    return token, deployment, None


# ── views ───────────────────────────────────────────────────────────────────


def _deploy_workflow_id(app_guid: str, env_guid: str) -> str:
    """Match the canonical workflow ID from ``start_deployment`` mutation."""
    return f"DeployAppWorkflow-{app_guid}-{env_guid}"


@require_http_methods(["POST"])
def ci_deploy(request: HttpRequest, app_slug: str) -> JsonResponse:
    """POST /api/cli/v1/apps/<app_slug>/deploy/

    Validate the CI deploy request, create a Deployment row, start
    DeployAppWorkflow, and return the deployment ID + polling URL.
    """
    from django.db import transaction

    from astrolift_lifecycle.models import AppEnvironment, Deployment
    from astrolift_manifest.parser import ManifestError, parse_raw
    from astrolift_operations.models import WorkflowRun  # noqa: PLC0415
    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.client import start_workflow
    from astrolift_workflows.inputs import Actor, DeployAppInput

    token, err = _resolve_deploy_token(request, app_slug)
    if err is not None:
        return err

    # ── parse request body ──────────────────────────────────────────
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"detail": f"invalid JSON: {exc}"}, status=400)

    # Load the app so we can derive declared workloads + environments.
    app = (
        RegisteredApp.objects.filter(slug=app_slug, deleted_at__isnull=True)
        .select_related("organization", "approver_team")
        .first()
    )
    if app is None:
        return JsonResponse({"detail": f"app {app_slug!r} not found"}, status=404)

    # Derive declared workloads from the stored manifest for tag validation.
    declared_workloads: tuple[str, ...] = ()
    if app.manifest_raw and app.manifest_raw.strip():
        try:
            parsed_manifest = parse_raw(app.manifest_raw)
            declared_workloads = tuple(w.name for w in parsed_manifest.workloads)
        except ManifestError:
            # Malformed manifest — proceed; validate_image_tags will
            # allow any workload slug and the deploy pre-flight will
            # catch the real error.
            declared_workloads = ()

    # If there's no declared workload list (missing or unparseable manifest),
    # relax the workload-tag validation so CI can still deliver (the deploy
    # pre-flight will catch the real manifest error).
    if not declared_workloads:
        declared_workloads = tuple(body.get("image_tags", {}).keys())

    registered_envs: tuple[str, ...] = tuple(
        AppEnvironment.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
        ).values_list("name", flat=True)
    )

    try:
        ci_req = parse_request(
            app_slug=app_slug,
            raw_body=body,
            declared_workloads=declared_workloads,
            registered_envs=registered_envs,
        )
    except CiDeployError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    # ── resolve environment ─────────────────────────────────────────
    env = (
        AppEnvironment.objects.filter(
            registered_app=app,
            name=ci_req.environment,
            deleted_at__isnull=True,
        )
        .select_related("tenant_cluster")
        .first()
    )
    if env is None:
        return JsonResponse(
            {"detail": f"environment {ci_req.environment!r} not found"},
            status=400,
        )

    if env.deploys_paused:
        return JsonResponse(
            {"detail": f"environment {env.name!r} has deploys paused"},
            status=409,
        )

    # app.webhook_deploys_paused applies to webhook-shaped triggers
    _WEBHOOK_TRIGGERS = {"ci", "scheduled", "webhook_scm"}
    if app.webhook_deploys_paused and ci_req.trigger_kind.value in _WEBHOOK_TRIGGERS:
        reason = (app.webhook_deploys_pause_reason or "").strip()
        suffix = f" Reason: {reason}" if reason else ""
        return JsonResponse(
            {"detail": f"app {app_slug!r} has webhook-fired deploys paused.{suffix}"},
            status=409,
        )

    # ── pick a representative image_tag for the Deployment row ──────
    # For multi-workload apps image_tags is a dict; Deployment.image_tag
    # holds the primary one. Store all tags on the image_tags JSON field
    # when it exists; fall back to the first value.
    primary_tag = next(iter(ci_req.image_tags.values()))

    # ── create Deployment + start workflow ──────────────────────────
    actor = Actor(kind="deploy_token", user_id=None)

    with transaction.atomic():
        deployment = Deployment.objects.create(
            registered_app=app,
            app_environment=env,
            triggered_by_user_id=None,
            triggered_by_token_kind="deploy_token",
            triggered_by_token_id=token.pk,
            trigger_kind=ci_req.trigger_kind.value,
            strategy="rolling",
            status=Deployment.Status.PENDING.value,
            image_tag=primary_tag,
            image_digest="",
            approvals_required=0,
            approvals_received=0,
            approval_token_hash="",
            commit_sha=ci_req.commit_sha,
            branch=ci_req.branch,
            ci_actor_kind="deploy_token",
        )

        wf_id = _deploy_workflow_id(str(app.guid), str(env.guid))
        handle = start_workflow(
            "DeployAppWorkflow",
            args=[
                DeployAppInput(
                    registered_app_id=app.pk,
                    app_environment_id=env.pk,
                    deployment_id=deployment.pk,
                    image_tags=ci_req.image_tags,
                    trigger_kind=ci_req.trigger_kind.value,
                    actor=actor,
                )
            ],
            workflow_id=wf_id,
        )
        if handle.enqueued:
            run = WorkflowRun.objects.create(
                workflow_kind="DeployAppWorkflow",
                workflow_id=handle.workflow_id,
                run_id=handle.run_id or "",
                status=WorkflowRun.Status.RUNNING,
                started_at=timezone.now(),
                organization_id=app.organization_id,
                registered_app_id=app.pk,
                app_environment_id=env.pk,
                trigger_actor_user_id=None,
                trigger_actor_token_kind="deploy_token",
                trigger_actor_token_id=token.pk,
            )
            deployment.workflow_run = run
            deployment.save(update_fields=["workflow_run", "updated_at", "version"])

    log.info(
        "ci_deploy: app=%s env=%s deployment=%s wf=%s",
        app_slug,
        ci_req.environment,
        str(deployment.guid),
        wf_id,
    )

    polling_url = f"/api/cli/v1/deployments/{deployment.guid}/status/"
    return JsonResponse(
        {
            "deployment_id": str(deployment.guid),
            # workflow_id is what the CLI logs as "Deploy enqueued: <id>"
            "workflow_id": wf_id,
            "workflow_run_id": handle.run_id if handle.enqueued else "",
            "polling_url": polling_url,
            "status": deployment.status,
        },
        status=201,
    )


# Map Deployment.Status → CLI terminal state vocabulary.
# The CLI polls for "state" and checks terminal set
# {succeeded, completed, failed, cancelled, timed_out}.
_STATUS_TO_CLI_STATE: dict[str, str] = {
    "pending": "pending",
    "pending_approval": "pending_approval",
    "deploying": "deploying",
    "running": "succeeded",  # terminal success
    "failed": "failed",  # terminal failure
    "aborted": "cancelled",  # terminal — aborted by signal
}


@require_http_methods(["GET"])
def deployment_status(request: HttpRequest, guid: str) -> JsonResponse:
    """GET /api/cli/v1/deployments/<guid>/status/

    Return the current status of a deployment. The caller (``astro ci deploy``
    in ``--wait`` mode) polls this every 5s until the status is terminal.

    The ``state`` field uses the CLI's terminal vocabulary:
      * ``succeeded``  — deployment reached ``running`` status.
      * ``failed``     — deployment failed.
      * ``cancelled``  — deployment was aborted.
      * ``pending`` / ``deploying`` — in-progress (not terminal).

    The ``status`` field preserves the raw Deployment.Status value for
    callers that need the platform-native vocabulary.
    """
    token, deployment, err = _resolve_deploy_token_for_deployment(request, guid)
    if err is not None:
        return err

    raw_status = deployment.status
    cli_state = _STATUS_TO_CLI_STATE.get(raw_status, raw_status)

    return JsonResponse(
        {
            "deployment_id": str(deployment.guid),
            "state": cli_state,  # CLI polls this field for terminal detection
            "status": raw_status,  # platform-native value preserved
            "image_tag": deployment.image_tag,
            "commit_sha": deployment.commit_sha,
            "branch": deployment.branch,
            "created_at": deployment.created_at.isoformat() if deployment.created_at else None,
            "updated_at": deployment.updated_at.isoformat() if deployment.updated_at else None,
            "environment": deployment.app_environment.name if deployment.app_environment_id else "",
        }
    )
