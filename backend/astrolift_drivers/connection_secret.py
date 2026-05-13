"""
Connection secret storage path + auth-mode dispatch (#25, spec 11
§10-§14).

Pure-Python helpers. The deploy workflow's
``provision_managed_service`` activity uses this to compute
where to write a binding's connection material in the secrets
backend, and how to materialize that into the pod (per the
service's auth mode).

Secret storage path convention (spec 11 §10):

  astrolift/<env>/<org>/<app>/managed-services/<service-name>

Auth modes (spec 11 §14):

  password — username + password in the connection secret. Pod
    receives them via envFrom (#19).
  iam      — workload identity grants access (IRSA / GCP WLI / AKS
    federated). No password in the secret; the connection envelope
    just carries endpoint/db/region.
  mtls     — pod presents a client certificate provisioned by
    TlsDriver. The connection secret carries the cert + key
    materialized as files mounted at a known path.

Pairs with the existing modules: #19 secret_materialization for
the in-cluster Secret/ExternalSecret rendering, #117 env_injection
for the envelope key set, #62 primitives.resolve_workload_identity
for the IAM mode plumbing.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import StrEnum


class AuthMode(StrEnum):
    PASSWORD = "password"
    IAM = "iam"
    MTLS = "mtls"


# ---- storage path ---------------------------------------------------


class SecretPathError(ValueError):
    pass


def _segment_ok(seg: str) -> bool:
    """Backend-agnostic path safety: every segment must be non-empty,
    no '/' (would break the path partition), no whitespace."""
    return bool(seg) and "/" not in seg and seg.strip() == seg


def secret_storage_path(
    *,
    env_slug: str,
    org_slug: str,
    app_slug: str,
    service_name: str,
) -> str:
    """Compose the canonical storage path.

    All segments are validated — a typo or smuggled '/' could
    collide with another binding's path or escape the org's
    namespace in the backend.
    """
    for seg, label in (
        (env_slug, "env_slug"),
        (org_slug, "org_slug"),
        (app_slug, "app_slug"),
        (service_name, "service_name"),
    ):
        if not _segment_ok(seg):
            raise SecretPathError(f"{label} {seg!r} contains invalid characters or is empty")
    return f"astrolift/{env_slug}/{org_slug}/{app_slug}/managed-services/{service_name}"


# ---- auth-mode dispatch ---------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ConnectionMaterial:
    """The values the deploy workflow writes to the secrets backend
    + injects into the pod for one binding.

    Shape varies by auth_mode:
      password — env_values carries credentials (USER, PASSWORD, etc.)
      iam      — env_values carries identity context (endpoint, db,
        region) but NO credentials; ``iam_grants`` lists the cloud
        permissions the pod's workload identity needs.
      mtls     — env_values carries endpoint + paths; ``mtls_files``
        carries the client cert + key bytes the activity mounts at
        the declared path.
    """

    auth_mode: AuthMode
    env_values: Mapping[str, str]
    iam_grants: tuple[Mapping[str, str], ...] = ()
    mtls_files: Mapping[str, bytes] = dataclasses.field(default_factory=dict)
    """File-name -> bytes. Caller renders these as a Secret with
    binary data, mounted via volumeMounts at e.g. /var/run/mtls/."""


class AuthModeError(ValueError):
    pass


def validate_material(material: ConnectionMaterial) -> None:
    """Sanity-check that the material matches its declared mode.

    Catches driver bugs early (e.g. password values leaking into
    an iam-mode binding when the driver was misconfigured).
    """
    if material.auth_mode == AuthMode.PASSWORD:
        if material.iam_grants:
            raise AuthModeError("password mode binding has iam_grants; misconfigured driver")
        if material.mtls_files:
            raise AuthModeError("password mode binding has mtls_files; misconfigured driver")
        return
    if material.auth_mode == AuthMode.IAM:
        # IAM mode must NOT carry password-shaped values
        leaked = {k for k in material.env_values if "PASSWORD" in k.upper()}
        if leaked:
            raise AuthModeError(
                f"iam mode binding leaks password keys {sorted(leaked)!r}; "
                "workload identity should make secrets unnecessary"
            )
        if material.mtls_files:
            raise AuthModeError("iam mode binding has mtls_files; misconfigured driver")
        return
    if material.auth_mode == AuthMode.MTLS:
        if not material.mtls_files:
            raise AuthModeError("mtls mode binding has no mtls_files (client cert + key required)")
        return
    raise AuthModeError(f"unknown auth_mode {material.auth_mode!r}")


def variant_supports_auth_mode(
    *,
    allowed_modes: frozenset[AuthMode],
    requested: AuthMode,
) -> bool:
    """Check the variant declares support for the requested mode."""
    return requested in allowed_modes
