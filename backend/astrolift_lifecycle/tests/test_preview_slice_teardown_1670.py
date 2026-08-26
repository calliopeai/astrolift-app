"""A torn-down preview drops the slice it owns (#1670).

The leak: `provision_preview_managed_services` carved a slice out of the
shared instance and nothing ever removed it, so a torn-down preview left
its database behind in the parent. `supports_slicing` requires both verbs
precisely so a driver cannot offer to carve a slice it can never remove,
and then the removal half was never called -- the capability was present,
tested, and reachable from its own test suite, so coverage stayed green
while every teardown leaked.

The test that matters most is `test_the_parent_is_never_deprovisioned`. A
teardown that dropped the shared instance is the worst outcome this path
can produce, and it is one wrong argument away.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.services.preview_service_provisioning import (
    deprovision_preview_managed_services,
    provision_preview_managed_services,
)

pytestmark = pytest.mark.django_db

SLICE_HANDLE = "postgres_slice/cl/ns/slice_preview_pr_7"
PARENT_REF = "postgres/pg-main"


class _SliceResult:
    def __init__(self):
        self.env_overrides = {"POSTGRES_DB": "slice_preview_pr_7"}
        self.slice_handle = SLICE_HANDLE


class _Driver:
    """Records every drop, and whether it was handed the parent or a slice."""

    def __init__(self, *, drops=True, raises=False):
        self._drops = drops
        self._raises = raises
        self.dropped: list[tuple] = []

    def supports_slicing(self):
        return True

    def provision_slice(self, spec):
        return _SliceResult()

    def deprovision_slice(self, spec, slice_handle):
        if self._raises:
            raise RuntimeError("the operator is unreachable")
        self.dropped.append((getattr(spec, "parent", None), slice_handle))
        return self._drops


class _NoLongerSliceable:
    def supports_slicing(self):
        return False


@pytest.fixture
def primary(app, cluster):
    from astrolift_lifecycle.models import AppEnvironment

    return AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="production", required_approvals=0
    )


@pytest.fixture
def preview(app, cluster, primary):
    from astrolift_lifecycle.models import AppEnvironment

    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="preview-pr-7",
        required_approvals=0,
        previewed_environment=primary,
    )


def _service(app, env):
    from astrolift_services.models import ManagedService

    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        name="postgres-main",
        kind="postgres",
        config={},
        backend_ref=PARENT_REF,
    )


@pytest.fixture
def install(monkeypatch):
    """Patch both halves onto one driver, so provision and teardown agree."""

    def _install(driver):
        import astrolift_lifecycle.services.preview_service_provisioning as mod

        def fake_slice_for(*, service, preview_env):
            return driver.provision_slice(None) if driver.supports_slicing() else None

        def fake_drop(*, service, attachment, preview_env):
            if not driver.supports_slicing():
                raise RuntimeError("driver no longer supports slicing")
            spec = type("S", (), {"parent": service.backend_ref})()
            return driver.deprovision_slice(spec, attachment.slice_handle)

        monkeypatch.setattr(mod, "_slice_for", fake_slice_for)
        monkeypatch.setattr(mod, "_drop_slice", fake_drop)

    return _install


def _attach(app, primary, preview, install, driver):
    install(driver)
    service = _service(app, primary)
    provision_preview_managed_services(preview)
    return service


# ---- the handle has to survive provisioning ---------------------------


def test_the_slice_handle_is_persisted_on_the_attachment(app, primary, preview, install):
    """Teardown has nothing to drop otherwise. `provision_slice` derives the
    handle from `slice_id` and returns it; nothing else can recover it."""
    from astrolift_services.models import ManagedServiceAttachment

    _attach(app, primary, preview, install, _Driver())

    attachment = ManagedServiceAttachment.objects.get(app_environment=preview)
    assert attachment.slice_handle == SLICE_HANDLE


# ---- teardown ---------------------------------------------------------


def test_teardown_drops_the_slice(app, primary, preview, install):
    driver = _Driver()
    _attach(app, primary, preview, install, driver)

    outcome = deprovision_preview_managed_services(preview)

    assert outcome.dropped == ["postgres-main"]
    assert outcome.leaked == []
    assert outcome.errors == {}
    assert len(driver.dropped) == 1


def test_the_parent_is_never_deprovisioned(app, primary, preview, install):
    """The worst outcome this path can produce, and one wrong argument
    away: the handle passed for removal must name the slice, never the
    parent instance the whole app shares."""
    driver = _Driver()
    _attach(app, primary, preview, install, driver)

    deprovision_preview_managed_services(preview)

    (parent, handle) = driver.dropped[0]
    assert handle == SLICE_HANDLE
    assert handle != PARENT_REF
    assert parent == PARENT_REF, "the parent is passed for reference, not for deletion"


def test_the_handle_is_cleared_so_a_second_teardown_is_a_no_op(app, primary, preview, install):
    """A preview torn down twice, or re-fired after a partial failure, must
    converge rather than fail on an already-dropped slice."""
    driver = _Driver()
    _attach(app, primary, preview, install, driver)

    deprovision_preview_managed_services(preview)
    second = deprovision_preview_managed_services(preview)

    assert second.dropped == []
    assert second.no_slice == ["postgres-main"]
    assert len(driver.dropped) == 1, "the driver must not be asked twice"


def test_a_delete_that_does_not_land_is_a_leak_not_an_error(app, primary, preview, install):
    """Different operator responses: a leak wants someone to go and drop a
    database, an error wants someone to work out why the call failed."""
    driver = _Driver(drops=False)
    _attach(app, primary, preview, install, driver)

    outcome = deprovision_preview_managed_services(preview)

    assert outcome.leaked == ["postgres-main"]
    assert outcome.errors == {}
    assert outcome.dropped == []


def test_a_leaked_slice_keeps_its_handle(app, primary, preview, install):
    """Clearing it would erase the only record of what was left behind, and
    a re-fired teardown could no longer retry the drop."""
    from astrolift_services.models import ManagedServiceAttachment

    _attach(app, primary, preview, install, _Driver(drops=False))

    deprovision_preview_managed_services(preview)

    attachment = ManagedServiceAttachment.objects.get(app_environment=preview)
    assert attachment.slice_handle == SLICE_HANDLE


def test_one_stuck_slice_does_not_stop_the_rest(app, primary, preview, install):
    """Raising would strand the preview's remaining teardown behind one
    undroppable database."""
    driver = _Driver(raises=True)
    _attach(app, primary, preview, install, driver)

    outcome = deprovision_preview_managed_services(preview)

    assert outcome.dropped == []
    assert "postgres-main" in outcome.errors


def test_an_attachment_with_no_slice_is_reported_not_dropped(app, primary, preview, install):
    """Attached straight to the parent, or attached before this field
    existed. Guessing a handle would risk dropping the parent's own
    database."""
    from astrolift_services.models import ManagedServiceAttachment

    driver = _Driver()
    _attach(app, primary, preview, install, driver)
    ManagedServiceAttachment.objects.filter(app_environment=preview).update(slice_handle="")

    outcome = deprovision_preview_managed_services(preview)

    assert outcome.no_slice == ["postgres-main"]
    assert driver.dropped == []


def test_a_preview_with_no_attachments_tears_down_cleanly(preview):
    outcome = deprovision_preview_managed_services(preview)

    assert outcome.dropped == []
    assert outcome.no_slice == []
    assert outcome.errors == {}
