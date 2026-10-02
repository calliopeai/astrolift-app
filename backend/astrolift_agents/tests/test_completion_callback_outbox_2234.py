"""Real PostgreSQL and HTTPS outage recovery, retention and signing boundaries."""

import json
import time
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from astrolift_agents.completion_webhook import CallbackConfigurationError, verify_signature
from astrolift_agents.models import AgentTask, AgentTaskCompletionCallback
from astrolift_agents.services.task_completion_callbacks import (
    callback_configuration,
    configure_policy,
    deliver_attempt,
    freeze_callback,
    normalized_usage,
    redeliver_callback,
    set_callback_secret,
)
from astrolift_identity.models import Organization
from core.secrets import EncryptedSecret, decrypt
from core.testing.callback_receiver import callback_receiver  # noqa: F401

pytestmark = pytest.mark.django_db
KEY = "integration-signing-key-with-32-bytes-minimum"
MARKER = "sensitive-result-marker-must-never-reach-control-plane-logs"


@pytest.fixture
def configured(callback_receiver):  # noqa: F811
    org = Organization.objects.create(name="Callback outbox", slug="callback-outbox")
    configure_policy(org.pk, [callback_receiver.host])
    set_callback_secret(org.pk, "receiver", KEY)
    return org, callback_receiver


def task_with_callback(configured, *, mode="FULL", status="running"):
    org, receiver = configured
    task = AgentTask.objects.create(
        organization=org,
        status=status,
        started_at=timezone.now() - timedelta(seconds=3),
        result={"exit_code": 0, "output": {"text": MARKER}},
        failure={"message": MARKER},
        completion_usage={
            "input_tokens": 7,
            "output_tokens": 11,
            "total_cost_usd": 0.015,
            "model": "example-model",
        },
    )
    row = AgentTaskCompletionCallback.objects.create(
        task=task,
        organization=org,
        callback_url=receiver.url,
        secret_ref="receiver",
        correlation_id="opaque caller ID — unchanged",
        mode=mode,
    )
    return task, row, receiver


def payload(row):
    row.refresh_from_db()
    return json.loads(decrypt(EncryptedSecret(row.payload_backend_kind, bytes(row.payload_ciphertext))))


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled", "timed_out"])
def test_one_frozen_event_for_every_terminal_transition(configured, state):
    task, row, receiver = task_with_callback(configured)
    assert row.final_event_id is None
    task.transition_to(state)
    body = payload(row)
    assert body["status"] == state
    assert body["task_id"] == str(task.guid)
    assert body["correlation_id"] == row.correlation_id
    assert body["usage"]["input_tokens"] == 7
    assert body["usage"]["output_tokens"] == 11
    assert body["usage"]["total_cost_usd"] == 0.015
    assert body["usage"]["duration_ms"] >= 3000
    assert row.generation == 1
    event_id, encrypted = row.final_event_id, bytes(row.payload_ciphertext)
    with transaction.atomic():
        freeze_callback(task)
    row.refresh_from_db()
    assert row.final_event_id == event_id
    assert bytes(row.payload_ciphertext) == encrypted
    assert row.generation == 1
    assert receiver.requests == []


def test_intermediate_states_and_activity_retries_never_freeze(configured):
    task, row, receiver = task_with_callback(configured, status="draft")
    for state in ("queued", "provisioning", "running"):
        task.transition_to(state)
        with transaction.atomic():
            freeze_callback(task)
        row.refresh_from_db()
        assert row.final_event_id is None
        assert not row.payload_ciphertext
    assert receiver.requests == []


@pytest.mark.parametrize("code", [200, 201, 204, 299])
def test_signed_success_purges_queued_payload_and_never_logs_bodies(configured, code, caplog):
    task, row, receiver = task_with_callback(configured)
    receiver.responses[:] = [code]
    receiver.response_body = MARKER.encode()
    task.transition_to("completed")
    row.refresh_from_db()
    assert MARKER.encode() not in bytes(row.payload_ciphertext)
    caplog.set_level("INFO")
    assert deliver_attempt(row.pk, row.generation)["state"] == "finished"
    row.refresh_from_db()
    assert row.status == "delivered"
    assert row.attempts == 1
    assert not row.payload_ciphertext and not row.payload_backend_kind
    assert row.delivered_at and not row.next_attempt_at
    headers, raw = receiver.requests[0]
    assert verify_signature(
        KEY.encode(),
        headers["X-Astrolift-Timestamp"],
        raw,
        headers["X-Astrolift-Signature"],
        now=int(time.time()),
    )
    assert headers["X-Astrolift-Event"] == "agent_task.finished"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(raw)["result"]["output"]["text"] == MARKER
    assert MARKER not in caplog.text
    assert MARKER not in str([record.__dict__ for record in caplog.records])
    assert MARKER not in json.dumps(row.event_metadata)


@pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 422])
def test_permanent_receiver_error_is_not_retried(configured, code):
    task, row, receiver = task_with_callback(configured)
    receiver.responses[:] = [code]
    task.transition_to("failed")
    row.refresh_from_db()
    assert deliver_attempt(row.pk, row.generation)["state"] == "finished"
    assert deliver_attempt(row.pk, row.generation)["state"] == "finished"
    row.refresh_from_db()
    assert row.status == "failed" and row.attempts == 1
    assert row.last_error == f"callback HTTP {code}"
    assert not row.payload_ciphertext
    assert len(receiver.requests) == 1


@pytest.mark.parametrize("code", [503, 408, 429, 302, None])
def test_receiver_outage_backoff_then_recovery_uses_identical_body_and_new_delivery_id(configured, code):
    task, row, receiver = task_with_callback(configured)
    receiver.responses[:] = [code, 200]
    task.transition_to("completed")
    row.refresh_from_db()
    start = row.retry_started_at
    outcome = deliver_attempt(row.pk, row.generation, now=start, jitter=0.5)
    assert outcome == {"state": "retry", "delay_seconds": 30.0, "attempts": 1}
    row.refresh_from_db()
    assert row.status == "pending" and row.next_attempt_at == start + timedelta(seconds=30)
    assert (
        deliver_attempt(row.pk, row.generation, now=start + timedelta(seconds=29), jitter=0.5)["state"]
        == "waiting"
    )
    assert len(receiver.requests) == 1
    assert (
        deliver_attempt(row.pk, row.generation, now=start + timedelta(seconds=30), jitter=0.5)["state"]
        == "finished"
    )
    row.refresh_from_db()
    assert row.status == "delivered" and row.attempts == 2
    assert receiver.requests[0][1] == receiver.requests[1][1]
    assert receiver.requests[0][0]["X-Astrolift-Delivery"] != receiver.requests[1][0]["X-Astrolift-Delivery"]


def test_continuous_origin_outage_gets_attempt_at_full_24_hour_boundary(configured):
    task, row, receiver = task_with_callback(configured)
    receiver.responses[:] = [503]
    task.transition_to("completed")
    row.refresh_from_db()
    start = row.retry_started_at
    now = start
    delays = []
    while True:
        outcome = deliver_attempt(row.pk, row.generation, now=now, jitter=0.5)
        row.refresh_from_db()
        if outcome["state"] == "finished":
            break
        delays.append(outcome["delay_seconds"])
        now = row.next_attempt_at
        assert len(delays) <= 40
    assert delays[:4] == [30, 120, 600, 1800]
    assert (now - start).total_seconds() == 86400
    assert row.status == "failed" and row.last_error == "callback retry window exhausted"
    assert not row.payload_ciphertext
    assert len(receiver.requests) == len(delays) + 1


def test_notify_omits_result_and_failure_text(configured):
    task, row, receiver = task_with_callback(configured, mode="NOTIFY")
    task.transition_to("failed")
    body = payload(row)
    assert "result" not in body and body["failure_message"] is None
    assert MARKER not in json.dumps(body)
    deliver_attempt(row.pk, row.generation)
    assert MARKER.encode() not in receiver.requests[0][1]


def test_rotation_reads_current_secret_each_attempt_and_replay_uses_same_final_event(configured):
    task, row, receiver = task_with_callback(configured)
    receiver.responses[:] = [503, 200]
    task.transition_to("completed")
    row.refresh_from_db()
    event_id = row.final_event_id
    start = row.retry_started_at
    deliver_attempt(row.pk, row.generation, now=start, jitter=0.5)
    new_key = "rotated-integration-key-at-least-32-bytes"
    set_callback_secret(task.organization_id, "receiver", new_key)
    deliver_attempt(row.pk, row.generation, now=start + timedelta(seconds=30), jitter=0.5)
    for key, (headers, raw) in zip((KEY, new_key), receiver.requests, strict=True):
        assert verify_signature(
            key.encode(),
            headers["X-Astrolift-Timestamp"],
            raw,
            headers["X-Astrolift-Signature"],
            now=int(time.time()),
        )
    replay = redeliver_callback(task)
    assert replay.generation == 2 and replay.final_event_id == event_id
    assert deliver_attempt(row.pk, 1)["state"] == "finished"
    assert len(receiver.requests) == 2
    deliver_attempt(row.pk, 2)
    assert len(receiver.requests) == 3
    assert receiver.requests[-1][1] == receiver.requests[0][1]


def test_active_delivery_lease_prevents_parallel_post(configured):
    task, row, receiver = task_with_callback(configured)
    task.transition_to("completed")
    row.refresh_from_db()
    row.lease_until = timezone.now() + timedelta(seconds=40)
    row.save()
    assert deliver_attempt(row.pk, row.generation)["state"] == "waiting"
    assert receiver.requests == []
    row.lease_until = timezone.now() - timedelta(seconds=1)
    row.save()
    assert deliver_attempt(row.pk, row.generation)["state"] == "finished"
    assert len(receiver.requests) == 1


def test_removed_destination_policy_prevents_delivery_and_deleted_task_purges(configured):
    task, row, receiver = task_with_callback(configured)
    task.transition_to("completed")
    row.refresh_from_db()
    configure_policy(task.organization_id, [])
    assert deliver_attempt(row.pk, row.generation)["state"] == "finished"
    row.refresh_from_db()
    assert row.status == "failed" and not row.payload_ciphertext
    assert receiver.requests == []
    configure_policy(task.organization_id, [receiver.host])
    redeliver_callback(task)
    task.soft_delete()
    row.refresh_from_db()
    assert deliver_attempt(row.pk, row.generation)["state"] == "finished"
    row.refresh_from_db()
    assert not row.payload_ciphertext
    assert receiver.requests == []


def test_invalid_registration_and_replay_retention_are_refused(configured):
    org, receiver = configured
    with pytest.raises(CallbackConfigurationError, match="allow-list"):
        callback_configuration(
            org.pk, callback_url="https://outside.example.org", callback_secret_ref="receiver"
        )
    with pytest.raises(CallbackConfigurationError, match="128"):
        callback_configuration(
            org.pk, callback_url=receiver.url, callback_secret_ref="receiver", correlation_id="x" * 129
        )
    task, row, _ = task_with_callback(configured)
    task.transition_to("completed")
    row.refresh_from_db()
    deliver_attempt(row.pk, row.generation)
    task.ended_at = timezone.now() - timedelta(hours=73)
    task.save()
    with pytest.raises(CallbackConfigurationError, match="retention"):
        redeliver_callback(task)


def test_unknown_usage_remains_unknown_and_invalid_values_are_not_fabricated():
    assert normalized_usage(
        {"input_tokens": True, "output_tokens": -1, "total_cost_usd": float("inf"), "model": "bad\nmodel"}
    ) == {
        "input_tokens": None,
        "output_tokens": None,
        "total_cost_usd": None,
        "model": None,
    }


@pytest.mark.parametrize("mode", ["FULL", "NOTIFY"])
def test_erased_task_result_is_never_delivered_in_full_mode(configured, mode):
    task, row, receiver = task_with_callback(configured, mode=mode)
    task.transition_to("completed")
    row.refresh_from_db()
    task.result = None
    task.save()
    deliver_attempt(row.pk, row.generation)
    row.refresh_from_db()
    assert row.status == ("failed" if mode == "FULL" else "delivered")
    assert not row.payload_ciphertext
    assert len(receiver.requests) == (0 if mode == "FULL" else 1)


@pytest.mark.parametrize("change", ["short_ttl", "deleted_run"])
def test_linked_run_retention_and_deletion_remove_pending_sensitive_payload(configured, change):
    from astrolift_identity.models import Team
    from astrolift_lifecycle.models import AgentRun
    from astrolift_registry.models import RegisteredApp, Workload

    task, row, receiver = task_with_callback(configured)
    team = Team.objects.create(organization=task.organization, name="Callbacks", slug="callbacks")
    app = RegisteredApp.objects.create(
        organization=task.organization, team=team, name="Callbacks", slug="callbacks"
    )
    workload = Workload.objects.create(registered_app=app, name="Report", slug="report", kind="agent")
    run = AgentRun.objects.create(workload=workload, result_ttl_hours=1, retry_count=2)
    task.agent_run = run
    task.save()
    task.transition_to("completed")
    row.refresh_from_db()
    assert payload(row)["attempt"] == 3
    if change == "deleted_run":
        run.soft_delete()
        now = row.retry_started_at
    else:
        now = row.retry_started_at + timedelta(hours=2)
    deliver_attempt(row.pk, row.generation, now=now, jitter=0.5)
    row.refresh_from_db()
    assert row.status == "failed" and not row.payload_ciphertext
    assert receiver.requests == []


def test_failed_agent_final_result_json_is_preserved(configured):
    task, row, _ = task_with_callback(configured)
    task.result = None
    task.failure = {"message": "execution failed", "output": {"text": MARKER}}
    task.save()
    task.transition_to("failed")
    assert payload(row)["result"] == {"text": MARKER}


def test_manual_replay_refuses_erased_failure_content(configured):
    task, row, _ = task_with_callback(configured)
    task.transition_to("failed")
    row.refresh_from_db()
    deliver_attempt(row.pk, row.generation)
    task.failure = None
    task.save()
    with pytest.raises(CallbackConfigurationError, match="unavailable"):
        redeliver_callback(task)


def test_oversized_reported_cost_cannot_interrupt_task_completion():
    assert normalized_usage({"total_cost_usd": 10**1000})["total_cost_usd"] is None


def test_global_http_debug_diagnostics_cannot_print_callback_bodies(configured, monkeypatch, capsys):
    import http.client

    task, row, _ = task_with_callback(configured)
    task.transition_to("completed")
    row.refresh_from_db()
    monkeypatch.setattr(http.client.HTTPConnection, "debuglevel", 1)
    monkeypatch.setattr(http.client.HTTPSConnection, "debuglevel", 1)
    deliver_attempt(row.pk, row.generation)
    row.refresh_from_db()
    assert row.status == "delivered"
    captured = capsys.readouterr()
    assert MARKER not in captured.out and MARKER not in captured.err


@pytest.mark.django_db(transaction=True)
def test_delivery_lease_does_not_lock_task_and_invert_replay_lock_order(configured, settings):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    task, row, receiver = task_with_callback(configured)
    task.transition_to("completed")
    row.refresh_from_db()

    def deliver():
        try:
            return deliver_attempt(row.pk, row.generation)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            AgentTask.objects.select_for_update().get(pk=task.pk)
            future = pool.submit(deliver)
            outcome = future.result(timeout=5)
    assert outcome["state"] == "finished"
    row.refresh_from_db()
    assert row.status == "delivered" and len(receiver.requests) == 1
