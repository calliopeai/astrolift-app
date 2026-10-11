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
    github_workflow_path_for,
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
        guid="11111111-1111-4111-8111-111111111111",
        organization_id=1,
        build_mode="ci_pushed",
        dockerfile_path="Dockerfile",
        build_context=".",
        build_args={},
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
PINNED_TEMPLATE_VERSION = 10

# content_hash (sha256, stamp removed) of each host's rendered body at
# PINNED_TEMPLATE_VERSION, computed against GOLDEN_API_URL and _golden_app().
#
# v3 (#1220): every host's notify step moved to the real CI deploy endpoint
# (``/api/cli/v1/apps/<slug>/deploy/``) with a wildcard ``image_tags`` body
# and a 2xx status assertion; the GitHub template additionally gained the
# skip-if-built ECR probe. The deploy-only render (blank registry_repo_uri)
# is covered by ``test_deploy_only_workflow.py`` instead.
PINNED_CONTENT_HASHES = {
    "github": "630aa557283580ea8d401f61779e2ba36f5a2991ebeebb3bd0db0812a8bfc479",
    "gitlab": "77f0d228c7b159e6040b7ec10a348b039f94ef188a565ea901b7c422b6dbcbc8",
    "gitea": "d370f2cf4f0080e7c04ad7e00ee735163c688a4222f8415114d894f6a9b83170",
    "bitbucket": "7821f3ac5724b4b30b3e48473652c39eb3f1edff8d771dcdad7e89b10e43e213",
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


def test_agent_github_workflow_validates_package_without_app_build_or_deploy(settings):
    settings.PLATFORM_API_URL = GOLDEN_API_URL
    app = _golden_app()
    app.is_agent = True
    app.manifest_path = "agents/emr-bug-triage/astrolift.toml"

    body = render_astrolift_ci_workflow(app)

    assert "name: astrolift agent package" in body
    assert "group: astrolift-11111111111141118111111111111111" in body
    assert "cancel-in-progress: true" in body
    assert "agents/emr-bug-triage/**" in body
    # v7 (#1697): at least one agent workload, not exactly one workload in
    # total. The old rule failed every app+agent repo -- the template was
    # selected because the app HAS an agent workload, then refused the
    # manifest for having anything else beside it.
    assert "selected manifest declares no agent workload" in body
    assert "exactly one agent workload" not in body
    assert "docker build" not in body
    assert "/api/cli/v1/apps/" not in body
    assert "ASTROLIFT_DEPLOY_TOKEN" not in body
    assert github_workflow_path_for(app) == ".github/workflows/astrolift-agent-hello-app.yml"


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
