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

Deployment prerequisite (edge SSO bypass)
-----------------------------------------
These endpoints authenticate with a **deploy-token bearer**, not an SSO
session. For an external CI caller to reach this view, the install's edge
(load balancer / ingress SSO authenticate action) MUST bypass its SSO
challenge for the ``/api/cli/v1/*`` prefix when a bearer is presented —
the same bypass the GraphQL API path (``/app/*``) already has. Without
that rule the edge answers the request itself with a ``302`` redirect to
the SSO login page and the bearer never reaches Django, so ``astro ci
deploy`` cannot trigger a deploy regardless of how correct the token is.
The sibling builder surface (``/api/builder/v1/*``) needs the same bypass.
Verified end-to-end only once that edge rule is in place (#977).

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
      "workflow_id":     "DeployAppWorkflow-<app-guid>-<env-guid>",  # CLI logs this
      "workflow_run_id": "<temporal-run-id>",   # or "" if already in-flight
      "polling_url":     "/api/cli/v1/deployments/<guid>/status/",
      "status":          "pending"
    }
  400 for validation errors (CiDeployError), 401 for bad/missing token,
  403 for token/app mismatch or scope error.

  ``workflow_id`` is the SAME single-flight id the GraphQL ``startDeployment``
  resolver uses (``DeployAppWorkflow-<app-guid>-<env-guid>``), so a CI deploy
  and a UI deploy to the same (app, env) collide on one DeployAppWorkflow.

``GET /api/cli/v1/deployments/<guid>/status/``
  200 with:
    {
      "deployment_id": "<guid>",
      "state":         "running",         # CLI-facing; polled for terminal detection
      "status":        "running",         # platform-native (pending|deploying|running|failed|aborted)
      "image_tag":     "abc1234",
      "commit_sha":    "<sha>",
      "branch":        "main",
      "created_at":    "2026-01-01T00:00:00Z",
      "updated_at":    "2026-01-01T00:05:00Z",
      "environment":   "production"
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
    # The deploy token is already bound to its app (verified in
    # _resolve_deploy_token), so use that row directly rather than
    # re-fetching by slug — slugs are unique only within an org, so a
    # sibling org's app sharing the slug could otherwise be picked (#1183).
    app = token.registered_app
    if app.deleted_at is not None:
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
                    commit_sha=deployment.commit_sha,
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


# ── static-site asset upload (CI_PUSHED mode, #1010) ─────────────────────────


class _BundleError(Exception):
    """Malformed upload bundle -- surfaced as a 400."""


def _s3_client(region: str):
    """Control-plane S3 client. The control plane authenticates via its own
    role (ECS task role) -- NOT the per-app IRSA build role (that one is
    in-cluster only). Factored out so tests can inject a recording fake."""
    import boto3

    return boto3.client("s3", region_name=region or None)


def _safe_join(root: str, member: str) -> str:
    """Resolve an archive member path under ``root``, rejecting traversal
    (zip-slip / tar ``..``)."""
    import os

    dest = os.path.realpath(os.path.join(root, member))
    if dest != os.path.realpath(root) and not dest.startswith(os.path.realpath(root) + os.sep):
        raise _BundleError(f"unsafe path in bundle: {member!r}")
    return dest


def _extract_bundle(request: HttpRequest) -> str:
    """Extract the uploaded asset bundle to a fresh temp dir, return its path.

    Accepts a multipart ``bundle`` file (``.tar.gz`` or ``.zip``) or a raw
    gzipped-tar request body. Guards against path traversal."""
    import io
    import os
    import shutil
    import tarfile
    import tempfile
    import zipfile

    upload = request.FILES.get("bundle")
    if upload is not None:
        raw = upload.read()
        filename = upload.name or ""
    else:
        raw = bytes(request.body or b"")
        filename = ""
    if not raw:
        raise _BundleError("empty bundle; send a 'bundle' file or a gzipped tar body")

    dest = tempfile.mkdtemp(prefix="astrolift-static-")
    bio = io.BytesIO(raw)
    try:
        if filename.endswith(".zip") or raw[:2] == b"PK":
            with zipfile.ZipFile(bio) as zf:
                for member in zf.namelist():
                    target = _safe_join(dest, member)
                    if member.endswith("/"):
                        os.makedirs(target, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
            return dest
        with tarfile.open(fileobj=bio, mode="r:*") as tf:
            for member in tf.getmembers():
                _safe_join(dest, member.name)  # reject absolute / .. names
            # ``filter="data"`` (py3.12) rejects absolute paths, ``..`` escapes,
            # AND symlink/hardlink members whose target leaves the dest -- the
            # name-only check above cannot catch a symlink-then-write-through
            # traversal, so the filter is the real guard.
            tf.extractall(dest, filter="data")  # noqa: S202
        return dest
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise _BundleError(f"malformed bundle (expected tar.gz or zip): {exc}") from exc


def _sync_dir_to_s3(root: str, bucket: str, prefix: str, region: str) -> int:
    """Upload every file under ``root`` to ``s3://bucket/prefix`` and delete
    any object under that prefix not in the upload set (mirrors ``aws s3 sync
    --delete``). Returns the number of objects uploaded."""
    import mimetypes
    import os

    s3 = _s3_client(region)
    base = prefix.strip("/")
    uploaded: set[str] = set()
    count = 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            key = f"{base}/{rel}" if base else rel
            ctype = mimetypes.guess_type(fn)[0] or "application/octet-stream"
            with open(full, "rb") as fh:
                s3.put_object(Bucket=bucket, Key=key, Body=fh.read(), ContentType=ctype)
            uploaded.add(key)
            count += 1

    # Delete extras under the prefix so a removed file disappears from the site.
    paginator = s3.get_paginator("list_objects_v2")
    stale: list[dict[str, str]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=base):
        for obj in page.get("Contents", []) or []:
            if obj["Key"] not in uploaded:
                stale.append({"Key": obj["Key"]})
    for i in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": stale[i : i + 1000]})
    return count


@require_http_methods(["POST"])
def ci_static_upload(request: HttpRequest, app_slug: str, workload: str) -> JsonResponse:
    """POST /api/cli/v1/apps/<app_slug>/static/<workload>/upload/

    CI_PUSHED mode: sync a prebuilt static-asset bundle to the workload's
    origin bucket and bust the CloudFront cache. The in-cluster
    ``sync_static_assets`` activity stays a no-op for this workload so a
    deploy doesn't clobber CI-pushed assets."""
    from aws.managed._base import parse_handle

    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.models import ManagedService
    from astrolift_workflows.activities.static_site import _service_names

    token, err = _resolve_deploy_token(request, app_slug)
    if err is not None:
        return err

    app = token.registered_app

    # Static rows are env-scoped -- one bucket + distribution per (app, env) --
    # so an upload must target a specific environment. Default to the app's
    # sole env; require an ``environment`` field when the app has several so a
    # CI push never silently lands on the wrong env's bucket.
    env_name = (request.POST.get("environment") or request.GET.get("environment") or "").strip()
    envs = list(AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True))
    if env_name:
        env = next((e for e in envs if e.name == env_name), None)
        if env is None:
            return JsonResponse({"detail": f"environment {env_name!r} not found"}, status=404)
    elif len(envs) == 1:
        env = envs[0]
    else:
        return JsonResponse(
            {"detail": "app has multiple environments; specify the 'environment' field"},
            status=400,
        )

    assets_name, cdn_name = _service_names(workload, env)
    assets = (
        ManagedService.objects.filter(registered_app=app, kind="object_store", name=assets_name)
        .select_related("app_environment__tenant_cluster__provider_plugin")
        .first()
    )
    cdn = ManagedService.objects.filter(registered_app=app, kind="cdn", name=cdn_name).first()
    active = ManagedService.Status.ACTIVE
    if (
        assets is None
        or cdn is None
        or assets.status != active
        or cdn.status != active
        or not assets.backend_ref
        or not cdn.backend_ref
    ):
        return JsonResponse(
            {"detail": "static services not provisioned; deploy once first"},
            status=409,
        )

    bucket = parse_handle(assets.backend_ref)[1]
    distribution_id = parse_handle(cdn.backend_ref)[1]
    prefix = (request.POST.get("prefix", "") or "").strip()

    try:
        extracted = _extract_bundle(request)
    except _BundleError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    import shutil

    from astrolift_workflows.activities.static_site import _cdn_driver, _region_account

    cluster = assets.app_environment.tenant_cluster if assets.app_environment_id else None
    region = _region_account(cluster)[0] if cluster is not None else ""
    try:
        objects = _sync_dir_to_s3(extracted, bucket, prefix, region)
    finally:
        shutil.rmtree(extracted, ignore_errors=True)

    invalidation_id = ""
    if cluster is not None:
        try:
            invalidation_id = (
                _cdn_driver(cluster, cdn).invalidate(distribution_id, ["/*"]).get("invalidation_id", "")
            )
        except Exception as exc:  # noqa: BLE001 -- invalidation is best-effort
            log.info("ci_static_upload: invalidate skipped for %s/%s (%s)", app_slug, workload, exc)

    log.info("ci_static_upload: app=%s workload=%s bucket=%s objects=%d", app_slug, workload, bucket, objects)
    return JsonResponse(
        {"ok": True, "bucket": bucket, "objects": objects, "invalidation_id": invalidation_id}
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
