from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from astrolift_identity.models import Organization
from astrolift_operations.models import WorkflowRun
from core.permissions import Permission, PermissionDenied
from core.run_input_contract import (
    InputContractError,
    digest,
    input_contract,
    no_input_schema,
    validate_inputs,
)
from core.tenancy import TenantContext, tenant_context
from workflows.models import WorkflowDefinition, WorkflowDefinitionStart, WorkflowStage
from workflows.reviewed_starts import (
    ReviewedStartError,
    definition_revision,
    dispatch_start,
    find_start,
    request_payload,
    reserve_start,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def local_cache(settings, monkeypatch):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    settings.CONSTANCE_DATABASE_CACHE_BACKEND = None
    monkeypatch.setattr("constance.settings.DATABASE_CACHE_BACKEND", None)


@pytest.fixture
def world(permission_resolver):
    org = Organization.objects.create(name="Reviewed starts", slug="reviewed-starts")
    user = get_user_model().objects.create_user(username="reviewed-start-actor")
    definition = WorkflowDefinition.objects.create(
        name="Reviewed workflow", slug="reviewed-workflow", organization=org, model_label=""
    )
    WorkflowStage.objects.create(definition=definition, slug="reviewed-stage", order=0, kind="checkpoint")
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    return SimpleNamespace(
        org=org,
        user=user,
        definition=definition,
        tenant=TenantContext(organization_id=org.pk, actor_user_id=user.pk),
    )


def reserve(world, **kwargs):
    definition = world.definition
    return reserve_start(
        definition_id=definition.guid,
        expected_revision=kwargs.pop("revision", definition_revision(definition)),
        expected_input_schema_digest=digest(definition.input_schema),
        request_id=kwargs.pop("request_id", "one-reviewed-request"),
        inputs=kwargs.pop("inputs", {}),
        user=world.user,
        **kwargs,
    )


def test_exact_global_guid_does_not_substitute_new_override(world):
    world.definition.organization = None
    world.definition.save()
    with tenant_context(world.tenant):
        reviewed = definition_revision(world.definition)
        replacement = WorkflowDefinition.objects.create(
            name="Override", slug=world.definition.slug, organization=world.org, model_label=""
        )
        WorkflowStage.objects.create(
            definition=replacement, slug="replacement-stage", order=0, kind="checkpoint"
        )
        row = reserve(world, revision=reviewed)
        assert row.definition_id == world.definition.pk
        assert row.execution.workflow_definition_id != replacement.pk
        assert request_payload(row)["plans"][str(world.definition.pk)]["definition_id"] == str(
            world.definition.pk
        )


def test_sensitive_refs_follow_only_applicable_schema_branches():
    schema = {
        "type": "object",
        "properties": {"mode": {"enum": ["public", "private"]}, "value": {"type": "string"}},
        "if": {"properties": {"mode": {"const": "private"}}},
        "then": {"properties": {"value": {"type": "string", "writeOnly": True}}},
    }
    assert validate_inputs(schema, {"mode": "public", "value": "ordinary input"})["value"] == "ordinary input"
    with pytest.raises(InputContractError, match="secret references"):
        validate_inputs(schema, {"mode": "private", "value": "literal credential"})
    assert (
        validate_inputs(schema, {"mode": "private", "value": "secret://credential"})["value"]
        == "secret://credential"
    )


@pytest.mark.parametrize("keyword", ["dependentSchemas", "contains"])
def test_sensitive_refs_cannot_hide_in_supported_applicators(keyword):
    if keyword == "dependentSchemas":
        schema = {
            "type": "object",
            "dependentSchemas": {
                "flag": {"properties": {"credential": {"type": "string", "writeOnly": True}}}
            },
        }
        value = {"flag": True, "credential": "literal credential"}
    else:
        schema = {
            "type": "object",
            "properties": {
                "credentials": {"type": "array", "contains": {"type": "string", "writeOnly": True}}
            },
        }
        value = {"credentials": ["literal credential"]}
    with pytest.raises(InputContractError, match="secret references"):
        validate_inputs(schema, value)


@pytest.mark.parametrize("change", ["disabled", "input", "stage", "deleted"])
def test_changed_review_cannot_start(world, change):
    reviewed = definition_revision(world.definition)
    if change == "disabled":
        world.definition.is_enabled = False
    elif change == "input":
        world.definition.input_schema = {"type": "object", "properties": {"choice": {"type": "string"}}}
    elif change == "deleted":
        from django.utils import timezone

        world.definition.deleted_at = timezone.now()
    else:
        stage = world.definition.stages.get()
        stage.prompt = "Changed instructions"
        stage.save()
    world.definition.save()
    with tenant_context(world.tenant), pytest.raises(ReviewedStartError):
        reserve(world, revision=reviewed)
    assert not WorkflowDefinitionStart.objects.exists()
    assert not WorkflowRun.objects.exists()


def test_idempotency_is_actor_scoped_and_payload_bound(world):
    with tenant_context(world.tenant):
        first = reserve(world)
        second = reserve(world)
        assert first.pk == second.pk
        assert find_start(first.request_id).pk == first.pk
        with pytest.raises(ReviewedStartError):
            reserve(world, inputs={"unknown": "different"})
        assert WorkflowDefinitionStart.objects.count() == WorkflowRun.objects.count() == 1
        other = get_user_model().objects.create_user(username="other-reviewed-actor")
        with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=other.pk)):
            assert find_start(first.request_id) is None


def test_inputs_encrypted_and_defaults_validated(world):
    marker = "PRIVATE-INPUT-MARKER-2236"
    world.definition.input_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "minLength": 1},
            "retries": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2},
        },
        "required": ["message"],
        "additionalProperties": False,
    }
    world.definition.save()
    with tenant_context(world.tenant):
        row = reserve(world, inputs={"message": marker})
    assert marker.encode() not in bytes(row.payload_ciphertext)
    assert request_payload(row)["inputs"] == {"message": marker, "retries": 2}


@pytest.mark.parametrize(
    "value", [None, {}, {"message": 123}, {"message": "okay", "retries": 10}, {"message": "okay", "extra": 1}]
)
def test_server_validates_current_input_contract(world, value):
    world.definition.input_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "enum": ["okay"]},
            "retries": {"type": "integer", "maximum": 3},
        },
        "required": ["message"],
        "additionalProperties": False,
    }
    world.definition.save()
    with tenant_context(world.tenant), pytest.raises(InputContractError):
        reserve(world, inputs=value)
    assert not WorkflowRun.objects.exists()


def test_permission_loss_and_cross_org_deny_before_start(world, permission_resolver):
    with tenant_context(world.tenant):
        row = reserve(world)
    permission_resolver.deny(Permission.WORKFLOW_TRIGGER)
    with tenant_context(world.tenant), pytest.raises(PermissionDenied):
        dispatch_start(row)
    other = Organization.objects.create(name="Other reviewed org", slug="other-reviewed-org")
    permission_resolver.grant(Permission.WORKFLOW_TRIGGER)
    with (
        tenant_context(TenantContext(organization_id=other.pk, actor_user_id=world.user.pk)),
        pytest.raises(ReviewedStartError),
    ):
        reserve(world)
    assert WorkflowRun.objects.count() == 1


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string"},
        {"type": "object", "properties": {"value": {"$ref": "https://example.test/schema"}}},
        {"type": "object", "properties": {"value": {"type": "string", "pattern": "(a+)+$"}}},
        {
            "type": "object",
            "properties": {"value": {"type": "string", "writeOnly": True, "default": "secret-literal"}},
        },
    ],
)
def test_unsupported_contract_is_explicit(schema):
    assert input_contract(schema)["supported"] is False
    with pytest.raises(InputContractError):
        validate_inputs(schema, {})


def test_no_input_contract_is_closed_and_sensitive_literals_are_refused():
    assert input_contract(no_input_schema())["fields"] == []
    assert validate_inputs(no_input_schema(), None) == {}
    with pytest.raises(InputContractError):
        validate_inputs(no_input_schema(), {"unknown": 1})
    schema = {
        "type": "object",
        "properties": {"credential": {"type": "string", "writeOnly": True}},
        "required": ["credential"],
    }
    with pytest.raises(InputContractError):
        validate_inputs(schema, {"credential": "PRIVATE-LITERAL"})
    assert validate_inputs(schema, {"credential": "secret://org/key"}) == {"credential": "secret://org/key"}


def test_database_idempotency_constraint_cannot_be_bypassed(world):
    with tenant_context(world.tenant):
        row = reserve(world)
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkflowDefinitionStart.objects.create(
            organization=world.org,
            definition=world.definition,
            execution=WorkflowRun.objects.create(
                organization=world.org, workflow_id="duplicate-engine-record"
            ),
            actor_key=row.actor_key,
            request_id=row.request_id,
            request_digest=row.request_digest,
            definition_revision=row.definition_revision,
            input_schema_digest=row.input_schema_digest,
            payload_backend_kind=row.payload_backend_kind,
            payload_ciphertext=row.payload_ciphertext,
        )


def test_nested_definition_change_invalidates_top_review(world):
    child = WorkflowDefinition.objects.create(
        name="Nested", slug="nested", organization=world.org, model_label=""
    )
    child_stage = WorkflowStage.objects.create(
        definition=child, slug="nested-stage", order=0, kind="checkpoint"
    )
    stage = world.definition.stages.get()
    stage.kind = "workflow"
    stage.workflow_ref = "nested"
    stage.save()
    reviewed = definition_revision(world.definition)
    child_stage.prompt = "Changed nested execution"
    child_stage.save()
    with tenant_context(world.tenant), pytest.raises(ReviewedStartError, match="changed"):
        reserve(world, revision=reviewed)


def test_revision_and_freeze_query_count_is_constant_for_repeated_agents_and_sibling_breadth(
    world, permission_resolver, monkeypatch
):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from astrolift_identity.models import Team
    from astrolift_registry.models import RegisteredApp, Workload
    from workflows import reviewed_starts
    from workflows.reviewed_starts import _freeze_plans

    permission_checks = []
    original_check = reviewed_starts.check_permission

    def record_check(permission, scope):
        permission_checks.append((permission, scope.kind, scope.id))
        return original_check(permission, scope=scope)

    monkeypatch.setattr(reviewed_starts, "check_permission", record_check)

    team = Team.objects.create(organization=world.org, name="Query proof", slug="review-query-proof")
    app = RegisteredApp.objects.create(
        organization=world.org, team=team, name="Query proof", slug="query-proof"
    )
    workload = Workload.objects.create(
        registered_app=app, kind="agent", name="Query proof agent", slug="query-proof-agent"
    )
    permission_resolver.grant(Permission.AGENT_DISPATCH)

    def add_child(index):
        child = WorkflowDefinition.objects.create(
            organization=world.org, model_label="", name=f"Query child{index}", slug=f"query-child-{index}"
        )
        WorkflowStage.objects.create(
            definition=child,
            order=0,
            kind="agent_dispatch",
            agent_definition=workload,
            slug=f"query-child-agent-{index}",
        )
        WorkflowStage.objects.create(
            definition=world.definition,
            order=index + 1,
            kind="workflow",
            workflow_ref=child.slug,
            slug=f"query-parent-child-{index}",
        )

    def counts():
        permission_checks.clear()
        with tenant_context(world.tenant):
            with CaptureQueriesContext(connection) as revision_queries:
                definition_revision(world.definition)
            with CaptureQueriesContext(connection) as plan_queries:
                plans = _freeze_plans(SimpleNamespace(organization_id=world.org.pk), world.definition)
        assert sum(check[0] == Permission.WORKFLOW_TRIGGER for check in permission_checks) == 1
        assert sum(check[0] == Permission.AGENT_DISPATCH for check in permission_checks) == 1
        return len(revision_queries), len(plan_queries), len(plans)

    add_child(0)
    narrow = counts()
    for index in range(1, 8):
        add_child(index)
    for index in range(8):
        WorkflowStage.objects.create(
            definition=world.definition,
            order=index + 20,
            kind="agent_dispatch",
            agent_definition=workload,
            slug=f"repeated-query-agent-{index}",
        )
    broad = counts()
    assert narrow[:2] == broad[:2] == (3, 4)
    assert narrow[2] == 2 and broad[2] == 9


def test_nested_change_on_second_connection_before_graph_lock_refuses_review(world, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections

    from workflows import reviewed_starts

    child = WorkflowDefinition.objects.create(
        organization=world.org, model_label="", name="Race child", slug="race-child"
    )
    child_stage = WorkflowStage.objects.create(
        definition=child, order=0, kind="checkpoint", slug="race-child-stage"
    )
    parent_stage = world.definition.stages.get()
    parent_stage.kind = "workflow"
    parent_stage.workflow_ref = child.slug
    parent_stage.save()
    reviewed = definition_revision(world.definition)
    original = reviewed_starts._definition_graph

    def change():
        close_old_connections()
        try:
            row = WorkflowStage.objects.get(pk=child_stage.pk)
            row.prompt = "Changed before graph lock"
            row.save()
        finally:
            close_old_connections()

    def load(*args, **kwargs):
        if kwargs.get("lock"):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(change).result(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(reviewed_starts, "_definition_graph", load)
    with tenant_context(world.tenant), pytest.raises(ReviewedStartError, match="changed"):
        reserve(world, revision=reviewed)
    assert not WorkflowDefinitionStart.objects.exists()
    assert not WorkflowRun.objects.exists()


def test_locked_nested_stages_cannot_change_between_revision_and_frozen_plan(world, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import close_old_connections, connection

    from workflows import reviewed_starts

    child = WorkflowDefinition.objects.create(
        organization=world.org, model_label="", name="Locked child", slug="locked-child"
    )
    child_stage = WorkflowStage.objects.create(
        definition=child, order=0, kind="checkpoint", slug="locked-child-stage"
    )
    parent_stage = world.definition.stages.get()
    parent_stage.kind = "workflow"
    parent_stage.workflow_ref = child.slug
    parent_stage.save()
    reviewed = definition_revision(world.definition)
    original = reviewed_starts._freeze_plans
    waiting = Event()
    backend_pid = []
    futures = []

    def change():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_pid.append(cursor.fetchone()[0])
            row = WorkflowStage.objects.get(pk=child_stage.pk)
            row.prompt = "Changed after reservation commits"
            waiting.set()
            row.save()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:

        def freeze(*args, **kwargs):
            futures.append(pool.submit(change))
            assert waiting.wait(timeout=5)
            for _ in range(100):
                with connection.cursor() as cursor:
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", backend_pid)
                    blocked = cursor.fetchone()[0] == "Lock"
                if blocked:
                    break
                time.sleep(0.01)
            else:
                raise AssertionError("The concurrent nested-stage update was not locked")
            return original(*args, **kwargs)

        monkeypatch.setattr(reviewed_starts, "_freeze_plans", freeze)
        with tenant_context(world.tenant):
            row = reserve(world, revision=reviewed)
        futures[0].result(timeout=5)
    assert request_payload(row)["plans"][str(child.pk)]["stages"][0]["prompt"] == ""
    child_stage.refresh_from_db()
    assert child_stage.prompt == "Changed after reservation commits"


def test_input_schema_manifest_round_trip_preserves_null_enum_and_defaults(world):
    from workflows.manifest import definition_to_manifest, emit_workflow_manifest, parse_workflow_manifest

    contract = {
        "type": "object",
        "properties": {"choice": {"type": ["string", "null"], "enum": [None, "small"], "default": None}},
        "additionalProperties": False,
    }
    world.definition.input_schema = contract
    world.definition.save()
    exported = emit_workflow_manifest(definition_to_manifest(world.definition))
    assert parse_workflow_manifest(exported).definition.input_schema == contract


def test_disposable_fixture_plan_is_read_only_then_creates_exact_supported_contracts(world):
    import io
    import json

    from django.core.management import call_command

    output = io.StringIO()
    initial = WorkflowDefinition.objects.count()
    call_command("create_reviewed_start_fixtures", organization=str(world.org.guid), stdout=output)
    assert WorkflowDefinition.objects.count() == initial
    output = io.StringIO()
    call_command(
        "create_reviewed_start_fixtures", organization=str(world.org.guid), execute=True, stdout=output
    )
    proof = json.loads(output.getvalue())
    assert {row["expectedOutcome"] for row in proof["fixtures"]} == {"completed", "failed"}
    assert all(
        WorkflowDefinition.objects.get(guid=row["definitionId"]).input_schema == no_input_schema()
        for row in proof["fixtures"]
    )
