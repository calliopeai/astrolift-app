"""Tests for portability mode validation (#51, spec 20 §1-2)."""

from __future__ import annotations

import pytest

from astrolift_manifest.portability import (
    BlockPinSnapshot,
    PortabilityMode,
    PortabilityViolation,
    parse_mode,
    parse_pin,
    validate,
)


def _b(
    kind: str, name: str = "main", *, variant_pin: str = "", has_portable_variant: bool = True
) -> BlockPinSnapshot:
    return BlockPinSnapshot(
        kind=kind,
        name=name,
        variant_pin=variant_pin,
        has_portable_variant=has_portable_variant,
    )


# ---- mode parsing ---------------------------------------------------


def test_parse_mode_default_when_missing():
    assert parse_mode(None) == PortabilityMode.AUTO
    assert parse_mode("") == PortabilityMode.AUTO


def test_parse_mode_known_values():
    assert parse_mode("auto") == PortabilityMode.AUTO
    assert parse_mode("portable") == PortabilityMode.PORTABLE
    assert parse_mode("pinned") == PortabilityMode.PINNED


def test_parse_mode_rejects_unknown():
    with pytest.raises(PortabilityViolation, match="not one of"):
        parse_mode("bogus")


# ---- pin parsing ----------------------------------------------------


def test_parse_pin_well_formed():
    assert parse_pin("aws-rds/aurora-15") == ("aws-rds", "aurora-15")
    assert parse_pin("vault/kv-v2") == ("vault", "kv-v2")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no-slash",
        "too/many/parts",
        "/missing-plugin",
        "plugin/",
        "Plugin/Variant",  # uppercase rejected
        "1plugin/v",  # leading digit
    ],
)
def test_parse_pin_rejects_malformed(bad):
    assert parse_pin(bad) is None


# ---- AUTO mode ------------------------------------------------------


def test_auto_with_no_pins_passes_clean():
    report = validate(
        declared=PortabilityMode.AUTO,
        blocks=[_b("postgres"), _b("redis")],
    )
    assert report.effective_mode == PortabilityMode.AUTO
    assert report.warnings == ()


def test_auto_with_some_pins_warns_about_hybrid():
    report = validate(
        declared=PortabilityMode.AUTO,
        blocks=[_b("postgres", variant_pin="aws-rds/aurora-15"), _b("redis")],
    )
    assert report.effective_mode == PortabilityMode.AUTO
    assert len(report.warnings) == 1
    assert "no longer fully portable" in report.warnings[0]


def test_auto_with_all_pins_suggests_pinned():
    report = validate(
        declared=PortabilityMode.AUTO,
        blocks=[
            _b("postgres", variant_pin="aws-rds/aurora-15"),
            _b("redis", variant_pin="aws-elasticache/redis-7"),
        ],
    )
    assert "consider declaring portability='pinned'" in report.warnings[0]


# ---- PORTABLE mode --------------------------------------------------


def test_portable_with_no_pins_passes():
    report = validate(
        declared=PortabilityMode.PORTABLE,
        blocks=[_b("postgres"), _b("redis")],
    )
    assert report.effective_mode == PortabilityMode.PORTABLE


def test_portable_with_any_pin_rejected():
    """Pinning while declaring portable is a contradiction."""
    with pytest.raises(PortabilityViolation) as exc:
        validate(
            declared=PortabilityMode.PORTABLE,
            blocks=[_b("postgres", variant_pin="aws-rds/aurora-15")],
        )
    assert exc.value.code == "portable_with_pins"


def test_portable_rejects_kind_with_no_portable_variant():
    """If the catalog only exposes cloud-specific Postgres variants
    (no portable fallback), portable can't be promised."""
    with pytest.raises(PortabilityViolation) as exc:
        validate(
            declared=PortabilityMode.PORTABLE,
            blocks=[_b("postgres", has_portable_variant=False)],
        )
    assert exc.value.code == "portable_no_fallback"


# ---- PINNED mode ----------------------------------------------------


def test_pinned_requires_every_block_pinned():
    with pytest.raises(PortabilityViolation) as exc:
        validate(
            declared=PortabilityMode.PINNED,
            blocks=[
                _b("postgres", variant_pin="aws-rds/aurora-15"),
                _b("redis"),  # not pinned
            ],
        )
    assert exc.value.code == "pinned_missing_pins"
    assert "redis" in str(exc.value.kinds[0])


def test_pinned_with_full_pin_set_passes():
    report = validate(
        declared=PortabilityMode.PINNED,
        blocks=[
            _b("postgres", variant_pin="aws-rds/aurora-15"),
            _b("redis", variant_pin="aws-elasticache/redis-7"),
        ],
    )
    assert report.effective_mode == PortabilityMode.PINNED


# ---- pin syntax -----------------------------------------------------


def test_malformed_pin_rejected_with_kind_listed():
    with pytest.raises(PortabilityViolation) as exc:
        validate(
            declared=PortabilityMode.AUTO,
            blocks=[_b("postgres", "main_db", variant_pin="not-a-pin")],
        )
    assert exc.value.code == "invalid_pin_syntax"
    assert "postgres/main_db" in exc.value.kinds[0]


def test_violation_lists_all_offending_blocks_at_once():
    """UX rule: surface every blocker in one report."""
    with pytest.raises(PortabilityViolation) as exc:
        validate(
            declared=PortabilityMode.PINNED,
            blocks=[
                _b("postgres"),  # no pin
                _b("redis"),  # no pin
                _b("queue", variant_pin="aws-sqs/standard"),
            ],
        )
    assert exc.value.code == "pinned_missing_pins"
    assert len(exc.value.kinds) == 2  # postgres + redis
