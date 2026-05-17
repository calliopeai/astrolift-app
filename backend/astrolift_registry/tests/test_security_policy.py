"""Tests for the supply-chain security policy on RegisteredApp (#313).

Covers:
- ``RegisteredApp.security_policy_resolved`` returns platform defaults
  when the underlying JSON blob is empty or partial.
- ``updateAstroliftSecurityPolicy`` persists the operator's choices.
- Threshold validation (>= 1; null clears).
- Permission gate (deny-by-default).
- ``SupplyChainBlockedPayload`` serialisation contract.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    RegistryMutation,
    UpdateSecurityPolicyInput,
)
from core.events import SupplyChainBlockedPayload
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
        subdomain="hello",
    )
    return org, team, project, app


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# -- model property -------------------------------------------------------


def test_security_policy_resolved_returns_defaults_when_empty():
    _, _, _, app = _scaffold()
    assert app.security_policy == {}

    resolved = app.security_policy_resolved
    assert resolved["block_on_critical_cves"] is True
    assert resolved["block_on_missing_signature"] is True
    assert resolved["block_on_high_cve_threshold"] is None


def test_security_policy_resolved_preserves_partial_overrides():
    _, _, _, app = _scaffold()
    # Only the threshold is set; the boolean defaults must still come
    # through so the gate doesn't accidentally loosen on a partial
    # write.
    app.security_policy = {"block_on_high_cve_threshold": 5}
    app.save(update_fields=["security_policy", "updated_at", "version"])

    resolved = app.security_policy_resolved
    assert resolved["block_on_critical_cves"] is True
    assert resolved["block_on_missing_signature"] is True
    assert resolved["block_on_high_cve_threshold"] == 5


def test_security_policy_resolved_honours_explicit_false():
    _, _, _, app = _scaffold()
    app.security_policy = {
        "block_on_critical_cves": False,
        "block_on_missing_signature": False,
    }
    app.save(update_fields=["security_policy", "updated_at", "version"])

    resolved = app.security_policy_resolved
    assert resolved["block_on_critical_cves"] is False
    assert resolved["block_on_missing_signature"] is False


# -- mutation -------------------------------------------------------------


def test_update_security_policy_writes_full_policy(permission_resolver):
    org, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_astrolift_security_policy(
            _info(),
            input=UpdateSecurityPolicyInput(
                app_slug=app.slug,
                block_on_critical_cves=False,
                block_on_missing_signature=True,
                block_on_high_cve_threshold=7,
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.security_policy == {
        "block_on_critical_cves": False,
        "block_on_missing_signature": True,
        "block_on_high_cve_threshold": 7,
    }
    # Returned type carries the resolved view.
    assert result.data.security_policy.block_on_critical_cves is False
    assert result.data.security_policy.block_on_missing_signature is True
    assert result.data.security_policy.block_on_high_cve_threshold == 7


def test_update_security_policy_null_threshold_clears_gate(permission_resolver):
    org, _, _, app = _scaffold()
    app.security_policy = {
        "block_on_critical_cves": True,
        "block_on_missing_signature": True,
        "block_on_high_cve_threshold": 3,
    }
    app.save(update_fields=["security_policy", "updated_at", "version"])
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_astrolift_security_policy(
            _info(),
            input=UpdateSecurityPolicyInput(
                app_slug=app.slug,
                block_on_critical_cves=True,
                block_on_missing_signature=True,
                block_on_high_cve_threshold=None,
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.security_policy["block_on_high_cve_threshold"] is None
    assert result.data.security_policy.block_on_high_cve_threshold is None


def test_update_security_policy_rejects_threshold_below_one(permission_resolver):
    org, _, _, app = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_astrolift_security_policy(
            _info(),
            input=UpdateSecurityPolicyInput(
                app_slug=app.slug,
                block_on_critical_cves=True,
                block_on_missing_signature=True,
                block_on_high_cve_threshold=0,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "blockOnHighCveThreshold"


def test_update_security_policy_rejects_unknown_app(permission_resolver):
    org, _, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = RegistryMutation().update_astrolift_security_policy(
            _info(),
            input=UpdateSecurityPolicyInput(
                app_slug="does-not-exist",
                block_on_critical_cves=True,
                block_on_missing_signature=True,
                block_on_high_cve_threshold=None,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"


def test_update_security_policy_denied_without_permission():
    org, _, _, app = _scaffold()
    # No grant — deny-by-default.

    with _ctx(org):
        result = RegistryMutation().update_astrolift_security_policy(
            _info(),
            input=UpdateSecurityPolicyInput(
                app_slug=app.slug,
                block_on_critical_cves=True,
                block_on_missing_signature=True,
                block_on_high_cve_threshold=None,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    app.refresh_from_db()
    # Persisted state untouched.
    assert app.security_policy == {}


# -- event payload --------------------------------------------------------


def test_supply_chain_blocked_payload_serialises_full_shape():
    blocked_at = dt.datetime(2026, 5, 16, 12, 30, 45, tzinfo=dt.UTC)
    payload = SupplyChainBlockedPayload(
        app_slug="hello-app",
        deployment_guid="11111111-2222-3333-4444-555555555555",
        image_digest="sha256:abc123",
        reason="critical_cves",
        cve_summary={"critical": 2, "high": 5, "medium": 9, "low": 14},
        signature_required=True,
        blocked_at=blocked_at,
    )

    out = payload.as_payload()
    assert out == {
        "app_slug": "hello-app",
        "deployment_guid": "11111111-2222-3333-4444-555555555555",
        "image_digest": "sha256:abc123",
        "reason": "critical_cves",
        "cve_summary": {"critical": 2, "high": 5, "medium": 9, "low": 14},
        "signature_required": True,
        "blocked_at": "2026-05-16T12:30:45Z",
    }


def test_supply_chain_blocked_payload_allows_missing_cve_summary():
    """Signature-only blocks fire before scanning, so cve_summary is
    None and must serialise as JSON null."""
    payload = SupplyChainBlockedPayload(
        app_slug="hello-app",
        deployment_guid="11111111-2222-3333-4444-555555555555",
        image_digest="sha256:def456",
        reason="missing_signature",
        cve_summary=None,
        signature_required=True,
        blocked_at=dt.datetime(2026, 5, 16, 0, 0, 0, tzinfo=dt.UTC),
    )

    out = payload.as_payload()
    assert out["cve_summary"] is None
    assert out["reason"] == "missing_signature"


def test_supply_chain_blocked_payload_rejects_unknown_reason():
    with pytest.raises(ValueError):
        SupplyChainBlockedPayload(
            app_slug="hello-app",
            deployment_guid="11111111-2222-3333-4444-555555555555",
            image_digest="sha256:abc",
            reason="not-a-real-reason",
            cve_summary=None,
            signature_required=False,
            blocked_at=dt.datetime(2026, 5, 16, 0, 0, 0, tzinfo=dt.UTC),
        )
