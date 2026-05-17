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
    """Run ``DeregisterAppWorkflow.run`` against a fake workflow API.

    The grace-period cancel (#436 B) added a ``workflow.wait_condition``
    call before the destructive steps run. The default stub here raises
    TimeoutError so the existing happy-path / partial-failure tests
    flow straight through into the destructive activities — the cancel
    boundary itself is exercised by the dedicated tests further down.
    """
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

    async def _grace_window_timeout(*_a, **_k):
        # Default: no cancel signal arrives → grace window elapses →
        # workflow proceeds to destructive steps.
        raise TimeoutError("grace period elapsed")

    with patch(
        "astrolift_workflows.workflows.deregister_app.workflow",
        SimpleNamespace(
            execute_activity=fake_api.execute_activity,
            wait_condition=_grace_window_timeout,
            logger=SimpleNamespace(
                warning=lambda *a, **k: None,
                info=lambda *a, **k: None,
            ),
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


# ---------------------------------------------------------------------------
# #436 A — blast-radius preview query
# ---------------------------------------------------------------------------


def test_preview_deregister_returns_grouped_resource_lists(
    app,
    env,
    org,
    fake_info,
    actor,
    permission_resolver,
):
    """Preview returns actual object names grouped by k8s / managed-
    services / identity / network rather than generic resource labels."""
    from astrolift_lifecycle.models import DeployToken
    from astrolift_lifecycle.schema.queries import LifecycleQuery
    from astrolift_registry.models import Workload
    from astrolift_services.models import ManagedService

    _grant_delete(permission_resolver)

    # Seed the per-resource fixtures the preview should surface.
    Workload.objects.create(registered_app=app, name="api", slug="api")
    Workload.objects.create(registered_app=app, name="worker", slug="worker")
    ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind="postgres",
        name="primary-db",
        variant="rds",
        status=ManagedService.Status.ACTIVE,
    )
    DeployToken.objects.create(
        registered_app=app,
        name="ci-runner",
        token_hash="abc123",
        token_last_4="cafe",
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
    app.source_repo = "acme/api"
    app.source_webhook_id = "4242"
    app.registry_repo_uri = "111.dkr.ecr.us-east-1.amazonaws.com/acme/api"
    app.save(
        update_fields=[
            "source_repo",
            "source_webhook_id",
            "registry_repo_uri",
            "updated_at",
            "version",
        ],
    )

    query = LifecycleQuery()
    with _tenant_for(org, actor):
        preview = query.preview_astrolift_deregister(
            info=fake_info,
            app_slug=app.slug,
        )
    assert preview is not None
    assert preview.app_slug == app.slug
    assert preview.app_name == app.name
    # k8s: 1 namespace per env + (Deployment+Service+Ingress+CronJob+Secret) ×
    # (2 workload names + bare-slug fallback) per env = 1 + 5*3 = 16 per env.
    assert len(preview.k8s_objects) == 16
    assert any(o.kind == "Namespace" for o in preview.k8s_objects)
    assert any(o.name == "hello-app-api" for o in preview.k8s_objects)
    assert any(o.name == "hello-app" for o in preview.k8s_objects)  # bare slug
    # Managed services / secret refs / deploy tokens populated.
    assert len(preview.managed_services) == 1
    assert preview.managed_services[0].kind == "postgres"
    assert preview.managed_services[0].variant == "rds"
    assert len(preview.secret_refs) == 1
    assert preview.secret_refs[0].bundle_slug == "api-keys"
    assert len(preview.deploy_tokens) == 1
    assert preview.deploy_tokens[0].last4 == "cafe"
    assert preview.source_webhook is not None
    assert preview.source_webhook.installed is True
    assert preview.source_webhook.hook_id == "4242"
    assert preview.registry_repo_uri.endswith("/acme/api")
    # Count includes k8s + managed services + secret refs + tokens +
    # installed source webhook + registry repo URI.
    assert preview.total_resource_count == (
        16 + 1 + 1 + 1 + 0 + 1 + 1  # identity_roles is 0 (no driver in tests)
    )


def test_preview_deregister_requires_delete_permission(
    app,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Operators without ``app.delete`` can't see the blast-radius list
    — same permission gate as the deregister mutation itself."""
    from astrolift_lifecycle.schema.queries import LifecycleQuery
    from core.permissions import PermissionDenied

    # No grant.
    query = LifecycleQuery()
    with _tenant_for(org, actor):
        with pytest.raises(PermissionDenied):
            query.preview_astrolift_deregister(
                info=fake_info,
                app_slug=app.slug,
            )


def test_preview_deregister_unknown_app_returns_none(
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Lookup miss returns None (FE renders empty state) rather than
    raising; tenant scoping keeps this from leaking row counts."""
    from astrolift_lifecycle.schema.queries import LifecycleQuery

    _grant_delete(permission_resolver)
    query = LifecycleQuery()
    with _tenant_for(org, actor):
        preview = query.preview_astrolift_deregister(
            info=fake_info,
            app_slug="ghost-app",
        )
    assert preview is None


# ---------------------------------------------------------------------------
# #436 B — grace-period cancel mutation + workflow signal handler
# ---------------------------------------------------------------------------


def _grant_delete_and_call_cancel(
    *,
    permission_resolver,
    fake_info,
    org,
    actor,
    workflow_id: str,
):
    from astrolift_lifecycle.schema.mutations import (
        CancelDeregisterInput,
        LifecycleMutation,
    )

    _grant_delete(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        return mutation.cancel_astrolift_deregister(
            info=fake_info,
            input=CancelDeregisterInput(
                workflow_id=workflow_id,
                reason="changed-my-mind",
            ),
        )


def test_cancel_deregister_sends_signal_to_workflow(
    app,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """Happy path: the cancel mutation sends ``cancel_teardown`` to the
    deterministic workflow id and returns ``signal_delivered=True``."""
    wf_id = f"DeregisterAppWorkflow-{app.guid}"
    result = _grant_delete_and_call_cancel(
        permission_resolver=permission_resolver,
        fake_info=fake_info,
        org=org,
        actor=actor,
        workflow_id=wf_id,
    )
    assert result.ok is True, result.errors
    assert result.data.workflow_id == wf_id
    assert result.data.signal_delivered is True
    # Signal landed on the right workflow with the right name.
    assert len(temporal_recorder.signals) == 1
    sig_id, sig_name, sig_args = temporal_recorder.signals[0]
    assert sig_id == wf_id
    assert sig_name == "cancel_teardown"
    assert sig_args == ("changed-my-mind",)


def test_cancel_deregister_rejects_non_deregister_workflow_id(
    app,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """The cancel surface only accepts ``DeregisterAppWorkflow-*`` ids —
    pointing it at a sibling workflow is a validation error."""
    result = _grant_delete_and_call_cancel(
        permission_resolver=permission_resolver,
        fake_info=fake_info,
        org=org,
        actor=actor,
        workflow_id="DeployAppWorkflow-some-other-guid",
    )
    assert result.ok is False
    assert any(e.code == ErrorCode.VALIDATION.value for e in result.errors)
    assert temporal_recorder.signals == []


def test_cancel_deregister_requires_app_delete_permission(
    app,
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """No ``app.delete`` → denied at resolver entry, no signal sent."""
    from astrolift_lifecycle.schema.mutations import (
        CancelDeregisterInput,
        LifecycleMutation,
    )

    # No grant.
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.cancel_astrolift_deregister(
            info=fake_info,
            input=CancelDeregisterInput(
                workflow_id=f"DeregisterAppWorkflow-{app.guid}",
            ),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PERMISSION_DENIED.value for e in result.errors)
    assert temporal_recorder.signals == []


def test_cancel_deregister_unknown_app_guid_returns_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
    temporal_recorder,
):
    """A workflow id pointing at an app this tenant can't see returns
    NOT_FOUND rather than firing a signal — keeps cross-org cancellation
    out of reach."""
    result = _grant_delete_and_call_cancel(
        permission_resolver=permission_resolver,
        fake_info=fake_info,
        org=org,
        actor=actor,
        workflow_id="DeregisterAppWorkflow-00000000-0000-0000-0000-000000000000",
    )
    assert result.ok is False
    assert any(e.code == ErrorCode.NOT_FOUND.value for e in result.errors)
    assert temporal_recorder.signals == []


# --- Workflow signal handler ----------------------------------------


class _CancellableWorkflowAPI(_FakeWorkflowAPI):
    """_FakeWorkflowAPI + a wait_condition that honours the signal flag.

    The grace-period cancel boundary is the workflow's first
    ``wait_condition`` call. For the cancel-on-time test we set the
    flag before calling ``run`` so the predicate resolves immediately;
    for the timeout test we leave the flag false and raise
    TimeoutError to mirror the runtime behaviour."""

    def __init__(self, *, fakes, on_wait):
        super().__init__(fakes=fakes)
        self._on_wait = on_wait

    async def wait_condition(self, predicate, *, timeout=None):
        # Honour the predicate first — operator-on-time signals come
        # in before the workflow even hits the wait. Then run the
        # injected hook (which the test uses to inject a signal or to
        # raise TimeoutError).
        if predicate():
            return
        await self._on_wait(predicate)


def test_workflow_grace_period_cancel_short_circuits_before_teardown(app):
    """Cancel signal received within the grace window → the workflow
    returns ``cancelled=True`` and NONE of the destructive activities
    run. The app row is not soft-deleted."""
    import asyncio

    from astrolift_workflows.workflows.deregister_app import DeregisterAppWorkflow

    # Activities that MUST NOT run during a within-window cancel.
    fakes = {
        "mark_app_tearing_down": lambda *_: None,
        "delete_app_namespaces": RuntimeError("should not delete on cancel"),
        "list_app_managed_service_ids": RuntimeError("should not enumerate"),
        "deprovision_managed_service": RuntimeError("should not deprovision"),
        "deprovision_app_registry_repo": RuntimeError("should not archive repo"),
        "deprovision_app_identity_role": RuntimeError("should not delete role"),
        "list_app_secret_targets": RuntimeError("should not list secrets"),
        "delete_secret_from_cluster": RuntimeError("should not delete secret"),
        "revoke_app_secret_bundle_refs": RuntimeError("should not revoke refs"),
        "delete_app_source_webhook": RuntimeError("should not delete webhook"),
        "revoke_app_deploy_tokens": RuntimeError("should not revoke tokens"),
        "soft_delete_app_records": RuntimeError("should not soft-delete"),
        "mark_app_deregistered": RuntimeError("should not mark deregistered"),
    }
    wf = DeregisterAppWorkflow()

    async def _signal_then_wait(predicate):
        # Simulate the operator clicking Cancel during the grace window.
        wf.cancel_teardown("changed-my-mind")
        # Predicate now returns True — wait_condition returns.

    fake_api = _CancellableWorkflowAPI(fakes=fakes, on_wait=_signal_then_wait)
    input = DeregisterWfInput(
        registered_app_id=app.pk,
        actor=Actor(kind="user", user_id=None, display="tester"),
        delete_data=True,
        force_destroy=True,
    )
    with patch(
        "astrolift_workflows.workflows.deregister_app.workflow",
        SimpleNamespace(
            execute_activity=fake_api.execute_activity,
            wait_condition=fake_api.wait_condition,
            logger=SimpleNamespace(
                warning=lambda *a, **k: None,
                info=lambda *a, **k: None,
            ),
        ),
    ):
        result = asyncio.run(wf.run(input))
    assert result.ok is False
    assert result.data["cancelled"] is True
    assert result.data["cancel_reason"] == "changed-my-mind"
    # The destructive activities never ran — only ``mark_app_tearing_down``
    # executed before the wait_condition boundary.
    called = [name for name, _, _ in fake_api.calls]
    assert called == ["mark_app_tearing_down"]
    # The app row survives the cancelled teardown.
    app.refresh_from_db()
    assert app.deleted_at is None


def test_workflow_grace_period_elapses_proceeds_with_teardown(app):
    """No cancel signal arrives within the grace window → the
    wait_condition raises TimeoutError → the workflow proceeds to the
    destructive activities. Happy path soft-deletes the app row."""
    import asyncio

    from astrolift_workflows.workflows.deregister_app import DeregisterAppWorkflow

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
    wf = DeregisterAppWorkflow()

    async def _raise_timeout(predicate):
        raise TimeoutError("grace period elapsed")

    fake_api = _CancellableWorkflowAPI(fakes=fakes, on_wait=_raise_timeout)
    input = DeregisterWfInput(
        registered_app_id=app.pk,
        actor=Actor(kind="user", user_id=None, display="tester"),
        delete_data=True,
        force_destroy=True,
    )
    with patch(
        "astrolift_workflows.workflows.deregister_app.workflow",
        SimpleNamespace(
            execute_activity=fake_api.execute_activity,
            wait_condition=fake_api.wait_condition,
            logger=SimpleNamespace(
                warning=lambda *a, **k: None,
                info=lambda *a, **k: None,
            ),
        ),
    ):
        result = asyncio.run(wf.run(input))
    assert result.ok is True
    # ``cancelled`` flag absent (or False) → proceeded with teardown.
    assert result.data.get("cancelled") is not True
    # Soft-delete activity ran.
    assert "soft_delete_app_records" in {name for name, _, _ in fake_api.calls}


# ---------------------------------------------------------------------------
# #436 D — force-redeploy in-flight deployment preview
# ---------------------------------------------------------------------------


def test_preview_force_redeploy_returns_in_flight_deploys(
    app,
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Preview returns the same in-flight rows the force-redeploy
    recovery path would transition to FAILED."""
    from astrolift_lifecycle.models import Deployment
    from astrolift_lifecycle.schema.queries import LifecycleQuery
    from astrolift_workflows.activities.force_redeploy import _IN_FLIGHT_STATUSES

    # One in-flight deploy + one terminal deploy. Only the in-flight
    # row should land in the preview.
    in_flight = Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.DEPLOYING,
        image_tag="v1.2.3",
        ci_actor_kind="github-action",
        ci_run_url="https://github.com/acme/api/actions/runs/42",
    )
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.RUNNING,
        image_tag="v1.2.2",
    )
    assert Deployment.Status.DEPLOYING.value in _IN_FLIGHT_STATUSES

    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_UPDATE)
    query = LifecycleQuery()
    with _tenant_for(org, actor):
        preview = query.preview_astrolift_force_redeploy(
            info=fake_info,
            app_slug=app.slug,
        )
    assert preview is not None
    assert preview.app_slug == app.slug
    assert len(preview.in_flight_deployments) == 1
    row = preview.in_flight_deployments[0]
    assert row.id == str(in_flight.guid)
    assert row.image_tag == "v1.2.3"
    assert row.status == "deploying"
    assert row.environment_name == env.name
    # Triggered-by display falls back to the CI actor kind when there's
    # no user FK (CI-bot deploy).
    assert row.triggered_by_display == "ci:github-action"


def test_preview_force_redeploy_environment_scope(
    app,
    env,
    env_requires_approval,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """``environmentName`` scopes the preview list to one env's deploys."""
    from astrolift_lifecycle.models import Deployment
    from astrolift_lifecycle.schema.queries import LifecycleQuery

    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.DEPLOYING,
        image_tag="prod-1",
    )
    Deployment.objects.create(
        registered_app=app,
        app_environment=env_requires_approval,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.PENDING,
        image_tag="staging-1",
    )

    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_UPDATE)
    query = LifecycleQuery()
    with _tenant_for(org, actor):
        preview = query.preview_astrolift_force_redeploy(
            info=fake_info,
            app_slug=app.slug,
            environment_name=env.name,
        )
    assert preview is not None
    assert preview.environment_name == env.name
    assert {r.image_tag for r in preview.in_flight_deployments} == {"prod-1"}


def test_preview_force_redeploy_requires_both_permissions(
    app,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """The mutation needs ``app.deploy`` AND ``app.update``; the preview
    surface enforces the same gate so a user who can't trigger the
    destructive action can't browse the preview either."""
    from astrolift_lifecycle.schema.queries import LifecycleQuery
    from core.permissions import PermissionDenied

    # Grant only one of the two — preview must still deny.
    permission_resolver.grant(Permission.APP_DEPLOY)
    query = LifecycleQuery()
    with _tenant_for(org, actor):
        with pytest.raises(PermissionDenied):
            query.preview_astrolift_force_redeploy(
                info=fake_info,
                app_slug=app.slug,
            )
