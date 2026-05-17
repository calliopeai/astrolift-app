"""Tests for per-service quick action mutations + queries (#401)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_operations.email_infra import (
    EmailKind,
    TransportKind,
    clear_transport,
    set_transport,
)
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_services.schema.mutations import (
    RevealManagedServiceConnectionInput,
    SendManagedServiceTestEmailInput,
    ServicesMutation,
)
from astrolift_services.schema.queries import ServicesQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


def _info(user=None, ip: str | None = None):
    """Minimal Strawberry-like Info shim — mirrors test_secrets_mutations."""
    if user is None:
        request = SimpleNamespace(user=None, META={})
    else:
        meta: dict[str, str] = {}
        if ip:
            meta["HTTP_X_FORWARDED_FOR"] = ip
        request = SimpleNamespace(user=user, META=meta)
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _make_user(username: str = "msvc-action-test"):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={
            "email": f"{username}@example.com",
            "first_name": "Op",
            "last_name": "Erator",
        },
    )
    return user


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme-msvc")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-msvc")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo-msvc",
    )
    # bulk_create bypasses Tracking.save() so the string ProviderPlugin
    # version field doesn't get incremented to a non-semver value.
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="K8s Native",
                slug="k8s-native",
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    plugin = ProviderPlugin.objects.get(slug="k8s-native")
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="local-msvc",
        name="Local",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="MSVC App",
        slug="msvc-app",
        provisioning_status="ready",
        manifest_raw='astrolift_version = 1\nname = "msvc"\n',
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    return org, app, env


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- revealManagedServiceConnection -----------------------------------


def test_reveal_postgres_returns_envelope_keys(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        variant="rds-postgres",
        connection_secret_ref="vault:/acme/app/prod/postgres-primary",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().reveal_managed_service_connection(
            _info(user=_make_user(), ip="10.0.0.7"),
            input=RevealManagedServiceConnectionInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert result.ok, result.errors
    payload = result.data
    assert payload is not None
    assert payload.kind == "postgres"
    assert payload.connection_secret_ref == "vault:/acme/app/prod/postgres-primary"
    keys = {k.key for k in payload.keys}
    # Envelope from astrolift_manifest.env_injection — postgres set
    assert "POSTGRES_HOST" in keys
    assert "POSTGRES_PASSWORD" in keys
    assert "DATABASE_URL" in keys
    # Plaintext is NEVER returned; values are opaque pointers.
    pw = next(k for k in payload.keys if k.key == "POSTGRES_PASSWORD")
    assert pw.value.startswith("secret-ref:vault:/acme/")
    assert pw.is_secret is True
    # Public keys (HOST, PORT, region) marked non-secret so the UI
    # doesn't mask them.
    host = next(k for k in payload.keys if k.key == "POSTGRES_HOST")
    assert host.is_secret is False
    # The cached last-action surface should be stamped so the summary
    # card can render "revealed N seconds ago" without re-walking audit.
    svc.refresh_from_db()
    assert svc.last_action_kind == "connection.reveal"
    assert svc.last_action_at is not None


def test_reveal_without_connection_ref_uses_placeholder(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.REDIS,
        name="cache",
        status=ManagedService.Status.PENDING,
    )
    with _ctx(org):
        result = ServicesMutation().reveal_managed_service_connection(
            _info(user=_make_user()),
            input=RevealManagedServiceConnectionInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert result.ok, result.errors
    assert result.data is not None
    assert all(k.value.startswith("placeholder:") for k in result.data.keys if k.is_secret)


def test_reveal_kind_without_envelope_rejected(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.MODEL_ENDPOINT,
        name="llm",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().reveal_managed_service_connection(
            _info(user=_make_user()),
            input=RevealManagedServiceConnectionInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_reveal_missing_managed_service_not_found(permission_resolver):
    org, _, _ = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    with _ctx(org):
        result = ServicesMutation().reveal_managed_service_connection(
            _info(user=_make_user()),
            input=RevealManagedServiceConnectionInput(
                managed_service_id=GUID("00000000-0000-0000-0000-000000000001"),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_reveal_requires_permission():
    org, app, env = _scaffold()
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().reveal_managed_service_connection(
            _info(user=_make_user()),
            input=RevealManagedServiceConnectionInput(
                managed_service_id=GUID(str(svc.guid)),
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- sendManagedServiceTestEmail --------------------------------------


@pytest.fixture
def stub_email_transport():
    """Register an in-memory transport so the test send doesn't hit
    a real provider — captures the Email payload for assertion."""
    captured: list = []

    def transport(email) -> None:
        captured.append(email)

    set_transport(kind=TransportKind.AWS_SES, fn=transport)
    yield captured
    clear_transport()


def test_send_test_email_routes_through_transport(permission_resolver, stub_email_transport):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.EMAIL,
        name="ses-default",
        variant="ses",
        config={"email_from": "noreply@acme.test"},
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().send_managed_service_test_email(
            _info(user=_make_user(), ip="203.0.113.42"),
            input=SendManagedServiceTestEmailInput(
                managed_service_id=GUID(str(svc.guid)),
                recipient="qa@acme.test",
                subject="Custom subject",
                body="Hello there",
            ),
        )
    assert result.ok, result.errors
    payload = result.data
    assert payload is not None
    assert payload.recipient == "qa@acme.test"
    assert payload.subject == "Custom subject"
    assert payload.transport == TransportKind.AWS_SES.value
    assert len(stub_email_transport) == 1
    sent = stub_email_transport[0]
    assert sent.to_address == "qa@acme.test"
    assert sent.from_address == "noreply@acme.test"
    assert sent.kind == EmailKind.MANAGED_SERVICE_TEST
    svc.refresh_from_db()
    assert svc.last_action_kind == "test_email.send"


def test_send_test_email_defaults_subject_and_body(permission_resolver, stub_email_transport):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.EMAIL,
        name="ses-default",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().send_managed_service_test_email(
            _info(user=_make_user()),
            input=SendManagedServiceTestEmailInput(
                managed_service_id=GUID(str(svc.guid)),
                recipient="qa@acme.test",
            ),
        )
    assert result.ok, result.errors
    sent = stub_email_transport[0]
    assert sent.subject.startswith("[Astrolift]")
    assert "msvc-app" in sent.plain_body or "ses-default" in sent.plain_body


def test_send_test_email_non_email_kind_rejected(permission_resolver, stub_email_transport):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().send_managed_service_test_email(
            _info(user=_make_user()),
            input=SendManagedServiceTestEmailInput(
                managed_service_id=GUID(str(svc.guid)),
                recipient="qa@acme.test",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    assert stub_email_transport == []


def test_send_test_email_invalid_recipient_rejected(permission_resolver, stub_email_transport):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.EMAIL,
        name="ses-default",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().send_managed_service_test_email(
            _info(user=_make_user()),
            input=SendManagedServiceTestEmailInput(
                managed_service_id=GUID(str(svc.guid)),
                recipient="not-an-email",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "recipient"


def test_send_test_email_without_transport_returns_precondition(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.MANAGED_SERVICE_UPDATE)
    # Ensure no transport is registered for this test
    clear_transport()
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.EMAIL,
        name="ses-default",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().send_managed_service_test_email(
            _info(user=_make_user()),
            input=SendManagedServiceTestEmailInput(
                managed_service_id=GUID(str(svc.guid)),
                recipient="qa@acme.test",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"


def test_send_test_email_requires_permission():
    org, app, env = _scaffold()
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.EMAIL,
        name="ses-default",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesMutation().send_managed_service_test_email(
            _info(user=_make_user()),
            input=SendManagedServiceTestEmailInput(
                managed_service_id=GUID(str(svc.guid)),
                recipient="qa@acme.test",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"


# ---- listManagedServiceObjects ----------------------------------------


def test_list_objects_reads_cached_snapshot(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    sampled = timezone.now().isoformat()
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.OBJECT_STORE,
        name="uploads",
        config={
            "recent_objects": [
                {"key": "a.txt", "size_bytes": 12, "last_modified": sampled},
                {"key": "b/c.png", "size_bytes": 9001, "last_modified": sampled},
            ],
            "recent_objects_sampled_at": sampled,
        },
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesQuery().astrolift_managed_service_objects(
            _info(user=_make_user()),
            managed_service_id=GUID(str(svc.guid)),
            limit=10,
        )
    assert result is not None
    assert [o.key for o in result.objects] == ["a.txt", "b/c.png"]
    assert result.objects[1].size_bytes == 9001
    assert result.cache_age_seconds is not None
    assert result.cache_age_seconds >= 0


def test_list_objects_non_object_store_returns_empty(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesQuery().astrolift_managed_service_objects(
            _info(user=_make_user()),
            managed_service_id=GUID(str(svc.guid)),
        )
    assert result is not None
    assert result.objects == []


def test_list_objects_requires_permission():
    org, app, env = _scaffold()
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.OBJECT_STORE,
        name="uploads",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        with pytest.raises(PermissionDenied):
            ServicesQuery().astrolift_managed_service_objects(
                _info(user=_make_user()),
                managed_service_id=GUID(str(svc.guid)),
            )


# ---- managedServiceQueueDepth -----------------------------------------


def test_queue_depth_reads_cached_snapshot(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    sampled = timezone.now().isoformat()
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.QUEUE,
        name="jobs",
        config={
            "depth_snapshot": {
                "depth": 42,
                "in_flight": 3,
                "sampled_at": sampled,
            },
        },
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesQuery().astrolift_managed_service_queue_depth(
            _info(user=_make_user()),
            managed_service_id=GUID(str(svc.guid)),
        )
    assert result is not None
    assert result.depth == 42
    assert result.in_flight == 3
    assert result.sampled_at is not None


def test_queue_depth_non_queue_returns_zero(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.ACTIVE,
    )
    with _ctx(org):
        result = ServicesQuery().astrolift_managed_service_queue_depth(
            _info(user=_make_user()),
            managed_service_id=GUID(str(svc.guid)),
        )
    assert result is not None
    assert result.depth == 0


# ---- astroliftManagedServices (existing query) carries new fields ----


def test_managed_services_query_exposes_last_action_fields(permission_resolver):
    org, app, env = _scaffold()
    permission_resolver.grant(Permission.APP_READ)
    svc = ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="primary",
        status=ManagedService.Status.ACTIVE,
        status_error="prior failure noted",
    )
    svc.last_action_at = timezone.now()
    svc.last_action_kind = "connection.reveal"
    svc.save()
    with _ctx(org):
        rows = ServicesQuery().astrolift_managed_services(
            _info(user=_make_user()),
            app_slug=app.slug,
        )
    assert len(rows) == 1
    assert rows[0].status_error == "prior failure noted"
    assert rows[0].last_action_kind == "connection.reveal"
    assert rows[0].last_action_at is not None
