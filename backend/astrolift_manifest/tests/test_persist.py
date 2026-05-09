"""Tests for manifest-to-DB persistence (#120).

The persist layer is the bridge between the parsed manifest and the
runtime data model — it must be idempotent, additive on first run,
diff-based on subsequent runs, and tear down rows that disappeared
from the manifest. Each test pins one branch of that contract.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization, Project, Team
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import Container, RegisteredApp, Workload

pytestmark = pytest.mark.django_db


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(
        organization=org, team=team, name="Demo", slug="demo"
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
    )
    return app


def _normalize(toml: str):
    return normalize(parse_raw(toml), defaults=NormalizationDefaults())


SIMPLE_MANIFEST = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
replicas = 2

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

    [workloads.containers.healthcheck]
    kind = "http"
    value = "/healthz"
"""


def test_persist_creates_workload_and_container_rows():
    app = _scaffold()
    result = persist_manifest(app, _normalize(SIMPLE_MANIFEST), raw_text=SIMPLE_MANIFEST)
    assert result.workloads_created == 1
    assert result.containers_created == 1
    assert result.hash_changed is True

    w = Workload.objects.get(registered_app=app)
    assert w.kind == "deployment"
    assert w.replicas == 2
    c = Container.objects.get(workload=w)
    assert c.is_primary is True
    assert c.port == 8080
    assert c.healthcheck_kind == "http"
    assert c.healthcheck_value == "/healthz"


def test_persist_writes_hash_and_normalized_json_to_app():
    app = _scaffold()
    persist_manifest(app, _normalize(SIMPLE_MANIFEST), raw_text=SIMPLE_MANIFEST)

    app.refresh_from_db()
    assert app.manifest_raw == SIMPLE_MANIFEST
    assert app.manifest_hash != ""
    # Stored normalized JSON round-trips with hash determinism.
    from astrolift_manifest.normalize import manifest_hash

    assert manifest_hash(app.manifest_normalized) == app.manifest_hash


def test_persist_is_idempotent_for_unchanged_manifest():
    """Re-applying the same manifest must not create or update rows
    a second time. This is the cheap short-circuit the platform
    relies on to skip downstream sync work on most pushes."""
    app = _scaffold()
    persist_manifest(app, _normalize(SIMPLE_MANIFEST), raw_text=SIMPLE_MANIFEST)

    second = persist_manifest(app, _normalize(SIMPLE_MANIFEST), raw_text=SIMPLE_MANIFEST)
    assert second.workloads_created == 0
    assert second.workloads_updated == 0
    assert second.containers_created == 0
    assert second.containers_updated == 0
    assert second.hash_changed is False
    assert second.changed is False


def test_persist_updates_changed_workload_fields():
    app = _scaffold()
    persist_manifest(app, _normalize(SIMPLE_MANIFEST), raw_text=SIMPLE_MANIFEST)

    new_toml = SIMPLE_MANIFEST.replace("replicas = 2", "replicas = 5")
    result = persist_manifest(app, _normalize(new_toml), raw_text=new_toml)
    assert result.workloads_updated == 1
    assert result.workloads_created == 0
    assert result.hash_changed is True

    w = Workload.objects.get(registered_app=app)
    assert w.replicas == 5


def test_persist_soft_deletes_removed_workloads():
    app = _scaffold()
    two_workloads = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

[[workloads]]
name = "worker"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
"""
    persist_manifest(app, _normalize(two_workloads), raw_text=two_workloads)
    assert Workload.objects.filter(registered_app=app, deleted_at__isnull=True).count() == 2

    # Drop the worker, keep web.
    one_workload = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""
    result = persist_manifest(app, _normalize(one_workload), raw_text=one_workload)
    assert result.workloads_soft_deleted == 1

    active = Workload.objects.filter(registered_app=app, deleted_at__isnull=True)
    assert [w.slug for w in active] == ["web"]
    # The dropped row is gone from the active queryset but still in
    # ``all_objects`` (audit trail).
    assert Workload.all_objects.filter(
        registered_app=app, slug="worker"
    ).count() == 1


def test_persist_soft_deletes_removed_container():
    app = _scaffold()
    multi_container = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

  [[workloads.containers]]
  name = "sidecar"
  is_primary = false
"""
    persist_manifest(app, _normalize(multi_container), raw_text=multi_container)
    assert Container.objects.filter(deleted_at__isnull=True).count() == 2

    single = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080
"""
    result = persist_manifest(app, _normalize(single), raw_text=single)
    assert result.containers_soft_deleted == 1
    assert Container.objects.filter(deleted_at__isnull=True).count() == 1


def test_persist_round_trips_jobs_into_workload_rows():
    """[[jobs]] desugars at parse time → persisted as cronjob
    Workload rows. This is the integration check between #115 and
    #120: same DB shape as a bare cronjob."""
    app = _scaffold()
    toml = """
name = "hello"

[[jobs]]
name = "nightly"
schedule = "0 0 * * *"
command = ["/bin/run"]
"""
    persist_manifest(app, _normalize(toml), raw_text=toml)
    w = Workload.objects.get(registered_app=app, slug="nightly")
    assert w.kind == "cronjob"
    assert w.schedule == "0 0 * * *"
    c = Container.objects.get(workload=w)
    assert c.command == ["/bin/run"]
    assert c.is_primary is True
    assert c.port == 0
