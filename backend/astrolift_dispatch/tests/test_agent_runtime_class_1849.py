"""Render the opt-in runtime and refuse a missing class before creating a pod."""

from unittest.mock import Mock

import pytest
from django.test import override_settings

from astrolift_dispatch.pod_hardening import AgentRuntimeClassError, preflight_agent_runtime
from astrolift_dispatch.tests.test_agent_pod_hardening_1848 import _render


@override_settings(AGENT_RUNTIME_CLASS="")
def test_default_does_not_require_a_runtime_class():
    pod = _render()["spec"]["template"]["spec"]
    assert "runtimeClassName" not in pod
    preflight_agent_runtime(None, pod)


@override_settings(AGENT_RUNTIME_CLASS="gvisor")
def test_setting_applies_to_agent_pods():
    assert _render()["spec"]["template"]["spec"]["runtimeClassName"] == "gvisor"


def test_runtime_preflight_uses_cluster_scoped_lookup(monkeypatch):
    driver = Mock()
    driver.get_manifest.return_value = {"metadata": {"name": "gvisor"}}
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: driver)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: Mock(slug="cluster-a")
    )
    preflight_agent_runtime(None, {"runtimeClassName": "gvisor"})
    driver.get_manifest.assert_called_once_with("cluster-a", None, "RuntimeClass", "gvisor")


@pytest.mark.parametrize("manifest", [None, {"metadata": {"deletionTimestamp": "now"}}])
def test_missing_or_deleting_runtime_is_refused(monkeypatch, manifest):
    driver = Mock()
    driver.get_manifest.return_value = manifest
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: driver)
    monkeypatch.setattr(
        "core.cluster_management._context_for_cluster", lambda cluster: Mock(slug="cluster-a")
    )
    with pytest.raises(AgentRuntimeClassError, match="not available"):
        preflight_agent_runtime(None, {"runtimeClassName": "gvisor"})


def test_unverifiable_runtime_is_refused(monkeypatch):
    def fail(cluster):
        raise RuntimeError("driver down")

    monkeypatch.setattr("core.cluster_management._driver_for_cluster", fail)
    with pytest.raises(AgentRuntimeClassError, match="Cannot verify"):
        preflight_agent_runtime(None, {"runtimeClassName": "gvisor"})


pytestmark = pytest.mark.django_db
