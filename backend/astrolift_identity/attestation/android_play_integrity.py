"""Android Play Integrity verifier (#496).

Google's Play Integrity API exposes a server endpoint that takes the
encrypted token the device produced and returns the decrypted +
verified claims. We POST the token to
``https://playintegrity.googleapis.com/v1/<package>:decodeIntegrityToken``
and validate the response.

Two API-key options are supported:

* **Service-account JSON** in ``GOOGLE_PLAY_INTEGRITY_API_KEY`` —
  parsed and used to mint an OAuth bearer token. Production setting.
* **Static API key** (the simple case for dev / fixture envs) —
  passed as ``?key=`` query parameter. Useful for the test suite
  and for installs that haven't wired a service account yet.

The HTTP transport is indirected through :func:`_post` so tests can
monkeypatch the response without touching the network.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from astrolift_identity.attestation import (
    AttestationError,
    VerifiedIntegrity,
)

log = logging.getLogger(__name__)

_PLAY_INTEGRITY_BASE = "https://playintegrity.googleapis.com/v1"

# Google publishes a list of expected verdict strings; we hard-code
# the "device passes" tag here so a typo in the response can't
# silently let a rooted device through. Keep this list in lockstep
# with Google's docs:
# https://developer.android.com/google/play/integrity/verdicts
_DEVICE_INTEGRITY_PASS = "MEETS_DEVICE_INTEGRITY"
_DEVICE_BASIC_PASS = "MEETS_BASIC_INTEGRITY"
_DEVICE_STRONG_PASS = "MEETS_STRONG_INTEGRITY"
_APP_INTEGRITY_PASS = "PLAY_RECOGNIZED"
_APP_INTEGRITY_UNRECOGNIZED = "UNRECOGNIZED_VERSION"
_APP_INTEGRITY_UNEVALUATED = "UNEVALUATED"


def verify_integrity_token(
    *,
    token: str,
    expected_nonce: str,
    expected_package: str,
    api_key: str,
    require_strong_integrity: bool = False,
) -> VerifiedIntegrity:
    """Decode + validate a Play Integrity token.

    ``require_strong_integrity`` (off by default) only accepts the
    ``MEETS_STRONG_INTEGRITY`` verdict — turn on for installs that
    want to exclude Android devices without hardware-backed key-
    storage (older / cheaper Android devices). The default of off
    accepts ``MEETS_DEVICE_INTEGRITY`` which is the right call for
    most enterprise apps.

    Raises :class:`AttestationError` on any failure: empty token,
    upstream error, nonce mismatch, rooted device, tampered app,
    package mismatch, or unexpected response shape.
    """
    if not token:
        raise AttestationError("EMPTY_TOKEN", "integrity token is empty")
    if not expected_package:
        raise AttestationError(
            "MISSING_PACKAGE",
            "expected_package is required (set ANDROID_PACKAGE_NAME)",
        )
    if not api_key:
        raise AttestationError(
            "MISSING_API_KEY",
            "GOOGLE_PLAY_INTEGRITY_API_KEY is not configured",
        )

    url = f"{_PLAY_INTEGRITY_BASE}/{expected_package}:decodeIntegrityToken"
    try:
        body = _post(url=url, api_key=api_key, payload={"integrityToken": token})
    except AttestationError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface as a typed code
        raise AttestationError(
            "UPSTREAM_FAILED",
            f"Play Integrity API call failed: {exc}",
        ) from exc

    payload = body.get("tokenPayloadExternal") or body
    if not isinstance(payload, dict):
        raise AttestationError(
            "INVALID_RESPONSE",
            "Play Integrity response missing tokenPayloadExternal",
        )

    request_details = payload.get("requestDetails") or {}
    nonce_in_token = request_details.get("nonce") or ""
    package_in_token = request_details.get("requestPackageName") or ""

    if package_in_token != expected_package:
        raise AttestationError(
            "PACKAGE_MISMATCH",
            f"token requestPackageName {package_in_token!r} != expected {expected_package!r}",
        )

    if nonce_in_token != expected_nonce:
        raise AttestationError(
            "NONCE_MISMATCH",
            "token nonce does not match server-issued challenge",
        )

    device_integrity = payload.get("deviceIntegrity") or {}
    verdicts = device_integrity.get("deviceRecognitionVerdict") or []
    if not isinstance(verdicts, list):
        verdicts = [verdicts] if verdicts else []
    verdict_set = {v for v in verdicts if isinstance(v, str)}
    acceptable = (
        {_DEVICE_STRONG_PASS} if require_strong_integrity else {_DEVICE_INTEGRITY_PASS, _DEVICE_STRONG_PASS}
    )
    if not (verdict_set & acceptable):
        raise AttestationError(
            "DEVICE_REJECTED",
            f"deviceRecognitionVerdict={sorted(verdict_set)!r} lacks {sorted(acceptable)!r} "
            "(rooted / emulator / non-Play device)",
        )

    app_integrity = payload.get("appIntegrity") or {}
    app_verdict = app_integrity.get("appRecognitionVerdict") or ""
    if app_verdict != _APP_INTEGRITY_PASS:
        raise AttestationError(
            "APP_TAMPERED",
            f"appRecognitionVerdict={app_verdict!r} (tampered / repackaged binary)",
        )

    account_details = payload.get("accountDetails") or {}
    account_verdict = account_details.get("appLicensingVerdict") or ""

    return VerifiedIntegrity(
        package_name=package_in_token,
        device_verdict=tuple(sorted(verdict_set)),
        app_verdict=app_verdict,
        account_verdict=account_verdict,
        raw=payload,
    )


def _post(*, url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST + JSON-decode. Split out so the test suite can monkeypatch
    this with a fixed response without standing up a fake HTTP server.

    Production code paths can swap in a service-account bearer token by
    overriding this function — :func:`register_transport` is the
    documented hook.
    """
    transport = _TRANSPORT or _default_transport
    return transport(url=url, api_key=api_key, payload=payload)


def _default_transport(*, url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    params: dict[str, str] = {}
    if api_key.strip().startswith("{"):
        # Service-account JSON — caller is expected to have wired a
        # bearer-token transport via ``register_transport``. Default
        # transport refuses to silently fall back to anonymous.
        raise AttestationError(
            "MISSING_TRANSPORT",
            "service-account JSON detected but no bearer-token transport registered",
        )
    params["key"] = api_key
    response = requests.post(url, params=params, headers=headers, data=json.dumps(payload), timeout=15)
    if response.status_code != 200:
        raise AttestationError(
            "UPSTREAM_ERROR",
            f"Play Integrity API returned {response.status_code}: {response.text[:200]!r}",
        )
    try:
        return response.json()
    except ValueError as exc:
        raise AttestationError(
            "INVALID_RESPONSE",
            f"Play Integrity response was not JSON: {exc}",
        ) from exc


_TRANSPORT = None


def register_transport(transport) -> None:
    """Install a custom transport callable.

    The callable must accept ``url``, ``api_key``, and ``payload``
    keyword args and return the decoded JSON dict. Lets installs that
    use service-account JSON wire OAuth at deploy time without
    teaching this module about google-auth.
    """
    global _TRANSPORT
    _TRANSPORT = transport


__all__ = [
    "register_transport",
    "verify_integrity_token",
]
