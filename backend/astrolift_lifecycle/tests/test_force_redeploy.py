"""Tests for ``forceAstroliftRedeploy`` (#389).

Covers the recovery mutation end-to-end:

  * Happy path — cancels in-flight deploys, deletes k8s objects via
    the cluster driver, dispatches the CI workflow.
  * Confirm-slug mismatch → VALIDATION on ``confirmSlug``.
  * Missing ``app.deploy`` permission → PERMISSION_DENIED.
  * Missing ``app.update`` permission (with ``app.deploy`` granted) →
    PERMISSION_DENIED.
  * Audit event carries the cancellation + delete counts.
  * Workflow dispatch failure surfaces a partial-success envelope —
    the cancellation + delete counts are still reported even when the
    CI dispatch can't reach the host.

The cluster driver is replaced with an in-process fake so the test
exercises the resolver + activity wiring against real Postgres rows
without standing up a kube-apiserver.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_lifecycle.schema.mutations import (
    ForceRedeployInput,
    LifecycleMutation,
)
from astrolift_registry.models import Workload
from astrolift_scm.models import SourceConnection
from astrolift_scm.services.workflows import WorkflowDispatchResult
from core.mutations import AuditEntry, register_audit_writer
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Local fixtures + helpers
# ---------------------------------------------------------------------------


def _info(user=None):
    request = SimpleNamespace(user=user)
    context = SimpleNamespace(request=request, user=user)
    return SimpleNamespace(context=context)


def _ctx(org):
    return tenant_context(TenantContext(organization_id=org.id))


@dataclasses.dataclass
class _FakeDeleteResult:
    """Stand-in for ``ApplyResult`` / ``DeleteResult`` — only the
    ``errors`` attribute is read on the delete path."""

    errors: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _FakeDriver:
    """Captures every ``delete_manifests`` call so the test can assert
    on the stubs the resolver fed in. Returns a ``DeleteResult`` with
    no errors by default; tests opt in to errors by mutating
    ``errors_per_call``."""

    captured: list[tuple[str, str, list[dict[str, Any]]]] = dataclasses.field(default_factory=list)
    errors_per_call: list[list[str]] = dataclasses.field(default_factory=list)

    def delete_manifests(self, cluster, namespace, manifests):
        self.captured.append((cluster, namespace, list(manifests)))
        if self.errors_per_call:
            return _FakeDeleteResult(errors=self.errors_per_call.pop(0))
        return _FakeDeleteResult(errors=[])


@dataclasses.dataclass
class _FakeContext:
    slug: str


@pytest.fixture
def app_with_repo(app):
    """Configure the base app fixture with a source repo so the CI
    re-dispatch step has something to call."""
    app.source_kind = "github"
    app.source_repo = "acme/api"
    app.default_branch = "main"
    app.deploy_branch = "main"
    app.save(
        update_fields=[
            "source_kind",
            "source_repo",
            "default_branch",
            "deploy_branch",
            "updated_at",
            "version",
        ]
    )
    return app


@pytest.fixture
def github_connection(org):
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


@pytest.fixture
def fake_driver(monkeypatch):
    """Replace the cluster-driver resolver + dispatcher so the test
    doesn't need a real k8s API or GitHub. Yields the driver so tests
    can assert on captured manifests."""
    driver = _FakeDriver()

    def _resolve_driver(_cluster):
        return driver

    def _resolve_ctx(cluster):
        return _FakeContext(slug=cluster.slug)

    monkeypatch.setattr(
        "astrolift_workflows.activities.force_redeploy._driver_for_cluster",
        _resolve_driver,
    )
    monkeypatch.setattr(
        "astrolift_workflows.activities.force_redeploy._context_for_cluster",
        _resolve_ctx,
    )
    return driver


@pytest.fixture
def audit_recorder(monkeypatch):
    """Capture every ``emit_audit`` call so tests can assert the audit
    row's action + extras payload."""
    entries: list[AuditEntry] = []
    previous = None

    def _writer(entry: AuditEntry) -> None:
        entries.append(entry)

    from core.mutations import _audit_writer  # noqa: PLC0415

    previous = _audit_writer
    register_audit_writer(_writer)
    yield entries
    register_audit_writer(previous)


@pytest.fixture
def _stub_dispatch_ok(monkeypatch):
    """The default CI-dispatch stub — pretend the host accepted the
    workflow dispatch and returned the runs-page URL."""

    def _ok(_app, branch=None, workflow_path=None):
        return WorkflowDispatchResult(
            ok=True,
            run_url="https://github.com/acme/api/actions/workflows/astrolift-ci.yml",
        )

    monkeypatch.setattr(
        "astrolift_workflows.activities.force_redeploy.dispatch_astrolift_ci_workflow",
        _ok,
    )


@pytest.fixture
def _stub_dispatch_fail(monkeypatch):
    """CI-dispatch fails with an API-error envelope — the partial-
    success branch of the resolver."""

    def _fail(_app, branch=None, workflow_path=None):
        return WorkflowDispatchResult(
            ok=False,
            error_code="API_ERROR",
            error_message="GitHub returned 500: upstream broken",
        )

    monkeypatch.setattr(
        "astrolift_workflows.activities.force_redeploy.dispatch_astrolift_ci_workflow",
        _fail,
    )


def _create_workload(app, slug: str) -> Workload:
    return Workload.objects.create(
        registered_app=app,
        name=slug.title(),
        slug=slug,
        kind=Workload.Kind.DEPLOYMENT,
    )


def _create_in_flight_deploy(app, env, status: str) -> Deployment:
    """Build a Deployment row directly in the requested status —
    bypasses the state machine because the only path into PENDING /
    DEPLOYING / REDEPLOYING is through the workflow runtime."""
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=status,
        image_tag="v1",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_force_redeploy_cancels_deploys_deletes_objects_and_dispatches(
    permission_resolver,
    org,
    app_with_repo,
    env,
    github_connection,
    fake_driver,
    audit_recorder,
    _stub_dispatch_ok,
):
    """Full happy path:

    * Two in-flight deploys (PENDING_APPROVAL, DEPLOYING) → both
      transition to FAILED.
    * Two workloads (api, worker) + bare-slug fallback → driver
      receives stubs for Deployment / Service / Ingress / CronJob
      per name, on the env's cluster.
    * CI dispatch is fired, run_url surfaces in the payload.
    * Audit entry carries the cancellation + delete counts."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_UPDATE)

    _create_workload(app_with_repo, "api")
    _create_workload(app_with_repo, "worker")

    d1 = _create_in_flight_deploy(app_with_repo, env, Deployment.Status.PENDING_APPROVAL.value)
    d2 = _create_in_flight_deploy(app_with_repo, env, Deployment.Status.DEPLOYING.value)
    # Terminal row that should NOT be touched.
    d_terminal = Deployment.objects.create(
        registered_app=app_with_repo,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.FAILED.value,
        image_tag="v0",
    )

    with _ctx(org):
        result = LifecycleMutation().force_astrolift_redeploy(
            _info(),
            input=ForceRedeployInput(
                app_slug=app_with_repo.slug,
                confirm_slug=app_with_repo.slug,
            ),
        )

    assert result.ok, result.errors
    payload = result.data
    assert payload.deployments_cancelled == 2
    # 3 names (api, worker, bare slug) × 4 kinds × 1 env = 12 stubs
    # accepted (no driver errors → all count as deleted).
    assert payload.k8s_objects_deleted == 12
    assert payload.workflow_dispatched is True
    assert payload.run_url == "https://github.com/acme/api/actions/workflows/astrolift-ci.yml"
    assert payload.dispatch_message is None

    # In-flight rows are now FAILED; terminal row is untouched.
    d1.refresh_from_db()
    d2.refresh_from_db()
    d_terminal.refresh_from_db()
    assert d1.status == Deployment.Status.FAILED.value
    assert d2.status == Deployment.Status.FAILED.value
    assert d_terminal.status == Deployment.Status.FAILED.value
    # The driver got one delete_manifests call for the one env.
    assert len(fake_driver.captured) == 1
    cluster_slug, namespace, manifests = fake_driver.captured[0]
    assert cluster_slug == env.tenant_cluster.slug
    # Namespace = <org-slug>-<app-slug> when k8s_namespace is unset.
    assert namespace == f"{org.slug}-{app_with_repo.slug}"
    # Every stub points at the right namespace + kind set.
    kinds = {m["kind"] for m in manifests}
    assert kinds == {"Deployment", "Service", "Ingress", "CronJob"}
    names = {m["metadata"]["name"] for m in manifests}
    assert names == {
        f"{app_with_repo.slug}-api",
        f"{app_with_repo.slug}-worker",
        app_with_repo.slug,
    }

    # Audit row carries action + extras.
    force_entries = [e for e in audit_recorder if e.action == "app.force_redeploy"]
    assert len(force_entries) == 1
    entry = force_entries[0]
    assert entry.decision == "ALLOW"
    assert entry.extra == {
        "deployments_cancelled": 2,
        "k8s_objects_deleted": 12,
        "workflow_dispatched": True,
    }


# ---------------------------------------------------------------------------
# Confirm-slug mismatch
# ---------------------------------------------------------------------------


def test_force_redeploy_confirm_slug_mismatch_returns_validation(
    permission_resolver,
    org,
    app_with_repo,
    env,
    fake_driver,
    _stub_dispatch_ok,
):
    """A typo on the confirm-slug field bounces with VALIDATION before
    any side effects land. No k8s deletes; no deployments transitioned."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_UPDATE)

    d = _create_in_flight_deploy(app_with_repo, env, Deployment.Status.PENDING.value)

    with _ctx(org):
        result = LifecycleMutation().force_astrolift_redeploy(
            _info(),
            input=ForceRedeployInput(
                app_slug=app_with_repo.slug,
                confirm_slug="not-the-slug",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "confirmSlug"
    # No side effects — driver never called, deployment status unchanged.
    assert fake_driver.captured == []
    d.refresh_from_db()
    assert d.status == Deployment.Status.PENDING.value


# ---------------------------------------------------------------------------
# Permission gates — both perms required
# ---------------------------------------------------------------------------


def test_force_redeploy_without_app_deploy_is_permission_denied(
    permission_resolver,
    org,
    app_with_repo,
    env,
    fake_driver,
    _stub_dispatch_ok,
):
    """Missing ``app.deploy`` → PERMISSION_DENIED, even when
    ``app.update`` is held."""
    permission_resolver.grant(Permission.APP_UPDATE)

    d = _create_in_flight_deploy(app_with_repo, env, Deployment.Status.PENDING.value)

    with _ctx(org):
        result = LifecycleMutation().force_astrolift_redeploy(
            _info(),
            input=ForceRedeployInput(
                app_slug=app_with_repo.slug,
                confirm_slug=app_with_repo.slug,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert fake_driver.captured == []
    d.refresh_from_db()
    assert d.status == Deployment.Status.PENDING.value


def test_force_redeploy_without_app_update_is_permission_denied(
    permission_resolver,
    org,
    app_with_repo,
    env,
    fake_driver,
    _stub_dispatch_ok,
):
    """Missing ``app.update`` → PERMISSION_DENIED, even when
    ``app.deploy`` is held. Confirms the stacked-perm gate fires both
    sides."""
    permission_resolver.grant(Permission.APP_DEPLOY)

    d = _create_in_flight_deploy(app_with_repo, env, Deployment.Status.PENDING.value)

    with _ctx(org):
        result = LifecycleMutation().force_astrolift_redeploy(
            _info(),
            input=ForceRedeployInput(
                app_slug=app_with_repo.slug,
                confirm_slug=app_with_repo.slug,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "PERMISSION_DENIED"
    assert fake_driver.captured == []
    d.refresh_from_db()
    assert d.status == Deployment.Status.PENDING.value


# ---------------------------------------------------------------------------
# Dispatch failure → partial success
# ---------------------------------------------------------------------------


def test_force_redeploy_dispatch_failure_returns_partial_success(
    permission_resolver,
    org,
    app_with_repo,
    env,
    github_connection,
    fake_driver,
    audit_recorder,
    _stub_dispatch_fail,
):
    """When the CI dispatch fails, the resolver still surfaces a
    success envelope (the cancellation + delete steps actually ran)
    with ``workflowDispatched=False`` and the failure message. The
    audit row reflects the same state."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_UPDATE)

    _create_workload(app_with_repo, "api")
    d1 = _create_in_flight_deploy(app_with_repo, env, Deployment.Status.PENDING.value)

    with _ctx(org):
        result = LifecycleMutation().force_astrolift_redeploy(
            _info(),
            input=ForceRedeployInput(
                app_slug=app_with_repo.slug,
                confirm_slug=app_with_repo.slug,
            ),
        )

    assert result.ok, result.errors
    payload = result.data
    assert payload.deployments_cancelled == 1
    # 2 names (api, bare slug) × 4 kinds = 8 stubs.
    assert payload.k8s_objects_deleted == 8
    assert payload.workflow_dispatched is False
    assert payload.run_url is None
    assert payload.dispatch_message is not None
    assert "upstream broken" in payload.dispatch_message

    d1.refresh_from_db()
    assert d1.status == Deployment.Status.FAILED.value

    # Audit row carries the partial-success counts.
    force_entries = [e for e in audit_recorder if e.action == "app.force_redeploy"]
    assert len(force_entries) == 1
    entry = force_entries[0]
    assert entry.extra == {
        "deployments_cancelled": 1,
        "k8s_objects_deleted": 8,
        "workflow_dispatched": False,
    }


# ---------------------------------------------------------------------------
# Environment scoping
# ---------------------------------------------------------------------------


def test_force_redeploy_environment_name_scopes_to_one_env(
    permission_resolver,
    org,
    app_with_repo,
    env,
    env_requires_approval,
    fake_driver,
    _stub_dispatch_ok,
):
    """Passing ``environment_name`` restricts cancellation + delete to
    that env. Deploys on the other env are left alone, and the driver
    only sees a single delete_manifests call (for the targeted env)."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_UPDATE)

    d_in_scope = _create_in_flight_deploy(
        app_with_repo,
        env,
        Deployment.Status.PENDING.value,
    )
    d_out_of_scope = _create_in_flight_deploy(
        app_with_repo,
        env_requires_approval,
        Deployment.Status.PENDING.value,
    )

    with _ctx(org):
        result = LifecycleMutation().force_astrolift_redeploy(
            _info(),
            input=ForceRedeployInput(
                app_slug=app_with_repo.slug,
                environment_name=env.name,
                confirm_slug=app_with_repo.slug,
            ),
        )

    assert result.ok, result.errors
    assert result.data.deployments_cancelled == 1

    d_in_scope.refresh_from_db()
    d_out_of_scope.refresh_from_db()
    assert d_in_scope.status == Deployment.Status.FAILED.value
    assert d_out_of_scope.status == Deployment.Status.PENDING.value

    assert len(fake_driver.captured) == 1
    cluster_slug, _, _ = fake_driver.captured[0]
    assert cluster_slug == env.tenant_cluster.slug


def test_force_redeploy_unknown_environment_returns_not_found(
    permission_resolver,
    org,
    app_with_repo,
    env,
    fake_driver,
    _stub_dispatch_ok,
):
    """An ``environment_name`` that doesn't exist on the app → NOT_FOUND
    with the field hint. No side effects."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = LifecycleMutation().force_astrolift_redeploy(
            _info(),
            input=ForceRedeployInput(
                app_slug=app_with_repo.slug,
                environment_name="does-not-exist",
                confirm_slug=app_with_repo.slug,
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "environmentName"
    assert fake_driver.captured == []


# ---------------------------------------------------------------------------
# Lookup miss
# ---------------------------------------------------------------------------


def test_force_redeploy_unknown_app_returns_not_found(permission_resolver, org):
    """Slug that doesn't match any active app → NOT_FOUND."""
    permission_resolver.grant(Permission.APP_DEPLOY)
    permission_resolver.grant(Permission.APP_UPDATE)

    with _ctx(org):
        result = LifecycleMutation().force_astrolift_redeploy(
            _info(),
            input=ForceRedeployInput(
                app_slug="no-such-app",
                confirm_slug="no-such-app",
            ),
        )

    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"
    assert result.errors[0].field == "appSlug"
