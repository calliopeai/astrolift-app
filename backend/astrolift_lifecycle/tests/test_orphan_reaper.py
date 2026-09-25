"""Orphan detection + reaper (#995 — the verify-clean primitive).

Detection (read-only) pins the core contract: an owned resource with no live
owner row is flagged; a live app's resource is not; and a cloud-enumerated
scan that can't fully enumerate reports INCOMPLETE rather than implying clean.

The reaper deprovisions a *detected* orphan THROUGH the provider drivers
(never a raw API delete), refuses anything with a live owner, and is
idempotent (already-gone -> success). It reuses the #1034 idempotent
managed-service deprovision path and the idempotent identity-role delete.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from astrolift_operations.services import orphan_reaper as reaper

pytestmark = pytest.mark.django_db

IAM = {reaper.KIND_IAM_ROLE}
MSVC = {reaper.KIND_MANAGED_SERVICE}


# ---- IAM-role detection (cloud-enumerated) -------------------------


class _FakeIdentityDriver:
    def __init__(self, roles, *, deleted=None):
        self._roles = roles
        self.deleted = deleted if deleted is not None else []

    def list_owned_roles(self):
        return list(self._roles)

    def delete_identity_role(self, role):
        self.deleted.append(role)


def _managed(cluster):
    from astrolift_clusters.models import TenantCluster

    cluster.lifecycle = TenantCluster.Lifecycle.MANAGED.value
    cluster.save(update_fields=["lifecycle"])
    return cluster


def test_orphan_role_flagged_live_role_not(app, cluster, monkeypatch):
    from core.app_deploy import workload_identity_role_name

    _managed(cluster)
    live_name = workload_identity_role_name(app)  # app is live → must NOT flag
    orphan_name = "astrolift-ghost-org-ghost-app"  # no DB row → orphan

    driver = _FakeIdentityDriver([live_name, orphan_name])
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: driver)

    report = reaper.scan_orphans(kinds=IAM)

    ids = [o.identifier for o in report.orphans]
    assert orphan_name in ids
    assert live_name not in ids
    assert report.complete
    assert report.scanned_kinds == [reaper.KIND_IAM_ROLE]
    assert all(o.classification == "dangling" for o in report.orphans)
    # The flagged orphan carries reap identity (#995).
    flagged = next(o for o in report.orphans if o.identifier == orphan_name)
    assert flagged.reap_key == orphan_name
    assert flagged.cluster_slug == cluster.slug
    assert flagged.reason


def test_live_app_build_role_is_not_an_orphan(app, cluster, monkeypatch):
    """A live app's build/CI/static roles are owned but don't match the
    workload-identity name — they must still be in the live set, or the
    reaper would wipe a running app's build role."""
    from core.app_deploy import build_identity_role_name, static_build_role_name

    _managed(cluster)
    build_name = build_identity_role_name(app)
    static_name = static_build_role_name(app)

    driver = _FakeIdentityDriver([build_name, static_name])
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: driver)

    report = reaper.scan_orphans(kinds=IAM)
    assert report.orphans == []


def test_soft_deleted_app_role_is_orphan(app, cluster, monkeypatch):
    from django.utils import timezone

    from core.app_deploy import workload_identity_role_name

    _managed(cluster)
    name = workload_identity_role_name(app)
    # App torn down at the DB layer but its IAM role lingered.
    app.deleted_at = timezone.now()
    app.save(update_fields=["deleted_at"])

    driver = _FakeIdentityDriver([name])
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: driver)

    report = reaper.scan_orphans(kinds=IAM)
    assert [o.identifier for o in report.orphans] == [name]


def test_unsupported_driver_marks_incomplete_not_clean(app, cluster, monkeypatch):
    from _sdk import UnsupportedOperationError

    _managed(cluster)

    class _Unsupported:
        def list_owned_roles(self):
            raise UnsupportedOperationError("not on this cloud")

    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: _Unsupported())
    report = reaper.scan_orphans(kinds=IAM)
    assert reaper.KIND_IAM_ROLE in report.incomplete_kinds
    assert not report.complete
    assert report.orphans == []


def test_list_error_marks_incomplete(app, cluster, monkeypatch):
    _managed(cluster)

    class _Broken:
        def list_owned_roles(self):
            raise RuntimeError("aws unreachable")

    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: _Broken())
    report = reaper.scan_orphans(kinds=IAM)
    assert reaper.KIND_IAM_ROLE in report.incomplete_kinds
    assert report.orphans == []


# ---- managed-service detection (db-enumerated) ---------------------


def _managed_service(app, env, *, backend_ref="object_store/acme-assets"):
    from astrolift_services.models import ManagedService

    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=ManagedService.Kind.OBJECT_STORE,
        name="assets",
        status=ManagedService.Status.ACTIVE,
        backend_ref=backend_ref,
    )


def test_managed_service_orphan_flagged_when_owning_app_deleted(app, env, cluster):
    """A live managed service whose owning app was soft-deleted is an orphan:
    a cloud handle (backend_ref) with no live owner."""
    from django.utils import timezone

    _managed(cluster)
    svc = _managed_service(app, env)
    app.deleted_at = timezone.now()
    app.save(update_fields=["deleted_at"])

    report = reaper.scan_orphans(kinds=MSVC)

    assert reaper.KIND_MANAGED_SERVICE in report.scanned_kinds
    assert report.complete  # pure DB scan — never incomplete
    flagged = [o for o in report.orphans if o.kind == reaper.KIND_MANAGED_SERVICE]
    assert len(flagged) == 1
    o = flagged[0]
    assert o.identifier == svc.backend_ref
    assert o.reap_key == str(svc.pk)
    assert o.cluster_slug == cluster.slug
    assert o.classification == "orphaned-owner"


def test_managed_service_with_live_owner_is_not_an_orphan(app, env, cluster):
    _managed(cluster)
    _managed_service(app, env)  # owning app + env both live
    report = reaper.scan_orphans(kinds=MSVC)
    assert [o for o in report.orphans if o.kind == reaper.KIND_MANAGED_SERVICE] == []


# ---- managed-service reaper ----------------------------------------


class _RecordingDriver:
    """Records deprovision calls and reports success — the orphan's
    backing resource is deleted through the driver."""

    calls: list[tuple[str, bool, bool]] = []

    def __init__(self, *, config):  # noqa: ANN001
        self._config = config

    def deprovision(self, spec, *, delete_data=False, force_destroy=False):  # noqa: ANN001
        from _sdk.managed_service import DeprovisionResult

        type(self).calls.append((spec.handle, delete_data, force_destroy))
        return DeprovisionResult(ok=True, handle=spec.handle, message="deprovisioned")


class _ReportingNotFoundDriver:
    """Driver that REPORTS the resource as already gone (ok=False, NoSuchBucket)
    — the #1034 path coerces it to success."""

    def __init__(self, *, config):  # noqa: ANN001
        self._config = config

    def deprovision(self, spec, *, delete_data=False, force_destroy=False):  # noqa: ANN001
        from _sdk.managed_service import DeprovisionResult

        return DeprovisionResult(
            ok=False,
            handle=spec.handle,
            message="empty failed: NoSuchBucket: The specified bucket does not exist",
            errors=["NoSuchBucket"],
        )


def _patch_driver(driver_cls):
    return (
        patch("astrolift_drivers.registry.plugins.get", return_value=driver_cls),
        patch("core.cluster_observability.managed_config_for", return_value={}),
    )


def _orphan_service(app, env, cluster):
    from django.utils import timezone

    _managed(cluster)
    svc = _managed_service(app, env)
    app.deleted_at = timezone.now()
    app.save(update_fields=["deleted_at"])
    return svc


def test_reap_managed_service_deprovisions_via_driver(app, env, cluster):
    from astrolift_services.models import ManagedService

    svc = _orphan_service(app, env, cluster)
    _RecordingDriver.calls = []
    p_get, p_cfg = _patch_driver(_RecordingDriver)
    with p_get, p_cfg:
        result = reaper.reap_orphan(
            kind=reaper.KIND_MANAGED_SERVICE,
            reap_key=str(svc.pk),
            force_destroy=True,
        )

    assert result.ok, result.message
    assert not result.already_gone
    # The driver's deprovision was actually called for this handle, with the
    # force_destroy flag threaded through.
    assert _RecordingDriver.calls == [(svc.backend_ref, True, True)]
    # Row is finalized (soft-deleted) so a re-scan reports clean.
    assert ManagedService.all_objects.get(pk=svc.pk).deleted_at is not None


def test_reap_managed_service_idempotent_on_rerun(app, env, cluster):
    svc = _orphan_service(app, env, cluster)
    _RecordingDriver.calls = []
    p_get, p_cfg = _patch_driver(_RecordingDriver)
    with p_get, p_cfg:
        first = reaper.reap_orphan(kind=reaper.KIND_MANAGED_SERVICE, reap_key=str(svc.pk))
        # Re-run over the now-finalized row: idempotent, already-gone success,
        # and the driver is NOT called a second time.
        before = len(_RecordingDriver.calls)
        second = reaper.reap_orphan(kind=reaper.KIND_MANAGED_SERVICE, reap_key=str(svc.pk))

    assert first.ok and not first.already_gone
    assert second.ok and second.already_gone
    assert len(_RecordingDriver.calls) == before  # no second driver call


def test_reap_managed_service_already_gone_backend_is_success(app, env, cluster):
    """Driver reports the backend resource already absent → reap still succeeds
    (reuses the #1034 already-gone coercion) and finalizes the row."""
    svc = _orphan_service(app, env, cluster)
    p_get, p_cfg = _patch_driver(_ReportingNotFoundDriver)
    with p_get, p_cfg:
        result = reaper.reap_orphan(kind=reaper.KIND_MANAGED_SERVICE, reap_key=str(svc.pk))
    assert result.ok, result.message
    assert result.already_gone
    svc.refresh_from_db()
    assert svc.deleted_at is not None


def test_reap_refuses_managed_service_with_live_owner(app, env, cluster):
    """A managed service whose owning app is still live must NOT be reaped —
    only detection-proven orphans are reapable."""
    from astrolift_services.models import ManagedService

    _managed(cluster)
    svc = _managed_service(app, env)  # app + env both live
    _RecordingDriver.calls = []
    p_get, p_cfg = _patch_driver(_RecordingDriver)
    with p_get, p_cfg:
        result = reaper.reap_orphan(kind=reaper.KIND_MANAGED_SERVICE, reap_key=str(svc.pk))

    assert not result.ok
    assert result.refused
    assert "live owning" in result.message
    assert _RecordingDriver.calls == []  # driver never touched
    # Row untouched.
    assert ManagedService.all_objects.get(pk=svc.pk).deleted_at is None


# ---- IAM-role reaper -----------------------------------------------


def test_reap_iam_role_deletes_via_identity_driver(app, cluster, monkeypatch):
    _managed(cluster)
    orphan_name = "astrolift-ghost-org-ghost-app"
    deleted: list[str] = []
    driver = _FakeIdentityDriver([orphan_name], deleted=deleted)
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: driver)

    result = reaper.reap_orphan(kind=reaper.KIND_IAM_ROLE, reap_key=orphan_name, cluster_slug=cluster.slug)
    assert result.ok, result.message
    assert deleted == [orphan_name]  # driver delete actually invoked


def test_reap_refuses_iam_role_of_live_app(app, cluster, monkeypatch):
    from core.app_deploy import workload_identity_role_name

    _managed(cluster)
    live_name = workload_identity_role_name(app)
    deleted: list[str] = []
    driver = _FakeIdentityDriver([live_name], deleted=deleted)
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *a, **k: driver)

    result = reaper.reap_orphan(kind=reaper.KIND_IAM_ROLE, reap_key=live_name, cluster_slug=cluster.slug)
    assert not result.ok
    assert result.refused
    assert deleted == []  # never deleted a live app's role


# ---- permission gate (mutation surface) ----------------------------


def _tenant_for(org, actor):
    from core.tenancy import TenantContext, tenant_context

    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=actor.id))


def _fake_info(actor):
    request = SimpleNamespace(user=actor)
    context = SimpleNamespace(request=request)
    return SimpleNamespace(context=context)


def test_reap_mutation_denied_for_non_operator(app, env, cluster, org, actor, permission_resolver):
    """The reap mutation is install-wide operator-gated (CLUSTER_MANAGE).
    A caller without it is denied before any reaping happens."""
    from astrolift_lifecycle.schema.mutations import (
        LifecycleMutation,
        ReapCloudOrphanInput,
    )
    from core.permissions import Permission

    svc = _orphan_service(app, env, cluster)
    # Grant an unrelated permission only — no CLUSTER_MANAGE.
    permission_resolver.grant(Permission.APP_READ)

    mut = LifecycleMutation()
    _RecordingDriver.calls = []
    p_get, p_cfg = _patch_driver(_RecordingDriver)
    with _tenant_for(org, actor), p_get, p_cfg:
        result = mut.reap_cloud_orphan(
            _fake_info(actor),
            input=ReapCloudOrphanInput(kind=reaper.KIND_MANAGED_SERVICE, reap_key=str(svc.pk)),
        )

    assert not result.ok
    assert _RecordingDriver.calls == []  # denied before any driver call


def test_reap_mutation_allows_operator(app, env, cluster, org, actor, permission_resolver):
    from astrolift_lifecycle.schema.mutations import (
        LifecycleMutation,
        ReapCloudOrphanInput,
    )
    from core.permissions import Permission

    svc = _orphan_service(app, env, cluster)
    permission_resolver.grant(Permission.CLUSTER_MANAGE)
    # Reaping is the platform operator's alone (#1978); CLUSTER_MANAGE by
    # itself is held by every org's admins.
    actor.is_superuser = True
    actor.save(update_fields=["is_superuser"])

    mut = LifecycleMutation()
    _RecordingDriver.calls = []
    p_get, p_cfg = _patch_driver(_RecordingDriver)
    with _tenant_for(org, actor), p_get, p_cfg:
        result = mut.reap_cloud_orphan(
            _fake_info(actor),
            input=ReapCloudOrphanInput(kind=reaper.KIND_MANAGED_SERVICE, reap_key=str(svc.pk)),
        )

    assert result.ok, result.errors
    assert result.data.reaped is True
    # delete_data=True (reaping reclaims fully), force_destroy=False (default).
    assert _RecordingDriver.calls == [(svc.backend_ref, True, False)]
