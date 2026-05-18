"""Tests for Azure's ``_build_k8s_client`` (#567).

The driver-level tests in ``test_cluster_aks.py`` mock the whole
client via ``k8s_client_factory``. This file exercises the production
factory: the DefaultAzureCredential → AAD-token → KubernetesDynamicClient
chain, and the per-op token refresh path.

No real Azure call happens — ``credential_factory`` is the injection
seam we test through.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from azure.cluster_aks import _AKS_AAD_SCOPE, _build_k8s_client, _NotFound, _RealK8sClient

# ---- alias sanity ------------------------------------------------


def test_real_k8s_client_is_shared_helper() -> None:
    """The local alias must be the shared helper itself — the #567 fix
    replaces a top-level ``raise NotImplementedError`` with a real
    factory that delegates to the shared body."""
    assert _RealK8sClient is KubernetesDynamicClient


def test_not_found_is_shared_helper_exception() -> None:
    from _sdk.k8s_dynamic_client import NotFoundError

    assert _NotFound is NotFoundError


# ---- _build_k8s_client wiring ------------------------------------


def _ca_b64() -> str:
    """A valid base64-PEM blob the shared helper can decode without
    talking to the filesystem."""
    return base64.b64encode(b"-----BEGIN CERTIFICATE-----\nfake\n").decode()


def test_build_returns_shared_helper() -> None:
    fake_credential = MagicMock()
    fake_credential.get_token.return_value = SimpleNamespace(
        token="tok-aad-1",
        expires_on=9999999999,
    )
    result = _build_k8s_client(
        endpoint="https://aks-prod.eastus.azmk8s.io",
        ca_data="",
        credential_factory=lambda: fake_credential,
    )
    assert isinstance(result, KubernetesDynamicClient)


def test_build_mints_initial_bearer_via_credential_factory() -> None:
    """Construction calls ``token_provider()`` once to populate the
    initial bearer on the kubernetes-client config."""
    fake_credential = MagicMock()
    fake_credential.get_token.return_value = SimpleNamespace(
        token="tok-aad-initial",
    )
    helper = _build_k8s_client(
        endpoint="https://aks-prod.eastus.azmk8s.io",
        ca_data=_ca_b64(),
        credential_factory=lambda: fake_credential,
    )
    assert helper._config.api_key == {
        "authorization": "Bearer tok-aad-initial",
    }
    # The credential was queried for the AKS AAD scope, not some
    # other resource — wrong scope yields a token AAD rejects.
    fake_credential.get_token.assert_called_with(_AKS_AAD_SCOPE)


def test_build_token_provider_remints_on_refresh() -> None:
    """AAD tokens are ~5min; the per-op refresh must re-call
    ``credential.get_token`` so long-running workflows can't 401."""
    fake_credential = MagicMock()
    fake_credential.get_token.side_effect = [
        SimpleNamespace(token="tok-1"),
        SimpleNamespace(token="tok-2"),
        SimpleNamespace(token="tok-3"),
    ]
    helper = _build_k8s_client(
        endpoint="https://aks.example.com",
        ca_data=_ca_b64(),
        credential_factory=lambda: fake_credential,
    )
    # __init__ consumed tok-1.
    assert helper._config.api_key == {"authorization": "Bearer tok-1"}
    helper._refresh_token()
    assert helper._config.api_key == {"authorization": "Bearer tok-2"}
    helper._refresh_token()
    assert helper._config.api_key == {"authorization": "Bearer tok-3"}


def test_build_credential_factory_invoked_once() -> None:
    """The credential is materialized at construction and the closure
    captures it; we shouldn't pay a fresh ``DefaultAzureCredential``
    resolution per token mint."""
    fake_credential = MagicMock()
    fake_credential.get_token.return_value = SimpleNamespace(token="tok")
    factory = MagicMock(return_value=fake_credential)
    _build_k8s_client(
        endpoint="https://aks.example.com",
        ca_data=_ca_b64(),
        credential_factory=factory,
    )
    factory.assert_called_once()


def test_build_handles_empty_ca_via_system_trust_bundle() -> None:
    """AKS's managed-cluster object doesn't expose the CA on its API
    model — the admin kubeconfig blob has it, but the
    ``(endpoint, ca_data)`` shape only gives us the endpoint. Empty
    ``ca_data`` must not crash; the kubernetes-client falls back to
    the system bundle (Azure-managed certs chain to a public CA)."""
    fake_credential = MagicMock()
    fake_credential.get_token.return_value = SimpleNamespace(token="tok")
    helper = _build_k8s_client(
        endpoint="https://aks-prod.eastus.azmk8s.io",
        ca_data="",
        credential_factory=lambda: fake_credential,
    )
    assert isinstance(helper, KubernetesDynamicClient)
    # No ssl_ca_cert configured when ca_data is empty (system bundle).
    assert helper._config.ssl_ca_cert is None or helper._config.ssl_ca_cert == ""


def test_build_token_coerces_to_str() -> None:
    """``DefaultAzureCredential.get_token`` returns an ``AccessToken``
    namedtuple-like with ``token`` attr; we must coerce to str even if
    a fake returns something exotic."""
    fake_credential = MagicMock()
    fake_credential.get_token.return_value = SimpleNamespace(token=12345)
    helper = _build_k8s_client(
        endpoint="https://aks.example.com",
        ca_data=_ca_b64(),
        credential_factory=lambda: fake_credential,
    )
    # The string form lands on the kubernetes-client config.
    assert helper._config.api_key["authorization"] == "Bearer 12345"
