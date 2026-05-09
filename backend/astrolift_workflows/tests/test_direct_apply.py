"""Tests for direct-apply policy (#4, spec 07 §4)."""

from __future__ import annotations

import asyncio

import pytest

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
    """Records calls and returns canned outcomes."""

    def __init__(self, dry_run_errors=None, diff_was_empty=False):
        self.calls: list[tuple] = []
        self.dry_run_errors = list(dry_run_errors or [])
        self.diff_was_empty = diff_was_empty

    def apply(self, cluster, objects, *, dry_run=False, field_manager=""):
        self.calls.append(
            ("apply", cluster, list(objects), dry_run, field_manager)
        )
        if dry_run:
            return {"dry_run_errors": self.dry_run_errors}
        return {"diff_was_empty": self.diff_was_empty}


def test_apply_runs_dry_run_first():
    driver = _StubDriver()
    result = apply_with_dry_run(driver, "cluster-1", [_deployment()])
    assert isinstance(result, ApplyResult)
    assert result.objects_applied == 1
    assert result.snapshot_hash.startswith("sha256:")
    # First call dry_run=True, second dry_run=False
    assert driver.calls[0][3] is True
    assert driver.calls[1][3] is False
    assert driver.calls[0][4] == FIELD_MANAGER


def test_dry_run_failure_aborts_before_real_apply():
    driver = _StubDriver(dry_run_errors=["Deployment.apps: forbidden"])
    with pytest.raises(DryRunFailed) as exc:
        apply_with_dry_run(driver, "c", [_deployment()])
    assert "forbidden" in str(exc.value)
    # Real apply never ran — only the dry-run call recorded
    assert len(driver.calls) == 1
    assert driver.calls[0][3] is True


def test_idempotent_re_apply_signals_diff_empty():
    """Re-applying an unchanged manifest set is success with
    diff_was_empty=True — UI shows 'no changes' instead of
    'redeployed'."""
    driver = _StubDriver(diff_was_empty=True)
    result = apply_with_dry_run(driver, "c", [_deployment()])
    assert result.diff_was_empty is True


def test_apply_passes_field_manager():
    driver = _StubDriver()
    apply_with_dry_run(driver, "c", [_deployment()])
    for call in driver.calls:
        assert call[4] == FIELD_MANAGER


def test_apply_with_custom_field_manager():
    driver = _StubDriver()
    apply_with_dry_run(driver, "c", [_deployment()], field_manager="custom")
    for call in driver.calls:
        assert call[4] == "custom"


# ---- driver shape compatibility ------------------------------------


class _LegacyDriver:
    """Driver with the older 2-arg apply shape (no kwargs)."""

    def __init__(self):
        self.calls: list[tuple] = []

    def apply(self, cluster, objects):
        self.calls.append(("apply", cluster, list(objects)))
        # No structured return — wrapper falls back to defaults
        return None


def test_legacy_driver_falls_back_gracefully():
    """Older driver stubs (no dry_run / field_manager kwargs) still
    work — module re-calls without the kwargs and treats no-error
    as success."""
    driver = _LegacyDriver()
    result = apply_with_dry_run(driver, "c", [_deployment()])
    # Two calls: dry-run and real apply, each via the kwargs fallback
    assert len(driver.calls) == 2
    assert result.objects_applied == 1
    assert result.diff_was_empty is False
