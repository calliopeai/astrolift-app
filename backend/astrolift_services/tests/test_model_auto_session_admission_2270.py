"""Actual browser admission is revalidated after the final subscription lock wait."""

from datetime import timedelta
from threading import Event, Thread

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import close_old_connections, connection, transaction
from django.db.models.query import QuerySet
from django.utils import timezone

from astrolift_identity.models import AstroliftSession, Policy
from astrolift_identity.step_up_sso import SESSION_SSO_AUTH_TIME_KEY
from astrolift_services.models import ManagedServiceAttachment
from astrolift_services.tests.test_model_connection_2270 import graphql_http, settings
from astrolift_services.tests.test_model_connection_2270 import queue as source_queue
from astrolift_services.tests.test_model_connection_2270 import world as source_world
from astrolift_services.tests.test_model_connection_concurrency_2270 import request_wire, wait_for_actual_lock

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch):
    return source_world.__wrapped__(monkeypatch)


@pytest.fixture
def queue(monkeypatch):
    return source_queue.__wrapped__(monkeypatch)


@pytest.mark.parametrize("operation", ["auto", "revoke"])
@pytest.mark.parametrize(
    "withdrawal", ["logout", "sidecar_revoked", "sidecar_expired", "auth_freshness", "unchanged"]
)
def test_browser_subscription_final_wait_rechecks_persisted_admission(
    world, queue, client, monkeypatch, operation, withdrawal
):
    settings(world, "AUTO")
    client.force_login(world.user)
    if withdrawal == "auth_freshness":
        store = client.session
        store[SESSION_SSO_AUTH_TIME_KEY] = int(timezone.now().timestamp())
        store.save()
        Policy.objects.create(
            organization=world.org,
            name="fresh browser model action",
            slug="fresh-browser-model-action",
            scope_level="ORG",
            effect="ALLOW",
            action_pattern="app.update",
            conditions=[{"kind": "freshness", "max_session_age_minutes": 1}],
        )
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    graphql_http(client, headers, "query{__typename}", {})
    key = client.session.session_key
    sidecar = AstroliftSession.objects.get(session_key=key)
    prior = ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        subscription_status="revoked" if operation == "auto" else "active",
        desired_enabled=operation == "revoke",
    )
    model_revision, model_version = world.model.subscription_revision, world.model.version
    if operation == "auto":
        wire = request_wire(world)
        wire = {
            k: v for k, v in wire.items() if k not in {"ifMatchAppVersion", "policyVersion", "idempotencyKey"}
        }
        field = "subscribeClusterModel"
        query = "mutation($input:SubscribeClusterModelInput!){subscribeClusterModel(input:$input){ok errors{code}}}"
    else:
        wire = {
            "organizationId": str(world.org.guid),
            "id": str(prior.guid),
            "expectedClusterId": str(world.cluster.guid),
            "expectedProviderId": str(world.cluster.provider_plugin.guid),
            "ifMatchVersion": prior.version,
            "ifMatchDeploymentVersion": world.model.version,
        }
        field = "revokeModelSubscription"
        query = "mutation($input:RevokeModelSubscriptionInput!){revokeModelSubscription(input:$input){ok errors{code}}}"
    entered = Event()
    pids, results, errors = [], [], []
    original = QuerySet.first

    def observed(qs):
        if qs.model is ManagedServiceAttachment and qs.query.select_for_update:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.append(cursor.fetchone()[0])
            entered.set()
        return original(qs)

    monkeypatch.setattr(QuerySet, "first", observed)

    def worker():
        close_old_connections()
        try:
            results.append(graphql_http(client, headers, query, {"input": wire}))
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    with transaction.atomic():
        ManagedServiceAttachment.objects.select_for_update().get(pk=prior.pk)
        thread = Thread(target=worker, daemon=True)
        thread.start()
        assert entered.wait(10), errors
        wait_for_actual_lock(pids[0])
        if withdrawal == "logout":
            Session.objects.filter(session_key=key).delete()
        elif withdrawal == "sidecar_revoked":
            AstroliftSession.all_objects.filter(pk=sidecar.pk).update(revoked_at=timezone.now())
        elif withdrawal == "sidecar_expired":
            AstroliftSession.all_objects.filter(pk=sidecar.pk).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )
        elif withdrawal == "auth_freshness":
            store = SessionStore(key)
            store[SESSION_SSO_AUTH_TIME_KEY] = int(timezone.now().timestamp()) - 600
            store.save()
        withdrawn_state = (
            AstroliftSession.all_objects.filter(pk=sidecar.pk)
            .values("user_id", "revoked_at", "deleted_at", "expires_at")
            .get()
        )
    thread.join(15)
    assert not thread.is_alive() and not errors, errors
    assert results and not results[0].get("errors"), results
    if withdrawal == "unchanged":
        assert results[0]["data"][field]["ok"], results
        assert len(queue) == 1
        if operation == "auto":
            assert ManagedServiceAttachment.objects.exclude(pk=prior.pk).count() == 1
        else:
            prior.refresh_from_db()
            assert not prior.desired_enabled and prior.subscription_status == "revoking"
        return
    assert not results[0]["data"][field]["ok"], {
        "reply": results,
        "new_attachment_count": ManagedServiceAttachment.objects.exclude(pk=prior.pk).count(),
        "queue_count": len(queue),
    }
    assert ManagedServiceAttachment.objects.exclude(pk=prior.pk).count() == 0 and queue == []
    prior.refresh_from_db()
    assert prior.deleted_at is None and prior.desired_enabled == (operation == "revoke")
    assert prior.subscription_status == ("revoked" if operation == "auto" else "active")
    world.model.refresh_from_db()
    assert world.model.subscription_revision == model_revision and world.model.version == model_version
    if withdrawal == "logout":
        assert not Session.objects.filter(session_key=key).exists()
    elif withdrawal in ("sidecar_revoked", "sidecar_expired"):
        assert (
            AstroliftSession.all_objects.filter(pk=sidecar.pk)
            .values("user_id", "revoked_at", "deleted_at", "expires_at")
            .get()
            == withdrawn_state
        )
    else:
        assert SessionStore(key)[SESSION_SSO_AUTH_TIME_KEY] < int(timezone.now().timestamp()) - 500
