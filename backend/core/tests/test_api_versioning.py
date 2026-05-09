"""Tests for API versioning + deprecation policy (#97, spec 27 §3+§19+§20)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.api_versioning import (
    MIN_DEPRECATION_DAYS,
    MIN_PREV_MAJOR_SUPPORT_DAYS,
    ApiVersion,
    ApiVersionError,
    ChangeKind,
    ChangelogEntry,
    DeprecationNotice,
    headers_for,
    is_breaking_change,
    is_due_for_removal,
    parse_version,
    prev_major_supported_until,
    required_changelog_kinds,
    validate_changelog_section,
)


# ---- version parsing -----------------------------------------------


def test_parse_strict_xyz():
    assert parse_version("2.4.0") == ApiVersion(2, 4, 0)


@pytest.mark.parametrize("bad", [
    "2.4", "2.4.0.1", "v2.4.0", "2.4.0-rc1", "latest", "",
])
def test_parse_rejects_loose(bad):
    with pytest.raises(ApiVersionError):
        parse_version(bad)


def test_header_value_includes_product_prefix():
    """Spec 27 §3: 'astrolift-api/2.4.0' so SDKs comparing
    across products don't confuse versions."""
    v = ApiVersion(2, 4, 0)
    assert v.header_value == "astrolift-api/2.4.0"


def test_versions_orderable():
    assert ApiVersion(1, 0, 0) < ApiVersion(2, 0, 0)
    assert ApiVersion(1, 9, 9) < ApiVersion(2, 0, 0)


# ---- breaking-change detection -------------------------------------


def test_minor_or_patch_is_not_breaking():
    assert is_breaking_change(
        from_v=ApiVersion(2, 4, 0), to_v=ApiVersion(2, 5, 0),
    ) is False
    assert is_breaking_change(
        from_v=ApiVersion(2, 4, 0), to_v=ApiVersion(2, 4, 1),
    ) is False


def test_major_bump_is_breaking():
    assert is_breaking_change(
        from_v=ApiVersion(2, 9, 9), to_v=ApiVersion(3, 0, 0),
    ) is True


def test_same_or_lower_major_is_not_breaking():
    """Same major = compatible by definition (spec 27 §3)."""
    assert is_breaking_change(
        from_v=ApiVersion(2, 4, 0), to_v=ApiVersion(2, 4, 0),
    ) is False


# ---- deprecation notice --------------------------------------------


def test_deprecation_window_min_6_months():
    """Spec 27 §3 mandates >= 6 months between deprecated_at and
    removed_at. CI catches drift."""
    with pytest.raises(ApiVersionError, match="window"):
        DeprecationNotice(
            field_path="Mutation.deleteApp",
            deprecated_at=date(2026, 1, 1),
            removed_at=date(2026, 5, 1),  # only 4 months
        )


def test_deprecation_window_at_min_passes():
    """6 months exactly is the boundary; should pass."""
    n = DeprecationNotice(
        field_path="Mutation.deleteApp",
        deprecated_at=date(2026, 1, 1),
        removed_at=date(2026, 1, 1) + timedelta(days=MIN_DEPRECATION_DAYS),
    )
    assert n.field_path == "Mutation.deleteApp"


def test_deprecation_rejects_inverted_window():
    with pytest.raises(ApiVersionError, match="removed_at"):
        DeprecationNotice(
            field_path="x",
            deprecated_at=date(2026, 6, 1),
            removed_at=date(2026, 1, 1),
        )


def test_deprecation_requires_field_path():
    with pytest.raises(ApiVersionError):
        DeprecationNotice(
            field_path="",
            deprecated_at=date(2026, 1, 1),
            removed_at=date(2027, 1, 1),
        )


# ---- RFC 8594 headers ----------------------------------------------


def test_headers_format_rfc_8594():
    """Per RFC 8594: Deprecation: true, Sunset: HTTP-date."""
    n = DeprecationNotice(
        field_path="x",
        deprecated_at=date(2026, 1, 1),
        removed_at=date(2026, 7, 1),
    )
    h = headers_for(n)
    assert h.deprecation == "true"
    # HTTP-date format: 'Wed, 01 Jul 2026 00:00:00 GMT'
    assert "Jul" in h.sunset
    assert "2026" in h.sunset
    assert h.sunset.endswith("GMT")


# ---- removal due ---------------------------------------------------


def test_is_due_for_removal_at_or_after_sunset():
    n = DeprecationNotice(
        field_path="x",
        deprecated_at=date(2026, 1, 1),
        removed_at=date(2026, 7, 1),
    )
    assert is_due_for_removal(n, today=date(2026, 7, 1)) is True
    assert is_due_for_removal(n, today=date(2026, 8, 1)) is True


def test_is_due_for_removal_false_before_sunset():
    n = DeprecationNotice(
        field_path="x",
        deprecated_at=date(2026, 1, 1),
        removed_at=date(2026, 7, 1),
    )
    assert is_due_for_removal(n, today=date(2026, 6, 30)) is False


# ---- previous-major support window ---------------------------------


def test_prev_major_supported_for_12_months():
    """v3.0 ships 2026-03-01 → v2.x must be supported until at
    least 2027-03-01."""
    sunset = prev_major_supported_until(
        current_major_released_on=date(2026, 3, 1),
    )
    assert (sunset - date(2026, 3, 1)).days >= MIN_PREV_MAJOR_SUPPORT_DAYS


# ---- changelog validation ------------------------------------------


def test_changelog_kinds_locked():
    """Lock-test on the vocabulary so new tags require deliberate
    review."""
    assert required_changelog_kinds() == {
        ChangeKind.ADDED, ChangeKind.CHANGED, ChangeKind.DEPRECATED,
        ChangeKind.REMOVED, ChangeKind.FIXED, ChangeKind.SECURITY,
    }


def test_changelog_within_major_rejects_removed():
    """Spec 27 §20: within a major version, NO breaking changes —
    'Removed' entries require a major bump."""
    with pytest.raises(ApiVersionError, match="major bump"):
        validate_changelog_section(
            version=ApiVersion(2, 4, 0),
            previous=ApiVersion(2, 3, 0),
            entries=[
                ChangelogEntry(kind=ChangeKind.REMOVED, summary="dropped foo"),
            ],
        )


def test_changelog_across_major_allows_removed():
    """Major bump → Removed entries OK."""
    validate_changelog_section(
        version=ApiVersion(3, 0, 0),
        previous=ApiVersion(2, 9, 9),
        entries=[
            ChangelogEntry(kind=ChangeKind.REMOVED, summary="dropped foo"),
        ],
    )


def test_changelog_minor_with_compat_changes_passes():
    validate_changelog_section(
        version=ApiVersion(2, 4, 0),
        previous=ApiVersion(2, 3, 0),
        entries=[
            ChangelogEntry(kind=ChangeKind.ADDED, summary="new field bar"),
            ChangelogEntry(kind=ChangeKind.DEPRECATED, summary="deprecate baz"),
            ChangelogEntry(kind=ChangeKind.FIXED, summary="bug in qux"),
        ],
    )


def test_changelog_rejects_non_increasing_version():
    """version must be strictly greater than previous."""
    with pytest.raises(ApiVersionError, match="greater"):
        validate_changelog_section(
            version=ApiVersion(2, 4, 0),
            previous=ApiVersion(2, 4, 0),
            entries=[],
        )
    with pytest.raises(ApiVersionError):
        validate_changelog_section(
            version=ApiVersion(2, 3, 0),
            previous=ApiVersion(2, 4, 0),
            entries=[],
        )
