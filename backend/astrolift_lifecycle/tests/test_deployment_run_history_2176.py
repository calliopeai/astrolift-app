# ruff: noqa: F811
"""Persisted history is independent of pod availability and scoped before reads."""

import json
from types import SimpleNamespace

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import Client

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle.models import Deployment, DeploymentLog
from astrolift_lifecycle.run_history import record
from astrolift_lifecycle.schema.queries import LifecycleQuery
from astrolift_lifecycle.schema.types import deployment_to_type
from astrolift_lifecycle.tests.test_owner_scopes_2104 import no_search, subject, world  # noqa: F401
from core.permissions import Permission, PermissionDenied
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


def info(world):
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=world.user, auth=None)))


def grant(world, kind="APP", owner=None):
    return bind_role(
        world.user,
        permissions=[Permission.APP_READ_LOGS],
        kind=kind,
        scope_id=(owner or world.medops_app).pk,
        slug="run-log-2176",
    )


def page(world, row, **kwargs):
    return LifecycleQuery().astrolift_deployment_run_log_page(info(world), str(row.guid), **kwargs)


def download(world, row):
    return LifecycleQuery().astrolift_deployment_run_log_download(info(world), str(row.guid))


def test_pages_reach_every_persisted_row_and_download_exceeds_legacy_cap(world):
    grant(world)
    row = world.rows["medops"]["deployment"]
    DeploymentLog.objects.bulk_create(
        [
            DeploymentLog(
                deployment=row, status="deploying", phase="build", event="output", message=f"line {i}"
            )
            for i in range(1107)
        ]
    )
    with subject(world):
        first = page(world, row, limit=200)
        assert first.page_size == 200 and first.has_more
        record(row.pk, "rollout", "started", "new live event")
        messages = [e.message for e in first.items]
        cursor = first.next_cursor
        while cursor:
            result = page(world, row, cursor=cursor, limit=200)
            messages = [e.message for e in result.items] + messages
            cursor = result.next_cursor
        assert messages == [f"line {i}" for i in range(1107)]
        artifact = download(world, row)
        assert artifact.filename == f"deployment-{row.guid}.log"
        assert "line 0\n" in artifact.content and "line 1106\n" in artifact.content
        assert "new live event" in artifact.content
        assert len(LifecycleQuery().astrolift_deployment_log(info(world), str(row.guid))) == 1000


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT", "ORG"])
def test_actual_owner_grants_ignore_selected_sibling_headers(world, kind):
    owner = {
        "APP": world.medops_app,
        "TEAM": world.medops,
        "PROJECT": world.medops_project,
        "ORG": world.org,
    }[kind]
    grant(world, kind, owner)
    row = world.rows["medops"]["deployment"]
    record(row.pk, "build", "output", "retained after pod deletion")
    with subject(world):
        assert page(world, row).items[0].message == "retained after pod deletion"
        assert "retained after pod deletion" in download(world, row).content
        if kind != "ORG":
            with pytest.raises(PermissionDenied):
                page(world, world.rows["platform"]["deployment"])
            with pytest.raises(PermissionDenied):
                download(world, world.rows["platform"]["deployment"])


@pytest.mark.parametrize("kind", ["none", "read-only", "team-bearer", "missing", "env-deny"])
def test_denied_history_never_leaks_output(world, kind):
    row = world.rows["medops"]["deployment"]
    token = None
    if kind in ("team-bearer", "missing", "env-deny"):
        grant(world, "ORG", world.org) if kind != "missing" else grant(world)
    elif kind == "read-only":
        bind_role(
            world.user,
            permissions=[Permission.APP_READ],
            kind="APP",
            scope_id=world.medops_app.pk,
            slug="no-logs",
        )
    if kind == "team-bearer":
        token = ApiToken.objects.create(
            user=world.user,
            organization=world.org,
            team=world.platform,
            name="Sibling ceiling",
            token_hash="2176-sibling",
            scopes=["admin"],
        )
    if kind == "missing":
        row.guid = "00000000-0000-0000-0000-000000000001"
    if kind == "env-deny":
        Policy.objects.create(
            organization=world.org,
            name="No production logs",
            slug="2176-production",
            effect="DENY",
            scope_level="ORG",
            action_pattern="app.read_logs",
            resource_pattern={"env": ["production"]},
        )
    with subject(world, token):
        with pytest.raises(PermissionDenied):
            page(world, row)
        with pytest.raises(PermissionDenied):
            download(world, row)


def test_cursor_rejects_tampering_and_another_deployment(world):
    grant(world, "ORG", world.org)
    row = world.rows["medops"]["deployment"]
    record(row.pk, "build", "output", "a")
    record(row.pk, "build", "output", "b")
    with subject(world):
        cursor = page(world, row, limit=1).next_cursor
        with pytest.raises(ValueError, match="cursor"):
            page(world, row, cursor=cursor + "x")
        with pytest.raises(ValueError, match="cursor"):
            page(world, world.rows["platform"]["deployment"], cursor=cursor)
        assert page(world, row, limit=0).page_size == 1
        assert page(world, row, limit=999).page_size == 200


def test_real_status_transitions_expose_healthy_and_failed_timestamps(world):
    row = world.rows["medops"]["deployment"]
    assert deployment_to_type(row).phases() == []
    row.transition_to(Deployment.Status.PENDING)
    row.transition_to(Deployment.Status.DEPLOYING)
    record(row.pk, "apply", "started")
    record(row.pk, "apply", "completed")
    row.transition_to(Deployment.Status.RUNNING)
    phases = {p.name: p for p in deployment_to_type(row).phases()}
    assert phases["apply"].started_at <= phases["apply"].completed_at
    assert phases["health"].healthy_at is not None
    assert "build" not in phases and "push" not in phases
    row.transition_to(Deployment.Status.FAILED)
    assert {p.name: p for p in deployment_to_type(row).phases()}["failed"].failed_at is not None


def test_persisted_rows_refuse_database_update_and_delete(world):
    entry = record(world.rows["medops"]["deployment"].pk, "build", "output", "durable")
    for action in (
        lambda: DeploymentLog.objects.filter(pk=entry.pk).update(message="changed"),
        lambda: DeploymentLog.objects.filter(pk=entry.pk).delete(),
    ):
        with pytest.raises(DatabaseError), transaction.atomic():
            action()
    assert DeploymentLog.objects.get(pk=entry.pk).message == "durable"


def test_http_bearer_download_and_page_enforce_owner_and_environment(world):
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    grant(world, "ORG", world.org)
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="HTTP",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )
    row = world.rows["medops"]["deployment"]
    record(row.pk, "build", "output", "survives pod")
    query = """query($id:String!){ astroliftDeploymentRunLogPage(deploymentId:$id){ items{message phase event} hasMore nextCursor }
      astroliftDeploymentRunLogDownload(deploymentId:$id){ filename content contentType } }"""

    def send(row):
        response = Client().post(
            "/app/gql/config/",
            data=json.dumps({"query": query, "variables": {"id": str(row.guid)}}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + minted.plaintext,
            HTTP_X_ASTROLIFT_TEAM=str(world.platform.pk),
        )
        assert response.status_code == 200, response.content
        return response.json()

    result = send(row)
    assert not result.get("errors"), result
    assert result["data"]["astroliftDeploymentRunLogPage"]["items"][0]["message"] == "survives pod"
    assert "survives pod" in result["data"]["astroliftDeploymentRunLogDownload"]["content"]
    assert send(world.rows["platform"]["deployment"]).get("errors")
    Policy.objects.create(
        organization=world.org,
        name="HTTP env deny",
        slug="2176-http-policy",
        effect="DENY",
        scope_level="ORG",
        action_pattern="app.read_logs",
        resource_pattern={"env": ["production"]},
    )
    assert send(row).get("errors")


def test_rejected_persisted_transition_rolls_back_status_and_never_publishes(world, monkeypatch):
    row = world.rows["medops"]["deployment"]
    published = []
    monkeypatch.setattr("core.pubsub.publish_sync", lambda *args: published.append(args))
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE FUNCTION pg_temp.reject_run_log() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'run log unavailable'; END; $$"
        )
        cursor.execute(
            "CREATE TRIGGER reject_run_log BEFORE INSERT ON astrolift_lifecycle_deploymentlog FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_run_log()"
        )
    try:
        with pytest.raises(DatabaseError):
            row.transition_to(Deployment.Status.PENDING)
        row.refresh_from_db()
        assert row.status == "pending_approval"
        assert row.logs.count() == 0
        assert published == []
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER reject_run_log ON astrolift_lifecycle_deploymentlog")


@pytest.mark.parametrize("level", ["viewer", "deployer", "owner"])
def test_share_permission_ceiling_controls_bearer_log_and_download(world, level):
    from astrolift_registry.models import AppTeamAccess

    grant(world, "ORG", world.org)
    row = world.rows["platform"]["deployment"]
    record(row.pk, "build", "output", "shared output")
    AppTeamAccess.objects.create(registered_app=world.platform_app, team=world.medops, access_level=level)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="Shared logs",
        token_hash="2176-shared",
        scopes=["admin"],
    )
    with subject(world, token):
        if level in ("deployer", "owner"):
            assert page(world, row).items[0].message == "shared output"
            assert "shared output" in download(world, row).content
        else:
            with pytest.raises(PermissionDenied):
                page(world, row)
            with pytest.raises(PermissionDenied):
                download(world, row)


def test_legacy_status_log_excludes_new_output_and_phase_events(world):
    grant(world)
    row = world.rows["medops"]["deployment"]
    row.transition_to(Deployment.Status.PENDING)
    record(row.pk, "build", "started")
    record(row.pk, "build", "output", "pod output")
    with subject(world):
        legacy = LifecycleQuery().astrolift_deployment_log(info(world), str(row.guid))
        assert [entry.status for entry in legacy] == ["pending"]
        assert len(page(world, row).items) == 3


@pytest.mark.parametrize(
    "condition", ["foreign-org", "sibling-env", "sibling-workload", "soft-teardown", "deleted-deployment"]
)
def test_history_retains_coherent_snapshots_and_confines_every_owner(world, condition):
    from core.tests.utils.scope_world import ScopeWorld

    grant(world, "ORG", world.org)
    row = world.rows["medops"]["deployment"]
    record(row.pk, "build", "output", "old run")
    if condition == "foreign-org":
        foreign = ScopeWorld("history-foreign")
        Deployment.objects.filter(pk=row.pk).update(registered_app=foreign.medops_app)
    elif condition == "sibling-env":
        Deployment.objects.filter(pk=row.pk).update(app_environment=world.rows["platform"]["environment"])
    elif condition == "sibling-workload":
        Deployment.objects.filter(pk=row.pk).update(workload=world.rows["platform"]["task_run"].workload)
    elif condition == "soft-teardown":
        world.medops_app.soft_delete()
        world.rows["medops"]["environment"].soft_delete()
    else:
        row.soft_delete()
    with subject(world):
        result = page(world, row)
        artifact = download(world, row)
        if condition == "soft-teardown":
            assert result.items[0].message == "old run"
            assert "old run" in artifact.content
        else:
            assert result.items == []
            assert artifact is None


def test_role_revocation_denies_a_previously_issued_cursor_and_export(world):
    binding = grant(world)
    row = world.rows["medops"]["deployment"]
    record(row.pk, "build", "output", "older")
    record(row.pk, "build", "output", "newer")
    with subject(world):
        cursor = page(world, row, limit=1).next_cursor
        assert cursor
        binding.soft_delete()
        with pytest.raises(PermissionDenied):
            page(world, row, cursor=cursor)
        with pytest.raises(PermissionDenied):
            download(world, row)
