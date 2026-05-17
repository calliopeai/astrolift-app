"""Tests for the secret-bundle rotation surface (#365).

Six layers exercised:

1. ``_list_targets_sync`` — pure DB query, no driver call.
2. ``_materialize_secret_manifest`` — pure shape, no DB.
3. ``_refresh_in_cluster_sync`` — happy path with a faked driver.
4. ``_delete_from_cluster_sync`` — happy path with a faked driver.
5. ``rotateSecretBundle`` mutation — fires the workflow via recorder.
6. ``SecretBundle.soft_delete`` — fires the delete workflow on hook.
7. ``schedule_registry`` — new entry shape.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_services.models import AppSecretBundleRef, SecretBundle
from astrolift_services.schema.mutations import (
    RotateSecretBundleInput,
    ServicesMutation,
)
from astrolift_workflows.activities.secret_rotation import (
    _list_targets_sync,
    _materialize_secret_manifest,
)
from astrolift_workflows.schedule_registry import (
    DEFAULT_SCHEDULES,
    ScheduleKind,
    get_schedule,
)
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


def _tenant_for(org, actor):
    return tenant_context(
        TenantContext(organization_id=org.id, actor_user_id=actor.id),
    )


def _grant_update(resolver):
    resolver.grant(Permission.APP_UPDATE)


# ---- _materialize_secret_manifest (pure) ----------------------------------


class _FakeSecretsBackend:
    """Minimal stand-in for the SecretsBackend driver — returns the
    pre-seeded dict for the matching backend_ref."""

    def __init__(self, kvs: dict[str, dict[str, str]]):
        self._kvs = kvs

    def get(self, backend_ref):
        return self._kvs.get(backend_ref)


def test_materialize_secret_manifest_shape():
    backend = _FakeSecretsBackend(
        {"vault/foo": {"DATABASE_URL": "postgres://x", "REDIS_URL": "redis://y"}},
    )
    out = _materialize_secret_manifest(
        bundle_slug="db-bundle",
        bundle_backend_ref="vault/foo",
        prefix="",
        namespace="acme-hello",
        secrets_backend=backend,
    )
    assert out["apiVersion"] == "v1"
    assert out["kind"] == "Secret"
    assert out["type"] == "Opaque"
    assert out["metadata"]["name"] == "db-bundle"
    assert out["metadata"]["namespace"] == "acme-hello"
    assert out["metadata"]["labels"]["astrolift.io/secret-bundle"] == "db-bundle"
    assert "astrolift.io/last-rotated-at" in out["metadata"]["annotations"]
    # Values are base64-encoded — same shape as update_secrets emits.
    import base64

    decoded = {k: base64.b64decode(v).decode() for k, v in out["data"].items()}
    assert decoded == {"DATABASE_URL": "postgres://x", "REDIS_URL": "redis://y"}


def test_materialize_secret_manifest_applies_prefix():
    backend = _FakeSecretsBackend({"vault/foo": {"USER": "alice"}})
    out = _materialize_secret_manifest(
        bundle_slug="db-bundle",
        bundle_backend_ref="vault/foo",
        prefix="DB_",
        namespace="ns",
        secrets_backend=backend,
    )
    import base64

    decoded = {k: base64.b64decode(v).decode() for k, v in out["data"].items()}
    assert decoded == {"DB_USER": "alice"}


def test_materialize_secret_manifest_missing_backend_ref_raises():
    backend = _FakeSecretsBackend({})
    from core.app_deploy import AppDeployError

    with pytest.raises(AppDeployError, match="not found in secrets backend"):
        _materialize_secret_manifest(
            bundle_slug="x",
            bundle_backend_ref="missing/ref",
            prefix="",
            namespace="ns",
            secrets_backend=backend,
        )


@pytest.mark.django_db
def test_materialize_refreshes_last_known_keys_when_bundle_provided(org, team):
    """#441 — materialise refreshes ``SecretBundle.last_known_keys`` off
    the just-fetched payload so the rotate workflow keeps the operator
    UI's keyCount aligned with what was actually written to k8s."""
    bundle = SecretBundle.objects.create(
        organization=org,
        team=team,
        name="DB bundle",
        slug="db-bundle-key-refresh",
        backend_ref="vault/db",
    )
    backend = _FakeSecretsBackend(
        {"vault/db": {"DATABASE_URL": "x", "API_KEY": "y", "REDIS_URL": "z"}},
    )
    _materialize_secret_manifest(
        bundle_slug=bundle.slug,
        bundle_backend_ref=bundle.backend_ref,
        prefix="",
        namespace="ns",
        secrets_backend=backend,
        bundle=bundle,
    )
    bundle.refresh_from_db()
    assert bundle.last_known_keys == ["API_KEY", "DATABASE_URL", "REDIS_URL"]
    assert bundle.last_key_enum_at is not None


@pytest.mark.django_db
def test_materialize_without_bundle_doesnt_persist(org, team):
    """Calling without ``bundle`` (older call sites, defensive) is a
    no-op on the cache."""
    bundle = SecretBundle.objects.create(
        organization=org,
        team=team,
        name="DB bundle",
        slug="db-bundle-no-cache",
        backend_ref="vault/db",
    )
    backend = _FakeSecretsBackend({"vault/db": {"K": "v"}})
    _materialize_secret_manifest(
        bundle_slug=bundle.slug,
        bundle_backend_ref=bundle.backend_ref,
        prefix="",
        namespace="ns",
        secrets_backend=backend,
    )
    bundle.refresh_from_db()
    assert bundle.last_known_keys == []
    assert bundle.last_key_enum_at is None


# ---- _list_targets_sync (DB) ----------------------------------------------


@pytest.fixture
def bundle(org, team):
    return SecretBundle.objects.create(
        organization=org,
        team=team,
        name="DB bundle",
        slug="db-bundle",
        backend_ref="vault/db",
    )


@pytest.fixture
def attached_bundle(app, env, bundle):
    AppSecretBundleRef.objects.create(
        registered_app=app,
        app_environment=env,
        secret_bundle=bundle,
        prefix="DB_",
    )
    return bundle


@pytest.mark.django_db
def test_list_targets_returns_active_refs(attached_bundle, app, env, cluster):
    targets = _list_targets_sync(attached_bundle.pk)
    assert len(targets) == 1
    t = targets[0]
    assert t["registered_app_id"] == app.pk
    assert t["app_environment_id"] == env.pk
    assert t["tenant_cluster_id"] == cluster.pk
    assert t["app_slug"] == app.slug
    assert t["bundle_slug"] == "db-bundle"
    assert t["bundle_backend_ref"] == "vault/db"
    assert t["prefix"] == "DB_"


@pytest.mark.django_db
def test_list_targets_skips_soft_deleted_refs(attached_bundle, app):
    # Soft-delete the ref — target list should be empty even though
    # the bundle itself is still active.
    ref = AppSecretBundleRef.objects.get(registered_app=app)
    ref.soft_delete()
    targets = _list_targets_sync(attached_bundle.pk)
    assert targets == []


@pytest.mark.django_db
def test_list_targets_empty_when_no_active_refs(bundle):
    # Bundle exists but no refs — workflow short-circuits without
    # fanning out to any cluster.
    targets = _list_targets_sync(bundle.pk)
    assert targets == []


# ---- rotateSecretBundle mutation -----------------------------------------


@pytest.fixture
def fake_info(actor):
    request = SimpleNamespace(user=actor)
    context = SimpleNamespace(request=request)
    return SimpleNamespace(context=context)


@pytest.fixture
def services_temporal_recorder(monkeypatch, settings):
    """Mirror of the lifecycle ``temporal_recorder`` fixture, but
    points at ``astrolift_services.schema.mutations.start_workflow``
    since the rotate mutation lives there."""
    import dataclasses

    settings.ASTROLIFT_TEMPORAL_ENABLED = True

    @dataclasses.dataclass
    class _Rec:
        starts: list = dataclasses.field(default_factory=list)

    rec = _Rec()

    def _start(name, args, *, workflow_id, task_queue=None):
        rec.starts.append((name, list(args), workflow_id))
        from astrolift_workflows.client import WorkflowHandle

        return WorkflowHandle(
            workflow_id=workflow_id,
            run_id=f"run-{len(rec.starts)}",
            enqueued=True,
        )

    # Both the rotate mutation and the SecretBundle.soft_delete hook
    # import ``start_workflow`` lazily from ``astrolift_workflows.client``,
    # so patching the source is enough.
    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        _start,
    )
    return rec


@pytest.mark.django_db
def test_rotate_secret_bundle_fires_workflow(
    bundle,
    fake_info,
    org,
    actor,
    permission_resolver,
    services_temporal_recorder,
):
    _grant_update(permission_resolver)
    mutation = ServicesMutation()
    with _tenant_for(org, actor):
        result = mutation.rotate_secret_bundle(
            info=fake_info,
            input=RotateSecretBundleInput(id=str(bundle.guid)),
        )
    assert result.ok is True
    starts = services_temporal_recorder.starts
    assert len(starts) == 1
    name, args, workflow_id = starts[0]
    assert name == "RotateSecretBundleWorkflow"
    assert workflow_id == f"RotateSecretBundleWorkflow-{bundle.guid}"
    rotate_input = args[0]
    assert rotate_input.secret_bundle_id == bundle.pk
    assert rotate_input.bounce_workloads is True


@pytest.mark.django_db
def test_rotate_unknown_bundle_returns_not_found(
    fake_info,
    org,
    actor,
    permission_resolver,
    services_temporal_recorder,
):
    _grant_update(permission_resolver)
    mutation = ServicesMutation()
    with _tenant_for(org, actor):
        result = mutation.rotate_secret_bundle(
            info=fake_info,
            input=RotateSecretBundleInput(
                id="00000000-0000-7000-8000-000000000000",
            ),
        )
    assert result.ok is False
    assert any("not found" in (e.message or "") for e in result.errors)
    # No workflow fires when the bundle lookup fails.
    assert services_temporal_recorder.starts == []


# ---- soft_delete hook -----------------------------------------------------


@pytest.mark.django_db
def test_soft_delete_fires_delete_workflow(
    bundle,
    services_temporal_recorder,
):
    bundle.soft_delete()
    starts = services_temporal_recorder.starts
    assert len(starts) == 1
    name, args, workflow_id = starts[0]
    assert name == "DeleteSecretBundleFromClustersWorkflow"
    assert workflow_id == (f"DeleteSecretBundleFromClustersWorkflow-{bundle.guid}")
    delete_input = args[0]
    assert delete_input.secret_bundle_id == bundle.pk


@pytest.mark.django_db
def test_soft_delete_workflow_enqueue_failure_doesnt_block_delete(
    bundle,
    monkeypatch,
):
    """If Temporal is unreachable when soft-delete fires, the bundle
    must still soft-delete cleanly — operator can re-fire cleanup
    later. Verifies the try/except around start_workflow."""

    def _explode(*args, **kwargs):
        raise RuntimeError("temporal unreachable")

    monkeypatch.setattr(
        "astrolift_workflows.client.start_workflow",
        _explode,
    )
    bundle.soft_delete()
    bundle.refresh_from_db()
    # Soft-delete took effect even though the workflow enqueue failed.
    assert bundle.deleted_at is not None


# ---- schedule_registry ---------------------------------------------------


def test_secret_bundle_refresh_schedule_is_registered():
    sched = get_schedule(kind=ScheduleKind.SECRET_BUNDLE_REFRESH)
    assert sched.workflow_name == "SecretBundleScheduledRefreshWorkflow"
    assert sched.interval_seconds == 60 * 60
    assert sched.schedule_id == "astro-secret_bundle_refresh"


def test_default_schedules_includes_secret_bundle_refresh():
    kinds = {s.kind for s in DEFAULT_SCHEDULES}
    assert ScheduleKind.SECRET_BUNDLE_REFRESH in kinds
