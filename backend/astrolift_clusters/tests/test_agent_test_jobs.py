"""Tests for the ``agent_test_jobs`` cache-backed job store (#2064).

Real cache backend (no mocking of ``django.core.cache``) so the ``add``
atomicity the conflict guard and rate limiter depend on is exercised for
real, matching the project's no-mocked-infra testing posture. A fresh
LocMemCache is forced per test so the suite doesn't depend on a live
Redis connection or leak state between tests.
"""

from __future__ import annotations

import uuid

import pytest
from django.core.cache import cache
from django.test import override_settings

from astrolift_clusters import agent_test_jobs

pytestmark = pytest.mark.django_db

_FRESH_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "agent-test-jobs-test",
    }
}


@pytest.fixture(autouse=True)
def _isolated_cache():
    with override_settings(CACHES=_FRESH_CACHE):
        cache.clear()
        yield
        cache.clear()


def _enqueue(cluster_guid: str | None = None, **overrides) -> tuple[str, str]:
    cluster_guid = cluster_guid or str(uuid.uuid4())
    kwargs = {
        "cluster_guid": cluster_guid,
        "managed_service_guid": str(uuid.uuid4()),
        "prompt": "hello",
        "model": "Qwen/Qwen3-8B",
        "base_url": "http://svc.ns.svc.cluster.local:8000/v1",
        "result_url": "https://cp.example.com/api/clusters/v1/x/model-test-result/",
        "secret_namespace": "ns",
        "secret_name": "svc-vllm",
        "secret_key": "api_key",
        "requested_by_user_id": 7,
    }
    kwargs.update(overrides)
    job_id = agent_test_jobs.enqueue(**kwargs)
    return cluster_guid, job_id


def test_enqueue_caps_prompt_length():
    long_prompt = "x" * (agent_test_jobs.MAX_PROMPT_CHARS + 500)
    _cluster_guid, job_id = _enqueue(prompt=long_prompt)
    job = agent_test_jobs.get_job(job_id)
    assert len(job["prompt"]) == agent_test_jobs.MAX_PROMPT_CHARS


def test_enqueue_fixes_max_tokens_server_side():
    _cluster_guid, job_id = _enqueue()
    job = agent_test_jobs.get_job(job_id)
    assert job["max_tokens"] == agent_test_jobs.MAX_TOKENS
    assert job["status"] == agent_test_jobs.PENDING


def test_second_enqueue_for_same_cluster_conflicts():
    cluster_guid, _job_id = _enqueue()
    with pytest.raises(agent_test_jobs.AgentTestConflict):
        _enqueue(cluster_guid=cluster_guid)


def test_dispatch_pending_returns_and_marks_dispatched():
    cluster_guid, job_id = _enqueue()
    job = agent_test_jobs.dispatch_pending(cluster_guid)
    assert job is not None
    assert job["job_id"] == job_id
    assert agent_test_jobs.get_job(job_id)["status"] == agent_test_jobs.DISPATCHED


def test_dispatch_pending_never_hands_out_the_same_job_twice():
    """A retried or duplicate heartbeat must not re-fire the prompt."""
    cluster_guid, _job_id = _enqueue()
    first = agent_test_jobs.dispatch_pending(cluster_guid)
    second = agent_test_jobs.dispatch_pending(cluster_guid)
    assert first is not None
    assert second is None


def test_dispatch_pending_none_when_nothing_queued():
    assert agent_test_jobs.dispatch_pending(str(uuid.uuid4())) is None


def test_record_result_success_frees_the_cluster_slot():
    cluster_guid, job_id = _enqueue()
    agent_test_jobs.dispatch_pending(cluster_guid)
    ok = agent_test_jobs.record_result(
        cluster_guid=cluster_guid,
        job_id=job_id,
        ok=True,
        reply="hi there",
        latency_ms=120,
        prompt_tokens=3,
        completion_tokens=4,
        total_tokens=7,
    )
    assert ok is True
    job = agent_test_jobs.get_job(job_id)
    assert job["status"] == agent_test_jobs.SUCCEEDED
    assert job["reply"] == "hi there"
    assert job["total_tokens"] == 7
    # The slot is free again -- a follow-up enqueue for the same cluster
    # does not conflict.
    agent_test_jobs.enqueue(
        cluster_guid=cluster_guid,
        managed_service_guid=str(uuid.uuid4()),
        prompt="again",
        model="m",
        base_url="http://x",
        result_url="http://y",
        secret_namespace="ns",
        secret_name="s",
        secret_key="api_key",
        requested_by_user_id=None,
    )


def test_record_result_caps_reply_and_error_length():
    cluster_guid, job_id = _enqueue()
    agent_test_jobs.dispatch_pending(cluster_guid)
    agent_test_jobs.record_result(
        cluster_guid=cluster_guid,
        job_id=job_id,
        ok=False,
        error="e" * (agent_test_jobs.MAX_ERROR_CHARS + 50),
    )
    job = agent_test_jobs.get_job(job_id)
    assert job["status"] == agent_test_jobs.FAILED
    assert len(job["error"]) == agent_test_jobs.MAX_ERROR_CHARS


def test_record_result_rejects_wrong_cluster():
    """A job id leaked or guessed from a different cluster's agent must not
    let that cluster record a result for it."""
    cluster_guid, job_id = _enqueue()
    ok = agent_test_jobs.record_result(cluster_guid=str(uuid.uuid4()), job_id=job_id, ok=True)
    assert ok is False
    assert agent_test_jobs.get_job(job_id)["status"] == agent_test_jobs.PENDING


def test_record_result_unknown_job_is_a_noop():
    assert agent_test_jobs.record_result(cluster_guid=str(uuid.uuid4()), job_id="nope", ok=True) is False


def test_await_result_returns_immediately_once_terminal():
    cluster_guid, job_id = _enqueue()
    agent_test_jobs.dispatch_pending(cluster_guid)
    agent_test_jobs.record_result(cluster_guid=cluster_guid, job_id=job_id, ok=True, reply="done")
    job = agent_test_jobs.await_result(job_id, heartbeat_interval_seconds=30, poll_seconds=0.01)
    assert job is not None
    assert job["reply"] == "done"


def test_await_result_gives_up_after_the_cap(monkeypatch):
    monkeypatch.setattr(agent_test_jobs, "wait_cap_seconds", lambda *_a, **_k: 0.05)
    _cluster_guid, job_id = _enqueue()
    # Never dispatched / never resolved.
    job = agent_test_jobs.await_result(job_id, heartbeat_interval_seconds=30, poll_seconds=0.01)
    assert job is None


def test_wait_cap_is_bounded_both_ways():
    assert agent_test_jobs.wait_cap_seconds(5) == 35
    assert agent_test_jobs.wait_cap_seconds(1000) == 60
    assert agent_test_jobs.wait_cap_seconds(0) == 30


def test_rate_limit_allows_the_budget_then_blocks():
    user_id = 42
    for _ in range(agent_test_jobs.RATE_LIMIT_PER_MINUTE):
        agent_test_jobs.check_rate_limit(user_id)  # must not raise
    with pytest.raises(agent_test_jobs.AgentTestRateLimited):
        agent_test_jobs.check_rate_limit(user_id)


def test_rate_limit_is_per_caller():
    for _ in range(agent_test_jobs.RATE_LIMIT_PER_MINUTE):
        agent_test_jobs.check_rate_limit(1)
    # A different caller has its own, unspent budget.
    agent_test_jobs.check_rate_limit(2)
