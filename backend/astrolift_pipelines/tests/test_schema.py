"""Schema tests for astrolift_pipelines — Pipeline CRUD + tenant isolation (#67).

Coverage:
* createPipeline happy path creates a Pipeline row and returns it in the result.
* createPipeline with missing name returns a VALIDATION error.
* astroliftPipelines query returns only pipelines for the current tenant —
  another org's pipelines must not leak.
* astroliftPipeline (single) returns None for a pipeline in another org.
* updatePipeline changes only the supplied fields.
* deletePipeline soft-deletes the row (deleted_at is set).
* createTrigger happy path creates a Trigger row for the pipeline.
* createTrigger with unknown kind returns VALIDATION error.
* triggerPipelineRun (stub) creates a pending PipelineRun row.
* cancelPipelineRun transitions a pending run to cancelled.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization
from astrolift_pipelines.models import Pipeline, PipelineRun, Trigger
from astrolift_pipelines.schema.mutations import (
    CreatePipelineInput,
    CreateTriggerInput,
    PipelinesMutation,
    UpdatePipelineInput,
)
from astrolift_pipelines.schema.queries import PipelinesQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_opensearch(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme Corp", slug="acme-corp")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Rival Inc", slug="rival-inc")


@pytest.fixture
def user(org):
    # Superuser so the @require_permission gates on the pipeline
    # mutations/queries resolve via the bootstrap-admin bypass; these
    # tests cover CRUD + tenant isolation, not the permission matrix.
    return User.objects.create_user(
        username="pipeline-ops",
        email="ops@example.com",
        password="x",
        is_superuser=True,
        is_staff=True,
    )


@pytest.fixture
def pipeline(org):
    return Pipeline.objects.create(
        organization=org,
        name="deploy-pipeline",
        repo_url="https://github.com/acme/app",
        default_branch="main",
    )


@pytest.fixture
def other_pipeline(other_org):
    return Pipeline.objects.create(
        organization=other_org,
        name="rival-pipeline",
        repo_url="https://github.com/rival/app",
        default_branch="main",
    )


def _admin_info(user):
    """Minimal request info with all permissions for mutation calls."""
    from types import SimpleNamespace

    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user, auth=None)))


def _readonly_info(user):
    """Info object with only APP_READ permission."""
    from types import SimpleNamespace

    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user, auth=None)))


# ---------------------------------------------------------------------------
# createPipeline
# ---------------------------------------------------------------------------


def test_create_pipeline_happy_path(user, org):
    mutation = PipelinesMutation()
    info = _admin_info(user)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.create_pipeline(
            info,
            input=CreatePipelineInput(
                name="ci-pipeline",
                repo_url="https://github.com/acme/ci",
                default_branch="main",
            ),
        )
    assert result.ok, result.errors
    assert result.data is not None
    assert result.data.name == "ci-pipeline"
    assert result.data.repo_url == "https://github.com/acme/ci"
    assert result.data.default_branch == "main"
    # Verify the row was persisted
    assert Pipeline.objects.filter(organization=org, name="ci-pipeline").exists()


def test_create_pipeline_missing_name_returns_validation_error(user, org):
    mutation = PipelinesMutation()
    info = _admin_info(user)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.create_pipeline(
            info,
            input=CreatePipelineInput(name="", repo_url="https://github.com/acme/app"),
        )
    assert not result.ok
    assert result.errors
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "name"


def test_create_pipeline_duplicate_name_returns_conflict(user, org, pipeline):
    mutation = PipelinesMutation()
    info = _admin_info(user)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.create_pipeline(
            info,
            input=CreatePipelineInput(
                name="deploy-pipeline",
                repo_url="https://github.com/acme/other",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "CONFLICT"


# ---------------------------------------------------------------------------
# updatePipeline
# ---------------------------------------------------------------------------


def test_update_pipeline_changes_fields(user, org, pipeline):
    mutation = PipelinesMutation()
    info = _admin_info(user)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.update_pipeline(
            info,
            id=str(pipeline.guid),
            input=UpdatePipelineInput(default_branch="release"),
        )
    assert result.ok, result.errors
    assert result.data.default_branch == "release"
    pipeline.refresh_from_db()
    assert pipeline.default_branch == "release"


def test_update_pipeline_not_found_for_other_org(user, org, other_pipeline):
    mutation = PipelinesMutation()
    info = _admin_info(user)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.update_pipeline(
            info,
            id=str(other_pipeline.guid),
            input=UpdatePipelineInput(default_branch="main"),
        )
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# deletePipeline
# ---------------------------------------------------------------------------


def test_delete_pipeline_soft_deletes(user, org, pipeline):
    mutation = PipelinesMutation()
    info = _admin_info(user)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.delete_pipeline(info, id=str(pipeline.guid))
    assert result.ok, result.errors
    pipeline.refresh_from_db()
    assert pipeline.deleted_at is not None


# ---------------------------------------------------------------------------
# astroliftPipelines — tenant isolation
# ---------------------------------------------------------------------------


def test_astrolift_pipelines_returns_only_own_org(user, org, pipeline, other_pipeline):
    """Pipelines belonging to another org must not appear in the response."""
    query = PipelinesQuery()
    info = _admin_info(user)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        results = query.astrolift_pipelines(info)

    ids = {str(p.id) for p in results}
    assert str(pipeline.guid) in ids
    assert str(other_pipeline.guid) not in ids


def test_astrolift_pipeline_single_is_tenant_scoped(user, org, other_pipeline):
    """astroliftPipeline must return None for a pipeline in another org."""
    query = PipelinesQuery()
    info = _admin_info(user)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = query.astrolift_pipeline(info, id=str(other_pipeline.guid))
    assert result is None


# ---------------------------------------------------------------------------
# createTrigger
# ---------------------------------------------------------------------------


def test_create_trigger_happy_path(user, org, pipeline, permission_resolver):
    mutation = PipelinesMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.create_trigger(
            info,
            input=CreateTriggerInput(
                pipeline_id=str(pipeline.guid),
                kind="push",
                config='{"branches": ["main"]}',
            ),
        )
    assert result.ok, result.errors
    assert result.data is not None
    assert result.data.kind == "push"
    assert result.data.config == {"branches": ["main"]}
    assert Trigger.objects.filter(pipeline=pipeline, kind="push").exists()


def test_create_trigger_unknown_kind(user, org, pipeline, permission_resolver):
    mutation = PipelinesMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.create_trigger(
            info,
            input=CreateTriggerInput(
                pipeline_id=str(pipeline.guid),
                kind="unknown-kind",
                config="{}",
            ),
        )
    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "kind"


# ---------------------------------------------------------------------------
# triggerPipelineRun (stub)
# ---------------------------------------------------------------------------


def test_trigger_pipeline_run_creates_pending_run(user, org, pipeline, permission_resolver, settings):
    # The mutation dispatches to Temporal now that the dispatch works at all
    # (#1614) -- it used to raise ImportError on its first line and be
    # swallowed, so no test needed this. Same stub the other suites use.
    settings.ASTROLIFT_TEMPORAL_ENABLED = False

    mutation = PipelinesMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.trigger_pipeline_run(
            info,
            pipeline_id=str(pipeline.guid),
            ref="refs/heads/main",
            request_id="reviewed-schema-test",
            expected_version=pipeline.version,
            confirmed=True,
        )
    assert not result.ok
    assert result.data.dispatch_status == "uncertain"
    assert result.data.status == "pending"
    assert result.data.run_number == 1
    assert PipelineRun.objects.filter(pipeline=pipeline, status="pending").exists()


# ---------------------------------------------------------------------------
# cancelPipelineRun
# ---------------------------------------------------------------------------


def test_legacy_unreviewed_cancel_cannot_claim_terminal_success(user, org, pipeline, permission_resolver):
    run = PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=1,
        trigger_kind=PipelineRun.TriggerKind.MANUAL,
        trigger_ref="refs/heads/main",
        status=PipelineRun.Status.PENDING,
    )
    mutation = PipelinesMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.cancel_pipeline_run(info, run_id=str(run.guid))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    run.refresh_from_db()
    assert run.status == "pending"
    assert run.finished_at is None


def test_legacy_unreviewed_cancel_preserves_active_jobs_and_steps(user, org, pipeline, permission_resolver):
    """The mutation must settle the whole run, not only its own row.

    Asserted through the mutation rather than the service, because the
    delegation *is* the fix: the service already cascaded and had no caller,
    so a test that only exercised the service would pass with the mutation
    still flipping status locally.

    Nothing in the codebase cancelled step runs before this. The Temporal
    workflow's cancel handler cascades to JobRun and stops, and step runs
    are only settled when a pod actually completed -- so a cancelled run's
    steps read `running` forever.
    """
    from astrolift_pipelines.models import Job, JobRun, Step, StepRun

    run = PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=3,
        trigger_kind=PipelineRun.TriggerKind.PUSH,
        trigger_ref="refs/heads/main",
        status=PipelineRun.Status.RUNNING,
    )
    job = Job.objects.create(pipeline=pipeline, pipeline_run=run, job_id="build", name="Build")
    job_run = JobRun.objects.create(pipeline_run=run, job=job, status=JobRun.Status.RUNNING)
    step = Step.objects.create(job=job, step_id="compile", position=0, run="make")
    step_run = StepRun.objects.create(job_run=job_run, step=step, status=StepRun.Status.RUNNING)

    mutation = PipelinesMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.cancel_pipeline_run(info, run_id=str(run.guid))

    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
    run.refresh_from_db()
    job_run.refresh_from_db()
    step_run.refresh_from_db()
    assert run.status == "running"
    assert job_run.status == "running"
    assert step_run.status == "running"


def test_cancel_pipeline_run_already_finished(user, org, pipeline, permission_resolver):
    run = PipelineRun.objects.create(
        pipeline=pipeline,
        run_number=2,
        trigger_kind=PipelineRun.TriggerKind.PUSH,
        status=PipelineRun.Status.SUCCESS,
    )
    mutation = PipelinesMutation()
    info = _admin_info(user)
    permission_resolver.grant(Permission.APP_UPDATE)
    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = mutation.cancel_pipeline_run(info, run_id=str(run.guid))
    assert not result.ok
    assert result.errors[0].code == "PRECONDITION"
