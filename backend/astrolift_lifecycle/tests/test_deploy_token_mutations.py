"""GraphQL mutation tests for deploy tokens (#425 grace surfacing).

Pins:

* ``createDeployToken`` returns ``rotation_grace_seconds = 0`` because
  there is no previous secret to honour yet.
* ``rotateDeployToken`` returns the live Constance grace value so the
  rotate-confirm dialog can show operators the exact window they're
  committing to (24h default).
* Operator-set Constance overrides flow through to the response.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import DeployToken
from astrolift_lifecycle.schema.mutations import (
    CreateDeployTokenInput,
    LifecycleMutation,
    RotateDeployTokenInput,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _tenant_for(org, actor):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _grant_update(resolver):
    resolver.grant(Permission.APP_UPDATE)


def test_create_returns_zero_grace_no_previous_secret(org, app, actor, fake_info, permission_resolver):
    _grant_update(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mut.create_deploy_token(
            fake_info,
            input=CreateDeployTokenInput(app_slug=app.slug, name="ci"),
        )
    assert result.ok, result.errors
    assert result.data.rotation_grace_seconds == 0
    # #449: mint must use the canonical ``alft_dt_`` prefix that
    # ``verify_token`` / ``DeployTokenAuthMiddleware`` recognise; the
    # pre-#449 ``alfdt_`` prefix is verifier-rejected outside the
    # legacy-acceptance grace.
    assert result.data.plaintext_secret.startswith("alft_dt_")


def test_rotate_returns_default_grace(org, app, actor, fake_info, permission_resolver):
    """Default Constance value is 24h (86400s); the response must
    reflect what the row was actually written with."""
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
    assert rotated.data.rotation_grace_seconds == 86400

    fresh = DeployToken.objects.get(guid=str(created.data.token.id))
    assert fresh.previous_token_expires_at is not None
    delta = fresh.previous_token_expires_at - fresh.last_rotated_at
    # Within 1s tolerance for clock drift between mutation lines.
    assert 86399 <= delta.total_seconds() <= 86401


def test_rotate_honours_constance_override(org, app, actor, fake_info, permission_resolver):
    """Operators can override the grace via Constance — the value
    that gets written to the row + returned in the envelope must
    track the override."""
    from constance.test import override_config

    _grant_update(permission_resolver)
    mut = LifecycleMutation()
    with _tenant_for(org, actor):
        created = mut.create_deploy_token(
            fake_info,
            input=CreateDeployTokenInput(app_slug=app.slug, name="ci"),
        )
        with override_config(DEPLOY_TOKEN_ROTATION_GRACE_SECONDS=3 * 60 * 60):
            rotated = mut.rotate_deploy_token(
                fake_info,
                input=RotateDeployTokenInput(id=created.data.token.id),
            )
    assert rotated.ok, rotated.errors
    assert rotated.data.rotation_grace_seconds == 3 * 60 * 60
