"""Actual HTTP admission after native reads observe committed PG withdrawals."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest
from django.contrib.sessions.models import Session
from django.db import close_old_connections
from django.utils import timezone

from astrolift_identity.models import AstroliftSession
from astrolift_services.models import ManagedServiceAttachment
from astrolift_services.tests.test_bedrock_connections_2269 import (
    register,
    world,  # noqa: F401 -- actual org/operator/provider fixture
)
from astrolift_services.tests.test_cluster_model_mutations_2213 import allowed, subscription
from astrolift_services.tests.test_cluster_model_mutations_2213 import (
    queue as queue_fixture,
)
from astrolift_services.tests.test_model_connection_2270 import graphql_http, http_token
from astrolift_services.tests.test_model_host_session_2269 import public

pytestmark = pytest.mark.django_db(transaction=True)
UPDATE = "mutation($input:UpdateBedrockModelConnectionInput!){updateBedrockModelConnection(input:$input){ok errors{code} data{id status ready desiredSubscriptionRevision operationId nativeSource{sourceArn}}}}"
SUBSCRIBE = "mutation($input:SubscribeClusterModelInput!){subscribeClusterModel(input:$input){ok errors{code} data{subscription{id}}}}"


@pytest.fixture
def queue(monkeypatch):
    return queue_fixture.__wrapped__(monkeypatch)


@pytest.mark.parametrize("operation", ["update", "subscribe"])
@pytest.mark.parametrize(
    "withdrawal",
    [
        "token_revoke",
        "token_scope",
        "actor_inactive",
        "session_logout",
        "session_revoked",
        "session_expired",
        "session_sidecar_expired",
        "global_flag",
    ],
)
def test_native_read_rechecks_current_authority_before_intent_effects(
    world,
    client,
    queue,
    monkeypatch,
    operation,
    withdrawal,  # noqa: F811 -- imported real world fixture
):
    w = world
    w.model, _ = register(w, client)
    if operation == "subscribe":
        allowed(w)
        w.user.is_superuser = False
        w.user.save()
        w.token, w.headers = http_token(w, scopes=["read:apps", "write:apps"])
        query, wire = SUBSCRIBE, public(asdict(subscription(w)))
    else:
        query = UPDATE
        wire = {
            "organizationId": str(w.org.guid),
            "id": str(w.model.guid),
            "expectedClusterId": str(w.cluster.guid),
            "expectedProviderId": str(w.cluster.provider_plugin.guid),
            "ifMatchVersion": w.model.version,
            "name": "Updated name",
            "allowSubscriptions": True,
        }
    browser = withdrawal.startswith("session_")
    headers = w.headers
    key = None
    sidecar = None
    if browser:
        client.force_login(w.user)
        key = client.session.session_key
        assert Session.objects.filter(session_key=key).exists()
        sidecar = AstroliftSession.objects.create(user=w.user, session_key=key, last_seen_at=timezone.now())
        headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(w.org.guid), "HTTP_X_PLATFORM": "web"}

    def committed_withdrawal():
        close_old_connections()
        try:
            if withdrawal == "token_revoke":
                type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)
            elif withdrawal == "token_scope":
                type(w.token).objects.filter(pk=w.token.pk).update(scopes=["read:apps"])
            elif withdrawal == "actor_inactive":
                type(w.user).objects.filter(pk=w.user.pk).update(is_active=False)
            elif withdrawal == "session_logout":
                Session.objects.filter(session_key=key).delete()
            elif withdrawal == "session_expired":
                Session.objects.filter(session_key=key).update(expire_date=timezone.now())
            elif withdrawal == "session_sidecar_expired":
                AstroliftSession.all_objects.filter(pk=sidecar.pk).update(expires_at=timezone.now())
            else:
                AstroliftSession.all_objects.filter(pk=sidecar.pk).update(revoked_at=timezone.now())
        finally:
            close_old_connections()

    def after_native_read():
        if withdrawal == "global_flag":
            monkeypatch.setattr("astrolift_services.native_model_connections.enabled", lambda: False)
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(committed_withdrawal).result(timeout=10)

    before_reads = len(w.reads)
    before_rows = ManagedServiceAttachment.objects.count()
    before = (w.model.version, w.model.subscription_revision, w.model.name, w.model.status)
    w.after_read = after_native_read
    result = graphql_http(client, headers, query, {"input": wire})
    field = "subscribeClusterModel" if operation == "subscribe" else "updateBedrockModelConnection"
    assert not result.get("errors"), result
    assert result["data"][field]["ok"] is False, result
    assert len(w.reads) > before_reads, "Refusal must follow the actual native metadata boundary"
    w.model.refresh_from_db()
    assert (w.model.version, w.model.subscription_revision, w.model.name, w.model.status) == before
    assert ManagedServiceAttachment.objects.count() == before_rows and not queue
    if withdrawal == "session_logout":
        assert not Session.objects.filter(session_key=key).exists()
    elif withdrawal == "session_revoked":
        assert AstroliftSession.all_objects.get(pk=sidecar.pk).revoked_at is not None
    elif withdrawal == "session_expired":
        assert Session.objects.get(session_key=key).expire_date <= timezone.now()
    elif withdrawal == "session_sidecar_expired":
        assert AstroliftSession.all_objects.get(pk=sidecar.pk).expires_at <= timezone.now()


def test_current_operator_edit_preserves_native_source_and_queues_configuration_only(world, client, queue):  # noqa: F811
    w = world
    w.model, _ = register(w, client)
    native = dict(w.model.config["native_connection"])
    revision = w.model.subscription_revision
    result = graphql_http(
        client,
        w.headers,
        UPDATE,
        {
            "input": {
                "organizationId": str(w.org.guid),
                "id": str(w.model.guid),
                "expectedClusterId": str(w.cluster.guid),
                "expectedProviderId": str(w.cluster.provider_plugin.guid),
                "ifMatchVersion": w.model.version,
                "name": "Updated native name",
                "allowSubscriptions": True,
            }
        },
    )
    assert not result.get("errors"), result
    envelope = result["data"]["updateBedrockModelConnection"]
    assert envelope["ok"], envelope
    data = envelope["data"]
    assert data["status"] == "updating" and data["ready"] is None
    assert data["desiredSubscriptionRevision"] == revision + 1 and data["operationId"]
    assert data["nativeSource"]["sourceArn"] == native["source_arn"]
    w.model.refresh_from_db()
    assert w.model.config["native_connection"] == native
    assert w.model.name == "Updated native name" and w.model.operation_kind == "reconcile"
    assert queue[0][0] == "NativeModelConnectionReconcileWorkflow"
    assert not queue[0][1][0].delete_data
