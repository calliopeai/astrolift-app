"""Actual persisted browser authentication is refreshed after hosting lock waits."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from threading import Event
from time import monotonic, sleep

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import close_old_connections, connection, transaction
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from astrolift_clusters.models import TenantCluster
from astrolift_identity.models import AstroliftSession
from astrolift_services.schema import cluster_model_mutations
from astrolift_services.tests.model_hosting_helpers import promote_host_operator
from astrolift_services.tests.test_model_hosting_sources import world as world_fixture
from astrolift_services.tests.test_model_runtime_settings_2269 import request as runtime_request
from core.current_session import fresh_authenticated_session
from core.permissions import Permission, PermissionDenied

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    return world_fixture.__wrapped__(monkeypatch)


def public(value):
    if isinstance(value, dict):
        return {
            key.split("_")[0] + "".join(part.title() for part in key.split("_")[1:]): public(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [public(item) for item in value]
    return value.name if hasattr(value, "name") else value


def body(w):
    return {
        "query": "mutation($input:UpdateClusterModelRuntimeInput!){updateClusterModelRuntime(input:$input){ok errors{code message currentVersion requestedVersion field requiresAttestation supportedMethods} data{clusterVersion}}}",
        "variables": {"input": public(asdict(runtime_request(w)))},
    }


def post(client, w, payload):
    response = client.post(
        "/app/gql/config/",
        content_type="application/json",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(w.org.guid),
        HTTP_X_PLATFORM="web",
        data=json.dumps(payload),
    )
    assert response.status_code == 200 and not response.json().get("errors"), response.json()
    return response.json()["data"]["updateClusterModelRuntime"]


@pytest.mark.parametrize("operator", [False, True])
def test_browser_http_runtime_requires_operator_and_valid_persisted_session(world, client, operator):
    if operator:
        promote_host_operator(world)
    client.force_login(world.user)
    result = post(client, world, body(world))
    assert result["ok"] is operator
    if not operator:
        assert result["errors"][0]["code"] == "PERMISSION_DENIED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("lock_kind", ["cluster", "provider"])
@pytest.mark.parametrize(
    "withdraw", ["deleted", "expired", "password", "revoked", "sidecar-deleted", "sidecar-expired"]
)
def test_actual_browser_session_withdrawn_while_hosting_waits_refuses_write(
    world, client, monkeypatch, lock_kind, withdraw
):
    promote_host_operator(world)
    client.force_login(world.user)
    key = client.session.session_key
    sidecar = AstroliftSession.objects.create(user=world.user, session_key=key, last_seen_at=timezone.now())
    payload, before = body(world), dict(world.cluster.provider_config)
    held, entered = Event(), Event()
    reader_pid = []
    original = cluster_model_mutations._locked_cluster

    def observed(*args):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            reader_pid.append(cursor.fetchone()[0])
        entered.set()
        return original(*args)

    monkeypatch.setattr(cluster_model_mutations, "_locked_cluster", observed)

    def hold():
        close_old_connections()
        try:
            with transaction.atomic():
                if lock_kind == "cluster":
                    TenantCluster.objects.select_for_update().get(pk=world.cluster.pk)
                else:
                    type(world.cluster.provider_plugin).objects.select_for_update().get(
                        pk=world.cluster.provider_plugin.pk
                    )
                held.set()
                assert entered.wait(15)
                deadline = monotonic() + 15
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s))", reader_pid)
                        if cursor.fetchone()[0]:
                            break
                    assert monotonic() < deadline
                    sleep(0.01)
                if withdraw == "deleted":
                    Session.objects.filter(session_key=key).delete()
                elif withdraw == "expired":
                    Session.objects.filter(session_key=key).update(expire_date=timezone.now())
                elif withdraw == "password":
                    actor = get_user_model().objects.get(pk=world.user.pk)
                    actor.set_password("a-new-test-password")
                    actor.save(update_fields=["password"])
                elif withdraw == "revoked":
                    AstroliftSession.all_objects.filter(pk=sidecar.pk).update(revoked_at=timezone.now())
                elif withdraw == "sidecar-deleted":
                    AstroliftSession.all_objects.filter(pk=sidecar.pk).update(deleted_at=timezone.now())
                else:
                    AstroliftSession.all_objects.filter(pk=sidecar.pk).update(expires_at=timezone.now())
        finally:
            connection.close()

    cookies = client.cookies.copy()

    def write():
        close_old_connections()
        try:
            assert held.wait(15)
            browser = Client()
            browser.cookies = cookies
            result = post(browser, world, payload)
            assert not result["ok"] and result["errors"][0]["code"] == "PERMISSION_DENIED", result
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(hold), pool.submit(write)]
        for future in futures:
            future.result(timeout=35)
    world.cluster.refresh_from_db()
    assert world.cluster.provider_config == before
    if withdraw == "revoked":
        assert AstroliftSession.all_objects.get(pk=sidecar.pk).revoked_at is not None
    elif withdraw == "sidecar-deleted":
        assert AstroliftSession.all_objects.get(pk=sidecar.pk).deleted_at is not None
    elif withdraw == "sidecar-expired":
        assert AstroliftSession.all_objects.get(pk=sidecar.pk).expires_at <= timezone.now()


@pytest.mark.parametrize("invalidate", [None, "hash", "missing", "expired", "foreign-sidecar"])
def test_session_validation_is_read_only_even_when_auth_hash_is_invalid(world, client, invalidate):
    client.force_login(world.user)
    actual = RequestFactory().get("/app/gql/config/")
    actual.user, actual.session = world.user, client.session
    key = actual.session.session_key
    if invalidate == "hash":
        actor = get_user_model().objects.get(pk=world.user.pk)
        actor.set_password("changed-test-password")
        actor.save(update_fields=["password"])
    elif invalidate == "missing":
        Session.objects.filter(session_key=key).delete()
    elif invalidate == "expired":
        Session.objects.filter(session_key=key).update(expire_date=timezone.now())
    elif invalidate == "foreign-sidecar":
        other = get_user_model().objects.create(username="other-session-actor")
        AstroliftSession.objects.create(user=other, session_key=key)
    before = list(Session.objects.values())
    with CaptureQueriesContext(connection) as queries:
        if invalidate:
            with pytest.raises(PermissionDenied):
                fresh_authenticated_session(
                    actual, actor_user_id=world.user.pk, permission=Permission.ORG_UPDATE
                )
        else:
            fresh = fresh_authenticated_session(
                actual, actor_user_id=world.user.pk, permission=Permission.ORG_UPDATE
            )
            assert fresh.session_key == key
    assert list(Session.objects.values()) == before
    assert all(query["sql"].lstrip().upper().startswith("SELECT") for query in queries)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("source", ["huggingface", "local-import"])
@pytest.mark.parametrize("withdraw", ["password", "revoked"])
def test_browser_source_request_revalidates_session_after_bounded_external_read(
    world, client, monkeypatch, source, withdraw
):
    """Real HTTP/session/ORM admission; a held provider-read port makes the race deterministic."""
    from types import SimpleNamespace

    from astrolift_services import local_model_artifacts
    from astrolift_services.models import HuggingFaceConnection, LocalModelArtifact

    promote_host_operator(world)
    client.force_login(world.user)
    key = client.session.session_key
    sidecar = AstroliftSession.objects.create(user=world.user, session_key=key, last_seen_at=timezone.now())
    entered, changed = Event(), Event()

    def external_read(*_args, **_kwargs):
        entered.set()
        assert changed.wait(15)
        return "verified-test-account"

    if source == "huggingface":
        monkeypatch.setattr("astrolift_services.schema.hf_connections.verify_account", external_read)
        operation = "connectHuggingFace"
        payload = {
            "query": "mutation($input:ConnectHuggingFaceInput!){connectHuggingFace(input:$input){ok errors{code message currentVersion} data{id}}}",
            "variables": {
                "input": {
                    "organizationId": str(world.org.guid),
                    "name": "Held source",
                    "token": "hf_fixture_not_a_real_credential",
                }
            },
        }
    else:
        monkeypatch.setattr(
            local_model_artifacts,
            "install_model_store",
            lambda **_kwargs: (SimpleNamespace(require_versioning=external_read), "held-test-source"),
        )
        operation = "beginLocalModelArtifact"
        payload = {
            "query": "mutation($input:BeginLocalModelArtifactInput!){beginLocalModelArtifact(input:$input){ok errors{code message currentVersion} data{id}}}",
            "variables": {
                "input": {
                    "organizationId": str(world.org.guid),
                    "name": "Held import",
                    "files": [
                        {"name": name, "sizeBytes": "3", "sha256": "a" * 64}
                        for name in ("config.json", "tokenizer.json", "model.safetensors")
                    ],
                }
            },
        }
    before = (HuggingFaceConnection.objects.count(), LocalModelArtifact.objects.count())
    cookies = client.cookies.copy()

    def revoke():
        close_old_connections()
        try:
            assert entered.wait(15)
            if withdraw == "password":
                actor = get_user_model().objects.get(pk=world.user.pk)
                actor.set_password("changed-source-test-password")
                actor.save(update_fields=["password"])
            else:
                AstroliftSession.all_objects.filter(pk=sidecar.pk).update(revoked_at=timezone.now())
            changed.set()
        finally:
            connection.close()

    def write():
        close_old_connections()
        try:
            browser = Client()
            browser.cookies = cookies
            response = browser.post(
                "/app/gql/config/",
                content_type="application/json",
                HTTP_X_PLATFORM="web",
                HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
                data=json.dumps(payload),
            )
            assert response.status_code == 200 and not response.json().get("errors"), response.json()
            result = response.json()["data"][operation]
            assert not result["ok"] and result["errors"][0]["code"] == "PERMISSION_DENIED", result
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(revoke), pool.submit(write)]
        for future in futures:
            future.result(timeout=35)
    assert (HuggingFaceConnection.objects.count(), LocalModelArtifact.objects.count()) == before
    if withdraw == "revoked":
        assert AstroliftSession.all_objects.get(pk=sidecar.pk).revoked_at is not None


def test_fresh_browser_login_after_revocation_uses_new_session_and_preserves_retired_sidecar(world, client):
    promote_host_operator(world)
    client.force_login(world.user)
    old_key = client.session.session_key
    old = AstroliftSession.objects.create(user=world.user, session_key=old_key, revoked_at=timezone.now())
    Session.objects.filter(session_key=old_key).delete()
    client.force_login(world.user)
    new_key = client.session.session_key
    assert new_key != old_key
    assert post(client, world, body(world))["ok"]
    assert AstroliftSession.all_objects.get(pk=old.pk).revoked_at is not None
    assert AstroliftSession.objects.filter(
        session_key=new_key, user=world.user, revoked_at__isnull=True
    ).exists()


def test_existing_session_header_auth_uses_same_persisted_validation(world, client):
    promote_host_operator(world)
    client.force_login(world.user)
    key = client.session.session_key
    response = Client().post(
        "/app/gql/config/",
        content_type="application/json",
        HTTP_X_PLATFORM="web",
        HTTP_X_ASTROLIFT_ORGANIZATION=str(world.org.guid),
        HTTP_AUTHORIZATION=f"Session {key}",
        data=json.dumps(body(world)),
    )
    assert response.status_code == 200 and not response.json().get("errors"), response.json()
    assert response.json()["data"]["updateClusterModelRuntime"]["ok"]
