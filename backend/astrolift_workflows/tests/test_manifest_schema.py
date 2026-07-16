"""GraphQL coverage for the workflow manifest import/export surface (#973)."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from astrolift_workflows.schema.manifest import (
    WorkflowManifestMutation,
    WorkflowManifestQuery,
)
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from workflows.models import WorkflowDefinition, WorkflowStage

pytestmark = pytest.mark.django_db

VALID_TOML = """\
[workflow]
slug = "feature-dev"
name = "Feature Dev"
pattern = "chained"

[[stage]]
kind = "agent_dispatch"
role = "implementer"
skills = ["write-tests", "acme/dev-skills/lint@v2"]
"""


def _info(user=None):
    rf = RequestFactory()
    return SimpleNamespace(context=SimpleNamespace(user=user, request=rf.get("/app/gql/config/")))


@contextmanager
def _tenant(org_id=1):
    with tenant_context(TenantContext(organization_id=org_id)):
        yield


# ---- preview ----------------------------------------------------------------


def test_preview_denied_without_read():
    q = WorkflowManifestQuery()
    with _tenant():
        with pytest.raises(PermissionDenied):
            q.preview_workflow_manifest(_info(), toml=VALID_TOML)


def test_preview_parses_structure(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    q = WorkflowManifestQuery()
    with _tenant():
        result = q.preview_workflow_manifest(_info(), toml=VALID_TOML)
    assert result.ok is True
    assert result.definition.slug == "feature-dev"
    assert result.definition.pattern == "chained"
    assert len(result.stages) == 1
    assert result.stages[0].role == "implementer"
    assert result.stages[0].skills == ["write-tests", "acme/dev-skills/lint@v2"]


def test_preview_malformed_returns_structured_error(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    q = WorkflowManifestQuery()
    with _tenant():
        result = q.preview_workflow_manifest(
            _info(),
            toml='[workflow]\nslug = "w"\nname = "W"\npattern = "nope"\n',
        )
    assert result.ok is False
    assert result.error_path == "workflow.pattern"
    assert result.definition is None


# ---- export -----------------------------------------------------------------


def _make_org():
    from astrolift_identity.models import Organization

    return Organization.objects.create(name="Manifest Org", slug="manifest-org")


def _make_definition(org_id):
    definition = WorkflowDefinition.objects.create(
        name="Feature Dev",
        slug="feature-dev",
        pattern_kind=WorkflowDefinition.PatternKind.CHAINED,
        model_label="agents.Agent",
        organization_id=org_id,
    )
    WorkflowStage.objects.create(
        definition=definition,
        order=0,
        kind=WorkflowStage.StageKind.AGENT_DISPATCH,
        role="implementer",
        skill_refs=["write-tests"],
    )
    return definition


def test_export_denied_without_read():
    q = WorkflowManifestQuery()
    with _tenant():
        with pytest.raises(PermissionDenied):
            q.export_workflow_manifest(_info(), definition_slug="feature-dev")


def test_export_emits_round_trippable_toml(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    org = _make_org()
    _make_definition(org.pk)

    q = WorkflowManifestQuery()
    with _tenant(org_id=org.pk):
        result = q.export_workflow_manifest(_info(), definition_slug="feature-dev")

    assert result.ok is True
    assert 'slug = "feature-dev"' in result.toml

    from workflows.manifest import parse_workflow_manifest

    parsed = parse_workflow_manifest(result.toml)
    assert parsed.definition.slug == "feature-dev"
    assert parsed.stages[0].role == "implementer"
    assert parsed.stages[0].skills == ["write-tests"]


def test_export_unknown_slug_returns_error(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_READ)
    org = _make_org()
    q = WorkflowManifestQuery()
    with _tenant(org_id=org.pk):
        result = q.export_workflow_manifest(_info(), definition_slug="does-not-exist")
    assert result.ok is False
    assert result.toml is None


# ---- import (#970/#972) -------------------------------------------------------


def test_import_denied_without_create():
    m = WorkflowManifestMutation()
    with _tenant():
        with pytest.raises(PermissionDenied):
            m.import_workflow_manifest(_info(), toml=VALID_TOML)


def test_import_preview_parses_without_persisting(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    before = WorkflowDefinition.objects.count()
    m = WorkflowManifestMutation()
    with _tenant():
        result = m.import_workflow_manifest(_info(), toml=VALID_TOML)
    assert result.ok is True
    assert result.created_slug is None
    assert result.manifest.definition.slug == "feature-dev"
    assert result.manifest.definition.pattern == "chained"
    assert WorkflowDefinition.objects.count() == before


def test_import_malformed_toml_returns_structured_error(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    before = WorkflowDefinition.objects.count()
    m = WorkflowManifestMutation()
    with _tenant():
        result = m.import_workflow_manifest(_info(), toml="[workflow]\nslug = ", preview=False)
    assert result.ok is False
    assert result.errors[0].field == "toml"
    assert result.manifest.ok is False
    assert result.manifest.error
    assert WorkflowDefinition.objects.count() == before


def test_import_persists_into_caller_org(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    org = _make_org()
    m = WorkflowManifestMutation()
    with _tenant(org_id=org.pk):
        result = m.import_workflow_manifest(_info(), toml=VALID_TOML, preview=False)

    assert result.ok is True
    assert result.created_slug == "feature-dev"
    # A catalogue global may share the slug — the import lands org-scoped.
    definition = WorkflowDefinition.objects.get(slug="feature-dev", organization_id=org.pk)
    assert definition.is_enabled is False  # imported disabled, bind agents first
    stage = definition.stages.get()
    assert stage.kind == WorkflowStage.StageKind.AGENT_DISPATCH.value
    assert stage.role == "implementer"


def test_import_uniquifies_slug_on_collision(permission_resolver):
    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    org = _make_org()
    m = WorkflowManifestMutation()
    with _tenant(org_id=org.pk):
        first = m.import_workflow_manifest(_info(), toml=VALID_TOML, preview=False)
        second = m.import_workflow_manifest(_info(), toml=VALID_TOML, preview=False)
    assert first.created_slug == "feature-dev"
    assert second.created_slug == "feature-dev-1"
    # The manifest echoes the slug that actually persisted.
    assert second.manifest.definition.slug == "feature-dev-1"


def test_import_foreign_org_id_rejected(permission_resolver):
    from astrolift_identity.models import Organization

    permission_resolver.grant(Permission.WORKFLOW_CREATE)
    org = _make_org()
    other = Organization.objects.create(name="Other Org", slug="other-manifest-org")
    before = WorkflowDefinition.objects.count()
    m = WorkflowManifestMutation()
    with _tenant(org_id=org.pk):
        result = m.import_workflow_manifest(_info(), toml=VALID_TOML, preview=False, org_id=str(other.guid))
    assert result.ok is False
    assert any("mismatch" in msg for e in result.errors for msg in e.messages)
    assert WorkflowDefinition.objects.count() == before
