"""Tests for image digest pinning (#26 part, spec 12 §9)."""

from __future__ import annotations

import pytest

from astrolift_workflows.activities.image_digest import (
    ImageRefError,
    is_digest,
    is_pinned,
    parse_pinned_ref,
    pin_to_digest,
)

GOOD_DIGEST = "sha256:" + "a" * 64
ANOTHER_DIGEST = "sha256:" + "b" * 64


# ---- digest validation ----------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "sha256:" + "a" * 64,
        "sha256:" + "0123456789abcdef" * 4,
    ],
)
def test_is_digest_accepts_well_formed(value):
    assert is_digest(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sha256:short",
        "sha256:" + "a" * 63,  # one short
        "sha256:" + "a" * 65,  # one long
        "sha256:" + "A" * 64,  # uppercase
        "md5:" + "a" * 32,  # wrong algorithm
        "a" * 64,  # missing prefix
        "sha256:" + "g" * 64,  # bad hex char
    ],
)
def test_is_digest_rejects_malformed(value):
    assert is_digest(value) is False


# ---- parse_pinned_ref ------------------------------------------------


def test_parse_pinned_basic():
    out = parse_pinned_ref(f"registry.acme.com/api@{GOOD_DIGEST}")
    assert out.repo == "registry.acme.com/api"
    assert out.digest == GOOD_DIGEST


def test_parse_pinned_with_port_in_registry():
    """``registry:5000/foo`` style — colon before port is part of
    the repo, not a tag."""
    out = parse_pinned_ref(f"registry:5000/api@{GOOD_DIGEST}")
    assert out.repo == "registry:5000/api"
    assert out.digest == GOOD_DIGEST


def test_parse_pinned_strips_whitespace():
    out = parse_pinned_ref(f"   registry/api@{GOOD_DIGEST}   ")
    assert out.repo == "registry/api"


@pytest.mark.parametrize(
    "bad",
    [
        "no-digest-here",
        "registry/api:tag",  # tagged, not pinned
        f"registry/api@md5:{'a' * 32}",  # wrong algo
        f"@{GOOD_DIGEST}",  # no repo
    ],
)
def test_parse_pinned_rejects_unpinned_or_malformed(bad):
    with pytest.raises(ImageRefError):
        parse_pinned_ref(bad)


# ---- pin_to_digest ---------------------------------------------------


def test_pin_bare_ref():
    out = pin_to_digest(ref="registry/api", digest=GOOD_DIGEST)
    assert out.repo == "registry/api"
    assert out.digest == GOOD_DIGEST
    assert str(out) == f"registry/api@{GOOD_DIGEST}"


def test_pin_strips_tag():
    """Once we know the digest, the tag is irrelevant — drop it
    so the canonical form is unambiguous."""
    out = pin_to_digest(ref="registry/api:v1.2.3", digest=GOOD_DIGEST)
    assert "v1.2.3" not in str(out)
    assert out.digest == GOOD_DIGEST


def test_pin_strips_only_tag_not_registry_port():
    """``registry:5000/foo/bar:v1`` — only the trailing tag should
    be stripped; ``5000`` is the registry port, part of the repo."""
    out = pin_to_digest(ref="registry:5000/foo/bar:v1.2.3", digest=GOOD_DIGEST)
    assert out.repo == "registry:5000/foo/bar"


def test_pin_preserves_registry_port_when_no_tag():
    out = pin_to_digest(ref="registry:5000/foo/bar", digest=GOOD_DIGEST)
    assert out.repo == "registry:5000/foo/bar"


def test_pin_idempotent_when_digest_matches():
    """Already-pinned ref + matching digest = no change."""
    pinned = f"registry/api@{GOOD_DIGEST}"
    out = pin_to_digest(ref=pinned, digest=GOOD_DIGEST)
    assert str(out) == pinned


def test_pin_rejects_repin_with_different_digest():
    """Already-pinned ref + DIFFERENT digest = refuse. The platform
    must never silently swap one digest for another — that's an
    integrity boundary."""
    pinned = f"registry/api@{GOOD_DIGEST}"
    with pytest.raises(ImageRefError, match="already pinned"):
        pin_to_digest(ref=pinned, digest=ANOTHER_DIGEST)


def test_pin_rejects_malformed_digest():
    with pytest.raises(ImageRefError, match="not in 'sha256"):
        pin_to_digest(ref="registry/api", digest="not-a-digest")


# ---- is_pinned ------------------------------------------------------


def test_is_pinned_true_for_pinned():
    assert is_pinned(f"registry/api@{GOOD_DIGEST}") is True


def test_is_pinned_false_for_tagged_or_bare():
    assert is_pinned("registry/api") is False
    assert is_pinned("registry/api:tag") is False
    assert is_pinned("") is False
