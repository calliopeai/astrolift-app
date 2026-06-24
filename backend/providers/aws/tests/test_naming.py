"""iam_role_name sanitizer (#994).

Guards the two onboarding-breaking defects: an ECR push-role name that
embedded the illegal '/' from ``<org>/<app>`` (failed for every app), and
an unbounded IRSA role name that blew past IAM's 64-char limit for long
slugs.
"""

from __future__ import annotations

import hashlib
import re

from providers.aws._naming import iam_role_name

_VALID = re.compile(r"^[A-Za-z0-9_-]+$")


def test_strips_illegal_slash_from_ecr_repo():
    # The exact failing case: repo name is "<org>/<app>".
    name = iam_role_name("astrolift", "acme/pickup-windows-tool", "ecr-push")
    assert "/" not in name
    assert _VALID.match(name)
    assert name == "astrolift-acme-pickup-windows-tool-ecr-push"


def test_sanitizes_arbitrary_disallowed_chars():
    name = iam_role_name("astrolift", "Weird Co. (LLC)!", "role")
    assert _VALID.match(name)
    # spaces / punctuation collapse to single '-', no leading/trailing/double '-'
    assert "--" not in name
    assert not name.startswith("-") and not name.endswith("-")


def test_short_name_passes_through_unchanged():
    assert iam_role_name("astrolift", "smd", "web") == "astrolift-smd-web"


def test_length_bounded_with_hash_suffix():
    long_app = "a" * 120
    name = iam_role_name("astrolift", long_app)
    assert len(name) <= 63
    cleaned = f"astrolift-{long_app}"
    expected_hash = hashlib.sha256(cleaned.encode()).hexdigest()[:8]
    assert name.endswith("-" + expected_hash)


def test_long_names_sharing_prefix_do_not_collide():
    # Two distinct over-length names with an identical 63-char prefix must
    # map to DIFFERENT role names (else two apps share one IAM role).
    base = "x" * 80
    a = iam_role_name("astrolift", base + "-alpha")
    b = iam_role_name("astrolift", base + "-beta")
    assert a != b
    assert len(a) <= 63 and len(b) <= 63


def test_deterministic():
    args = ("astrolift", "some-very-long-org-slug-" + "y" * 60, "app")
    assert iam_role_name(*args) == iam_role_name(*args)


def test_empty_parts_fall_back():
    assert iam_role_name("", "") == "astrolift"
    # empty parts are skipped, not rendered as stray separators
    assert iam_role_name("astrolift", "", "web") == "astrolift-web"
