"""Keyed digest of an app's staged manifest buffer (#1759).

``AstroliftRegisteredApp.rawManifestStagedHash`` hands this to the client,
which passes it back as ``applyStagedManifest``'s ``expectedStagedHash`` so
an apply never lands a draft the caller did not load. It is served to every
``app.read`` holder, and once ``[env]`` values are masked on read (#1920) a
plain sha256 of the unmasked text would let that reader confirm a guess at
a masked value offline: rebuild the text with the guess, hash, compare.
Keying it with the server's ``SECRET_KEY`` closes that; binding it to the
app guid keeps two apps with identical manifests from sharing a digest.
"""

from __future__ import annotations

from django.utils.crypto import salted_hmac

_SALT = "astrolift.registry.staged-manifest"


def staged_manifest_hash(app) -> str:
    return salted_hmac(
        _SALT,
        f"{app.guid}\n{app.manifest_raw_staged or ''}",
        algorithm="sha256",
    ).hexdigest()
