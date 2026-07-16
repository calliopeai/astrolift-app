"""Probe/init/volume exposure on workload detail (#739).

Tests that:
* The manifest parser parses ``[[workloads.<name>.volumes]]`` into the
  ``volumes`` tuple on ``WorkloadManifest``.
* Invalid volume entries surface as ``ManifestError``.
* ``persist_manifest`` writes volumes to the Workload row on create
  and detects changes on subsequent persists.
* The GQL ``astroliftWorkload`` query returns probe fields on
  Container and the ``volumes`` JSON on Workload.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Team
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import Container, RegisteredApp, Workload
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Manifest parser — volume parsing
# ---------------------------------------------------------------------------

_MANIFEST_WITH_PVC = """
name = "vol-test"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.volumes]]
  name = "data"
  kind = "pvc"
  mount_path = "/data"
  size = "10Gi"
  storage_class = "standard"
  access_mode = "ReadWriteOnce"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""


def test_parser_volume_pvc():
    raw = parse_raw(_MANIFEST_WITH_PVC)
    w = raw.workloads[0]
    assert len(w.volumes) == 1
    v = w.volumes[0]
    assert v["name"] == "data"
    assert v["kind"] == "pvc"
    assert v["mount_path"] == "/data"
    assert v["size"] == "10Gi"
    assert v["storage_class"] == "standard"
    assert v["access_mode"] == "ReadWriteOnce"


_MANIFEST_WITH_EMPTY_DIR = """
name = "vol-test"

[[workloads]]
name = "worker"
kind = "deployment"

  [[workloads.volumes]]
  name = "scratch"
  kind = "empty_dir"
  mount_path = "/tmp/work"

  [[workloads.containers]]
  name = "runner"
  is_primary = true
"""


def test_parser_volume_empty_dir():
    raw = parse_raw(_MANIFEST_WITH_EMPTY_DIR)
    w = raw.workloads[0]
    assert len(w.volumes) == 1
    v = w.volumes[0]
    assert v["kind"] == "empty_dir"
    assert v["mount_path"] == "/tmp/work"


def test_parser_volume_invalid_kind():
    toml = """
name = "test"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.volumes]]
  name = "data"
  kind = "nfs"
  mount_path = "/data"

  [[workloads.containers]]
  name = "app"
  is_primary = true
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "volumes[0]" in str(exc.value)


def test_parser_volume_pvc_missing_size():
    toml = """
name = "test"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.volumes]]
  name = "data"
  kind = "pvc"
  mount_path = "/data"

  [[workloads.containers]]
  name = "app"
  is_primary = true
"""
    with pytest.raises(ManifestError) as exc:
        parse_raw(toml)
    assert "size" in str(exc.value)


def test_parser_no_volumes_defaults_to_empty_tuple():
    toml = """
name = "test"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
"""
    raw = parse_raw(toml)
    assert raw.workloads[0].volumes == ()


# ---------------------------------------------------------------------------
# persist_manifest — volume persistence
# ---------------------------------------------------------------------------


def _scaffold_app(suffix: str = ""):
    org = Organization.objects.create(name="Acme", slug=f"vol-acme{suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"vol-eng{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"vol-demo{suffix}")
    return RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug=f"vol-hello{suffix}",
        provisioning_status="ready",
    )


def _norm(toml: str):
    return normalize(parse_raw(toml), defaults=NormalizationDefaults())


def test_persist_writes_volumes_on_create():
    app = _scaffold_app("-c")
    persist_manifest(app, _norm(_MANIFEST_WITH_PVC))
    w = Workload.objects.get(registered_app=app, slug="web")
    assert len(w.volumes) == 1
    assert w.volumes[0]["name"] == "data"
    assert w.volumes[0]["size"] == "10Gi"


def test_persist_detects_volume_change():
    app = _scaffold_app("-u")
    persist_manifest(app, _norm(_MANIFEST_WITH_PVC))

    updated = _MANIFEST_WITH_PVC.replace('size = "10Gi"', 'size = "20Gi"')
    result = persist_manifest(app, _norm(updated))
    assert result.workloads_updated == 1

    w = Workload.objects.get(registered_app=app, slug="web")
    assert w.volumes[0]["size"] == "20Gi"


def test_persist_idempotent_with_unchanged_volumes():
    app = _scaffold_app("-i")
    persist_manifest(app, _norm(_MANIFEST_WITH_PVC))
    result = persist_manifest(app, _norm(_MANIFEST_WITH_PVC))
    assert result.workloads_updated == 0


# ---------------------------------------------------------------------------
# GQL — probe fields on Container, volumes on Workload
# ---------------------------------------------------------------------------


def _info(user=None):
    return SimpleNamespace(context=SimpleNamespace(user=user))


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


def _gql_scaffold(suffix: str = ""):
    User = get_user_model()
    user = User.objects.create(username=f"vol-user{suffix}", email=f"vol{suffix}@test")
    org = Organization.objects.create(name="Gql Org", slug=f"vol-gql-org{suffix}")
    team = Team.objects.create(organization=org, name="Gql Team", slug=f"vol-gql-team{suffix}")
    project = Project.objects.create(
        organization=org, team=team, name="Gql Proj", slug=f"vol-gql-proj{suffix}"
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="GqlApp",
        slug=f"vol-gql-app{suffix}",
        provisioning_status="ready",
    )
    return user, org, app


def test_workload_gql_returns_volumes(permission_resolver):
    user, org, app = _gql_scaffold("-wv")
    persist_manifest(app, _norm(_MANIFEST_WITH_PVC))
    permission_resolver.grant(Permission.APP_READ)

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        result = RegistryQuery().astrolift_workload(info=_info(user), app_slug=app.slug, slug="web")

    assert result is not None
    assert len(result.volumes) == 1
    assert result.volumes[0]["name"] == "data"


def test_container_gql_has_probe_fields(permission_resolver):
    user, org, app = _gql_scaffold("-cp")
    persist_manifest(app, _norm(_MANIFEST_WITH_PVC))
    permission_resolver.grant(Permission.APP_READ)

    w = Workload.objects.get(registered_app=app, slug="web")
    probe_data = {
        "initialDelaySeconds": 5,
        "periodSeconds": 10,
        "httpGet": {"path": "/healthz", "port": 8080},
    }
    c = Container.objects.get(workload=w)
    c.startup_probe = probe_data
    c.readiness_probe = {"initialDelaySeconds": 3, "periodSeconds": 5}
    c.save()

    with tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id)):
        containers = RegistryQuery().astrolift_containers(info=_info(user), workload_slug="web")

    assert len(containers) == 1
    ct = containers[0]
    assert ct.startup_probe == probe_data
    assert ct.readiness_probe is not None
    assert ct.liveness_probe is None
