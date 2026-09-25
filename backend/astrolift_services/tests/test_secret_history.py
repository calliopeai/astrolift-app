"""Tests for the per-key app-secret history query (#725).

Backed by ``astrolift_operations.AuditEvent`` rows written by the
``@mutation_audit`` writer registered in ``astrolift_operations.apps``.
Tests drive the writer end-to-end (call the real mutation, then read
the query) rather than synthesizing audit rows by hand — that way the
``target_kind`` / ``target_id`` contract between writer + reader stays
honest. A synthesized-row regression test guards the read side
explicitly so a writer regression can't quietly mask the query-shape
bug.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Member, Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.models import AuditEvent
from astrolift_registry.models import RegisteredApp
from astrolift_services.schema.mutations import (
    DeleteAppSecretInput,
    RotateAppSecretInput,
    ServicesMutation,
    SetAppSecretInput,
)
from astrolift_services.schema.queries import ServicesQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


_BASE_TOML = """\
astrolift_version = 1
name = "hist"

[env]
KEEP_ME = "yes"

[[workloads]]
name = "web"
kind = "deployment"
"""


def _info(user=None):
    if user is None:
        request = SimpleNamespace(user=None, META={})
    else:
        request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _make_user(username: str):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _scaffold():
    org = Organization.objects.create(name="History", slug="hist")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo",
    )
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
        name="History",
        slug="history-app",
        provisioning_status="ready",
        manifest_raw=_BASE_TOML,
    )
    AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return org, app


def _ctx(org, user=None):
    actor_id = user.id if user is not None else None
    if user is not None:
        # Audit rows are filed under the org only for its members (#1955).
        Member.objects.get_or_create(user=user, scope_kind=Member.ScopeKind.ORG, scope_id=org.id)
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor_id))


def test_secret_history_returns_newest_first(permission_resolver):
    org, app = _scaffold()
    user = _make_user("ana")
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)

    # Drive three writes against the same (app, key); each emits an
    # AuditEvent row through the registered writer.
    with _ctx(org, user):
        ServicesMutation().set_app_secret(
            _info(user),
            input=SetAppSecretInput(app_slug=app.slug, key="DB_URL", value="v1"),
        )
        ServicesMutation().rotate_app_secret(
            _info(user),
            input=RotateAppSecretInput(app_slug=app.slug, key="DB_URL", value="v2"),
        )
        ServicesMutation().delete_app_secret(
            _info(user),
            input=DeleteAppSecretInput(app_slug=app.slug, key="DB_URL"),
        )

    with _ctx(org, user):
        rows = ServicesQuery().astrolift_app_secret_history(
            _info(user),
            app_slug=app.slug,
            key="DB_URL",
        )

    # Newest first: delete, then rotate, then set.
    assert [r.action for r in rows] == ["delete", "rotate", "set"]
    for r in rows:
        assert r.actor.username == "ana"
        assert r.success is True
        assert r.error_code == ""


def test_secret_history_isolates_keys(permission_resolver):
    """Writes to a different key on the same app don't bleed into the
    requested key's history — that's the whole point of the composite
    target id."""
    org, app = _scaffold()
    user = _make_user("bob")
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(org, user):
        ServicesMutation().set_app_secret(
            _info(user),
            input=SetAppSecretInput(app_slug=app.slug, key="WANTED", value="x"),
        )
        ServicesMutation().set_app_secret(
            _info(user),
            input=SetAppSecretInput(app_slug=app.slug, key="OTHER", value="y"),
        )

    with _ctx(org, user):
        rows = ServicesQuery().astrolift_app_secret_history(
            _info(user),
            app_slug=app.slug,
            key="WANTED",
        )

    assert len(rows) == 1
    assert rows[0].action == "set"


def test_secret_history_unknown_app_returns_empty(permission_resolver):
    org, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    with _ctx(org):
        rows = ServicesQuery().astrolift_app_secret_history(
            _info(),
            app_slug="does-not-exist",
            key="K",
        )
    assert rows == []


def test_secret_history_requires_permission():
    """Without ``APP_READ`` the resolver should not return any rows.

    ``require_permission`` raises ``PermissionDenied`` from the query
    path (queries have no MutationResult envelope) — assert the
    exception class is raised by the decorator stack."""
    from core.permissions import PermissionDenied

    org, app = _scaffold()
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            ServicesQuery().astrolift_app_secret_history(
                _info(),
                app_slug=app.slug,
                key="DB_URL",
            )


def test_secret_history_caps_at_50(permission_resolver):
    """A flurry of writes is capped to the 50 most-recent rows. Avoid
    hammering the real mutation 60 times — synthesize the audit rows
    directly with the same shape the writer produces (this is also the
    read-side regression test for the target_id encoding)."""
    org, app = _scaffold()
    user = _make_user("carol")
    permission_resolver.grant(Permission.APP_READ)

    for _ in range(60):
        AuditEvent.objects.create(
            organization_id=org.id,
            actor_kind="user",
            actor_id=str(user.id),
            action="app.secret.set",
            decision="ALLOW",
            target_kind="AppSecret",
            target_id=f"{app.slug}:BIG_KEY",
            data={"duration_ms": 1, "permissions": [], "error_code": None, "error_message": None},
        )

    with _ctx(org, user):
        rows = ServicesQuery().astrolift_app_secret_history(
            _info(user),
            app_slug=app.slug,
            key="BIG_KEY",
        )
    assert len(rows) == 50
    assert rows[0].actor.username == "carol"


def test_secret_history_surfaces_error_code_on_failed_write(permission_resolver):
    """Audit rows for failed writes (validation, denied, etc.) end up in
    history with ``success=False`` + the resolver's error code surfaced
    on the row. SRE uses this to spot repeated denied attempts.

    The writer side stores ``error_code`` in ``data``; synthesize a row
    in that shape so the read side can be asserted in isolation from a
    real failed-mutation path."""
    org, app = _scaffold()
    user = _make_user("eve")
    permission_resolver.grant(Permission.APP_READ)

    AuditEvent.objects.create(
        organization_id=org.id,
        actor_kind="user",
        actor_id=str(user.id),
        action="app.secret.set",
        decision="ALLOW",
        target_kind="AppSecret",
        target_id=f"{app.slug}:FLAKY",
        data={
            "duration_ms": 2,
            "permissions": [],
            "error_code": "VALIDATION",
            "error_message": "bad key",
        },
        request_ip="10.0.0.1",
    )

    with _ctx(org, user):
        rows = ServicesQuery().astrolift_app_secret_history(
            _info(user),
            app_slug=app.slug,
            key="FLAKY",
        )

    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].error_code == "VALIDATION"
    assert rows[0].source_ip == "10.0.0.1"


def test_secret_history_omits_untargeted_legacy_rows(permission_resolver):
    """Audit rows that pre-date #725's targeting (no ``target_kind``)
    are intentionally not returned — they cannot be associated with a
    specific (app, key) pair without ambiguity."""
    org, app = _scaffold()
    user = _make_user("dave")
    permission_resolver.grant(Permission.APP_READ)

    # Legacy untargeted row — same action, but no target encoding.
    AuditEvent.objects.create(
        organization_id=org.id,
        actor_kind="user",
        actor_id=str(user.id),
        action="app.secret.set",
        decision="ALLOW",
        target_kind="",
        target_id="",
        data={"duration_ms": 1},
    )

    with _ctx(org, user):
        rows = ServicesQuery().astrolift_app_secret_history(
            _info(user),
            app_slug=app.slug,
            key="WHATEVER",
        )
    assert rows == []
