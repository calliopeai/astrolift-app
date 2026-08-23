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

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger
from astrolift_pipelines.webhook_security import (
    WebhookSecurityError,
    check_payload_size,
    check_replay,
    check_webhook_rate_limit,
)

logger = logging.getLogger(__name__)

# Events we handle; everything else gets a 200 "not handled".
_HANDLED_EVENTS = {"push", "pull_request"}


def verify_signature(secret: bytes, body: bytes, signature_header: str) -> bool:
    """Verify the ``X-Hub-Signature-256`` HMAC.

    A thin delegation to ``webhook_security.verify_github_hmac``, which had
    the identical logic and no caller. Kept as a local function with this
    name on purpose: ``core/tests/test_webhook_signature_guard.py`` requires
    every webhook view to call a verifier from a small allowlist of names,
    and it descends only one level and only within the same module. A
    cross-module call would not satisfy it, so removing this wrapper would
    trade a real duplicate for a failed security gate.
    """
    from astrolift_pipelines.webhook_security import (
        WebhookSecurityError,
        verify_github_hmac,
    )

    try:
        verify_github_hmac(secret, body, signature_header)
    except WebhookSecurityError:
        return False
    return True


def _get_org_pipeline_secret(org: Organization) -> bytes | None:
    """Return the org's GitHub webhook HMAC secret, or None if unset.

    Reads the encrypted secret off the org's `SourceConnection`, the same
    column `astrolift_scm/webhook_views.py` and `auth1/scm_webhook.py` read.
    See `webhook_security.org_webhook_secret` for why this replaced a lookup
    through a module that does not exist.
    """
    from astrolift_pipelines.webhook_security import org_webhook_secret

    return org_webhook_secret(org, source_kind="github")


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
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    # Payload size first, before spending CPU on an HMAC over a body that
    # is already too large to be a real GitHub delivery.
    try:
        check_payload_size(body)
    except WebhookSecurityError as exc:
        logger.warning("pipelines.webhook: oversized payload for org %s: %s", org_slug, exc)
        _record_webhook(org_slug, "rejected")
        return JsonResponse({"error": "payload too large"}, status=413)

    # Signature verification — required for all events
    secret = _get_org_pipeline_secret(org)
    if not secret:
        logger.warning("pipelines.webhook: org %s has no pipeline webhook secret", org_slug)
        _record_webhook(org_slug, "signature_invalid", signature_failure=True)
        return JsonResponse({"error": "invalid signature"}, status=401)

    if not verify_signature(secret, body, signature):
        logger.warning("pipelines.webhook: signature mismatch for org %s", org_slug)
        # The counter this feeds is the one worth alerting on: a spike is
        # either a rotated secret nobody updated or someone probing the
        # endpoint, and until now it had no producer at all.
        _record_webhook(org_slug, "signature_invalid", signature_failure=True)
        return JsonResponse({"error": "invalid signature"}, status=401)

    # Rate limit only *after* the signature passes. `org_slug` comes from
    # the URL, so limiting before verification would let anyone who knows
    # an org's slug spend its budget and lock out its real webhooks.
    try:
        check_webhook_rate_limit(org_slug)
    except WebhookSecurityError as exc:
        logger.warning("pipelines.webhook: rate limited org %s: %s", org_slug, exc)
        _record_webhook(org_slug, "rate_limited")
        return JsonResponse({"error": "rate limited"}, status=429)

    # Replay protection. A duplicate returns 200: the delivery was already
    # processed, and a non-2xx would make GitHub retry it forever.
    try:
        check_replay(delivery_id, org_slug)
    except WebhookSecurityError as exc:
        logger.info("pipelines.webhook: duplicate delivery for org %s: %s", org_slug, exc)
        _record_webhook(org_slug, "replay")
        return JsonResponse({"status": "duplicate", "delivery": delivery_id})

    # Parse payload
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        return JsonResponse({"error": "bad request", "detail": str(exc)}, status=400)

    # Filter to handled events
    if event not in _HANDLED_EVENTS:
        _record_webhook(org_slug, "filtered")
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

    _record_webhook(org_slug, "dispatched" if dispatched else "filtered")
    return JsonResponse({"status": "ok", "dispatched": dispatched})


def _record_webhook(
    org_slug: str,
    outcome: str,
    *,
    provider: str = "github",
    signature_failure: bool = False,
) -> None:
    """Count a webhook outcome (#98).

    ``astrolift_pipelines/metrics.py`` declared both of these counters and
    nothing incremented either, so an operator could not see webhook volume
    or a signature-failure spike at all.

    ``filtered`` covers both an unhandled event type and a handled one that
    matched no pipeline: neither dispatched anything, and the distinction is
    already in the response body. Swallows, because a metrics failure must
    not turn a webhook into a 500 and make the host retry it.
    """
    try:
        from astrolift_pipelines.metrics import (
            record_webhook_delivery,
            record_webhook_signature_failure,
        )

        record_webhook_delivery(org_slug, provider, outcome)
        if signature_failure:
            record_webhook_signature_failure(org_slug, provider)
    except Exception:  # noqa: BLE001
        logger.warning("pipeline metrics: webhook outcome %s not recorded", outcome, exc_info=True)


def _next_run_number(pipeline: Pipeline) -> int:
    """Return the next monotonic run number for a pipeline."""
    from django.db.models import Max

    result = PipelineRun.objects.filter(pipeline=pipeline).aggregate(Max("run_number"))
    current = result["run_number__max"] or 0
    return current + 1


def _dispatch_pipeline_run(run: PipelineRun) -> None:
    """Start PipelineRunWorkflow via the Temporal client.

    Five things were wrong here and each on its own was fatal, so no webhook
    has ever started a pipeline (#1614):

    * ``astrolift_workflows.inputs`` has no ``PipelineRunInput``. That raised
      ImportError on the first line of the ``try``, which is why the other
      four were never reached and never surfaced.
    * ``start_workflow`` takes ``args`` as a *list*, not a single value.
    * Its keyword is ``workflow_id``, not ``id``.
    * ``PipelineRunWorkflow.run`` takes the integer PK, not a GUID string.
    * ``task_queue="pipelines"`` names no queue anybody registers; the
      workflow is in the single ``WORKFLOWS`` tuple served on
      ``TEMPORAL_TASK_QUEUE``, so the default is the correct one.

    The write of ``temporal_workflow_id`` was inside the same ``try``, which
    made this worse than a dead dispatch: the field stayed empty, and
    ``astrolift_pipelines.cancellation._signal_temporal_cancel`` returns
    early on an empty one. So cancelling a pipeline run could not work
    either, for a second and independent reason, even after that function's
    own signature bug was fixed.

    Still best-effort -- Temporal is genuinely optional on dev/test installs
    -- but the failure is logged with its exception now. A bare
    "dispatch skipped" is indistinguishable from "Temporal is off", and that
    is precisely how the five above survived.
    """
    from astrolift_workflows.client import start_workflow

    workflow_id = f"pipeline-run-{run.pipeline_id}-{run.run_number}"
    try:
        start_workflow(
            "PipelineRunWorkflow",
            [run.pk],
            workflow_id=workflow_id,
        )
    except Exception:
        logger.exception("pipelines.webhook: Temporal dispatch failed for run %s", run.guid)
        return

    # Outside the try: a dispatch that succeeded must be recorded even if
    # this save were to fail, and a save failure is not a dispatch failure.
    run.temporal_workflow_id = workflow_id
    run.save(update_fields=["temporal_workflow_id", "updated_at", "version"])
