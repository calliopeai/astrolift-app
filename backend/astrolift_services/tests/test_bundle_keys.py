"""Tests for the bundle key reflector (#441).

Covers the cache write + refresh primitives in
``astrolift_services.bundle_keys`` + the GraphQL surfaces that consume
them (``keyCount`` on attachments, ``keyCount`` /
``lastKnownKeysAt`` on the bundle).

Tests use a fake secrets-backend conforming to the vendor SDK
``SecretsBackend`` protocol so the platform driver registry stays out
of scope -- the protocol contract is what the resolver depends on.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services import bundle_keys
from astrolift_services.bundle_keys import (
    STALE_AFTER,
    _set_known_keys,
    force_refresh_bundle_known_keys,
    known_key_count,
    maybe_refresh_bundle_known_keys,
    refresh_bundle_known_keys,
)
from astrolift_services.models import AppSecretBundleRef, SecretBundle
from astrolift_services.schema.mutations import (
    AttachSecretBundleInput,
    ServicesMutation,
)
from astrolift_services.schema.queries import ServicesQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---- helpers -----------------------------------------------------


class FakeSecretsBackend:
    """Conforms to vendor _sdk.secrets.SecretsBackend (duck-typed -- the
    protocol is structural and the platform code only calls ``get`` +
    ``list_keys``).  Returns ``payload`` from both calls so the
    protocol-default ``list_keys = sorted(get().keys())`` path is the
    one under test."""

    def __init__(self, payload: dict[str, str] | None):
        self._payload = payload
        self.get_calls = 0
        self.list_keys_calls = 0

    def get(self, path: str) -> dict[str, str] | None:
        self.get_calls += 1
        return None if self._payload is None else dict(self._payload)

    def list_keys(self, path: str) -> list[str]:
        self.list_keys_calls += 1
        if self._payload is None:
            return []
        return sorted(self._payload.keys())


class UnimplementedListKeysBackend(FakeSecretsBackend):
    def list_keys(self, path: str) -> list[str]:
        raise NotImplementedError("driver-side opt-out")


class NoListKeysBackend:
    """Older driver shape -- has ``get`` but no ``list_keys`` at all.
    Verifies the resolver tolerates pre-#441 drivers."""

    def __init__(self, payload):
        self._payload = payload

    def get(self, path: str) -> dict[str, str] | None:
        return None if self._payload is None else dict(self._payload)


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _make_user(username: str = "kc"):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )
    return user


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme-kc")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-kc")
    project = Project.objects.create(
        organization=org,
        team=team,
        name="Demo",
        slug="demo-kc",
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
        slug="local-kc",
        name="Local",
        provider_plugin=plugin,
        endpoint="http://localhost:8443",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-kc",
        provisioning_status="ready",
        manifest_raw="astrolift_version = 1\nname = 'hello'\n",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
    )
    bundle = SecretBundle.objects.create(
        organization=org,
        team=team,
        slug="prod-secrets-kc",
        name="Prod Secrets",
        backend_ref="vault:/acme/prod",
    )
    return org, app, env, bundle


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


# ---- SDK protocol contract (#441 A) -----------------------------


def test_sdk_protocol_default_list_keys_returns_sorted_keys():
    """The vendor _sdk.secrets.SecretsBackend Protocol provides a
    default ``list_keys`` that returns sorted ``get()`` keys without
    revealing values.  Verify that contract on a class that
    explicitly inherits from the Protocol."""
    # Import the actual vendor protocol (not the fake) so a behaviour
    # regression in the SDK contract surfaces here.
    import sys
    from pathlib import Path

    providers_root = Path(__file__).resolve().parents[3] / "providers"
    if str(providers_root) not in sys.path:
        sys.path.insert(0, str(providers_root))
    from _sdk.secrets import SecretsBackend

    class _Concrete(SecretsBackend):
        def __init__(self, payload):
            self._payload = payload
            self.get_calls = 0

        def get(self, path):  # type: ignore[override]
            self.get_calls += 1
            return self._payload

        def upsert(self, path, kvs):  # noqa: ARG002
            return None

        def delete(self, path):  # noqa: ARG002
            return None

        def list(self, prefix):  # noqa: ARG002
            return []

    backend = _Concrete({"DB_URL": "x", "API_KEY": "y", "REDIS_URL": "z"})
    keys = backend.list_keys("vault:/whatever")
    assert keys == ["API_KEY", "DB_URL", "REDIS_URL"]
    assert backend.get_calls == 1  # default fetches once
    # Verify None payload normalises to []
    empty = _Concrete(None)
    assert empty.list_keys("vault:/missing") == []


# ---- refresh_bundle_known_keys (#441 B) -------------------------


def test_refresh_bundle_known_keys_persists_sorted_unique_set():
    _, _, _, bundle = _scaffold()
    backend = FakeSecretsBackend({"FOO": "v", "BAR": "v"})
    keys = refresh_bundle_known_keys(bundle, secrets_backend=backend)
    assert keys == ["BAR", "FOO"]

    bundle.refresh_from_db()
    assert bundle.last_known_keys == ["BAR", "FOO"]
    assert bundle.last_key_enum_at is not None


def test_refresh_bundle_known_keys_swallows_not_implemented_and_keeps_prior():
    _, _, _, bundle = _scaffold()
    _set_known_keys(bundle, ["KEPT_FROM_LAST_RUN"])
    bundle.refresh_from_db()
    backend = UnimplementedListKeysBackend({"NEW": "v"})
    keys = refresh_bundle_known_keys(bundle, secrets_backend=backend)
    assert keys == ["KEPT_FROM_LAST_RUN"]
    bundle.refresh_from_db()
    assert bundle.last_known_keys == ["KEPT_FROM_LAST_RUN"]


def test_refresh_bundle_known_keys_tolerates_driver_without_list_keys():
    _, _, _, bundle = _scaffold()
    _set_known_keys(bundle, ["PRIOR"])
    bundle.refresh_from_db()
    backend = NoListKeysBackend({"NEW": "v"})
    keys = refresh_bundle_known_keys(bundle, secrets_backend=backend)
    assert keys == ["PRIOR"]


# ---- maybe_refresh_bundle_known_keys (lazy on read) -------------


def test_maybe_refresh_skips_when_cache_fresh(monkeypatch):
    _, _, _, bundle = _scaffold()
    _set_known_keys(bundle, ["A", "B"])
    bundle.refresh_from_db()

    called = {"n": 0}

    def _boom(_bundle):
        called["n"] += 1
        raise AssertionError("should not resolve backend when cache fresh")

    monkeypatch.setattr(bundle_keys, "_resolve_secrets_backend_for", _boom)
    out = maybe_refresh_bundle_known_keys(bundle)
    assert out == ["A", "B"]
    assert called["n"] == 0


def test_maybe_refresh_runs_when_stale(monkeypatch):
    _, app, env, bundle = _scaffold()
    _set_known_keys(bundle, ["OLD"])
    # Push the timestamp backwards past STALE_AFTER.
    SecretBundle.all_objects.filter(pk=bundle.pk).update(
        last_key_enum_at=timezone.now() - STALE_AFTER - timedelta(minutes=1),
    )
    bundle.refresh_from_db()
    backend = FakeSecretsBackend({"NEW1": "v", "NEW2": "v"})
    monkeypatch.setattr(
        bundle_keys,
        "_resolve_secrets_backend_for",
        lambda _b: backend,
    )
    out = maybe_refresh_bundle_known_keys(bundle)
    assert out == ["NEW1", "NEW2"]
    bundle.refresh_from_db()
    assert bundle.last_known_keys == ["NEW1", "NEW2"]


def test_maybe_refresh_swallows_backend_error_and_returns_prior(monkeypatch):
    _, _, _, bundle = _scaffold()
    _set_known_keys(bundle, ["PRIOR"])
    SecretBundle.all_objects.filter(pk=bundle.pk).update(
        last_key_enum_at=timezone.now() - STALE_AFTER - timedelta(minutes=5),
    )
    bundle.refresh_from_db()

    def _boom(_b):
        raise RuntimeError("vault unreachable")

    monkeypatch.setattr(bundle_keys, "_resolve_secrets_backend_for", _boom)
    out = maybe_refresh_bundle_known_keys(bundle)
    assert out == ["PRIOR"]


def test_maybe_refresh_skips_empty_backend_ref():
    _, _, _, bundle = _scaffold()
    SecretBundle.all_objects.filter(pk=bundle.pk).update(backend_ref="")
    bundle.refresh_from_db()
    # No backend resolution happens because backend_ref is empty.
    assert maybe_refresh_bundle_known_keys(bundle) == []


def test_known_key_count_returns_int(monkeypatch):
    _, _, _, bundle = _scaffold()
    _set_known_keys(bundle, ["A", "B", "C"])
    bundle.refresh_from_db()

    monkeypatch.setattr(
        bundle_keys,
        "_resolve_secrets_backend_for",
        lambda _b: (_ for _ in ()).throw(AssertionError("no refresh needed")),
    )
    assert known_key_count(bundle) == 3


# ---- force_refresh (attach hook) --------------------------------


def test_force_refresh_bypasses_fresh_short_circuit(monkeypatch):
    _, _, _, bundle = _scaffold()
    _set_known_keys(bundle, ["STALE"])
    # Fresh timestamp -- maybe_refresh would skip.
    bundle.refresh_from_db()
    backend = FakeSecretsBackend({"FRESH": "v"})
    monkeypatch.setattr(
        bundle_keys,
        "_resolve_secrets_backend_for",
        lambda _b: backend,
    )
    out = force_refresh_bundle_known_keys(bundle)
    assert out == ["FRESH"]


# ---- GraphQL surfaces (#441 C) ----------------------------------


def test_attachment_query_returns_real_keycount(monkeypatch, permission_resolver):
    org, app, env, bundle = _scaffold()
    user = _make_user("kc-q")
    permission_resolver.grant(Permission.APP_READ)
    permission_resolver.grant(Permission.APP_UPDATE)
    # Pre-seed the cache so we don't need to wire the driver.
    _set_known_keys(bundle, ["DATABASE_URL", "API_KEY", "REDIS_URL"])

    # Skip the backend resolution -- cache is fresh.
    monkeypatch.setattr(
        bundle_keys,
        "_resolve_secrets_backend_for",
        lambda _b: (_ for _ in ()).throw(AssertionError("cache hit expected")),
    )

    with _ctx(org):
        AppSecretBundleRef.objects.create(
            registered_app=app,
            app_environment=env,
            secret_bundle=bundle,
        )
        rows = ServicesQuery().astrolift_app_secret_bundle_attachments(
            _info(user=user),
            app_slug=app.slug,
            environment_name=env.name,
        )

    assert len(rows) == 1
    assert rows[0].key_count == 3


def test_attach_mutation_eagerly_populates_keycount(monkeypatch, permission_resolver):
    org, app, env, bundle = _scaffold()
    user = _make_user("kc-attach")
    permission_resolver.grant(Permission.APP_UPDATE)
    backend = FakeSecretsBackend({"K1": "v", "K2": "v"})
    monkeypatch.setattr(
        bundle_keys,
        "_resolve_secrets_backend_for",
        lambda _b: backend,
    )

    with _ctx(org):
        result = ServicesMutation().attach_secret_bundle(
            _info(user=user),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
            ),
        )

    assert result.ok, result.errors
    assert result.data.key_count == 2
    bundle.refresh_from_db()
    assert bundle.last_known_keys == ["K1", "K2"]
    assert bundle.last_key_enum_at is not None


def test_attach_mutation_swallows_backend_error(monkeypatch, permission_resolver):
    """Attach must not 500 because secrets backend is momentarily
    unreachable -- the cache stays empty + UI shows '?'."""
    org, app, env, bundle = _scaffold()
    user = _make_user("kc-attach-err")
    permission_resolver.grant(Permission.APP_UPDATE)

    def _boom(_b):
        raise RuntimeError("vault timeout")

    monkeypatch.setattr(bundle_keys, "_resolve_secrets_backend_for", _boom)

    with _ctx(org):
        result = ServicesMutation().attach_secret_bundle(
            _info(user=user),
            input=AttachSecretBundleInput(
                app_slug=app.slug,
                environment_name=env.name,
                bundle_slug=bundle.slug,
            ),
        )

    assert result.ok, result.errors
    assert result.data.key_count == 0  # cache stayed empty
    bundle.refresh_from_db()
    assert bundle.last_known_keys == []


def test_secret_bundles_query_surfaces_keycount_and_timestamp(monkeypatch, permission_resolver):
    org, _, _, bundle = _scaffold()
    user = _make_user("kc-list")
    permission_resolver.grant(Permission.APP_READ)
    _set_known_keys(bundle, ["A", "B"])

    monkeypatch.setattr(
        bundle_keys,
        "_resolve_secrets_backend_for",
        lambda _b: (_ for _ in ()).throw(AssertionError("should not refresh")),
    )

    with _ctx(org):
        rows = ServicesQuery().astrolift_secret_bundles(_info(user=user))

    assert len(rows) == 1
    assert rows[0].key_count == 2
    assert rows[0].last_known_keys_at is not None


def test_secret_bundles_query_keycount_zero_when_never_populated(permission_resolver):
    org, _, _, _ = _scaffold()
    user = _make_user("kc-empty")
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(org):
        rows = ServicesQuery().astrolift_secret_bundles(_info(user=user))

    assert len(rows) == 1
    assert rows[0].key_count == 0
    assert rows[0].last_known_keys_at is None
