"""Actual observability resolvers obey owners, live ancestry and bearer ceilings."""

from __future__ import annotations

import contextlib
import datetime as dt
from types import SimpleNamespace

import pytest
from graphql import build_schema

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_observability.schema.log_queries import LogHistoryQuery
from astrolift_observability.schema.queries import GoldenSignalsQuery
from astrolift_observability.scopes import managed_service_metrics_scope
from astrolift_registry.models import AppTeamAccess
from astrolift_services.models import ManagedService
from core.permissions import Permission, PermissionDenied, PermissionScope, ScopeKind
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db

READS = (
    ("astrolift_app_golden_signals", {}),
    ("astrolift_app_status_code_breakdown", {}),
    ("astrolift_workload_resource_usage", {"workload_slug": "web"}),
    ("astrolift_app_url_health", {"url": "not-a-url"}),
    ("astrolift_app_url_probe_history", {"url": "not-a-url"}),
    ("astrolift_pod_resource_usage", {"pod_name": "web-0"}),
    ("astrolift_app_metric_names", {}),
    ("astrolift_execute_promql", {"query": "up", "start_unix": 1, "end_unix": 2, "step_seconds": 1}),
    ("astrolift_app_traces", {"since": "2026-09-28T00:00:00Z", "until": "2026-09-29T00:00:00Z"}),
    ("astrolift_trace_spans", {"trace_id": "0" * 32}),
    ("astrolift_app_endpoint_metrics", {}),
    (
        "astrolift_app_logs",
        {"since": dt.datetime(2026, 9, 28, tzinfo=dt.UTC), "until": dt.datetime(2026, 9, 29, tzinfo=dt.UTC)},
    ),
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, gid: None))

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Observability contacted an external provider without an admitted configured target"
        )

    monkeypatch.setattr("astrolift_observability.url_probe._execute_probe", forbidden)
    monkeypatch.setattr("httpx.Client.send", forbidden)
    monkeypatch.setattr("requests.sessions.Session.send", forbidden)


@pytest.fixture
def world():
    return ScopeWorld("obs2111")


@pytest.fixture
def actor():
    return make_user("obs2111")


def permission_for(name):
    return Permission.APP_READ_LOGS if name == "astrolift_app_logs" else Permission.APP_READ


def invoke(name, arguments, user, slug):
    query = LogHistoryQuery() if name == "astrolift_app_logs" else GoldenSignalsQuery()
    return getattr(query, name)(make_info(user), app_slug=slug, **arguments)


@contextlib.contextmanager
def bearer(world, *, team=None, scopes=("read:apps",)):
    handle = set_current_api_token(
        SimpleNamespace(organization_id=world.org.pk, team_id=team.pk if team else None, scopes=list(scopes))
    )
    try:
        yield
    finally:
        reset_current_api_token(handle)


@pytest.mark.parametrize("name,arguments", READS)
@pytest.mark.parametrize("kind", ("APP", "PROJECT", "TEAM", "ORG"))
def test_real_binding_reads_own_target(world, actor, name, arguments, kind):
    target = {
        "APP": world.medops_app,
        "PROJECT": world.medops_project,
        "TEAM": world.medops,
        "ORG": world.org,
    }[kind]
    bind_role(actor, permissions=[permission_for(name)], kind=kind, scope_id=target.pk, slug=f"obs-{kind}")
    with tenant_context(
        TenantContext(organization_id=world.org.pk, actor_user_id=actor.pk, team_id=world.platform.pk)
    ):
        invoke(name, arguments, actor, world.medops_app.slug)


@pytest.mark.parametrize("name,arguments", READS)
@pytest.mark.parametrize("target", ("sibling", "missing", "foreign", "deleted", "stale-project"))
def test_selected_team_does_not_admit_other_or_unresolved_target(world, actor, name, arguments, target):
    bind_role(
        actor, permissions=[permission_for(name)], kind="TEAM", scope_id=world.medops.pk, slug="obs-team"
    )
    slug = world.medops_app.slug
    if target == "sibling":
        slug = world.platform_app.slug
    elif target == "missing":
        slug = "does-not-exist"
    elif target == "foreign":
        slug = ScopeWorld("obs-foreign").medops_app.slug
    elif target == "deleted":
        world.medops_app.deleted_at = dt.datetime.now(dt.UTC)
        world.medops_app.save(update_fields=["deleted_at"])
    else:
        world.medops_project.deleted_at = dt.datetime.now(dt.UTC)
        world.medops_project.save(update_fields=["deleted_at"])
    with (
        tenant_context(
            TenantContext(organization_id=world.org.pk, actor_user_id=actor.pk, team_id=world.medops.pk)
        ),
        pytest.raises(PermissionDenied),
    ):
        invoke(name, arguments, actor, slug)


@pytest.mark.parametrize("name,arguments", READS)
def test_team_bearer_cannot_borrow_owners_org_grant(world, actor, name, arguments):
    bind_role(actor, permissions=[permission_for(name)], kind="ORG", scope_id=world.org.pk, slug="obs-org")
    with (
        tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=actor.pk)),
        bearer(world, team=world.medops, scopes=("admin",)),
    ):
        invoke(name, arguments, actor, world.medops_app.slug)
        for slug in (world.platform_app.slug, "missing"):
            with pytest.raises(PermissionDenied):
                invoke(name, arguments, actor, slug)


@pytest.mark.parametrize("name,arguments", READS)
def test_bearer_permission_ceiling_still_applies(world, actor, name, arguments):
    bind_role(actor, permissions=[permission_for(name)], kind="ORG", scope_id=world.org.pk, slug="obs-org")
    with (
        tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=actor.pk)),
        bearer(world, scopes=("read:clusters",)),
        pytest.raises(PermissionDenied),
    ):
        invoke(name, arguments, actor, world.medops_app.slug)


@pytest.mark.parametrize(
    "access_level,logs_allowed", (("viewer", False), ("deployer", True), ("owner", True))
)
def test_team_bearer_share_uses_actual_read_permission(world, actor, access_level, logs_allowed):
    bind_role(
        actor,
        permissions=[Permission.APP_READ, Permission.APP_READ_LOGS],
        kind="ORG",
        scope_id=world.org.pk,
        slug="obs-org",
    )
    AppTeamAccess.objects.create(
        registered_app=world.platform_app, team=world.medops, access_level=access_level
    )
    with (
        tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=actor.pk)),
        bearer(world, team=world.medops),
    ):
        invoke(READS[0][0], READS[0][1], actor, world.platform_app.slug)
        if logs_allowed:
            invoke(READS[-1][0], READS[-1][1], actor, world.platform_app.slug)
        else:
            with pytest.raises(PermissionDenied):
                invoke(READS[-1][0], READS[-1][1], actor, world.platform_app.slug)


@pytest.mark.parametrize(
    "target", ("own", "sibling", "missing", "foreign", "deleted", "stale-project", "stale-team")
)
def test_project_service_metrics_owner_and_fallback(world, actor, target):
    bind_role(
        actor,
        permissions=[Permission.APP_READ],
        kind="PROJECT",
        scope_id=world.medops_project.pk,
        slug="obs-project",
    )
    service = ManagedService.objects.create(
        project=world.medops_project,
        tenant_cluster=make_cluster(world, "obs2111"),
        kind="postgres",
        name="db",
    )
    guid = service.guid
    if target == "sibling":
        service.project = world.platform_project
        service.save(update_fields=["project"])
    elif target == "missing":
        guid = "00000000-0000-4000-8000-000000000000"
    elif target == "foreign":
        foreign = ScopeWorld("obs-ms-foreign")
        guid = ManagedService.objects.create(
            project=foreign.medops_project,
            tenant_cluster=make_cluster(foreign, "obs-foreign"),
            kind="postgres",
            name="db",
        ).guid
    elif target != "own":
        row = {"deleted": service, "stale-project": world.medops_project, "stale-team": world.medops}[target]
        row.deleted_at = dt.datetime.now(dt.UTC)
        row.save(update_fields=["deleted_at"])
    with tenant_context(
        TenantContext(
            organization_id=world.org.pk, actor_user_id=actor.pk, project_id=world.medops_project.pk
        )
    ):
        if target == "own":
            assert managed_service_metrics_scope()({"managed_service_id": guid}) == PermissionScope(
                kind=ScopeKind.PROJECT, id=world.medops_project.pk
            )
            assert (
                GoldenSignalsQuery().astrolift_app_managed_service_metrics(
                    make_info(actor), managed_service_id=str(guid)
                )
                is None
            )
        else:
            with pytest.raises(PermissionDenied):
                GoldenSignalsQuery().astrolift_app_managed_service_metrics(
                    make_info(actor), managed_service_id=str(guid)
                )


def test_schema_removes_only_accidental_metric_catalog_fields():
    from pathlib import Path

    from config.schema import schema

    previous = build_schema((Path(__file__).parents[2] / "schema.graphql").read_text())
    old, current = previous.query_type.fields, schema._schema.query_type.fields
    accidental = {"PostgresMetrics", "ObjectStoreMetrics", "ModelEndpointMetrics"}
    assert not accidental & current.keys()
    assert old.keys() - current.keys() == accidental & old.keys()
    for name, field in old.items():
        if name in accidental:
            continue
        assert str(current[name].type) == str(field.type)
        assert {key: str(arg.type) for key, arg in current[name].args.items()} == {
            key: str(arg.type) for key, arg in field.args.items()
        }


@pytest.mark.parametrize(
    "target", ("own", "sibling", "stale-app", "stale-project", "team-bearer", "foreign-bearer")
)
def test_app_service_metrics_run_the_actual_owner_gate(world, actor, target):
    from astrolift_lifecycle.models import AppEnvironment

    bind_role(
        actor, permissions=[Permission.APP_READ], kind="APP", scope_id=world.medops_app.pk, slug="obs-app"
    )
    cluster = make_cluster(world, "obs-app-private")
    app = world.platform_app if target == "sibling" else world.medops_app
    environment = AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="production")
    service = ManagedService.objects.create(
        registered_app=app, app_environment=environment, kind="postgres", name="db"
    )
    if target in ("stale-app", "stale-project"):
        row = app if target == "stale-app" else world.medops_project
        row.deleted_at = dt.datetime.now(dt.UTC)
        row.save(update_fields=["deleted_at"])
    credential_world = ScopeWorld("obs-token-foreign") if target == "foreign-bearer" else world
    credential = (
        bearer(credential_world, team=world.platform)
        if target in ("team-bearer", "foreign-bearer")
        else contextlib.nullcontext()
    )
    with (
        tenant_context(
            TenantContext(organization_id=world.org.pk, actor_user_id=actor.pk, team_id=world.medops.pk)
        ),
        credential,
    ):
        if target == "own":
            result = GoldenSignalsQuery().astrolift_app_managed_service_metrics(
                make_info(actor), managed_service_id=str(service.guid)
            )
            assert result.name == "db"
            assert result.series == []
        else:
            with pytest.raises(PermissionDenied):
                GoldenSignalsQuery().astrolift_app_managed_service_metrics(
                    make_info(actor), managed_service_id=str(service.guid)
                )
