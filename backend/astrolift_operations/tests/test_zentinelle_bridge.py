"""Audit entries reach Zentinelle as ``AUDIT.*`` events (#1775).

The catalog, the subscription template and the webhook fan-out all existed;
no code path ever emitted an event with a catalog type, so a Zentinelle
subscription delivered nothing. These tests pin the join.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_operations.audit_writer import write_audit_entry
from astrolift_operations.models import AuditEvent
from astrolift_operations.webhook_format import adapt_payload
from astrolift_operations.zentinelle_bridge import (
    build_envelope,
    event_type_for_action,
    is_zentinelle_envelope,
)
from astrolift_operations.zentinelle_integration import (
    REQUIRED_PAYLOAD_KEYS,
    ZentinelleEnvelope,
    ZentinelleEventType,
)
from core.events import register_event_subscriber, unregister_event_subscriber
from core.mutations import AuditEntry


def _entry(**overrides) -> AuditEntry:
    values = {
        "actor_user_id": 7,
        "organization_id": 34,
        "action": "app.deploy",
        "decision": "ALLOW",
        "target_kind": "app",
        "target_id": "calliope-brain",
        "duration_ms": 12,
        "permissions": ("app.deploy",),
        "error_code": None,
        "error_message": None,
        "extra": {"deployment_id": "01abc", "environment": "production"},
    }
    values.update(overrides)
    return AuditEntry(**values)


def test_mapping_covers_catalog_and_ignores_the_rest():
    assert event_type_for_action("app.deploy") is ZentinelleEventType.APP_DEPLOY
    assert event_type_for_action("role_binding.grant") is ZentinelleEventType.ROLE_BINDING_GRANT
    assert event_type_for_action("app.secret.reveal") is ZentinelleEventType.SECRET_VIEWED
    assert event_type_for_action("agents.task.launch") is None
    assert event_type_for_action("session.heartbeat") is None


def test_envelope_is_the_locked_wire_shape():
    env = build_envelope(_entry(), event_id="01EVENT", occurred_at_unix=1_700_000_000)
    assert env is not None
    # Round-trips through the locked dataclass, so the receiver's checks pass.
    ZentinelleEnvelope(**env)
    assert env["event_type"] == "AUDIT.app.deploy"
    assert env["org_id"] == 34
    assert env["idempotency_key"] == "zentinelle-34-01EVENT"
    for key in REQUIRED_PAYLOAD_KEYS[ZentinelleEventType.APP_DEPLOY]:
        assert key in env["payload"]
    assert env["payload"]["app_slug"] == "calliope-brain"
    assert env["payload"]["deployment_id"] == "01abc"
    assert env["payload"]["image_digest"] is None  # unknown, never invented


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision": "DENY"},
        {"organization_id": None},
        {"action": "agents.task.launch"},
    ],
)
def test_non_evidence_entries_build_nothing(overrides):
    assert build_envelope(_entry(**overrides), event_id="01EVENT") is None


@pytest.mark.django_db
def test_persisted_audit_entry_is_emitted_as_audit_event():
    org = Organization.objects.create(name="Acme", slug="acme")
    seen = []
    register_event_subscriber(seen.append)
    try:
        write_audit_entry(
            _entry(
                actor_user_id=None,
                organization_id=org.pk,
                action="app.secret.reveal",
                target_kind="AppSecret",
                target_id="DATABASE_URL",
                extra=None,
            )
        )
        write_audit_entry(_entry(actor_user_id=None, organization_id=org.pk, action="session.heartbeat"))
    finally:
        unregister_event_subscriber(seen.append)
    assert AuditEvent.objects.filter(organization_id=org.pk).count() == 2
    audit_events = [e for e in seen if e.event_type.startswith("AUDIT.")]
    assert [e.event_type for e in audit_events] == ["AUDIT.secret.viewed"]
    envelope = audit_events[0].payload
    assert envelope["org_id"] == org.pk
    assert envelope["payload"]["secret_path"] == "DATABASE_URL"
    assert audit_events[0].organization_id == org.pk


def test_generic_format_posts_the_inner_envelope():
    inner = build_envelope(_entry(), event_id="01EVENT", occurred_at_unix=1_700_000_000)
    wrapped = {"event_type": "AUDIT.app.deploy", "payload": inner, "organization_id": 34}
    assert is_zentinelle_envelope("AUDIT.app.deploy", inner)
    assert adapt_payload(format="generic", envelope=wrapped) == inner
    plain = {"event_type": "deployment.succeeded", "payload": {"hello": "world"}}
    assert adapt_payload(format="generic", envelope=plain) == plain
    assert not is_zentinelle_envelope("deployment.succeeded", plain["payload"])
