"""Tests for ``deregisterAstroliftApp`` mutation + ``DeregisterAppWorkflow`` (#392).

Coverage:
* Mutation surface — happy path, confirm-name mismatch, permission
  gate, idempotent workflow id on re-fire.
* Workflow body — happy path soft-deletes the app; partial failure
  (managed-service deprovision raises) leaves the app live and
  returns ``ok=False`` with the still-live list.
* Source-webhook activity — GitHub-App-install short-circuit,
  no-repo short-circuit, and the host-side delete path against a
  stubbed urllib.

Backend stack is real Postgres; the Temporal client is replaced with
the ``temporal_recorder`` fixture so we can assert the (workflow,
args, workflow_id) tuple without running a Temporal server. Workflow-
body tests drive the workflow object directly with stub activity
functions so each step is observable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_lifecycle.schema.mutations import (
    DeregisterAppInput,
    LifecycleMutation,
)
from astrolift_services.models import (
    AppSecretBundleRef,
    SecretBundle,
)
from astrolift_workflows.activities.app_deregister import (
    _delete_source_webhook_sync,
)
from astrolift_workflows.inputs import (
    Actor,
    WorkflowResult,
)
from astrolift_workflows.inputs import (
    DeregisterAppInput as DeregisterWfInput,
)
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _tenant_for(org, actor):
    return tenant_context(
        TenantContext(organization_id=org.id, actor_user_id=actor.id),
    )


def _grant_delete(resolver):
    resolver.grant(Permission.APP_DELETE)


# ---------------------------------------------------------------------------
# Mutation surface
# ---------------------------------------------------------------------------


def test_deregister_mutation_happy_path_starts_workflow(
    app,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """Happy path: confirm-name matches, workflow id is deterministic,
    payload carries it back to the operator. Initial kickoff returns
    an empty still-live list (the workflow runs async)."""
    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.deregister_astrolift_app(
            info=fake_info,
            input=DeregisterAppInput(
                app_slug=app.slug,
                confirm_name=app.name,
            ),
        )
    assert result.ok is True, result.errors
    assert result.data.workflow_id == f"DeregisterAppWorkflow-{app.guid}"
    assert result.data.still_live_resources == []
    # Workflow was enqueued with the right id + payload shape.
    assert len(temporal_recorder.starts) == 1
    name, args, wf_id = temporal_recorder.starts[0]
    assert name == "DeregisterAppWorkflow"
    assert wf_id == f"DeregisterAppWorkflow-{app.guid}"
    assert isinstance(args[0], DeregisterWfInput)
    assert args[0].registered_app_id == app.pk
    # Hard-deregister always fires with the danger-zone four-corner.
    assert args[0].delete_data is True
    assert args[0].force_destroy is True


def test_deregister_mutation_confirm_name_mismatch_is_validation(
    app,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """A typo in the confirm field stops the mutation cold — the
    workflow never starts."""
    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.deregister_astrolift_app(
            info=fake_info,
            input=DeregisterAppInput(
                app_slug=app.slug,
                confirm_name=app.name + "-typo",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.VALIDATION.value for e in result.errors)
    # Crucially: no workflow started.
    assert temporal_recorder.starts == []
    # The app row is untouched.
    app.refresh_from_db()
    assert app.deleted_at is None


def test_deregister_mutation_requires_permission(
    app,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """Actor without app.delete is denied at the resolver entry —
    first-line permission check, no workflow."""
    # No grant.
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.deregister_astrolift_app(
            info=fake_info,
            input=DeregisterAppInput(
                app_slug=app.slug,
                confirm_name=app.name,
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PERMISSION_DENIED.value for e in result.errors)
    assert temporal_recorder.starts == []


def test_deregister_mutation_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.deregister_astrolift_app(
            info=fake_info,
            input=DeregisterAppInput(
                app_slug="ghost-app",
                confirm_name="Anything",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.NOT_FOUND.value for e in result.errors)
    assert temporal_recorder.starts == []


def test_deregister_mutation_resume_uses_same_workflow_id(
    app,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """Re-firing the same mutation hits the same workflow id; Temporal
    de-dup means the resume joins the live run rather than starting a
    parallel teardown. The recorder receives both starts (the dedup
    happens server-side at Temporal); the deterministic id is what
    matters at this layer."""
    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        first = mutation.deregister_astrolift_app(
            info=fake_info,
            input=DeregisterAppInput(app_slug=app.slug, confirm_name=app.name),
        )
        second = mutation.deregister_astrolift_app(
            info=fake_info,
            input=DeregisterAppInput(app_slug=app.slug, confirm_name=app.name),
        )
    assert first.ok and second.ok
    assert first.data.workflow_id == second.data.workflow_id
    assert first.data.workflow_id == f"DeregisterAppWorkflow-{app.guid}"
    assert {wf_id for _, _, wf_id in temporal_recorder.starts} == {f"DeregisterAppWorkflow-{app.guid}"}


# ---------------------------------------------------------------------------
# Source-webhook activity
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_webhook(app):
    """App that recorded a webhook install + carries source bookkeeping."""
    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.default_branch = "main"
    app.source_webhook_id = "4242"
    app.source_webhook_installed_at = datetime.now(UTC)
    app.save(
        update_fields=[
            "source_kind",
            "source_repo",
            "default_branch",
            "source_webhook_id",
            "source_webhook_installed_at",
            "updated_at",
            "version",
        ]
    )
    return app


def _make_github_oauth_connection(org):
    from astrolift_scm.models import SourceConnection
    from core.secrets import encrypt_at_rest

    encrypted = encrypt_at_rest(b"gho_test_token_never_hits_the_network")
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
        display_name="GitHub: alice",
        account_login="alice",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )


def test_delete_source_webhook_no_repo_short_circuits(app):
    """App with no source repo configured short-circuits with a
    clean ``no_repo`` status — no host call, no error."""
    summary = _delete_source_webhook_sync(app.pk)
    assert summary["status"] == "no_repo"
    assert summary["hook_id"] == ""


def test_delete_source_webhook_github_app_install_clears_bookkeeping(app, org):
    """GitHub-App-install connections never install per-repo hooks;
    the activity clears the platform bookkeeping (so the UI flips
    back to "not installed") without touching GitHub."""
    from astrolift_scm.models import SourceConnection
    from core.secrets import encrypt_at_rest

    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.source_webhook_id = "999"  # we have a recorded id but the App owns delivery
    app.source_webhook_installed_at = datetime.now(UTC)
    app.save(
        update_fields=[
            "source_kind",
            "source_repo",
            "source_webhook_id",
            "source_webhook_installed_at",
            "updated_at",
            "version",
        ]
    )
    encrypted = encrypt_at_rest(b"installation-token")
    SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL,
        display_name="GitHub App",
        account_login="acme",
        secret_backend_kind=encrypted.backend_kind,
        secret_ciphertext=encrypted.backend_ref,
        is_active=True,
    )
    summary = _delete_source_webhook_sync(app.pk)
    assert summary["status"] == "app_install"
    app.refresh_from_db()
    assert app.source_webhook_id == ""
    assert app.source_webhook_installed_at is None


def test_delete_source_webhook_calls_host_and_clears_bookkeeping(
    app_with_webhook,
    org,
):
    """Happy path: PAT/OAuth connection + recorded hook id → host
    DELETE call + bookkeeping cleared."""
    _make_github_oauth_connection(org)
    calls: list[str] = []

    def _fake_delete_webhook(connection, *, repo_full_name, hook_id):
        calls.append(f"{repo_full_name}:{hook_id}")

    with patch(
        "astrolift_scm.providers.delete_webhook",
        _fake_delete_webhook,
    ):
        summary = _delete_source_webhook_sync(app_with_webhook.pk)
    assert summary["status"] == "deleted"
    assert summary["hook_id"] == "4242"
    assert calls == ["acme/api:4242"]
    app_with_webhook.refresh_from_db()
    assert app_with_webhook.source_webhook_id == ""
    assert app_with_webhook.source_webhook_installed_at is None


# ---------------------------------------------------------------------------
# Workflow body — happy path + partial failure
# ---------------------------------------------------------------------------


class _FakeWorkflowAPI:
    """Captures workflow.execute_activity calls so each step is observable.

    Implements just enough of the temporalio.workflow surface for our
    workflow's body: ``execute_activity`` dispatches to an injected
    fake activity that returns/raises whatever the test wants.
    """

    def __init__(self, *, fakes: dict[str, object]):
        self._fakes = fakes
        self.calls: list[tuple[str, tuple, dict]] = []

    async def execute_activity(self, activity_fn, *args, **kwargs):
        name = getattr(activity_fn, "__name__", str(activity_fn))
        explicit_args = kwargs.pop("args", None)
        if explicit_args is not None:
            call_args = tuple(explicit_args)
        else:
            call_args = args
        self.calls.append((name, call_args, kwargs))
        fake = self._fakes.get(name)
        if fake is None:
            return None
        if isinstance(fake, Exception):
            raise fake
        if callable(fake):
            return fake(*call_args)
        return fake


def _run_workflow_with_fakes(app, fakes):
    """Run ``DeregisterAppWorkflow.run`` against a fake workflow API."""
    import asyncio

    from astrolift_workflows.workflows.deregister_app import DeregisterAppWorkflow

    fake_api = _FakeWorkflowAPI(fakes=fakes)
    actor = Actor(kind="user", user_id=None, display="tester")
    input = DeregisterWfInput(
        registered_app_id=app.pk,
        actor=actor,
        delete_data=True,
        force_destroy=True,
    )
    with patch(
        "astrolift_workflows.workflows.deregister_app.workflow",
        SimpleNamespace(
            execute_activity=fake_api.execute_activity,
            logger=SimpleNamespace(warning=lambda *a, **k: None),
        ),
    ):
        result = asyncio.run(DeregisterAppWorkflow().run(input))
    return fake_api, result


def test_workflow_happy_path_soft_deletes_app(app):
    """Every activity returns success → soft-delete fires + workflow
    returns ok=True with an empty still-live list."""
    fakes = {
        "mark_app_tearing_down": lambda *_: None,
        "delete_app_namespaces": lambda *_: ["dev-cluster/acme-hello"],
        "list_app_managed_service_ids": lambda *_: [],
        "deprovision_app_registry_repo": lambda *_: {"repo": "acme/hello"},
        "deprovision_app_identity_role": lambda *_: {"role": "astrolift-acme-hello"},
        "list_app_secret_targets": lambda *_: [],
        "delete_app_source_webhook": lambda *_: {
            "status": "not_installed",
            "detail": "nothing to do",
            "hook_id": "",
        },
        "revoke_app_deploy_tokens": lambda *_: 0,
        "soft_delete_app_records": lambda *_: {"registered_app": 1},
        "mark_app_deregistered": lambda *_: None,
    }
    _, result = _run_workflow_with_fakes(app, fakes)
    assert isinstance(result, WorkflowResult)
    assert result.ok is True
    assert result.data["still_live_resources"] == []
    # The soft_delete_app_records step ran.
    teardown = result.data["teardown"]
    assert teardown["platform_rows"]["ok"] is True


def test_workflow_partial_failure_keeps_app_live(app):
    """A managed-service deprovision failure marks ``managed_services``
    still live and gates the platform-row soft-delete so the app row
    survives for the operator's retry."""
    fakes = {
        "mark_app_tearing_down": lambda *_: None,
        "delete_app_namespaces": lambda *_: [],
        # One ms id flows through fan-out → the deprovision raises.
        "list_app_managed_service_ids": lambda *_: [12345],
        # Driver call raises — the workflow records it and keeps
        # going, but the partial failure gates the soft-delete.
        "deprovision_managed_service": RuntimeError("rds-final-snapshot required but skipped"),
        "deprovision_app_registry_repo": lambda *_: {"repo": "acme/hello"},
        "deprovision_app_identity_role": lambda *_: {"role": "astrolift-acme-hello"},
        "list_app_secret_targets": lambda *_: [],
        "delete_app_source_webhook": lambda *_: {
            "status": "not_installed",
            "detail": "",
            "hook_id": "",
        },
        "revoke_app_deploy_tokens": lambda *_: 0,
        # Soft-delete activity must NOT run on partial failure; the
        # workflow's guard skips this step when still_live is non-
        # empty. Wire it to raise so the test detects the bug if
        # someone removes the guard.
        "soft_delete_app_records": RuntimeError(
            "soft_delete should be gated on a clean teardown",
        ),
        "mark_app_deregistered": RuntimeError(
            "should not mark deregistered on partial failure",
        ),
    }
    _, result = _run_workflow_with_fakes(app, fakes)
    assert result.ok is False
    assert "managed_services" in result.data["still_live_resources"]
    # Platform-rows step recorded as skipped (not ok=True from soft-
    # delete activity).
    teardown = result.data["teardown"]
    assert teardown["platform_rows"]["ok"] is False
    assert "skipped" in teardown["platform_rows"]["detail"]
    # The app row is NOT soft-deleted.
    app.refresh_from_db()
    assert app.deleted_at is None


# ---------------------------------------------------------------------------
# Materialized-secret fan-out target listing
# ---------------------------------------------------------------------------


def test_list_app_secret_targets_emits_one_row_per_active_ref(app, org, env):
    """``list_app_secret_targets`` returns one row per active
    AppSecretBundleRef — the workflow uses this to drive
    ``delete_secret_from_cluster`` per (cluster, env, bundle)."""
    from astrolift_workflows.activities.app_deregister import (
        _list_app_secret_targets_sync,
    )

    bundle = SecretBundle.objects.create(
        organization=org,
        name="api-keys",
        slug="api-keys",
        backend_ref="api-keys",
    )
    AppSecretBundleRef.objects.create(
        registered_app=app,
        app_environment=env,
        secret_bundle=bundle,
        prefix="",
    )
    rows = _list_app_secret_targets_sync(app.pk)
    assert len(rows) == 1
    assert rows[0]["bundle_slug"] == "api-keys"
    assert rows[0]["registered_app_id"] == app.pk
    assert rows[0]["tenant_cluster_id"] == env.tenant_cluster_id
