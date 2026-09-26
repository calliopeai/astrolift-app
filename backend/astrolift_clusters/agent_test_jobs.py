"""Cache-backed hand-off between ``testModelEndpoint`` and the in-cluster
keep-alive agent (#2064).

The control plane never reaches a hosted model directly: the vLLM Service
is ClusterIP-only, gated by a NetworkPolicy that admits only the owning
app's namespace, plus -- opt-in -- the agent's own namespace/pod (see
``k8s_native.managed.model_endpoint_vllm``). So one bounded chat-completion
request is relayed through the cluster's own keep-alive agent: the same
agent that already POSTs a heartbeat on a fixed cadence
(``views_heartbeat.py``, #808) with no prior command channel in the other
direction.

A test job rides that existing channel:

1. ``enqueue()`` stores the job under a fresh id and points the cluster's
   "current job" slot at it. ``testModelEndpoint`` then blocks (bounded,
   see ``wait_cap_seconds``) polling ``get_job()`` for a terminal status.
2. The agent's next heartbeat calls ``dispatch_pending()``, which hands the
   job back in the heartbeat response body exactly once and flips it to
   DISPATCHED.
3. The agent runs the bounded chat completion in-cluster -- reading the
   model's API key straight off its Kubernetes Secret, never through the
   control plane -- and POSTs the outcome to the model-test-result view,
   which calls ``record_result()``.

Cache-backed, not a model: a test prompt and its reply are ephemeral
diagnostic content an operator typed to check a model is alive, not a
durable business record. The platform's "never hard delete" rule on
business models would otherwise pin arbitrary prompt/reply text in
Postgres forever; everything here instead expires on its own via the
cache TTL.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any

from django.core.cache import cache

# ---- tunables -----------------------------------------------------------

MAX_PROMPT_CHARS = 4000
MAX_TOKENS = 128
AGENT_TIMEOUT_SECONDS = 20
MAX_REPLY_CHARS = 8000
MAX_ERROR_CHARS = 500

# Ceiling on the cache entry's own life. Generous relative to the
# mutation's own wait cap (below) so a slow agent still finds the job to
# dispatch on its next pulse even after the mutation itself gave up
# waiting on it.
_JOB_TTL_SECONDS = 180

# Fixed-window per-caller budget ("rate-limited per caller"). A test
# triggers a real bounded inference call through the agent, so the budget
# is deliberately tight -- this is a diagnostic action, not a chat UI.
RATE_LIMIT_PER_MINUTE = 6

_JOB_KEY = "astrolift:model_test:job:{job_id}"
_CURRENT_KEY = "astrolift:model_test:current:{cluster_guid}"
_RATE_KEY = "astrolift:model_test:rate:{user_id}:{minute}"

PENDING = "pending"
DISPATCHED = "dispatched"
SUCCEEDED = "succeeded"
FAILED = "failed"
_TERMINAL = (SUCCEEDED, FAILED)


class AgentTestError(Exception):
    """Base for the test-prompt job-store errors."""


class AgentTestConflict(AgentTestError):
    """A test job is already in flight for this cluster's agent."""


class AgentTestRateLimited(AgentTestError):
    """Caller exceeded the per-minute test-prompt budget."""


def check_rate_limit(user_id: int) -> None:
    """Raise :class:`AgentTestRateLimited` once ``user_id`` exceeds the
    per-minute budget.

    Mirrors ``astrolift_pipelines.webhook_security.check_webhook_rate_limit``:
    ``add`` then ``incr`` on a per-minute bucket so concurrent callers land
    on one atomic counter rather than a read-modify-write race that lets
    the limit slip under load.
    """
    minute = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M")
    key = _RATE_KEY.format(user_id=user_id, minute=minute)
    cache.add(key, 0, 120)
    try:
        count = cache.incr(key)
    except ValueError:
        # Key expired between `add` and `incr` -- treat as a fresh bucket.
        cache.set(key, 1, 120)
        count = 1
    if count > RATE_LIMIT_PER_MINUTE:
        raise AgentTestRateLimited(
            f"too many test prompts ({count}/{RATE_LIMIT_PER_MINUTE} per minute); wait and retry"
        )


def enqueue(
    *,
    cluster_guid: str,
    managed_service_guid: str,
    prompt: str,
    model: str,
    base_url: str,
    result_url: str,
    secret_namespace: str,
    secret_name: str,
    secret_key: str,
    requested_by_user_id: int | None,
) -> str:
    """Queue one bounded test-prompt job for ``cluster_guid``'s agent.

    Raises :class:`AgentTestConflict` when a job for this cluster is
    already pending or dispatched -- one cluster runs one agent pod, so
    only one chat completion is ever in flight for it at a time.
    """
    current_key = _CURRENT_KEY.format(cluster_guid=cluster_guid)
    if cache.get(current_key):
        raise AgentTestConflict("a test prompt is already in flight for this cluster's agent")
    job_id = uuid.uuid4().hex
    # `add`, not `set`: closes the race the check above leaves open between
    # two concurrent submissions for the same cluster.
    if not cache.add(current_key, job_id, _JOB_TTL_SECONDS):
        raise AgentTestConflict("a test prompt is already in flight for this cluster's agent")
    job = {
        "job_id": job_id,
        "cluster_guid": cluster_guid,
        "managed_service_guid": managed_service_guid,
        "prompt": prompt[:MAX_PROMPT_CHARS],
        "max_tokens": MAX_TOKENS,
        "model": model,
        "base_url": base_url,
        "result_url": result_url,
        "secret_namespace": secret_namespace,
        "secret_name": secret_name,
        "secret_key": secret_key,
        "timeout_seconds": AGENT_TIMEOUT_SECONDS,
        "requested_by_user_id": requested_by_user_id,
        "status": PENDING,
        "reply": "",
        "latency_ms": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "error": "",
        "created_unix": time.time(),
    }
    cache.set(_JOB_KEY.format(job_id=job_id), job, _JOB_TTL_SECONDS)
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    return cache.get(_JOB_KEY.format(job_id=job_id))


def current_job_id(cluster_guid: str) -> str | None:
    """The id of the job currently occupying ``cluster_guid``'s single
    in-flight slot, if any. Mainly a test seam -- production code goes
    through ``enqueue`` / ``dispatch_pending`` / ``record_result``."""
    return cache.get(_CURRENT_KEY.format(cluster_guid=cluster_guid))


def dispatch_pending(cluster_guid: str) -> dict[str, Any] | None:
    """Called from the heartbeat view: return (and mark DISPATCHED) this
    cluster's pending job, if any.

    Returns None when there is nothing to do, including when the job
    already left the PENDING state -- a job is handed to the agent exactly
    once, so a retried or duplicate heartbeat can't fire the same prompt
    twice.
    """
    job_id = cache.get(_CURRENT_KEY.format(cluster_guid=cluster_guid))
    if not job_id:
        return None
    job = get_job(job_id)
    if job is None or job["status"] != PENDING:
        return None
    job["status"] = DISPATCHED
    cache.set(_JOB_KEY.format(job_id=job_id), job, _JOB_TTL_SECONDS)
    return job


def record_result(
    *,
    cluster_guid: str,
    job_id: str,
    ok: bool,
    reply: str = "",
    latency_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    error: str = "",
) -> bool:
    """Called from the agent's result callback. Returns False (no-op) when
    the job is unknown/expired or does not belong to ``cluster_guid`` -- the
    callback view maps either into one 404 without distinguishing them, so
    a stolen job id from a different cluster can't be used to probe or
    poison another cluster's result.
    """
    job = get_job(job_id)
    if job is None or job.get("cluster_guid") != cluster_guid:
        return False
    job["status"] = SUCCEEDED if ok else FAILED
    job["reply"] = reply[:MAX_REPLY_CHARS]
    job["latency_ms"] = latency_ms
    job["prompt_tokens"] = prompt_tokens
    job["completion_tokens"] = completion_tokens
    job["total_tokens"] = total_tokens
    job["error"] = error[:MAX_ERROR_CHARS]
    cache.set(_JOB_KEY.format(job_id=job_id), job, _JOB_TTL_SECONDS)
    # Free the cluster's slot right away instead of waiting out the full
    # TTL, so back-to-back tests don't trip the in-flight conflict check.
    cache.delete(_CURRENT_KEY.format(cluster_guid=cluster_guid))
    return True


def wait_cap_seconds(heartbeat_interval_seconds: int) -> float:
    """How long ``testModelEndpoint`` blocks for a result before giving up.

    Bounded above regardless of the cluster's configured cadence: an
    operator-set heartbeat interval is not license to hold a web worker
    open indefinitely. Bounded below by enough room for one full interval
    plus the agent's own call timeout, so a cluster on the default cadence
    gets a fair shot at finishing before the mutation gives up.
    """
    return float(min(60, max(20, int(heartbeat_interval_seconds or 0) + AGENT_TIMEOUT_SECONDS + 10)))


def await_result(
    job_id: str, *, heartbeat_interval_seconds: int, poll_seconds: float = 0.5
) -> dict[str, Any] | None:
    """Poll the cache for ``job_id`` to reach a terminal status.

    Returns the terminal job dict, or None if the wait cap elapses first
    -- the caller reports that as "the cluster agent did not respond in
    time" rather than treating it as a hard failure.
    """
    deadline = time.monotonic() + wait_cap_seconds(heartbeat_interval_seconds)
    while True:
        job = get_job(job_id)
        if job is not None and job["status"] in _TERMINAL:
            return job
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_seconds)
