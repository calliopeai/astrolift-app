"""Tests for the app-secrets GraphQL mutations (#279, #424)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import AppSecretBundleRef, SecretBundle
from astrolift_services.schema.mutations import (
    AttachSecretBundleInput,
    BulkImportAppSecretsInput,
    DeleteAppSecretInput,
    DetachSecretBundleInput,
    RevealAppSecretInput,
    RotateAppSecretInput,
    ServicesMutation,
    SetAppSecretInput,
)
from astrolift_services.schema.queries import ServicesQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None, ip: str | None = None):
    """Build a minimal Strawberry-like Info shim.

    When ``user`` is given the resolver's `_actor_user` helper resolves
    it the same way it would in prod; `ip` populates the X-Forwarded-For
    header so reveal-disclosure audit rows capture the source."""

    if user is None:
        request = SimpleNamespace(user=None, META={})
    else:
        meta = {}
        if ip:
            meta["HTTP_X_FORWARDED_FOR"] = ip
        request = SimpleNamespace(user=user, META=meta)
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _make_user(username: str = "reveal-test"):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com", "first_name": "Op", "last_name": "Erator"},
    )
    return user


_BASE_TOML = """\
astrolift_version = 1
name = "hello"

[env]
KEEP_ME = "yes"

[[workloads]]
name = "web"
kind = "deployment"
"""


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo",
    )
    # Seeded directly; the plugin row is scaffolding for this test.
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s Native",
                slug="k8s-native",
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="k8s-native")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="local",
        name="Local",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
        manifest_raw=_BASE_TOML,
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    bundle = SecretBundle.objects.create(
        organization=org,
        team=team,
        slug="prod-secrets",
        name="Prod Secrets",
        backend_ref="vault:/acme/prod",
    )
    return org, app, env, bundle


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- setAppSecret ------------------------------------------------


def test_set_app_secret_writes_to_staging(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="API_KEY",
                value="abc123",
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    staged = app.manifest_raw_staged
    assert staged != ""  # staging populated
    parsed = read_app_env(staged)
    assert parsed["API_KEY"] == "abc123"
    assert parsed["KEEP_ME"] == "yes"  # preserved


def test_set_app_secret_invalid_key_rejected(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="bad-key",
                value="x",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "key"


def test_set_app_secret_non_ascii_key_rejected(permission_resolver):
    """A non-ASCII letter passes str.isalnum, but the deploy path skips the
    key (a Secret data key is ASCII), so storing it would be a silent
    no-op rather than a secret (#1758)."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key="CLÉ", value="x"),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "key"
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_set_app_secret_unknown_app_returns_not_found(permission_resolver):
    org, _, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug="missing",
                key="K",
                value="v",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_set_app_secret_requires_permission():
    org, app, _, _ = _scaffold()
    with _ctx(org):
        result = ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(
                app_slug=app.slug,
                key="K",
                value="v",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- deleteAppSecret --------------------------------------------


def test_delete_app_secret_removes_key(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().delete_app_secret(
            _info(),
            input=DeleteAppSecretInput(
                app_slug=app.slug,
                key="KEEP_ME",
            ),
        )

    assert result.ok
    app.refresh_from_db()
    parsed = read_app_env(app.manifest_raw_staged)
    assert "KEEP_ME" not in parsed


def test_delete_app_secret_missing_key_not_found(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().delete_app_secret(
            _info(),
            input=DeleteAppSecretInput(
                app_slug=app.slug,
                key="NEVER_EXISTED",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---- rotateAppSecret (#726) --------------------------------------


def test_rotate_app_secret_writes_new_value(permission_resolver):
    """``rotate_app_secret`` stages the new value just like ``set_app_secret``
    — the lifecycle differs only in the audit action."""
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().rotate_app_secret(
            _info(),
            input=RotateAppSecretInput(
                app_slug=app.slug,
                key="API_KEY",
                value="rotated-1",
            ),
        )

    assert result.ok, result.errors
    app.refresh_from_db()
    parsed = read_app_env(app.manifest_raw_staged)
    assert parsed["API_KEY"] == "rotated-1"
    assert parsed["KEEP_ME"] == "yes"  # untouched


def test_rotate_app_secret_invalid_key_rejected(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().rotate_app_secret(
            _info(),
            input=RotateAppSecretInput(
                app_slug=app.slug,
                key="bad-key",
                value="x",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "key"


def test_rotate_app_secret_unknown_app_returns_not_found(permission_resolver):
    org, _, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    with _ctx(org):
        result = ServicesMutation().rotate_app_secret(
            _info(),
            input=RotateAppSecretInput(
                app_slug="missing",
                key="K",
                value="v",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_rotate_app_secret_requires_permission():
    org, app, _, _ = _scaffold()
    with _ctx(org):
        result = ServicesMutation().rotate_app_secret(
            _info(),
            input=RotateAppSecretInput(
                app_slug=app.slug,
                key="K",
                value="v",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_rotate_app_secret_emits_distinct_audit_action(permission_resolver):
    """The whole point of #726: SRE can tell rotates apart from sets in
    the audit timeline. Confirm the resulting ``AuditEvent.action`` row
    is ``app.secret.rotate`` (not ``app.secret.set``)."""
    from astrolift_operations.models import AuditEvent

    org, app, _, _ = _scaffold()
    user = _make_user("rotator")
    permission_resolver.grant(Permission.APP_UPDATE)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = ServicesMutation().rotate_app_secret(
            _info(user=user),
            input=RotateAppSecretInput(
                app_slug=app.slug,
                key="API_KEY",
                value="rotated-2",
            ),
        )
    assert result.ok, result.errors

    events = list(
        AuditEvent.objects.filter(
            target_kind="AppSecret",
            target_id=f"{app.slug}:API_KEY",
        ).order_by("-occurred_at")
    )
    assert events, "expected an AuditEvent for the rotate"
    assert events[0].action == "app.secret.rotate"


# ---- bulkImportAppSecrets ---------------------------------------


def test_bulk_import_parses_and_stages(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    text = """\
# Production envs
DATABASE_URL=postgres://prod
LOG_LEVEL=warn
SENTRY_DSN="https://x@y.io/1"
"""
    with _ctx(org):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(),
            input=BulkImportAppSecretsInput(
                app_slug=app.slug,
                dotenv_text=text,
            ),
        )

    assert result.ok, result.errors
    assert set(result.data.keys_set) == {
        "DATABASE_URL",
        "LOG_LEVEL",
        "SENTRY_DSN",
    }
    app.refresh_from_db()
    parsed = read_app_env(app.manifest_raw_staged)
    assert parsed["DATABASE_URL"] == "postgres://prod"
    assert parsed["KEEP_ME"] == "yes"  # preserved


def test_bulk_import_empty_returns_validation(permission_resolver):
    org, app, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(),
            input=BulkImportAppSecretsInput(
                app_slug=app.slug,
                dotenv_text="# only comments\n",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


# ---- attachSecretBundle ------------------------------------------


def test_attach_secret_bundle_creates_ref(permission_resolver):
    org, app, env, bundle = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().attach_secret_bundle(
            _info(),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
                prefix="STRIPE_",
            ),
        )

    assert result.ok, result.errors
    refs = AppSecretBundleRef.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
    )
    assert refs.count() == 1
    assert refs.first().prefix == "STRIPE_"


def test_attach_secret_bundle_idempotent_updates_prefix(
    permission_resolver,
):
    org, app, env, bundle = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        ServicesMutation().attach_secret_bundle(
            _info(),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
                prefix="A_",
            ),
        )
        result = ServicesMutation().attach_secret_bundle(
            _info(),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
                prefix="B_",
            ),
        )

    assert result.ok
    refs = AppSecretBundleRef.objects.filter(
        registered_app=app,
        deleted_at__isnull=True,
    )
    assert refs.count() == 1
    assert refs.first().prefix == "B_"


def test_attach_secret_bundle_unknown_env_not_found(permission_resolver):
    org, app, _, bundle = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = ServicesMutation().attach_secret_bundle(
            _info(),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name="never",
                bundle_slug=bundle.slug,
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_detach_secret_bundle_soft_deletes(permission_resolver):
    org, app, env, bundle = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    ref = AppSecretBundleRef.objects.create(
        registered_app=app,
        app_environment=env,
        secret_bundle=bundle,
    )

    with _ctx(org):
        result = ServicesMutation().detach_secret_bundle(
            _info(),
            input=DetachSecretBundleInput(
                attachment_id=str(ref.guid),
            ),
        )

    assert result.ok
    ref.refresh_from_db()
    assert ref.deleted_at is not None


# ---- #424: revealAppSecret ---------------------------------------


def test_reveal_app_secret_returns_literal_plaintext(permission_resolver):
    org, app, env, _ = _scaffold()
    user = _make_user("revealer")
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.SECRET_READ)

    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(user=user),
            input=SetAppSecretInput(app_slug=app.slug, key="API_KEY", value="topsecret"),
        )
        result = ServicesMutation().reveal_app_secret(
            _info(user=user, ip="203.0.113.7"),
            input=RevealAppSecretInput(
                app_slug=app.slug,
                secret_id=f"literal:{env.name}:API_KEY",
            ),
        )

    assert result.ok, result.errors
    assert result.data.value == "topsecret"
    assert result.data.key == "API_KEY"
    assert result.data.environment_name == env.name
    assert result.data.revealed_at is not None


def test_reveal_app_secret_requires_secret_read(permission_resolver):
    org, app, env, _ = _scaffold()
    user = _make_user("noperms")
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    # SECRET_READ intentionally not granted

    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(user=user),
            input=SetAppSecretInput(app_slug=app.slug, key="API_KEY", value="x"),
        )
        result = ServicesMutation().reveal_app_secret(
            _info(user=user),
            input=RevealAppSecretInput(
                app_slug=app.slug,
                secret_id=f"literal:{env.name}:API_KEY",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


def test_reveal_app_secret_rejects_bundle_source(permission_resolver):
    org, app, env, _ = _scaffold()
    user = _make_user("rev2")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.SECRET_READ)

    with _ctx(org):
        result = ServicesMutation().reveal_app_secret(
            _info(user=user),
            input=RevealAppSecretInput(
                app_slug=app.slug,
                secret_id=f"bundle:{env.name}:stripe-prod",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_reveal_app_secret_missing_key_not_found(permission_resolver):
    org, app, env, _ = _scaffold()
    user = _make_user("rev3")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.SECRET_READ)

    with _ctx(org):
        result = ServicesMutation().reveal_app_secret(
            _info(user=user),
            input=RevealAppSecretInput(
                app_slug=app.slug,
                secret_id=f"literal:{env.name}:NEVER_SET",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_reveal_app_secret_validates_id_shape(permission_resolver):
    org, app, _, _ = _scaffold()
    user = _make_user("rev4")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.SECRET_READ)

    with _ctx(org):
        result = ServicesMutation().reveal_app_secret(
            _info(user=user),
            input=RevealAppSecretInput(
                app_slug=app.slug,
                secret_id="not-a-valid-id",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"


def test_reveal_app_secret_emits_disclosure_audit_with_ip(monkeypatch, permission_resolver):
    org, app, env, _ = _scaffold()
    user = _make_user("auditor")
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.SECRET_READ)

    captured = []

    from core import mutations as core_mutations

    original = core_mutations._audit_writer
    core_mutations.register_audit_writer(lambda entry: captured.append(entry))
    try:
        with _ctx(org):
            ServicesMutation().set_app_secret(
                _info(user=user),
                input=SetAppSecretInput(app_slug=app.slug, key="API_KEY", value="v"),
            )
            ServicesMutation().reveal_app_secret(
                _info(user=user, ip="198.51.100.42"),
                input=RevealAppSecretInput(
                    app_slug=app.slug,
                    secret_id=f"literal:{env.name}:API_KEY",
                ),
            )
    finally:
        core_mutations.register_audit_writer(original)

    disclosures = [e for e in captured if e.action == "app.secret.reveal.disclosure"]
    assert len(disclosures) == 1
    assert disclosures[0].extra["client_ip"] == "198.51.100.42"
    assert disclosures[0].extra["key"] == "API_KEY"
    assert disclosures[0].extra["app_slug"] == app.slug


# ---- #424: lastEditedBy + attachment metadata --------------------


def test_set_app_secret_sets_updated_by(permission_resolver):
    org, app, _, _ = _scaffold()
    user = _make_user("setter")
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(user=user),
            input=SetAppSecretInput(app_slug=app.slug, key="K", value="v"),
        )

    app.refresh_from_db()
    assert app.updated_by_id == user.pk


def test_app_secrets_query_surfaces_last_edited_by(permission_resolver):
    org, app, env, _ = _scaffold()
    user = _make_user("editor")
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(org):
        ServicesMutation().set_app_secret(
            _info(user=user),
            input=SetAppSecretInput(app_slug=app.slug, key="API_KEY", value="x"),
        )
        rows = ServicesQuery().astrolift_app_secrets(
            _info(user=user),
            app_slug=app.slug,
            environment_name=env.name,
        )

    literal_rows = [r for r in rows if r.source == "literal" and r.key == "API_KEY"]
    assert len(literal_rows) == 1
    editor = literal_rows[0].last_edited_by
    assert editor is not None
    assert editor.username == "editor"


def test_attachment_query_surfaces_team_count_order(permission_resolver):
    org, app, env, bundle = _scaffold()
    user = _make_user("attacher")
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)

    # Create a second bundle so we can assert merge-order numbering.
    bundle_b = SecretBundle.objects.create(
        organization=org,
        team=bundle.team,
        slug="prod-extras",
        name="Prod Extras",
        backend_ref="vault:/acme/prod-extras",
    )
    with _ctx(org):
        ServicesMutation().attach_secret_bundle(
            _info(user=user),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
                prefix="A_",
            ),
        )
        ServicesMutation().attach_secret_bundle(
            _info(user=user),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle_b.slug,
                prefix="B_",
            ),
        )
        rows = ServicesQuery().astrolift_app_secret_bundle_attachments(
            _info(user=user),
            app_slug=app.slug,
            environment_name=env.name,
        )

    assert [r.bundle_slug for r in rows] == ["prod-secrets", "prod-extras"]
    assert [r.merge_order for r in rows] == [0, 1]
    assert rows[0].team_slug == bundle.team.slug
    assert rows[0].attached_at is not None
    # key_count comes from the SecretBundle.last_known_keys cache (#441).
    # No driver is wired in this scaffold (the cluster has the k8s_native
    # plugin but no secrets driver registered), and we didn't pre-seed the
    # cache, so the count is 0 + the UI surfaces '?'.
    assert rows[0].key_count == 0
