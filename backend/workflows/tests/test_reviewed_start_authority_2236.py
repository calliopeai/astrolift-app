import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import sleep
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction

from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    SCOPE_READ_APPS,
    SCOPE_WORKFLOW_TRIGGER,
    mint_token,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ApiToken, Member, Organization, Policy, Role, RoleBinding
from astrolift_identity.permission_resolver import resolve
from astrolift_workflows.client import WorkflowHandle
from core.permissions import (
    Permission,
    PermissionDenied,
    get_permission_resolver,
    register_permission_resolver,
)
from core.run_input_contract import digest
from core.tenancy import TenantContext, tenant_context
from workflows.models import WorkflowDefinition, WorkflowDefinitionStart, WorkflowStage
from workflows.reviewed_starts import definition_revision, dispatch_start, recover_start, reserve_start

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def authority(settings, monkeypatch):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    monkeypatch.setattr("constance.settings.DATABASE_CACHE_BACKEND", None)
    previous = get_permission_resolver()
    register_permission_resolver(resolve)
    org = Organization.objects.create(name="Locked starts", slug="locked-starts")
    user = get_user_model().objects.create_user(username="locked-start-actor")
    member = Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk)
    role = Role.objects.create(
        organization=org,
        name="Workflow starter",
        slug="workflow-starter",
        scope_level="ORG",
        permissions=[Permission.WORKFLOW_TRIGGER.value, Permission.WORKFLOW_READ.value],
    )
    binding = RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.pk)
    token = ApiToken.objects.create(
        organization=org,
        user=user,
        name="Reviewed start",
        token_hash="test-reviewed-start",
        scopes=[SCOPE_WORKFLOW_TRIGGER, SCOPE_READ_APPS],
    )
    definition = WorkflowDefinition.objects.create(
        organization=org,
        name="Locked workflow",
        slug="locked-workflow",
        model_label="",
    )
    WorkflowStage.objects.create(definition=definition, slug="locked-stage", kind="checkpoint", order=0)
    try:
        yield SimpleNamespace(
            org=org,
            user=user,
            member=member,
            binding=binding,
            token=token,
            definition=definition,
            tenant=TenantContext(organization_id=org.pk, actor_user_id=user.pk),
        )
    finally:
        register_permission_resolver(previous)


def _reserve(world):
    return reserve_start(
        definition_id=world.definition.guid,
        expected_revision=definition_revision(world.definition),
        expected_input_schema_digest=digest(world.definition.input_schema),
        request_id="locked-request",
        inputs={},
        user=world.user,
    )


@pytest.mark.parametrize(
    "boundary", ["reservation", "reservation_stages", "dispatch", "dispatch_stages", "recovery"]
)
@pytest.mark.parametrize(
    "withdrawal", ["revoke", "narrow", "actor", "member", "grant", "policy", "team_move"]
)
def test_authority_withdrawn_during_final_row_lock_refuses_work(authority, monkeypatch, boundary, withdrawal):
    _assert_withdrawn_authority(authority, monkeypatch, boundary, withdrawal)


def _assert_withdrawn_authority(authority, monkeypatch, boundary, withdrawal):
    world = authority
    if withdrawal == "team_move":
        from astrolift_identity.models import Project, Team

        own = Team.objects.create(organization=world.org, name="Original team", slug="original-team")
        sibling = Team.objects.create(organization=world.org, name="New team", slug="new-team")
        project = Project.objects.create(
            organization=world.org, team=own, name="Moved project", slug="moved-project"
        )
        world.definition.project = project
        world.definition.save()
        world.token.team = own
        world.token.save()
    marker = set_current_api_token(world.token)
    try:
        with tenant_context(world.tenant):
            row = _reserve(world) if boundary in ("dispatch", "dispatch_stages", "recovery") else None
    finally:
        reset_current_api_token(marker)
    effects = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow_once",
        lambda *args, **kwargs: effects.append(kwargs["workflow_id"])
        or WorkflowHandle(kwargs["workflow_id"], "run", True),
    )
    monkeypatch.setattr(
        "astrolift_workflows.client.recover_workflow_once", lambda *args, **kwargs: effects.append("recovery")
    )
    ready = Event()
    pid = []

    def submit():
        close_old_connections()
        state = set_current_api_token(world.token)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pid.append(cursor.fetchone()[0])
            ready.set()
            with (
                tenant_context(world.tenant),
                abac.request_attributes(abac.RequestAttributes(actor_user_id=world.user.pk)),
            ):
                try:
                    if boundary == "recovery":
                        recover_start(row)
                    elif row is not None:
                        dispatch_start(row)
                    else:
                        _reserve(world)
                except PermissionDenied:
                    return "denied"
                return "accepted"
        finally:
            reset_current_api_token(state)
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            if boundary.endswith("_stages"):
                model, pk = WorkflowStage, world.definition.stages.get().pk
            else:
                model, pk = (
                    (WorkflowDefinitionStart, row.pk)
                    if row is not None
                    else (WorkflowDefinition, world.definition.pk)
                )
            model.objects.select_for_update().get(pk=pk)
            future = pool.submit(submit)
            assert ready.wait(timeout=5)
            for _ in range(500):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", pid)
                    waiting = cursor.fetchone()[0] == "Lock"
                if waiting:
                    break
                sleep(0.01)
            else:
                raise AssertionError("The actual start did not wait for its PostgreSQL row lock")
            if withdrawal == "revoke":
                ApiToken.objects.filter(pk=world.token.pk).update(is_revoked=True)
            elif withdrawal == "narrow":
                scopes = [SCOPE_WORKFLOW_TRIGGER] if boundary == "recovery" else [SCOPE_READ_APPS]
                ApiToken.objects.filter(pk=world.token.pk).update(scopes=scopes)
            elif withdrawal == "actor":
                get_user_model().objects.filter(pk=world.user.pk).update(is_active=False)
            elif withdrawal == "member":
                Member.objects.filter(pk=world.member.pk).update(is_active=False, lifecycle="deactivated")
            elif withdrawal == "grant":
                from django.utils import timezone

                RoleBinding.objects.filter(pk=world.binding.pk).update(deleted_at=timezone.now())
            elif withdrawal == "team_move":
                Project.objects.filter(pk=project.pk).update(team=sibling)
            else:
                Policy.objects.create(
                    organization=world.org,
                    name="Withdraw workflow admission",
                    slug="withdraw-start",
                    scope_level="ORG",
                    effect="DENY",
                    action_pattern=Permission.WORKFLOW_READ.value
                    if boundary == "recovery"
                    else Permission.WORKFLOW_TRIGGER.value,
                )
        assert future.result(timeout=10) == "denied"
    assert effects == []
    assert WorkflowDefinitionStart.objects.count() == (1 if row is not None else 0)


@pytest.mark.parametrize("boundary", ["reservation_stages", "dispatch_stages", "recovery"])
def test_session_membership_withdrawal_is_not_a_bearer_only_check(authority, monkeypatch, boundary):
    authority.token = None
    _assert_withdrawn_authority(authority, monkeypatch, boundary, "member")


@pytest.mark.parametrize("boundary", ["reservation", "dispatch"])
def test_region_changed_after_graph_lock_uses_current_operation_facts(authority, monkeypatch, boundary):
    from astrolift_clusters.models import TenantCluster
    from core.tests.utils.scope_world import make_cluster
    from workflows import reviewed_starts

    world = authority
    cluster = make_cluster(world, "reviewed-region")
    cluster.lifecycle = TenantCluster.Lifecycle.MANAGED
    cluster.region = "us-west-2"
    cluster.save()
    Policy.objects.create(
        organization=world.org,
        name="Denied dispatch region",
        slug="denied-dispatch-region",
        scope_level="ORG",
        effect="DENY",
        action_pattern=Permission.WORKFLOW_TRIGGER.value,
        resource_pattern={"region": ["us-east-1"]},
    )
    marker = set_current_api_token(world.token)
    attrs = abac.RequestAttributes(actor_user_id=world.user.pk, region="us-west-2")
    effects = []
    try:
        with tenant_context(world.tenant), abac.request_attributes(attrs):
            row = _reserve(world) if boundary == "dispatch" else None
            original = reviewed_starts._definition_graph

            def change_region(*args, **kwargs):
                graph = original(*args, **kwargs)
                if kwargs.get("lock"):
                    TenantCluster.objects.filter(pk=cluster.pk).update(region="us-east-1")
                return graph

            monkeypatch.setattr(reviewed_starts, "_definition_graph", change_region)
            monkeypatch.setattr(
                "astrolift_workflows.client.start_workflow_once",
                lambda *args, **kwargs: effects.append("dispatch"),
            )
            with pytest.raises(PermissionDenied):
                dispatch_start(row) if row is not None else _reserve(world)
            assert abac.current_attributes() is attrs and attrs.region == "us-west-2"
    finally:
        reset_current_api_token(marker)
    assert effects == []
    assert WorkflowDefinitionStart.objects.count() == (1 if row is not None else 0)


def test_permission_refusal_http_returns_complete_error_envelope(authority, client):
    from django.utils import timezone

    from core.schema.audit import MutationAuditLog

    world = authority
    minted = mint_token()
    ApiToken.objects.filter(pk=world.token.pk).update(token_hash=minted.token_hash)
    RoleBinding.objects.filter(pk=world.binding.pk).update(deleted_at=timezone.now())
    response = client.post(
        "/app/gql/config/",
        data=json.dumps(
            {
                "query": "mutation($input:StartWorkflowDefinitionInput!){startWorkflowDefinition(input:$input){ok errors{code message field currentVersion requestedVersion supportedMethods requiresAttestation} data{executionId}}}",
                "variables": {
                    "input": {
                        "definitionId": str(world.definition.guid),
                        "expectedRevision": definition_revision(world.definition),
                        "expectedInputSchemaDigest": digest(world.definition.input_schema),
                        "requestId": "denied-http-start",
                        "confirmed": True,
                    }
                },
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {minted.plaintext}",
        HTTP_X_ASTROLIFT_ORG=str(world.org.guid),
    )
    assert response.status_code == 200
    body = response.json()
    assert not body.get("errors")
    refused = body["data"]["startWorkflowDefinition"]
    assert refused["ok"] is False and refused["data"] is None
    assert refused["errors"][0]["code"] == "PERMISSION_DENIED"
    assert refused["errors"][0]["currentVersion"] is None
    assert not WorkflowDefinitionStart.objects.exists()
    audit = MutationAuditLog.objects.get(user=world.user, organization=world.org)
    assert audit.operation == "workflow.definition.start" and audit.success is False


@pytest.mark.parametrize("target", ["own_project", "sibling_project", "organization", "global"])
def test_team_bearer_ceiling_is_exact_for_the_reviewed_definition(authority, target):
    from astrolift_identity.models import Project, Team

    world = authority
    own = Team.objects.create(organization=world.org, name="Own team", slug="own-team")
    sibling = Team.objects.create(organization=world.org, name="Sibling team", slug="sibling-team")
    project = Project.objects.create(organization=world.org, team=own, name="Own project", slug="own-project")
    sibling_project = Project.objects.create(
        organization=world.org, team=sibling, name="Sibling project", slug="sibling-project"
    )
    world.definition.project = {"own_project": project, "sibling_project": sibling_project}.get(target)
    if target == "global":
        world.definition.organization = None
    world.definition.save()
    world.token.team = own
    world.token.save()
    marker = set_current_api_token(world.token)
    try:
        with tenant_context(world.tenant):
            if target == "own_project":
                row = _reserve(world)
                assert row.definition_id == world.definition.pk and row.organization_id == world.org.pk
            else:
                with pytest.raises(PermissionDenied):
                    _reserve(world)
                assert not WorkflowDefinitionStart.objects.exists()
    finally:
        reset_current_api_token(marker)


@pytest.mark.parametrize("credential", ["session", "bearer"])
def test_only_platform_operator_sessions_keep_the_membership_exception(authority, credential):
    world = authority
    get_user_model().objects.filter(pk=world.user.pk).update(is_superuser=True)
    world.member.soft_delete()
    marker = set_current_api_token(world.token if credential == "bearer" else None)
    try:
        with tenant_context(world.tenant):
            if credential == "session":
                assert _reserve(world).definition_id == world.definition.pk
            else:
                with pytest.raises(PermissionDenied):
                    _reserve(world)
                assert not WorkflowDefinitionStart.objects.exists()
    finally:
        reset_current_api_token(marker)
