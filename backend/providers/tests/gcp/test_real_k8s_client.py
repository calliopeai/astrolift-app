"""Tests for GCP's ``_build_k8s_client`` (#568).

The driver-level tests in ``test_cluster_gke.py`` mock the whole
client via ``k8s_client_factory``. This file exercises the production
factory: the google.auth ADC → Workload Identity bearer →
KubernetesDynamicClient chain, and the per-op refresh path that keeps
long-running workflows alive past the GKE token TTL.

No real Google call happens — ``credentials_factory`` is the injection
seam.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock, patch

from _sdk.k8s_dynamic_client import KubernetesDynamicClient
from gcp.cluster_gke import _build_k8s_client, _NotFound, _RealK8sClient

# ---- alias sanity ------------------------------------------------


def test_real_k8s_client_is_shared_helper() -> None:
    """The local alias must be the shared helper itself — the #568 fix
    replaces a top-level ``raise NotImplementedError`` with a real
    factory that delegates to the shared body."""
    assert _RealK8sClient is KubernetesDynamicClient


def test_not_found_is_shared_helper_exception() -> None:
    from _sdk.k8s_dynamic_client import NotFoundError

    assert _NotFound is NotFoundError


# ---- _build_k8s_client wiring ------------------------------------


def _ca_b64() -> str:
    return base64.b64encode(b"-----BEGIN CERTIFICATE-----\nfake\n").decode()


def _fake_creds(token: str = "tok-adc-initial") -> MagicMock:
    """A mock credential object that mimics google.auth.Credentials.

    ``refresh()`` is a no-op (the production helper uses
    ``_refresh_credentials`` which we patch out). ``token`` is the
    attribute the bearer is read from.
    """
    creds = MagicMock()
    creds.token = token
    return creds


def test_build_returns_shared_helper() -> None:
    creds = _fake_creds()
    with patch("gcp.cluster_gke._refresh_credentials"):
        result = _build_k8s_client(
            endpoint="https://gke-prod.example.com",
            ca_data=_ca_b64(),
            credentials_factory=lambda: (creds, "proj-1"),
        )
    assert isinstance(result, KubernetesDynamicClient)


def test_build_mints_initial_bearer_via_credentials() -> None:
    creds = _fake_creds(token="tok-adc-1")
    with patch("gcp.cluster_gke._refresh_credentials"):
        helper = _build_k8s_client(
            endpoint="https://gke-prod.example.com",
            ca_data=_ca_b64(),
            credentials_factory=lambda: (creds, "proj-1"),
        )
    assert helper._config.api_key == {
        "authorization": "Bearer tok-adc-1",
    }


def test_build_refreshes_creds_at_construction() -> None:
    """The factory refreshes once eagerly so an auth misconfig surfaces
    at driver-construction time, not on the first apply."""
    creds = _fake_creds()
    with patch("gcp.cluster_gke._refresh_credentials") as refresh:
        _build_k8s_client(
            endpoint="https://gke.example.com",
            ca_data=_ca_b64(),
            credentials_factory=lambda: (creds, "proj"),
        )
    refresh.assert_called_with(creds)


def test_build_token_provider_refreshes_each_call() -> None:
    """ADC tokens are ~60min; on each op the closure refreshes creds
    in place so the cached value stays fresh even across a long
    workflow."""
    creds = _fake_creds(token="tok-1")
    with patch("gcp.cluster_gke._refresh_credentials") as refresh:
        helper = _build_k8s_client(
            endpoint="https://gke.example.com",
            ca_data=_ca_b64(),
            credentials_factory=lambda: (creds, "proj"),
        )
        # Construction triggers two refresh calls: one eager
        # (so misconfig fails fast) + one inside the initial
        # ``token_provider()`` invocation that populates the bearer.
        baseline = refresh.call_count
        assert baseline >= 1
        helper._refresh_token()
        helper._refresh_token()
        # Each top-level op refreshes once more.
        assert refresh.call_count == baseline + 2


def test_build_token_provider_tolerates_refresh_failure() -> None:
    """A transient refresh failure mid-workflow must fall back to the
    cached token rather than crashing the activity. The kubernetes
    client surfaces the 401 if the token is genuinely expired."""
    creds = _fake_creds(token="tok-cached")

    call_count = [0]

    def _maybe_fail(c: Any) -> None:
        call_count[0] += 1
        if call_count[0] == 2:
            raise RuntimeError("transient google metadata server failure")

    with patch("gcp.cluster_gke._refresh_credentials", side_effect=_maybe_fail):
        helper = _build_k8s_client(
            endpoint="https://gke.example.com",
            ca_data=_ca_b64(),
            credentials_factory=lambda: (creds, "proj"),
        )
        # Should not raise even though refresh fails this round.
        helper._refresh_token()
    # The cached token survived.
    assert helper._config.api_key == {"authorization": "Bearer tok-cached"}


def test_build_falls_back_to_default_credentials_factory() -> None:
    """Operators don't pass a factory in production — the default is
    ``_default_credentials_factory`` which resolves ADC via
    ``google.auth.default``. We patch the module's default factory to
    confirm wiring without touching real Google."""
    creds = _fake_creds(token="tok-adc-default")
    with (
        patch(
            "gcp.cluster_gke._default_credentials_factory",
            return_value=(creds, "proj-default"),
        ),
        patch("gcp.cluster_gke._refresh_credentials"),
    ):
        helper = _build_k8s_client(
            endpoint="https://gke.example.com",
            ca_data=_ca_b64(),
        )
    assert helper._config.api_key == {
        "authorization": "Bearer tok-adc-default",
    }


def test_build_token_coerces_to_str() -> None:
    """``creds.token`` is normally a str but a None or non-str must not
    crash the bearer construction. None falls back to ``""``."""
    creds = MagicMock()
    creds.token = None
    with patch("gcp.cluster_gke._refresh_credentials"):
        helper = _build_k8s_client(
            endpoint="https://gke.example.com",
            ca_data=_ca_b64(),
            credentials_factory=lambda: (creds, "proj"),
        )
    # Initial mint: empty bearer because creds.token was None; the
    # apiserver will 401 on first op and the resolver layer surfaces
    # the empty UI state. We don't crash at construction.
    assert helper._config.api_key == {"authorization": "Bearer "}
