"""Real PostgreSQL lock waits must invalidate authority approved before waiting."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections, transaction

from astrolift_clusters.models import TenantCluster
from astrolift_clusters.tests import test_auth_user_preconditions_2225 as cases
from astrolift_clusters.tests.test_auth_user_preconditions_2225 import (
    OPERATIONS,
    execute,
    source,
)
from core.permissions import Permission

setup = cases.setup
no_search = cases.no_search

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("change", ["pool", "aba", "credential", "permission", "actor", "role"])
def test_actual_lock_wait_rechecks_source_and_authority_before_sdk(setup, change, monkeypatch):
    s = setup
    binding = None
    if change == "role":
        from astrolift_identity.permission_resolver import resolve
        from core.tests.utils.scope_world import bind_role

        monkeypatch.setattr("core.permissions._resolver", resolve)
        binding = bind_role(
            s.actor,
            permissions=[Permission.CLUSTER_USERS],
            kind="ORG",
            scope_id=s.org.pk,
            slug="reviewed-auth-users-2225",
        )
    expected = source(s)
    s.idp.calls.clear()
    operation = OPERATIONS[5]
    started = threading.Event()
    worker_pid = []

    def write():
        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                worker_pid.append(cursor.fetchone()[0])
            started.set()
            return execute(s, operation, expected, s.idp.subject)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction.atomic():
            locked = TenantCluster.objects.select_for_update().get(pk=s.cluster.pk)
            future = executor.submit(write)
            assert started.wait(5)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [worker_pid[0]]
                    )
                    waiting = cursor.fetchone()
                if waiting and waiting[0] == "Lock":
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("Worker never reached the real PostgreSQL row lock")
            assert not future.done()
            if change in {"pool", "aba"}:
                original = dict(locked.oidc_auth_config)
                locked.oidc_auth_config = {
                    **original,
                    "discovery_url": original["discovery_url"].replace("xU96Y7DAg", "OTHERPOOL"),
                }
                locked.save(update_fields=["oidc_auth_config"])
                if change == "aba":
                    locked.oidc_auth_config = original
                    locked.save(update_fields=["oidc_auth_config"])
            elif change == "credential":
                locked.auth_config = {"kubeconfig": "CHANGED_TEST_ONLY"}
                locked.save(update_fields=["auth_config"])
            elif change == "permission":
                s.permission.deny(Permission.CLUSTER_USERS)
            elif change == "role":
                binding.soft_delete()
            else:
                s.actor.is_active = False
                s.actor.save(update_fields=["is_active"])
        result = future.result(timeout=15)
    assert result["ok"] is False
    assert result["errors"][0]["code"] in {"PRECONDITION", "PERMISSION_DENIED"}
    assert s.idp.calls == []
