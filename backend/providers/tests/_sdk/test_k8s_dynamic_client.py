"""Cloud-neutral tests for the shared ``KubernetesDynamicClient``.

The four cloud cluster drivers (EKS, GKE, AKS, k8s_native) share one
DynamicClient body via this helper. Coverage of that body lives here
so we don't depend on AWS imports to validate the wire shape; the
per-cloud test files cover the auth-shim wiring instead.

We don't talk to a real apiserver — every ``DynamicClient`` /
``CoreV1Api`` / streaming primitive is patched in.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from _sdk.cluster import ExecResult
from _sdk.k8s_dynamic_client import (
    DEFAULT_API_VERSION_FOR_KIND,
    KubernetesDynamicClient,
    NotFoundError,
    PortForwardHandle,
    _decode_ca_data,
    split_kind,
)

# ---- helpers ------------------------------------------------------


def _make_client(token_provider: Any | None = None) -> KubernetesDynamicClient:
    """Build a helper against a fake endpoint with a base64-PEM CA."""
    return KubernetesDynamicClient(
        endpoint="https://cluster.example.com",
        ca_data=base64.b64encode(b"-----BEGIN CERTIFICATE-----\nfake\n").decode(),
        token_provider=token_provider or (lambda: "tok-initial"),
    )


def _resource_instance(payload: dict[str, Any]) -> MagicMock:
    ri = MagicMock()
    ri.to_dict.return_value = payload
    return ri


class _FakeDynNotFoundError(Exception):
    """Stand-in raised by the fake DynamicClient resource methods.

    Patched in via ``kubernetes.dynamic.exceptions.NotFoundError`` so
    the wrapper's ``except DynNotFound`` clauses catch our stand-in.
    """


def _install_fake_dynamic(
    client: KubernetesDynamicClient,
    fake_resource: Any,
) -> MagicMock:
    fake_dyn = MagicMock()
    fake_dyn.resources.get.return_value = fake_resource
    client._dynamic = fake_dyn
    return fake_dyn


# ---- split_kind ---------------------------------------------------


def test_split_kind_bare_core_v1() -> None:
    assert split_kind("Pod") == ("v1", "Pod")
    assert split_kind("Namespace") == ("v1", "Namespace")
    assert split_kind("Service") == ("v1", "Service")


def test_split_kind_bare_apps_v1() -> None:
    assert split_kind("Deployment") == ("apps/v1", "Deployment")
    assert split_kind("StatefulSet") == ("apps/v1", "StatefulSet")


def test_split_kind_qualified_crd() -> None:
    assert split_kind("helm.toolkit.fluxcd.io/v2/HelmRelease") == (
        "helm.toolkit.fluxcd.io/v2",
        "HelmRelease",
    )


def test_split_kind_two_part_path() -> None:
    assert split_kind("v1/Namespace") == ("v1", "Namespace")


def test_split_kind_unknown_bare_raises() -> None:
    with pytest.raises(KeyError):
        split_kind("MyCustomResource")


def test_split_kind_table_covers_common_kinds() -> None:
    for required in ("Pod", "Service", "Deployment", "Job", "Namespace"):
        assert required in DEFAULT_API_VERSION_FOR_KIND


# ---- _decode_ca_data ---------------------------------------------


def test_decode_ca_data_handles_base64_pem() -> None:
    pem = b"-----BEGIN CERTIFICATE-----\nMIIC\n-----END CERTIFICATE-----\n"
    encoded = base64.b64encode(pem).decode()
    assert _decode_ca_data(encoded) == pem


def test_decode_ca_data_falls_back_to_raw_pem() -> None:
    """k8s_native passes the PEM through directly; not base64."""
    pem = "-----BEGIN CERTIFICATE-----\nraw\n-----END CERTIFICATE-----\n"
    assert _decode_ca_data(pem) == pem.encode("utf-8")


def test_decode_ca_data_empty_returns_none() -> None:
    """AKS hands us an empty CA — the admin kubeconfig carries it."""
    assert _decode_ca_data("") is None


# ---- token refresh -----------------------------------------------


def test_init_uses_initial_token() -> None:
    client = _make_client(token_provider=lambda: "tok-initial")
    assert client._config.api_key == {"authorization": "Bearer tok-initial"}


def test_refresh_token_remints_via_provider() -> None:
    tokens = iter(["tok-1", "tok-2", "tok-3"])
    client = KubernetesDynamicClient(
        endpoint="https://cluster.example.com",
        ca_data=base64.b64encode(b"fake-ca").decode(),
        token_provider=lambda: next(tokens),
    )
    assert client._config.api_key == {"authorization": "Bearer tok-1"}
    client._refresh_token()
    assert client._config.api_key == {"authorization": "Bearer tok-2"}
    client._refresh_token()
    assert client._config.api_key == {"authorization": "Bearer tok-3"}


def test_refresh_token_noop_when_provider_returns_empty() -> None:
    """The kubeconfig path (from_api_client) uses a no-op provider so
    ``_refresh_token`` shouldn't clobber whatever auth the loaded
    kubeconfig populated on the ApiClient."""
    client = KubernetesDynamicClient.from_api_client(
        api_client=MagicMock(configuration=MagicMock(api_key={"x": "y"})),
    )
    pre = dict(client._config.api_key)
    client._refresh_token()
    assert dict(client._config.api_key) == pre


# ---- server_side_apply -------------------------------------------


def _ssa_test_setup(
    *,
    pre_get_payload: dict[str, Any] | None,
    post_apply_payload: dict[str, Any],
    pre_get_raises_404: bool = False,
) -> tuple[KubernetesDynamicClient, MagicMock, MagicMock]:
    token_provider = MagicMock(side_effect=["tok-1", "tok-2"])
    client = KubernetesDynamicClient(
        endpoint="https://cluster.example.com",
        ca_data=base64.b64encode(b"fake-ca").decode(),
        token_provider=token_provider,
    )
    fake_resource = MagicMock()
    if pre_get_raises_404:
        fake_resource.get.side_effect = _FakeDynNotFoundError()
    else:
        fake_resource.get.return_value = _resource_instance(pre_get_payload or {})
    fake_resource.server_side_apply.return_value = _resource_instance(post_apply_payload)
    _install_fake_dynamic(client, fake_resource)
    return client, fake_resource, token_provider


def test_ssa_returns_created_when_pre_get_404() -> None:
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, fake_resource, token_provider = _ssa_test_setup(
            pre_get_payload=None,
            post_apply_payload={"metadata": {"name": "x", "generation": 1}},
            pre_get_raises_404=True,
        )
        outcome = client.server_side_apply(
            namespace="ns",
            manifest={
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "x"},
                "spec": {"replicas": 3},
            },
            dry_run=False,
        )
    assert outcome == "created"
    _, kwargs = fake_resource.server_side_apply.call_args
    assert kwargs["field_manager"] == "astrolift"
    assert kwargs["force_conflicts"] is True
    assert kwargs["namespace"] == "ns"
    assert "dry_run" not in kwargs
    assert token_provider.call_count >= 2


def test_ssa_forces_conflicts_by_default() -> None:
    """Regression for the agent-deploy 409 ``FieldManagerConflict``.

    ``build_agent_manifests`` applies the ``astrolift-system`` Namespace
    with ``astrolift.io/managed-by: platform`` through the cluster
    drivers' ``apply_manifests`` → this helper. That label key is already
    owned by other field managers (legacy ``OpenAPI-Generator`` and the
    bootstrap's ``astrolift-control-plane``), so a non-forcing apply was
    rejected with a 409 and agent dispatch failed. The platform is the
    authoritative manager for the resources it applies, so the helper
    must force conflicts by default.
    """
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, fake_resource, _tok = _ssa_test_setup(
            pre_get_payload={"metadata": {"name": "astrolift-system", "generation": 1}},
            post_apply_payload={"metadata": {"name": "astrolift-system", "generation": 1}},
        )
        client.server_side_apply(
            namespace=None,
            manifest={
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": "astrolift-system",
                    "labels": {"astrolift.io/managed-by": "platform"},
                },
            },
            dry_run=False,
        )
    _, kwargs = fake_resource.server_side_apply.call_args
    assert kwargs["force_conflicts"] is True


def test_ssa_respects_explicit_force_conflicts_override() -> None:
    """An explicit ``force_conflicts=False`` still defers to the owner."""
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, fake_resource, _tok = _ssa_test_setup(
            pre_get_payload={"metadata": {"name": "x", "generation": 1}},
            post_apply_payload={"metadata": {"name": "x", "generation": 1}},
        )
        client.server_side_apply(
            namespace="ns",
            manifest={
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "x"},
            },
            dry_run=False,
            force_conflicts=False,
        )
    _, kwargs = fake_resource.server_side_apply.call_args
    assert kwargs["force_conflicts"] is False


def test_ssa_returns_updated_when_generation_bumps() -> None:
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, _resource, _tok = _ssa_test_setup(
            pre_get_payload={"metadata": {"name": "x", "generation": 3}},
            post_apply_payload={"metadata": {"name": "x", "generation": 4}},
        )
        outcome = client.server_side_apply(
            namespace="ns",
            manifest={
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "x"},
            },
            dry_run=False,
        )
    assert outcome == "updated"


def test_ssa_returns_unchanged_when_generation_stable() -> None:
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, _resource, _tok = _ssa_test_setup(
            pre_get_payload={"metadata": {"name": "x", "generation": 7}},
            post_apply_payload={"metadata": {"name": "x", "generation": 7}},
        )
        outcome = client.server_side_apply(
            namespace="ns",
            manifest={
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "x"},
            },
            dry_run=False,
        )
    assert outcome == "unchanged"


def test_ssa_falls_back_to_resource_version() -> None:
    """ConfigMap / Secret don't carry generation; rv comparison instead."""
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, _resource, _tok = _ssa_test_setup(
            pre_get_payload={"metadata": {"name": "cm", "resourceVersion": "10"}},
            post_apply_payload={"metadata": {"name": "cm", "resourceVersion": "11"}},
        )
        outcome = client.server_side_apply(
            namespace="ns",
            manifest={
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "cm"},
            },
            dry_run=False,
        )
    assert outcome == "updated"


def test_ssa_dry_run_translates_to_all_string() -> None:
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, fake_resource, _tok = _ssa_test_setup(
            pre_get_payload=None,
            post_apply_payload={"metadata": {"name": "x", "generation": 1}},
            pre_get_raises_404=True,
        )
        client.server_side_apply(
            namespace="ns",
            manifest={
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "x"},
            },
            dry_run=True,
        )
    _, kwargs = fake_resource.server_side_apply.call_args
    assert kwargs["dry_run"] == "All"


def test_ssa_requires_kind() -> None:
    client = _make_client()
    with pytest.raises(ValueError, match="kind"):
        client.server_side_apply(
            namespace="ns",
            manifest={"apiVersion": "v1", "metadata": {"name": "x"}},
            dry_run=False,
        )


def test_ssa_requires_name() -> None:
    client = _make_client()
    _install_fake_dynamic(client, MagicMock())
    with pytest.raises(ValueError, match="name"):
        client.server_side_apply(
            namespace="ns",
            manifest={
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {},
            },
            dry_run=False,
        )


# ---- get ---------------------------------------------------------


def test_get_returns_dict_on_success() -> None:
    client = _make_client()
    fake_resource = MagicMock()
    fake_resource.get.return_value = _resource_instance(
        {"metadata": {"name": "api"}, "spec": {"replicas": 3}},
    )
    _install_fake_dynamic(client, fake_resource)
    result = client.get(kind="Deployment", namespace="ns", name="api")
    assert result == {"metadata": {"name": "api"}, "spec": {"replicas": 3}}


def test_get_returns_none_on_404() -> None:
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client = _make_client()
        fake_resource = MagicMock()
        fake_resource.get.side_effect = _FakeDynNotFoundError()
        _install_fake_dynamic(client, fake_resource)
        assert client.get(kind="Pod", namespace="ns", name="gone") is None


def test_get_resolves_crd_path() -> None:
    client = _make_client()
    fake_dyn = _install_fake_dynamic(
        client,
        MagicMock(
            get=MagicMock(return_value=_resource_instance({"metadata": {"name": "hr"}})),
        ),
    )
    client.get(
        kind="helm.toolkit.fluxcd.io/v2/HelmRelease",
        namespace="flux",
        name="hr",
    )
    fake_dyn.resources.get.assert_called_with(
        api_version="helm.toolkit.fluxcd.io/v2",
        kind="HelmRelease",
    )


# ---- delete ------------------------------------------------------


def test_delete_returns_true_on_success() -> None:
    client = _make_client()
    fake_resource = MagicMock()
    _install_fake_dynamic(client, fake_resource)
    assert client.delete(kind="Pod", namespace="ns", name="x") is True
    fake_resource.delete.assert_called_with(name="x", namespace="ns")


def test_delete_returns_false_on_404() -> None:
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client = _make_client()
        fake_resource = MagicMock()
        fake_resource.delete.side_effect = _FakeDynNotFoundError()
        _install_fake_dynamic(client, fake_resource)
        assert client.delete(kind="Pod", namespace="ns", name="gone") is False


# ---- get_namespace -----------------------------------------------


def test_get_namespace_returns_dict() -> None:
    client = _make_client()
    fake_resource = MagicMock()
    fake_resource.get.return_value = _resource_instance(
        {
            "metadata": {"name": "ns", "labels": {"k": "v"}, "annotations": {}},
            "status": {"phase": "Active"},
        },
    )
    _install_fake_dynamic(client, fake_resource)
    ns = client.get_namespace(name="ns")
    assert ns is not None
    assert ns["metadata"]["name"] == "ns"


def test_get_namespace_returns_none_on_404() -> None:
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client = _make_client()
        fake_resource = MagicMock()
        fake_resource.get.side_effect = _FakeDynNotFoundError()
        _install_fake_dynamic(client, fake_resource)
        assert client.get_namespace(name="missing") is None


# ---- exec_in_pod -------------------------------------------------


def _make_fake_ws() -> MagicMock:
    ws = MagicMock()
    ws.is_open.side_effect = [True, False]
    ws.peek_stdout.side_effect = [True, False]
    ws.peek_stderr.side_effect = [False, False]
    ws.read_stdout.return_value = "hello\n"
    ws.read_stderr.return_value = ""
    ws.read_channel.return_value = ""
    return ws


def test_exec_in_pod_returns_captured_streams() -> None:
    client = _make_client()
    fake_ws = _make_fake_ws()
    with patch("kubernetes.stream.stream", return_value=fake_ws):
        result = client.exec_in_pod(
            namespace="ns",
            pod="mypod",
            container="app",
            command=["sh", "-c", "echo hello"],
        )
    assert isinstance(result, ExecResult)
    assert result.stdout == "hello\n"
    assert result.exit_code == 0
    fake_ws.close.assert_called_once()


def test_exec_in_pod_decodes_non_zero_exit() -> None:
    client = _make_client()
    fake_ws = MagicMock()
    fake_ws.is_open.side_effect = [True, False]
    fake_ws.peek_stdout.side_effect = [False, False]
    fake_ws.peek_stderr.side_effect = [True, False]
    fake_ws.read_stdout.return_value = ""
    fake_ws.read_stderr.return_value = "boom\n"
    fake_ws.read_channel.return_value = (
        '{"status": "Failure", "details": {"causes": [{"reason": "ExitCode", "message": "7"}]}}'
    )
    with patch("kubernetes.stream.stream", return_value=fake_ws):
        result = client.exec_in_pod(
            namespace="ns",
            pod="mypod",
            container="app",
            command=["false"],
        )
    assert result.exit_code == 7
    assert result.stderr == "boom\n"


# ---- port_forward ------------------------------------------------


def test_port_forward_returns_handle_with_first_pair() -> None:
    client = _make_client()
    fake_pf = MagicMock()
    with patch("kubernetes.stream.portforward", return_value=fake_pf) as pf_mock:
        handle = client.port_forward(
            namespace="ns",
            pod="mypod",
            ports=[(15432, 5432), (16379, 6379)],
        )
    assert isinstance(handle, PortForwardHandle)
    assert handle.local_port == 15432
    assert handle.remote_port == 5432
    _, kwargs = pf_mock.call_args
    assert kwargs["ports"] == "5432,6379"


def test_port_forward_close_swallows_websocket_errors() -> None:
    client = _make_client()
    fake_pf = MagicMock()
    fake_pf.close.side_effect = RuntimeError("websocket already dead")
    with patch("kubernetes.stream.portforward", return_value=fake_pf):
        handle = client.port_forward(
            namespace="ns",
            pod="mypod",
            ports=[(15432, 5432)],
        )
    handle.close()


# ---- DynamicClient lazy construction -----------------------------


def test_dynamic_client_built_once_and_reused() -> None:
    client = _make_client()
    with patch("kubernetes.dynamic.DynamicClient") as dyn_cls:
        dyn_cls.return_value = MagicMock()
        first = client._dyn()
        second = client._dyn()
    assert first is second
    dyn_cls.assert_called_once_with(client._api_client)


# ---- from_api_client alternate constructor ------------------------


def test_from_api_client_skips_token_plumbing() -> None:
    """k8s_native loads kubeconfig externally — the constructor should
    skip building its own ApiClient and instead carry the one the
    caller passes."""
    api_client = MagicMock()
    api_client.configuration = MagicMock()
    helper = KubernetesDynamicClient.from_api_client(api_client=api_client)
    assert helper._api_client is api_client
    # Default token provider is the no-op; calling it returns ""
    assert helper._token_provider() == ""


def test_not_found_error_is_exception() -> None:
    assert issubclass(NotFoundError, Exception)
