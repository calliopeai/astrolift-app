"""
BYO external secret references (#150, spec 12 §6.5).

Manifests can declare ``secret_ref = "<scheme>://<path>[#key]"``.
At deploy time the platform resolves the ref via a registered
backend resolver and writes the resolved bytes into the workload's
secret object — but **never stores the plaintext** in our database.

Why a small parser + registry instead of importing each cloud SDK
inline:

* Resolvers live in ``astrolift-providers`` (the plugin repo) where
  the SDK dependencies belong. The platform repo only knows the
  *shape*: ``Resolver = Callable[[ExternalSecretRef], bytes]``.
* Registration is explicit (``register_resolver(scheme, fn)``) so a
  test can swap resolvers without monkey-patching imports.
* Resolution is **fail-closed**: an unregistered scheme, an
  unreachable backend, or any resolver exception raises
  :class:`ExternalSecretResolutionError`. The deploy workflow turns
  that into a friendly user error and refuses to roll forward.

Supported scheme registry (resolvers themselves live elsewhere):

  - ``vault``      — HashiCorp Vault, key from KV engine
  - ``aws-sm``     — AWS Secrets Manager
  - ``aws-ssm``    — AWS Systems Manager Parameter Store
  - ``gcp-sm``     — GCP Secret Manager
  - ``azure-kv``   — Azure Key Vault
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Final
from urllib.parse import urlparse


class ExternalSecretRefError(ValueError):
    """Malformed ``secret_ref`` string in a manifest."""


class ExternalSecretResolutionError(RuntimeError):
    """Resolver couldn't fetch the secret. Surfaced to deploy as
    fail-closed: the deployment must not proceed."""


@dataclasses.dataclass(frozen=True, slots=True)
class ExternalSecretRef:
    """Parsed form of ``<scheme>://<path>[#key]``."""

    scheme: str
    path: str
    key: str = ""

    def __str__(self) -> str:
        if self.key:
            return f"{self.scheme}://{self.path}#{self.key}"
        return f"{self.scheme}://{self.path}"


# Subset of schemes the platform recognises. Adding a scheme is
# explicit so we don't accidentally accept ``http://`` and turn the
# control plane into a credential exfiltration vector.
SUPPORTED_SCHEMES: Final[frozenset[str]] = frozenset(
    {"vault", "aws-sm", "aws-ssm", "gcp-sm", "azure-kv"}
)


def parse_secret_ref(value: str) -> ExternalSecretRef:
    """Parse a manifest ``secret_ref`` string.

    Accepts ``<scheme>://<path>[#key]``. Raises
    :class:`ExternalSecretRefError` on anything else — the parser is
    deliberately strict so a typo surfaces at validation time, not
    at deploy time.
    """
    if not value or not isinstance(value, str):
        raise ExternalSecretRefError("secret_ref must be a non-empty string")

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if not scheme:
        raise ExternalSecretRefError(
            f"secret_ref {value!r} missing scheme; "
            "expected <scheme>://<path>[#key]"
        )
    if scheme not in SUPPORTED_SCHEMES:
        raise ExternalSecretRefError(
            f"secret_ref scheme {scheme!r} not supported; "
            f"choose from {sorted(SUPPORTED_SCHEMES)}"
        )

    # urlparse populates `netloc` for `aws-sm://foo/bar`, where
    # `foo` is netloc and `/bar` is path. Recombine so callers see
    # the full path uniformly.
    path = (parsed.netloc + parsed.path).strip("/")
    if not path:
        raise ExternalSecretRefError(
            f"secret_ref {value!r} missing path after scheme"
        )

    return ExternalSecretRef(scheme=scheme, path=path, key=parsed.fragment)


# ---- resolver registry ------------------------------------------------


Resolver = Callable[[ExternalSecretRef], bytes]


_RESOLVERS: dict[str, Resolver] = {}


def register_resolver(scheme: str, resolver: Resolver) -> None:
    """Register a resolver for a scheme. Plugin packages call this
    at import time; tests call it inside fixtures."""
    if scheme not in SUPPORTED_SCHEMES:
        raise ExternalSecretRefError(
            f"cannot register resolver for unsupported scheme {scheme!r}"
        )
    _RESOLVERS[scheme] = resolver


def unregister_resolver(scheme: str) -> None:
    _RESOLVERS.pop(scheme, None)


def resolve(ref: ExternalSecretRef) -> bytes:
    """Resolve an external ref to its bytes, fail-closed.

    No caching here — caching is a deploy-workflow concern (see
    spec 12 §6.5 ``cache_ttl_seconds``) so the cache invalidation
    rules can be policy-driven rather than baked into this module.
    """
    resolver = _RESOLVERS.get(ref.scheme)
    if resolver is None:
        raise ExternalSecretResolutionError(
            f"no resolver registered for scheme {ref.scheme!r}; "
            "the provider plugin probably isn't loaded"
        )
    try:
        out = resolver(ref)
    except ExternalSecretResolutionError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-closed wraps any error
        raise ExternalSecretResolutionError(
            f"resolver for {ref.scheme!r} failed on {ref.path!r}: {exc}"
        ) from exc
    if not isinstance(out, (bytes, bytearray)):
        raise ExternalSecretResolutionError(
            f"resolver for {ref.scheme!r} returned non-bytes "
            f"({type(out).__name__})"
        )
    return bytes(out)
