"""Golden version/stamp tests for the managed CI workflow templates (#1208).

These pin the ``content_hash`` of every host's rendered workflow body to the
current ``TEMPLATE_VERSION``. The contract they enforce:

    Any change to a ``render_astrolift_*`` body (or to
    ``astrolift_lifecycle/templates/astrolift-ci.yml.j2``) MUST be paired with
    a ``TEMPLATE_VERSION`` bump in ``astrolift_scm/ci_templates.py`` and an
    update to ``PINNED_CONTENT_HASHES`` below.

So a template fix can never silently ship without advancing the version the
platform stamps into (and persists for) every app — which is what lets a later
phase tell a repo whose file predates the fix (``template_stale``) apart from a
current one.

Pure render tests — no DB. ``settings.PLATFORM_API_URL`` is pinned because the
renderers fold it into the body, so the golden hashes must be computed against
a fixed value.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrolift_scm.ci_templates import (
    TEMPLATE_VERSION,
    content_hash,
    git_blob_sha,
    parse_stamp,
    stamp_workflow,
)
from astrolift_scm.services.workflow_sync import (
    render_astrolift_bitbucket_pipeline,
    render_astrolift_ci_workflow,
    render_astrolift_gitea_ci_workflow,
    render_astrolift_gitlab_ci_workflow,
)

# The exact platform URL the golden hashes below were computed against. The
# renderers embed ``settings.PLATFORM_API_URL``; pinning it keeps the hashes
# deterministic regardless of the ambient test env.
GOLDEN_API_URL = "https://platform.astrolift.test"


def _golden_app() -> SimpleNamespace:
    """A fixed, fully-populated app the golden hashes are computed from.

    Every field the four renderers read is set to a stable literal so the
    rendered bodies — and therefore their content hashes — never move unless a
    TEMPLATE body actually changes.
    """
    return SimpleNamespace(
        slug="hello-app",
        deploy_branch="main",
        registry_repo_uri="123456789012.dkr.ecr.us-west-2.amazonaws.com/hello-app",
        push_role_ref="arn:aws:iam::123456789012:role/astrolift-push-hello-app",
        source_kind="github",
        source_repo="acme/hello-app",
    )


# host -> renderer under test.
_RENDERERS = {
    "github": render_astrolift_ci_workflow,
    "gitlab": render_astrolift_gitlab_ci_workflow,
    "gitea": render_astrolift_gitea_ci_workflow,
    "bitbucket": render_astrolift_bitbucket_pipeline,
}

# The version these hashes belong to. Kept as its own constant (rather than
# reading TEMPLATE_VERSION) so that bumping TEMPLATE_VERSION without refreshing
# the pins trips ``test_template_version_matches_pins`` loudly.
PINNED_TEMPLATE_VERSION = 1

# content_hash (sha256, stamp removed) of each host's rendered body at
# PINNED_TEMPLATE_VERSION, computed against GOLDEN_API_URL and _golden_app().
PINNED_CONTENT_HASHES = {
    "github": "ecb3b5f953cb448b36fba02e960838122ad1e51ed4aaa4edee25ee4368724c86",
    "gitlab": "6a5b611916a74cf588acf09b705692119df846778935eac9fb0e92948ece6a1a",
    "gitea": "7b657331f76fa07f7b2b638b356b7425686039153f1e10604b3588a5dac2c4c9",
    "bitbucket": "100c976863570761c7bf798556ca2bd1859c2db31cd0a5d0f108209ca51fa933",
}

_BUMP_HINT = (
    "A managed CI-workflow body changed. If that's intentional, bump "
    "TEMPLATE_VERSION in astrolift_scm/ci_templates.py, set "
    "PINNED_TEMPLATE_VERSION to match, and refresh PINNED_CONTENT_HASHES."
)


def test_template_version_matches_pins():
    assert TEMPLATE_VERSION == PINNED_TEMPLATE_VERSION, _BUMP_HINT


@pytest.mark.parametrize("host", sorted(_RENDERERS))
def test_content_hash_is_pinned(host, settings):
    settings.PLATFORM_API_URL = GOLDEN_API_URL
    body = _RENDERERS[host](_golden_app())
    assert content_hash(body) == PINNED_CONTENT_HASHES[host], f"{host}: {_BUMP_HINT}"


@pytest.mark.parametrize("host", sorted(_RENDERERS))
def test_every_render_is_stamped(host, settings):
    """Each rendered body carries an inert ``#`` stamp inside its header."""
    settings.PLATFORM_API_URL = GOLDEN_API_URL
    body = _RENDERERS[host](_golden_app())
    parsed = parse_stamp(body)

    assert parsed.version == TEMPLATE_VERSION
    # The stamp embeds the first 16 hex of the (stamp-stripped) content hash.
    assert parsed.declared_sha == content_hash(body)[:16]

    lines = body.split("\n")
    stamp_idx = next(i for i, ln in enumerate(lines) if ln.startswith("# astrolift-managed:"))
    first_noncomment = next(i for i, ln in enumerate(lines) if ln and not ln.lstrip().startswith("#"))
    # Stamp lives within the leading comment header, and is itself a host
    # comment (so it never alters pipeline behaviour).
    assert stamp_idx < first_noncomment
    assert lines[stamp_idx].startswith("#")
    assert f"template-version={TEMPLATE_VERSION}" in lines[stamp_idx]


@pytest.mark.parametrize("host", sorted(_RENDERERS))
def test_stamp_removal_is_hash_stable(host, settings):
    """content_hash is identical with or without the stamp line present."""
    settings.PLATFORM_API_URL = GOLDEN_API_URL
    body = _RENDERERS[host](_golden_app())
    parsed = parse_stamp(body)
    assert content_hash(parsed.body_without_stamp) == content_hash(body)
    # And stamping is round-trippable: strip → re-derive the original body.
    assert parsed.body_without_stamp != body  # a stamp was actually present


def test_content_hash_ignores_stamp_version():
    """A version bump alone must not change content_hash — it hashes the BODY.

    Two files with identical bodies but different stamp versions hash equal;
    that's what makes the golden pin a statement about template CONTENT, not
    about the version number stamped on it.
    """
    body = "# header\nname: demo\njobs: {}\n"
    v1 = stamp_workflow(body, version=1, digest="a" * 64)
    v2 = stamp_workflow(body, version=2, digest="a" * 64)
    assert v1 != v2  # the stamp lines differ
    assert content_hash(v1) == content_hash(v2) == content_hash(body)


def test_parse_stamp_absent_is_identity():
    """An unstamped file parses to (None, None, itself)."""
    raw = "# just a header\nname: demo\n"
    parsed = parse_stamp(raw)
    assert parsed.version is None
    assert parsed.declared_sha is None
    assert parsed.body_without_stamp == raw


def test_git_blob_sha_matches_git():
    """git_blob_sha reproduces `git hash-object` for a known input."""
    # printf 'hello' | git hash-object --stdin
    assert git_blob_sha(b"hello") == "b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0"
    # Empty blob is git's well-known e69de29... sha.
    assert git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
