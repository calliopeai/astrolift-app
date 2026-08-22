"""GitHub webhook receiver for the Astrolift Pipelines feature (#72).

POST /webhooks/pipelines/github/<org_slug>/

Registered per Astrolift organization when the operator calls
``installPipelineWebhook``. GitHub posts all repo events for the org
to this URL. This receiver:

1. Looks up the Organization by ``org_slug``.
2. Verifies the HMAC-SHA256 signature (``X-Hub-Signature-256``) against
   the per-org pipeline webhook secret stored in Astrolift's secret store.
3. Classifies the event (push / pull_request).
4. Finds matching Pipeline records for the repo URL in the payload.
5. For each matching Pipeline + Trigger pair: creates a PipelineRun and
   dispatches PipelineRunWorkflow via the sync Temporal client.

Wire-protocol contract
----------------------
* 401 on HMAC failure — generic body so an attacker can't probe the surface.
* 404 when org_slug doesn't resolve.
* 400 on malformed JSON.
* 200 for everything else (ignored events, no matching pipelines, successful dispatch).

GitHub retries on non-2xx; keeping responses in the 2xx band ensures the
retry queue stays empty for events we intentionally don't handle.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger

logger = logging.getLogger(__name__)

# Events we handle; everything else gets a 200 "not handled".
_HANDLED_EVENTS = {"push", "pull_request"}


def verify_signature(secret: bytes, body: bytes, signature_header: str) -> bool:
    """Verify GitHub's HMAC-SHA256 payload signature.

    GitHub sends: ``X-Hub-Signature-256: sha256=<hex>``

    Named ``verify_signature`` so the ``test_webhook_signature_guard`` CI
    guard (#529) recognizes it as the HMAC trust boundary on
    ``pipeline_github_webhook``. Returns False on a missing / malformed /
    mismatched signature; the caller turns False into a generic 401.
    """
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _get_org_pipeline_secret(org: Organization) -> bytes | None:
    """Return the pipeline webhook secret for an org, or None if unset.

    The secret is stored in Astrolift's secret store under the key
    ``astrolift/pipeline/webhook_secret``. This falls back to
    ``PIPELINE_WEBHOOK_SECRET`` on the org's ``settings`` dict when
    the secret store isn't wired (dev installs, tests).
    """
    # Dev/test fallback — check org.extra_data for a plaintext secret.
    extra = getattr(org, "extra_data", None) or {}
    dev_secret = extra.get("pipeline_webhook_secret")
    if dev_secret:
        return dev_secret.encode() if isinstance(dev_secret, str) else dev_secret

    # Production path: read from the installed secrets backend.
    # The secret is written by installPipelineWebhook at setup time.
    try:
        from astrolift_lifecycle.services.secrets import read_org_secret

        value = read_org_secret(org, "astrolift/pipeline/webhook_secret")
        if value:
            return value.encode() if isinstance(value, str) else value
    except Exception:  # noqa: BLE001 — degraded path, don't blow up the receiver
        pass

    return None


def _repo_url_matches(pipeline_url: str, payload_clone_url: str, payload_html_url: str) -> bool:
    """Fuzzy-match a pipeline's configured repo URL against the webhook payload's URLs.

    Handles HTTPS vs SSH, trailing slashes, and .git suffix differences.
    """

    def normalize(url: str) -> str:
        url = url.lower().rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        return url

    norm = normalize(pipeline_url)
    return norm in {normalize(payload_clone_url), normalize(payload_html_url)}


def _extract_repo_info(event: str, payload: dict) -> tuple[str, str, str, str, str]:
    """Extract (repo_clone_url, repo_html_url, ref, actor, commit_sha)."""
    repo = payload.get("repository") or {}
    clone_url = repo.get("clone_url", "")
    html_url = repo.get("html_url", "")
    actor = (payload.get("sender") or {}).get("login", "")

    commit_sha = ""
    if event == "push":
        ref = payload.get("ref", "")  # e.g. "refs/heads/main" or "refs/tags/v1.0"
        # The commit the push landed on. A ref moves; this does not, and it
        # is what a run is actually of (#1531).
        commit_sha = str(payload.get("after") or "")
    elif event == "pull_request":
        pr = payload.get("pull_request") or {}
        head = pr.get("head") or {}
        ref = head.get("ref", "")  # branch name
        commit_sha = str(head.get("sha") or "")
    else:
        ref = ""

    return clone_url, html_url, ref, actor, commit_sha


def _trigger_matches(trigger: Trigger, event: str, ref: str, payload: dict | None = None):
    """Apply a Trigger's filters to an incoming event.

    Returns a `TriggerFilterResult`, or None when the trigger is not for
    this event kind at all.

    The matching itself lives in `astrolift_pipelines.trigger_filters`,
    which had no caller. What used to be here did exact-string membership
    on branch names, so `branches = ["release/*"]` matched nothing and the
    pipeline looked broken for no visible reason; it ignored `paths`
    entirely, so a docs-only push triggered every pipeline in the org; and
    it never looked at whether a pull request came from a fork, which is
    the half that decides whether org secrets are handed to code from
    outside the org.

    The kind gate stays here because it is a property of the Trigger row
    rather than of its config, and the filter module only sees the config.
    """
    from astrolift_pipelines.trigger_filters import apply_trigger_filters

    kind_map = {
        "push": ("push",),
        "pull_request": ("pull_request",),
    }
    if trigger.kind not in kind_map.get(event, ()):
        return None

    return apply_trigger_filters(trigger.config or {}, event, ref, payload or {})


@require_http_methods(["POST"])
def pipeline_github_webhook(request: HttpRequest, org_slug: str) -> JsonResponse:
    """GitHub webhook receiver for pipeline CI triggers (#72)."""
    try:
        org = Organization.objects.get(slug=org_slug, deleted_at__isnull=True)
    except Organization.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)

    body = request.body
    event = request.headers.get("X-GitHub-Event", "")
    signature = request.headers.get("X-Hub-Signature-256", "")

    # Signature verification — required for all events
    secret = _get_org_pipeline_secret(org)
    if not secret:
        logger.warning("pipelines.webhook: org %s has no pipeline webhook secret", org_slug)
        return JsonResponse({"error": "invalid signature"}, status=401)

    if not verify_signature(secret, body, signature):
        logger.warning("pipelines.webhook: signature mismatch for org %s", org_slug)
        return JsonResponse({"error": "invalid signature"}, status=401)

    # Parse payload
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        return JsonResponse({"error": "bad request", "detail": str(exc)}, status=400)

    # Filter to handled events
    if event not in _HANDLED_EVENTS:
        return JsonResponse({"status": "not handled", "event": event})

    clone_url, html_url, ref, actor, commit_sha = _extract_repo_info(event, payload)

    # Find pipelines for this org whose repo URL matches the webhook
    pipelines = Pipeline.objects.filter(
        organization=org,
        deleted_at__isnull=True,
    )

    dispatched = []
    for pipeline in pipelines:
        if not _repo_url_matches(pipeline.repo_url, clone_url, html_url):
            continue

        # Check triggers
        triggers = Trigger.objects.filter(pipeline=pipeline, deleted_at__isnull=True)
        for trigger in triggers:
            decision = _trigger_matches(trigger, event, ref, payload)
            if decision is None or not decision.should_trigger:
                continue

            # Create PipelineRun and dispatch workflow
            try:
                run = PipelineRun.objects.create(
                    pipeline=pipeline,
                    run_number=_next_run_number(pipeline),
                    trigger_kind=event,
                    trigger_ref=ref,
                    commit_sha=commit_sha,
                    trigger_actor=actor,
                    # A fork's pull request runs code the org has not
                    # reviewed. Recorded on the run rather than recomputed
                    # at spawn time, because the payload is gone by then
                    # and the answer must not be able to differ.
                    skip_secrets=decision.skip_secrets,
                    status="pending",
                    temporal_workflow_id="",  # set by dispatcher on workflow start
                )
                _dispatch_pipeline_run(run)
                dispatched.append({"pipeline": pipeline.name, "run_number": run.run_number})
            except Exception:  # noqa: BLE001
                logger.exception("pipelines.webhook: failed to dispatch pipeline %s", pipeline.name)
            break  # One trigger match per pipeline is enough

    return JsonResponse({"status": "ok", "dispatched": dispatched})


def _next_run_number(pipeline: Pipeline) -> int:
    """Return the next monotonic run number for a pipeline."""
    from django.db.models import Max

    result = PipelineRun.objects.filter(pipeline=pipeline).aggregate(Max("run_number"))
    current = result["run_number__max"] or 0
    return current + 1


def _dispatch_pipeline_run(run: PipelineRun) -> None:
    """Start PipelineRunWorkflow via the Temporal client.

    No-ops gracefully when Temporal is disabled (dev/test installs).
    """
    try:
        from astrolift_workflows.client import start_workflow
        from astrolift_workflows.inputs import PipelineRunInput

        workflow_id = f"pipeline-run-{run.pipeline_id}-{run.run_number}"
        start_workflow(
            "PipelineRunWorkflow",
            PipelineRunInput(pipeline_run_id=str(run.guid)),
            id=workflow_id,
            task_queue="pipelines",
        )
        run.temporal_workflow_id = workflow_id
        run.save(update_fields=["temporal_workflow_id", "updated_at", "version"])
    except Exception:  # noqa: BLE001 — Temporal may not be enabled
        logger.info("pipelines.webhook: Temporal dispatch skipped for run %s", run.guid)
