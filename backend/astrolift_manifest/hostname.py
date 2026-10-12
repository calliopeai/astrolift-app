"""
Hostname computation for an app + workload set.

The platform's hostname rules (spec 05 §12) compress to:

* 0 public workloads → no hostname
* 1 public workload  → ``<subdomain>.<org-slug>.<base-zone>``
                       where ``subdomain`` defaults to the app slug
                       but can be overridden via [networking].subdomain
* 2+ public workloads → ``<subdomain>-<workload-slug>.<org-slug>.<base-zone>``
                       for each, flat suffix so a single wildcard
                       cert covers them all.

Preview environments are one label below the zone, so the zone's wildcard
certificate covers them (#2091): ``pr-<n>-<app-slug>.<base-zone>`` for a pull
request, ``preview-<branch>-<app-slug>.<base-zone>`` for a manual preview.

This module is pure-Python (no DB, no I/O) so it composes into the
renderer + the env-creation flow + the preview-build flow without
worrying about ordering.
"""

from __future__ import annotations

import dataclasses
import re

from astrolift_manifest.types import NormalizedManifest, WorkloadManifest

_VALID_DNS_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# Subdomains the platform reserves for itself — users cannot register
# an app under any of these. The list is intentionally inclusive of
# common operational hostnames; we'd rather refuse a legitimate
# request and have the user pick another label than leak control over
# something like ``api.<org>.<base>`` that's likely to clash with a
# platform endpoint someday. Per spec 13 §6.2.
RESERVED_SUBDOMAINS: frozenset[str] = frozenset(
    {
        "admin",
        "api",
        "app",
        "auth",
        "console",
        "dashboard",
        "docs",
        "download",
        "health",
        "login",
        "logout",
        "metrics",
        "register",
        "signup",
        "status",
        "webhook",
        "webhooks",
        "www",
    }
)


def is_reserved_subdomain(value: str) -> bool:
    """Return True if ``value`` collides with the platform's reserved
    name list (case-insensitive)."""
    return value.strip().lower() in RESERVED_SUBDOMAINS


def validate_subdomain_label(value: str) -> str:
    """Normalize + validate a user-supplied subdomain.

    Returns the lowered + stripped form on success, raises
    :class:`ValueError` with a precise reason on failure. The caller
    handles the GraphQL-side error envelope.
    """
    s = (value or "").strip().lower()
    if not s:
        raise ValueError("subdomain is empty")
    if not _VALID_DNS_LABEL.match(s):
        raise ValueError(
            f"{s!r} is not a valid DNS label (lowercase letters, digits, "
            "and hyphens; 1-63 chars; no leading/trailing hyphen)"
        )
    if is_reserved_subdomain(s):
        raise ValueError(f"{s!r} is a reserved platform subdomain")
    return s


@dataclasses.dataclass(frozen=True, slots=True)
class HostnameInputs:
    """Inputs the renderer collects from app + env + manifest.

    Centralized so callers can't accidentally pass mismatched zone +
    org pairs (e.g. an org's subdomain on a different zone)."""

    app_slug: str
    org_slug: str
    base_zone: str
    subdomain_override: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class WorkloadHostname:
    """One workload's hostname assignment."""

    workload_slug: str
    hostname: str


def compute_hostnames(manifest: NormalizedManifest, inputs: HostnameInputs) -> list[WorkloadHostname]:
    """Return the hostname for each public workload.

    No-public-workload manifests return an empty list. Single-public
    case yields one entry with the bare subdomain; 2+ public yields
    per-workload entries with the workload slug suffixed.
    """
    public = [w for w in manifest.workloads if w.is_public]
    if not public:
        return []

    base = _normalize_subdomain(inputs.subdomain_override or inputs.app_slug)
    # Hostname pattern: <app>.<base-zone> — org slug is not included in the
    # public URL so customers get clean short hostnames.
    suffix = inputs.base_zone

    if len(public) == 1:
        w = public[0]
        return [WorkloadHostname(workload_slug=w.name, hostname=f"{base}.{suffix}")]

    return [
        WorkloadHostname(
            workload_slug=w.name,
            hostname=f"{base}-{_normalize_subdomain(w.name)}.{suffix}",
        )
        for w in public
    ]


#: A label a preview's hostname starts with; no app may take one (#2091).
PREVIEW_LABEL_RE = re.compile(r"^(pr-[0-9]+|preview)-")


def preview_hostname(*, label: str, app_slug: str, zone: str | None, org_slug: str) -> str:
    """The one preview hostname scheme (#2091): ``<label>-<app>.<zone>``, one
    label below the zone like every other environment, so ``*.<zone>`` covers
    it on ALB, CloudFront and cert-manager alike. ``label`` is ``pr-<n>`` or
    ``preview-<branch>``. A first label over 63 characters keeps its start and
    ends in a short digest, so it stays a valid, stable DNS label. Without a
    managed zone it stops at the org slug; nothing serves that name publicly.
    """
    import hashlib

    first = f"{label}-{app_slug}".lower().strip("-")
    if len(first) > 63:
        digest = hashlib.sha256(first.encode()).hexdigest()[:8]
        first = f"{first[:54].rstrip('-')}-{digest}"
    return f"{first}.{zone}".lower() if zone else f"{first}.{org_slug}".lower()


def compute_preview_hostname(
    *,
    pr_number: int,
    app_slug: str,
    org_slug: str,
    base_zone: str,
) -> str:
    """``pr-<n>-<app>.<base-zone>`` (see :func:`preview_hostname`)."""
    if pr_number <= 0:
        raise ValueError(f"pr_number must be positive, got {pr_number}")
    return preview_hostname(label=f"pr-{pr_number}", app_slug=app_slug, zone=base_zone, org_slug=org_slug)


# ---- helpers ----------------------------------------------------------


def _normalize_subdomain(value: str) -> str:
    """Lower-case + strip; reject anything that wouldn't be a legal
    DNS label so the renderer doesn't emit broken hostnames.

    The slug-style validation matches RFC 1123 hostname labels:
    1-63 chars, [a-z0-9-], no leading/trailing hyphen.
    """
    s = value.strip().lower()
    if not s:
        raise ValueError("subdomain is empty")
    if not _VALID_DNS_LABEL.match(s):
        raise ValueError(f"{s!r} is not a valid DNS label")
    return s


# ---- workload counting (used by GraphQL surface) ----------------------


def public_workloads(manifest: NormalizedManifest) -> list[WorkloadManifest]:
    """Filter helper for callers that just need the public set."""
    return [w for w in manifest.workloads if w.is_public]
