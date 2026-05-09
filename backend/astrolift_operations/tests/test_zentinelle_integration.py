"""Tests for Zentinelle GRC integration policy (#228, spec 17 §6 + §11)."""

from __future__ import annotations

import pytest

from astrolift_operations.zentinelle_integration import (
    DEFAULT_SUBSCRIBED_EVENTS,
    DEFAULT_TEMPLATE,
    PAYLOAD_VERSION,
    REQUIRED_PAYLOAD_KEYS,
    ZentinelleEnvelope,
    ZentinelleEventType,
    ZentinelleIntegrationError,
    ZentinelleValidationCallback,
    frameworks_for_event,
    idempotency_key_for,
    render_subscription_target,
    validate_payload,
)

# ---- payload version + vocabulary locked --------------------------


def test_payload_version_locked():
    assert PAYLOAD_VERSION == 1


def test_event_type_count_locked():
    """Lock the catalog so adding an event type requires
    deliberate code review (Zentinelle's parser depends on it)."""
    assert len(list(ZentinelleEventType)) == 14


def test_required_keys_present_for_every_event():
    """Schema invariant: every event type has a required-keys
    entry. Missing entry = parser would crash on that event."""
    for event_type in ZentinelleEventType:
        assert event_type in REQUIRED_PAYLOAD_KEYS


# ---- envelope validation ------------------------------------------


def _envelope(**overrides) -> ZentinelleEnvelope:
    base = dict(
        payload_version=PAYLOAD_VERSION,
        event_type=ZentinelleEventType.APP_DEPLOY.value,
        event_id="01HXXX",
        org_id=42,
        actor_user_id=1,
        occurred_at_unix=1_700_000_000,
        payload={
            "app_slug": "api",
            "deployment_id": 100,
            "image_digest": "sha256:abc",
            "environment": "prod",
            "trigger_kind": "ci",
        },
        idempotency_key="zentinelle-42-01HXXX",
    )
    base.update(overrides)
    return ZentinelleEnvelope(**base)


def test_envelope_basic():
    e = _envelope()
    assert e.event_type == "AUDIT.app.deploy"


def test_envelope_rejects_wrong_version():
    """Forward-compat: receiver expecting v1 must reject v2."""
    with pytest.raises(ZentinelleIntegrationError, match="payload_version"):
        _envelope(payload_version=2)


def test_envelope_requires_event_id():
    with pytest.raises(ZentinelleIntegrationError, match="event_id"):
        _envelope(event_id="")


def test_envelope_requires_idempotency_key():
    """Anti-replay invariant — Zentinelle dedupes on this."""
    with pytest.raises(ZentinelleIntegrationError, match="idempotency"):
        _envelope(idempotency_key="")


def test_envelope_rejects_negative_org_id():
    with pytest.raises(ZentinelleIntegrationError):
        _envelope(org_id=0)


def test_envelope_rejects_unknown_event_type():
    """Wire-format vocabulary is locked; unknown types refused."""
    with pytest.raises(ZentinelleIntegrationError, match="vocabulary"):
        _envelope(event_type="AUDIT.something.weird")


def test_envelope_actor_user_id_can_be_none():
    """System-initiated events (e.g. scheduled rotation) have
    no actor."""
    e = _envelope(actor_user_id=None)
    assert e.actor_user_id is None


# ---- payload schema validation ------------------------------------


def test_validate_payload_app_deploy_happy():
    validate_payload(
        event_type=ZentinelleEventType.APP_DEPLOY,
        payload={
            "app_slug": "api", "deployment_id": 1,
            "image_digest": "sha256:abc",
            "environment": "prod", "trigger_kind": "ci",
        },
    )


def test_validate_payload_missing_required_key():
    with pytest.raises(ZentinelleIntegrationError, match="missing"):
        validate_payload(
            event_type=ZentinelleEventType.APP_DEPLOY,
            payload={"app_slug": "api"},
        )


def test_validate_payload_role_binding_grant():
    validate_payload(
        event_type=ZentinelleEventType.ROLE_BINDING_GRANT,
        payload={
            "role_id": 1, "scope": "org", "subject_user_id": 42,
        },
    )


def test_validate_payload_secret_rotated():
    validate_payload(
        event_type=ZentinelleEventType.SECRET_ROTATED,
        payload={
            "secret_path": "db/prod/main",
            "rotation_kind": "scheduled",
        },
    )


def test_validate_payload_extra_keys_ok():
    """Extra keys allowed (forward-compat — emitter may add
    additional context); only missing-required is refused."""
    validate_payload(
        event_type=ZentinelleEventType.APP_DEPLOY,
        payload={
            "app_slug": "api", "deployment_id": 1,
            "image_digest": "sha256:abc",
            "environment": "prod", "trigger_kind": "ci",
            "extra_field": "future-context",
        },
    )


# ---- idempotency key format ---------------------------------------


def test_idempotency_key_format():
    key = idempotency_key_for(org_id=42, event_id="01HXXX")
    assert key == "zentinelle-42-01HXXX"


def test_idempotency_key_includes_org():
    """Cross-org collision defense — even pathological event_id
    reuse can't shadow another org's event."""
    a = idempotency_key_for(org_id=1, event_id="X")
    b = idempotency_key_for(org_id=2, event_id="X")
    assert a != b


def test_idempotency_key_rejects_invalid_org():
    with pytest.raises(ZentinelleIntegrationError):
        idempotency_key_for(org_id=0, event_id="X")


def test_idempotency_key_rejects_empty_event_id():
    with pytest.raises(ZentinelleIntegrationError):
        idempotency_key_for(org_id=1, event_id="")


# ---- subscription template ----------------------------------------


def test_default_template_subscribes_all_events():
    """Operator can narrow but the default is full evidence."""
    assert (
        set(DEFAULT_TEMPLATE.event_types)
        == set(DEFAULT_SUBSCRIBED_EVENTS)
    )


def test_default_template_signing_required():
    """HMAC signing always on — compliance evidence in
    cleartext is unacceptable."""
    assert DEFAULT_TEMPLATE.signing_secret_required is True


def test_render_subscription_target_basic():
    url = render_subscription_target(
        template=DEFAULT_TEMPLATE,
        zentinelle_url="https://zentinelle.acme.io",
    )
    assert url == "https://zentinelle.acme.io/integrations/astrolift/v1/audit"


def test_render_subscription_target_strips_trailing_slash():
    url = render_subscription_target(
        template=DEFAULT_TEMPLATE,
        zentinelle_url="https://zentinelle.acme.io/",
    )
    assert "//" not in url.replace("https://", "")


def test_render_subscription_rejects_http():
    """Compliance evidence over plaintext = audit chain
    meaningless. Refuse loudly."""
    with pytest.raises(ZentinelleIntegrationError, match="HTTPS"):
        render_subscription_target(
            template=DEFAULT_TEMPLATE,
            zentinelle_url="http://zentinelle.acme.io",
        )


def test_render_subscription_rejects_empty():
    with pytest.raises(ZentinelleIntegrationError):
        render_subscription_target(
            template=DEFAULT_TEMPLATE, zentinelle_url="",
        )


# ---- framework cross-reference -----------------------------------


def test_frameworks_for_role_binding_covers_all_three():
    """Role grants/revokes are universal access-control evidence."""
    fw = frameworks_for_event(
        event_type=ZentinelleEventType.ROLE_BINDING_GRANT,
    )
    assert set(fw) == {"soc2", "hipaa", "iso27001"}


def test_frameworks_for_secret_viewed_hipaa_only():
    """SOC2/ISO don't require per-view audit; HIPAA does for
    ePHI-adjacent secrets."""
    fw = frameworks_for_event(
        event_type=ZentinelleEventType.SECRET_VIEWED,
    )
    assert "hipaa" in fw
    assert "soc2" not in fw


def test_frameworks_for_residency_hipaa_only():
    """Residency policy changes mostly relevant to HIPAA
    (data sovereignty for ePHI)."""
    fw = frameworks_for_event(
        event_type=ZentinelleEventType.RESIDENCY_POLICY_UPDATED,
    )
    assert "hipaa" in fw


def test_frameworks_returned_sorted():
    """UI badge consumes; sorted = stable rendering."""
    fw = frameworks_for_event(
        event_type=ZentinelleEventType.SECRET_ROTATED,
    )
    assert list(fw) == sorted(fw)


# ---- bidirectional callback shape ---------------------------------


def test_validation_callback_basic():
    cb = ZentinelleValidationCallback(
        event_id="01HXXX",
        zentinelle_evidence_id="ev_abc",
        validated_at_unix=1_700_000_000,
    )
    assert cb.zentinelle_evidence_id == "ev_abc"


def test_validation_callback_requires_event_id():
    with pytest.raises(ZentinelleIntegrationError):
        ZentinelleValidationCallback(
            event_id="", zentinelle_evidence_id="ev_abc",
            validated_at_unix=0,
        )


def test_validation_callback_requires_zentinelle_id():
    """Without Zentinelle's reference there's nothing to link
    back to in the operator UI."""
    with pytest.raises(ZentinelleIntegrationError):
        ZentinelleValidationCallback(
            event_id="X", zentinelle_evidence_id="",
            validated_at_unix=0,
        )
