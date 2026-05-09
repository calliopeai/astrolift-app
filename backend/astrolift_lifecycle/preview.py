"""
Preview environment policy (#92 hostname / #94 config / #96 cost).

Pure-Python module covering three preview concerns:

* **Hostname pattern** — ``pr-<n>-<app>.pr.<org>.<platform>`` for
  single-workload apps; ``pr-<n>-<app>-<workload>.pr.<org>....``
  for multi-workload. DNS wildcard + wildcard TLS cover the whole
  ``*.pr.<org>.<platform>`` zone; per-preview A/CNAME records are
  emitted per-preview so explicit lookups work.
* **Configuration** — ``[preview]`` section in
  ``astrolift.toml`` with org-level defaults merged underneath
  app-level overrides.
* **Cost-containment defaults** — bounded ``max_active``,
  ``auto_teardown_on_close=True``, ``share_main_secrets=False``
  out of the box (spec 18 §6).
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping


# ---- hostname pattern -----------------------------------------------


_LABEL_RE = re.compile(r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")


class PreviewHostnameError(ValueError):
    pass


def preview_hostname(
    *,
    pr_number: int,
    app_slug: str,
    org_slug: str,
    platform_domain: str,
    workload: str = "",
) -> str:
    """Compose the preview FQDN per spec 18 §2.

    Single-workload: ``pr-<n>-<app>.pr.<org>.<platform>``
    Multi-workload:  ``pr-<n>-<app>-<workload>.pr.<org>.<platform>``

    Validates the leftmost label fits the 63-char DNS limit so a
    long app/workload name doesn't produce an invalid FQDN. The
    wildcard cert covers ``*.pr.<org>.<platform>`` so any leftmost
    label that fits gets TLS automatically.
    """
    if pr_number <= 0:
        raise PreviewHostnameError(
            f"pr_number must be positive, got {pr_number}"
        )
    if not app_slug or not org_slug or not platform_domain:
        raise PreviewHostnameError(
            "app_slug, org_slug, and platform_domain are required"
        )

    if workload:
        leftmost = f"pr-{pr_number}-{app_slug}-{workload}"
    else:
        leftmost = f"pr-{pr_number}-{app_slug}"

    if len(leftmost) > 63:
        raise PreviewHostnameError(
            f"preview leftmost label {leftmost!r} exceeds 63 chars; "
            "shorten the app/workload name"
        )
    if not _LABEL_RE.match(leftmost):
        raise PreviewHostnameError(
            f"preview leftmost label {leftmost!r} is not a valid DNS label"
        )

    return f"{leftmost}.pr.{org_slug}.{platform_domain}"


def wildcard_zone_for_org(*, org_slug: str, platform_domain: str) -> str:
    """The zone the wildcard cert + wildcard A/CNAME cover for an
    org's previews. One per (org, platform_domain)."""
    return f"*.pr.{org_slug}.{platform_domain}"


# ---- configuration --------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewConfig:
    """Merged preview config for one (org, app) pair."""

    enabled: bool = True
    max_active: int = 5
    auto_teardown_on_close: bool = True
    auto_redeploy_on_push: bool = True
    share_main_secrets: bool = False
    """Cost + security default: previews get fresh secrets, not
    main's. Operators opt in if they need parity."""
    notify_on_ready: bool = True
    notify_on_failure: bool = True

    def __post_init__(self) -> None:
        if self.max_active <= 0 or self.max_active > 100:
            raise ValueError(
                f"max_active {self.max_active} must be 1..100"
            )


# Spec 18 §6 cost-containment defaults — applied when no
# app-level / org-level override is set.
DEFAULTS = PreviewConfig()


def merge_preview_config(
    *,
    org_defaults: Mapping[str, object] | None,
    app_overrides: Mapping[str, object] | None,
) -> PreviewConfig:
    """Org defaults beat platform defaults; app overrides beat both.

    Unknown keys in either dict are ignored — defensive against
    typos in admin UI without raising on fields the platform
    might add later. Validation happens at the dataclass level
    via ``__post_init__``.
    """
    merged = dataclasses.asdict(DEFAULTS)
    valid_keys = set(merged.keys())

    for source in (org_defaults or {}, app_overrides or {}):
        for k, v in source.items():
            if k in valid_keys:
                merged[k] = v
    return PreviewConfig(**merged)


# ---- garbage collection ---------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class PreviewSnapshot:
    """One active preview env. The GC workflow projects rows here
    to decide which to evict when ``max_active`` is exceeded."""

    preview_id: int
    pr_number: int
    last_activity_unix: int
    """Most recent push or access. GC evicts least-recently-active
    first to preserve the previews developers are actually using."""

    is_pinned: bool = False
    """Operator-pinned previews are NEVER auto-evicted, regardless
    of max_active. Lets a stakeholder demo survive the next push."""


def previews_to_evict(
    *,
    active: list[PreviewSnapshot],
    max_active: int,
) -> tuple[PreviewSnapshot, ...]:
    """Return the previews that exceed the max_active cap, ordered
    by least-recent-activity first.

    Pinned previews are excluded from the calculation entirely —
    they don't count against max_active.
    """
    if max_active <= 0:
        raise ValueError("max_active must be positive")
    unpinned = [p for p in active if not p.is_pinned]
    if len(unpinned) <= max_active:
        return ()
    unpinned.sort(key=lambda p: p.last_activity_unix)
    excess = len(unpinned) - max_active
    return tuple(unpinned[:excess])
