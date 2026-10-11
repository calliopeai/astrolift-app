"""Real PG lock waits with actual HTTP admission after withdrawal."""

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import close_old_connections, connection, transaction
from django.test import Client

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import (
    ApiToken,
    AstroliftSession,
    Member,
    RoleBinding,
    Team,
    TeamMembershipAction,
)

from .test_team_memberships_2273 import CHANGE, command, http, reviewed
from .test_team_memberships_2273 import world as world_fixture

pytestmark = pytest.mark.django_db(transaction=True)

world = world_fixture


def wait_for_team_lock():
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query LIKE '%%astrolift_identity_team%%'"
            )
            if cursor.fetchone()[0]:
                return
        time.sleep(0.01)
    raise AssertionError("owned HTTP operation did not reach the held team row lock")


def request_in_thread(w, input, client, bearer=None):
    close_old_connections()
    try:
        return http(w, CHANGE, {"input": input}, client=client, bearer=bearer)
    finally:
        close_old_connections()


@pytest.mark.parametrize(
    "withdrawal",
    [
        "member",
        "actor",
        "role-binding",
        "token-revoked",
        "token-expired",
        "session-deleted",
        "password-changed",
        "elevation-withdrawn",
        "sidecar-revoked",
        "sidecar-expired",
        "sidecar-deleted",
        "sidecar-foreign",
    ],
)
def test_post_lock_fresh_http_admission_refuses_withdrawn_authority(world, withdrawal, request):
    w = world
    input = command(w, reviewed(w))
    if withdrawal == "elevation-withdrawn":
        from constance.test import override_config

        from astrolift_identity.session_elevation import METHOD_PASSWORD, elevate

        config_scope = ExitStack()
        config_scope.enter_context(override_config(REQUIRE_STEP_UP_AUTH=True))
        request.addfinalizer(config_scope.close)
        session = w.client.session
        elevate(session, method=METHOD_PASSWORD)
        session.save()
    client = Client()
    client.cookies = w.client.cookies.copy()
    bearer = None
    token = None
    if withdrawal.startswith("token"):
        issued = mint_token()
        bearer = issued.plaintext
        token = ApiToken.objects.create(
            user=w.actor, organization=w.org, name="Race", token_hash=issued.token_hash, scopes=["admin"]
        )
        client = Client()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Team.objects.select_for_update().get(pk=w.team.pk)
            future = pool.submit(request_in_thread, w, input, client, bearer)
            wait_for_team_lock()
            if withdrawal == "member":
                Member.objects.filter(pk=w.actor_member.pk).update(is_active=False)
            elif withdrawal == "actor":
                get_user_model().objects.filter(pk=w.actor.pk).update(is_active=False)
            elif withdrawal == "role-binding":
                w.actor_binding.soft_delete()
            elif withdrawal == "token-revoked":
                ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
            elif withdrawal == "token-expired":
                from datetime import timedelta

                from django.utils import timezone

                ApiToken.objects.filter(pk=token.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
            elif withdrawal.startswith("sidecar"):
                from datetime import timedelta

                from django.utils import timezone

                row = AstroliftSession.all_objects.get(session_key=w.client.session.session_key)
                if withdrawal == "sidecar-revoked":
                    AstroliftSession.all_objects.filter(pk=row.pk).update(
                        revoked_at=timezone.now(), revocation_reason="Owned withdrawal"
                    )
                elif withdrawal == "sidecar-expired":
                    AstroliftSession.all_objects.filter(pk=row.pk).update(
                        expires_at=timezone.now() - timedelta(seconds=1)
                    )
                elif withdrawal == "sidecar-deleted":
                    AstroliftSession.all_objects.filter(pk=row.pk).update(deleted_at=timezone.now())
                else:
                    AstroliftSession.all_objects.filter(pk=row.pk).update(user_id=w.subject.pk)
                before = AstroliftSession.all_objects.filter(pk=row.pk).values().get()
            elif withdrawal == "password-changed":
                user = get_user_model().objects.get(pk=w.actor.pk)
                user.set_unusable_password()
                get_user_model().objects.filter(pk=user.pk).update(password=user.password)
            elif withdrawal == "elevation-withdrawn":
                from django.contrib.sessions.backends.db import SessionStore

                from astrolift_identity.session_elevation import SESSION_KEY_ELEVATED_UNTIL

                session = SessionStore(session_key=w.client.session.session_key)
                session.pop(SESSION_KEY_ELEVATED_UNTIL, None)
                session.save()
            else:
                Session.objects.filter(session_key=w.client.session.session_key).delete()
        result = future.result(timeout=15)
    assert not result.get("errors"), result
    payload = result["data"]["changeAstroliftTeamMembership"]
    assert not payload["ok"] and payload["errors"][0]["code"] == (
        "STEP_UP_REQUIRED" if withdrawal == "elevation-withdrawn" else "PERMISSION_DENIED"
    )
    if withdrawal.startswith("sidecar"):
        assert AstroliftSession.all_objects.filter(pk=row.pk).values().get() == before
    assert not TeamMembershipAction.objects.exists()
    assert not Member.objects.filter(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk).exists()
    assert not RoleBinding.objects.filter(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk).exists()


def test_concurrent_same_original_request_commits_one_relationship_and_receipt(world):
    w = world
    input = command(w, reviewed(w))
    clients = [Client(), Client()]
    for client in clients:
        client.cookies = w.client.cookies.copy()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request_in_thread, w, input, client) for client in clients]
        results = [future.result(timeout=15) for future in futures]
    payloads = [result["data"]["changeAstroliftTeamMembership"] for result in results]
    assert all(p["ok"] for p in payloads), results
    assert len({p["data"]["changeId"] for p in payloads}) == 1
    assert sorted(p["data"]["replayed"] for p in payloads) == [False, True]
    assert TeamMembershipAction.objects.count() == 1
    assert Member.objects.filter(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk).count() == 1
    assert RoleBinding.objects.filter(user=w.subject, scope_kind="TEAM", scope_id=w.team.pk).count() == 1


def test_waited_original_receipt_cannot_replay_after_person_guid_reassignment(world):
    w = world
    input = command(w, reviewed(w))
    first = http(w, CHANGE, {"input": input})["data"]["changeAstroliftTeamMembership"]
    assert first["ok"]
    replacement = get_user_model().objects.create(username="replacement-person-2273")
    client = Client()
    client.cookies = w.client.cookies.copy()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=w.subject.pk)
            future = pool.submit(request_in_thread, w, input, client)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_stat_clear_snapshot()")
                    cursor.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query LIKE %s",
                        ["%" + get_user_model()._meta.db_table + "%"],
                    )
                    if cursor.fetchone()[0]:
                        break
                time.sleep(0.01)
            else:
                raise AssertionError("owned receipt replay did not reach held User lock")
            Member.objects.filter(pk=w.member.pk).update(user_id=replacement.pk)
        payload = future.result(timeout=15)["data"]["changeAstroliftTeamMembership"]
    assert not payload["ok"] and payload["errors"][0]["code"] == "CONFLICT"
    assert TeamMembershipAction.objects.count() == 1
    assert not Member.objects.filter(user=replacement, scope_kind="TEAM").exists()
    assert not RoleBinding.objects.filter(user=replacement).exists()
