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
"""

from __future__ import annotations

import hashlib
import re

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
