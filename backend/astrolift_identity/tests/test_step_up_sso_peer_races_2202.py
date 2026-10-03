from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import SESSION_KEY, get_user_model, login
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import close_old_connections, connection, transaction
from django.test import RequestFactory
from django.utils import timezone

from astrolift_identity import step_up_sso
from astrolift_identity.session_elevation import is_elevated
from astrolift_identity.tests import test_step_up_sso_binding_2202 as native_proof
from auth1.models import UserInfo

pytestmark = pytest.mark.django_db(transaction=True)
oidc = native_proof.oidc


def _authenticated_ceremony(oidc):
    actor = get_user_model().objects.create_user(username="peer-sso-actor")
    UserInfo.objects.create(
        sub="peer-owned-subject",
        iss=oidc.issuer,
        internal_user=actor,
        updated_at=timezone.now(),
        email_verified=True,
        iat=int(time.time()),
        exp=int(time.time()) + 300,
    )
    session = SessionStore()
    session.create()
    factory = RequestFactory()
    start = factory.get("/app/auth1/elevate-sso/?return=/app/admin/")
    start.user, start.session = actor, session
    login(start, actor, backend="django.contrib.auth.backends.ModelBackend")
    session.save()
    response = step_up_sso.elevate_sso_start(start)
    assert response.status_code == 302
    session.save()
    query = parse_qs(urlparse(response["Location"]).query)
    now = int(time.time())
    oidc.claims = {
        "sub": "peer-owned-subject",
        "iss": oidc.issuer,
        "aud": "native-client",
        "nonce": query["nonce"][0],
        "iat": now,
        "exp": now + 300,
        "auth_time": now,
    }
    callback = factory.get(
        "/app/auth1/elevate-sso/callback/", {"state": query["state"][0], "code": "peer-native-code"}
    )
    callback.user, callback.session = actor, SessionStore(session_key=session.session_key)
    assert callback.session[SESSION_KEY] == str(actor.pk)
    return actor, callback


def _callback_at_actor_lock(request, waiting):
    close_old_connections()
    table = get_user_model()._meta.db_table

    def observed_lock(execute, sql, params, many, context):
        if "FOR UPDATE" in sql and f'"{table}"' in sql:
            waiting.set()
        return execute(sql, params, many, context)

    try:
        with connection.execute_wrapper(observed_lock):
            response = step_up_sso.elevate_sso_callback(request)
            return SessionMiddleware(lambda request: response).process_response(request, response)
    finally:
        connection.close()


def test_current_session_logout_cannot_be_undone_by_a_verified_callback(oidc):
    actor, request = _authenticated_ceremony(oidc)
    session_key = request.session.session_key
    waiting = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=actor.pk)
            callback = pool.submit(_callback_at_actor_lock, request, waiting)
            assert waiting.wait(10), "Native verified callback did not reach the actor lock."
            assert not callback.done()
            # Auth1SessionWorkflow.logout clears the bag; response middleware
            # persists the empty session without replacing its existing key.
            signed_out = SessionStore(session_key=session_key)
            signed_out.clear()
            signed_out.save()
            assert SessionStore(session_key=session_key).get(SESSION_KEY) is None
        response = callback.result(timeout=10)
    persisted = SessionStore(session_key=session_key)
    assert persisted.get(SESSION_KEY) is None, "Callback restored an authenticated session after logout."
    assert not is_elevated(persisted), "Callback elevated a session cleared by concurrent logout."
    assert "stepUp=" in response["Location"]
    assert oidc.calls == 1


def test_verified_proof_must_still_be_fresh_after_waiting_for_actor_lock(oidc, monkeypatch):
    actor, request = _authenticated_ceremony(oidc)
    clock = [time.time()]
    monkeypatch.setattr(step_up_sso, "time", SimpleNamespace(time=lambda: clock[0]))
    window = step_up_sso.freshness_window_seconds()
    waiting = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=actor.pk)
            callback = pool.submit(_callback_at_actor_lock, request, waiting)
            assert waiting.wait(10), "Native verified callback did not reach the actor lock."
            assert not callback.done()
            clock[0] += window + 2
        response = callback.result(timeout=10)
    assert not is_elevated(SessionStore(session_key=request.session.session_key))
    assert "stepUp=stale_auth_time" in response["Location"]
    assert oidc.calls == 1


@pytest.mark.parametrize("save_every_request", [False, True])
def test_logout_after_callback_commit_survives_response_middleware(oidc, settings, save_every_request):
    settings.SESSION_SAVE_EVERY_REQUEST = save_every_request
    _, request = _authenticated_ceremony(oidc)
    session_key = request.session.session_key
    response = step_up_sso.elevate_sso_callback(request)
    assert response["Location"] == "/app/admin/"
    assert is_elevated(SessionStore(session_key=session_key))

    # Interleave a completed logout after the guarded callback save/commit,
    # before Django processes that callback's response.
    signed_out = SessionStore(session_key=session_key)
    signed_out.clear()
    signed_out.save()
    assert SessionStore(session_key=session_key).get(SESSION_KEY) is None
    SessionMiddleware(lambda request: response).process_response(request, response)

    persisted = SessionStore(session_key=session_key)
    assert persisted.get(SESSION_KEY) is None, "Response middleware restored a logged-out session."
    assert not is_elevated(persisted)
    assert oidc.calls == 1
