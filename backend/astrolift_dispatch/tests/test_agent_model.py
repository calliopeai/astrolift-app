"""Managed-model dispatch wiring (``astrolift_dispatch.agent_model``).

``resolve_managed_model_wiring`` bridges the cluster driver (cloud-specific
identity mint + model env) and the spawner. It resolves the driver, mints /
reuses the identity, computes the provider model env, and builds the
annotated ServiceAccount manifest — collapsing every failure into one
:class:`ManagedModelError` the spawner surfaces on the task.

Driven with a fake driver + fake cluster; ``_driver_for_cluster`` is
monkeypatched on ``core.cluster_management`` (resolved at call time inside
the wiring, mirroring the spawner's own patch pattern).
"""

from __future__ import annotations

import pytest

from astrolift_dispatch.agent_model import (
    AGENT_MODEL_ROLE_ANNOTATION,
    AGENT_MODEL_SERVICE_ACCOUNT,
    ManagedModelError,
    build_service_account_manifest,
    resolve_managed_model_wiring,
)


class _FakeDriver:
    def __init__(
        self, *, env=None, role="arn:aws:iam::123456789012:role/model", identity_exc=None, env_exc=None
    ):
        self._env = env or {
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_REGION": "us-west-2",
            "ANTHROPIC_MODEL": "sonnet",
            "ANTHROPIC_SMALL_FAST_MODEL": "haiku",
        }
        self._role = role
        self._identity_exc = identity_exc
        self._env_exc = env_exc
        self.calls: list[tuple] = []

    def ensure_agent_model_identity(self, *, namespace, service_account, provider_config):
        self.calls.append(("identity", namespace, service_account, dict(provider_config)))
        if self._identity_exc is not None:
            raise self._identity_exc
        return self._role

    def agent_model_env(self, *, region, provider_config):
        self.calls.append(("env", region, dict(provider_config)))
        if self._env_exc is not None:
            raise self._env_exc
        return dict(self._env)


class _FakeCluster:
    def __init__(self, *, provider_config=None, region="us-west-2"):
        self.provider_config = provider_config or {}
        self.region = region


def _patch_driver(monkeypatch, driver_or_raiser):
    import core.cluster_management as cm

    if isinstance(driver_or_raiser, BaseException):
        exc = driver_or_raiser

        def _raise(_c):
            raise exc

        monkeypatch.setattr(cm, "_driver_for_cluster", _raise, raising=False)
    else:
        monkeypatch.setattr(cm, "_driver_for_cluster", lambda _c: driver_or_raiser, raising=False)


# ---- build_service_account_manifest ---------------------------------


def test_service_account_manifest_shape_and_annotation():
    sa = build_service_account_manifest(
        namespace="astrolift-agents-acme",
        service_account=AGENT_MODEL_SERVICE_ACCOUNT,
        role_arn="arn:aws:iam::1:role/r",
    )
    assert sa["kind"] == "ServiceAccount"
    assert sa["metadata"]["name"] == AGENT_MODEL_SERVICE_ACCOUNT
    assert sa["metadata"]["namespace"] == "astrolift-agents-acme"
    assert sa["metadata"]["annotations"][AGENT_MODEL_ROLE_ANNOTATION] == "arn:aws:iam::1:role/r"
    assert sa["metadata"]["labels"]["astrolift.dev/managed-by"] == "astrolift-agents"


# ---- resolve_managed_model_wiring -----------------------------------


def test_resolve_wiring_happy_path(monkeypatch):
    driver = _FakeDriver()
    _patch_driver(monkeypatch, driver)

    wiring = resolve_managed_model_wiring(
        cluster=_FakeCluster(provider_config={"account_id": "1"}),
        namespace="astrolift-agents-acme",
    )
    assert wiring.service_account == AGENT_MODEL_SERVICE_ACCOUNT

    env = {e["name"]: e["value"] for e in wiring.env}
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["ANTHROPIC_MODEL"] == "sonnet"

    sa = wiring.service_account_manifest
    assert (
        sa["metadata"]["annotations"][AGENT_MODEL_ROLE_ANNOTATION] == "arn:aws:iam::123456789012:role/model"
    )

    # Identity resolved with the model SA + provider_config threaded; env
    # resolved with the cluster region.
    assert (
        "identity",
        "astrolift-agents-acme",
        AGENT_MODEL_SERVICE_ACCOUNT,
        {"account_id": "1"},
    ) in driver.calls
    assert ("env", "us-west-2", {"account_id": "1"}) in driver.calls


def test_resolve_wiring_region_from_provider_config(monkeypatch):
    driver = _FakeDriver()
    _patch_driver(monkeypatch, driver)
    resolve_managed_model_wiring(
        cluster=_FakeCluster(provider_config={"region": "eu-west-1"}, region=""),
        namespace="ns",
    )
    assert ("env", "eu-west-1", {"region": "eu-west-1"}) in driver.calls


def test_resolve_wiring_identity_failure_wraps(monkeypatch):
    driver = _FakeDriver(identity_exc=RuntimeError("managed model is not supported on this cluster provider"))
    _patch_driver(monkeypatch, driver)
    with pytest.raises(ManagedModelError) as ei:
        resolve_managed_model_wiring(cluster=_FakeCluster(), namespace="ns")
    assert "not supported" in str(ei.value)


def test_resolve_wiring_env_failure_wraps(monkeypatch):
    driver = _FakeDriver(env_exc=RuntimeError("no model access enabled"))
    _patch_driver(monkeypatch, driver)
    with pytest.raises(ManagedModelError) as ei:
        resolve_managed_model_wiring(cluster=_FakeCluster(), namespace="ns")
    assert "no model access enabled" in str(ei.value)


def test_resolve_wiring_driver_build_failure_wraps(monkeypatch):
    import core.cluster_management as cm

    _patch_driver(monkeypatch, cm.ClusterManagementError("plugin 'aws' not loaded"))
    with pytest.raises(ManagedModelError) as ei:
        resolve_managed_model_wiring(cluster=_FakeCluster(), namespace="ns")
    assert "plugin 'aws' not loaded" in str(ei.value)
