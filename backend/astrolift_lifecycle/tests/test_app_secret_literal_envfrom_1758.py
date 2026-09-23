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
from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_services.schema.mutations import ServicesMutation
from astrolift_services.schema.mutations.types import SetAppSecretInput
from astrolift_workflows.activities.app_lifecycle import (
    _app_env_secret_name,
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


def _info():
    request = SimpleNamespace(user=None, META={})
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


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


def _materialize(deployment, monkeypatch) -> dict[str, str]:
    """Run update_secrets against a fake cluster and return the literal
    Secret's decoded data, or {} when it wasn't materialized at all."""
    driver = _FakeClusterDriver()
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda d: (driver, SimpleNamespace(slug="test-cluster"), "acme-hello-app"),
    )
    _update_secrets_sync(deployment.pk)
    secrets = _literal_secrets(driver.applied)
    assert len(secrets) <= 1
    return _decoded(secrets[0]) if secrets else {}


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
    assert _app_env_secret_name(app.slug) in _env_from_names(resources)


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
    assert secret["metadata"]["name"] == _app_env_secret_name(app.slug)
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
