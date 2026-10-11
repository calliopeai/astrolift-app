"""Real HTTP requests waiting on PostgreSQL locks must re-admit current authority."""

from threading import Event, Thread
from time import monotonic, sleep

import pytest
from django.db import close_old_connections, connection, transaction
from django.test import Client
from django.utils import timezone

from astrolift_identity.models import Member, Policy, RoleBinding
from astrolift_services.models import ManagedServiceAttachment, ModelConnectionRequest
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_model_connection_2270 import (
    create_request,
    graphql_http,
    http_token,
    placement_wire,
    proposal,
    reviewer,
    settings,
    vote,
)
from astrolift_services.tests.test_model_connection_2270 import queue as queue_fixture
from astrolift_services.tests.test_model_connection_2270 import world as world_fixture

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch):
    return world_fixture.__wrapped__(monkeypatch)


@pytest.fixture
def queue(monkeypatch):
    return queue_fixture.__wrapped__(monkeypatch)


def request_wire(w):
    with subject(w):
        value = proposal(w)
    return placement_wire(w) | {
        "appEnvironmentId": str(w.env.guid),
        "alias": value.alias,
        "ifMatchVersion": value.if_match_version,
        "ifMatchEnvironmentVersion": value.if_match_environment_version,
        "ifMatchAppVersion": value.if_match_app_version,
        "policyVersion": value.policy_version,
        "idempotencyKey": str(value.idempotency_key),
    }


def wait_for_actual_lock(pid):
    until = monotonic() + 10
    while monotonic() < until:
        with connection.cursor() as cursor:
            cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pid])
            row = cursor.fetchone()
        if row and row[0] == "Lock":
            return
        sleep(0.02)
    pytest.fail("HTTP worker did not wait on an actual PostgreSQL row lock")


@pytest.mark.parametrize("operation", ["request", "finalize"])
@pytest.mark.parametrize(
    "withdrawal",
    [
        "role",
        "scope",
        "token",
        "membership",
        "actor",
        "organization",
        "policy",
        "app",
        "environment",
        "model",
    ],
)
def test_http_rechecks_withdrawals_after_actual_lock_wait(world, queue, monkeypatch, operation, withdrawal):
    import astrolift_services.model_connection_requests as orchestration

    settings(world)
    row = None
    if operation == "finalize":
        row = create_request(world)
        vote(world, row, reviewer(world, "http-wait"))
        query = "mutation($input:DecideModelConnectionRequestInput!){finalizeModelConnectionRequest(input:$input){ok errors{code} data{status subscriptionId}}}"
        variables = {"input": {"id": str(row.guid), "ifMatchVersion": row.version}}
        field = "finalizeModelConnectionRequest"
    else:
        query = "mutation($input:RequestModelConnectionInput!){requestModelConnection(input:$input){ok errors{code} data{status subscriptionId}}}"
        variables = {"input": request_wire(world)}
        field = "requestModelConnection"
    token, headers = http_token(world)
    entered = Event()
    pids = []
    result = []
    errors = []
    original = orchestration.locked_organization

    def observed_lock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pids.append(cursor.fetchone()[0])
        entered.set()
        return original()

    monkeypatch.setattr(orchestration, "locked_organization", observed_lock)

    def worker():
        close_old_connections()
        try:
            result.append(graphql_http(Client(), headers, query, variables))
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    with transaction.atomic():
        type(world.org).objects.select_for_update().get(pk=world.org.pk)
        thread = Thread(target=worker, daemon=True)
        thread.start()
        assert entered.wait(10), errors
        wait_for_actual_lock(pids[0])
        if withdrawal == "role":
            RoleBinding.objects.filter(user=world.user, scope_kind="APP").update(deleted_at=timezone.now())
        elif withdrawal == "scope":
            type(token).objects.filter(pk=token.pk).update(scopes=["read:apps"])
        elif withdrawal == "token":
            type(token).objects.filter(pk=token.pk).update(is_revoked=True)
        elif withdrawal == "membership":
            Member.objects.filter(user=world.user, scope_kind="ORG").update(is_active=False)
        elif withdrawal == "actor":
            type(world.user).objects.filter(pk=world.user.pk).update(is_active=False)
        elif withdrawal == "organization":
            type(world.org).objects.filter(pk=world.org.pk).update(deleted_at=timezone.now())
        elif withdrawal == "policy":
            Policy.objects.create(
                organization=world.org,
                name="withdrawn",
                slug="withdrawn",
                scope_level="ORG",
                effect="DENY",
                action_pattern="app.update",
            )
        elif withdrawal == "app":
            type(world.medops_app).objects.filter(pk=world.medops_app.pk).update(deleted_at=timezone.now())
        elif withdrawal == "environment":
            type(world.env).objects.filter(pk=world.env.pk).update(deleted_at=timezone.now())
        else:
            type(world.model).objects.filter(pk=world.model.pk).update(deleted_at=timezone.now())
    thread.join(15)
    assert not thread.is_alive() and not errors, errors
    assert result and not result[0].get("errors"), result
    assert not result[0]["data"][field]["ok"], result
    assert not ManagedServiceAttachment.objects.exists() and queue == []
    if operation == "request":
        assert not ModelConnectionRequest.objects.exists()


def parallel_http(headers, query, variables, count=2):
    results = []
    errors = []
    start = Event()

    def worker():
        close_old_connections()
        try:
            assert start.wait(5)
            results.append(graphql_http(Client(), headers, query, variables))
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [Thread(target=worker, daemon=True) for _ in range(count)]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(15)
    assert not any(thread.is_alive() for thread in threads) and not errors, errors
    return results


def test_http_concurrent_idempotent_intake_and_finalize_create_one_effect(world, queue):
    settings(world)
    _, headers = http_token(world)
    variables = {"input": request_wire(world)}
    results = parallel_http(
        headers,
        "mutation($input:RequestModelConnectionInput!){requestModelConnection(input:$input){ok errors{code} data{id version}}}",
        variables,
    )
    assert all(
        not reply.get("errors") and reply["data"]["requestModelConnection"]["ok"] for reply in results
    ), results
    assert (
        ModelConnectionRequest.objects.count() == 1
        and not ManagedServiceAttachment.objects.exists()
        and queue == []
    )
    row = ModelConnectionRequest.objects.get()
    vote(world, row, reviewer(world, "parallel-finalize"))
    results = parallel_http(
        headers,
        "mutation($input:DecideModelConnectionRequestInput!){finalizeModelConnectionRequest(input:$input){ok errors{code} data{subscriptionId}}}",
        {"input": {"id": str(row.guid), "ifMatchVersion": row.version}},
    )
    assert all(
        not reply.get("errors") and reply["data"]["finalizeModelConnectionRequest"]["ok"] for reply in results
    ), results
    assert (
        len({reply["data"]["finalizeModelConnectionRequest"]["data"]["subscriptionId"] for reply in results})
        == 1
    )
    assert ManagedServiceAttachment.objects.count() == 1 and len(queue) == 1


def test_http_concurrent_same_human_votes_once(world, queue):
    from astrolift_services.models import ModelConnectionApproval

    settings(world, quorum=2)
    row = create_request(world)
    actor = reviewer(world, "parallel-vote")
    _, headers = http_token(world, actor=actor, scopes=("admin",))
    results = parallel_http(
        headers,
        "mutation($input:DecideModelConnectionRequestInput!){approveModelConnectionRequest(input:$input){ok errors{code} data{status approvalCount}}}",
        {"input": {"id": str(row.guid), "ifMatchVersion": row.version}},
    )
    assert all(
        not reply.get("errors") and reply["data"]["approveModelConnectionRequest"]["ok"] for reply in results
    ), results
    row.refresh_from_db()
    assert row.status == "pending" and ModelConnectionApproval.objects.filter(request=row).count() == 1
    assert not ManagedServiceAttachment.objects.exists() and queue == []


@pytest.mark.parametrize("withdrawal", ["logout", "expiry", "auth_hash"])
def test_http_browser_session_withdrawn_during_actual_lock_wait(world, queue, monkeypatch, withdrawal):
    from datetime import timedelta

    from django.contrib.sessions.models import Session

    import astrolift_services.model_connection_requests as orchestration

    settings(world)
    client = Client()
    client.force_login(world.user)
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    key = client.session.session_key
    variables = {"input": request_wire(world)}
    query = (
        "mutation($input:RequestModelConnectionInput!){requestModelConnection(input:$input){ok errors{code}}}"
    )
    entered = Event()
    result = []
    errors = []
    pids = []
    original = orchestration.locked_organization

    def observed_lock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pids.append(cursor.fetchone()[0])
        entered.set()
        return original()

    monkeypatch.setattr(orchestration, "locked_organization", observed_lock)

    def worker():
        close_old_connections()
        try:
            result.append(graphql_http(client, headers, query, variables))
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    with transaction.atomic():
        type(world.org).objects.select_for_update().get(pk=world.org.pk)
        thread = Thread(target=worker, daemon=True)
        thread.start()
        assert entered.wait(10), errors
        wait_for_actual_lock(pids[0])
        if withdrawal == "logout":
            Session.objects.filter(session_key=key).delete()
        elif withdrawal == "expiry":
            Session.objects.filter(session_key=key).update(expire_date=timezone.now() - timedelta(seconds=1))
        else:
            world.user.set_password("new-private-test-password")
            world.user.save(update_fields=["password"])
    thread.join(15)
    assert not thread.is_alive() and not errors, errors
    assert result and not result[0].get("errors"), result
    assert not result[0]["data"]["requestModelConnection"]["ok"], result
    assert (
        not ModelConnectionRequest.objects.exists()
        and not ManagedServiceAttachment.objects.exists()
        and queue == []
    )


@pytest.mark.parametrize(
    "operation,withdrawal",
    [
        ("finalize", "source"),
        ("finalize", "policy"),
        ("finalize", "voter_role"),
        ("finalize", "voter_token"),
        ("finalize", "voter_session"),
        ("auto", "source"),
        ("auto", "policy"),
    ],
)
def test_actual_http_last_attachment_wait_rechecks_source_policy_and_current_voters(
    world, queue, monkeypatch, operation, withdrawal
):
    from django.contrib.sessions.models import Session
    from django.db.models.query import QuerySet

    from astrolift_services.models import HuggingFaceConnection, ModelConnectionApproval

    connection_source = HuggingFaceConnection.objects.create(
        organization=world.org,
        name="bounded source",
        account_username="synthetic-reader",
        secret_backend_kind="local",
        secret_ciphertext=b"private-test-bytes",
        verified_at=timezone.now(),
    )
    world.model.model_hf_connection = connection_source
    world.model.model_hf_connection_version = connection_source.version
    world.model.save()
    settings(world, "AUTO" if operation == "auto" else "REQUIRE_APPROVAL")
    pending = None
    if operation == "finalize":
        pending = create_request(world)
        actor = reviewer(world, "last-lock-voter")
        if withdrawal == "voter_session":
            # Record a genuine authenticated browser vote, retaining its current session.
            voter_client = Client()
            voter_client.force_login(actor)
            voter_headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
            # Login response normally records this sidecar; force_login bypasses a view.
            graphql_http(voter_client, voter_headers, "query{__typename}", {})
            approved = graphql_http(
                voter_client,
                voter_headers,
                "mutation($input:DecideModelConnectionRequestInput!){approveModelConnectionRequest(input:$input){ok errors{code}}}",
                {"input": {"id": str(pending.guid), "ifMatchVersion": pending.version}},
            )
            assert (
                not approved.get("errors") and approved["data"]["approveModelConnectionRequest"]["ok"]
            ), approved
            pending.refresh_from_db()
        else:
            vote(world, pending, actor)
        _, headers = http_token(world)
        query = "mutation($input:DecideModelConnectionRequestInput!){finalizeModelConnectionRequest(input:$input){ok errors{code} data{status subscriptionId}}}"
        variables = {"input": {"id": str(pending.guid), "ifMatchVersion": pending.version}}
        field = "finalizeModelConnectionRequest"
    else:
        _, headers = http_token(world, scopes=("admin",))
        query = "mutation($input:SubscribeClusterModelInput!){subscribeClusterModel(input:$input){ok errors{code}}}"
        wire = request_wire(world)
        variables = {
            "input": {
                key: value
                for key, value in wire.items()
                if key not in {"ifMatchAppVersion", "policyVersion", "idempotencyKey"}
            }
        }
        field = "subscribeClusterModel"
    prior = ManagedServiceAttachment.objects.create(
        managed_service=world.model,
        app_environment=world.env,
        model_subscription=True,
        binding_alias="chat",
        subscription_status="revoked",
        desired_enabled=False,
    )
    entered = Event()
    pids = []
    results = []
    errors = []
    original_first = QuerySet.first

    def observed_first(qs):
        if qs.model is ManagedServiceAttachment and qs.query.select_for_update:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.append(cursor.fetchone()[0])
            entered.set()
        return original_first(qs)

    monkeypatch.setattr(QuerySet, "first", observed_first)

    def worker():
        close_old_connections()
        try:
            results.append(graphql_http(Client(), headers, query, variables))
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
        if withdrawal == "source":
            HuggingFaceConnection.objects.filter(pk=connection_source.pk).update(deleted_at=timezone.now())
        elif withdrawal == "policy":
            Policy.objects.create(
                organization=world.org,
                name="last lock deny",
                slug="last-lock-deny",
                scope_level="ORG",
                effect="DENY",
                action_pattern="app.update",
            )
        elif withdrawal == "voter_role":
            RoleBinding.objects.filter(user=actor).update(deleted_at=timezone.now())
        elif withdrawal == "voter_token":
            record = ModelConnectionApproval.objects.get(request=pending)
            type(record.api_token).objects.filter(pk=record.api_token_id).update(is_revoked=True)
        else:
            Session.objects.filter(session_key=voter_client.session.session_key).delete()
    thread.join(15)
    assert not thread.is_alive() and not errors, errors
    assert results and not results[0].get("errors"), results
    payload = results[0]["data"][field]
    assert not payload["ok"] or (operation == "finalize" and payload["data"]["status"] == "STALE"), results
    prior.refresh_from_db()
    world.model.refresh_from_db()
    assert prior.deleted_at is None and ManagedServiceAttachment.objects.count() == 1
    assert world.model.subscription_revision == 0 and world.model.status == "active" and queue == []


@pytest.mark.parametrize(
    "operation,withdrawal", [("duplicate", "scope"), ("approve", "role"), ("approve", "policy")]
)
def test_actual_http_last_request_row_wait_rechecks_admission_and_policy(
    world, queue, monkeypatch, operation, withdrawal
):
    from django.db.models.query import QuerySet

    from astrolift_services.models import ModelConnectionApproval

    settings(world)
    row = create_request(world)
    if operation == "duplicate":
        token, headers = http_token(world)
        wire = request_wire(world)
        wire["idempotencyKey"] = str(row.idempotency_key)
        query = "mutation($input:RequestModelConnectionInput!){requestModelConnection(input:$input){ok errors{code} data{status}}}"
        variables = {"input": wire}
        field = "requestModelConnection"
    else:
        actor = reviewer(world, "request-row-wait")
        token, headers = http_token(world, actor=actor, scopes=("admin",))
        query = "mutation($input:DecideModelConnectionRequestInput!){approveModelConnectionRequest(input:$input){ok errors{code} data{status}}}"
        variables = {"input": {"id": str(row.guid), "ifMatchVersion": row.version}}
        field = "approveModelConnectionRequest"
    entered = Event()
    pids = []
    results = []
    errors = []
    first = QuerySet.first

    def observed_first(qs):
        if qs.model is ModelConnectionRequest and qs.query.select_for_update:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.append(cursor.fetchone()[0])
            entered.set()
        return first(qs)

    monkeypatch.setattr(QuerySet, "first", observed_first)

    def worker():
        close_old_connections()
        try:
            results.append(graphql_http(Client(), headers, query, variables))
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    with transaction.atomic():
        ModelConnectionRequest.objects.select_for_update().get(pk=row.pk)
        thread = Thread(target=worker, daemon=True)
        thread.start()
        assert entered.wait(10), errors
        wait_for_actual_lock(pids[0])
        if withdrawal == "scope":
            type(token).objects.filter(pk=token.pk).update(scopes=["read:apps"])
        elif withdrawal == "role":
            RoleBinding.objects.filter(user=actor).update(deleted_at=timezone.now())
        else:
            Policy.objects.create(
                organization=world.org,
                name="request row policy",
                slug="request-row-policy",
                scope_level="ORG",
                effect="DENY",
                action_pattern="app.update",
            )
    thread.join(15)
    assert not thread.is_alive() and not errors, errors
    assert results and not results[0].get("errors"), results
    payload = results[0]["data"][field]
    assert not payload["ok"] or payload["data"]["status"] == "STALE", results
    assert not ModelConnectionApproval.objects.exists() and ModelConnectionRequest.objects.count() == 1
    assert not ManagedServiceAttachment.objects.exists() and queue == []


def test_actual_http_org_policy_wait_reloads_persisted_authentication_facts(world, queue, monkeypatch):
    from django.contrib.sessions.backends.db import SessionStore

    import astrolift_services.schema.model_connections as api
    from astrolift_identity.step_up_sso import SESSION_SSO_AUTH_TIME_KEY

    actor = reviewer(world, "policy-freshness")
    Policy.objects.create(
        organization=world.org,
        name="fresh settings",
        slug="fresh-settings",
        scope_level="ORG",
        effect="ALLOW",
        action_pattern="org.update",
        conditions=[{"kind": "freshness", "max_session_age_minutes": 1}],
    )
    client = Client()
    client.force_login(actor)
    store = client.session
    store[SESSION_SSO_AUTH_TIME_KEY] = int(timezone.now().timestamp())
    store.save()
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    query = "mutation($input:UpdateOrganizationModelConnectionPolicyInput!){updateOrganizationModelConnectionPolicy(input:$input){ok errors{code}}}"
    variables = {
        "input": {
            "organizationId": str(world.org.guid),
            "ifMatchVersion": 0,
            "mode": "REQUIRE_APPROVAL",
            "requiredApprovals": 1,
            "allowSelfApproval": False,
        }
    }
    entered = Event()
    pids = []
    results = []
    errors = []
    original = api.locked_organization

    def observed_lock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pids.append(cursor.fetchone()[0])
        entered.set()
        return original()

    monkeypatch.setattr(api, "locked_organization", observed_lock)

    def worker():
        close_old_connections()
        try:
            results.append(graphql_http(client, headers, query, variables))
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    with transaction.atomic():
        type(world.org).objects.select_for_update().get(pk=world.org.pk)
        thread = Thread(target=worker, daemon=True)
        thread.start()
        assert entered.wait(10), errors
        wait_for_actual_lock(pids[0])
        fresh = SessionStore(store.session_key)
        fresh[SESSION_SSO_AUTH_TIME_KEY] = int(timezone.now().timestamp()) - 600
        fresh.save()
    thread.join(15)
    assert not thread.is_alive() and not errors, errors
    assert results and not results[0].get("errors"), results
    assert not results[0]["data"]["updateOrganizationModelConnectionPolicy"]["ok"], results
    from astrolift_services.models import ModelConnectionPolicy

    assert (
        not ModelConnectionPolicy.objects.exists()
        and not ManagedServiceAttachment.objects.exists()
        and queue == []
    )


@pytest.mark.parametrize("operation", ["request", "approve"])
@pytest.mark.parametrize("withdrawal", ["revoked", "expired", "deleted", "foreign_actor"])
def test_actual_http_sidecar_withdrawal_after_lock_remains_withdrawn_after_response(
    world, queue, client, monkeypatch, operation, withdrawal
):
    from datetime import timedelta

    import astrolift_services.model_connection_requests as orchestration
    from astrolift_identity.models import AstroliftSession
    from astrolift_services.models import ModelConnectionApproval

    settings(world)
    if operation == "approve":
        pending = create_request(world)
        actor = reviewer(world, "sidecar-revalidation")
        query = "mutation($input:DecideModelConnectionRequestInput!){approveModelConnectionRequest(input:$input){ok errors{code}}}"
        variables = {"input": {"id": str(pending.guid), "ifMatchVersion": pending.version}}
        field = "approveModelConnectionRequest"
    else:
        actor = world.user
        query = "mutation($input:RequestModelConnectionInput!){requestModelConnection(input:$input){ok errors{code}}}"
        variables = {"input": request_wire(world)}
        field = "requestModelConnection"
    client.force_login(actor)
    headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    graphql_http(client, headers, "query{__typename}", {})
    sidecar = AstroliftSession.objects.get(session_key=client.session.session_key)
    assert sidecar.user_id == actor.pk and sidecar.revoked_at is None
    results, errors, pids = [], [], []
    entered = Event()
    original = orchestration.locked_organization

    def observed_lock():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pids.append(cursor.fetchone()[0])
        entered.set()
        return original()

    monkeypatch.setattr(orchestration, "locked_organization", observed_lock)

    def worker():
        close_old_connections()
        try:
            results.append(graphql_http(client, headers, query, variables))
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    with transaction.atomic():
        type(world.org).objects.select_for_update().get(pk=world.org.pk)
        thread = Thread(target=worker, daemon=True)
        thread.start()
        assert entered.wait(10), errors
        wait_for_actual_lock(pids[0])
        value = timezone.now()
        change = {
            "revoked": {"revoked_at": value},
            "expired": {"expires_at": value - timedelta(seconds=1)},
            "deleted": {"deleted_at": value},
            "foreign_actor": {"user_id": reviewer(world, "foreign-sidecar").pk},
        }[withdrawal]
        AstroliftSession.all_objects.filter(pk=sidecar.pk).update(**change)
        expected = (
            AstroliftSession.all_objects.filter(pk=sidecar.pk)
            .values("user_id", "revoked_at", "expires_at", "deleted_at")
            .get()
        )
    thread.join(15)
    assert not thread.is_alive() and not errors, errors
    assert results and not results[0].get("errors"), results
    assert not results[0]["data"][field]["ok"], results
    assert (
        AstroliftSession.all_objects.filter(pk=sidecar.pk)
        .values("user_id", "revoked_at", "expires_at", "deleted_at")
        .get()
        == expected
    )
    assert not ModelConnectionApproval.objects.exists()
    if operation == "request":
        assert not ModelConnectionRequest.objects.exists()
    else:
        pending.refresh_from_db()
        assert pending.status == "pending"
    assert not ManagedServiceAttachment.objects.exists() and queue == []
