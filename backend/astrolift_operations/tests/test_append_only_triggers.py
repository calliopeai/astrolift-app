"""
DB-level append-only enforcement.

Spec/04 §1 modeling principle 7: every UPDATE / DELETE on Event,
AuditEvent, and DeploymentLog must be refused at the storage layer,
not just by the ORM mixin. These tests run direct SQL against the
test database to prove the triggers fire.
"""

from __future__ import annotations

import pytest
from django.db import connection, transaction

from astrolift_identity.models import Organization
from astrolift_operations.models import AuditEvent, Event

pytestmark = pytest.mark.django_db


@pytest.fixture
def org():
    return Organization.objects.create(name="Trigger Test", slug="trigger-test")


def _direct_update(table: str, guid: str) -> Exception | None:
    """Run a raw UPDATE bypassing the ORM; return the exception or None."""
    try:
        with transaction.atomic(), connection.cursor() as c:
            c.execute(
                f"UPDATE {table} SET event_type='HACKED' WHERE guid=%s",
                [guid],
            )
    except Exception as exc:  # noqa: BLE001 — we want the wrapped exception
        return exc
    return None


def _direct_delete(table: str, guid: str) -> Exception | None:
    try:
        with transaction.atomic(), connection.cursor() as c:
            c.execute(f"DELETE FROM {table} WHERE guid=%s", [guid])
    except Exception as exc:
        return exc
    return None


def test_event_table_refuses_update(org):
    e = Event.objects.create(
        organization=org, event_type="UNIT_TEST", payload={"x": 1}
    )
    err = _direct_update("astrolift_operations_event", str(e.guid))
    assert err is not None, "UPDATE on Event should have raised"
    assert "append-only" in str(err)


def test_event_table_refuses_delete(org):
    e = Event.objects.create(
        organization=org, event_type="UNIT_TEST", payload={}
    )
    err = _direct_delete("astrolift_operations_event", str(e.guid))
    assert err is not None, "DELETE on Event should have raised"
    assert "append-only" in str(err)


def test_audit_event_refuses_update(org):
    a = AuditEvent.objects.create(
        organization=org,
        actor_kind="user",
        actor_id="1",
        action="unit.test",
        decision="ALLOW",
    )
    try:
        with transaction.atomic(), connection.cursor() as c:
            c.execute(
                "UPDATE astrolift_operations_auditevent SET decision='DENY' WHERE guid=%s",
                [str(a.guid)],
            )
    except Exception as exc:
        assert "append-only" in str(exc)
    else:
        pytest.fail("UPDATE on AuditEvent should have raised")


def test_audit_event_refuses_delete(org):
    a = AuditEvent.objects.create(
        organization=org,
        actor_kind="system",
        action="unit.test",
        decision="ALLOW",
    )
    err = _direct_delete("astrolift_operations_auditevent", str(a.guid))
    assert err is not None
    assert "append-only" in str(err)


def test_orm_layer_also_refuses(org):
    """The AppendOnlyMixin should refuse before SQL is even attempted."""
    e = Event.objects.create(organization=org, event_type="UNIT_TEST", payload={})
    e.event_type = "MUTATED"
    with pytest.raises(RuntimeError, match="append-only"):
        e.save()
    with pytest.raises(RuntimeError, match="append-only"):
        e.delete()


def test_inserts_still_work(org):
    """The trigger fires only on UPDATE/DELETE; INSERTs pass through."""
    a = Event.objects.create(organization=org, event_type="OK", payload={"k": "v"})
    assert a.pk is not None
    assert Event.objects.filter(pk=a.pk).exists()
