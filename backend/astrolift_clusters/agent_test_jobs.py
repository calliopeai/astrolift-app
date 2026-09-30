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
   job back in the heartbeat response body at most once and flips it to
   DISPATCHED. A lost response can mean no execution; it is not re-dispatched.
3. The agent runs the bounded chat completion in-cluster -- reading the
   model's API key straight off its Kubernetes Secret, never through the
   control plane -- and POSTs the outcome to the model-test-result view,
   which calls ``record_result()``. The agent derives that view's URL from
   its own configured heartbeat endpoint, never from the job: nothing here
   carries a callback URL for the agent to trust.

Cache-backed, not a model: a test prompt and its reply are ephemeral
diagnostic content an operator typed to check a model is alive, not a
durable business record. The platform's "never hard delete" rule on
business models would otherwise pin arbitrary prompt/reply text in
Postgres forever; everything here instead expires on its own via the
cache TTL.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from typing import Any

from django.core.cache import DEFAULT_CACHE_ALIAS, cache, caches
from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache

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


class AgentTestUnavailable(AgentTestError):
    """The cache cannot safely admit or transition a relay job."""


_LOCAL_LOCKS = tuple(threading.Lock() for _ in range(64))


def _relay_cache():
    try:
        backend = caches[DEFAULT_CACHE_ALIAS]
    except Exception as exc:
        raise AgentTestUnavailable("model test relay cache is unavailable") from exc
    if not isinstance(backend, (RedisCache, LocMemCache)):
        raise AgentTestUnavailable("model test relay cache is unavailable")
    return backend


def _atomic_transition(cluster_guid, job_id, decide):
    """Commit the job and matching slot together; no expiring lock lease."""
    backend = _relay_cache()
    current_key = _CURRENT_KEY.format(cluster_guid=cluster_guid)
    try:
        if isinstance(backend, LocMemCache):
            lock = _LOCAL_LOCKS[hash(cluster_guid) % len(_LOCAL_LOCKS)]
            if not lock.acquire(timeout=1):
                raise AgentTestUnavailable("model test relay cache is busy")
            try:
                current_id = backend.get(current_key)
                target = job_id or current_id
                key = _JOB_KEY.format(job_id=target)
                job = backend.get(key) if target else None
                result, update, slot = decide(current_id, job)
                if update is not None:
                    backend.set(key, update, _JOB_TTL_SECONDS)
                if slot == "claim":
                    backend.set(current_key, target, _JOB_TTL_SECONDS)
                elif slot == "release":
                    backend.delete(current_key)
                return result
            finally:
                lock.release()

        from redis.exceptions import WatchError

        storage = backend._cache
        client = storage.get_client(write=True)
        serializer = storage._serializer
        slot_key = backend.make_and_validate_key(current_key)
        for _attempt in range(8):
            with client.pipeline() as pipeline:
                try:
                    pipeline.watch(slot_key)
                    value = pipeline.get(slot_key)
                    current_id = serializer.loads(value) if value is not None else None
                    target = job_id or current_id
                    key = backend.make_and_validate_key(_JOB_KEY.format(job_id=target))
                    pipeline.watch(key)
                    value = pipeline.get(key)
                    job = serializer.loads(value) if value is not None else None
                    result, update, slot = decide(current_id, job)
                    if update is None and slot == "keep":
                        return result
                    pipeline.multi()
                    if update is not None:
                        pipeline.set(key, serializer.dumps(update), ex=_JOB_TTL_SECONDS)
                    if slot == "claim":
                        pipeline.set(slot_key, serializer.dumps(target), ex=_JOB_TTL_SECONDS)
                    elif slot == "release":
                        pipeline.delete(slot_key)
                    pipeline.execute()
                    return result
                except WatchError:
                    continue
        raise AgentTestUnavailable("model test relay cache is busy")
    except (AgentTestConflict, AgentTestUnavailable):
        raise
    except Exception as exc:
        raise AgentTestUnavailable("model test relay cache is unavailable") from exc


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
    try:
        backend = _relay_cache()
        backend.add(key, 0, 120)
        try:
            count = backend.incr(key)
        except ValueError:
            backend.set(key, 1, 120)
            count = 1
    except AgentTestUnavailable:
        raise
    except Exception as exc:
        raise AgentTestUnavailable("model test relay cache is unavailable") from exc
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
    secret_namespace: str,
    secret_name: str,
    secret_key: str,
    requested_by_user_id: int | None,
) -> str:
    """Queue one bounded test-prompt job for ``cluster_guid``'s agent.

    Raises :class:`AgentTestConflict` when a job for this cluster is
    already pending or dispatched -- one cluster runs one agent pod, so
    only one chat completion is ever in flight for it at a time.

    No ``result_url`` here on purpose: the agent derives where to report
    back from its own configured heartbeat endpoint, never from anything
    this job carries -- a compromised or buggy control plane naming an
    arbitrary URL here would otherwise be a way to exfiltrate the
    cluster's agent key (the result POST is Bearer-authenticated with
    it).
    """
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "cluster_guid": cluster_guid,
        "managed_service_guid": managed_service_guid,
        "prompt": prompt[:MAX_PROMPT_CHARS],
        "max_tokens": MAX_TOKENS,
        "model": model,
        "base_url": base_url,
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

    def admit(current_id, _job):
        if current_id is not None:
            raise AgentTestConflict("a test prompt is already in flight for this cluster's agent")
        return job_id, job, "claim"

    return _atomic_transition(cluster_guid, job_id, admit)


def get_job(job_id: str) -> dict[str, Any] | None:
    try:
        return _relay_cache().get(_JOB_KEY.format(job_id=job_id))
    except AgentTestUnavailable:
        raise
    except Exception as exc:
        raise AgentTestUnavailable("model test relay cache is unavailable") from exc


def current_job_id(cluster_guid: str) -> str | None:
    """The id of the job currently occupying ``cluster_guid``'s single
    in-flight slot, if any. Mainly a test seam -- production code goes
    through ``enqueue`` / ``dispatch_pending`` / ``record_result``."""
    return cache.get(_CURRENT_KEY.format(cluster_guid=cluster_guid))


def dispatch_pending(cluster_guid: str) -> dict[str, Any] | None:
    """Called from the heartbeat view: return (and mark DISPATCHED) this
    cluster's pending job at most once, if any.

    Returns None when there is nothing to do, including when the job
    already left the PENDING state. A lost heartbeat response can result in
    zero execution; a retried or duplicate heartbeat never re-dispatches it.
    """

    def dispatch(current_id, job):
        if (
            not isinstance(job, dict)
            or current_id != job.get("job_id")
            or job.get("cluster_guid") != cluster_guid
            or job.get("status") != PENDING
        ):
            return None, None, "keep"
        dispatched = {**job, "status": DISPATCHED}
        return dispatched, dispatched, "claim"

    return _atomic_transition(cluster_guid, None, dispatch)


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
    poison another cluster's result. Only the current dispatched job can
    finish. A terminal replay acknowledges the original outcome unchanged
    and never releases a newer job's slot.
    """

    def finish(current_id, job):
        if (
            not isinstance(job, dict)
            or job.get("cluster_guid") != cluster_guid
            or job.get("job_id") != job_id
        ):
            return False, None, "keep"
        if job.get("status") in _TERMINAL:
            return True, None, "keep"
        if current_id != job_id or job.get("status") != DISPATCHED:
            return False, None, "keep"
        completed = {
            **job,
            "status": SUCCEEDED if ok else FAILED,
            "reply": reply[:MAX_REPLY_CHARS],
            "latency_ms": latency_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "error": error[:MAX_ERROR_CHARS],
        }
        return True, completed, "release"

    return _atomic_transition(cluster_guid, job_id, finish)


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
