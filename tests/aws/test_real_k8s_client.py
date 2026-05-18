"""Tests for ``_RealK8sClient`` — the live-cluster wrapper.

The driver-level tests in ``test_cluster_eks.py`` mock the whole
client via ``k8s_client_factory``. These tests target the wrapper
itself: the DynamicClient call shape, the created/updated/unchanged
classification, 404 mapping, kind→apiVersion resolution, and the
per-call token refresh that keeps long-running workflows alive.

We don't talk to a real apiserver — every ``DynamicClient`` /
``CoreV1Api`` / streaming primitive is patched in. The integration
verification lives outside CI (operator re-runs
``InstallClusterPrereqsWorkflow`` against prod EKS after this lands).
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from _sdk.cluster import ExecResult
from aws.cluster_eks import (
    _DEFAULT_API_VERSION_FOR_KIND,
    _NotFoundError,
    _PortForwardHandle,
    _RealK8sClient,
    _split_kind,
)

# ---- helpers ------------------------------------------------------


def _make_client(token_provider: Any | None = None) -> _RealK8sClient:
    """Build a ``_RealK8sClient`` against a fake endpoint.

    The constructor decodes the CA into a tempfile and builds an
    ``ApiClient``; that work is real but has no network side effects.
    Per-method tests then patch ``DynamicClient`` / ``CoreV1Api`` to
    intercept the live calls.
    """
    return _RealK8sClient(
        endpoint="https://eks.example.com",
        ca_data=base64.b64encode(b"-----BEGIN CERTIFICATE-----\nfake\n").decode(),
        token_provider=token_provider or (lambda: "tok-initial"),
    )


def _resource_instance(payload: dict[str, Any]) -> MagicMock:
    """Mimic the kubernetes ``ResourceInstance`` (.to_dict() shape)."""
    ri = MagicMock()
    ri.to_dict.return_value = payload
    return ri


class _FakeDynNotFoundError(Exception):
    """Stand-in raised by the fake DynamicClient resource methods.

    We patch ``kubernetes.dynamic.exceptions.NotFoundError`` to this
    class for the duration of each test so the wrapper's
    ``except DynNotFound`` clauses catch our stand-in.
    """


# ---- _split_kind --------------------------------------------------


def test_split_kind_bare_core_v1() -> None:
    assert _split_kind("Pod") == ("v1", "Pod")
    assert _split_kind("Namespace") == ("v1", "Namespace")
    assert _split_kind("Service") == ("v1", "Service")


def test_split_kind_bare_apps_v1() -> None:
    assert _split_kind("Deployment") == ("apps/v1", "Deployment")
    assert _split_kind("StatefulSet") == ("apps/v1", "StatefulSet")


def test_split_kind_qualified_crd() -> None:
    assert _split_kind("helm.toolkit.fluxcd.io/v2/HelmRelease") == (
        "helm.toolkit.fluxcd.io/v2",
        "HelmRelease",
    )


def test_split_kind_unknown_bare_raises() -> None:
    with pytest.raises(KeyError):
        _split_kind("MyCustomResource")


def test_split_kind_table_covers_common_kinds() -> None:
    """Sanity that the lookup table didn't lose an entry in a merge."""
    for required in ("Pod", "Service", "Deployment", "Job", "Namespace"):
        assert required in _DEFAULT_API_VERSION_FOR_KIND


# ---- token refresh -----------------------------------------------


def test_init_uses_initial_token() -> None:
    client = _make_client(token_provider=lambda: "tok-initial")
    assert client._config.api_key == {"authorization": "Bearer tok-initial"}


def test_refresh_token_remints_via_provider() -> None:
    """Each top-level op re-mints; long-running workflows can't 401
    mid-flight even if a single client is held across activities."""
    tokens = iter(["tok-1", "tok-2", "tok-3"])
    client = _RealK8sClient(
        endpoint="https://eks.example.com",
        ca_data=base64.b64encode(b"fake-ca").decode(),
        token_provider=lambda: next(tokens),
    )
    # __init__ consumed "tok-1".
    assert client._config.api_key == {"authorization": "Bearer tok-1"}
    client._refresh_token()
    assert client._config.api_key == {"authorization": "Bearer tok-2"}
    client._refresh_token()
    assert client._config.api_key == {"authorization": "Bearer tok-3"}


def _install_fake_dynamic(client: _RealK8sClient, fake_resource: Any) -> MagicMock:
    """Force the lazy dynamic-client builder to hand back a mock
    whose ``.resources.get(...)`` returns ``fake_resource``."""
    fake_dyn = MagicMock()
    fake_dyn.resources.get.return_value = fake_resource
    client._dynamic = fake_dyn
    return fake_dyn


# ---- server_side_apply -------------------------------------------


def _ssa_test_setup(
    *,
    pre_get_payload: dict[str, Any] | None,
    post_apply_payload: dict[str, Any],
    pre_get_raises_404: bool = False,
) -> tuple[_RealK8sClient, MagicMock, MagicMock]:
    """Build a client whose dynamic resource returns the configured
    pre-GET + post-apply payloads. Returns (client, fake_resource,
    token_provider_mock) for assertions."""
    token_provider = MagicMock(side_effect=["tok-1", "tok-2"])
    client = _RealK8sClient(
        endpoint="https://eks.example.com",
        ca_data=base64.b64encode(b"fake-ca").decode(),
        token_provider=token_provider,
    )
    # __init__ consumed "tok-1".
    fake_resource = MagicMock()
    if pre_get_raises_404:
        fake_resource.get.side_effect = _FakeDynNotFoundError()
    else:
        fake_resource.get.return_value = _resource_instance(pre_get_payload or {})
    fake_resource.server_side_apply.return_value = _resource_instance(post_apply_payload)
    _install_fake_dynamic(client, fake_resource)
    return client, fake_resource, token_provider


def test_server_side_apply_returns_created_when_pre_get_404() -> None:
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
    # The apply call carried the right field_manager + no dry_run kwarg.
    _, kwargs = fake_resource.server_side_apply.call_args
    assert kwargs["field_manager"] == "astrolift"
    assert kwargs["force_conflicts"] is False
    assert kwargs["namespace"] == "ns"
    assert "dry_run" not in kwargs
    # Token was refreshed before the call (provider called > once).
    assert token_provider.call_count >= 2


def test_server_side_apply_returns_updated_when_generation_bumps() -> None:
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


def test_server_side_apply_returns_unchanged_when_generation_stable() -> None:
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


def test_server_side_apply_falls_back_to_resource_version_when_no_generation() -> None:
    """ConfigMap / Secret don't carry generation; classifier must
    fall back to resourceVersion to avoid mis-labeling every
    re-apply as ``unchanged``."""
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, _resource, _tok = _ssa_test_setup(
            pre_get_payload={
                "metadata": {"name": "cm", "resourceVersion": "10"},
            },
            post_apply_payload={
                "metadata": {"name": "cm", "resourceVersion": "11"},
            },
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


def test_server_side_apply_unchanged_via_resource_version() -> None:
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, _resource, _tok = _ssa_test_setup(
            pre_get_payload={
                "metadata": {"name": "cm", "resourceVersion": "10"},
            },
            post_apply_payload={
                "metadata": {"name": "cm", "resourceVersion": "10"},
            },
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
    assert outcome == "unchanged"


def test_server_side_apply_dry_run_translates_to_all_string() -> None:
    """bool dry_run must reach the dynamic client as the literal
    string ``"All"`` (the kubernetes API expects that wire form)."""
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


def test_server_side_apply_requires_kind() -> None:
    client = _make_client()
    with pytest.raises(ValueError, match="kind"):
        client.server_side_apply(
            namespace="ns",
            manifest={"apiVersion": "v1", "metadata": {"name": "x"}},
            dry_run=False,
        )


def test_server_side_apply_requires_name() -> None:
    client = _make_client()
    # Need a resource to get past resource resolution.
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


def test_server_side_apply_defaults_api_version_when_missing() -> None:
    """Some callers omit apiVersion; default to ``v1`` matches what
    k8s_native/management.py does for the same case."""
    with patch(
        "kubernetes.dynamic.exceptions.NotFoundError",
        _FakeDynNotFoundError,
    ):
        client, _resource, _tok = _ssa_test_setup(
            pre_get_payload=None,
            post_apply_payload={"metadata": {"name": "x", "generation": 1}},
            pre_get_raises_404=True,
        )
        outcome = client.server_side_apply(
            namespace=None,
            manifest={
                "kind": "Namespace",
                "metadata": {"name": "x"},
            },
            dry_run=False,
        )
    assert outcome == "created"
    fake_dyn = client._dynamic
    fake_dyn.resources.get.assert_called_with(
        api_version="v1",
        kind="Namespace",
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


def test_get_refreshes_token() -> None:
    token_provider = MagicMock(side_effect=["tok-1", "tok-2"])
    client = _RealK8sClient(
        endpoint="https://eks.example.com",
        ca_data=base64.b64encode(b"ca").decode(),
        token_provider=token_provider,
    )
    fake_resource = MagicMock()
    fake_resource.get.return_value = _resource_instance({"metadata": {"name": "x"}})
    _install_fake_dynamic(client, fake_resource)
    client.get(kind="Pod", namespace="ns", name="x")
    # __init__ + before-get = 2 mints.
    assert token_provider.call_count == 2


def test_get_resolves_crd_path() -> None:
    """``group/version/Kind`` form routes through the right resource
    lookup; this is the HelmRelease path that drives bootstrap."""
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


def test_delete_refreshes_token() -> None:
    token_provider = MagicMock(side_effect=["tok-1", "tok-2"])
    client = _RealK8sClient(
        endpoint="https://eks.example.com",
        ca_data=base64.b64encode(b"ca").decode(),
        token_provider=token_provider,
    )
    _install_fake_dynamic(client, MagicMock())
    client.delete(kind="Pod", namespace="ns", name="x")
    assert token_provider.call_count == 2


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
    assert ns["status"]["phase"] == "Active"


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


def test_get_namespace_uses_core_v1() -> None:
    client = _make_client()
    fake_dyn = _install_fake_dynamic(
        client,
        MagicMock(get=MagicMock(return_value=_resource_instance({"metadata": {"name": "n"}}))),
    )
    client.get_namespace(name="n")
    fake_dyn.resources.get.assert_called_with(api_version="v1", kind="Namespace")


# ---- exec_in_pod -------------------------------------------------


def _make_fake_ws() -> MagicMock:
    """Build a fake websocket response matching kubernetes.stream.stream."""
    ws = MagicMock()
    # First update opens the channel with output, second closes it.
    ws.is_open.side_effect = [True, False]
    ws.peek_stdout.side_effect = [True, False]
    ws.peek_stderr.side_effect = [False, False]
    ws.read_stdout.return_value = "hello\n"
    ws.read_stderr.return_value = ""
    ws.read_channel.return_value = ""  # exit 0 = no Failure payload
    return ws


def test_exec_in_pod_returns_captured_streams() -> None:
    client = _make_client()
    fake_ws = _make_fake_ws()
    with patch("kubernetes.stream.stream", return_value=fake_ws) as stream_mock:
        result = client.exec_in_pod(
            namespace="ns",
            pod="mypod",
            container="app",
            command=["sh", "-c", "echo hello"],
        )
    assert isinstance(result, ExecResult)
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    # connect_get_namespaced_pod_exec was passed pod+namespace+command.
    args, kwargs = stream_mock.call_args
    assert args[1] == "mypod"
    assert args[2] == "ns"
    assert kwargs["container"] == "app"
    assert kwargs["command"] == ["sh", "-c", "echo hello"]
    fake_ws.close.assert_called_once()


def test_exec_in_pod_decodes_non_zero_exit() -> None:
    """``v1.Status`` exit-code frame on channel 3 maps to ExecResult.exit_code."""
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


def test_exec_in_pod_refreshes_token() -> None:
    token_provider = MagicMock(side_effect=["tok-1", "tok-2"])
    client = _RealK8sClient(
        endpoint="https://eks.example.com",
        ca_data=base64.b64encode(b"ca").decode(),
        token_provider=token_provider,
    )
    fake_ws = _make_fake_ws()
    with patch("kubernetes.stream.stream", return_value=fake_ws):
        client.exec_in_pod(
            namespace="ns",
            pod="mypod",
            container="app",
            command=["true"],
        )
    assert token_provider.call_count == 2


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
    assert isinstance(handle, _PortForwardHandle)
    assert handle.local_port == 15432
    assert handle.remote_port == 5432
    # The wire takes a CSV of *remote* ports.
    _, kwargs = pf_mock.call_args
    assert kwargs["ports"] == "5432,6379"


def test_port_forward_close_passes_through() -> None:
    client = _make_client()
    fake_pf = MagicMock()
    with patch("kubernetes.stream.portforward", return_value=fake_pf):
        handle = client.port_forward(
            namespace="ns",
            pod="mypod",
            ports=[(15432, 5432)],
        )
    handle.close()
    fake_pf.close.assert_called_once()


def test_port_forward_close_swallows_websocket_errors() -> None:
    """``close()`` is best-effort — a failed close during shutdown
    mustn't mask the original work the caller was doing."""
    client = _make_client()
    fake_pf = MagicMock()
    fake_pf.close.side_effect = RuntimeError("websocket already dead")
    with patch("kubernetes.stream.portforward", return_value=fake_pf):
        handle = client.port_forward(
            namespace="ns",
            pod="mypod",
            ports=[(15432, 5432)],
        )
    # Must not raise.
    handle.close()


def test_port_forward_socket_accessor() -> None:
    client = _make_client()
    fake_pf = MagicMock()
    fake_pf.socket.return_value = "fake-socket"
    with patch("kubernetes.stream.portforward", return_value=fake_pf):
        handle = client.port_forward(
            namespace="ns",
            pod="mypod",
            ports=[(15432, 5432), (16379, 6379)],
        )
    assert handle.socket(6379) == "fake-socket"
    fake_pf.socket.assert_called_with(6379)


def test_port_forward_refreshes_token() -> None:
    token_provider = MagicMock(side_effect=["tok-1", "tok-2"])
    client = _RealK8sClient(
        endpoint="https://eks.example.com",
        ca_data=base64.b64encode(b"ca").decode(),
        token_provider=token_provider,
    )
    with patch("kubernetes.stream.portforward", return_value=MagicMock()):
        client.port_forward(
            namespace="ns",
            pod="mypod",
            ports=[(15432, 5432)],
        )
    assert token_provider.call_count == 2


# ---- DynamicClient lazy construction -----------------------------


def test_dynamic_client_built_once_and_reused() -> None:
    """The DynamicClient discovers resources on construction; one
    build per ``_RealK8sClient`` keeps long-lived workers cheap."""
    client = _make_client()
    with patch("kubernetes.dynamic.DynamicClient") as dyn_cls:
        dyn_cls.return_value = MagicMock()
        first = client._dyn()
        second = client._dyn()
    assert first is second
    dyn_cls.assert_called_once_with(client._api_client)


# ---- _NotFoundError surface for callers --------------------------


def test_not_found_error_is_re_exported() -> None:
    """The driver-level call sites in cluster_eks.py catch this exact
    class; importing it from elsewhere should keep working."""
    # Sanity assertion: existing tests already import this — guard
    # against an accidental rename.
    assert issubclass(_NotFoundError, Exception)
