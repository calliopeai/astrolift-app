"""In-cluster agent heartbeat ingest endpoint (#808).

Called by the lightweight keep-alive agent running as a Deployment in
the cluster's ``astrolift-system`` namespace — not by browser clients.

Auth: ``Authorization: Bearer <agent_key>``. The agent key is scoped to
exactly one ``TenantCluster`` (issued once via the
``issueClusterAgentKey`` GraphQL mutation; only its SHA-256 persists).
This mirrors the proven scoped-Bearer-key pattern already used by the
Dispatch Service (``astrolift_dispatch/views.py``) and the self-hosted
runner (``astrolift_pipelines/runner_views.py``).

Endpoint:
    POST /api/clusters/v1/<cluster_guid>/heartbeat/

Request body (JSON, all fields optional — the agent version dictates
how rich the snapshot is):
    {
        "node_count":        <int>,
        "pods_by_namespace": {"astrolift-system": 3, "acme-prod": 12},
        "cpu_utilization":   <float 0..1>,
        "memory_utilization":<float 0..1>,
        "ingress_ips":       ["203.0.113.10"],
        "agent_version":     "0.1.0"
    }

Response 200:
    {"ok": true, "interval_seconds": 30}
        — the configured cadence so the agent can self-tune.
    A pending ``testModelEndpoint`` job (#2064) rides along as an
    optional ``test_job`` key -- the heartbeat is the only channel the
    control plane has to reach the agent, so a test-prompt job waits
    for the agent's own next pulse rather than opening a new one:
    {"ok": true, "interval_seconds": 30, "test_job": {
        "job_id": "...", "base_url": "http://svc.ns.svc.cluster.local:8000/v1",
        "model": "...", "prompt": "...", "max_tokens": 128,
        "timeout_seconds": 20, "secret_namespace": "...",
        "secret_name": "...", "secret_key": "api_key"
    }}
    Deliberately no result-callback URL here: the agent derives where to
    report the outcome from its own configured heartbeat endpoint, never
    from anything this response carries -- a compromised or buggy
    response naming an arbitrary URL would otherwise be a way to steer
    the agent's Bearer-authenticated result POST (and so its agent key)
    anywhere.
Response 401: missing / invalid agent key, or key not bound to the
    cluster in the URL.
Response 400: malformed JSON.

This module also carries the result callback for that same job
(``cluster_test_result``), authenticated the same way.
"""

from __future__ import annotations

import hashlib
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from astrolift_clusters import agent_test_jobs
from astrolift_clusters.models import TenantCluster

logger = logging.getLogger(__name__)


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of an agent key for constant-shape storage/lookup."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _bearer_token(request: HttpRequest) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer ") :].strip()
    return token or None


def _cluster_for_agent_key(request: HttpRequest, cluster_guid: str) -> TenantCluster | None:
    """Resolve + authenticate the cluster a Bearer agent key names.

    Shared by the heartbeat and test-result views: both bind the key hash
    to the ``cluster_guid`` in the URL, the same way, for the same reason
    (a leaked key for cluster A must never drive a request against
    cluster B's URL).
    """
    raw_key = _bearer_token(request)
    if raw_key is None:
        return None
    return TenantCluster.objects.filter(
        guid=cluster_guid,
        agent_key_hash=_hash_key(raw_key),
        deleted_at__isnull=True,
    ).first()


# Keys the agent may report. The agent version owns the shape; we
# allow-list to keep an over-eager or malicious agent from stuffing the
# row with arbitrary keys, but stay permissive within the allow-list so
# the snapshot can grow without a control-plane lockstep.
_ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "node_count",
        "node_ready_count",
        "pods_by_namespace",
        "app_readiness",
        "cpu_utilization",
        "memory_utilization",
        "ingress_ips",
        "agent_version",
    }
)


def _sanitize_payload(body: dict) -> dict:
    """Project the request body down to the allow-listed snapshot keys."""
    return {k: body[k] for k in _ALLOWED_PAYLOAD_KEYS if k in body}


@csrf_exempt
@require_http_methods(["POST"])
def cluster_heartbeat(request: HttpRequest, cluster_guid: str) -> JsonResponse:
    """Record one heartbeat pulse from a cluster's keep-alive agent.

    Updates ``last_heartbeat_at`` and ``last_heartbeat_payload`` on the
    cluster the agent key is scoped to. Returns the configured pulse
    interval so the agent can align its cadence with the control plane,
    plus (#2064) any test-prompt job waiting to be dispatched.
    """
    # Don't distinguish "no such cluster" from "wrong key" — both are 401
    # so a caller can't probe which cluster guids exist. Empty
    # agent_key_hash rows (no agent provisioned) never match because the
    # lookup hash is always 64 hex chars, never empty.
    cluster = _cluster_for_agent_key(request, cluster_guid)
    if cluster is None:
        return JsonResponse({"error": "unauthorized"}, status=401)

    try:
        body = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "body must be a JSON object"}, status=400)

    cluster.last_heartbeat_at = timezone.now()
    cluster.last_heartbeat_payload = _sanitize_payload(body)
    cluster.save(
        update_fields=[
            "last_heartbeat_at",
            "last_heartbeat_payload",
            "updated_at",
            "version",
        ]
    )

    logger.debug(
        "cluster.heartbeat: cluster=%s nodes=%s agent_version=%s",
        cluster.slug,
        body.get("node_count"),
        body.get("agent_version"),
    )

    response = {
        "ok": True,
        "interval_seconds": cluster.heartbeat_interval_seconds,
    }
    try:
        job = agent_test_jobs.dispatch_pending(str(cluster.guid))
    except agent_test_jobs.AgentTestUnavailable:
        logger.warning("cluster.model_test_dispatch_unavailable: cluster=%s", cluster.guid)
        job = None
    if job is not None:
        response["test_job"] = {
            "job_id": job["job_id"],
            "base_url": job["base_url"],
            "model": job["model"],
            "prompt": job["prompt"],
            "max_tokens": job["max_tokens"],
            "timeout_seconds": job["timeout_seconds"],
            "secret_namespace": job["secret_namespace"],
            "secret_name": job["secret_name"],
            "secret_key": job["secret_key"],
        }
    return JsonResponse(response)


@csrf_exempt
@require_http_methods(["POST"])
def cluster_test_result(request: HttpRequest, cluster_guid: str) -> JsonResponse:
    """Ingest the outcome of one dispatched test-prompt job (#2064).

    Called by the keep-alive agent once it finishes the bounded chat
    completion a heartbeat handed it. Same Bearer-agent-key auth as
    ``cluster_heartbeat``.

    Request body (JSON):
        {"job_id": "...", "ok": true, "reply": "...", "latency_ms": 842,
         "prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}
        or, on failure:
        {"job_id": "...", "ok": false, "error": "..."}

    Response 200: {"ok": true}; terminal replay preserves the original outcome.
    Response 401: missing / invalid agent key, or key not bound to the
        cluster in the URL.
    Response 400: malformed JSON / missing job_id.
    Response 404: job unknown, expired, not current/dispatched, or not owned
        by this cluster; these read identically to avoid probing.
    Response 503: the relay cache cannot safely record the result.
    """
    cluster = _cluster_for_agent_key(request, cluster_guid)
    if cluster is None:
        return JsonResponse({"error": "unauthorized"}, status=401)

    try:
        body = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "body must be a JSON object"}, status=400)

    job_id = body.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return JsonResponse({"error": "job_id is required"}, status=400)

    try:
        recorded = agent_test_jobs.record_result(
            cluster_guid=str(cluster.guid),
            job_id=job_id,
            ok=bool(body.get("ok")),
            reply=str(body.get("reply") or ""),
            latency_ms=_as_int_or_none(body.get("latency_ms")),
            prompt_tokens=_as_int_or_none(body.get("prompt_tokens")),
            completion_tokens=_as_int_or_none(body.get("completion_tokens")),
            total_tokens=_as_int_or_none(body.get("total_tokens")),
            error=str(body.get("error") or ""),
        )
    except agent_test_jobs.AgentTestUnavailable:
        logger.warning("cluster.model_test_result_unavailable: cluster=%s", cluster.guid)
        return JsonResponse({"error": "model test relay is unavailable"}, status=503)
    if not recorded:
        return JsonResponse({"error": "job not found"}, status=404)

    logger.debug(
        "cluster.model_test_result: cluster=%s job=%s ok=%s",
        cluster.slug,
        job_id,
        bool(body.get("ok")),
    )
    return JsonResponse({"ok": True})


def _as_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
