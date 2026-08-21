"""Tests for the URL health probe (#406).

Covers two layers:

* The pure ``url_probe`` core — classification, cache hit/miss,
  history ring buffer, transport-error mapping.
* The Strawberry resolvers — permission gate, URL membership check,
  tenant scoping.

The httpx transport is the only mocked layer (a stub ``Client``
that records calls + returns canned responses). The Django cache
and DB are real.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import httpx
import pytest
from django.core.cache import cache

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability import url_probe, url_resolution
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_registry.models import RegisteredApp, Workload
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------
# scaffold + stub http client
# ---------------------------------------------------------------------


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=SimpleNamespace(user=None)))


def _scaffold() -> tuple[Organization, RegisteredApp, AppEnvironment]:
    org = Organization.objects.create(name="Probe Co", slug="probe-co")
    team = Team.objects.create(organization=org, name="Eng", slug="probe-eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="probe-demo")
    # Cluster setup mirrors the observability test suite — we don't
    # exercise the cluster here but AppEnvironment.tenant_cluster is
    # PROTECT so we need a real row.
    plugin = ProviderPlugin(
        name="K8s probe",
        slug="dummy-probe",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    plugin = ProviderPlugin.objects.get(slug="dummy-probe")
    cluster = TenantCluster.objects.create(
        organization=org,
        name="probe-cluster",
        slug="probe-cluster",
        provider_plugin=plugin,
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        provider_config={},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Probe App",
        slug="probe-app",
        provisioning_status="ready",
        subdomain="probe.acme.astrolift.app",
    )
    env = AppEnvironment.objects.create(
        registered_app=app,
        name="production",
        tenant_cluster=cluster,
        url="https://probe.acme.astrolift.app/",
    )
    Workload.objects.create(
        registered_app=app,
        name="API",
        slug="api",
        is_public=True,
    )
    return org, app, env


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeClient:
    """Stub the resolver passes in lieu of httpx.Client.

    Captures every (url, timeout) the resolver invoked us with so
    tests can assert on the call shape, and returns a canned
    response or raises a canned exception based on the URL.
    """

    def __init__(self, *, response_map: dict, raise_for: dict | None = None):
        self.response_map = response_map
        self.raise_for = raise_for or {}
        self.calls: list[str] = []

    def __call__(self, *, timeout=None, follow_redirects=None, headers=None):
        self._timeout = timeout
        self._follow_redirects = follow_redirects
        self._headers = headers
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url: str):
        self.calls.append(url)
        if url in self.raise_for:
            raise self.raise_for[url]
        return _FakeResponse(self.response_map[url])


@pytest.fixture(autouse=True)
def _clear_cache():
    """Probe results + history live in the Django default cache; wipe
    between tests so the (app, url) key namespace stays clean."""
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------
# pure-core tests
# ---------------------------------------------------------------------


class TestClassify:
    def test_2xx_fast_is_ok(self):
        assert url_probe.classify(200, 0.05, error=None) == "ok"

    def test_2xx_slow_is_degraded(self):
        assert url_probe.classify(200, 6.0, error=None) == "degraded"

    def test_3xx_is_degraded(self):
        assert url_probe.classify(301, 0.1, error=None) == "degraded"

    def test_4xx_is_degraded(self):
        assert url_probe.classify(404, 0.1, error=None) == "degraded"

    def test_5xx_is_down(self):
        assert url_probe.classify(500, 0.1, error=None) == "down"

    def test_transport_error_is_down(self):
        assert url_probe.classify(None, None, error="conn refused") == "down"

    def test_partial_code_with_error_is_down(self):
        # Belt and suspenders: even if the transport layer surfaced
        # a half-baked status before the error, the result is down.
        assert url_probe.classify(200, 0.05, error="reset") == "down"


class TestProbeUrl:
    URL = "https://probe.acme.astrolift.app/"

    def test_happy_path_returns_ok_and_caches(self):
        client = _FakeClient(response_map={self.URL: 200})
        first = url_probe.probe_url(app_guid="guid-1", url=self.URL, client_factory=client)
        assert first.status == "ok"
        assert first.status_code == 200
        assert first.latency_ms is not None and first.latency_ms >= 0
        assert client.calls == [self.URL]

        # Second call within the cache window should not hit the
        # network — we re-instantiate the stub so any extra call
        # surfaces as an AttributeError on .get().
        empty_client = _FakeClient(response_map={})
        second = url_probe.probe_url(app_guid="guid-1", url=self.URL, client_factory=empty_client)
        assert second.status == "ok"
        assert second.status_code == 200
        assert empty_client.calls == []

    def test_force_refresh_skips_cache(self):
        client = _FakeClient(response_map={self.URL: 200})
        url_probe.probe_url(app_guid="guid-1", url=self.URL, client_factory=client)

        # Cached → would normally short-circuit. With ``use_cache=False``
        # the probe runs again and the second response (a 503) wins.
        next_client = _FakeClient(response_map={self.URL: 503})
        fresh = url_probe.probe_url(
            app_guid="guid-1",
            url=self.URL,
            use_cache=False,
            client_factory=next_client,
        )
        assert fresh.status == "down"
        assert fresh.status_code == 503
        assert next_client.calls == [self.URL]

    def test_timeout_classifies_down(self):
        client = _FakeClient(
            response_map={},
            raise_for={self.URL: httpx.ReadTimeout("slow", request=None)},
        )
        result = url_probe.probe_url(app_guid="guid-1", url=self.URL, client_factory=client)
        assert result.status == "down"
        assert result.status_code is None
        assert "timed out" in result.message.lower()

    def test_connect_error_classifies_down(self):
        client = _FakeClient(
            response_map={},
            raise_for={self.URL: httpx.ConnectError("nope")},
        )
        result = url_probe.probe_url(app_guid="guid-1", url=self.URL, client_factory=client)
        assert result.status == "down"
        assert "couldn't connect" in result.message

    def test_history_records_recent_probes_newest_first(self):
        # Three distinct probes against (guid, url) — newest must
        # surface first in the history ring buffer.
        for code in (200, 500, 200):
            client = _FakeClient(response_map={self.URL: code})
            url_probe.probe_url(
                app_guid="guid-1",
                url=self.URL,
                use_cache=False,
                client_factory=client,
            )

        history = url_probe.history_for(app_guid="guid-1", url=self.URL, limit=10)
        assert len(history) == 3
        assert [h.status_code for h in history] == [200, 500, 200]

    def test_history_caps_at_max_entries(self):
        for _ in range(url_probe.HISTORY_MAX_ENTRIES + 3):
            client = _FakeClient(response_map={self.URL: 200})
            url_probe.probe_url(
                app_guid="guid-1",
                url=self.URL,
                use_cache=False,
                client_factory=client,
            )
        history = url_probe.history_for(app_guid="guid-1", url=self.URL, limit=100)
        assert len(history) == url_probe.HISTORY_MAX_ENTRIES


# ---------------------------------------------------------------------
# url_resolution
# ---------------------------------------------------------------------


class TestAppUrls:
    def test_includes_env_url_and_public_workload_host(self):
        _, app, _ = _scaffold()
        urls = url_resolution.app_urls(app)
        # env URL comes first (normalized lowercase scheme + default
        # path); then the synthesized public workload URL.
        assert "https://probe.acme.astrolift.app/" in urls
        assert "https://api.probe.acme.astrolift.app/" in urls

    def test_deduplicates_overlapping_urls(self):
        _, app, env = _scaffold()
        # Make the env URL collide with the public workload host —
        # dedup should still leave one entry.
        env.url = "https://api.probe.acme.astrolift.app/"
        env.save(update_fields=["url"])
        urls = url_resolution.app_urls(app)
        assert urls.count("https://api.probe.acme.astrolift.app/") == 1

    def test_normalize_rejects_non_http(self):
        assert url_resolution.normalize_url("ftp://example.com/") is None
        assert url_resolution.normalize_url("") is None
        assert url_resolution.normalize_url("not a url") is None

    def test_normalize_default_path(self):
        out = url_resolution.normalize_url("https://Example.COM")
        assert out == "https://example.com/"


# ---------------------------------------------------------------------
# resolver tests
# ---------------------------------------------------------------------


class TestAppUrlHealthResolver:
    URL = "https://probe.acme.astrolift.app/"

    def test_requires_permission(self):
        org, _, _ = _scaffold()
        with tenant_context(TenantContext(organization_id=org.id)):
            with pytest.raises(PermissionDenied):
                GoldenSignalsQuery().astrolift_app_url_health(_info(), app_slug="probe-app", url=self.URL)

    def test_unknown_app_returns_none(self, permission_resolver):
        org, _, _ = _scaffold()
        permission_resolver.grant(Permission.APP_READ)
        with tenant_context(TenantContext(organization_id=org.id)):
            out = GoldenSignalsQuery().astrolift_app_url_health(_info(), app_slug="ghost", url=self.URL)
        assert out is None

    def test_unknown_url_for_app_returns_none(self, permission_resolver, monkeypatch):
        # Even with a valid app the resolver refuses to probe a URL
        # outside the app's known set — this is the open-relay guard.
        org, _, _ = _scaffold()
        permission_resolver.grant(Permission.APP_READ)

        called: list[str] = []

        def _no_probe(**kwargs):
            called.append(kwargs.get("url", ""))
            raise AssertionError("probe should not be issued for unknown URL")

        monkeypatch.setattr(url_probe, "probe_url", _no_probe)

        with tenant_context(TenantContext(organization_id=org.id)):
            out = GoldenSignalsQuery().astrolift_app_url_health(
                _info(),
                app_slug="probe-app",
                url="https://attacker.example.com/",
            )
        assert out is None
        assert called == []

    def test_happy_path_returns_ok(self, permission_resolver, monkeypatch):
        org, _, _ = _scaffold()
        permission_resolver.grant(Permission.APP_READ)
        client = _FakeClient(response_map={self.URL: 200})
        monkeypatch.setattr(url_probe, "httpx", httpx)  # ensure real ref
        # Patch the default client factory used inside ``probe_url``
        # via a wrapper so we don't have to thread the factory through
        # the resolver signature.
        from astrolift_observability.schema import queries as queries_module

        original_probe = queries_module.url_probe.probe_url

        def _probe(**kwargs):
            return original_probe(**{**kwargs, "client_factory": client})

        monkeypatch.setattr(queries_module.url_probe, "probe_url", _probe)

        with tenant_context(TenantContext(organization_id=org.id)):
            result = GoldenSignalsQuery().astrolift_app_url_health(
                _info(), app_slug="probe-app", url=self.URL
            )
        assert result is not None
        assert result.status == "ok"
        assert result.status_code == 200
        assert isinstance(result.last_checked, dt.datetime)

    def test_history_returns_recent_probes(self, permission_resolver, monkeypatch):
        org, app, _ = _scaffold()
        permission_resolver.grant(Permission.APP_READ)

        # Seed two probes directly so the history is populated.
        for code in (200, 503):
            client = _FakeClient(response_map={self.URL: code})
            url_probe.probe_url(
                app_guid=str(app.guid),
                url=self.URL,
                use_cache=False,
                client_factory=client,
            )

        with tenant_context(TenantContext(organization_id=org.id)):
            history = GoldenSignalsQuery().astrolift_app_url_probe_history(
                _info(),
                app_slug="probe-app",
                url=self.URL,
                limit=5,
            )
        assert [h.status_code for h in history] == [503, 200]

    def test_history_requires_permission(self):
        org, _, _ = _scaffold()
        with tenant_context(TenantContext(organization_id=org.id)):
            with pytest.raises(PermissionDenied):
                GoldenSignalsQuery().astrolift_app_url_probe_history(
                    _info(), app_slug="probe-app", url=self.URL
                )

    def test_history_unknown_url_returns_empty(self, permission_resolver):
        org, _, _ = _scaffold()
        permission_resolver.grant(Permission.APP_READ)
        with tenant_context(TenantContext(organization_id=org.id)):
            out = GoldenSignalsQuery().astrolift_app_url_probe_history(
                _info(),
                app_slug="probe-app",
                url="https://attacker.example.com/",
            )
        assert out == []
