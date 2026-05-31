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

import hmac
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger
from astrolift_pipelines.webhook_views import (
    _next_run_number,
    _dispatch_pipeline_run,
    _repo_url_matches,
    _trigger_matches,
)

logger = logging.getLogger(__name__)

_HANDLED_GITLAB_EVENTS = {"Push Hook", "Tag Push Hook", "Merge Request Hook"}


def _get_org_pipeline_secret(org: Organization) -> str | None:
    """Return the pipeline webhook secret for an org, or None if unset."""
    extra = getattr(org, "extra_data", None) or {}
    dev_secret = extra.get("pipeline_webhook_secret")
    if dev_secret:
        return dev_secret if isinstance(dev_secret, str) else dev_secret.decode()

    try:
        from astrolift_lifecycle.services.secrets import read_org_secret
        value = read_org_secret(org, "astrolift/pipeline/webhook_secret")
        if value:
            return value if isinstance(value, str) else value.decode()
    except Exception:  # noqa: BLE001
        pass

    return None


def _verify_gitlab_token(secret: str, provided: str) -> bool:
    """Constant-time compare for GitLab's opaque token scheme."""
    return hmac.compare_digest(secret.encode(), provided.encode())


def _extract_gitlab_info(event: str, payload: dict) -> tuple[str, str, str, str]:
    """Extract (clone_url, http_url, ref, actor) from a GitLab payload."""
    project = payload.get("project") or {}
    clone_url = project.get("git_ssh_url", "")
    http_url = project.get("http_url", "")
    actor = payload.get("user_username", "")

    if event in ("Push Hook", "Tag Push Hook"):
        ref = payload.get("ref", "")  # already "refs/heads/..." or "refs/tags/..."
    elif event == "Merge Request Hook":
        attrs = payload.get("object_attributes") or {}
        ref = attrs.get("source_branch", "")  # bare branch name, not refs/heads/
    else:
        ref = ""

    return clone_url, http_url, ref, actor


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

    # Token verification
    secret = _get_org_pipeline_secret(org)
    if not secret:
        logger.warning("pipelines.gitlab_webhook: org %s has no pipeline webhook secret", org_slug)
        return JsonResponse({"error": "invalid token"}, status=403)

    if not _verify_gitlab_token(secret, gitlab_token):
        logger.warning("pipelines.gitlab_webhook: token mismatch for org %s", org_slug)
        return JsonResponse({"error": "invalid token"}, status=403)

    # Parse payload
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        return JsonResponse({"error": "bad request", "detail": str(exc)}, status=400)

    if event not in _HANDLED_GITLAB_EVENTS:
        return JsonResponse({"status": "not handled", "event": event})

    canonical_kind = _gitlab_event_to_canonical(event, payload)
    if canonical_kind is None:
        return JsonResponse({"status": "not handled", "event": event, "reason": "action ignored"})

    clone_url, http_url, ref, actor = _extract_gitlab_info(event, payload)

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
            if not _trigger_matches(trigger, canonical_kind, ref):
                continue
            try:
                run = PipelineRun.objects.create(
                    pipeline=pipeline,
                    run_number=_next_run_number(pipeline),
                    trigger_kind=canonical_kind,
                    trigger_ref=ref,
                    trigger_actor=actor,
                    status="pending",
                    temporal_workflow_id="",
                )
                _dispatch_pipeline_run(run)
                dispatched.append({"pipeline": pipeline.name, "run_number": run.run_number})
            except Exception:  # noqa: BLE001
                logger.exception("pipelines.gitlab_webhook: failed to dispatch pipeline %s", pipeline.name)
            break

    return JsonResponse({"status": "ok", "dispatched": dispatched})
