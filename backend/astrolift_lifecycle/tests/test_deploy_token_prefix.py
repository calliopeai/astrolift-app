"""Tests for the deploy-token prefix canonicalisation (#449).

Three behaviours pinned here:

* The mint sites (``create_deploy_token`` / ``rotate_deploy_token`` GraphQL
  mutations + the ``issue_token`` / ``rotate_token`` helpers) all
  produce the canonical ``alft_dt_`` prefix that ``verify_token`` and
  ``DeployTokenAuthMiddleware`` recognise. Pre-#449 the GraphQL mints
  wrote ``alfdt_`` and the verifier silently rejected every fresh
  token.
* Round-trip: a token minted via the GraphQL mutation verifies cleanly
  via ``verify_token`` AND via the bearer middleware.
* Legacy ``alfdt_`` tokens (synthesised here to simulate in-flight DB
  rows issued by the pre-#449 mint) keep authenticating while the
  Constance ``DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED`` flag is on, and a
  warning + ``deploy_token.legacy_prefix_accepted`` audit event fire on
  every legacy accept so operators can size the straggler population
  before flipping the flag off.
"""

from __future__ import annotations

import hashlib
import logging

import pytest
from constance.test import override_config
from django.http import HttpResponse
from django.test import RequestFactory

from astrolift_identity.auth_schemes import AuthScheme, classify
from astrolift_lifecycle.deploy_tokens import (
    LEGACY_PLAINTEXT_PREFIX,
    PLAINTEXT_PREFIX,
    is_acceptable_prefix,
    issue_token,
    legacy_prefix_accepted_from_constance,
    verify_token,
)
from astrolift_lifecycle.middleware import (
    DeployTokenAuthMiddleware,
    get_request_deploy_token,
)
from astrolift_lifecycle.models import DeployToken
from astrolift_lifecycle.schema.mutations import (
    CreateDeployTokenInput,
    LifecycleMutation,
    RotateDeployTokenInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _ok(_request) -> HttpResponse:
    return HttpResponse("ok")


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _grant_update(resolver):
    resolver.grant(Permission.APP_UPDATE)


# ---- canonical prefix --------------------------------------------------


def test_canonical_prefix_constants_have_expected_bytes():
    """The verifier, middleware and auth-scheme classifier all key off
    these constants — pin the bytes so an accidental rename doesn't
    silently break the bearer dispatch."""
    assert PLAINTEXT_PREFIX == "alft_dt_"
    assert LEGACY_PLAINTEXT_PREFIX == "alfdt_"


def test_create_mutation_mints_canonical_prefix(org, app, actor, fake_info, permission_resolver):
    """Pre-#449 the mutation hard-coded ``alfdt_`` and the verifier
    rejected every freshly-minted token. Pin the canonical shape."""
    _grant_update(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.create_deploy_token(
            fake_info,
            input=CreateDeployTokenInput(app_slug=app.slug, name="ci"),
        )
    assert result.ok, result.errors
    assert result.data.plaintext_secret.startswith(PLAINTEXT_PREFIX)
    assert not result.data.plaintext_secret.startswith(LEGACY_PLAINTEXT_PREFIX + "_")


def test_rotate_mutation_mints_canonical_prefix(org, app, actor, fake_info, permission_resolver):
    _grant_update(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        created = mut.create_deploy_token(
            fake_info,
            input=CreateDeployTokenInput(app_slug=app.slug, name="ci"),
        )
        rotated = mut.rotate_deploy_token(
            fake_info,
            input=RotateDeployTokenInput(id=created.data.token.id),
        )
    assert rotated.ok, rotated.errors
    assert rotated.data.plaintext_secret.startswith(PLAINTEXT_PREFIX)


# ---- round-trip mint → verify ----------------------------------------


def test_round_trip_create_mutation_to_verify_token(org, app, actor, fake_info, permission_resolver):
    """The core #449 contract: a GraphQL-minted plaintext must verify
    against the live row. Pre-fix the prefix mismatch silently rejected
    every fresh token."""
    _grant_update(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.create_deploy_token(
            fake_info,
            input=CreateDeployTokenInput(app_slug=app.slug, name="ci"),
        )
    plaintext = result.data.plaintext_secret
    row = verify_token(plaintext, app=app)
    assert row is not None
    assert str(row.guid) == str(result.data.token.id)


def test_round_trip_create_mutation_to_middleware(org, app, actor, fake_info, permission_resolver):
    """End-to-end via the bearer middleware shipped in #425: a token
    minted today must produce a 200 OK and attach to the request."""
    _grant_update(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.create_deploy_token(
            fake_info,
            input=CreateDeployTokenInput(app_slug=app.slug, name="ci"),
        )
    plaintext = result.data.plaintext_secret

    request = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {plaintext}")
    response = DeployTokenAuthMiddleware(_ok)(request)
    assert response.status_code == 200
    attached = get_request_deploy_token(request)
    assert attached is not None
    assert str(attached.guid) == str(result.data.token.id)


def test_round_trip_round_trip_via_helpers(app):
    """Helpers were already canonical — pin the contract so a future
    helper refactor can't silently regress."""
    _row, plaintext = issue_token(app=app, name="ci")
    assert plaintext.startswith(PLAINTEXT_PREFIX)
    assert verify_token(plaintext, app=app) is not None


# ---- legacy prefix grace ----------------------------------------------


def _insert_legacy_prefix_row(app, *, name="legacy") -> tuple[DeployToken, str]:
    """Forge a DB row that hashes a legacy-prefix plaintext, to
    simulate an in-flight token issued by the pre-#449 mint. We can't
    use ``issue_token`` because it now (correctly) mints canonical
    plaintext only."""
    import secrets as secrets_lib

    plaintext = LEGACY_PLAINTEXT_PREFIX + secrets_lib.token_urlsafe(32)
    digest = hashlib.sha256(plaintext.encode()).hexdigest()
    row = DeployToken.objects.create(
        registered_app=app,
        name=name,
        token_hash=digest,
        token_last_4=plaintext[-4:],
        scopes=["app.deploy"],
    )
    return row, plaintext


def test_legacy_prefix_accepted_default_true():
    """Default must be True so this code's rollout doesn't 401 every
    in-flight CI runner the moment it ships."""
    assert legacy_prefix_accepted_from_constance() is True


def test_legacy_prefix_accepted_can_be_disabled():
    with override_config(DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED=False):
        assert legacy_prefix_accepted_from_constance() is False


def test_is_acceptable_prefix_routes_canonical_and_legacy():
    assert is_acceptable_prefix("alft_dt_anything") is True
    assert is_acceptable_prefix("alfdt_anything") is True  # default flag on
    assert is_acceptable_prefix("alft_at_anapi") is False
    assert is_acceptable_prefix("") is False
    assert is_acceptable_prefix("garbage") is False


def test_is_acceptable_prefix_drops_legacy_when_flag_off():
    with override_config(DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED=False):
        assert is_acceptable_prefix("alft_dt_anything") is True
        assert is_acceptable_prefix("alfdt_anything") is False


def test_verify_accepts_legacy_prefix_token_when_flag_on(app, caplog):
    row, plaintext = _insert_legacy_prefix_row(app)
    caplog.set_level(logging.WARNING, logger="astrolift_lifecycle.deploy_tokens")

    found = verify_token(plaintext, app=app)

    assert found is not None
    assert found.pk == row.pk
    # Audit-visible warning must fire so operators can see the
    # straggler frequency before flipping the flag off.
    assert any(record.message == "deploy_token.legacy_prefix_accepted" for record in caplog.records), [
        r.message for r in caplog.records
    ]


def test_verify_rejects_legacy_prefix_token_when_flag_off(app):
    _row, plaintext = _insert_legacy_prefix_row(app)
    with override_config(DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED=False):
        assert verify_token(plaintext, app=app) is None


def test_verify_emits_audit_event_on_legacy_accept(app, monkeypatch):
    """The audit event lets a downstream sink (webhook fan-out,
    activity feed) surface stragglers without an operator scraping
    logs.

    The fixture snapshots ``core.events._writer`` and lets ``monkeypatch``
    restore the original value at teardown — restoring to a hard-coded
    ``_log_event`` would mask the persistent DB writer registered at
    ``apps.ready`` for every subsequent test.
    """
    from core import events as core_events

    captured = []
    monkeypatch.setattr(core_events, "_writer", captured.append)

    _row, plaintext = _insert_legacy_prefix_row(app)
    verify_token(plaintext, app=app)

    assert any(env.event_type == "deploy_token.legacy_prefix_accepted" for env in captured), [
        e.event_type for e in captured
    ]


def test_verify_does_not_emit_audit_for_canonical_token(app, monkeypatch):
    from core import events as core_events

    captured = []
    monkeypatch.setattr(core_events, "_writer", captured.append)

    _row, plaintext = issue_token(app=app, name="ci")
    verify_token(plaintext, app=app)

    assert not any(env.event_type == "deploy_token.legacy_prefix_accepted" for env in captured)


def test_middleware_accepts_legacy_prefix_when_flag_on(app):
    _row, plaintext = _insert_legacy_prefix_row(app)
    request = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {plaintext}")
    response = DeployTokenAuthMiddleware(_ok)(request)
    assert response.status_code == 200
    assert get_request_deploy_token(request) is not None


def test_middleware_rejects_legacy_prefix_when_flag_off(app):
    _row, plaintext = _insert_legacy_prefix_row(app)
    with override_config(DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED=False):
        request = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {plaintext}")
        response = DeployTokenAuthMiddleware(_ok)(request)
    # Flag off → middleware noops on the legacy prefix (it's no longer
    # a recognised deploy-token shape), so the chained handler runs
    # and we hit the OK fallback rather than a 401. Pin both branches.
    assert response.status_code == 200
    assert get_request_deploy_token(request) is None


# ---- auth-scheme classifier ------------------------------------------


def test_classifier_routes_legacy_prefix_to_deploy_token_scheme():
    """Without this entry the classifier would reject a legacy bearer
    with 'token format not recognized' before the verifier (with its
    Constance gate) ever ran."""
    classified = classify(
        path="/api/deploy/",
        headers={"Authorization": "Bearer alfdt_some-legacy-bytes"},
    )
    assert classified.scheme == AuthScheme.DEPLOY_TOKEN


def test_classifier_routes_canonical_prefix_to_deploy_token_scheme():
    classified = classify(
        path="/api/deploy/",
        headers={"Authorization": "Bearer alft_dt_some-canonical-bytes"},
    )
    assert classified.scheme == AuthScheme.DEPLOY_TOKEN
