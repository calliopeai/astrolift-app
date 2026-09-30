"""Allocate preview environment names while the app row is locked (#2095)."""

from __future__ import annotations

import hashlib


def preview_environment_name(app, preferred: str, *, source: str) -> str:
    from astrolift_lifecycle.models import AppEnvironment

    names = AppEnvironment.objects.filter(registered_app=app)
    preferred = preferred[:128]
    candidate = preferred
    for attempt in range(100):
        if not names.filter(name=candidate).exists():
            return candidate
        token = hashlib.sha256(f"{app.guid}:{source}:{attempt}".encode()).hexdigest()[:8]
        candidate = f"{preferred[:119]}-{token}"
    raise RuntimeError(f"no free preview environment name derived from {preferred!r}")
