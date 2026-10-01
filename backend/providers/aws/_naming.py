"""AWS resource-name sanitization (#994).

IAM role names accept only ``[A-Za-z0-9+=,.@_-]`` and cap at 64 chars.
Names assembled from org/app slugs + structural separators violated this
two ways:

  * the ECR CI-push role embeds the ECR repo name ``<org>/<app>`` — the
    literal ``/`` is illegal in a RoleName, so CreateRole failed for every
    app; and
  * the IRSA workload-identity role is ``astrolift-<org>-<app>`` with no
    length bound — long slugs blow past 64.

``iam_role_name`` produces a name valid for BOTH IAM (≤64) and the K8s
ServiceAccount it doubles as (DNS label ≤63), hence the 63 default.

Managed-service names retain the complete persisted service UUID independently
of human slugs and only truncate the cosmetic operator prefix (#2032).
"""

from __future__ import annotations

import hashlib
import re
from uuid import UUID

# Restrict to the slug-safe subset of IAM's allowed charset. IAM also
# permits ``+=,.@`` but those never appear in slugs/repo names, and
# excluding them keeps the names greppable and DNS-label-safe (the same
# string is used as the workload-identity ServiceAccount name).
_DISALLOWED = re.compile(r"[^A-Za-z0-9_-]+")
_RUNS = re.compile(r"-{2,}")


def iam_role_name(*parts: str, max_len: int = 63) -> str:
    """Join ``parts`` into a deterministic, charset-valid, length-bounded
    IAM role name.

    Disallowed characters (incl. the ``/`` in an ECR repo name) collapse to
    ``-``. When the result exceeds ``max_len`` it is truncated and given an
    8-hex suffix derived from the full sanitized string, so two long names
    sharing a prefix don't collide onto the same role. Deterministic — the
    same inputs always yield the same name, so create / ServiceAccount
    annotation / deprovision (which all derive the name independently) agree.
    """
    joined = "-".join(p for p in parts if p)
    cleaned = _RUNS.sub("-", _DISALLOWED.sub("-", joined)).strip("-")
    if not cleaned:
        cleaned = "astrolift"
    if len(cleaned) <= max_len:
        return cleaned
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:8]
    return cleaned[: max_len - 9].rstrip("-") + "-" + digest


def managed_service_identity(managed_service_id: str) -> str:
    """The complete persisted UUID; never invent an identity for an SDK request."""
    try:
        identity = UUID(managed_service_id) if isinstance(managed_service_id, str) else None
    except (ValueError, AttributeError):
        identity = None
    if identity is None or not identity.int or str(identity) != managed_service_id:
        raise ValueError("managed-service identity must be a persisted canonical nonzero UUID")
    return identity.hex


def managed_service_name(managed_service_id: str, *, prefix: str, max_len: int) -> str:
    """Only the cosmetic operator prefix may be truncated; the UUID stays whole."""
    identity = managed_service_identity(managed_service_id)
    if not isinstance(prefix, str):
        raise ValueError("AWS resource prefix must be a string")
    cleaned = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", prefix.lower())).strip("-") or "astrolift"
    if max_len < len(identity) + 2:
        raise ValueError("AWS name limit cannot preserve the complete managed-service UUID")
    cosmetic = cleaned[: max_len - len(identity) - 1].rstrip("-")
    return f"{cosmetic}-{identity}"
