"""Tests for manifest-to-DB persistence (#120).

The persist layer is the bridge between the parsed manifest and the
runtime data model — it must be idempotent, additive on first run,
diff-based on subsequent runs, and tear down rows that disappeared
from the manifest. Each test pins one branch of that contract.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import Container, RegisteredApp, Workload
from astrolift_services.models import ManagedService, ManagedServiceAttachment

pytestmark = pytest.mark.django_db


def _scaffold():
    org = Organization.objects.create(name="Acme", slug="acme")
    team = Team.objects.create(organization=org, name="Eng", slug="eng")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug="demo")
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
    )
    return app


def _add_environment(app, name="production"):
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="manifest-test-provider",
        defaults={
            "name": "Manifest test provider",
            "plugin_version": "0.0.1",
            "capabilities_manifest": {},
            "config_schema": {},
        },
    )
    cluster = TenantCluster.objects.create(
        organization=app.organization,
        slug=f"manifest-test-{app.pk}-{name}",
        name=f"Manifest test {name}",
        provider_plugin=plugin,
        endpoint="https://example.invalid",
    )
    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name=name,
    )


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
    assert Workload.all_objects.filter(registered_app=app, slug="worker").count() == 1


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
    # #427: default concurrency policy persists as "forbid".
    assert w.concurrency_policy == "forbid"
    c = Container.objects.get(workload=w)
    assert c.command == ["/bin/run"]
    assert c.is_primary is True
    assert c.port == 0


def test_persist_round_trips_concurrency_policy_override():
    """A manifest that specifies ``concurrency_policy = "replace"``
    lands on the Workload row so the jobs UI can render the badge
    (#427)."""
    app = _scaffold()
    toml = """
name = "hello"

[[jobs]]
name = "rolling"
schedule = "*/5 * * * *"
command = ["/bin/run"]
concurrency_policy = "replace"
"""
    persist_manifest(app, _normalize(toml), raw_text=toml)
    w = Workload.objects.get(registered_app=app, slug="rolling")
    assert w.concurrency_policy == "replace"


def test_persist_updates_concurrency_policy_on_existing_row():
    """Edit-flow: a Workload row already exists; re-persisting the
    same manifest with a flipped concurrency_policy updates the row
    (rather than soft-deleting + recreating)."""
    app = _scaffold()
    before = """
name = "hello"

[[jobs]]
name = "rolling"
schedule = "*/5 * * * *"
command = ["/bin/run"]
"""
    persist_manifest(app, _normalize(before), raw_text=before)
    after = """
name = "hello"

[[jobs]]
name = "rolling"
schedule = "*/5 * * * *"
command = ["/bin/run"]
concurrency_policy = "queue"
"""
    result = persist_manifest(app, _normalize(after), raw_text=after)
    assert result.workloads_updated == 1
    w = Workload.objects.get(registered_app=app, slug="rolling")
    assert w.concurrency_policy == "queue"


def test_persist_materializes_app_and_project_managed_services(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "astrolift_services.managed_service_catalog.resolve_variant",
        lambda **kwargs: SimpleNamespace(variant=kwargs.get("requested_variant") or "resolved-default"),
    )
    monkeypatch.setattr("astrolift_services.managed_service_catalog.validate_config", lambda *_: None)
    app = _scaffold()
    env = _add_environment(app)
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
  [[workloads.containers]]
  name = "web"

[[managed_services]]
kind = "postgres"
name = "private-db"
environment = "production"
bind_workloads = ["web"]
size = "medium"

[[managed_services]]
kind = "redis"
name = "shared-cache"
owner_scope = "project"
environment = "production"
"""

    from astrolift_manifest.persist import allow_project_attach

    with allow_project_attach():
        result = persist_manifest(app, _normalize(toml), raw_text=toml)

    private = ManagedService.objects.get(registered_app=app, name="private-db")
    assert private.app_environment == env
    assert private.manifest_managed is True
    assert private.bind_workloads == ["web"]
    assert private.config["size"] == "medium"
    shared = ManagedService.objects.get(project=app.project, name="shared-cache")
    attachment = ManagedServiceAttachment.objects.get(managed_service=shared, app_environment=env)
    assert shared.manifest_managed is True
    assert attachment.manifest_managed is True
    assert attachment.workload_names == ["*"]
    assert result.managed_services_created == 2
    assert result.managed_service_attachments_created == 1


def test_removing_manifest_service_enqueues_non_destructive_teardown(
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "astrolift_services.managed_service_catalog.resolve_variant",
        lambda **kwargs: SimpleNamespace(variant=kwargs.get("requested_variant") or "resolved-default"),
    )
    monkeypatch.setattr("astrolift_services.managed_service_catalog.validate_config", lambda *_: None)
    started = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, *, args, workflow_id: started.append((name, args[0], workflow_id)),
    )
    app = _scaffold()
    _add_environment(app)
    with_service = """
name = "hello"
[[managed_services]]
kind = "postgres"
name = "records"
deletion_policy = "retain"
"""
    without_service = 'name = "hello"\n'

    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(with_service), raw_text=with_service)
    started.clear()
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(without_service), raw_text=without_service)

    name, workflow_input, _ = started[-1]
    assert name == "DeprovisionManagedServiceWorkflow"
    assert workflow_input.delete_data is False
    assert workflow_input.force_destroy is False


# ---------------------------------------------------------------------
# A lost provision enqueue has to be retried (#1688)
# ---------------------------------------------------------------------
#
# ``_enqueue_manifest_workflow`` swallows a start failure on purpose --
# "desired state remains retryable" -- but nothing retried it. Enqueueing
# happened only when the row was created or its manifest block changed,
# so a first enqueue lost to a queue outage or a stopped worker was lost
# for good: every later deploy re-reconciled, saw no change, and enqueued
# nothing. The row kept backend_ref="" forever, no driver call was ever
# attempted, and the app's binding secret stayed empty.


_PG_MANIFEST = """
name = "hello"
[[managed_services]]
kind = "postgres"
name = "records"
"""


def _catalog(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "astrolift_services.managed_service_catalog.resolve_variant",
        lambda **kwargs: SimpleNamespace(variant=kwargs.get("requested_variant") or "resolved-default"),
    )
    monkeypatch.setattr("astrolift_services.managed_service_catalog.validate_config", lambda *_: None)


def _provision_names(started):
    return [n for n, _i, _w in started if n == "ProvisionManagedServiceWorkflow"]


def test_an_unprovisioned_service_is_re_enqueued_on_the_next_deploy(
    monkeypatch, django_capture_on_commit_callbacks
):
    """The bug: the second deploy enqueued nothing, forever."""

    _catalog(monkeypatch)
    started = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, *, args, workflow_id: started.append((name, args[0], workflow_id)),
    )
    app = _scaffold()
    _add_environment(app)

    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)
    assert _provision_names(started) == ["ProvisionManagedServiceWorkflow"]

    # Same manifest again, row still never provisioned.
    started.clear()
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)

    assert _provision_names(started) == ["ProvisionManagedServiceWorkflow"]


def test_a_provisioned_service_is_not_re_enqueued(monkeypatch, django_capture_on_commit_callbacks):
    """Once it has a backend_ref there is nothing to retry, and
    re-enqueueing would restart work on every deploy."""

    from astrolift_services.models import ManagedService

    _catalog(monkeypatch)
    started = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, *, args, workflow_id: started.append((name, args[0], workflow_id)),
    )
    app = _scaffold()
    _add_environment(app)
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)

    ManagedService.objects.filter(registered_app=app).update(
        backend_ref="postgres/records", status=ManagedService.Status.ACTIVE
    )
    started.clear()
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)

    assert _provision_names(started) == []


def test_a_run_already_in_flight_is_not_restarted(monkeypatch, django_capture_on_commit_callbacks):
    """start_workflow reuses the id under TERMINATE_IF_RUNNING, so
    re-enqueueing a PROVISIONING row would kill and restart it on every
    deploy."""

    from astrolift_services.models import ManagedService

    _catalog(monkeypatch)
    started = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, *, args, workflow_id: started.append((name, args[0], workflow_id)),
    )
    app = _scaffold()
    _add_environment(app)
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)

    ManagedService.objects.filter(registered_app=app).update(status=ManagedService.Status.PROVISIONING)
    started.clear()
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)

    assert _provision_names(started) == []


def test_a_failed_provision_is_retried(monkeypatch, django_capture_on_commit_callbacks):
    """The operator's normal loop is fix-and-push; the next deploy should
    pick a failed service back up."""

    from astrolift_services.models import ManagedService

    _catalog(monkeypatch)
    started = []
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        lambda name, *, args, workflow_id: started.append((name, args[0], workflow_id)),
    )
    app = _scaffold()
    _add_environment(app)
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)

    ManagedService.objects.filter(registered_app=app).update(status=ManagedService.Status.FAILED)
    started.clear()
    with django_capture_on_commit_callbacks(execute=True):
        persist_manifest(app, _normalize(_PG_MANIFEST), raw_text=_PG_MANIFEST)

    assert _provision_names(started) == ["ProvisionManagedServiceWorkflow"]


# ---------------------------------------------------------------------
# Deferred declarations and project scope without a project (#1759)
# ---------------------------------------------------------------------

_PROJECT_SCOPED = """
name = "hello"
[[managed_services]]
kind = "postgres"
name = "shared"
owner_scope = "project"
"""


def test_declarations_on_an_app_with_no_environment_come_back_deferred(monkeypatch):
    """Nothing is reconciled without an environment, but a caller that
    gates bindings has to see what the bootstrap will reconcile later."""
    _catalog(monkeypatch)
    app = _scaffold()

    result = persist_manifest(app, _normalize(_PROJECT_SCOPED), raw_text=_PROJECT_SCOPED)

    assert [(s.owner_scope, s.kind, s.name) for s in result.managed_services_deferred] == [
        ("project", "postgres", "shared")
    ]
    assert result.managed_service_changes == []
    assert not ManagedService.objects.exists()


def test_a_project_scoped_service_on_an_app_with_no_project_is_refused_before_any_lookup(monkeypatch):
    """project=None matched other orgs' app-private rows of the same kind
    and name, and creating one broke the owner-scope CHECK constraint."""
    _catalog(monkeypatch)
    app = _scaffold()
    app.project = None
    app.save(update_fields=["project"])
    _add_environment(app)
    other_org = Organization.objects.create(name="Other", slug="other")
    other_team = Team.objects.create(organization=other_org, name="T", slug="t-other")
    other_app = RegisteredApp.objects.create(
        organization=other_org, team=other_team, name="Theirs", slug="theirs"
    )
    ManagedService.objects.create(
        registered_app=other_app, app_environment=_add_environment(other_app), kind="postgres", name="shared"
    )

    with pytest.raises(ValueError, match="belongs to no project"):
        persist_manifest(app, _normalize(_PROJECT_SCOPED), raw_text=_PROJECT_SCOPED)

    assert not ManagedServiceAttachment.objects.exists()
    assert ManagedService.objects.count() == 1


def test_project_services_are_withheld_unless_the_caller_may_attach_them(monkeypatch):
    """A repo sync, webhook or CI bootstrap has no actor to check, so a
    manifest cannot create, attach or rebind a project managed service there
    (#1966); an authorized caller opts in with ``allow_project_attach``."""
    from types import SimpleNamespace

    from astrolift_manifest.persist import allow_project_attach
    from astrolift_services.models import ManagedService, ManagedServiceAttachment

    monkeypatch.setattr(
        "astrolift_services.managed_service_catalog.resolve_variant",
        lambda **kwargs: SimpleNamespace(variant=kwargs.get("requested_variant") or "resolved-default"),
    )
    monkeypatch.setattr("astrolift_services.managed_service_catalog.validate_config", lambda *_: None)
    app = _scaffold()
    env = _add_environment(app)
    toml = """
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"
  [[workloads.containers]]
  name = "web"

[[managed_services]]
kind = "redis"
name = "shared-cache"
owner_scope = "project"
environment = "production"
bind_workloads = ["web"]
"""

    withheld = persist_manifest(app, _normalize(toml), raw_text=toml)

    assert withheld.project_changes_withheld == ["create project:redis/shared-cache"]
    assert not ManagedService.objects.filter(project=app.project).exists()

    with allow_project_attach():
        persist_manifest(app, _normalize(toml), raw_text=toml)
    shared = ManagedService.objects.get(project=app.project, name="shared-cache")
    attachment = ManagedServiceAttachment.objects.get(managed_service=shared, app_environment=env)
    assert attachment.workload_names == ["web"]

    rebind = toml.replace('bind_workloads = ["web"]', 'bind_workloads = ["*"]')
    result = persist_manifest(app, _normalize(rebind), raw_text=rebind)

    assert result.project_changes_withheld == [f"rebind project:redis/shared-cache@{env.name}"]
    attachment.refresh_from_db()
    assert attachment.workload_names == ["web"]
    assert attachment.deleted_at is None
