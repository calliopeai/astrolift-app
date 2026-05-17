"""Tests for the ``ifMatchVersion`` optimistic-concurrency gate (#497).

The gate lives in ``core.optimistic.check_version_match`` and is wired
into the high-value update mutations so two operators editing the same
entity from two devices can't silently overwrite each other.

Covers:

* The check helper itself — match, mismatch, opt-out.
* ``updateAstroliftApp`` — round-trip on match, refusal on stale.
* ``updateAstroliftPolicy`` — same.
* ``updateAstroliftIdentityProvider`` — same.
* ``updateAstroliftWebhookSubscription`` — same.
* ``setAppSecret`` — same.
* Race semantics — concurrent ``ifMatchVersion`` writes pick a winner.
* Append-only mutations (create/delete) are unaffected.
* ``version`` initial value + auto-increment on save.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import (
    IdentityProvider,
    Organization,
    Policy,
    Project,
    Team,
)
from astrolift_identity.schema.mutations import (
    CreatePolicyInput,
    IdentityMutation,
    UpdateIdentityProviderInput,
    UpdatePolicyInput,
)
from astrolift_operations.models import WebhookSubscription
from astrolift_operations.schema.mutations import (
    OperationsMutation,
    UpdateWebhookSubscriptionInput,
)
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import (
    RegistryMutation,
    UpdateAppInput,
)
from astrolift_services.schema.mutations import (
    ServicesMutation,
    SetAppSecretInput,
)
from core.optimistic import check_version_match
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db
User = get_user_model()


# ---------------------------------------------------------------------
# Scaffolding


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user), user=user))


def _user(username: str = "operator"):
    return User.objects.create(username=username, email=f"{username}@test.local")


_BASE_TOML = """\
astrolift_version = 1
name = "hello"

[env]
KEEP_ME = "yes"

[[workloads]]
name = "web"
kind = "deployment"
"""


def _scaffold(slug_suffix: str = "497"):
    org = Organization.objects.create(name="Acme", slug=f"acme-{slug_suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{slug_suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{slug_suffix}")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug=f"hello-{slug_suffix}",
        provisioning_status="ready",
        subdomain=f"hello-{slug_suffix}",
        manifest_raw=_BASE_TOML,
    )
    return org, team, project, app


def _tenant(org, user=None):
    actor_id = user.id if user is not None else None
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor_id))


# ---------------------------------------------------------------------
# 1. Check helper unit tests


def test_check_version_match_returns_none_when_omitted():
    """``if_match_version=None`` is the back-compat opt-out path."""
    inst = SimpleNamespace(version=7)
    assert check_version_match(inst, if_match_version=None) is None


def test_check_version_match_returns_none_when_matching():
    inst = SimpleNamespace(version=7)
    assert check_version_match(inst, if_match_version=7) is None


def test_check_version_match_returns_envelope_when_stale():
    inst = SimpleNamespace(version=9)
    result = check_version_match(inst, if_match_version=4, kind="App")
    assert result is not None
    assert result.ok is False
    assert result.data is None
    assert len(result.errors) == 1
    err = result.errors[0]
    assert err.code == "VERSION_MISMATCH"
    assert err.current_version == 9
    assert err.requested_version == 4
    assert "App" in err.message


# ---------------------------------------------------------------------
# 2. updateAstroliftApp — match, stale, omit


def test_update_app_with_matching_version_succeeds_and_bumps(permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    org, _, _, app = _scaffold()
    starting_version = app.version
    user = _user("op-app-match")
    with _tenant(org, user):
        result = RegistryMutation().update_app(
            _info(user),
            input=UpdateAppInput(
                id=str(app.guid),
                description="updated via match",
                if_match_version=starting_version,
            ),
        )
    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.description == "updated via match"
    # Auto-increment fires on every save() — the version moved on.
    assert app.version == starting_version + 1


def test_update_app_with_stale_version_returns_version_mismatch(permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    org, _, _, app = _scaffold()
    user = _user("op-app-stale")
    stale_version = app.version - 1
    with _tenant(org, user):
        result = RegistryMutation().update_app(
            _info(user),
            input=UpdateAppInput(
                id=str(app.guid),
                description="overwrite attempt",
                if_match_version=stale_version,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VERSION_MISMATCH"
    assert result.errors[0].current_version == app.version
    assert result.errors[0].requested_version == stale_version
    # No mutation applied — the persisted row keeps its original fields
    # (the seed scaffold leaves ``description`` empty by default).
    app.refresh_from_db()
    assert app.description == ""


def test_update_app_omitting_version_skips_check(permission_resolver):
    """Back-compat: callers that don't supply ``ifMatchVersion``
    keep working without any check."""
    permission_resolver.grant(Permission.APP_UPDATE)
    org, _, _, app = _scaffold()
    user = _user("op-app-omit")
    with _tenant(org, user):
        result = RegistryMutation().update_app(
            _info(user),
            input=UpdateAppInput(id=str(app.guid), description="no version arg"),
        )
    assert result.ok, result.errors
    app.refresh_from_db()
    assert app.description == "no version arg"


# ---------------------------------------------------------------------
# 3. updateAstroliftPolicy — match, stale, omit


def test_update_policy_with_matching_version_succeeds(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    permission_resolver.grant(Permission.ORG_READ)
    org = Organization.objects.create(name="Acme", slug="acme-policy-497")
    actor = _user("op-policy")
    mut = IdentityMutation()
    with _tenant(org, actor):
        created = mut.create_policy(
            _info(actor),
            input=CreatePolicyInput(
                name="p1",
                slug="p1-497",
                scope_level="ORG",
                effect="DENY",
                action_pattern="app.deploy",
            ),
        )
        assert created.ok, created.errors
        starting_version = created.data.version
        upd = mut.update_policy(
            _info(actor),
            input=UpdatePolicyInput(
                id=created.data.id,
                action_pattern="app.*",
                if_match_version=starting_version,
            ),
        )
    assert upd.ok, upd.errors
    assert upd.data.version == starting_version + 1


def test_update_policy_with_stale_version_fails(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    permission_resolver.grant(Permission.ORG_READ)
    org = Organization.objects.create(name="Acme", slug="acme-policy-stale")
    actor = _user("op-policy-stale")
    mut = IdentityMutation()
    with _tenant(org, actor):
        created = mut.create_policy(
            _info(actor),
            input=CreatePolicyInput(
                name="p2",
                slug="p2-497",
                scope_level="ORG",
                effect="DENY",
            ),
        )
        assert created.ok
        stale = created.data.version - 1
        upd = mut.update_policy(
            _info(actor),
            input=UpdatePolicyInput(
                id=created.data.id,
                description="never written",
                if_match_version=stale,
            ),
        )
    assert not upd.ok
    assert upd.errors[0].code == "VERSION_MISMATCH"
    assert upd.errors[0].current_version == created.data.version
    row = Policy.objects.get(slug="p2-497")
    assert row.description == ""


# ---------------------------------------------------------------------
# 4. updateAstroliftIdentityProvider — match + stale


def test_update_identity_provider_with_matching_version_succeeds(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org = Organization.objects.create(name="Acme", slug="acme-idp-match")
    actor = _user("op-idp")
    idp = IdentityProvider.objects.create(
        organization=org,
        kind=IdentityProvider.Kind.OIDC,
        display_name="primary",
    )
    starting_version = idp.version
    with _tenant(org, actor):
        result = IdentityMutation().update_identity_provider(
            _info(actor),
            input=UpdateIdentityProviderInput(
                id=str(idp.guid),
                display_name="renamed",
                if_match_version=starting_version,
            ),
        )
    assert result.ok, result.errors
    assert result.data.version == starting_version + 1
    idp.refresh_from_db()
    assert idp.display_name == "renamed"


def test_update_identity_provider_with_stale_version_fails(permission_resolver):
    permission_resolver.grant(Permission.ORG_UPDATE)
    org = Organization.objects.create(name="Acme", slug="acme-idp-stale")
    actor = _user("op-idp-stale")
    idp = IdentityProvider.objects.create(
        organization=org,
        kind=IdentityProvider.Kind.OIDC,
        display_name="primary",
    )
    stale = idp.version - 1
    with _tenant(org, actor):
        result = IdentityMutation().update_identity_provider(
            _info(actor),
            input=UpdateIdentityProviderInput(
                id=str(idp.guid),
                display_name="overwrite-attempt",
                if_match_version=stale,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VERSION_MISMATCH"
    idp.refresh_from_db()
    assert idp.display_name == "primary"


# ---------------------------------------------------------------------
# 5. updateAstroliftWebhookSubscription — match, stale, omit


def test_update_webhook_subscription_with_matching_version_succeeds(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org = Organization.objects.create(name="Acme", slug="acme-wh-match")
    actor = _user("op-wh")
    sub = WebhookSubscription.objects.create(
        organization=org,
        url="https://example.invalid/wh",
        secret_hash="abc",
        events=["deploy.completed"],
        is_active=True,
        format=WebhookSubscription.Format.GENERIC,
    )
    starting_version = sub.version
    with _tenant(org, actor):
        result = OperationsMutation().update_webhook_subscription(
            _info(actor),
            input=UpdateWebhookSubscriptionInput(
                id=str(sub.guid),
                url="https://example.invalid/wh2",
                if_match_version=starting_version,
            ),
        )
    assert result.ok, result.errors
    sub.refresh_from_db()
    assert sub.url == "https://example.invalid/wh2"
    assert sub.version == starting_version + 1


def test_update_webhook_subscription_with_stale_version_fails(permission_resolver):
    permission_resolver.grant(Permission.WEBHOOK_UPDATE)
    org = Organization.objects.create(name="Acme", slug="acme-wh-stale")
    actor = _user("op-wh-stale")
    sub = WebhookSubscription.objects.create(
        organization=org,
        url="https://example.invalid/wh",
        secret_hash="abc",
        events=["deploy.completed"],
        is_active=True,
        format=WebhookSubscription.Format.GENERIC,
    )
    stale = sub.version - 1
    with _tenant(org, actor):
        result = OperationsMutation().update_webhook_subscription(
            _info(actor),
            input=UpdateWebhookSubscriptionInput(
                id=str(sub.guid),
                url="https://example.invalid/overwrite",
                if_match_version=stale,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VERSION_MISMATCH"
    sub.refresh_from_db()
    assert sub.url == "https://example.invalid/wh"


# ---------------------------------------------------------------------
# 6. setAppSecret — match + stale


def test_set_app_secret_with_matching_version_succeeds(permission_resolver):
    """The step-up gate auto-bypasses when ``info.context.request`` has
    no ``.session`` attribute (direct test call), so this exercises the
    real resolver body."""
    permission_resolver.grant(Permission.APP_UPDATE)
    org, _, _, app = _scaffold()
    actor = _user("op-secret")
    starting_version = app.version
    with _tenant(org, actor):
        result = ServicesMutation().set_app_secret(
            _info(actor),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="API_KEY",
                value="abc123",
                if_match_version=starting_version,
            ),
        )
    assert result.ok, result.errors


def test_set_app_secret_with_stale_version_fails(permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    org, _, _, app = _scaffold()
    actor = _user("op-secret-stale")
    stale = app.version - 1
    with _tenant(org, actor):
        result = ServicesMutation().set_app_secret(
            _info(actor),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="API_KEY",
                value="overwrite",
                if_match_version=stale,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VERSION_MISMATCH"
    app.refresh_from_db()
    # No staging happened — the staged buffer is still empty.
    assert app.manifest_raw_staged in ("", None)


# ---------------------------------------------------------------------
# 7. Race semantics — first wins, second sees VERSION_MISMATCH


def test_concurrent_update_app_first_wins(permission_resolver):
    """Two operators read the same version, both try to write — the
    first commits, the second's ``ifMatchVersion`` is now stale and
    rejects cleanly."""
    permission_resolver.grant(Permission.APP_UPDATE)
    org, _, _, app = _scaffold()
    a = _user("op-race-a")
    b = _user("op-race-b")
    snapshot_version = app.version  # both clients read this

    with _tenant(org, a):
        first = RegistryMutation().update_app(
            _info(a),
            input=UpdateAppInput(
                id=str(app.guid),
                description="A wrote first",
                if_match_version=snapshot_version,
            ),
        )
    assert first.ok, first.errors

    with _tenant(org, b):
        second = RegistryMutation().update_app(
            _info(b),
            input=UpdateAppInput(
                id=str(app.guid),
                description="B tried after",
                if_match_version=snapshot_version,
            ),
        )
    assert not second.ok
    assert second.errors[0].code == "VERSION_MISMATCH"

    app.refresh_from_db()
    assert app.description == "A wrote first"


# ---------------------------------------------------------------------
# 8. Append-only mutations don't have the gate


def test_create_policy_has_no_version_field(permission_resolver):
    """Create mutations don't take ``ifMatchVersion`` — they aren't
    racing against a prior read of the same row."""
    permission_resolver.grant(Permission.ORG_UPDATE)
    permission_resolver.grant(Permission.ORG_READ)
    org = Organization.objects.create(name="Acme", slug="acme-create-noop")
    actor = _user("op-create-noop")
    # Sanity: ``CreatePolicyInput`` has no ``if_match_version`` attr.
    assert (
        not hasattr(CreatePolicyInput, "if_match_version")
        or "if_match_version" not in CreatePolicyInput.__annotations__
    )
    with _tenant(org, actor):
        result = IdentityMutation().create_policy(
            _info(actor),
            input=CreatePolicyInput(
                name="freshly-created",
                slug="freshly-created-497",
                scope_level="ORG",
                effect="DENY",
            ),
        )
    assert result.ok, result.errors
    # First version after the auto-increment fires on insert.
    assert result.data.version == 1


# ---------------------------------------------------------------------
# 9. Version field shape


def test_existing_rows_have_version_after_initial_save():
    """Backfill / migration coverage — every BaseCoreModel row has a
    ``version`` column populated from the auto-increment on ``.save()``.

    The initial migrations declare ``version IntegerField(default=0)``
    so rows that predate #497 (and any row inserted via raw SQL) start
    at 0; the first ORM save bumps to 1. New rows go through the
    overridden ``BaseCoreModel.save()`` which increments before insert,
    landing at 1 on the very first persisted state.
    """
    _, _, _, app = _scaffold()
    assert app.version == 1  # first save() flipped 0 -> 1.

    app.description = "second save"
    app.save()
    assert app.version == 2

    app.description = "third save"
    app.save()
    assert app.version == 3
