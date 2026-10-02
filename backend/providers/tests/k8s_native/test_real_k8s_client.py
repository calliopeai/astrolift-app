"""Tests for k8s_native's ``_build_k8s_client`` (#566).

The driver-level tests in ``test_*.py`` mock the whole client via
``k8s_client_factory``. This file exercises the production factory:
the kubeconfig / in-cluster / default-kubeconfig auth paths, and the
delegation to the shared ``KubernetesDynamicClient`` helper.

No real kubeconfig is loaded — ``kubernetes.config`` is fully patched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from k8s_native.cluster import _build_k8s_client, _NotFound, _RealK8sClient


def _patched_kubernetes() -> Any:
    """Return a (client, config) tuple of MagicMocks for patch().

    The factory imports ``from kubernetes import client, config``; we
    stub both so no real network / filesystem load happens.
    """
    fake_client = MagicMock()
    fake_config = MagicMock()
    fake_api_client = MagicMock()
    fake_client.ApiClient.return_value = fake_api_client
    fake_client.Configuration.return_value = MagicMock()
    return fake_client, fake_config, fake_api_client


# ---- shared helper class identity -------------------------------


def test_real_k8s_client_is_the_shared_helper() -> None:
    """The local alias must be the shared helper itself — the #566 fix
    is structural: instead of a stub class that raises on every method,
    we re-export the implemented helper."""
    assert _RealK8sClient is KubernetesDynamicClient


def test_not_found_is_shared_helper_exception() -> None:
    """``except _NotFound:`` clauses in the driver catch what the
    shared helper raises. Aliasing the exception is how we keep the
    existing call-site code working without rewriting every resolver."""
    from _sdk.k8s_dynamic_client import NotFoundError

    assert _NotFound is NotFoundError


# ---- _build_k8s_client auth paths --------------------------------


def test_build_in_cluster_loads_incluster_config() -> None:
    fake_client, fake_config, _fake_api_client = _patched_kubernetes()
    # The factory does ``from kubernetes import client, config``; we
    # can't patch the import statement directly, so patch the bound
    # names on the kubernetes package and also seed ``sys.modules`` so
    # an evaluation under ``from kubernetes import ...`` resolves.
    with (
        patch.dict(
            "sys.modules",
            {"kubernetes": MagicMock(client=fake_client, config=fake_config)},
        ),
        patch("kubernetes.client", fake_client),
        patch("kubernetes.config", fake_config),
    ):
        result = _build_k8s_client(
            kubeconfig_path="",
            context="",
            in_cluster=True,
        )
    fake_config.load_incluster_config.assert_called_once()
    fake_config.load_kube_config.assert_not_called()
    assert isinstance(result, KubernetesDynamicClient)


def test_build_with_kubeconfig_path_loads_from_file() -> None:
    fake_client, fake_config, _ = _patched_kubernetes()
    with (
        patch("kubernetes.client", fake_client),
        patch(
            "kubernetes.config",
            fake_config,
        ),
    ):
        result = _build_k8s_client(
            kubeconfig_path="/path/to/kubeconfig",
            context="prod",
            in_cluster=False,
        )
    fake_config.load_kube_config.assert_called_once_with(
        config_file="/path/to/kubeconfig",
        context="prod",
        client_configuration=fake_client.Configuration.return_value,
    )
    fake_config.load_incluster_config.assert_not_called()
    assert isinstance(result, KubernetesDynamicClient)


def test_build_with_no_path_falls_back_to_default_kubeconfig() -> None:
    """``~/.kube/config`` with the active context — the operator's
    workstation default."""
    fake_client, fake_config, _ = _patched_kubernetes()
    with (
        patch("kubernetes.client", fake_client),
        patch(
            "kubernetes.config",
            fake_config,
        ),
    ):
        _build_k8s_client(
            kubeconfig_path="",
            context="",
            in_cluster=False,
        )
    fake_config.load_kube_config.assert_called_once_with(
        context=None, client_configuration=fake_client.Configuration.return_value
    )


def test_build_explicit_context_passed_through() -> None:
    fake_client, fake_config, _ = _patched_kubernetes()
    with (
        patch("kubernetes.client", fake_client),
        patch(
            "kubernetes.config",
            fake_config,
        ),
    ):
        _build_k8s_client(
            kubeconfig_path="",
            context="staging",
            in_cluster=False,
        )
    fake_config.load_kube_config.assert_called_once_with(
        context="staging", client_configuration=fake_client.Configuration.return_value
    )


def test_built_client_carries_api_client_from_loaded_config() -> None:
    """The factory must populate the helper with the ApiClient built
    from the loaded kubeconfig — that's the carrier for the
    cert-based or projected-token auth."""
    fake_client, fake_config, fake_api_client = _patched_kubernetes()
    with (
        patch("kubernetes.client", fake_client),
        patch(
            "kubernetes.config",
            fake_config,
        ),
    ):
        helper = _build_k8s_client(
            kubeconfig_path="/x",
            context="",
            in_cluster=False,
        )
    assert helper._api_client is fake_api_client


def test_built_client_uses_noop_token_provider() -> None:
    """kubeconfig auth carries its own bearer/cert; the shared
    helper's per-op refresh must not clobber it."""
    fake_client, fake_config, _ = _patched_kubernetes()
    with (
        patch("kubernetes.client", fake_client),
        patch(
            "kubernetes.config",
            fake_config,
        ),
    ):
        helper = _build_k8s_client(
            kubeconfig_path="",
            context="",
            in_cluster=True,
        )
    # The token provider returns empty so _refresh_token bails early.
    assert helper._token_provider() == ""
