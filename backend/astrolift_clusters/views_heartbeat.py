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
Response 401: missing / invalid agent key, or key not bound to the
    cluster in the URL.
Response 400: malformed JSON.
"""

from __future__ import annotations

import hashlib
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

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
    interval so the agent can align its cadence with the control plane.
    """
    raw_key = _bearer_token(request)
    if raw_key is None:
        return JsonResponse({"error": "unauthorized"}, status=401)

    # Bind the key to the cluster named in the URL. Filtering on both the
    # guid AND the key hash means a leaked key for cluster A cannot be
    # used to spoof heartbeats for cluster B — the agent must hit its own
    # cluster's URL with its own key. Empty agent_key_hash rows (no agent
    # provisioned) never match because the lookup hash is always 64 hex
    # chars, never empty.
    cluster = TenantCluster.objects.filter(
        guid=cluster_guid,
        agent_key_hash=_hash_key(raw_key),
        deleted_at__isnull=True,
    ).first()
    if cluster is None:
        # Don't distinguish "no such cluster" from "wrong key" — both are
        # 401 so a caller can't probe which cluster guids exist.
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

    return JsonResponse(
        {
            "ok": True,
            "interval_seconds": cluster.heartbeat_interval_seconds,
        }
    )
