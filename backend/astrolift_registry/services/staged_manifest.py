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


_PUBLIC_SALT = "astrolift.registry.manifest-hash"


def public_manifest_hash(app, value: str) -> str:
    """The form of a stored manifest digest the API serves (#1993).

    ``manifest_hash`` / ``last_synced_hash`` are unkeyed sha256 digests of the
    normalized manifest, container and ``[env]`` values included, so a reader
    of the (masked) manifest could confirm a guessed low-entropy secret
    offline. The API serves them keyed per app: equal stored digests still
    serve equal values, so the client's drift comparison is unchanged, but a
    guess can no longer be checked. Internal sync-state comparisons keep the
    raw digests.
    """
    if not value:
        return ""
    return salted_hmac(_PUBLIC_SALT, f"{app.guid}\n{value}", algorithm="sha256").hexdigest()
