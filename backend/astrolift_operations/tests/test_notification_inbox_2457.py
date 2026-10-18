from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from asgiref.sync import sync_to_async
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.utils import timezone
from graphql import GraphQLError

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member
from astrolift_operations.models import Notification, NotificationInboxCounter, UserAlertSubscription
from astrolift_operations.schema.mutations import MarkNotificationReadInput, OperationsMutation
from astrolift_operations.schema.notification_inbox import NotificationInboxSubscription
from astrolift_operations.schema.queries import OperationsQuery
from astrolift_registry import expiry
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_external_delivery(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, row: None))
    monkeypatch.setattr("astrolift_scm.services.autowire.run_autowire", lambda *args, **kwargs: None)
    monkeypatch.setattr("astrolift_operations.notification_email.notifications_email_enabled", lambda: False)


@pytest.fixture
def world():
    value = ScopeWorld("inbox2457")
    value.user = make_user("inbox2457")
    for kind, scope in [("ORG", value.org.pk), ("TEAM", value.medops.pk)]:
        Member.objects.create(user=value.user, scope_kind=kind, scope_id=scope, is_active=True)
    bind_role(
        value.user, permissions=[Permission.APP_READ], kind="ORG", scope_id=value.org.pk, slug="inbox-reader"
    )
    return value


@contextmanager
def actor(world, *, user=None, token=None):
    user = user or world.user
    with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=user.pk)):
        marker = set_current_api_token(token)
        try:
            yield make_info(user)
        finally:
            reset_current_api_token(marker)


def notice(world, **kwargs):
    return Notification.objects.create(
        organization=world.org,
        user=world.user,
        kind=Notification.Kind.SYSTEM,
        title="A real notification",
        **kwargs,
    )


def token_for(world, *, scopes=None, team=None):
    return ApiToken.objects.create(
        organization=world.org,
        user=world.user,
        team=team,
        name="Inbox reader",
        token_hash=hashlib.sha256(str(timezone.now()).encode()).hexdigest(),
        token_prefix="alft_inbox",
        scopes=scopes if scopes is not None else ["read:apps"],
    )


def page(world, *, token=None, **kwargs):
    with actor(world, token=token) as info:
        return OperationsQuery().astrolift_my_notifications_page(info, **kwargs)


def warn(world, remaining=dt.timedelta(minutes=45)):
    app = world.medops_app
    app.expires_at = timezone.now() + remaining
    app.save(update_fields=["expires_at", "updated_at", "version"])
    expiry.next_step(app.pk)
    return Notification.objects.get(user=world.user, kind="app_expiring")


def test_actual_warning_provenance_missed_window_and_extension(world):
    original = warn(world)
    first = page(world)
    assert [str(row.id) for row in first.items] == [str(original.guid)]
    warning = first.items[0].expiry_warning
    assert str(warning.app_id) == str(world.medops_app.guid)
    assert warning.app_slug == world.medops_app.slug
    assert warning.warning_window == "1h"
    assert warning.expires_at == world.medops_app.expires_at
    world.medops_app.refresh_from_db()
    assert set(world.medops_app.expiry_warnings_sent) == {"24h", "1h"}
    expiry.next_step(world.medops_app.pk)
    assert Notification.objects.count() == 1

    world.medops_app.expires_at += dt.timedelta(hours=20)
    world.medops_app.expiry_warnings_sent = []
    world.medops_app.save(update_fields=["expires_at", "expiry_warnings_sent", "updated_at", "version"])
    expiry.next_step(world.medops_app.pk)
    second = page(world, after=first.end_cursor)
    assert len(second.items) == 1
    assert str(second.items[0].id) != str(original.guid)
    assert second.items[0].expiry_warning.warning_window == "24h"
    assert second.items[0].expiry_warning.expires_at > warning.expires_at
    assert page(world).items[0].expiry_warning.expires_at == warning.expires_at


def test_cursor_walk_dedup_empty_progress_and_read_state(world):
    rows = [notice(world) for _ in range(3)]
    a = page(world, limit=1)
    b = page(world, after=a.end_cursor, limit=1)
    replay = page(world, after=a.end_cursor, limit=1)
    c = page(world, after=b.end_cursor, limit=1)
    assert [str(p.items[0].id) for p in (a, b, c)] == [str(row.guid) for row in rows]
    assert replay == b
    assert a.has_more and b.has_more and not c.has_more
    empty = page(world, after=c.end_cursor)
    assert empty.items == [] and empty.end_cursor == c.end_cursor
    with actor(world) as info:
        marked = OperationsMutation().mark_notification_read(
            info, MarkNotificationReadInput(id=str(rows[0].guid))
        )
        assert marked.ok
    assert Notification.objects.filter(read_at__isnull=True).count() == 2
    assert page(world, after=c.end_cursor).items == []


def test_actual_recipient_only_tenant_cursor_and_mutations(world):
    own = notice(world)
    other_user = make_user("inbox-other")
    Notification.objects.create(organization=world.org, user=other_user, kind="system", title="private")
    other = ScopeWorld("inbox-foreign")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=other.org.pk)
    foreign = Notification.objects.create(
        organization=other.org, user=world.user, kind="system", title="foreign"
    )
    assert [str(row.id) for row in page(world).items] == [str(own.guid)]
    with actor(world) as info:
        assert (
            not OperationsMutation()
            .mark_notification_read(info, MarkNotificationReadInput(id=str(foreign.guid)))
            .ok
        )
        assert OperationsMutation().mark_all_notifications_read(info).ok
    foreign.refresh_from_db()
    assert foreign.read_at is None
    cursor = page(world).end_cursor
    with actor(other, user=world.user) as info:
        with pytest.raises(GraphQLError):
            OperationsQuery().astrolift_my_notifications_page(info, after=cursor)


def test_graphql_query_schema_and_unauthenticated_denial(world):
    from config.schema import schema

    original = warn(world)
    query = """{ astroliftMyNotificationsPage(limit: 10) {
      items { id kind expiryWarning { appId appSlug expiresAt warningWindow } }
      endCursor hasMore
    } }"""
    with actor(world) as info:
        result = schema.execute_sync(query, context_value=info.context)
    assert result.errors is None
    assert result.data["astroliftMyNotificationsPage"]["items"][0]["id"] == str(original.guid)
    result = schema.execute_sync(query, context_value=make_info(None).context)
    assert result.errors


def test_cursor_install_person_and_changed_ceiling_are_rejected(world, monkeypatch):
    notice(world)
    token = token_for(world)
    first = page(world, token=token)
    with pytest.raises(GraphQLError, match="cursor"):
        page(world, token=token, after=first.end_cursor + "bad")
    monkeypatch.setenv("ASTROLIFT_INSTALL_ID", "another-install")
    with pytest.raises(GraphQLError, match="cursor"):
        page(world, token=token, after=first.end_cursor)
    monkeypatch.delenv("ASTROLIFT_INSTALL_ID")
    ApiToken.objects.filter(pk=token.pk).update(team=world.medops)
    with pytest.raises(GraphQLError, match="cursor"):
        page(world, token=token, after=first.end_cursor)


def test_inbox_ignores_external_preferences_and_role_revocation_hides_warning(world):
    from astrolift_identity.models import RoleBinding
    from astrolift_operations.models import NotificationPreference

    warn(world)
    NotificationPreference.objects.create(
        user=world.user,
        channel="push",
        event_type="app.expiring",
        enabled=False,
    )
    assert len(page(world).items) == 1
    RoleBinding.objects.filter(user=world.user).update(deleted_at=timezone.now())
    assert page(world).items == []


@pytest.mark.parametrize("scopes", [[], ["mcp:read"], ["read:clusters"]])
def test_wrong_token_scope_is_explicitly_rejected(world, scopes):
    notice(world)
    with pytest.raises(GraphQLError, match="authorization"):
        page(world, token=token_for(world, scopes=scopes))


def test_team_ceiling_current_audience_and_muting(world):
    warning = warn(world)
    wrong_team = token_for(world, team=world.platform)
    assert page(world, token=wrong_team).items == []
    good_team = token_for(world, team=world.medops)
    assert len(page(world, token=good_team).items) == 1
    cursor = page(world, token=good_team).end_cursor
    with pytest.raises(GraphQLError, match="cursor"):
        page(world, token=wrong_team, after=cursor)
    UserAlertSubscription.objects.create(
        user=world.user,
        registered_app=world.medops_app,
        alert_kind="app_expiring",
        enabled=False,
    )
    muted = page(world)
    assert muted.items == []
    assert muted.end_cursor == page(world, after=None, limit=1).end_cursor
    UserAlertSubscription.objects.all().update(enabled=True)
    Member.objects.filter(user=world.user, scope_kind="TEAM").update(is_active=False)
    assert page(world).items == []
    warning.refresh_from_db()
    assert warning.read_at is None


def test_revoked_rows_advance_cursor_and_old_unstructured_warning_is_not_guessed(world):
    old = Notification.objects.create(
        organization=world.org, user=world.user, kind="app_expiring", title="Old warning"
    )
    final = notice(world)
    first = page(world, limit=1)
    assert first.items == [] and first.has_more
    second = page(world, after=first.end_cursor, limit=1)
    assert str(second.items[0].id) == str(final.guid)
    assert old.expiry_warning is None


def test_bulk_and_old_writer_sql_and_deletion_never_reuse_sequence(world):
    bulk = [
        Notification(organization=world.org, user=world.user, kind="system", title="bulk") for _ in range(2)
    ]
    Notification.objects.bulk_create(bulk)
    assert [row.inbox_sequence for row in bulk] == [1, 2]
    n = notice(world)
    assert n.inbox_sequence == 3
    n.soft_delete()
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM astrolift_operations_notification WHERE id = %s", [n.pk])
    # Simulate a rolling old binary that does not name either new column.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO astrolift_operations_notification
            (guid, organization_id, user_id, kind, title, body, link, created_at, updated_at, version)
            VALUES (gen_random_uuid(), %s, %s, 'system', 'old writer', '', '', now(), now(), 0)
            RETURNING inbox_sequence, expiry_warning
        """,
            [world.org.pk, world.user.pk],
        )
        assert cursor.fetchone() == (4, None)
    assert NotificationInboxCounter.objects.get(organization=world.org, user=world.user).last_sequence == 4


@pytest.mark.parametrize("missing", ["user_id", "organization_id"])
def test_system_notifications_still_require_recipient_and_tenant(world, missing):
    fields = {"organization_id": world.org.pk, "user_id": world.user.pk, missing: None}
    with pytest.raises(IntegrityError), transaction.atomic():
        Notification.objects.create(kind="system", title="No missing audience", **fields)


async def test_missing_stream_identity_is_an_explicit_error():
    stream = NotificationInboxSubscription().astrolift_my_notification_stream(make_info(None))
    with pytest.raises(GraphQLError, match="authorization"):
        await anext(stream)
    await stream.aclose()


@pytest.mark.django_db(transaction=True)
def test_late_commit_cannot_be_skipped_by_cursor(world):
    inserted, release, second_started = threading.Event(), threading.Event(), threading.Event()

    def first():
        close_old_connections()
        try:
            with transaction.atomic():
                row = notice(world)
                inserted.set()
                assert release.wait(10)
            return row.inbox_sequence
        finally:
            close_old_connections()

    def second():
        close_old_connections()
        try:
            second_started.set()
            return notice(world).inbox_sequence
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first)
        assert inserted.wait(10)
        two = pool.submit(second)
        assert second_started.wait(10)
        empty = page(world)
        assert empty.items == []
        release.set()
        assert one.result(timeout=10) == 1
        assert two.result(timeout=10) == 2
    assert len(page(world, after=empty.end_cursor).items) == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("revocation", ["token", "rotation", "membership", "audience", "mute"])
async def test_stream_rechecks_after_connection(world, monkeypatch, revocation):
    monkeypatch.setattr("astrolift_operations.schema.notification_inbox.POLL_SECONDS", 0.01)
    token = await sync_to_async(token_for)(world)
    first_notice = await sync_to_async(warn)(world)
    tenant = TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)
    info = make_info(world.user)
    info.context._ws_tenant = tenant
    info.context._ws_api_token = token
    stream = NotificationInboxSubscription().astrolift_my_notification_stream(info)
    first_page = await anext(stream)
    assert str(first_page.items[0].id) == str(first_notice.guid)

    def change():
        if revocation == "token":
            ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
        elif revocation == "rotation":
            ApiToken.objects.filter(pk=token.pk).update(token_hash=hashlib.sha256(b"rotated").hexdigest())
        elif revocation == "membership":
            Member.objects.filter(user=world.user, scope_kind="ORG").update(is_active=False)
        elif revocation == "audience":
            Member.objects.filter(user=world.user, scope_kind="TEAM").update(is_active=False)
        else:
            UserAlertSubscription.objects.create(
                user=world.user,
                registered_app=world.medops_app,
                alert_kind="app_expiring",
                enabled=False,
            )
        return Notification.objects.create(
            organization=world.org,
            user=world.user,
            kind="app_expiring",
            title="next",
            expiry_warning=first_notice.expiry_warning,
        )

    await sync_to_async(change)()
    try:
        if revocation in {"token", "rotation", "membership"}:
            with pytest.raises(GraphQLError, match="authorization"):
                await asyncio.wait_for(anext(stream), timeout=5)
        else:
            next_page = await asyncio.wait_for(anext(stream), timeout=5)
            assert next_page.items == [] and next_page.end_cursor != first_page.end_cursor
    finally:
        await stream.aclose()


@pytest.mark.django_db(transaction=True)
async def test_durable_expiry_workflow_warning_reaches_actual_inbox(world, temporal_env):
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    from astrolift_registry.models import RegisteredApp
    from astrolift_workflows.activities.app_expiry import app_expiry_next_step
    from astrolift_workflows.inputs import AppExpiryInput
    from astrolift_workflows.workflows.app_expiry import AppExpiryWorkflow

    await sync_to_async(RegisteredApp.objects.filter(pk=world.medops_app.pk).update)(
        expires_at=timezone.now() + dt.timedelta(minutes=30),
    )
    queue = f"inbox2457-{uuid.uuid4()}"
    async with Worker(
        temporal_env.client,
        task_queue=queue,
        workflows=[AppExpiryWorkflow],
        activities=[app_expiry_next_step],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        handle = await temporal_env.client.start_workflow(
            AppExpiryWorkflow.run,
            AppExpiryInput(registered_app_id=world.medops_app.pk),
            id=queue,
            task_queue=queue,
        )
        try:
            for _ in range(100):
                received = await sync_to_async(page)(world)
                if received.items:
                    break
                await asyncio.sleep(0.05)
            assert len(received.items) == 1
            assert received.items[0].expiry_warning.warning_window == "1h"
            original = await sync_to_async(Notification.objects.get)(user=world.user, kind="app_expiring")
            assert str(received.items[0].id) == str(original.guid)
            await sync_to_async(RegisteredApp.objects.filter(pk=world.medops_app.pk).update)(expires_at=None)
            await handle.signal(AppExpiryWorkflow.reschedule)
            assert (await asyncio.wait_for(handle.result(), timeout=20)).ok
        finally:
            description = await handle.describe()
            if description.status.name == "RUNNING":
                await handle.cancel()
