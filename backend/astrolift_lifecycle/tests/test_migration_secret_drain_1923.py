"""Draining a migration's source cluster deletes the literal Secret too (#1923).

``_drain_source_cluster_sync`` renders and deletes the app's *workload*
resource set from the source cluster. The literal ``[env]`` Secret is
synthesized separately by ``update_secrets``, so it was never in that
delete set and stayed behind on the source cluster holding the app's last
plaintext values indefinitely.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_services.schema.mutations import ServicesMutation
from astrolift_services.schema.mutations.types import SetAppSecretInput
from astrolift_workflows.activities.app_lifecycle import _app_env_secret_name
from astrolift_workflows.activities.migration import _drain_source_cluster_sync
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


def _set_app_secret(app, *, key: str, value: str) -> None:
    with _ctx(app):
        result = ServicesMutation().set_app_secret(
            _info(), input=SetAppSecretInput(app_slug=app.slug, key=key, value=value, scope="all")
        )
    assert result.ok, result.errors


class _FakeSourceDriver:
    def __init__(self):
        self.deleted_names: list[str] = []

    def delete_manifests(self, cluster_slug, namespace, manifests, **_):
        from providers._sdk.cluster import ApplyResult

        self.deleted_names.extend(m["metadata"]["name"] for m in manifests)
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])


def test_drain_deletes_the_literal_secret_alongside_the_workloads(
    permission_resolver, app, env, cluster, monkeypatch
):
    permission_resolver.grant(Permission.APP_UPDATE)
    app.manifest_raw = _MANIFEST
    app.save(update_fields=["manifest_raw"])
    _set_app_secret(app, key="API_KEY", value="draining-value")
    app.refresh_from_db()
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1",
    )

    driver = _FakeSourceDriver()
    monkeypatch.setattr(
        "core.cluster_management._driver_for_cluster",
        lambda c: driver,
    )
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster",
        lambda c: SimpleNamespace(slug="source-cluster"),
    )

    errors = _drain_source_cluster_sync(app.pk, env.pk, cluster.pk)

    assert errors == []
    assert _app_env_secret_name(app.slug, env.name) in driver.deleted_names


def test_drain_is_a_noop_when_the_source_cluster_id_no_longer_resolves(app, env):
    Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1",
    )

    assert _drain_source_cluster_sync(app.pk, env.pk, 999999) == []
