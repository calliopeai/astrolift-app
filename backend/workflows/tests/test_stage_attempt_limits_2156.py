"""Authoring/storage bounds use PostgreSQL, without permissive coercion."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from astrolift_identity.models import Organization
from astrolift_workflows.activities.workflow_stage_activities import _get_workflow_stages_sync
from core.run_input_contract import digest
from core.tenancy import TenantContext, tenant_context
from workflows.manifest import (
    create_definition_from_manifest,
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
    replace_definition_content,
)
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.reviewed_starts import definition_revision, request_payload, reserve_start
from workflows.services.dsl_parser import DslParseError, parse_workflows_dsl, validate_workflow_dsl
from workflows.stage_limits import validate_stage_attempts


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 21, 1.5, "4"])
def test_invalid_attempt_bounds_are_rejected_by_both_authoring_formats(value):
    with pytest.raises(ValueError, match="between 1 and 20"):
        validate_stage_attempts(value)
    # The DSL's parser does not turn booleans, strings or floats into counts.
    import yaml

    content = yaml.safe_dump(
        {
            "workflows": [
                {
                    "slug": "bounded",
                    "name": "Bounded",
                    "stages": [
                        {"kind": "checkpoint", "max_attempts": value},
                    ],
                }
            ]
        }
    )
    with pytest.raises(DslParseError, match="max_attempts"):
        parse_workflows_dsl(content)
    parsed = {
        "slug": "bounded",
        "name": "Bounded",
        "pattern_kind": "single",
        "stages": [
            {"kind": "checkpoint", "max_attempts": value},
        ],
    }
    assert any("max_attempts" in error for error in validate_workflow_dsl(parsed))


@pytest.mark.django_db
def test_manifest_persistence_replacement_and_revision_keep_attempt_bound():
    org = Organization.objects.create(name="Bounded workflows", slug="bounded-workflows")
    source = """[workflow]
slug = "bounded"
name = "Bounded"
[[stage]]
kind = "checkpoint"
max_attempts = 4
"""
    parsed = parse_workflow_manifest(source)
    assert parsed.stages[0].max_attempts == 4
    assert parse_workflow_manifest(emit_workflow_manifest(parsed)) == parsed
    definition = create_definition_from_manifest(parsed, organization=org, is_enabled=True)
    assert definition.stages.get().max_attempts == 4
    assert definition_to_manifest(definition) == parsed
    assert (
        _get_workflow_stages_sync("bounded", review_organization_id=org.pk)["stages"][0]["max_attempts"] == 4
    )
    before = definition_revision(definition)
    replacement = parse_workflow_manifest(source.replace("max_attempts = 4", "max_attempts = 1"))
    replace_definition_content(definition, replacement, organization=org)
    assert definition.stages.get().max_attempts == 1
    assert definition_revision(definition) != before


@pytest.mark.django_db
@pytest.mark.parametrize("value", [0, 21])
def test_database_refuses_out_of_range_attempt_counts(value):
    definition = WorkflowDefinition.objects.create(name="Bounded", slug="bounded", model_label="")
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkflowStage.objects.create(definition=definition, order=0, kind="checkpoint", max_attempts=value)


@pytest.mark.django_db
def test_reviewed_attempt_count_stays_frozen_after_definition_edit():
    org = Organization.objects.create(name="Frozen caps", slug="frozen-caps")
    user = get_user_model().objects.create_superuser(
        username="cap-reviewer", email="cap@example.test", password="test"
    )
    definition = WorkflowDefinition.objects.create(
        name="Frozen", slug="frozen", organization=org, model_label=""
    )
    stage = WorkflowStage.objects.create(definition=definition, order=0, kind="checkpoint", max_attempts=4)
    with tenant_context(TenantContext(organization_id=org.pk, actor_user_id=user.pk)):
        row = reserve_start(
            definition_id=definition.guid,
            expected_revision=definition_revision(definition),
            expected_input_schema_digest=digest(definition.input_schema),
            request_id="frozen-cap",
            inputs={},
            user=user,
        )
        stage.max_attempts = 1
        stage.save()
        plan = _get_workflow_stages_sync(
            "frozen", str(row.execution.pk), workflow_definition_id=str(definition.pk)
        )
        assert plan["stages"][0]["max_attempts"] == 4
        assert request_payload(row)["plans"][str(definition.pk)] == plan
