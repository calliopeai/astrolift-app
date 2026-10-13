"""Brain read tokens: how a machine client reads a brain through the edge (#2320).

A brain app sits behind the cluster's central-auth gate, which admits only a
browser session. A client the caller connects (Chat Studio, cykick) instead
gets a short-lived token from here, minted only after ``visible_brains()``
admits the caller, so team shares, the API token ceiling and archiving decide
it exactly as they decide the listing. The cluster edge verifies the token
against this install's JWKS and lets it into that one brain's hosts, with the
caller's identity in the usual headers (``edge_gateway.BRAIN_TOKEN_HEADER``).

A revoked share stops new tokens at once; a token already issued lives out
its 15 minutes (decision 2026-10-13: lag accepted over an ext-authz call on
every request).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import secrets
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from astrolift_registry.models import BrainTokenKey, RegisteredApp

TOKEN_TTL = dt.timedelta(minutes=15)
KEY_SIGNING_LIFETIME = dt.timedelta(days=30)
KEY_PUBLISHED_AFTER_RETIREMENT = dt.timedelta(days=1)
JWKS_PATH = "/api/brains/v1/jwks.json"
SCOPE = "brain:read"
ALGORITHM = "ES256"


class BrainTokenError(Exception):
    pass


def issuer() -> str:
    """This install's URL, the ``iss`` of every token and the JWKS's origin."""
    base = (getattr(settings, "APP_BASE_URL", "") or getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    if not base.startswith("https://") and not settings.DEBUG:
        raise BrainTokenError("APP_BASE_URL must be the install's https URL to issue brain tokens")
    return base


def jwks_uri() -> str:
    return issuer() + JWKS_PATH


def _new_key() -> BrainTokenKey:
    from core.secrets import encrypt_at_rest

    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    kid = secrets.token_hex(8)
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    sealed = encrypt_at_rest(pem)
    return BrainTokenKey.objects.create(
        kid=kid,
        private_key_backend_kind=sealed.backend_kind,
        private_key_ciphertext=sealed.backend_ref,
        public_jwk={**public, "kid": kid, "use": "sig", "alg": ALGORITHM},
    )


def signing_key(*, now: dt.datetime | None = None) -> BrainTokenKey:
    """The key that signs now; rotated after ``KEY_SIGNING_LIFETIME``."""
    now = now or timezone.now()
    with transaction.atomic():
        live = list(
            BrainTokenKey.objects.select_for_update().filter(retired_at__isnull=True).order_by("-created_at")
        )
        current = live[0] if live and live[0].created_at > now - KEY_SIGNING_LIFETIME else None
        for key in live:
            if key is not current:
                key.retired_at = now
                key.save(update_fields=["retired_at", "updated_at", "version"])
        return current or _new_key()


def jwks(*, now: dt.datetime | None = None) -> dict[str, Any]:
    """The public keys the edge verifies against: the live ones and those
    retired within ``KEY_PUBLISHED_AFTER_RETIREMENT``."""
    from django.db.models import Q

    now = now or timezone.now()
    rows = BrainTokenKey.objects.filter(
        Q(retired_at__isnull=True) | Q(retired_at__gt=now - KEY_PUBLISHED_AFTER_RETIREMENT)
    ).order_by("-created_at")
    return {"keys": [dict(row.public_jwk) for row in rows]}


@dataclasses.dataclass(frozen=True)
class BrainReadToken:
    token: str
    expires_at: dt.datetime


def issue(*, user: Any, brain: RegisteredApp, now: dt.datetime | None = None) -> BrainReadToken:
    """A token for ``user`` to read ``brain``. The caller has already checked
    that ``visible_brains()`` admits ``brain``; this does not."""
    from core.secrets import EncryptedSecret, decrypt

    now = now or timezone.now()
    key = signing_key(now=now)
    pem = decrypt(
        EncryptedSecret(
            backend_kind=key.private_key_backend_kind, backend_ref=bytes(key.private_key_ciphertext)
        )
    )
    expires = now + TOKEN_TTL
    claims = {
        "iss": issuer(),
        "aud": str(brain.guid),
        "sub": str(getattr(user, "guid", "") or user.pk),
        "email": str(getattr(user, "email", "") or ""),
        "scope": SCOPE,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_hex(12),
    }
    token = jwt.encode(claims, pem, algorithm=ALGORITHM, headers={"kid": key.kid})
    return BrainReadToken(token=token, expires_at=expires)


def connection_descriptor(*, user: Any, brain: RegisteredApp) -> dict[str, Any]:
    """What a client stores to mount the brain: where, how and until when."""
    from astrolift_registry.brains import describe_brain
    from providers.k8s_native.edge_gateway import BRAIN_TOKEN_HEADER

    issued = issue(user=user, brain=brain)
    described = describe_brain(brain)
    return {
        "id": described["id"],
        "slug": described["slug"],
        "environments": described["environments"],
        "header": BRAIN_TOKEN_HEADER,
        "token": issued.token,
        "expires_at": issued.expires_at.isoformat(),
    }
