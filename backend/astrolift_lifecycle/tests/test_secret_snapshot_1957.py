"""A deploy's literal secret snapshot, and restore on failure/rollback (#1957).

Before this, ``render_resources_for_deployment`` and ``_update_secrets_sync``
each read the live staged buffer independently (#1923: they could disagree if
an edit landed between the two calls within one deploy), and neither a failed
deploy nor a rollback put back what the previous release actually ran with.
``Deployment.secret_snapshot`` is written once per deployment row (the first
caller computes it, every later caller for the same row reads it back), a
rollback copies its target's snapshot forward at creation, and a failed
deploy restores the previous release's snapshot.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_services.schema.mutations.types import SetAppSecretInput
from astrolift_services.secret_literals import snapshot_literal_secrets
from astrolift_workflows.activities.app_lifecycle import (
    _app_env_secret_name,
    _create_rollback_deployment_sync,
    _restore_previous_secrets_sync,
    _update_secrets_sync,
)
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


def _deployment(app, env, **fields) -> Deployment:
    if not app.manifest_raw:
        _seed_manifest(app)
    defaults = {
        "registered_app": app,
        "app_environment": env,
        "trigger_kind": Deployment.TriggerKind.MANUAL.value,
        "status": Deployment.Status.PENDING.value,
        "image_tag": "v1",
    }
    defaults.update(fields)
    return Deployment.objects.create(**defaults)


def _set_app_secret(app, *, key: str, value: str, scope: str = "all"):
    from astrolift_services.schema.mutations import ServicesMutation

    with _ctx(app):
        return ServicesMutation().set_app_secret(
            _info(), input=SetAppSecretInput(app_slug=app.slug, key=key, value=value, scope=scope)
        )


class _FakeClusterDriver:
    def __init__(self):
        self.applied: list[dict] = []
        self.deleted: list[str] = []

    def apply_manifests(self, cluster_slug, namespace, manifests, dry_run=False):
        from providers._sdk.cluster import ApplyResult

        if dry_run:
            return ApplyResult(created=[], updated=[], unchanged=[], errors=[])
        self.applied = manifests
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])

    def delete_manifests(self, cluster_slug, namespace, manifests, **_):
        self.deleted.extend(m["metadata"]["name"] for m in manifests)


def _literal_secret(manifests: list[dict]) -> dict | None:
    matches = [m for m in manifests if "astrolift.io/app-env-secrets" in m["metadata"].get("labels", {})]
    assert len(matches) <= 1
    return matches[0] if matches else None


def _decoded(secret: dict) -> dict[str, str]:
    return {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}


def _patch_driver(monkeypatch, driver=None) -> _FakeClusterDriver:
    driver = driver or _FakeClusterDriver()
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda d: (driver, SimpleNamespace(slug="test-cluster"), "acme-hello-app"),
    )
    return driver


def test_snapshot_is_computed_once_and_then_reused(permission_resolver, app, env):
    """A live edit after the first call must not change the second call's
    answer for the same deployment row (#1923 snapshot drift)."""
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="API_KEY", value="first").ok
    app.refresh_from_db()
    deployment = _deployment(app, env)

    first = snapshot_literal_secrets(deployment)
    assert first == {"API_KEY": "first"}

    assert _set_app_secret(app, key="API_KEY", value="changed-after-snapshot").ok
    app.refresh_from_db()

    second = snapshot_literal_secrets(deployment)
    assert second == {"API_KEY": "first"}

    deployment.refresh_from_db()
    assert deployment.secret_snapshot["literals"] == {"API_KEY": "first"}


def test_render_and_update_secrets_agree_even_if_the_buffer_moves_between_them(
    permission_resolver, app, env, monkeypatch
):
    """render_resources_for_deployment (the render half) and _update_secrets_sync
    (the write half) must materialize the exact same deployment's snapshot,
    not independently recomputed live answers, even if a secret edit lands
    between the two calls within one deploy."""
    from core.app_deploy import render_resources_for_deployment

    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="API_KEY", value="rendered-value").ok
    app.refresh_from_db()
    deployment = _deployment(app, env)

    resources = render_resources_for_deployment(deployment)
    workload = next(r for r in resources if r["kind"] == "Deployment")
    env_from = [
        ref["secretRef"]["name"] for ref in workload["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    ]
    assert _app_env_secret_name(app.slug, env.name) in env_from

    # A concurrent edit lands after the render but before update_secrets runs.
    assert _set_app_secret(app, key="API_KEY", value="written-after-render").ok
    app.refresh_from_db()

    driver = _patch_driver(monkeypatch)
    _update_secrets_sync(deployment.pk)

    secret = _literal_secret(driver.applied)
    assert secret is not None
    assert _decoded(secret) == {"API_KEY": "rendered-value"}


def test_rollback_copies_the_target_deployments_snapshot(permission_resolver, app, env, monkeypatch):
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="API_KEY", value="running-release-value").ok
    app.refresh_from_db()
    running = _deployment(app, env, status=Deployment.Status.RUNNING.value)
    snapshot_literal_secrets(running)  # materialize + persist, as a real deploy would

    # The bad deploy is itself RUNNING (it shipped, and turned out to be
    # bad) -- SUPERSEDED is only a valid transition from RUNNING.
    bad = _deployment(app, env, status=Deployment.Status.RUNNING.value)

    new_id = _create_rollback_deployment_sync(bad.pk)
    rollback = Deployment.objects.get(pk=new_id)
    assert rollback.secret_snapshot == running.secret_snapshot

    # The app's live secret has since moved on; the rollback must restore
    # what `running` had, not the live value.
    assert _set_app_secret(app, key="API_KEY", value="edited-after-rollback-created").ok
    app.refresh_from_db()

    driver = _patch_driver(monkeypatch)
    _update_secrets_sync(rollback.pk)

    assert _decoded(_literal_secret(driver.applied)) == {"API_KEY": "running-release-value"}

    running.refresh_from_db()
    assert Deployment.objects.get(pk=bad.pk).status == Deployment.Status.SUPERSEDED.value


def test_promotion_does_not_copy_a_snapshot_across_environments(permission_resolver, app, env, monkeypatch):
    """Unlike rollback, promotion moves to a different AppEnvironment whose
    scope-filtered literals legitimately differ -- copying the source env's
    snapshot forward would apply the wrong environment's values."""
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_lifecycle.models.preview_environment import PreviewEnvironment
    from astrolift_workflows.activities.app_lifecycle import _create_promotion_deployment_sync

    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="SHARED", value="shared-value").ok
    assert _set_app_secret(app, key="PROD_ONLY", value="prod-value", scope="production").ok
    app.refresh_from_db()

    preview_env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=env.tenant_cluster,
        name="preview-src",
        url="https://preview-src.example.com",
        required_approvals=0,
    )
    PreviewEnvironment.objects.create(
        registered_app=app,
        branch="feat-x",
        is_manual=True,
        status=PreviewEnvironment.Status.RUNNING,
        hostname="preview-src.example.com",
        namespace="acme-hello-app",
        app_environment=preview_env,
    )

    source = _deployment(app, preview_env, status=Deployment.Status.RUNNING.value)
    snapshot_literal_secrets(source)
    assert source.secret_snapshot["literals"] == {"SHARED": "shared-value"}

    new_id = _create_promotion_deployment_sync(source.pk, env.pk)
    promoted = Deployment.objects.get(pk=new_id)
    assert promoted.secret_snapshot == {}

    driver = _patch_driver(monkeypatch)
    _update_secrets_sync(promoted.pk)
    assert _decoded(_literal_secret(driver.applied)) == {
        "SHARED": "shared-value",
        "PROD_ONLY": "prod-value",
    }


def test_restore_previous_secrets_writes_back_the_prior_running_deployments_values(
    permission_resolver, app, env, monkeypatch
):
    permission_resolver.grant(Permission.APP_UPDATE)
    _seed_manifest(app)
    assert _set_app_secret(app, key="API_KEY", value="last-good-value").ok
    app.refresh_from_db()
    running = _deployment(app, env, status=Deployment.Status.RUNNING.value)
    snapshot_literal_secrets(running)

    # The new deploy rotated the value and then failed after writing it.
    assert _set_app_secret(app, key="API_KEY", value="never-shipped-value").ok
    app.refresh_from_db()
    failed = _deployment(app, env)
    snapshot_literal_secrets(failed)

    driver = _patch_driver(monkeypatch)
    assert _restore_previous_secrets_sync(failed.pk) is True

    assert _decoded(_literal_secret(driver.applied)) == {"API_KEY": "last-good-value"}


def test_restore_previous_secrets_deletes_when_the_prior_had_no_literals(
    permission_resolver, app, env, monkeypatch
):
    running = _deployment(app, env, status=Deployment.Status.RUNNING.value)
    snapshot_literal_secrets(running)
    assert running.secret_snapshot["literals"] == {}

    permission_resolver.grant(Permission.APP_UPDATE)
    assert _set_app_secret(app, key="API_KEY", value="rolled-out-but-failed").ok
    app.refresh_from_db()
    failed = _deployment(app, env)
    snapshot_literal_secrets(failed)

    driver = _patch_driver(monkeypatch)
    assert _restore_previous_secrets_sync(failed.pk) is True

    assert driver.applied == []
    assert driver.deleted == [_app_env_secret_name(app.slug, env.name)]


def test_restore_previous_secrets_is_a_noop_with_no_prior_deployment(app, env, monkeypatch):
    failed = _deployment(app, env)
    driver = _patch_driver(monkeypatch)

    assert _restore_previous_secrets_sync(failed.pk) is False
    assert driver.applied == [] and driver.deleted == []


def test_restore_previous_secrets_is_a_noop_when_the_prior_predates_the_snapshot_feature(
    app, env, monkeypatch
):
    """A prior RUNNING deployment created before #1957 has secret_snapshot={}
    -- no "literals" key at all -- and restore must not treat that as "the
    prior had no literals" and delete anything."""
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v0",
    )
    failed = _deployment(app, env)
    driver = _patch_driver(monkeypatch)

    assert _restore_previous_secrets_sync(failed.pk) is False
    assert driver.applied == [] and driver.deleted == []
