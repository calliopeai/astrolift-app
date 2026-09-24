"""Literal app secrets (setAppSecret) reach a deployed workload (#1758).

Before this fix, ``core.app_deploy.render_resources_for_deployment`` built
envFrom from ``AppSecretBundleRef`` + the managed-service bindings Secret
only. Nothing rendered the app-wide ``[env]`` literals a
set/rotate/delete/bulk-import mutation writes to
``RegisteredApp.manifest_raw_staged``, so a value set via ``setAppSecret``
never reached a pod's environment even after a redeploy.
"""

from __future__ import annotations

import base64
import itertools
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_lifecycle.models.preview_environment import PreviewEnvironment
from astrolift_manifest.env_edit import delete_app_env_key, set_app_env_keys
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.mutations import RegistryMutation, UpdateManifestInput
from astrolift_services.models import (
    AppSecretBundleRef,
    AppSecretMetadata,
    ManagedService,
    SecretBundle,
    SecretChangeProposal,
)
from astrolift_services.schema.mutations import ApproveSecretChangeInput, ServicesMutation
from astrolift_services.schema.mutations.types import (
    BulkImportAppSecretsInput,
    DeleteAppSecretInput,
    RotateAppSecretInput,
    SetAppSecretInput,
    SetAppSecretMetadataInput,
)
from astrolift_services.secret_change_apply import apply_proposal
from astrolift_services.secret_literals import base_raw_digest
from astrolift_workflows.activities.app_lifecycle import (
    _app_env_secret_name,
    _bindings_secret_name,
    _update_secrets_sync,
)
from core.app_deploy import render_resources_for_deployment
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db

_MANIFEST = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true
"""


def _info(user=None):
    request = SimpleNamespace(user=user, META={})
    return SimpleNamespace(context=SimpleNamespace(user=user, request=request))


def _ctx(app):
    return _tenant_ctx(TenantContext(organization_id=app.organization_id))


def _seed_manifest(app) -> None:
    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])


def _deployment(app, env) -> Deployment:
    if not app.manifest_raw:
        _seed_manifest(app)
    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )


def _set_app_secret(app, *, key: str, value: str, scope: str = "all"):
    with _ctx(app):
        return ServicesMutation().set_app_secret(
            _info(),
            input=SetAppSecretInput(app_slug=app.slug, key=key, value=value, scope=scope),
        )


class _FakeClusterDriver:
    """Records what update_secrets would apply to the cluster."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.applied: list[dict] = []

    def ensure_namespace(self, cluster_slug, namespace, labels, annotations):
        self.calls.append(("ensure_namespace", namespace))

    def apply_manifests(self, cluster_slug, namespace, manifests):
        from providers._sdk.cluster import ApplyResult

        self.calls.append(("apply_manifests", namespace, [m["metadata"]["name"] for m in manifests]))
        self.applied = manifests
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])


def _literal_secrets(manifests: list[dict]) -> list[dict]:
    return [m for m in manifests if "astrolift.io/app-env-secrets" in m["metadata"].get("labels", {})]


def _decoded(secret: dict) -> dict[str, str]:
    return {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}


def _materialized_literal_secret(deployment, monkeypatch) -> dict | None:
    """Run update_secrets against a fake cluster and return the literal
    Secret it would apply, or None when there is none."""
    driver = _FakeClusterDriver()
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda d: (driver, SimpleNamespace(slug="test-cluster"), "acme-hello-app"),
    )
    _update_secrets_sync(deployment.pk)
    secrets = _literal_secrets(driver.applied)
    assert len(secrets) <= 1
    return secrets[0] if secrets else None


def _materialize(deployment, monkeypatch) -> dict[str, str]:
    """The literal Secret's decoded data, or {} when it wasn't materialized."""
    secret = _materialized_literal_secret(deployment, monkeypatch)
    return _decoded(secret) if secret is not None else {}


def _env_from_list(resources: list[dict]) -> list[str]:
    deployment = next(r for r in resources if r["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return [ref["secretRef"]["name"] for ref in container.get("envFrom", [])]


def _env_from_names(resources: list[dict]) -> set[str]:
    deployment = next(r for r in resources if r["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {ref["secretRef"]["name"] for ref in container.get("envFrom", [])}


def test_set_app_secret_appears_in_env_from(permission_resolver, app, env):
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    result = _set_app_secret(app, key="API_KEY", value="shh")
    assert result.ok is True, result.errors
    app.refresh_from_db()

    resources = render_resources_for_deployment(_deployment(app, env))
    assert _app_env_secret_name(app.slug, env.name) in _env_from_names(resources)


def test_no_literal_secrets_omits_the_secret_from_env_from(app, env):
    resources = render_resources_for_deployment(_deployment(app, env))
    assert _env_from_names(resources) == set()


def test_preview_scoped_secret_is_excluded_from_a_non_preview_env(permission_resolver, app, env):
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    result = _set_app_secret(app, key="PREVIEW_ONLY", value="x", scope="preview")
    assert result.ok is True, result.errors
    app.refresh_from_db()

    # `env` (fixture) is a plain, non-preview environment -- a preview-scoped
    # secret must not leak into its envFrom.
    resources = render_resources_for_deployment(_deployment(app, env))
    assert _env_from_names(resources) == set()


def test_update_secrets_sync_materializes_the_literal_value(permission_resolver, app, env, monkeypatch):
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    result = _set_app_secret(app, key="API_KEY", value="super-secret")
    assert result.ok is True, result.errors
    app.refresh_from_db()

    deployment = _deployment(app, env)

    class _ClusterDriver:
        def __init__(self):
            self.applied: list[dict] = []

        def apply_manifests(self, cluster_slug, namespace, manifests):
            from providers._sdk.cluster import ApplyResult

            self.applied = manifests
            return ApplyResult(created=[], updated=[], unchanged=[], errors=[])

    driver = _ClusterDriver()
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda d: (driver, SimpleNamespace(slug="test-cluster"), "acme-hello-app"),
    )

    assert _update_secrets_sync(deployment.pk) == 1
    assert len(driver.applied) == 1
    secret = driver.applied[0]
    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == _app_env_secret_name(app.slug, env.name)
    assert base64.b64decode(secret["data"]["API_KEY"]).decode() == "super-secret"


def test_repo_keys_a_secret_cannot_carry_are_skipped_not_applied(app, env, monkeypatch):
    """A hand-edited repo manifest reaches manifest_raw through sync with no
    key validation. A key the API server rejects in Secret.data would fail
    the whole update_secrets apply, so it is skipped (#1758 review, L1)."""
    app.manifest_raw = (
        _MANIFEST
        + "\n[env]\n"
        + '"HAS SPACE" = "rejected-by-the-api-server"\n'
        + '"CLÉ" = "also-rejected"\n'
        + "NESTED = { a = 1 }\n"
        + 'GOOD_KEY = "kept"\n'
    )
    app.save(update_fields=["manifest_raw"])

    assert _materialize(_deployment(app, env), monkeypatch) == {"GOOD_KEY": "kept"}


def _preview_environment(app, env, *, name: str, status: str, branch: str = "feat-x"):
    """An AppEnvironment backed by a PreviewEnvironment row, on the same
    cluster (and so the same app namespace) as ``env``."""
    preview_env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=env.tenant_cluster,
        name=name,
        url=f"https://{name}.hello.example.com",
        required_approvals=0,
    )
    PreviewEnvironment.objects.create(
        registered_app=app,
        branch=branch,
        is_manual=True,
        status=status,
        hostname=f"{name}.hello.example.com",
        namespace="acme-hello-app",
        app_environment=preview_env,
    )
    return preview_env


def test_each_environment_gets_its_own_literal_secret(permission_resolver, app, env, monkeypatch):
    """Prod and a preview of the same app share the app namespace on one
    cluster, and scope filtering gives them different key sets. With one
    app-wide Secret name, whichever materialized last overwrote the
    other's, and the preview's pods read production values on their next
    restart (#1758 review, M2)."""
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="SHARED", value="shared-value").ok
    assert _set_app_secret(app, key="PROD_ONLY", value="prod-value", scope="production").ok
    app.refresh_from_db()
    preview = _preview_environment(app, env, name="preview-feat-x", status=PreviewEnvironment.Status.RUNNING)

    prod_secret = _materialized_literal_secret(_deployment(app, env), monkeypatch)
    preview_secret = _materialized_literal_secret(_deployment(app, preview), monkeypatch)
    preview_env_from = _env_from_names(render_resources_for_deployment(_deployment(app, preview)))

    assert _decoded(prod_secret) == {"SHARED": "shared-value", "PROD_ONLY": "prod-value"}
    assert _decoded(preview_secret) == {"SHARED": "shared-value"}
    assert prod_secret["metadata"]["name"] != preview_secret["metadata"]["name"]
    assert prod_secret["metadata"]["name"] not in preview_env_from
    assert preview_secret["metadata"]["name"] in preview_env_from


# A preview must never receive production-scoped keys, whatever state its
# build is in. The deploy path used to recognise a preview only while its
# PreviewEnvironment row was BUILDING or RUNNING and not soft-deleted, so a
# FAILED or torn-down one fell through to the production scope set
# (#1758 review, H2).


def _set_shared_and_production_only(app) -> None:
    _seed_manifest(app)
    assert _set_app_secret(app, key="SHARED", value="shared-value").ok
    assert _set_app_secret(app, key="PROD_ONLY", value="prod-value", scope="production").ok
    app.refresh_from_db()


def test_running_preview_does_not_receive_production_scoped_keys(permission_resolver, app, env, monkeypatch):
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_shared_and_production_only(app)
    preview = _preview_environment(app, env, name="preview-running", status=PreviewEnvironment.Status.RUNNING)

    assert _materialize(_deployment(app, preview), monkeypatch) == {"SHARED": "shared-value"}
    assert _materialize(_deployment(app, env), monkeypatch) == {
        "SHARED": "shared-value",
        "PROD_ONLY": "prod-value",
    }


def test_failed_preview_does_not_receive_production_scoped_keys(permission_resolver, app, env, monkeypatch):
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_shared_and_production_only(app)
    preview = _preview_environment(app, env, name="preview-failed", status=PreviewEnvironment.Status.FAILED)

    assert _materialize(_deployment(app, preview), monkeypatch) == {"SHARED": "shared-value"}


def test_torn_down_preview_does_not_receive_production_scoped_keys(
    permission_resolver, app, env, monkeypatch
):
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_shared_and_production_only(app)
    preview = _preview_environment(app, env, name="preview-gone", status=PreviewEnvironment.Status.TORN_DOWN)
    PreviewEnvironment.all_objects.get(app_environment=preview).soft_delete()

    assert _materialize(_deployment(app, preview), monkeypatch) == {"SHARED": "shared-value"}


def test_preview_known_only_by_its_lineage_does_not_receive_production_scoped_keys(
    permission_resolver, app, env, monkeypatch
):
    permission_resolver.grant(Permission.APP_UPDATE)
    _set_shared_and_production_only(app)
    preview = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=env.tenant_cluster,
        name="preview-lineage",
        url="https://preview-lineage.hello.example.com",
        required_approvals=0,
        previewed_environment=env,
    )

    assert _materialize(_deployment(app, preview), monkeypatch) == {"SHARED": "shared-value"}


# With secret approval on (#488), set/rotate/delete create a proposal
# instead of writing the staged buffer. updateManifest and
# bulkImportAppSecrets write that buffer with no proposal at all, so once
# literals deploy, reading the buffer as-is let either one put a value in
# front of a workload without quorum (#1758 review, H1).

_usernames = itertools.count()


def _user():
    n = next(_usernames)
    return get_user_model().objects.create(username=f"user-{n}@test", email=f"user-{n}@test")


def _as(app, user):
    return _tenant_ctx(TenantContext(organization_id=app.organization_id, actor_user_id=user.id))


def _require_secret_approval(app) -> None:
    app.requires_secret_approval = True
    app.save(update_fields=["requires_secret_approval"])


def _propose(app, write):
    """Run a secret write as a fresh user; it must come back as a proposal."""
    proposer = _user()
    with _as(app, proposer):
        result = write(_info(proposer))
    assert result.ok, result.errors
    assert result.data.pending_proposal_id is not None
    return str(result.data.pending_proposal_id)


def _approve(app, proposal_id: str) -> None:
    approver = _user()
    with _as(app, approver):
        result = ServicesMutation().approve_secret_change(
            _info(approver), input=ApproveSecretChangeInput(proposal_id=proposal_id)
        )
    assert result.ok, result.errors
    assert SecretChangeProposal.objects.get(guid=proposal_id).status == SecretChangeProposal.Status.APPLIED
    app.refresh_from_db()


def _stage_via_update_manifest(app, text: str) -> None:
    with _ctx(app):
        result = RegistryMutation().update_manifest(
            _info(), input=UpdateManifestInput(id=str(app.guid), raw_manifest=text)
        )
    assert result.ok, result.errors
    app.refresh_from_db()


def test_under_approval_a_literal_deploys_once_its_proposal_is_applied(
    permission_resolver, app, env, monkeypatch
):
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    _seed_manifest(app)
    _require_secret_approval(app)

    proposal_id = _propose(
        app,
        lambda info: ServicesMutation().set_app_secret(
            info, input=SetAppSecretInput(app_slug=app.slug, key="API_KEY", value="approved-value")
        ),
    )
    app.refresh_from_db()
    assert _materialize(_deployment(app, env), monkeypatch) == {}

    _approve(app, proposal_id)
    assert _materialize(_deployment(app, env), monkeypatch) == {"API_KEY": "approved-value"}


def test_under_approval_bulk_import_does_not_reach_the_workload(permission_resolver, app, env, monkeypatch):
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    _require_secret_approval(app)

    with _ctx(app):
        result = ServicesMutation().bulk_import_app_secrets(
            _info(), input=BulkImportAppSecretsInput(app_slug=app.slug, dotenv_text="SNUCK_IN=value")
        )
    assert result.ok, result.errors
    app.refresh_from_db()
    assert "SNUCK_IN" in app.manifest_raw_staged

    deployment = _deployment(app, env)
    assert _materialize(deployment, monkeypatch) == {}
    assert _env_from_names(render_resources_for_deployment(deployment)) == set()


def test_under_approval_update_manifest_does_not_reach_the_workload(
    permission_resolver, app, env, monkeypatch
):
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    _require_secret_approval(app)

    _stage_via_update_manifest(app, set_app_env_keys(_MANIFEST, {"SNUCK_IN": "value"}))
    assert "SNUCK_IN" in app.manifest_raw_staged

    deployment = _deployment(app, env)
    assert _materialize(deployment, monkeypatch) == {}
    assert _env_from_names(render_resources_for_deployment(deployment)) == set()


def test_under_approval_an_unapproved_edit_after_an_approval_is_not_deployed(
    permission_resolver, app, env, monkeypatch
):
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    _seed_manifest(app)
    _require_secret_approval(app)
    _approve(
        app,
        _propose(
            app,
            lambda info: ServicesMutation().set_app_secret(
                info, input=SetAppSecretInput(app_slug=app.slug, key="API_KEY", value="approved-value")
            ),
        ),
    )

    _stage_via_update_manifest(app, set_app_env_keys(app.manifest_raw_staged, {"API_KEY": "tampered"}))

    assert _materialize(_deployment(app, env), monkeypatch) == {}


def test_under_approval_an_unapproved_removal_keeps_the_repo_value(
    permission_resolver, app, env, monkeypatch
):
    permission_resolver.grant(Permission.APP_UPDATE)
    app.manifest_raw = set_app_env_keys(_MANIFEST, {"API_KEY": "from-the-repo"})
    app.save(update_fields=["manifest_raw"])
    _require_secret_approval(app)

    without_key, removed = delete_app_env_key(app.manifest_raw, "API_KEY")
    assert removed
    _stage_via_update_manifest(app, without_key)

    assert _materialize(_deployment(app, env), monkeypatch) == {"API_KEY": "from-the-repo"}


def test_under_approval_an_approved_delete_removes_the_key(permission_resolver, app, env, monkeypatch):
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    app.manifest_raw = set_app_env_keys(_MANIFEST, {"API_KEY": "from-the-repo"})
    app.save(update_fields=["manifest_raw"])
    _require_secret_approval(app)

    _approve(
        app,
        _propose(
            app,
            lambda info: ServicesMutation().delete_app_secret(
                info, input=DeleteAppSecretInput(app_slug=app.slug, key="API_KEY")
            ),
        ),
    )

    assert _materialize(_deployment(app, env), monkeypatch) == {}


def test_under_approval_a_later_repo_change_beats_an_older_approval(
    permission_resolver, app, env, monkeypatch
):
    """An applied proposal only vouches for a change still pending in the
    staged buffer. Once the draft is pushed and synced the buffer clears,
    and a reviewed repo change to the same key must not be overridden by
    the old approval."""
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    _seed_manifest(app)
    _require_secret_approval(app)
    _approve(
        app,
        _propose(
            app,
            lambda info: ServicesMutation().set_app_secret(
                info, input=SetAppSecretInput(app_slug=app.slug, key="API_KEY", value="approved-value")
            ),
        ),
    )
    # The draft lands in the repo and syncs back, clearing the buffer ...
    app.manifest_raw = app.manifest_raw_staged
    app.manifest_raw_staged = ""
    app.save(update_fields=["manifest_raw", "manifest_raw_staged"])
    # ... and a later reviewed repo change moves the key on.
    app.manifest_raw = set_app_env_keys(app.manifest_raw, {"API_KEY": "from-the-repo"})
    app.save(update_fields=["manifest_raw"])

    assert _materialize(_deployment(app, env), monkeypatch) == {"API_KEY": "from-the-repo"}


# Pods take envFrom values when they start, and Kubernetes rolls a workload
# only when its pod template changes, so rotating a literal and redeploying
# the same image left the running pods on the old value (#1758 review, M3).

_DIGEST = "astrolift.io/app-env-secrets-digest"

_WITH_A_TASK = (
    _MANIFEST
    + """
[[workloads]]
name = "migrate"
kind = "task"

  [[workloads.containers]]
  name = "migrate"
  is_primary = true
"""
)


def _pod_template_annotations(resources: list[dict], kind: str) -> dict[str, str]:
    workload = next(r for r in resources if r["kind"] == kind)
    return workload["spec"]["template"].get("metadata", {}).get("annotations", {})


def _digest(app, env) -> str | None:
    resources = render_resources_for_deployment(_deployment(app, env))
    return _pod_template_annotations(resources, "Deployment").get(_DIGEST)


def test_rotating_a_literal_changes_the_pod_template(permission_resolver, app, env):
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="API_KEY", value="first-value").ok
    app.refresh_from_db()
    before = _digest(app, env)
    unchanged = _digest(app, env)

    with _ctx(app):
        rotated = ServicesMutation().rotate_app_secret(
            _info(), input=RotateAppSecretInput(app_slug=app.slug, key="API_KEY", value="second-value")
        )
    assert rotated.ok, rotated.errors
    app.refresh_from_db()
    after = _digest(app, env)

    assert before is not None
    assert unchanged == before
    assert after is not None and after != before


def test_the_pod_template_digest_is_keyed_with_the_platform_secret(permission_resolver, app, env, settings):
    """The annotation is readable by anyone who can read the pod spec; an
    unkeyed hash of a short value could be brute-forced from it."""
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="PIN", value="1234").ok
    app.refresh_from_db()
    with_platform_key = _digest(app, env)

    settings.SECRET_KEY = "a-different-platform-secret-key-for-this-test"

    assert with_platform_key is not None
    assert _digest(app, env) != with_platform_key


def test_the_digest_goes_on_rolling_workloads_only(permission_resolver, app, env):
    """A Job's pod template is immutable, so a digest that changes on
    rotation would fail its apply; its pods read the Secret when they
    start anyway."""
    permission_resolver.grant(Permission.APP_UPDATE)
    app.manifest_raw = _WITH_A_TASK
    app.save(update_fields=["manifest_raw"])
    assert _set_app_secret(app, key="API_KEY", value="v1").ok
    app.refresh_from_db()

    resources = render_resources_for_deployment(_deployment(app, env))

    assert _DIGEST in _pod_template_annotations(resources, "Deployment")
    assert _DIGEST not in _pod_template_annotations(resources, "Job")


def test_no_literal_secrets_adds_no_digest(app, env):
    assert _digest(app, env) is None


# A migration applied the workloads to the target cluster but never the
# Secrets their envFrom names, so the target's pods could not start
# (#1758 review, M1). The workflow-level ordering lives in
# astrolift_workflows/tests/test_migrate_app_secrets_1758.py.


def test_migration_puts_the_namespace_then_the_secrets_on_the_target(
    permission_resolver, app, env, monkeypatch
):
    from astrolift_workflows.activities.migration import _materialize_secrets_on_target_sync

    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="API_KEY", value="migrated-value").ok
    app.refresh_from_db()
    deployment = _deployment(app, env)
    target = _FakeClusterDriver()
    monkeypatch.setattr(
        "core.app_deploy.driver_for_target_cluster",
        lambda d, target_cluster_id: (target, SimpleNamespace(slug="target-cluster"), "acme-hello-app"),
    )

    def _source_cluster(d):
        raise AssertionError("migration secrets must be applied to the target, not the source cluster")

    monkeypatch.setattr("core.app_deploy.driver_for_deployment", _source_cluster)

    assert _materialize_secrets_on_target_sync(deployment.pk, 4242) == 1

    assert [call[0] for call in target.calls] == ["ensure_namespace", "apply_manifests"]
    assert target.calls[0] == ("ensure_namespace", "acme-hello-app")
    assert target.calls[1][2] == [_app_env_secret_name(app.slug, env.name)]
    assert _decoded(_literal_secrets(target.applied)[0]) == {"API_KEY": "migrated-value"}


def test_env_from_lists_literals_first_so_bundles_and_bindings_win(permission_resolver, app, env, team):
    """A later envFrom source wins a key collision in Kubernetes, and
    env_injection documents app literal < bundle < managed service, so the
    list order is the contract, not just membership."""
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="API_KEY", value="literal-value").ok
    app.refresh_from_db()
    bundle = SecretBundle.objects.create(
        organization=app.organization,
        team=team,
        slug="prod-bundle",
        name="Prod Bundle",
        backend_ref="vault:/acme/prod",
    )
    AppSecretBundleRef.objects.create(registered_app=app, app_environment=env, secret_bundle=bundle)
    ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.POSTGRES,
        name="orders",
        variant="postgres_flexible",
        status=ManagedService.Status.ACTIVE,
    )

    resources = render_resources_for_deployment(_deployment(app, env))

    assert _env_from_list(resources) == [
        _app_env_secret_name(app.slug, env.name),
        "prod-bundle",
        _bindings_secret_name(app.slug),
    ]


def test_a_deleted_literal_is_left_out_of_the_next_secret_apply(permission_resolver, app, env, monkeypatch):
    """The cluster only loses a deleted key if the applied Secret leaves it
    out: server-side apply under the platform's one field manager prunes
    fields that manager no longer sends. This pins the leaving-out half."""
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="KEEP_ME", value="keep").ok
    assert _set_app_secret(app, key="REMOVE_ME", value="gone").ok
    with _ctx(app):
        deleted = ServicesMutation().delete_app_secret(
            _info(), input=DeleteAppSecretInput(app_slug=app.slug, key="REMOVE_ME")
        )
    assert deleted.ok, deleted.errors
    app.refresh_from_db()

    assert _materialize(_deployment(app, env), monkeypatch) == {"KEEP_ME": "keep"}


# An approved write must leave the metadata a direct write would. The
# set/rotate proposals carried only key and value, and apply never wrote
# AppSecretMetadata, so an approved production-only key fell back to scope
# "all" and reached previews (#1758 re-review, 1).


def _approved(app, write) -> None:
    _approve(app, _propose(app, write))


def _approved_set(app, **fields) -> None:
    _approved(
        app,
        lambda info: ServicesMutation().set_app_secret(
            info, input=SetAppSecretInput(app_slug=app.slug, **fields)
        ),
    )


def _live_metadata(app, key: str):
    return AppSecretMetadata.objects.filter(registered_app=app, key=key, deleted_at__isnull=True)


@pytest.fixture
def approver_grants(permission_resolver):
    permission_resolver.grant(Permission.APP_UPDATE)
    permission_resolver.grant(Permission.SECRET_APPROVE)
    return permission_resolver


def test_under_approval_an_approved_production_secret_never_reaches_a_preview(
    approver_grants, app, env, monkeypatch
):
    _seed_manifest(app)
    _require_secret_approval(app)
    preview = _preview_environment(
        app, env, name="preview-approved", status=PreviewEnvironment.Status.RUNNING
    )

    _approved_set(app, key="PROD_ONLY", value="prod-value", scope="production")

    assert _materialize(_deployment(app, preview), monkeypatch) == {}
    assert _materialize(_deployment(app, env), monkeypatch) == {"PROD_ONLY": "prod-value"}


def test_under_approval_a_rotation_without_a_scope_keeps_the_approved_scope(
    approver_grants, app, env, monkeypatch
):
    _seed_manifest(app)
    _require_secret_approval(app)
    preview = _preview_environment(app, env, name="preview-rotated", status=PreviewEnvironment.Status.RUNNING)
    _approved_set(app, key="PROD_ONLY", value="prod-value", scope="production")

    _approved(
        app,
        lambda info: ServicesMutation().rotate_app_secret(
            info, input=RotateAppSecretInput(app_slug=app.slug, key="PROD_ONLY", value="rotated")
        ),
    )

    assert _materialize(_deployment(app, preview), monkeypatch) == {}
    assert _materialize(_deployment(app, env), monkeypatch) == {"PROD_ONLY": "rotated"}


def test_under_approval_expiry_and_source_travel_with_the_proposal(approver_grants, app, env):
    _seed_manifest(app)
    _require_secret_approval(app)
    expires = timezone.now() + timedelta(days=30)

    _approved_set(app, key="API_KEY", value="v", expires_at=expires, set_via="cli")

    row = _live_metadata(app, "API_KEY").get()
    assert (row.expires_at, row.source, row.scope) == (expires, "cli", "all")


def test_the_proposal_diff_shows_the_scope_it_asks_to_approve(approver_grants, app, env):
    _seed_manifest(app)
    _require_secret_approval(app)

    proposal_id = _propose(
        app,
        lambda info: ServicesMutation().set_app_secret(
            info,
            input=SetAppSecretInput(app_slug=app.slug, key="PROD_ONLY", value="v", scope="production"),
        ),
    )

    diff = SecretChangeProposal.objects.get(guid=proposal_id).payload_diff
    assert (diff["before"]["scope"], diff["after"]["scope"]) == ("all", "production")
    assert "scope all to production" in diff["summary"]


def test_under_approval_an_approved_delete_retires_the_metadata_of_a_draft_only_key(
    approver_grants, app, env
):
    _seed_manifest(app)
    _require_secret_approval(app)
    _approved_set(app, key="DRAFT_ONLY", value="v", scope="production")
    assert _live_metadata(app, "DRAFT_ONLY").exists()

    _approved(
        app,
        lambda info: ServicesMutation().delete_app_secret(
            info, input=DeleteAppSecretInput(app_slug=app.slug, key="DRAFT_ONLY")
        ),
    )

    assert not _live_metadata(app, "DRAFT_ONLY").exists()


def test_under_approval_discarding_an_approved_delete_keeps_the_key_scoped(
    approver_grants, app, env, monkeypatch
):
    """While manifest_raw still has the key, an approved delete is a draft.
    Discarding the draft brings the key back, and it must come back with
    its restriction, not as scope "all"."""
    app.manifest_raw = set_app_env_keys(_MANIFEST, {"PROD_ONLY": "repo-value"})
    app.save(update_fields=["manifest_raw"])
    with _ctx(app):
        restricted = ServicesMutation().set_app_secret_metadata(
            _info(), input=SetAppSecretMetadataInput(app_slug=app.slug, key="PROD_ONLY", scope="production")
        )
    assert restricted.ok, restricted.errors
    _require_secret_approval(app)
    preview = _preview_environment(app, env, name="preview-discard", status=PreviewEnvironment.Status.RUNNING)
    _approved(
        app,
        lambda info: ServicesMutation().delete_app_secret(
            info, input=DeleteAppSecretInput(app_slug=app.slug, key="PROD_ONLY")
        ),
    )

    _stage_via_update_manifest(app, app.manifest_raw)

    assert app.manifest_raw_staged == ""
    assert _materialize(_deployment(app, preview), monkeypatch) == {}
    assert _materialize(_deployment(app, env), monkeypatch) == {"PROD_ONLY": "repo-value"}


# An applied proposal vouches only while manifest_raw still has the value it
# was applied against. Otherwise re-staging a value the repo has since
# rotated out would ride on the approval that first set it
# (#1758 re-review, 3).


def _push_and_sync(app) -> None:
    """What a merged pushManifestToRepo followed by a sync leaves behind."""
    app.manifest_raw = app.manifest_raw_staged
    app.manifest_raw_staged = ""
    app.save(update_fields=["manifest_raw", "manifest_raw_staged"])


def _repo_change(app, text: str) -> None:
    app.manifest_raw = text
    app.save(update_fields=["manifest_raw"])


def test_under_approval_re_staging_a_value_the_repo_rotated_out_does_not_deploy(
    approver_grants, app, env, monkeypatch
):
    _seed_manifest(app)
    _require_secret_approval(app)
    _approved_set(app, key="API_KEY", value="leaked-value")
    _push_and_sync(app)
    _repo_change(app, set_app_env_keys(app.manifest_raw, {"API_KEY": "rotated-in-the-repo"}))

    _stage_via_update_manifest(app, set_app_env_keys(app.manifest_raw, {"API_KEY": "leaked-value"}))

    assert _materialize(_deployment(app, env), monkeypatch) == {"API_KEY": "rotated-in-the-repo"}


def test_under_approval_re_staging_a_delete_the_repo_undid_does_not_deploy(
    approver_grants, app, env, monkeypatch
):
    _repo_change(app, set_app_env_keys(_MANIFEST, {"API_KEY": "old-value"}))
    _require_secret_approval(app)
    _approved(
        app,
        lambda info: ServicesMutation().delete_app_secret(
            info, input=DeleteAppSecretInput(app_slug=app.slug, key="API_KEY")
        ),
    )
    _push_and_sync(app)
    _repo_change(app, set_app_env_keys(app.manifest_raw, {"API_KEY": "re-added-in-the-repo"}))

    without_key, removed = delete_app_env_key(app.manifest_raw, "API_KEY")
    assert removed
    _stage_via_update_manifest(app, without_key)

    assert _materialize(_deployment(app, env), monkeypatch) == {"API_KEY": "re-added-in-the-repo"}


def test_under_approval_a_proposal_applied_without_a_base_stamp_vouches_for_nothing(
    approver_grants, app, env, monkeypatch
):
    """Proposals applied before the stamp existed fail closed."""
    _seed_manifest(app)
    _require_secret_approval(app)
    SecretChangeProposal.objects.create(
        registered_app=app,
        op=SecretChangeProposal.Op.SET.value,
        payload={"key": "API_KEY", "value": "approved-long-ago"},
        status=SecretChangeProposal.Status.APPLIED.value,
        applied_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=7),
    )
    _stage_via_update_manifest(app, set_app_env_keys(_MANIFEST, {"API_KEY": "approved-long-ago"}))

    assert _materialize(_deployment(app, env), monkeypatch) == {}


# A stamp over the value alone vouched for any key, in any app, whose repo
# value was the same, and equal stamps showed that two keys share a value
# (#1758 re-review, L2).


def test_a_base_stamp_vouches_only_for_the_key_it_was_applied_to(approver_grants, app, env, monkeypatch):
    _seed_manifest(app)
    _require_secret_approval(app)
    _approved_set(app, key="A", value="same")
    stamp = SecretChangeProposal.objects.get(registered_app=app, payload__key="A").payload["base_raw_digest"]
    SecretChangeProposal.objects.create(
        registered_app=app,
        op=SecretChangeProposal.Op.SET.value,
        payload={"key": "B", "value": "same", "base_raw_digest": stamp},
        status=SecretChangeProposal.Status.APPLIED.value,
        applied_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=7),
    )

    _stage_via_update_manifest(app, set_app_env_keys(app.manifest_raw_staged, {"B": "same"}))

    assert _materialize(_deployment(app, env), monkeypatch) == {"A": "same"}


def test_a_value_gets_a_different_stamp_under_another_key_or_app(app, org, project, team):
    other = RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Other",
        slug="other-app",
        provisioning_status="ready",
    )

    stamps = {
        base_raw_digest(app, "K", "same"),
        base_raw_digest(app, "K2", "same"),
        base_raw_digest(other, "K", "same"),
    }

    assert len(stamps) == 3


# Under secret approval, a scope change through setAppSecretMetadata widened
# which environments get a value, previews included, with APP_UPDATE alone.
# It now waits on a proposal like a value write (#1946).


def _set_scope(app, key: str, **fields):
    with _ctx(app):
        return ServicesMutation().set_app_secret_metadata(
            _info(_user()), input=SetAppSecretMetadataInput(app_slug=app.slug, key=key, **fields)
        )


def _repo_secret_restricted_to_production(app) -> None:
    _repo_change(app, set_app_env_keys(_MANIFEST, {"PROD_ONLY": "repo-value"}))
    assert _set_scope(app, "PROD_ONLY", scope="production").ok


def test_under_approval_a_metadata_scope_change_waits_for_its_proposal(
    approver_grants, app, env, monkeypatch
):
    _repo_secret_restricted_to_production(app)
    _require_secret_approval(app)
    preview = _preview_environment(app, env, name="preview-widen", status=PreviewEnvironment.Status.RUNNING)

    widened = _set_scope(app, "PROD_ONLY", scope="all")

    assert widened.ok, widened.errors
    assert widened.data.pending_proposal_id is not None
    assert widened.data.scope == "production"
    assert _live_metadata(app, "PROD_ONLY").get().scope == "production"
    assert _materialize(_deployment(app, preview), monkeypatch) == {}

    _approve(app, str(widened.data.pending_proposal_id))

    assert _materialize(_deployment(app, preview), monkeypatch) == {"PROD_ONLY": "repo-value"}


def test_under_approval_a_metadata_edit_that_keeps_the_scope_applies_directly(approver_grants, app, env):
    _repo_secret_restricted_to_production(app)
    _require_secret_approval(app)

    result = _set_scope(app, "PROD_ONLY", scope="production", set_via="cli")

    assert result.ok, result.errors
    assert result.data.pending_proposal_id is None
    row = _live_metadata(app, "PROD_ONLY").get()
    assert (row.scope, row.source) == ("production", "cli")
    assert not SecretChangeProposal.objects.filter(registered_app=app).exists()


def test_under_approval_a_new_per_environment_row_does_not_widen_the_key(
    approver_grants, app, env, monkeypatch
):
    """The deploy prefers a key's per-environment row to its app-wide one.
    Created as "all", a row that only set an expiry for one preview put a
    production-only key in that preview, without a proposal."""
    _repo_secret_restricted_to_production(app)
    _require_secret_approval(app)
    preview = _preview_environment(app, env, name="preview-expiry", status=PreviewEnvironment.Status.RUNNING)

    result = _set_scope(
        app, "PROD_ONLY", environment_name=preview.name, expires_at=timezone.now() + timedelta(days=30)
    )

    assert result.ok, result.errors
    assert result.data.pending_proposal_id is None
    assert _materialize(_deployment(app, preview), monkeypatch) == {}
    assert result.data.scope == "production"


# A per-environment row written without a scope copied the app-wide scope in
# force when it was created, and the deploy prefers that row. An approved
# app-wide narrowing therefore never reached the environment, and the
# approver's diff showed only the app-wide row (#1758 re-review, M1).


def test_under_approval_narrowing_the_app_wide_scope_reaches_a_preview_with_its_own_row(
    approver_grants, app, env, monkeypatch
):
    _repo_change(app, set_app_env_keys(_MANIFEST, {"K": "repo-value"}))
    _require_secret_approval(app)
    preview = _preview_environment(app, env, name="preview-pin", status=PreviewEnvironment.Status.RUNNING)
    expiry = _set_scope(
        app, "K", environment_name=preview.name, expires_at=timezone.now() + timedelta(days=30)
    )
    assert expiry.ok, expiry.errors
    assert expiry.data.pending_proposal_id is None

    narrowed = _set_scope(app, "K", scope="production")
    _approve(app, str(narrowed.data.pending_proposal_id))

    assert _materialize(_deployment(app, preview), monkeypatch) == {}
    assert _materialize(_deployment(app, env), monkeypatch) == {"K": "repo-value"}


def test_a_per_environment_row_without_a_scope_follows_the_app_wide_scope(
    approver_grants, app, env, monkeypatch
):
    _repo_change(app, set_app_env_keys(_MANIFEST, {"K": "repo-value"}))
    preview = _preview_environment(app, env, name="preview-follow", status=PreviewEnvironment.Status.RUNNING)
    assert _set_scope(app, "K", environment_name=preview.name, set_via="cli").ok
    assert _materialize(_deployment(app, preview), monkeypatch) == {"K": "repo-value"}

    assert _set_scope(app, "K", scope="production").ok
    assert _materialize(_deployment(app, preview), monkeypatch) == {}
    assert _materialize(_deployment(app, env), monkeypatch) == {"K": "repo-value"}

    assert _set_scope(app, "K", scope="preview").ok
    assert _materialize(_deployment(app, preview), monkeypatch) == {"K": "repo-value"}
    assert _materialize(_deployment(app, env), monkeypatch) == {}


def test_under_approval_an_explicit_per_environment_scope_waits_for_its_proposal(
    approver_grants, app, env, monkeypatch
):
    _repo_secret_restricted_to_production(app)
    _require_secret_approval(app)
    preview = _preview_environment(app, env, name="preview-own", status=PreviewEnvironment.Status.RUNNING)

    widened = _set_scope(app, "PROD_ONLY", environment_name=preview.name, scope="all")

    assert widened.ok, widened.errors
    assert widened.data.pending_proposal_id is not None
    assert widened.data.scope == "production"
    assert not _live_metadata(app, "PROD_ONLY").filter(environment_name=preview.name).exists()
    assert _materialize(_deployment(app, preview), monkeypatch) == {}

    _approve(app, str(widened.data.pending_proposal_id))

    assert _materialize(_deployment(app, preview), monkeypatch) == {"PROD_ONLY": "repo-value"}
    assert _materialize(_deployment(app, env), monkeypatch) == {"PROD_ONLY": "repo-value"}


def test_under_approval_an_explicit_per_environment_scope_equal_to_the_scope_in_force_waits_for_its_proposal(
    approver_grants, app, env, monkeypatch
):
    """The review's probe with the scope named: pinning a preview to the
    scope it already has still stops later app-wide changes reaching it,
    so it waits on a proposal like any scope change."""
    _repo_change(app, set_app_env_keys(_MANIFEST, {"K": "repo-value"}))
    _require_secret_approval(app)
    preview = _preview_environment(app, env, name="preview-pin", status=PreviewEnvironment.Status.RUNNING)

    pinned = _set_scope(
        app, "K", environment_name=preview.name, scope="all", expires_at=timezone.now() + timedelta(days=30)
    )

    assert pinned.ok, pinned.errors
    assert pinned.data.pending_proposal_id is not None
    assert pinned.data.scope == "all"
    assert not _live_metadata(app, "K").filter(environment_name=preview.name).exists()
    narrowed = _set_scope(app, "K", scope="production")
    _approve(app, str(narrowed.data.pending_proposal_id))
    assert _materialize(_deployment(app, preview), monkeypatch) == {}


def test_a_pin_proposal_says_the_environment_stops_following_the_app_wide_scope(approver_grants, app, env):
    """Pinned at the scope it already has, the diff otherwise reads as a
    change from all to all, which an approver would take for a no-op."""
    _repo_change(app, set_app_env_keys(_MANIFEST, {"K": "repo-value"}))
    _require_secret_approval(app)
    preview = _preview_environment(
        app, env, name="preview-pin-diff", status=PreviewEnvironment.Status.RUNNING
    )

    pinned = _set_scope(app, "K", environment_name=preview.name, scope="all")

    assert pinned.data.pending_proposal_id is not None
    diff = SecretChangeProposal.objects.get(guid=str(pinned.data.pending_proposal_id)).payload_diff
    assert (diff["before"].get("follows_app_wide"), diff["after"].get("follows_app_wide")) == (True, False)
    assert diff["summary"].startswith("Pin scope of K in preview-pin-diff to all;")


def test_set_metadata_refuses_an_environment_the_app_does_not_have(approver_grants, app, env):
    """A row for a name the app does not have would govern whatever
    environment later takes that name."""
    _repo_change(app, set_app_env_keys(_MANIFEST, {"K": "repo-value"}))
    gone = _preview_environment(app, env, name="preview-gone", status=PreviewEnvironment.Status.TORN_DOWN)
    gone.soft_delete()
    other_app = RegisteredApp.objects.create(
        organization=app.organization,
        project=app.project,
        team=app.team,
        name="Other",
        slug="other-app",
        provisioning_status="ready",
    )
    AppEnvironment.objects.create(
        registered_app=other_app,
        tenant_cluster=env.tenant_cluster,
        name="only-in-other-app",
        url="https://other.example.com",
        required_approvals=0,
    )

    for name in ("no-such-env", gone.name, "only-in-other-app"):
        result = _set_scope(app, "K", environment_name=name, expires_at=timezone.now() + timedelta(days=30))

        assert result.ok is False, name
        assert (result.errors[0].code, result.errors[0].field) == ("NOT_FOUND", "environmentName"), name
    assert not AppSecretMetadata.objects.filter(key="K").exists()


def test_an_app_wide_scope_proposal_lists_the_environments_that_keep_their_own_scope(
    approver_grants, app, env
):
    """A per-environment row with a scope of its own is not reached by an
    app-wide change, so the approver must see it next to the new scope."""
    _repo_change(app, set_app_env_keys(_MANIFEST, {"K": "repo-value"}))
    running = PreviewEnvironment.Status.RUNNING
    pinned = _preview_environment(app, env, name="preview-pinned", status=running, branch="feat-a")
    follows = _preview_environment(app, env, name="preview-follows", status=running, branch="feat-b")
    matches = _preview_environment(app, env, name="preview-matches", status=running, branch="feat-c")
    assert _set_scope(app, "K", environment_name=pinned.name, scope="all").ok
    assert _set_scope(app, "K", environment_name=follows.name, set_via="cli").ok
    assert _set_scope(app, "K", environment_name=matches.name, scope="production").ok
    _require_secret_approval(app)

    by_metadata = _set_scope(app, "K", scope="production")
    by_rotation = _propose(
        app,
        lambda info: ServicesMutation().rotate_app_secret(
            info, input=RotateAppSecretInput(app_slug=app.slug, key="K", value="rotated", scope="production")
        ),
    )

    for proposal_id in (str(by_metadata.data.pending_proposal_id), by_rotation):
        diff = SecretChangeProposal.objects.get(guid=proposal_id).payload_diff
        assert (diff["before"]["scope"], diff["after"]["scope"]) == ("all", "production")
        assert diff["after"].get("overrides") == "preview-pinned (all)"
        assert diff["summary"].endswith("; preview-pinned keeps all")


# setAppSecretMetadata decides between writing and proposing from the scope
# in force: an app-wide edit that names the scope in force applies directly.
# Read outside a lock, that scope could be one an approved change was
# committing at that moment, and the direct write then put back the scope
# the approval had just changed, without a proposal.


def _lock_waiters() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        return cursor.fetchone()[0]


def _on_own_connection(fn) -> tuple[threading.Thread, dict]:
    box: dict = {}

    def run():
        close_old_connections()
        try:
            box["result"] = fn()
        except BaseException as exc:
            box["error"] = exc
        finally:
            close_old_connections()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, box


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("change", ["metadata", "metadata-over-an-app-wide-row", "rotation"])
def test_under_approval_a_scope_check_waits_for_a_scope_change_being_applied(
    approver_grants, app, env, monkeypatch, change
):
    _repo_change(app, set_app_env_keys(_MANIFEST, {"K": "repo-value"}))
    if change == "metadata-over-an-app-wide-row":
        assert _set_scope(app, "K", set_via="web").ok
    _require_secret_approval(app)
    preview = _preview_environment(app, env, name="preview-race", status=PreviewEnvironment.Status.RUNNING)
    if change == "rotation":
        proposal_id = _propose(
            app,
            lambda info: ServicesMutation().rotate_app_secret(
                info, input=RotateAppSecretInput(app_slug=app.slug, key="K", value="v2", scope="production")
            ),
        )
    else:
        proposal_id = str(_set_scope(app, "K", scope="production").data.pending_proposal_id)
    proposal = SecretChangeProposal.objects.select_related("registered_app").get(guid=proposal_id)
    applied, release = threading.Event(), threading.Event()

    def apply_and_hold():
        with transaction.atomic():
            assert apply_proposal(proposal).ok
            applied.set()
            release.wait(timeout=30)

    applier, applier_box = _on_own_connection(apply_and_hold)
    assert applied.wait(timeout=30)
    editor, editor_box = _on_own_connection(lambda: _set_scope(app, "K", scope="all", set_via="cli"))
    deadline = time.monotonic() + 30
    while editor.is_alive() and not _lock_waiters() and time.monotonic() < deadline:
        time.sleep(0.05)
    release.set()
    applier.join(timeout=30)
    editor.join(timeout=30)

    assert "error" not in applier_box, applier_box
    assert "error" not in editor_box, editor_box
    edited = editor_box["result"]
    assert edited.ok, edited.errors
    assert edited.data.pending_proposal_id is not None
    assert edited.data.scope == "production"
    assert _live_metadata(app, "K").get(environment_name="").scope == "production"
    assert _materialize(_deployment(app, preview), monkeypatch) == {}
