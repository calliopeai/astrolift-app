"""Tearing down a preview deletes its literal Secret from the app's shared
namespace, not just its own dedicated namespace (#1923).

A preview gets its own dedicated Kubernetes namespace (``PreviewEnvironment.
namespace``) for ``provision_preview_namespace`` / ``delete_preview_namespace``,
but its actual workloads and literal ``[env]`` Secret deploy into the app's
SHARED namespace (``namespace_for_app`` -- every environment of an app,
prod/staging/every preview, shares one namespace per cluster). Deleting the
preview's own namespace therefore never touched its literal Secret sitting in
the shared one, and it outlived the preview indefinitely.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.models.preview_environment import PreviewEnvironment
from astrolift_services.schema.mutations import ServicesMutation
from astrolift_services.schema.mutations.types import SetAppSecretInput
from astrolift_workflows.activities.app_lifecycle import (
    _app_env_secret_name,
    _delete_preview_namespace_sync,
)
from core.permissions import Permission
from core.tenancy import TenantContext
from core.tenancy import tenant_context as _tenant_ctx

pytestmark = pytest.mark.django_db


def _info():
    request = SimpleNamespace(user=None, META={})
    return SimpleNamespace(context=SimpleNamespace(user=None, request=request))


def _ctx(app):
    return _tenant_ctx(TenantContext(organization_id=app.organization_id))


def _set_app_secret(app, *, key: str, value: str, scope: str = "all") -> None:
    with _ctx(app):
        result = ServicesMutation().set_app_secret(
            _info(), input=SetAppSecretInput(app_slug=app.slug, key=key, value=value, scope=scope)
        )
    assert result.ok, result.errors


class _FakeDriver:
    def __init__(self):
        self.deleted_manifest_names: list[str] = []
        self.deleted_namespaces: list[str] = []

    def delete_manifests(self, cluster_slug, namespace, manifests, **_):
        self.deleted_manifest_names.extend(m["metadata"]["name"] for m in manifests)

    def delete_namespace(self, cluster_slug, namespace, wait=False):
        self.deleted_namespaces.append(namespace)


def _preview(app, env, *, name: str = "preview-teardown") -> PreviewEnvironment:
    preview_env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=env.tenant_cluster,
        name=name,
        url=f"https://{name}.hello.example.com",
        required_approvals=0,
    )
    return PreviewEnvironment.objects.create(
        registered_app=app,
        branch="feat-teardown",
        is_manual=True,
        status=PreviewEnvironment.Status.RUNNING,
        hostname=f"{name}.hello.example.com",
        namespace=f"preview-{name}",
        app_environment=preview_env,
    )


_MANIFEST = """
name = "hello-app"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "web"
  is_primary = true
"""


def test_teardown_deletes_the_literal_secret_from_the_shared_namespace(
    permission_resolver, app, env, monkeypatch
):
    permission_resolver.grant(Permission.APP_UPDATE)
    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    _set_app_secret(app, key="API_KEY", value="preview-value", scope="preview")
    app.refresh_from_db()
    preview = _preview(app, env)

    driver = _FakeDriver()
    monkeypatch.setattr(
        "core.cluster_management._driver_for_cluster",
        lambda cluster: driver,
    )
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster",
        lambda cluster: SimpleNamespace(slug="test-cluster"),
    )

    namespace = _delete_preview_namespace_sync(preview.pk)

    assert namespace == preview.namespace
    assert driver.deleted_namespaces == [preview.namespace]
    # The literal Secret lives in the app's shared namespace, not the
    # preview's own dedicated one, and the shared namespace itself must
    # never be deleted -- other environments of this app live there too.
    assert _app_env_secret_name(app.slug, preview.app_environment.name) in driver.deleted_manifest_names
    assert preview.namespace not in driver.deleted_manifest_names


def test_teardown_is_harmless_when_the_preview_has_no_literal_secret(app, env, monkeypatch):
    preview = _preview(app, env, name="preview-empty")

    driver = _FakeDriver()
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: driver)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster",
        lambda cluster: SimpleNamespace(slug="test-cluster"),
    )

    _delete_preview_namespace_sync(preview.pk)

    assert driver.deleted_namespaces == [preview.namespace]
