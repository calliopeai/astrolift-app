"""Registration records what happened to the manifest (#1553).

Registration is deliberately forgiving about a manifest it cannot use — a bad
``astrolift.toml`` must not cost the operator the whole registration. But
"forgiving" had meant "silent": the app landed with zero workloads, nothing
recorded why, and the first signal was a deploy that shipped nothing much
later. Worse, an app registered from a repo *without* an inline manifest never
had its manifest fetched at all, so there was nothing stale to refresh — the
operator was chasing a cache that had never been populated.

These pin the contract: registration still survives every failure, and it
always leaves a status behind.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegisterAppInput, RegistryMutation
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


GOOD_MANIFEST = """
name = "checkout"

[[workloads]]
name = "api"
image = "nginx:1.27"
"""


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    # register_app rejects an org with zero managed clusters (#315/#316);
    # bulk_create the plugin to dodge the version-field collision the other
    # registry tests document.
    [plugin] = ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="Local", slug="local", capabilities_manifest={}, config_schema={})]
    )
    TenantCluster.objects.create(
        organization=org,
        name="local",
        slug="local",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, project


def _register(org, project, *, slug: str, **extra):
    with tenant_context(TenantContext(organization_id=org.id)):
        return RegistryMutation().register_app(
            _info(),
            input=RegisterAppInput(
                project_id=str(project.guid),
                name=slug.title(),
                slug=slug,
                **extra,
            ),
        )


def test_inline_manifest_records_applied(permission_resolver):
    """The happy path is recorded too, so ``applied`` is distinguishable from
    "we never looked"."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, slug="good", source_repo="acme/good", manifest_raw=GOOD_MANIFEST)

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="good")
    assert app.manifest_bootstrap_status == "applied"
    assert app.manifest_bootstrap_error == ""
    assert app.workloads.filter(deleted_at__isnull=True).count() == 1


def test_unparseable_inline_manifest_registers_but_records_the_failure(permission_resolver):
    """Registration survives, and says why there are no workloads."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    result = _register(org, project, slug="bad", source_repo="acme/bad", manifest_raw="this is not toml {{{")

    assert result.ok, "registration must survive a bad manifest"
    app = RegisteredApp.objects.get(slug="bad")
    assert app.manifest_bootstrap_status == "parse_failed"
    assert app.manifest_bootstrap_error
    assert app.workloads.filter(deleted_at__isnull=True).count() == 0


def test_no_inline_manifest_fetches_from_the_repo(permission_resolver, monkeypatch):
    """The gap this closes: nothing used to fetch the manifest when it wasn't
    passed inline, so the app registered with no workloads and no attempt."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    calls: list[str] = []

    class _Result:
        status = "applied"
        error = ""

    def _fake_resync(app, **kwargs):
        calls.append(app.slug)
        return _Result()

    monkeypatch.setattr(
        "astrolift_registry.services.manifest_sync.resync_app_manifest_from_repo",
        _fake_resync,
    )

    result = _register(org, project, slug="fromrepo", source_repo="acme/fromrepo")

    assert result.ok, result.errors
    assert calls == ["fromrepo"], "registration should fetch the repo manifest"
    assert RegisteredApp.objects.get(slug="fromrepo").manifest_bootstrap_status == "applied"


def test_repo_fetch_failure_is_recorded_not_raised(permission_resolver, monkeypatch):
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    class _Result:
        status = "fetch_failed"
        error = "'astrolift.toml' not found on 'main'"

    monkeypatch.setattr(
        "astrolift_registry.services.manifest_sync.resync_app_manifest_from_repo",
        lambda *a, **k: _Result(),
    )

    result = _register(org, project, slug="nofile", source_repo="acme/nofile")

    assert result.ok, "a missing manifest must not fail registration"
    app = RegisteredApp.objects.get(slug="nofile")
    assert app.manifest_bootstrap_status == "fetch_failed"
    assert "not found" in app.manifest_bootstrap_error


def test_no_source_and_no_manifest_is_recorded_as_no_source(permission_resolver):
    """Nothing to bootstrap from is a distinct, benign state — not a failure
    the UI should nag about."""
    org, project = _scaffold()
    permission_resolver.grant(Permission.APP_CREATE)

    # source_repo is required on the input; empty means "nothing to fetch from".
    result = _register(org, project, slug="bare", source_repo="")

    assert result.ok, result.errors
    app = RegisteredApp.objects.get(slug="bare")
    assert app.manifest_bootstrap_status == "no_source"
    assert app.manifest_bootstrap_error == ""
