"""GitLab webhook receiver for the Astrolift Pipelines feature (#73).

POST /webhooks/pipelines/gitlab/<org_slug>/

Parallel to the GitHub receiver (webhook_views.py) but adapted for
GitLab's payload shapes and authentication scheme.

GitLab authenticates via ``X-Gitlab-Token`` (opaque token, not HMAC) sent
with every push. The token value is compared with constant-time hmac.compare_digest
against the stored per-org secret to avoid timing attacks.

Supported events (``X-Gitlab-Event`` header):
  - ``Push Hook``        → push trigger
  - ``Tag Push Hook``    → push trigger (refs/tags/*)
  - ``Merge Request Hook`` (action open/update/reopen) → pull_request trigger
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
    check_webhook_rate_limit,
)
from astrolift_pipelines.webhook_views import (
    _dispatch_pipeline_run,
    _next_run_number,
    _record_webhook,
    _repo_url_matches,
    _trigger_matches,
)

logger = logging.getLogger(__name__)

_HANDLED_GITLAB_EVENTS = {"Push Hook", "Tag Push Hook", "Merge Request Hook"}


def _get_org_pipeline_secret(org: Organization) -> str | None:
    """Return the org's GitLab webhook token, or None if unset.

    GitLab presents the secret verbatim in `X-Gitlab-Token` rather than
    signing the body, so this is compared in constant time as a string --
    hence the decode. Scoped to gitlab connections: verifying a GitLab
    delivery against a GitHub connection's secret would be a cross-host
    confusion.
    """
    from astrolift_pipelines.webhook_security import org_webhook_secret

    secret = org_webhook_secret(org, source_kind="gitlab")
    return secret.decode("utf-8") if secret else None


def verify_signature(secret: str, provided: str) -> bool:
    """Verify the ``X-Gitlab-Token`` header.

    Delegates to ``webhook_security.verify_gitlab_token``. Kept as a local
    function with this name because
    ``core/tests/test_webhook_signature_guard.py`` requires each webhook
    view to call a verifier from a small allowlist, descending one level and
    only within the same module.
    """
    from astrolift_pipelines.webhook_security import (
        WebhookSecurityError,
        verify_gitlab_token,
    )

    try:
        verify_gitlab_token(secret, provided)
    except WebhookSecurityError:
        return False
    return True


def _extract_gitlab_info(event: str, payload: dict) -> tuple[str, str, str, str, str]:
    """Extract (clone_url, http_url, ref, actor, commit_sha) from a GitLab payload."""
    project = payload.get("project") or {}
    clone_url = project.get("git_ssh_url", "")
    http_url = project.get("http_url", "")
    actor = payload.get("user_username", "")

    commit_sha = ""
    if event in ("Push Hook", "Tag Push Hook"):
        ref = payload.get("ref", "")  # already "refs/heads/..." or "refs/tags/..."
        # GitLab calls it `checkout_sha`; `after` is also present and is the
        # same value for a normal push, but is all-zeroes on a branch delete.
        commit_sha = str(payload.get("checkout_sha") or "")
    elif event == "Merge Request Hook":
        attrs = payload.get("object_attributes") or {}
        ref = attrs.get("source_branch", "")  # bare branch name, not refs/heads/
        commit_sha = str((attrs.get("last_commit") or {}).get("id") or "")
    else:
        ref = ""

    return clone_url, http_url, ref, actor, commit_sha


def _gitlab_event_to_canonical(event: str, payload: dict) -> str | None:
    """Map GitLab event name to the canonical trigger kind (push | pull_request)."""
    if event in ("Push Hook", "Tag Push Hook"):
        return "push"
    if event == "Merge Request Hook":
        attrs = payload.get("object_attributes") or {}
        action = attrs.get("action", "")
        if action in ("open", "update", "reopen"):
            return "pull_request"
        return None  # closed / merged — skip
    return None


@require_http_methods(["POST"])
def pipeline_gitlab_webhook(request: HttpRequest, org_slug: str) -> JsonResponse:
    """GitLab webhook receiver for pipeline CI triggers (#73)."""
    try:
        org = Organization.objects.get(slug=org_slug, deleted_at__isnull=True)
    except Organization.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)

    body = request.body
    event = request.headers.get("X-Gitlab-Event", "")
    gitlab_token = request.headers.get("X-Gitlab-Token", "")

    try:
        check_payload_size(body)
    except WebhookSecurityError as exc:
        logger.warning("pipelines.gitlab_webhook: oversized payload for org %s: %s", org_slug, exc)
        _record_webhook(org_slug, "rejected", provider="gitlab")
        return JsonResponse({"error": "payload too large"}, status=413)

    # Token verification
    secret = _get_org_pipeline_secret(org)
    if not secret:
        logger.warning("pipelines.gitlab_webhook: org %s has no pipeline webhook secret", org_slug)
        _record_webhook(org_slug, "signature_invalid", provider="gitlab", signature_failure=True)
        return JsonResponse({"error": "invalid token"}, status=403)

    if not verify_signature(secret, gitlab_token):
        logger.warning("pipelines.gitlab_webhook: token mismatch for org %s", org_slug)
        _record_webhook(org_slug, "signature_invalid", provider="gitlab", signature_failure=True)
        return JsonResponse({"error": "invalid token"}, status=403)

    # After the token, for the same reason as the GitHub receiver: the org
    # slug is in the URL, so limiting before verification hands anyone a way
    # to exhaust a real org's budget.
    try:
        check_webhook_rate_limit(org_slug)
    except WebhookSecurityError as exc:
        logger.warning("pipelines.gitlab_webhook: rate limited org %s: %s", org_slug, exc)
        _record_webhook(org_slug, "rate_limited", provider="gitlab")
        return JsonResponse({"error": "rate limited"}, status=429)

    # No replay check: GitLab does not send a stable per-delivery id, so
    # there is nothing to deduplicate on. Stated rather than omitted, so the
    # asymmetry with the GitHub receiver reads as a decision.

    # Parse payload
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        return JsonResponse({"error": "bad request", "detail": str(exc)}, status=400)

    if event not in _HANDLED_GITLAB_EVENTS:
        _record_webhook(org_slug, "filtered", provider="gitlab")
        return JsonResponse({"status": "not handled", "event": event})

    canonical_kind = _gitlab_event_to_canonical(event, payload)
    if canonical_kind is None:
        _record_webhook(org_slug, "filtered", provider="gitlab")
        return JsonResponse({"status": "not handled", "event": event, "reason": "action ignored"})

    clone_url, http_url, ref, actor, commit_sha = _extract_gitlab_info(event, payload)

    # Normalize ref for push events (GitLab always sends full ref)
    if canonical_kind == "push" and not ref.startswith("refs/"):
        ref = f"refs/heads/{ref}"

    pipelines = Pipeline.objects.filter(organization=org, deleted_at__isnull=True)

    dispatched = []
    for pipeline in pipelines:
        if not _repo_url_matches(pipeline.repo_url, clone_url, http_url):
            continue

        triggers = Trigger.objects.filter(pipeline=pipeline, deleted_at__isnull=True)
        for trigger in triggers:
            # Remap trigger kind for gitlab MR → pull_request matching
            decision = _trigger_matches(trigger, canonical_kind, ref, payload)
            if decision is None or not decision.should_trigger:
                continue
            try:
                run = PipelineRun.objects.create(
                    pipeline=pipeline,
                    run_number=_next_run_number(pipeline),
                    trigger_kind=canonical_kind,
                    trigger_ref=ref,
                    commit_sha=commit_sha,
                    trigger_actor=actor,
                    status="pending",
                    temporal_workflow_id="",
                )
                _dispatch_pipeline_run(run)
                dispatched.append({"pipeline": pipeline.name, "run_number": run.run_number})
            except Exception:  # noqa: BLE001
                logger.exception("pipelines.gitlab_webhook: failed to dispatch pipeline %s", pipeline.name)
            break

    # Same convention as the GitHub receiver: `filtered` covers a delivery
    # that matched no pipeline, since nothing was dispatched either way.
    _record_webhook(org_slug, "dispatched" if dispatched else "filtered", provider="gitlab")
    return JsonResponse({"status": "ok", "dispatched": dispatched})
