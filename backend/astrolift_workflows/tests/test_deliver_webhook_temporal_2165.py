"""Outbound retries run in the real sandbox and replay without new randomness."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from temporalio.worker import Replayer

from astrolift_identity.models import Organization
from astrolift_operations.models import WebhookSubscription
from astrolift_operations.webhook_delivery import verify_signature
from astrolift_workflows.activities.webhook_deliver import deliver_webhook
from astrolift_workflows.workflows.deliver_webhook import DeliverWebhookWorkflow
from core.testing.temporal import temporal_worker


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("first_status", [503, 410, 400])
async def test_real_delivery_retries_transient_responses_and_replays(temporal_env, first_status):
    received = []
    secret = "test-webhook-secret"

    class Subscriber(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((body, dict(self.headers)))
            self.send_response(first_status if len(received) == 1 else 200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Subscriber)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def subscribe():
        org = Organization.objects.create(name="Webhook", slug=f"webhook-{uuid4().hex}")
        return WebhookSubscription.objects.create(
            organization=org,
            url=f"http://127.0.0.1:{server.server_port}/hook",
            secret_hash=secret,
        )

    try:
        subscription = await sync_to_async(subscribe)()
        async with temporal_worker(
            temporal_env, workflows=[DeliverWebhookWorkflow], activities=[deliver_webhook]
        ):
            handle = await temporal_env.client.start_workflow(
                DeliverWebhookWorkflow.run,
                {
                    "subscription_id": subscription.pk,
                    "event_type": "deployment.succeeded",
                    "envelope": {"event_type": "deployment.succeeded", "payload": {"hello": "world"}},
                },
                id=f"webhook-retry-{uuid4()}",
                task_queue="astrolift-test",
            )
            outcome = await asyncio.wait_for(handle.result(), timeout=20)
            history = await handle.fetch_history()
        expected_attempts = 2 if first_status == 503 else 1
        assert outcome["attempts"] == expected_attempts
        assert (
            outcome["classification"]
            == {503: "success", 410: "immediate_disable", 400: "permanent_failure"}[first_status]
        )
        assert len(received) == expected_attempts
        for body, headers in received:
            timestamp = int(headers["X-Astrolift-Timestamp"])
            assert verify_signature(
                secret=secret.encode(),
                timestamp_unix=timestamp,
                raw_body=body,
                presented=headers["X-Astrolift-Signature"],
                now_unix=timestamp,
            )
        deliveries = await sync_to_async(list)(
            subscription.deliveries.order_by("retry_attempt").values_list(
                "retry_attempt", "status_code", "success"
            )
        )
        assert deliveries == (
            [(1, 503, False), (2, 200, True)] if first_status == 503 else [(1, first_status, False)]
        )
        await sync_to_async(subscription.refresh_from_db)()
        if first_status == 503:
            assert subscription.failure_count == 0
            assert subscription.is_active
        elif first_status == 410:
            assert not subscription.is_active
        await Replayer(workflows=[DeliverWebhookWorkflow]).replay_workflow(history)
        assert len(received) == expected_attempts
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)
