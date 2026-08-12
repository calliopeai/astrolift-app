from __future__ import annotations

import pytest
from django.db import transaction

from astrolift_identity.models import Organization
from astrolift_operations.audit_writer import write_audit_entry
from astrolift_operations.models import AuditEvent
from core.mutations import AuditEntry

pytestmark = pytest.mark.django_db


def _entry(org, **overrides):
    values = {
        "actor_user_id": None,
        "organization_id": org.pk,
        "action": "agents.secret.binding.upsert",
        "decision": "ALLOW",
        "target_kind": "AgentSecret",
        "target_id": "agent:TOKEN",
        "duration_ms": 1,
        "permissions": (),
        "error_code": None,
        "error_message": None,
        "extra": None,
    }
    values.update(overrides)
    return AuditEntry(**values)


def test_writer_compacts_oversized_identifiers_deterministically():
    org = Organization.objects.create(name="Audit Bounds", slug="audit-bounds")
    oversized = "agent:" + "A" * 200

    write_audit_entry(_entry(org, target_id=oversized))
    row = AuditEvent.objects.get(organization=org)

    assert len(row.target_id) == 64
    assert row.target_id.startswith("agent:")
    assert row.target_id != oversized[:64]


def test_failed_audit_insert_does_not_poison_outer_transaction(monkeypatch):
    org = Organization.objects.create(name="Audit Failure", slug="audit-failure")
    original_create = AuditEvent.objects.create

    def fail_create(**kwargs):
        # Force a real database error inside the writer's savepoint.
        kwargs["target_id"] = "x" * 65
        return original_create(**kwargs)

    monkeypatch.setattr(AuditEvent.objects, "create", fail_create)
    with transaction.atomic():
        write_audit_entry(_entry(org))
        assert Organization.objects.filter(pk=org.pk).exists()
