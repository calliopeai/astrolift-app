"""A restore reads only a snapshot Astrolift retained for the same owner (#2087).

``restore.snapshot_id`` and ``restore.source_handle`` are typed into the
manifest, and ``_provision_sync`` handed them to the service's driver as they
were. On a cluster two orgs share, that let one org name the other's retained
backup, revision or export, and every driver's ``restore`` read it with the
platform's credentials. The platform now requires the pair to be a snapshot
it recorded itself (``lifecycle_policy.last_retained_snapshot``) for a service
of the same organization, app or project, kind and cluster, before any driver
is resolved.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_workflows.activities.managed_service_lifecycle import (
    ManagedServicePreflightError,
    _provision_sync,
)

pytestmark = pytest.mark.django_db

# One (plugin, kind, variant) each driver family registers a restore for.
FAMILIES = [
    pytest.param("aws", "postgres", "rds", id="aws"),
    pytest.param("azure", "postgres", "azure_pg_flex", id="azure"),
    pytest.param("gcp", "workflow_engine", "workflows", id="gcp"),
    pytest.param("k8s_native", "postgres", "cnpg", id="k8s_native"),
]

_REFUSED = "is not a snapshot Astrolift retained"


def _plugin(slug: str) -> ProviderPlugin:
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=slug,
                slug=slug,
                plugin_version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            )
        ],
        ignore_conflicts=True,
    )
    return ProviderPlugin.objects.get(slug=slug)


def _cluster(label: str, plugin_slug: str, *, organization=None) -> TenantCluster:
    return TenantCluster.objects.create(
        organization=organization,
        slug=f"cluster-{label}-2087",
        name=f"Cluster {label}",
        provider_plugin=_plugin(plugin_slug),
        endpoint="https://example.invalid",
    )


def _tenant(label: str) -> SimpleNamespace:
    org = Organization.objects.create(name=f"Org {label}", slug=f"org-{label}-2087")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{label}-2087")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{label}-2087")
    return SimpleNamespace(org=org, team=team, project=project)


def _app(tenant: SimpleNamespace, label: str, cluster: TenantCluster, environment: str = "production"):
    app = RegisteredApp.objects.create(
        organization=tenant.org,
        team=tenant.team,
        project=tenant.project,
        name=f"App {label}",
        slug=f"app-{label}-2087",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name=environment, tenant_cluster=cluster)
    return app, env


def _retained(*, snapshot_id: str, source_handle: str, **owner) -> ManagedService:
    """A service a data-preserving teardown already retained and finalized."""
    row = ManagedService.objects.create(
        name="old",
        lifecycle_policy={
            "last_retained_snapshot": {
                "snapshot_id": snapshot_id,
                "source_handle": source_handle,
                "created_at": "2026-09-01T00:00:00Z",
            }
        },
        **owner,
    )
    row.soft_delete()
    return row


def _restoring(*, snapshot_id: str, source_handle: str, created_at: str = "", **owner) -> ManagedService:
    restore = {"snapshot_id": snapshot_id, "source_handle": source_handle}
    if created_at:
        restore["created_at"] = created_at
    return ManagedService.objects.create(
        name="new", config={}, lifecycle_policy={"restore": restore}, **owner
    )


def _spy_driver():
    calls: list = []

    class _SpyDriver:
        constructed = 0

        def __init__(self, *, config):  # noqa: ANN001 - test stub
            type(self).constructed += 1

        def provision(self, spec):  # noqa: ANN001 - test stub
            raise AssertionError("a restore declaration must not fall back to a fresh provision")

        def restore(self, snapshot, target):  # noqa: ANN001 - test stub
            calls.append(snapshot)
            return SimpleNamespace(
                ok=True, handle="restored/handle", ready=True, message="restored", errors=[]
            )

    return _SpyDriver, calls


def _provision(svc: ManagedService):
    driver_cls, calls = _spy_driver()
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=driver_cls),
        patch("core.cluster_observability.managed_config_for", return_value={}),
        patch(
            "astrolift_workflows.activities.managed_service_lifecycle._run_managed_service_preflight",
        ) as preflight,
    ):
        try:
            return _provision_sync(svc.pk), driver_cls, calls, preflight
        except ManagedServicePreflightError as exc:
            return exc, driver_cls, calls, preflight


@pytest.mark.parametrize(("plugin_slug", "kind", "variant"), FAMILIES)
def test_another_orgs_retained_snapshot_is_refused_before_any_driver_runs(plugin_slug, kind, variant):
    shared = _cluster("shared", plugin_slug)
    victim, attacker = _tenant("victim"), _tenant("attacker")
    victim_app, victim_env = _app(victim, "victim", shared)
    attacker_app, attacker_env = _app(attacker, "attacker", shared)
    _retained(
        registered_app=victim_app,
        app_environment=victim_env,
        kind=kind,
        variant=variant,
        snapshot_id="victim-final-20260901",
        source_handle=f"{kind}/victim-db",
    )
    target = _restoring(
        registered_app=attacker_app,
        app_environment=attacker_env,
        kind=kind,
        variant=variant,
        snapshot_id="victim-final-20260901",
        source_handle=f"{kind}/victim-db",
    )

    outcome, driver_cls, calls, preflight = _provision(target)

    assert isinstance(outcome, ManagedServicePreflightError)
    assert _REFUSED in str(outcome)
    assert "victim" not in str(outcome).replace("victim-final-20260901", "")
    assert calls == []
    assert driver_cls.constructed == 0
    preflight.assert_not_called()


@pytest.mark.parametrize(("plugin_slug", "kind", "variant"), FAMILIES)
def test_the_apps_own_retained_snapshot_restores_through_the_driver(plugin_slug, kind, variant):
    cluster = _cluster("own", plugin_slug)
    tenant = _tenant("own")
    app, env = _app(tenant, "own", cluster)
    _retained(
        registered_app=app,
        app_environment=env,
        kind=kind,
        variant=variant,
        snapshot_id="own-final-20260901",
        source_handle=f"{kind}/own-db",
    )
    target = _restoring(
        registered_app=app,
        app_environment=env,
        kind=kind,
        variant=variant,
        snapshot_id="own-final-20260901",
        source_handle=f"{kind}/own-db",
    )

    outcome, _driver_cls, calls, _preflight = _provision(target)

    assert outcome["handle"] == "restored/handle"
    assert [(call.snapshot_id, call.handle) for call in calls] == [("own-final-20260901", f"{kind}/own-db")]


def test_a_snapshot_nobody_recorded_is_refused():
    cluster = _cluster("unrecorded", "aws")
    app, env = _app(_tenant("unrecorded"), "unrecorded", cluster)
    target = _restoring(
        registered_app=app,
        app_environment=env,
        kind="postgres",
        variant="rds",
        snapshot_id="rds:some-instance-2026-09-01-00-00",
        source_handle="postgres/some-instance",
    )

    outcome, _driver_cls, calls, _preflight = _provision(target)

    assert isinstance(outcome, ManagedServicePreflightError)
    assert calls == []


def test_both_halves_of_the_pair_must_match_the_record():
    cluster = _cluster("pair", "aws")
    app, env = _app(_tenant("pair"), "pair", cluster)
    owner = {"registered_app": app, "app_environment": env, "kind": "postgres", "variant": "rds"}
    _retained(snapshot_id="pair-final", source_handle="postgres/pair-db", **owner)
    target = _restoring(snapshot_id="pair-final", source_handle="postgres/someone-elses-db", **owner)

    outcome, _driver_cls, calls, _preflight = _provision(target)

    assert isinstance(outcome, ManagedServicePreflightError)
    assert calls == []


def test_another_apps_snapshot_in_the_same_org_is_refused():
    cluster = _cluster("siblings", "aws")
    tenant = _tenant("siblings")
    first, first_env = _app(tenant, "first", cluster)
    second, second_env = _app(tenant, "second", cluster)
    _retained(
        registered_app=first,
        app_environment=first_env,
        kind="postgres",
        variant="rds",
        snapshot_id="first-final",
        source_handle="postgres/first-db",
    )
    target = _restoring(
        registered_app=second,
        app_environment=second_env,
        kind="postgres",
        variant="rds",
        snapshot_id="first-final",
        source_handle="postgres/first-db",
    )

    outcome, _driver_cls, calls, _preflight = _provision(target)

    assert isinstance(outcome, ManagedServicePreflightError)
    assert calls == []


def test_a_snapshot_taken_on_another_cluster_is_refused():
    tenant = _tenant("clusters")
    here, there = _cluster("here", "aws"), _cluster("there", "aws")
    app, env = _app(tenant, "clusters", here)
    staging = AppEnvironment.objects.create(registered_app=app, name="staging", tenant_cluster=there)
    _retained(
        registered_app=app,
        app_environment=staging,
        kind="postgres",
        variant="rds",
        snapshot_id="staging-final",
        source_handle="postgres/staging-db",
    )
    target = _restoring(
        registered_app=app,
        app_environment=env,
        kind="postgres",
        variant="rds",
        snapshot_id="staging-final",
        source_handle="postgres/staging-db",
    )

    outcome, _driver_cls, calls, _preflight = _provision(target)

    assert isinstance(outcome, ManagedServicePreflightError)
    assert calls == []


def test_another_environment_of_the_same_app_on_the_same_cluster_restores():
    cluster = _cluster("envs", "aws")
    app, production = _app(_tenant("envs"), "envs", cluster)
    staging = AppEnvironment.objects.create(registered_app=app, name="staging", tenant_cluster=cluster)
    _retained(
        registered_app=app,
        app_environment=production,
        kind="postgres",
        variant="rds",
        snapshot_id="production-final",
        source_handle="postgres/production-db",
    )
    target = _restoring(
        registered_app=app,
        app_environment=staging,
        kind="postgres",
        variant="rds",
        snapshot_id="production-final",
        source_handle="postgres/production-db",
    )

    outcome, _driver_cls, calls, _preflight = _provision(target)

    assert outcome["ok"] is True
    assert [call.snapshot_id for call in calls] == ["production-final"]


def test_a_snapshot_of_another_kind_is_refused():
    cluster = _cluster("kinds", "aws")
    app, env = _app(_tenant("kinds"), "kinds", cluster)
    _retained(
        registered_app=app,
        app_environment=env,
        kind="redis",
        variant="elasticache",
        snapshot_id="cache-final",
        source_handle="redis/cache",
    )
    target = _restoring(
        registered_app=app,
        app_environment=env,
        kind="postgres",
        variant="rds",
        snapshot_id="cache-final",
        source_handle="redis/cache",
    )

    outcome, _driver_cls, calls, _preflight = _provision(target)

    assert isinstance(outcome, ManagedServicePreflightError)
    assert calls == []


def test_a_pair_another_org_also_recorded_is_refused():
    shared = _cluster("ambiguous", "aws")
    mine, theirs = _tenant("mine"), _tenant("theirs")
    my_app, my_env = _app(mine, "mine", shared)
    their_app, their_env = _app(theirs, "theirs", shared)
    pair = {"snapshot_id": "shared-final", "source_handle": "postgres/shared-db"}
    _retained(registered_app=my_app, app_environment=my_env, kind="postgres", variant="rds", **pair)
    _retained(registered_app=their_app, app_environment=their_env, kind="postgres", variant="rds", **pair)
    target = _restoring(registered_app=my_app, app_environment=my_env, kind="postgres", variant="rds", **pair)

    outcome, _driver_cls, calls, _preflight = _provision(target)

    assert isinstance(outcome, ManagedServicePreflightError)
    assert calls == []


def test_a_project_service_restores_only_its_own_projects_snapshots():
    cluster = _cluster("project", "aws")
    tenant = _tenant("project")
    app, env = _app(tenant, "project", cluster)
    owner = {"project": tenant.project, "tenant_cluster": cluster, "kind": "postgres", "variant": "rds"}
    _retained(snapshot_id="project-final", source_handle="postgres/project-db", **owner)
    _retained(
        registered_app=app,
        app_environment=env,
        kind="postgres",
        variant="rds",
        snapshot_id="app-final",
        source_handle="postgres/app-db",
    )

    allowed, _driver_cls, allowed_calls, _preflight = _provision(
        _restoring(snapshot_id="project-final", source_handle="postgres/project-db", **owner),
    )
    ManagedService.objects.filter(project=tenant.project, name="new").update(name="restored")
    refused, _driver_cls, refused_calls, _preflight = _provision(
        _restoring(snapshot_id="app-final", source_handle="postgres/app-db", **owner),
    )

    assert allowed["ok"] is True
    assert [call.snapshot_id for call in allowed_calls] == ["project-final"]
    assert isinstance(refused, ManagedServicePreflightError)
    assert "of this project" in str(refused)
    assert refused_calls == []


def test_the_driver_restores_the_recorded_point_in_time():
    cluster = _cluster("when", "azure")
    app, env = _app(_tenant("when"), "when", cluster)
    owner = {"registered_app": app, "app_environment": env, "kind": "postgres", "variant": "azure_pg_flex"}
    _retained(snapshot_id="when-final", source_handle="postgres/when-db", **owner)

    omitted, _driver_cls, omitted_calls, _preflight = _provision(
        _restoring(snapshot_id="when-final", source_handle="postgres/when-db", **owner),
    )
    ManagedService.objects.filter(registered_app=app, name="new").update(name="restored")
    repeated, _driver_cls, repeated_calls, _preflight = _provision(
        _restoring(
            snapshot_id="when-final",
            source_handle="postgres/when-db",
            created_at="2026-09-01T00:00:00Z",
            **owner,
        ),
    )

    assert omitted["ok"] is True and repeated["ok"] is True
    assert [call.created_at for call in omitted_calls + repeated_calls] == ["2026-09-01T00:00:00Z"] * 2


def test_a_point_in_time_other_than_the_recorded_one_is_refused():
    cluster = _cluster("earlier", "azure")
    app, env = _app(_tenant("earlier"), "earlier", cluster)
    owner = {"registered_app": app, "app_environment": env, "kind": "postgres", "variant": "azure_pg_flex"}
    _retained(snapshot_id="earlier-final", source_handle="postgres/earlier-db", **owner)
    target = _restoring(
        snapshot_id="earlier-final",
        source_handle="postgres/earlier-db",
        created_at="2026-01-01T00:00:00Z",
        **owner,
    )

    outcome, _driver_cls, calls, _preflight = _provision(target)

    assert isinstance(outcome, ManagedServicePreflightError)
    assert calls == []
