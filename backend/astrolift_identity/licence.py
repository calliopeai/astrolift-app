"""The per-org modules the install's signed licence grants (#1879).

Client Cove signs the licence (RS256, ``iss`` ``client-cove``) and the
deployment agent pulls it and delivers it into the deployment's
``<deployment>-astrolift-license`` secret (calliope-installer); Astrolift reads
it from the environment (``settings.CALLIOPE_LICENSE``) and verifies it against
``settings.CALLIOPE_LICENSE_PUBLIC_KEY``. ECS resolves both at task start, so a
new licence takes effect at the next Astrolift restart. One claim is read:
``astrolift_modules``, the list of ``OrganizationModule`` keys this install's
organizations may turn on. A grant only allows a module; it never turns one on.

``None`` from :func:`licensed_modules` means the licence has no say: no
licence, no key, a signature that does not verify, or a licence without the
claim. Unverified claims are never acted on. ``exp`` is not checked: the
newest signed statement of the modules stands until the agent delivers the
next one, so a task that outlives its licence's TTL does not hand withheld
modules back.
"""

from __future__ import annotations

import functools
import hashlib
import logging

import jwt
from django.conf import settings

logger = logging.getLogger(__name__)

ISSUER = "client-cove"
MODULES_CLAIM = "astrolift_modules"


def licensed_modules() -> frozenset[str] | None:
    """Module keys the verified licence grants, or ``None`` when it has no say."""
    return _verified_modules(settings.CALLIOPE_LICENSE, settings.CALLIOPE_LICENSE_PUBLIC_KEY)


def licence_fingerprint() -> str:
    """A short name for the licence in audit rows; never the token itself."""
    token = settings.CALLIOPE_LICENSE
    return hashlib.sha256(token.encode()).hexdigest()[:12] if token else ""


@functools.lru_cache(maxsize=4)
def _verified_modules(token: str, public_key: str) -> frozenset[str] | None:
    if not token or not public_key:
        return None
    try:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=ISSUER,
            options={"require": ["iss"], "verify_exp": False, "verify_aud": False},
        )
    except jwt.PyJWTError as error:
        logger.warning("ignoring the licence for modules, it does not verify: %s", error)
        return None
    claimed = claims.get(MODULES_CLAIM)
    if claimed is None:
        return None
    # A bare string is one module; any other shape is a claim to nothing.
    if isinstance(claimed, str):
        claimed = [claimed]
    if not isinstance(claimed, list):
        return frozenset()
    return frozenset(str(key).strip() for key in claimed if str(key).strip())
