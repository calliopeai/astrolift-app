"""Reviewed exact-resource writes: real PostgreSQL locks and fresh grants."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from django.db import close_old_connections, connection, transaction
from django.db.models import F

from astrolift_graphql import GUID
from astrolift_services.models import ManagedService, ManagedServiceAttachment
from astrolift_services.schema import resource_actions
from astrolift_services.schema.mutations import ServicesMutation
from astrolift_services.schema.mutations.types import (
    AttachProjectManagedServiceInput,
    DeprovisionManagedServiceInput,
    DetachProjectManagedServiceInput,
    ReprovisionManagedServiceInput,
    UpdateManagedServiceInput,
)
from core.permissions import Permission
from core.tests.utils.scope_world import make_info

from . import test_resource_reads_2207 as resource_fixtures
from .test_resource_reads_2207 import detail, grant, subject

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return resource_fixtures.world.__wrapped__()


@pytest.fixture(autouse=True)
def no_search(monkeypatch):
    resource_fixtures.no_search.__wrapped__(monkeypatch)


@pytest.fixture
def actions(world, monkeypatch):
    grant(world, Permission.PROJECT_READ, Permission.PROJECT_UPDATE, kind="ORG", target=world.org.pk)
    world.service.status = ManagedService.Status.ACTIVE
    world.service.save()
    calls = []
    monkeypatch.setattr(
        "astrolift_services.schema.mutations.managed_services._start_project_service_provision",
        lambda *args: calls.append(args),
    )
    with subject(world):
        revision = detail(world).context_revision
    return world, revision, calls


@pytest.mark.parametrize("changed", ["owner", "cluster", "project", "service", "deleted"])
def test_context_changes_refuse_before_state_or_enqueue(actions, changed):
    w, revision, calls = actions
    if changed == "owner":
        w.service.project = w.platform_project
        w.service.save()
    elif changed == "deleted":
        w.service.soft_delete()
        replacement = ManagedService.objects.create(
            project=w.medops_project, tenant_cluster=w.cluster, name=w.service.name, kind=w.service.kind
        )
    elif changed == "service":
        w.service.name = "new-name"
        w.service.save()
    else:
        target = w.cluster if changed == "cluster" else w.medops_project
        target.name = "Changed"
        target.save()
    with subject(w):
        result = ServicesMutation().reprovision_project_managed_service(
            make_info(w.user),
            input=ReprovisionManagedServiceInput(
                managed_service_id=GUID(str(w.service.guid)), expected_context_revision=revision
            ),
        )
    assert result.ok is False
    assert result.errors[0].code == "STALE_TARGET"
    assert calls == []
    w.service.refresh_from_db()
    assert w.service.status == ManagedService.Status.ACTIVE
    if changed == "deleted":
        replacement.refresh_from_db()
        assert replacement.status == ManagedService.Status.PENDING


def test_reviewed_action_accepts_exact_current_context(actions):
    w, revision, calls = actions
    with subject(w):
        result = ServicesMutation().reprovision_project_managed_service(
            make_info(w.user),
            input=ReprovisionManagedServiceInput(
                managed_service_id=GUID(str(w.service.guid)), expected_context_revision=revision
            ),
        )
    assert result.ok is True
    assert len(calls) == 1
    w.service.refresh_from_db()
    assert w.service.status == ManagedService.Status.PENDING


def test_stale_attach_update_and_reparented_detach_have_zero_writes(actions):
    from astrolift_lifecycle.models import AppEnvironment

    w, revision, calls = actions
    env = AppEnvironment.objects.create(
        registered_app=w.medops_app, tenant_cluster=w.cluster, name="production"
    )
    attachment = ManagedServiceAttachment.objects.create(managed_service=w.service, app_environment=env)
    other = ManagedService.objects.create(
        project=w.medops_project, tenant_cluster=w.cluster, name="other-cache", kind="redis"
    )
    attachment.managed_service = other
    attachment.save()
    w.cluster.name = "Changed"
    w.cluster.save()
    with subject(w):
        results = [
            ServicesMutation().attach_project_managed_service(
                make_info(w.user),
                input=AttachProjectManagedServiceInput(
                    managed_service_id=GUID(str(w.service.guid)),
                    expected_context_revision=revision,
                    app_environment_id=GUID(str(env.guid)),
                ),
            ),
            ServicesMutation().update_project_managed_service(
                make_info(w.user),
                input=UpdateManagedServiceInput(
                    id=GUID(str(w.service.guid)), expected_context_revision=revision, name="should-not-write"
                ),
            ),
            ServicesMutation().detach_project_managed_service(
                make_info(w.user),
                input=DetachProjectManagedServiceInput(
                    attachment_id=GUID(str(attachment.guid)),
                    managed_service_id=GUID(str(w.service.guid)),
                    expected_context_revision=revision,
                ),
            ),
        ]
    assert all(not result.ok and result.errors[0].code == "STALE_TARGET" for result in results)
    assert calls == []
    assert ManagedServiceAttachment.objects.filter(pk=attachment.pk).exists()
    w.service.refresh_from_db()
    assert w.service.name == "cache"


def test_stale_deprovision_refuses_before_enqueue(actions, monkeypatch):
    from constance.test import override_config

    w, revision, calls = actions
    monkeypatch.setattr(
        "astrolift_services.schema.mutations.managed_services._start_project_service_deprovision",
        lambda *args, **kwargs: calls.append(args),
    )
    w.cluster.name = "Changed"
    w.cluster.save()
    with subject(w), override_config(REQUIRE_STEP_UP_AUTH=False):
        result = ServicesMutation().deprovision_project_managed_service(
            make_info(w.user),
            input=DeprovisionManagedServiceInput(
                id=GUID(str(w.service.guid)), expected_context_revision=revision
            ),
        )
    assert not result.ok and result.errors[0].code == "STALE_TARGET"
    assert calls == []
    w.service.refresh_from_db()
    assert w.service.status == ManagedService.Status.ACTIVE


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("changed", ["owner", "replacement", "grant"])
def test_waiting_action_rechecks_owner_replacement_and_current_grants(actions, monkeypatch, changed):
    w, revision, calls = actions
    entered = threading.Event()
    worker_pid = []
    original_atomic = transaction.atomic

    @contextmanager
    def traced_atomic(*args, **kwargs):
        with original_atomic(*args, **kwargs):
            if threading.current_thread().name.startswith("resource-action2207") and not entered.is_set():
                worker_pid.append(connection.connection.get_backend_pid())
                entered.set()
            yield

    monkeypatch.setattr(resource_actions.transaction, "atomic", traced_atomic)

    def dispatch():
        close_old_connections()
        try:
            with subject(w):
                return ServicesMutation().reprovision_project_managed_service(
                    make_info(w.user),
                    input=ReprovisionManagedServiceInput(
                        managed_service_id=GUID(str(w.service.guid)), expected_context_revision=revision
                    ),
                )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="resource-action2207") as executor:
        with original_atomic():
            ManagedService.objects.select_for_update().get(pk=w.service.pk)
            pending = executor.submit(dispatch)
            assert entered.wait(10)
            deadline = time.monotonic() + 10
            while True:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", worker_pid)
                    state = cursor.fetchone()
                if state and state[0] == "Lock":
                    break
                assert time.monotonic() < deadline, "worker never waited on the real service row lock"
                time.sleep(0.01)
            if changed == "owner":
                ManagedService.objects.filter(pk=w.service.pk).update(
                    project=w.platform_project, version=F("version") + 1
                )
            elif changed == "replacement":
                w.service.soft_delete()
                ManagedService.objects.create(
                    project=w.medops_project, tenant_cluster=w.cluster, name="cache", kind="redis"
                )
            else:
                from astrolift_identity.models import RoleBinding

                RoleBinding.objects.filter(user=w.user).delete()
        if changed == "grant":
            result = pending.result(timeout=10)
            assert result.ok is False
            assert result.errors[0].code == "PERMISSION_DENIED"
        else:
            result = pending.result(timeout=10)
            assert result.ok is False
            assert result.errors[0].code == "STALE_TARGET"
    assert calls == []
