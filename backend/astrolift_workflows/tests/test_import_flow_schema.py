"""GraphQL coverage for importWorkflowFlow (#984/#985/#986)."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from astrolift_workflows.schema.import_flow import WorkflowImportMutation, _stage_types
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from workflows.importers.base import FlowImportResult
from workflows.manifest import ParsedWorkflowManifest, WorkflowDefSpec, WorkflowStageSpec
from workflows.models import WorkflowDefinition, WorkflowStage
from workflows.tests.importer_fixtures import flowise_gate, langflow_chained

pytestmark = pytest.mark.django_db


def _info(user=None):
    rf = RequestFactory()
    return SimpleNamespace(context=SimpleNamespace(user=user, request=rf.get("/app/gql/config/")))


@contextmanager
def _tenant(org_id=1):
    with tenant_context(TenantContext(organization_id=org_id)):
        yield


def _make_org(slug="import-org"):
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Import Org", slug=slug)


# ---- permission gate --------------------------------------------------------


def test_import_denied_without_create_permission():
    m = WorkflowImportMutation()
    with _tenant():
        with pytest.raises(PermissionDenied):
            m.import_workflow_flow(_info(), format="langflow", payload=langflow_chained(), preview=True)


# ---- preview (no persistence) ----------------------------------------------


def test_preview_returns_manifest_and_gaps_without_persisting(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    before = WorkflowDefinition.objects.count()
    m = WorkflowImportMutation()
    with _tenant():
        result = m.import_workflow_flow(_info(), format="langflow", payload=langflow_chained(), preview=True)

    assert result.ok is True
    assert result.preview is True
    assert result.created_slug is None
    assert result.definition.pattern == "chained"
    assert [s.role for s in result.stages] == ["Implementer", "Reviewer"]
    assert any(g.code == "flow_input" for g in result.gaps)
    # Preview persists nothing (count unchanged from the catalogue baseline).
    assert WorkflowDefinition.objects.count() == before


def test_stage_types_preserve_runtime_and_output_fields():
    result = FlowImportResult(
        manifest=ParsedWorkflowManifest(
            definition=WorkflowDefSpec(slug="imported", name="Imported", pattern="single"),
            stages=[
                WorkflowStageSpec(
                    order=0,
                    kind="agent_dispatch",
                    environment_spec_slug="triage-prod",
                    output_key="findings",
                )
            ],
        ),
        gaps=[],
    )

    stage = _stage_types(result)[0]

    assert stage.environment_spec_slug == "triage-prod"
    assert stage.output_key == "findings"


def test_unknown_format_returns_structured_error(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    before = WorkflowDefinition.objects.count()
    m = WorkflowImportMutation()
    with _tenant():
        result = m.import_workflow_flow(_info(), format="zapier", payload={}, preview=True)
    assert result.ok is False
    assert result.errors[0].field == "format"
    assert WorkflowDefinition.objects.count() == before


# ---- create (org-scoped persistence) ---------------------------------------


def test_create_persists_org_scoped_definition(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    org = _make_org()
    m = WorkflowImportMutation()
    with _tenant(org_id=org.pk):
        result = m.import_workflow_flow(_info(), format="flowise", payload=flowise_gate(), preview=False)

    assert result.ok is True
    assert result.preview is False
    assert result.created_slug == "approval-flow"

    definition = WorkflowDefinition.objects.get(slug="approval-flow")
    assert definition.organization_id == org.pk
    assert definition.is_enabled is False  # imported disabled, bind agents first
    kinds = list(definition.stages.order_by("order").values_list("kind", flat=True))
    assert kinds == [
        WorkflowStage.StageKind.AGENT_DISPATCH.value,
        WorkflowStage.StageKind.HUMAN_GATE.value,
        WorkflowStage.StageKind.AGENT_DISPATCH.value,
    ]
    # The unmappable memory node still surfaces as a gap on the create path.
    assert any(g.code == "unmapped_node" for g in result.gaps)


def test_create_uniquifies_slug_on_collision(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    org = _make_org()
    m = WorkflowImportMutation()
    with _tenant(org_id=org.pk):
        first = m.import_workflow_flow(_info(), format="flowise", payload=flowise_gate(), preview=False)
        second = m.import_workflow_flow(_info(), format="flowise", payload=flowise_gate(), preview=False)

    assert first.created_slug == "approval-flow"
    assert second.created_slug == "approval-flow-1"
    assert WorkflowDefinition.objects.filter(organization_id=org.pk).count() == 2
