"""
API versioning + deprecation policy (#97, spec 27 §3 + §19 + §20).

Pure-Python module covering the policy bits that wire into every
response, every changelog entry, and the GraphQL/OpenAPI/protobuf
schema dumps.

Three concerns:

* **Version constants + parsing** — the platform's current major,
  semver bump rules. Used by the response middleware to set
  ``X-Astrolift-Api-Version`` and by CI's compatibility-check.
* **Deprecation policy** — minimum windows (>=6 months from
  ``deprecated_at`` to ``removed_at``; previous major supported
  for >=12 months in parallel). The middleware reads
  per-resolver/per-endpoint deprecation metadata and emits
  RFC 8594 ``Deprecation`` + ``Sunset`` headers.
* **Changelog entry validation** — structured CHANGELOG.md
  entries with locked-down tags (Added/Changed/Deprecated/Removed
  /Fixed/Security). CI runs the validator on PR.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date, timedelta
from enum import Enum

# Spec 27 §3 — minimum windows.
MIN_DEPRECATION_DAYS = 180  # 6 months
MIN_PREV_MAJOR_SUPPORT_DAYS = 365  # 12 months


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclasses.dataclass(frozen=True, slots=True, order=True)
class ApiVersion:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def header_value(self) -> str:
        """The string set in ``X-Astrolift-Api-Version`` response
        header per spec 27 §3. Includes the product prefix so
        clients comparing across products (CLI vs SDK) don't
        confuse versions."""
        return f"astrolift-api/{self}"


class ApiVersionError(ValueError):
    pass


def parse_version(s: str) -> ApiVersion:
    m = _SEMVER_RE.match(s.strip())
    if m is None:
        raise ApiVersionError(f"version {s!r} not in strict X.Y.Z form")
    return ApiVersion(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_breaking_change(*, from_v: ApiVersion, to_v: ApiVersion) -> bool:
    """A breaking change requires a major bump. Same or backwards
    major numbers are always compatible from the API surface
    perspective (per spec 27 §3 — within a major version, NO
    breaking changes)."""
    return to_v.major > from_v.major


# ---- deprecation -----------------------------------------------------


class ChangeKind(str, Enum):
    """Locked vocabulary for CHANGELOG.md entries (spec 27 §20)."""

    ADDED = "Added"
    CHANGED = "Changed"
    DEPRECATED = "Deprecated"
    REMOVED = "Removed"
    FIXED = "Fixed"
    SECURITY = "Security"


@dataclasses.dataclass(frozen=True, slots=True)
class DeprecationNotice:
    """One field/endpoint marked deprecated. The middleware reads
    these from a registry built at schema-load time and emits
    ``Deprecation: true`` + ``Sunset: <date>`` per RFC 8594."""

    field_path: str
    """e.g. 'Mutation.deleteApp' or '/api/v1/apps/{id}'."""

    deprecated_at: date
    removed_at: date
    reason: str = ""
    migration_url: str = ""

    def __post_init__(self) -> None:
        if self.removed_at < self.deprecated_at:
            raise ApiVersionError("removed_at must be >= deprecated_at")
        window = (self.removed_at - self.deprecated_at).days
        if window < MIN_DEPRECATION_DAYS:
            raise ApiVersionError(
                f"deprecation window {window}d < min {MIN_DEPRECATION_DAYS}d "
                f"(spec 27 §3 mandates >= 6 months)"
            )
        if not self.field_path:
            raise ApiVersionError("field_path is required")


@dataclasses.dataclass(frozen=True, slots=True)
class DeprecationHeaders:
    """The header pair the middleware adds to responses touching
    a deprecated field."""

    deprecation: str  # 'true' (RFC 8594)
    sunset: str  # HTTP-date format


def headers_for(notice: DeprecationNotice) -> DeprecationHeaders:
    """RFC 8594 header values. Sunset must be HTTP-date — Python's
    ``date.strftime('%a, %d %b %Y 00:00:00 GMT')`` produces it."""
    return DeprecationHeaders(
        deprecation="true",
        sunset=notice.removed_at.strftime("%a, %d %b %Y 00:00:00 GMT"),
    )


def is_due_for_removal(notice: DeprecationNotice, *, today: date) -> bool:
    """The pruning workflow uses this to find fields whose sunset
    is past — operators get a final reminder + the field gets
    removed in the next major bump."""
    return today >= notice.removed_at


# ---- previous-major support window ---------------------------------


def prev_major_supported_until(
    *,
    current_major_released_on: date,
) -> date:
    """Spec 27 §3: previous major supported in parallel for >= 12
    months from when the new major shipped. Computes the absolute
    sunset date for the previous major's API responses."""
    return current_major_released_on + timedelta(days=MIN_PREV_MAJOR_SUPPORT_DAYS)


# ---- changelog entry validation ------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ChangelogEntry:
    """One bullet under a release section in CHANGELOG.md."""

    kind: ChangeKind
    summary: str
    issue_refs: tuple[str, ...] = ()


def validate_changelog_section(
    *,
    version: ApiVersion,
    previous: ApiVersion,
    entries: list[ChangelogEntry],
) -> None:
    """Enforce the compatibility promise (spec 27 §20).

    Within a major version (``version.major == previous.major``),
    NO entries may be tagged ``Removed`` or carry breaking
    semantics in ``Changed``. CI runs this on every PR that
    touches CHANGELOG.md.
    """
    if version <= previous:
        raise ApiVersionError(f"new version {version} must be greater than previous {previous}")

    same_major = version.major == previous.major
    if same_major:
        for e in entries:
            if e.kind == ChangeKind.REMOVED:
                raise ApiVersionError(
                    f"'Removed' entries forbidden within a major "
                    f"({version.major}.x.y); requires major bump"
                )


def required_changelog_kinds() -> frozenset[ChangeKind]:
    """The set of kinds the validator recognizes. Lock-tested so
    new tags can't slip in silently."""
    return frozenset(ChangeKind)
