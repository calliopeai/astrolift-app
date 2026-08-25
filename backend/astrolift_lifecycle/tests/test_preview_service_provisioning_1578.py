"""A preview finally gets a managed-service envelope (#1578).

The complaint the issue opens with: a preview `AppEnvironment` owns zero
`ManagedService` rows and inherits none, and the deploy render only
synthesizes the bindings Secret when that set is non-empty -- so a preview
workload boots with no DB / redis / queue envelope at all.

The decision worth reading is `test_a_shared_service_whose_driver_cannot_
slice_is_not_attached`. Attaching it would give the preview a binding
straight to the parent instance with the parent's credentials, which looks
like success and is the exact failure this issue was filed about.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.services.preview_service_provisioning import (
    provision_preview_managed_services,
)

pytestmark = pytest.mark.django_db


class _SliceResult:
    def __init__(self, overrides):
        self.env_overrides = overrides
        self.slice_handle = "postgres_slice/cl/ns/slice_preview_pr_7"


class _Sliceable:
    calls: list = []

    def supports_slicing(self):
        return True

    def provision_slice(self, spec):
        type(self).calls.append(spec)
        return _SliceResult({"POSTGRES_DB": "slice_preview_pr_7"})


class _NotSliceable:
    def supports_slicing(self):
        return False


class _Raising:
    def supports_slicing(self):
        return True

    def provision_slice(self, spec):
        raise RuntimeError("carve failed")


@pytest.fixture(autouse=True)
def _reset():
    _Sliceable.calls = []
    yield


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


def _service(app, env, *, kind="postgres", config=None, backend_ref="postgres/pg-main"):
    from astrolift_services.models import ManagedService

    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        name=f"{kind}-main",
        kind=kind,
        config=config or {},
        backend_ref=backend_ref,
    )


@pytest.fixture
def install(monkeypatch):
    def _install(driver):
        import astrolift_lifecycle.services.preview_service_provisioning as mod

        def fake_slice_for(*, service, preview_env):
            if driver is None:
                return None
            if not driver.supports_slicing():
                return None
            return driver.provision_slice(
                type("S", (), {"slice_id": preview_env.name, "parent": service.backend_ref})()
            )

        monkeypatch.setattr(mod, "_slice_for", fake_slice_for)

    return _install


def test_a_preview_with_no_lineage_inherits_nothing(app, cluster, primary, install):
    """Guessing a source would be worse than inheriting nothing."""
    from astrolift_lifecycle.models import AppEnvironment

    install(_Sliceable())
    orphan = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=cluster, name="preview-pr-9", required_approvals=0
    )
    _service(app, primary)

    outcome = provision_preview_managed_services(orphan)

    assert outcome.attached == []
    assert outcome.errors == {}


def test_a_shared_sliceable_service_is_attached_with_its_overrides(app, primary, preview, install):
    install(_Sliceable())
    _service(app, primary)

    outcome = provision_preview_managed_services(preview)

    assert outcome.attached == ["postgres-main"]
    assert outcome.sliced == ["postgres-main"]
    assert outcome.env_overrides["POSTGRES_DB"] == "slice_preview_pr_7"


def test_the_attachment_row_is_created(app, primary, preview, install):
    """An attachment, not a copy: a second ManagedService row would
    provision a second instance, which is the DEDICATED policy."""
    from astrolift_services.models import ManagedService, ManagedServiceAttachment

    install(_Sliceable())
    _service(app, primary)

    provision_preview_managed_services(preview)

    assert ManagedServiceAttachment.objects.filter(app_environment=preview).count() == 1
    assert ManagedService.objects.filter(app_environment=preview).count() == 0


def test_it_is_idempotent(app, primary, preview, install):
    from astrolift_services.models import ManagedServiceAttachment

    install(_Sliceable())
    _service(app, primary)

    provision_preview_managed_services(preview)
    provision_preview_managed_services(preview)

    assert ManagedServiceAttachment.objects.filter(app_environment=preview).count() == 1


def test_a_shared_service_whose_driver_cannot_slice_is_not_attached(app, primary, preview, install):
    """The load-bearing decision.

    Attaching it would hand the preview a binding straight to the parent
    instance with the parent's credentials -- production data in a PR
    preview, which is what this issue is about, and it would look like
    success. Unattached, the workload boots without that envelope key and
    fails loudly on first use.
    """
    install(_NotSliceable())
    _service(app, primary, kind="queue")

    outcome = provision_preview_managed_services(preview)

    assert outcome.attached == []
    assert outcome.shared_unsliced == ["queue-main"]


def test_a_dedicated_service_is_skipped_not_attached(app, primary, preview, install):
    """A dedicated preview service is a provision, not an attach, and
    belongs to the managed-service lifecycle workflow."""
    install(_Sliceable())
    _service(app, primary, kind="search")

    outcome = provision_preview_managed_services(preview)

    assert outcome.skipped == ["search-main"]
    assert outcome.attached == []


def test_a_manifest_override_beats_the_kind_default(app, primary, preview, install):
    """postgres defaults to shared; the manifest can pin it dedicated."""
    install(_Sliceable())
    _service(app, primary, kind="postgres", config={"preview_policy": "dedicated"})

    outcome = provision_preview_managed_services(preview)

    assert outcome.skipped == ["postgres-main"]


def test_an_unknown_policy_string_is_an_error_not_a_default(app, primary, preview, install):
    """Defaulting to shared would be the less safe reading of a typo."""
    install(_Sliceable())
    _service(app, primary, config={"preview_policy": "sharedish"})

    outcome = provision_preview_managed_services(preview)

    assert "postgres-main" in outcome.errors
    assert outcome.attached == []


def test_one_failing_service_does_not_stop_the_others(app, primary, preview, monkeypatch):
    """A preview whose redis could not be sliced should still get its
    postgres, and the operator needs to see which half happened."""
    import astrolift_lifecycle.services.preview_service_provisioning as mod

    _service(app, primary, kind="postgres")
    _service(app, primary, kind="redis")

    def fake_slice_for(*, service, preview_env):
        if service.kind == "redis":
            raise RuntimeError("carve failed")
        return _SliceResult({"POSTGRES_DB": "slice_preview_pr_7"})

    monkeypatch.setattr(mod, "_slice_for", fake_slice_for)

    outcome = provision_preview_managed_services(preview)

    assert outcome.attached == ["postgres-main"]
    assert "redis-main" in outcome.errors


def test_the_naming_half_of_the_orphan_module_is_deliberately_unused():
    """`shared_plan_for` / `teardown_plan_for` compute per-preview
    identifiers, and the slice contract requires the *driver* to derive its
    own from `slice_id`. Wiring both would give two authorities for what a
    preview's database is called, and only one of them creates it.

    Asserted rather than left as a comment, because the obvious next move
    for someone closing the orphan module out is to wire all five of its
    public functions.
    """
    import inspect

    import astrolift_lifecycle.services.preview_service_provisioning as mod

    source = inspect.getsource(mod)
    assert "resolve_policy" in source, "the policy half is what this uses"
    assert "shared_plan_for" not in source.replace("`shared_plan_for`", "")
    assert "teardown_plan_for" not in source.replace("`teardown_plan_for`", "")
