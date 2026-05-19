"""Mobile universal-link / app-link well-known endpoints (#541).

iOS Universal Links (``apple-app-site-association``) and Android App
Links (``assetlinks.json``) require the OS to fetch a JSON manifest from
``https://<host>/.well-known/...`` at install / first-launch time. If
the host returns a 404 — or returns the wrong ``Content-Type`` — the OS
silently disables deep-link verification and taps fall through to the
browser instead of opening the installed app.

These two views MUST remain unauthenticated and MUST NOT be tenant-
scoped. The OS fetches them as an anonymous client, often before the
user has signed into anything. Wiring them through ``@login_required``
or the tenant middleware would break every universal-link verification
on iOS / Android.

The Team ID + Android signing fingerprints are pulled from Django
settings (``MOBILE_TEAM_ID`` / ``MOBILE_ANDROID_SHA256``) so ops can
patch the manifest via env var without re-deploying the app server.
The bundle / package identifiers are intentionally hard-coded — they
match the mobile shipping artifact and rotating them would invalidate
every installed copy of the app.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

# Universal-link paths the mobile app claims. Tap on any of these on a
# device with the app installed → OS routes to the app instead of
# Safari / Chrome. Keep this list in sync with the mobile project's
# ``Associated Domains`` entitlement + Android intent filters.
_AASA_PATHS: tuple[str, ...] = (
    "/approve/*",
    "/approve-secret/*",
    "/alerts/*",
    "/sessions/*",
    "/app/*",
    "/connect",
)

# iOS bundle identifier. Hard-coded — see module docstring.
_IOS_BUNDLE_ID = "dev.astrolift.mobile"

# Android package name. Hard-coded — see module docstring.
_ANDROID_PACKAGE = "dev.astrolift.mobile"

# 1-hour TTL. iOS / Android cache aggressively; a short TTL lets ops
# roll fingerprint rotations without bouncing every installed device,
# but is long enough that the file isn't re-fetched on every cold start.
_CACHE_MAX_AGE = 3600


def _team_id() -> str:
    """Apple Developer Team ID, prefix on the iOS appID. Settings-backed
    so ops can patch via env var without a code deploy."""
    return getattr(settings, "MOBILE_TEAM_ID", "PLACEHOLDER_TEAM_ID")


def _android_sha256_fingerprints() -> list[str]:
    """SHA-256 fingerprints of the Android signing keys (upload key +
    play app-signing key, typically). Settings carries a single string
    by default; tests may patch a comma-separated list."""
    raw = getattr(settings, "MOBILE_ANDROID_SHA256", "PLACEHOLDER_UPLOAD_KEY_SHA256")
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if str(x).strip()]
    parts = [p.strip() for p in str(raw).split(",")]
    return [p for p in parts if p]


def _build_aasa() -> dict:
    return {
        "applinks": {
            "apps": [],
            "details": [
                {
                    "appID": f"{_team_id()}.{_IOS_BUNDLE_ID}",
                    "paths": list(_AASA_PATHS),
                }
            ],
        }
    }


def _build_assetlinks() -> list[dict]:
    return [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": _ANDROID_PACKAGE,
                "sha256_cert_fingerprints": _android_sha256_fingerprints(),
            },
        }
    ]


@csrf_exempt
@require_GET
@cache_control(public=True, max_age=_CACHE_MAX_AGE)
def apple_app_site_association(request) -> HttpResponse:
    """Serve ``/.well-known/apple-app-site-association``.

    Apple's universal-link verifier fetches this URL as an anonymous
    client; the file MUST NOT be auth-gated. Apple requires the file
    be served WITHOUT a file extension and with
    ``Content-Type: application/json``.
    """
    body = json.dumps(_build_aasa(), separators=(",", ":"))
    return HttpResponse(body, content_type="application/json")


@csrf_exempt
@require_GET
@cache_control(public=True, max_age=_CACHE_MAX_AGE)
def assetlinks_json(request) -> HttpResponse:
    """Serve ``/.well-known/assetlinks.json``.

    Android's App Links verifier fetches this URL as an anonymous
    client; the file MUST NOT be auth-gated. Standard JSON array body.
    """
    body = json.dumps(_build_assetlinks(), separators=(",", ":"))
    return HttpResponse(body, content_type="application/json")
