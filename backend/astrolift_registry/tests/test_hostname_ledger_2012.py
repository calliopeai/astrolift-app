"""Hostname ledger: rendered hostnames, not labels, are unique per zone (#2012).

Follow-up to #1930 (see ``test_shared_zone_hostnames_1930.py``): that fix
refuses a new app *label* that collides with another org's label in a shared
zone, but never renders the hostname, so it cannot see a multi-workload
app's suffixed form (``<label>-<workload>``) colliding with another org's
plain label. These tests cover the ledger that closes that gap: creation,
the multi-workload collision itself, the hard refusal at ``setAppSubdomain``,
release on deletion, the read-only report command, and the data migration's
first-claimant-wins backfill.
"""

from __future__ import annotations

import uuid
from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.hostname_claims import (
    hostname_claim_refusal,
    release_app_hostname_claims,
    sync_workload_hostname_claims,
)
from astrolift_registry.management.commands.report_shared_zone_hostname_collisions import (
    find_shared_zone_collisions,
)
from astrolift_registry.models import HostnameClaim, RegisteredApp, Workload
from astrolift_registry.schema.mutations import RegistryMutation, SetAppSubdomainInput, SoftDeleteAppInput
from core.permissions import Permission

pytestmark = pytest.mark.django_db

_populate_hostname_claims = import_module(
    "astrolift_registry.migrations.0041_populate_hostname_claims"
).populate_hostname_claims


@pytest.fixture(autouse=True)
def _no_workflows(monkeypatch):
    monkeypatch.setattr("astrolift_workflows.client.start_workflow", lambda *a, **k: None)


@pytest.fixture
def shared():
    return ManagedDomain.objects.create(
        organization=None, zone=f"apps-{uuid.uuid4().hex[:6]}.test", default_for=ManagedDomain.DefaultFor.BOTH
    )


def _org(tag):
    org = Organization.objects.create(name=tag, slug=f"{tag}-{uuid.uuid4().hex[:6]}")
    team = Team.objects.create(organization=org, name="t", slug=f"t-{uuid.uuid4().hex[:4]}")
    project = Project.objects.create(organization=org, team=team, name="p", slug=f"p-{uuid.uuid4().hex[:4]}")
    [plugin] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="Local", slug=f"local-{uuid.uuid4().hex[:6]}", capabilities_manifest={}, config_schema={}
            )
        ]
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        name="local",
        slug=f"local-{uuid.uuid4().hex[:6]}",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )
    return org, project, cluster


def _app(org, project, slug, subdomain=""):
    return RegisteredApp.objects.create(
        organization=org, team=project.team, project=project, name=slug, slug=slug, subdomain=subdomain
    )


def _env(app, cluster, managed_domain, name="production"):
    return AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name=name, managed_domain=managed_domain
    )


def _workload(app, slug, *, is_public=True):
    return Workload.objects.create(
        registered_app=app, name=slug, slug=slug, kind="deployment", is_public=is_public
    )


def _info():
    return SimpleNamespace(context=SimpleNamespace(user=None, request=None))


# ---- creation -----------------------------------------------------------


def test_single_public_workload_claims_the_bare_label(shared):
    org, project, cluster = _org("a")
    app = _app(org, project, "web")
    _env(app, cluster, shared)
    _workload(app, "web")

    conflicts = sync_workload_hostname_claims(app)

    assert conflicts == []
    claim = HostnameClaim.objects.get(registered_app=app)
    assert claim.hostname == f"web.{shared.zone}"
    assert claim.managed_domain_id == shared.pk
    assert claim.organization_id == org.pk


def test_multi_workload_app_claims_one_suffixed_hostname_per_workload(shared):
    org, project, cluster = _org("a")
    app = _app(org, project, "web")
    _env(app, cluster, shared)
    _workload(app, "frontend")
    _workload(app, "api")

    conflicts = sync_workload_hostname_claims(app)

    assert conflicts == []
    hostnames = set(HostnameClaim.objects.filter(registered_app=app).values_list("hostname", flat=True))
    assert hostnames == {f"web-frontend.{shared.zone}", f"web-api.{shared.zone}"}


def test_private_workload_claims_nothing(shared):
    org, project, cluster = _org("a")
    app = _app(org, project, "web")
    _env(app, cluster, shared)
    _workload(app, "worker", is_public=False)

    conflicts = sync_workload_hostname_claims(app)

    assert conflicts == []
    assert not HostnameClaim.objects.filter(registered_app=app).exists()


def test_an_orgs_own_zone_claims_are_not_limited_to_shared_zones(shared):
    # The ledger enforces (zone, hostname) uniqueness everywhere, not only
    # shared zones -- an org's own zone can still host two of its own apps
    # that happen to render the same string, which is worth catching too.
    org, project, cluster = _org("a")
    own_zone = ManagedDomain.objects.create(
        organization=org, zone=f"a-{uuid.uuid4().hex[:6]}.test", default_for=ManagedDomain.DefaultFor.BOTH
    )
    app = _app(org, project, "web")
    _env(app, cluster, own_zone)
    _workload(app, "web")

    sync_workload_hostname_claims(app)

    assert HostnameClaim.objects.get(registered_app=app).hostname == f"web.{own_zone.zone}"


# ---- the multi-workload collision (#2012's own example) -----------------


def test_multiworkload_suffix_colliding_with_anothers_plain_label_is_left_unclaimed(shared):
    b_org, b_project, b_cluster = _org("b")
    b_app = _app(b_org, b_project, "web-api")
    _env(b_app, b_cluster, shared)
    _workload(b_app, "svc")
    assert sync_workload_hostname_claims(b_app) == []

    a_org, a_project, a_cluster = _org("a")
    a_app = _app(a_org, a_project, "web")
    _env(a_app, a_cluster, shared)
    _workload(a_app, "frontend")
    _workload(a_app, "api")

    conflicts = sync_workload_hostname_claims(a_app)

    assert len(conflicts) == 1
    assert f"web-api.{shared.zone}" in conflicts[0]
    # B keeps it; A claims only the hostname nobody else holds.
    assert HostnameClaim.objects.get(hostname=f"web-api.{shared.zone}").registered_app_id == b_app.pk
    assert HostnameClaim.objects.get(registered_app=a_app).hostname == f"web-frontend.{shared.zone}"


def test_hostname_claim_refusal_sees_the_suffixed_form_hostname_label_refusal_misses(shared):
    b_org, b_project, b_cluster = _org("b")
    b_app = _app(b_org, b_project, "web-api")
    _env(b_app, b_cluster, shared)
    _workload(b_app, "svc")
    sync_workload_hostname_claims(b_app)

    a_org, a_project, a_cluster = _org("a")
    a_app = _app(a_org, a_project, "site")
    _env(a_app, a_cluster, shared)
    _workload(a_app, "frontend")
    _workload(a_app, "api")
    sync_workload_hostname_claims(a_app)

    from astrolift_registry.hostname_claims import hostname_label_refusal

    # The label alone ("web") collides with nobody -- B's app is literally
    # named "web-api", not "web" -- so the #1930 check passes it through.
    assert hostname_label_refusal("web", organization=a_org) is None
    # The ledger sees the rendered "web-api" the rename would also produce.
    refusal = hostname_claim_refusal(a_app, subdomain="web")
    assert refusal is not None
    assert "web-api" in refusal


def test_set_app_subdomain_refuses_the_multiworkload_collision(shared, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    b_org, b_project, b_cluster = _org("b")
    b_app = _app(b_org, b_project, "web-api")
    _env(b_app, b_cluster, shared)
    _workload(b_app, "svc")
    sync_workload_hostname_claims(b_app)

    a_org, a_project, a_cluster = _org("a")
    a_app = _app(a_org, a_project, "site", subdomain="site")
    _env(a_app, a_cluster, shared)
    _workload(a_app, "frontend")
    _workload(a_app, "api")
    sync_workload_hostname_claims(a_app)

    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=a_org.id)):
        result = RegistryMutation().set_app_subdomain(
            _info(), input=SetAppSubdomainInput(id=GUID(str(a_app.guid)), subdomain="web")
        )

    assert result.ok is False and result.errors[0].code == "CONFLICT", result
    a_app.refresh_from_db()
    assert a_app.subdomain == "site"
    # No half-applied ledger state from the refused rename.
    assert not HostnameClaim.objects.filter(hostname=f"web-api.{shared.zone}", registered_app=a_app).exists()


def test_set_app_subdomain_commits_the_ledger_on_a_clean_rename(shared, permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    org, project, cluster = _org("a")
    app = _app(org, project, "site")
    _env(app, cluster, shared)
    _workload(app, "web")
    sync_workload_hostname_claims(app)
    assert HostnameClaim.objects.get(registered_app=app).hostname == f"site.{shared.zone}"

    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryMutation().set_app_subdomain(
            _info(), input=SetAppSubdomainInput(id=GUID(str(app.guid)), subdomain="renamed")
        )

    assert result.ok is True, result
    claim = HostnameClaim.objects.get(registered_app=app)
    assert claim.hostname == f"renamed.{shared.zone}"


# ---- release --------------------------------------------------------------


def test_sync_releases_a_removed_workloads_claim(shared):
    # Three public workloads throughout, so removing one leaves the app
    # still in the 2+-public (suffixed) branch -- isolates "api's claim
    # goes away" from any single/multi-workload rendering change on the
    # others, which a two-workload setup would conflate.
    org, project, cluster = _org("a")
    app = _app(org, project, "web")
    _env(app, cluster, shared)
    web = _workload(app, "web")
    api = _workload(app, "api")
    admin = _workload(app, "admin")
    sync_workload_hostname_claims(app)
    before = {
        row.workload_id: row.pk
        for row in HostnameClaim.objects.filter(registered_app=app, deleted_at__isnull=True)
    }
    assert set(before) == {web.pk, api.pk, admin.pk}

    api.soft_delete()
    sync_workload_hostname_claims(app)

    live = list(HostnameClaim.objects.filter(registered_app=app, deleted_at__isnull=True))
    assert {row.workload_id for row in live} == {web.pk, admin.pk}
    assert {row.hostname for row in live} == {f"web-web.{shared.zone}", f"web-admin.{shared.zone}"}
    # Untouched claims aren't recreated, just left alone.
    assert {row.pk for row in live} == {before[web.pk], before[admin.pk]}
    released = HostnameClaim.all_objects.get(registered_app=app, workload_id=api.pk)
    assert released.deleted_at is not None


def test_release_app_hostname_claims_soft_deletes_every_claim(shared):
    org, project, cluster = _org("a")
    app = _app(org, project, "web")
    _env(app, cluster, shared)
    _workload(app, "frontend")
    _workload(app, "api")
    sync_workload_hostname_claims(app)
    assert HostnameClaim.objects.filter(registered_app=app, deleted_at__isnull=True).count() == 2

    released = release_app_hostname_claims(app)

    assert released == 2
    assert HostnameClaim.objects.filter(registered_app=app, deleted_at__isnull=True).count() == 0


def test_soft_delete_app_mutation_releases_claims(shared, permission_resolver):
    permission_resolver.grant(Permission.APP_DELETE)
    org, project, cluster = _org("a")
    app = _app(org, project, "web")
    _env(app, cluster, shared)
    _workload(app, "web")
    sync_workload_hostname_claims(app)

    from core.tenancy import TenantContext, tenant_context

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryMutation().soft_delete_app(_info(), input=SoftDeleteAppInput(id=GUID(str(app.guid))))

    assert result.ok is True, result
    assert HostnameClaim.objects.filter(registered_app=app, deleted_at__isnull=True).count() == 0

    # The hostname is free for another org to take.
    other_org, other_project, other_cluster = _org("b")
    other_app = _app(other_org, other_project, "web")
    _env(other_app, other_cluster, shared)
    _workload(other_app, "web")
    assert sync_workload_hostname_claims(other_app) == []
    assert HostnameClaim.objects.get(hostname=f"web.{shared.zone}").registered_app_id == other_app.pk


def test_app_teardown_activity_releases_claims(shared):
    from astrolift_workflows.activities.app_teardown import _soft_delete_app_records_sync

    org, project, cluster = _org("a")
    app = _app(org, project, "web")
    _env(app, cluster, shared)
    _workload(app, "web")
    sync_workload_hostname_claims(app)
    assert HostnameClaim.objects.filter(registered_app=app, deleted_at__isnull=True).exists()

    summary = _soft_delete_app_records_sync(app.pk)

    assert summary["hostname_claims"] == 1
    assert HostnameClaim.objects.filter(registered_app=app, deleted_at__isnull=True).count() == 0


# ---- preview environments never feed the renderer --------------------


def test_preview_environment_claims_nothing(shared):
    org, project, cluster = _org("a")
    app = _app(org, project, "web")
    primary = _env(app, cluster, shared, name="production")
    _workload(app, "web")
    sync_workload_hostname_claims(app)
    assert HostnameClaim.objects.filter(registered_app=app).count() == 1

    AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="preview-1",
        managed_domain=shared,
        previewed_environment=primary,
    )

    # Re-sync must not add a second, fictitious claim for the preview row.
    sync_workload_hostname_claims(app)
    assert HostnameClaim.objects.filter(registered_app=app, deleted_at__isnull=True).count() == 1


# ---- the read-only report --------------------------------------------


def test_report_finds_the_multiworkload_collision(shared):
    b_org, b_project, b_cluster = _org("b")
    b_app = _app(b_org, b_project, "web-api")
    _env(b_app, b_cluster, shared)
    _workload(b_app, "svc")

    a_org, a_project, a_cluster = _org("a")
    a_app = _app(a_org, a_project, "web")
    _env(a_app, a_cluster, shared)
    _workload(a_app, "frontend")
    _workload(a_app, "api")

    collisions = find_shared_zone_collisions()

    matches = [row for row in collisions if row["hostname"] == f"web-api.{shared.zone}"]
    assert len(matches) == 1
    orgs = {c["organization_slug"] for c in matches[0]["claims"]}
    assert orgs == {a_org.slug, b_org.slug}


def test_report_excludes_an_orgs_own_zone(shared):
    org, project, cluster = _org("a")
    own_zone = ManagedDomain.objects.create(
        organization=org, zone=f"a-{uuid.uuid4().hex[:6]}.test", default_for=ManagedDomain.DefaultFor.BOTH
    )
    app = _app(org, project, "web")
    _env(app, cluster, own_zone)
    _workload(app, "web")

    collisions = find_shared_zone_collisions()

    assert all(row["zone"] != own_zone.zone for row in collisions)


def test_report_excludes_preview_environments(shared):
    a_org, a_project, a_cluster = _org("a")
    a_app = _app(a_org, a_project, "site")
    primary = _env(a_app, a_cluster, shared, name="production")
    _workload(a_app, "web")

    # A second org's plain "preview-x" app would collide with a naively
    # rendered preview label -- confirm the preview row itself is excluded
    # from the renderer rather than trusting there's nothing to collide
    # with by construction.
    AppEnvironment.objects.create(
        registered_app=a_app,
        tenant_cluster=a_cluster,
        name="preview-1",
        managed_domain=shared,
        previewed_environment=primary,
    )

    b_org, b_project, b_cluster = _org("b")
    b_app = _app(b_org, b_project, "site")
    _env(b_app, b_cluster, shared)
    _workload(b_app, "web")

    collisions = find_shared_zone_collisions()

    # "site.<zone>" collides (both apps' primary environment render it);
    # nothing about the preview environment appears at all.
    assert len(collisions) == 1
    assert collisions[0]["hostname"] == f"site.{shared.zone}"
    for claim in collisions[0]["claims"]:
        assert claim["environment"] != "preview-1"


# ---- the data migration's first-claimant-wins backfill -----------------


def test_migration_backfill_keeps_the_oldest_app_on_a_preexisting_collision(shared):
    # Simulate state from before the ledger existed: two orgs already
    # rendering the same hostname, no HostnameClaim rows yet.
    b_org, b_project, b_cluster = _org("b")
    b_app = _app(b_org, b_project, "web")
    _env(b_app, b_cluster, shared)
    _workload(b_app, "web")

    a_org, a_project, a_cluster = _org("a")
    a_app = _app(a_org, a_project, "web")
    _env(a_app, a_cluster, shared)
    _workload(a_app, "web")

    assert not HostnameClaim.objects.exists()

    _populate_hostname_claims(django_apps, None)

    claim = HostnameClaim.objects.get(hostname=f"web.{shared.zone}")
    # B was created first (lower pk / earlier created_at) -- the migration's
    # deterministic tiebreak keeps the oldest claimant.
    assert claim.registered_app_id == b_app.pk
    assert not HostnameClaim.objects.filter(registered_app=a_app).exists()


def test_migration_backfill_claims_every_non_colliding_app(shared):
    org, project, cluster = _org("a")
    app = _app(org, project, "web")
    _env(app, cluster, shared)
    _workload(app, "frontend")
    _workload(app, "api")

    _populate_hostname_claims(django_apps, None)

    hostnames = set(HostnameClaim.objects.filter(registered_app=app).values_list("hostname", flat=True))
    assert hostnames == {f"web-frontend.{shared.zone}", f"web-api.{shared.zone}"}
