"""
Lifecycle test fixtures.

Builds the org / project / team / app / env / cluster scaffolding that
every deployment test needs in one place. Each fixture is composable
via standard pytest dependency injection.

The Temporal client is patched out by default (``ASTROLIFT_TEMPORAL_ENABLED=False``
via the ``no_temporal`` fixture) — tests opt in to recording
``start_workflow`` calls through ``temporal_recorder`` so they can
assert what would have been enqueued without running a Temporal
server.

OpenSearch profile indexing is suppressed via ``_no_opensearch_profile_index``
(autouse) — creating a Django User triggers a Profile post_save that
tries to write to OpenSearch, which is expensive and flaky in tests.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp


@pytest.fixture(autouse=True)
def _no_opensearch_profile_index(monkeypatch):
    """Silence the User → Profile → OpenSearch indexing chain."""
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile",
        classmethod(lambda cls, profile: None),
    )
    monkeypatch.setattr(
        "core.documents.ProfileDocument.delete_profile",
        classmethod(lambda cls, profile_gid: None),
    )


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-test")


@pytest.fixture
def team(org):
    return Team.objects.create(organization=org, name="Platform", slug="platform")


@pytest.fixture
def project(org, team):
    return Project.objects.create(organization=org, team=team, name="Demo", slug="demo")


@pytest.fixture
def actor():
    User = get_user_model()
    return User.objects.create(username="actor@test", email="actor@test")


@pytest.fixture
def other_actor():
    User = get_user_model()
    return User.objects.create(username="approver@test", email="approver@test")


@pytest.fixture
def provider_plugin():
    # Seeded directly; the plugin row is scaffolding for this test.
    plugin = ProviderPlugin(
        name="Test Provider",
        slug="test-provider",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return ProviderPlugin.objects.get(slug="test-provider")


@pytest.fixture
def cluster(org, provider_plugin):
    return TenantCluster.objects.create(
        organization=org,
        name="dev-cluster",
        slug="dev-cluster",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://dev.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
        # Deploy/lifecycle tests rely on this cluster being a valid
        # deploy target; the registerApp + count gates require
        # ``lifecycle = "managed"`` (#316).
        lifecycle=TenantCluster.Lifecycle.MANAGED.value,
    )


@pytest.fixture
def app(org, project, team):
    return RegisteredApp.objects.create(
        organization=org,
        project=project,
        team=team,
        name="Hello",
        slug="hello-app",
        provisioning_status="ready",
    )


@pytest.fixture
def env(app, cluster):
    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="prod",
        url="https://hello.example.com",
        required_approvals=0,
    )


@pytest.fixture
def env_requires_approval(app, cluster):
    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="staging",
        url="https://hello.staging.example.com",
        required_approvals=1,
    )


@pytest.fixture
def fake_info(actor):
    """Minimal `info`-shaped object accepted by every mutation resolver."""
    request = SimpleNamespace(user=actor)
    context = SimpleNamespace(request=request)
    return SimpleNamespace(context=context)


@pytest.fixture
def fake_info_other(other_actor):
    request = SimpleNamespace(user=other_actor)
    context = SimpleNamespace(request=request)
    return SimpleNamespace(context=context)


@pytest.fixture
def no_temporal(settings):
    """Force the temporal client into disabled (no-op) mode."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = False
    yield


@dataclasses.dataclass
class TemporalRecorder:
    starts: list[tuple[str, list[Any], str]] = dataclasses.field(default_factory=list)
    signals: list[tuple[str, str, tuple[Any, ...]]] = dataclasses.field(default_factory=list)
    terminates: list[tuple[str, str]] = dataclasses.field(default_factory=list)


@pytest.fixture
def temporal_recorder(monkeypatch, settings):
    """Replace the temporal client facade with an in-memory recorder."""
    settings.ASTROLIFT_TEMPORAL_ENABLED = True

    rec = TemporalRecorder()

    def _start(name, args, *, workflow_id, task_queue=None):
        rec.starts.append((name, list(args), workflow_id))
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(workflow_id=workflow_id, run_id=f"run-{len(rec.starts)}", enqueued=True)

    def _signal(workflow_id, signal_name, *args):
        rec.signals.append((workflow_id, signal_name, args))
        return True

    def _terminate(workflow_id, reason):
        rec.terminates.append((workflow_id, reason))
        return True

    # The control-plane mutations live in a per-feature mixin package; each
    # submodule imports the Temporal client fns by name, so the recorder must
    # be bound into every submodule that references them (patching a single
    # module no longer reaches every resolver).
    import importlib
    import pkgutil

    import astrolift_lifecycle.schema.mutations as _mutpkg

    _wf = {"start_workflow": _start, "signal_workflow": _signal, "terminate_workflow": _terminate}
    for _sub in pkgutil.iter_modules(_mutpkg.__path__):
        _m = importlib.import_module(f"astrolift_lifecycle.schema.mutations.{_sub.name}")
        for _name, _fn in _wf.items():
            if hasattr(_m, _name):
                monkeypatch.setattr(_m, _name, _fn)

    # Deploy starts are now registered via ``transaction.on_commit`` so the
    # worker never races an uncommitted deployment row (#1025). Under the
    # ``django_db`` outer transaction those callbacks would otherwise never
    # fire, so run them at registration time to model the production commit —
    # this keeps the recorder observing exactly the starts a real commit would
    # enqueue.
    def _run_on_commit(func, using=None):
        func()

    monkeypatch.setattr(transaction, "on_commit", _run_on_commit)
    return rec
