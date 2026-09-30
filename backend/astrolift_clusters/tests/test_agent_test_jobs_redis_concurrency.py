"""Real Redis contenders and expiry/replay proofs; no mocked cache operations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.conf import settings
from django.core.cache import caches
from django.test import override_settings
from redis.client import Pipeline

from astrolift_clusters import agent_test_jobs as jobs
from astrolift_clusters.tests.test_agent_test_jobs import _enqueue


@pytest.fixture(autouse=True)
def redis_cache():
    location = (
        os.environ.get("ASTROLIFT_TEST_REDIS_URL")
        or os.environ.get("DJANGO_CACHE_URL")
        or settings.CACHES["default"].get("LOCATION")
    )
    assert location, "Real Redis is required; configure DJANGO_CACHE_URL or ASTROLIFT_TEST_REDIS_URL"
    configuration = {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": location,
        "KEY_PREFIX": "relay-regression-" + uuid.uuid4().hex,
        "OPTIONS": {"socket_timeout": 2, "socket_connect_timeout": 2},
    }
    with override_settings(CACHES={"default": configuration}):
        backend = caches["default"]
        client = backend._cache.get_client(write=True)
        assert client.ping()
        yield backend, client, configuration
        keys = list(client.scan_iter(match=backend.make_key("*")))
        if keys:
            client.delete(*keys)


def concurrent(count, action):
    barrier = threading.Barrier(count)

    def contend(index):
        barrier.wait(timeout=5)
        return action(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(contend, range(count)))


def test_concurrent_heartbeats_dispatch_at_most_once_across_real_redis_clients():
    cluster, job = _enqueue()
    dispatched = concurrent(12, lambda _index: jobs.dispatch_pending(cluster))
    assert [entry["job_id"] for entry in dispatched if entry] == [job]
    assert jobs.get_job(job)["status"] == jobs.DISPATCHED
    assert jobs.current_job_id(cluster) == job


def test_concurrent_admission_initializes_one_complete_job_and_slot():
    cluster = str(uuid.uuid4())

    def admit(_index):
        try:
            return _enqueue(cluster)[1]
        except jobs.AgentTestConflict:
            return None

    admitted = [job for job in concurrent(12, admit) if job]
    assert len(admitted) == 1
    assert jobs.current_job_id(cluster) == admitted[0]
    assert jobs.get_job(admitted[0])["status"] == jobs.PENDING
    assert jobs.dispatch_pending(cluster)["job_id"] == admitted[0]


def test_dispatch_is_shared_across_independent_processes(redis_cache):
    _backend, _client, configuration = redis_cache
    cluster, job = _enqueue()
    script = """
import json, sys
from django.conf import settings
settings.configure(CACHES={"default": json.loads(sys.argv[1])})
from astrolift_clusters import agent_test_jobs
print("ready", flush=True)
sys.stdin.readline()
job = agent_test_jobs.dispatch_pending(sys.argv[2])
print(json.dumps(job["job_id"] if job else None), flush=True)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, json.dumps(configuration), cluster],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    try:
        for process in processes:
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("dispatch\n")
            process.stdin.flush()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
        assert [result for result in results if result] == [job]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_replayed_and_conflicting_old_results_preserve_first_outcome_and_new_slot():
    cluster, first = _enqueue()
    jobs.dispatch_pending(cluster)
    assert jobs.record_result(
        cluster_guid=cluster, job_id=first, ok=True, reply="First answer", total_tokens=7
    )
    original = jobs.get_job(first)
    _cluster, second = _enqueue(cluster)
    assert jobs.record_result(
        cluster_guid=cluster, job_id=first, ok=False, reply="Conflicting replay", total_tokens=99
    )
    assert jobs.get_job(first) == original
    assert jobs.current_job_id(cluster) == second
    with pytest.raises(jobs.AgentTestConflict):
        _enqueue(cluster)
    assert jobs.dispatch_pending(cluster)["job_id"] == second


def test_pending_or_no_longer_current_jobs_cannot_submit_a_result(redis_cache):
    backend, _client, _configuration = redis_cache
    cluster, first = _enqueue()
    assert not jobs.record_result(cluster_guid=cluster, job_id=first, ok=True)
    assert jobs.get_job(first)["status"] == jobs.PENDING
    jobs.dispatch_pending(cluster)
    backend.delete(jobs._CURRENT_KEY.format(cluster_guid=cluster))
    _cluster, second = _enqueue(cluster)
    assert not jobs.record_result(cluster_guid=cluster, job_id=first, ok=True)
    assert jobs.get_job(first)["status"] == jobs.DISPATCHED
    assert jobs.current_job_id(cluster) == second


def test_concurrent_results_commit_one_whole_original_outcome():
    cluster, job = _enqueue()
    jobs.dispatch_pending(cluster)
    recorded = concurrent(
        12,
        lambda index: jobs.record_result(
            cluster_guid=cluster, job_id=job, ok=index % 2 == 0, reply=f"Answer {index}", total_tokens=index
        ),
    )
    assert all(recorded)
    outcome = jobs.get_job(job)
    index = int(outcome["reply"].split()[-1])
    assert outcome["total_tokens"] == index
    assert outcome["status"] == (jobs.SUCCEEDED if index % 2 == 0 else jobs.FAILED)
    assert jobs.current_job_id(cluster) is None


@pytest.mark.parametrize("operation", ["dispatch", "result", "enqueue"])
def test_expiry_and_replacement_during_watched_transition_never_overwrite_new_slot(
    redis_cache, monkeypatch, operation
):
    backend, client, _configuration = redis_cache
    cluster, first = _enqueue()
    if operation in ("result", "enqueue"):
        jobs.dispatch_pending(cluster)
    if operation == "enqueue":
        assert jobs.record_result(cluster_guid=cluster, job_id=first, ok=True)
    ready, resume = threading.Event(), threading.Event()
    execute = Pipeline.execute
    paused = False

    def paused_execute(pipeline, *args, **kwargs):
        nonlocal paused
        if threading.current_thread().name.startswith("watched-contender") and not paused:
            paused = True
            ready.set()
            assert resume.wait(timeout=5)
        return execute(pipeline, *args, **kwargs)

    monkeypatch.setattr(Pipeline, "execute", paused_execute)

    def old_transition():
        if operation == "dispatch":
            return jobs.dispatch_pending(cluster)
        if operation == "result":
            return jobs.record_result(cluster_guid=cluster, job_id=first, ok=True)
        try:
            return _enqueue(cluster)[1]
        except jobs.AgentTestConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="watched-contender") as executor:
        future = executor.submit(old_transition)
        try:
            assert ready.wait(timeout=5)
            slot_key = backend.make_key(jobs._CURRENT_KEY.format(cluster_guid=cluster))
            if operation != "enqueue":
                client.pexpire(slot_key, 10)
                deadline = time.monotonic() + 2
                while client.exists(slot_key):
                    assert time.monotonic() < deadline
                    time.sleep(0.01)
            _cluster, second = _enqueue(cluster)
        finally:
            resume.set()
        result = future.result(timeout=5)
    assert jobs.current_job_id(cluster) == second
    if operation == "dispatch":
        assert result["job_id"] == second
        assert jobs.get_job(first)["status"] == jobs.PENDING
        assert jobs.dispatch_pending(cluster) is None
    elif operation == "result":
        assert result is False
        assert jobs.get_job(first)["status"] == jobs.DISPATCHED
    else:
        assert result == "conflict"


def test_dispatch_refreshes_matching_slot_and_job_ttl_together(redis_cache):
    backend, client, _configuration = redis_cache
    cluster, job = _enqueue()
    slot_key = backend.make_key(jobs._CURRENT_KEY.format(cluster_guid=cluster))
    job_key = backend.make_key(jobs._JOB_KEY.format(job_id=job))
    client.pexpire(slot_key, 1000)
    client.pexpire(job_key, 1000)
    assert jobs.dispatch_pending(cluster)["job_id"] == job
    assert 179_000 <= client.pttl(slot_key) <= 180_000
    assert 179_000 <= client.pttl(job_key) <= 180_000
