"""A managed-service config secret ref outside the org fails closed after write time (#1921).

The mutations refuse such a config as it is written. A config or a binding row
stored before they did must not reach a store either, so the same check runs
again where each one would:

* the provision and update activities, before a driver dereferences the config
  (a driver can read a ``*_secret_ref`` itself: the FSx directory join, the
  Amazon MQ LDAP bind);
* ``_sync_binding_rows``, before a copied ref becomes a row;
* the resolve paths that read stored rows: app deploy, volume credentials and
  the app's IRSA grants.

A ref the driver minted from the instance's identity keeps resolving. The
namespace is the owning app's, ``services/<org guid>/<app guid>/``, so a config
naming another app of the same org is refused on these paths too.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from _sdk.managed_service import Binding, Grant, ValueRef, VolumeMount, VolumeSourceKind

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService, ManagedServiceBinding, ManagedServiceVolumeBinding
from astrolift_workflows.activities.managed_service_lifecycle import (
    ManagedServicePreflightError,
    _provision_sync,
    _sync_binding_rows,
    _update_sync,
)
from core.app_deploy import AppDeployError
from providers._sdk.cluster import ApplyResult

pytestmark = pytest.mark.django_db

# The AWS driver resolves this to astrolift/managed/rds-orders/url: another
# tenant's database URL.
_VICTIM = "managed/rds-orders/url"
_VICTIM_ARN = "arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/rds/orders/master-AbCdEf"
_MINTED = "astrolift/msk/events-1921/password"


def _world():
    org = Organization.objects.create(name="Acme", slug="acme-msr-1921")
    team = Team.objects.create(organization=org, name="Eng", slug="eng-msr-1921")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo-msr-1921")
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="AWS 1921", slug="aws-1921", plugin_version="0.0.1")],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug="aws-1921-cluster",
        name="AWS",
        provider_plugin=ProviderPlugin.objects.get(slug="aws-1921"),
        endpoint="https://cluster.invalid",
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="App",
        slug="app-msr-1921",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return SimpleNamespace(org=org, team=team, project=project, app=app, env=env, cluster=cluster)


def _other_app_ns(world, name):
    """A location in the namespace of another app of the same org."""
    other = RegisteredApp.objects.create(
        organization=world.org,
        team=world.team,
        project=world.project,
        name="Billing",
        slug="billing-msr-1921",
        provisioning_status="ready",
    )
    return f"services/{world.org.guid}/{other.guid}/{name}"


def _service(world, *, config=None, kind=ManagedService.Kind.EVENT_STREAM, name="events"):
    return ManagedService.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        kind=kind,
        variant="msk",
        name=name,
        config=dict(config or {}),
        applied_config=dict(config or {}),
        backend_ref=f"{kind}/{name}",
        status=ManagedService.Status.ACTIVE,
    )


def _driver_must_not_run(*_args, **_kwargs):
    raise AssertionError("the driver was resolved for a config naming a secret outside the org")


# ---- provision and update activities --------------------------------------------


@pytest.mark.parametrize("activity", [_provision_sync, _update_sync], ids=["provision", "update"])
def test_the_activity_refuses_a_stored_config_before_the_driver_runs(activity):
    world = _world()
    svc = _service(world, config={"ldap_service_account_password_secret_ref": _VICTIM})

    with (
        patch(
            "astrolift_drivers.managed_resolution.resolve_managed_driver", side_effect=_driver_must_not_run
        ),
        pytest.raises(ManagedServicePreflightError, match=f"services/{world.org.guid}/"),
    ):
        activity(svc.pk)


def test_the_activity_refuses_a_google_secret_in_another_project_before_the_driver_runs():
    """Cloud Functions hands ``secret_environment`` to Google, so a config
    stored before the write-time check reaches no API: the preflight refuses
    a project other than the install's."""
    world = _world()
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name="GCP", slug="gcp", plugin_version="0.0.1")], ignore_conflicts=True
    )
    world.cluster.provider_plugin = ProviderPlugin.objects.get(slug="gcp")
    world.cluster.provider_config = {"project_id": "acme-prod", "region": "us-central1"}
    world.cluster.save(update_fields=["provider_plugin", "provider_config"])
    own = f"astrolift-services-{world.org.guid}-{world.app.guid}-api-token"
    svc = _service(
        world,
        kind=ManagedService.Kind.FAAS,
        name="fn",
        config={
            "secret_environment": [
                {"key": "API_TOKEN", "secret": own, "version": "1", "project_id": "victim-project"}
            ]
        },
    )

    with (
        patch(
            "astrolift_drivers.managed_resolution.resolve_managed_driver", side_effect=_driver_must_not_run
        ),
        pytest.raises(ManagedServicePreflightError, match="install project 'acme-prod'"),
    ):
        _provision_sync(svc.pk)


# ---- binding sync ---------------------------------------------------------------


def _sync(svc, binding):
    with patch(
        "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
        return_value=binding,
    ):
        return _sync_binding_rows(svc)


def test_sync_refuses_a_binding_ref_copied_from_any_config_field():
    """Copied from a field no config check names: the row check recognises
    the copy, whatever field it came from."""
    world = _world()
    svc = _service(world, config={"broker_password_location": _VICTIM})

    with pytest.raises(ValueError, match="comes from the event_stream service's config"):
        _sync(svc, Binding(env_vars={"EVENT_STREAM_PASSWORD": ValueRef(secret_ref=f"{_VICTIM}#password")}))

    assert not ManagedServiceBinding.objects.filter(managed_service=svc).exists()


def test_sync_refuses_every_ref_of_a_service_whose_config_names_a_secret_outside_the_org():
    """A driver may turn the value into another spelling (an ARN, a grant
    pattern), so a tainted config taints everything the service binds."""
    world = _world()
    svc = _service(world, config={"password_secret_ref": _VICTIM})
    ManagedServiceBinding.objects.create(
        managed_service=svc, env_key="EVENT_STREAM_BROKERS", env_value_ref="b-1:9092", is_secret=False
    )

    with pytest.raises(ValueError, match="config.password_secret_ref"):
        _sync(svc, Binding(env_vars={"EVENT_STREAM_PASSWORD": ValueRef(secret_ref=_MINTED)}))

    # Refused before the transaction: the last good rows are still there.
    assert list(
        ManagedServiceBinding.objects.filter(managed_service=svc).values_list("env_key", flat=True)
    ) == ["EVENT_STREAM_BROKERS"]


def test_sync_refuses_a_volume_credential_copied_from_the_config():
    world = _world()
    svc = _service(
        world,
        kind=ManagedService.Kind.FILESYSTEM,
        name="files",
        config={"share_credentials_location": _VICTIM},
    )
    volume = VolumeMount(
        name="files",
        mount_path="/data",
        source_kind=VolumeSourceKind.CSI,
        protocol="smb3",
        csi_driver="smb.csi.k8s.io",
        volume_handle="files##share",
        secret_refs={"username": f"{_VICTIM}#username", "password": f"{_VICTIM}#password"},
    )

    with pytest.raises(ValueError, match="volume files"):
        _sync(svc, Binding(env_vars={}, pod_volume_mounts=[volume]))

    assert not ManagedServiceVolumeBinding.objects.filter(managed_service=svc).exists()


def test_sync_refuses_a_ref_copied_from_a_config_naming_another_apps_secret():
    world = _world()
    theirs = _other_app_ns(world, "kafka")
    svc = _service(world, config={"password_secret_ref": f"{theirs}#password"})

    with pytest.raises(ValueError, match=f"services/{world.org.guid}/{world.app.guid}/"):
        _sync(svc, Binding(env_vars={"EVENT_STREAM_PASSWORD": ValueRef(secret_ref=f"{theirs}#password")}))

    assert not ManagedServiceBinding.objects.filter(managed_service=svc).exists()


def test_sync_writes_refs_the_driver_minted_and_config_refs_inside_the_apps_namespace():
    world = _world()
    own = f"services/{world.org.guid}/{world.app.guid}/kafka#password"
    svc = _service(world, config={"password_secret_ref": own})

    _sync(
        svc,
        Binding(
            env_vars={
                "EVENT_STREAM_PASSWORD": ValueRef(secret_ref=own),
                "MSK_ADMIN_PASSWORD": ValueRef(secret_ref=_MINTED),
            }
        ),
    )

    assert dict(
        ManagedServiceBinding.objects.filter(managed_service=svc).values_list("env_key", "env_value_ref")
    ) == {
        "EVENT_STREAM_PASSWORD": own,
        "MSK_ADMIN_PASSWORD": _MINTED,
    }


# ---- app deploy -----------------------------------------------------------------


class _Store:
    def __init__(self, store):
        self.store = dict(store)
        self.reads: list[str] = []

    def get(self, path):
        self.reads.append(path)
        return self.store.get(path)


class _ClusterDriver:
    def __init__(self):
        self.applied: list[dict] = []

    def apply_manifests(self, cluster_slug, namespace, manifests):
        self.applied.extend(manifests)
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])


@pytest.fixture
def deploy_drivers(monkeypatch):
    store = _Store({})
    cluster_driver = _ClusterDriver()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda _cluster, _capability: store)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda _deployment: (cluster_driver, SimpleNamespace(slug="dev-cluster"), "apps"),
    )
    return SimpleNamespace(store=store, cluster=cluster_driver)


def _deploy(world):
    from astrolift_workflows.activities.app_lifecycle import _update_secrets_sync

    deployment = Deployment.objects.create(
        registered_app=world.app,
        app_environment=world.env,
        trigger_kind=Deployment.TriggerKind.MANUAL,
        status=Deployment.Status.PENDING,
        image_tag="secret-refs-1921",
    )
    return _update_secrets_sync(deployment.pk)


@pytest.fixture
def world_and_planted_row():
    world = _world()
    svc = _service(world, config={"password_secret_ref": _VICTIM})
    ManagedServiceBinding.objects.create(
        managed_service=svc, env_key="EVENT_STREAM_PASSWORD", env_value_ref=_VICTIM, is_secret=True
    )
    return world, svc


def test_deploy_refuses_a_binding_row_planted_before_the_config_check(world_and_planted_row, deploy_drivers):
    world, _svc = world_and_planted_row
    deploy_drivers.store.store[_VICTIM] = {"value": "postgres://victim"}

    with pytest.raises(AppDeployError, match="EVENT_STREAM_PASSWORD"):
        _deploy(world)

    assert deploy_drivers.store.reads == []
    assert deploy_drivers.cluster.applied == []


def test_deploy_refuses_a_stored_row_copied_from_another_apps_namespace(deploy_drivers):
    """Stored under the org-wide ``services/<org guid>/`` rule of the previous
    round: the row names another app's secret, so it is never read."""
    world = _world()
    theirs = _other_app_ns(world, "db-password")
    svc = _service(world, config={"password_secret_ref": theirs})
    ManagedServiceBinding.objects.create(
        managed_service=svc, env_key="EVENT_STREAM_PASSWORD", env_value_ref=theirs, is_secret=True
    )
    deploy_drivers.store.store[theirs] = {"value": "billing-db-password"}

    with pytest.raises(AppDeployError, match="EVENT_STREAM_PASSWORD"):
        _deploy(world)

    assert deploy_drivers.store.reads == []
    assert deploy_drivers.cluster.applied == []


def test_deploy_resolves_a_row_the_driver_minted(deploy_drivers):
    world = _world()
    svc = _service(world)
    ManagedServiceBinding.objects.create(
        managed_service=svc, env_key="EVENT_STREAM_PASSWORD", env_value_ref=_MINTED, is_secret=True
    )
    deploy_drivers.store.store[_MINTED] = {"value": "own-password"}

    assert _deploy(world) == 1
    assert deploy_drivers.store.reads == [_MINTED]


def test_volume_credentials_copied_from_the_config_are_not_read():
    from astrolift_services.filesystem_bindings import (
        FilesystemBindingError,
        resolve_binding_secret_manifests,
    )

    world = _world()
    svc = _service(
        world,
        kind=ManagedService.Kind.FILESYSTEM,
        name="files",
        config={"mount_password_secret_ref": _VICTIM},
    )
    row = ManagedServiceVolumeBinding.objects.create(
        managed_service=svc,
        name="files",
        mount_path="/data",
        source_kind="csi",
        protocol="smb3",
        csi_driver="smb.csi.k8s.io",
        volume_handle="files##share",
        secret_refs={"password": _VICTIM},
    )
    store = _Store({_VICTIM: {"password": "victim"}})

    with pytest.raises(FilesystemBindingError, match="mount_password_secret_ref"):
        resolve_binding_secret_manifests([row], secrets_backend=store, namespace="apps", consumer_key="c")

    assert store.reads == []


# ---- workload identity grants ---------------------------------------------------


@dataclass
class _IdentityDriver:
    roles: list[tuple[str, list[dict[str, Any]]]]

    def create_identity_role(self, name, permissions):
        self.roles.append((name, permissions))
        return f"arn:aws:iam::123456789012:role/{name}"

    def bind_service_account(self, cluster, namespace, sa, role):
        return {"eks.amazonaws.com/role-arn": role}


def _ensure_identity(world, binding):
    from astrolift_workflows.activities.workload_identity import _ensure_workload_identity_sync

    driver = _IdentityDriver(roles=[])
    with (
        patch(
            "astrolift_workflows.activities.managed_service_lifecycle._managed_binding_for",
            return_value=binding,
        ),
        patch(
            "astrolift_workflows.activities.capability_deprovision._resolve_capability_driver",
            return_value=driver,
        ),
    ):
        _ensure_workload_identity_sync(world.app.pk, world.env.pk)
    return driver


def test_the_app_role_is_never_granted_a_secret_its_service_config_names_outside_the_org():
    """RDS Proxy grants GetSecretValue on the ARN AWS stored for the tenant's
    ``secret_arn``, which need not be spelled the way the config spelled it."""
    world = _world()
    _service(world, kind=ManagedService.Kind.DATABASE_PROXY, name="proxy", config={"secret_arn": _VICTIM_ARN})
    stored = "arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/rds/orders/master-Zz9Yx8"

    with pytest.raises(ValueError, match="config.secret_arn"):
        _ensure_identity(
            world, Binding(env_vars={}, iam_grants=[Grant(stored, ["secretsmanager:GetSecretValue"])])
        )


def test_the_app_role_keeps_grants_on_secrets_the_driver_minted():
    world = _world()
    _service(world, kind=ManagedService.Kind.DATABASE_PROXY, name="proxy")
    minted = "arn:aws:secretsmanager:us-west-2:123456789012:secret:astrolift/rds/proxy-1921/master-AbCdEf"

    driver = _ensure_identity(
        world, Binding(env_vars={}, iam_grants=[Grant(minted, ["secretsmanager:GetSecretValue"])])
    )

    [(_role, permissions)] = driver.roles
    assert permissions == [
        {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": minted}
    ]
