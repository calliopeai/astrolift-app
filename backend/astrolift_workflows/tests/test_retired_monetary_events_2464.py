"""Exact retired event routes stop without rewriting histories or harming other hooks."""

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.worker import Replayer

from astrolift_operations.models import WebhookDelivery, WebhookSubscription
from astrolift_operations.webhook_fanout import matching_subscriptions
from astrolift_workflows.activities.webhook_deliver import _deliver_sync, deliver_webhook
from astrolift_workflows.workflows.deliver_webhook import DeliverWebhookWorkflow
from core.testing.temporal import temporal_worker

RETIRED = "AUDIT.agent.model_spend"


@pytest.mark.parametrize("outer,inner", [(RETIRED, ""), ("", RETIRED), (RETIRED, "deployment.succeeded")])
def test_historical_routing_is_suppressed_before_db_secret_or_http(outer, inner, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("retired event read a subscription, secret or HTTP")

    monkeypatch.setattr(WebhookSubscription.objects, "filter", forbidden)
    monkeypatch.setattr("astrolift_workflows.activities.webhook_deliver._secret_for", forbidden)
    monkeypatch.setattr("astrolift_operations.webhook_delivery.post_webhook", forbidden)
    payload = {
        "subscription_id": "not-looked-up",
        "event_type": outer,
        "envelope": {"event_type": inner, "payload": {"estimated_usd": 99}},
    }
    original = copy.deepcopy(payload)
    assert _deliver_sync(payload) == {"classification": "dropped", "reason": "event retired"}
    assert payload == original
    assert matching_subscriptions(SimpleNamespace(event_type=RETIRED, organization_id=999)) == []


@pytest.mark.django_db
def test_unrelated_event_keeps_retry_auth_history_and_arbitrary_user_payload(monkeypatch):
    from astrolift_identity.models import Organization

    org = Organization.objects.create(name="Ordinary hooks", slug="ordinary-hooks")
    sub = WebhookSubscription.objects.create(
        organization=org, url="https://hooks.example.test", secret_hash="existing-secret", is_active=True
    )
    calls = []

    def post(**kwargs):
        calls.append(kwargs)
        return {
            "status_code": 503,
            "duration_ms": 1,
            "response_body_excerpt": "",
            "error": "",
            "delivery_id": "ordinary-id",
        }

    monkeypatch.setattr("astrolift_operations.webhook_delivery.post_webhook", post)
    payload = {
        "subscription_id": sub.pk,
        "event_type": "deployment.succeeded",
        "envelope": {"payload": {"event_type": RETIRED, "estimated_usd": 1}},
    }
    assert _deliver_sync(payload)["classification"] == "retry"
    assert calls[0]["secret"] == b"existing-secret"
    assert calls[0]["payload"] == payload["envelope"]
    sub.refresh_from_db()
    assert sub.failure_count == 1 and sub.is_active
    assert WebhookDelivery.objects.get(subscription=sub).event_type == "deployment.succeeded"
    sub.secret_hash = ""
    sub.save()
    assert _deliver_sync(payload) == {"classification": "dropped", "reason": "no signing secret"}
    assert len(calls) == 1 and WebhookDelivery.objects.count() == 1


@pytest.mark.asyncio
async def test_old_queued_workflow_input_ends_retired_without_http_or_replay_changes(
    temporal_env, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("retired workflow attempted HTTP")

    monkeypatch.setattr("astrolift_operations.webhook_delivery.post_webhook", forbidden)
    payload = {
        "subscription_id": 999,
        "event_type": RETIRED,
        "envelope": {"event_type": RETIRED, "payload": {"estimated_usd": 99}},
    }
    original = copy.deepcopy(payload)
    queue = f"retired-money-event-{uuid4()}"
    async with temporal_worker(
        temporal_env, task_queue=queue, workflows=[DeliverWebhookWorkflow], activities=[deliver_webhook]
    ):
        handle = await temporal_env.client.start_workflow(
            DeliverWebhookWorkflow.run, payload, id=f"retired-money-event-{uuid4()}", task_queue=queue
        )
        assert await handle.result() == {
            "classification": "dropped",
            "reason": "event retired",
            "attempts": 1,
        }
        history = await handle.fetch_history()
    assert payload == original
    started = history.events[0].workflow_execution_started_event_attributes.input.payloads[0]
    assert json.loads(started.data) == original
    await Replayer(workflows=[DeliverWebhookWorkflow]).replay_workflow(history)


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))
