"""Tests for direct-apply policy (#4, spec 07 §4).

The dry-run stubs here now mirror the real ``ClusterDriver`` contract
(``apply_manifests(cluster, namespace, manifests, dry_run=...)``
returning a providers ``ApplyResult``). They used to stub an
``apply(cluster, objects)`` method no driver has ever had, which is how
the module stayed green while being impossible to call from the deploy
path.
"""

from __future__ import annotations

import pathlib

import pytest
from _sdk.cluster import ApplyError
from _sdk.cluster import ApplyResult as DriverApplyResult

from astrolift_workflows.activities.direct_apply import (
    FIELD_MANAGER,
    ApplyResult,
    DryRunFailed,
    apply_with_dry_run,
    canonical_object_bytes,
    manifest_set_sha256,
)


def _deployment(name: str = "api", ns: str = "acme-api") -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": ns},
        "spec": {"replicas": 1},
    }


def _service(name: str = "api", ns: str = "acme-api") -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": ns},
        "spec": {"ports": [{"port": 80}]},
    }


# ---- field manager constant ----------------------------------------


def test_field_manager_is_astrolift():
    """Locked: renaming this would re-own every field on next deploy
    and silently flap with other controllers (cert-manager, ESO)."""
    assert FIELD_MANAGER == "astrolift"


def test_driver_stamps_this_field_manager():
    """The value is hardcoded in the providers tree (it can't import
    backend apps), so nothing but this test keeps the two in step."""
    src = pathlib.Path("providers/_sdk/k8s_dynamic_client.py").read_text()

    assert f'"field_manager": "{FIELD_MANAGER}"' in src


# ---- snapshot hashing ----------------------------------------------


def test_canonical_bytes_are_deterministic():
    a = canonical_object_bytes(_deployment())
    b = canonical_object_bytes(_deployment())
    assert a == b


def test_canonical_bytes_sorted_keys():
    """Same logical object with shuffled key order produces the same
    canonical bytes (sort_keys=True)."""
    a = canonical_object_bytes({"b": 1, "a": 2})
    b = canonical_object_bytes({"a": 2, "b": 1})
    assert a == b


def test_snapshot_hash_format():
    h = manifest_set_sha256([_deployment(), _service()])
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_snapshot_hash_order_independent():
    """Cosmetic reorderings shouldn't produce a 'new' snapshot."""
    a = manifest_set_sha256([_deployment(), _service()])
    b = manifest_set_sha256([_service(), _deployment()])
    assert a == b


def test_snapshot_hash_changes_when_object_changes():
    a = manifest_set_sha256([_deployment(name="api")])
    b = manifest_set_sha256([_deployment(name="other")])
    assert a != b


def test_snapshot_hash_changes_when_object_added():
    a = manifest_set_sha256([_deployment()])
    b = manifest_set_sha256([_deployment(), _service()])
    assert a != b


# ---- dry-run / apply -----------------------------------------------


class _StubDriver:
    """Records calls and returns canned outcomes, in the shape every
    real ``ClusterDriver.apply_manifests`` returns."""

    def __init__(self, dry_run_errors=None, diff_was_empty=False):
        self.calls: list[tuple] = []
        self.dry_run_errors = list(dry_run_errors or [])
        self.diff_was_empty = diff_was_empty

    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):
        objs = list(manifests)
        self.calls.append(("apply_manifests", cluster, namespace, objs, dry_run))
        errors = [
            ApplyError(
                kind="Deployment",
                name="api",
                namespace=namespace,
                exception_type="ApiException",
                exception_message=msg,
                is_retryable=False,
            )
            for msg in (self.dry_run_errors if dry_run else [])
        ]
        refs = [f"{o.get('kind')}/{o.get('metadata', {}).get('name')}" for o in objs]
        return DriverApplyResult(
            created=[] if (errors or self.diff_was_empty) else refs,
            updated=[],
            unchanged=refs if self.diff_was_empty else [],
            errors=errors,
        )


def test_apply_runs_dry_run_first():
    driver = _StubDriver()
    result = apply_with_dry_run(driver, "cluster-1", "acme-api", [_deployment()])
    assert isinstance(result, ApplyResult)
    assert result.objects_applied == 1
    assert result.snapshot_hash.startswith("sha256:")
    # First call dry_run=True, second dry_run=False
    assert driver.calls[0][4] is True
    assert driver.calls[1][4] is False
    assert driver.calls[0][2] == "acme-api"


def test_dry_run_failure_aborts_before_real_apply():
    driver = _StubDriver(dry_run_errors=["Deployment.apps: forbidden"])
    with pytest.raises(DryRunFailed) as exc:
        apply_with_dry_run(driver, "c", "acme-api", [_deployment()])
    assert "forbidden" in str(exc.value)
    # Real apply never ran — only the dry-run call recorded
    assert len(driver.calls) == 1
    assert driver.calls[0][4] is True


def test_idempotent_re_apply_signals_diff_empty():
    """Re-applying an unchanged manifest set is success with
    diff_was_empty=True — UI shows 'no changes' instead of
    'redeployed'."""
    driver = _StubDriver(diff_was_empty=True)
    result = apply_with_dry_run(driver, "c", "acme-api", [_deployment()])
    assert result.diff_was_empty is True
    assert result.unchanged == ("Deployment/api",)


def test_changed_set_is_not_reported_as_empty_diff():
    driver = _StubDriver()
    result = apply_with_dry_run(driver, "c", "acme-api", [_deployment()])
    assert result.diff_was_empty is False
    assert result.created == ("Deployment/api",)
    assert result.errors == ()
