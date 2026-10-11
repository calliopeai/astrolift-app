"""Managed CI stamping and retained legacy deploy-only template helpers.

Production sync uses ``services.workflow_sync``. Its GitHub template builds
and publishes explicitly CI-pushed images before notifying the platform, with
app-owned workflow paths and secret names. The legacy renderers below remain
for compatibility tests; they are not the production sync entry point.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re

# ---- template versioning (#1208) ------------------------------------
#
# TEMPLATE_VERSION is the single source of truth for "which generation
# of the managed CI workflow does the platform currently render". It is
# stamped into every workflow file we push (see ``stamp_workflow`` and
# the ``render_astrolift_*`` renderers in
# ``astrolift_scm.services.workflow_sync``) and persisted per-app on
# ``RegisteredApp.ci_workflow_template_version`` so a later phase can tell
# a repo whose file predates a template fix (``template_stale``) apart
# from one that's current.
#
# INCREMENT THIS whenever ANY managed workflow body changes — that means
# any edit to a ``render_astrolift_*`` output OR to
# ``astrolift_lifecycle/templates/astrolift-ci.yml.j2``. The golden test
# ``astrolift_scm/tests/test_ci_template_version.py`` pins the
# ``content_hash`` of every host's rendered body to this version and goes
# red on a body change with no bump, so the two can never drift apart.
#
# v2 (#emr-deploy-only): renderers gained a deploy-only mode for apps with
# no platform-built image (blank ``registry_repo_uri``) — the ECR-login +
# build/push steps are dropped and only the notify step runs. The
# platform-built render path is byte-for-byte unchanged, so the golden
# content hashes below are identical to v1; only the stamped version moves.
#
# v3 (#1220): the notify step targets the real CI deploy endpoint
# (``/api/cli/v1/apps/<slug>/deploy/``) with a wildcard ``image_tags``
# body and an explicit 2xx status assertion (a 302 from an auth layer
# used to count as success). The GitHub template additionally skips the
# build/push when the commit's image already exists in the registry, so
# re-runs on a built SHA no longer die on immutable tags.
#
# v4 (#1220): the notify body carries ``branch`` (the runner's own ref —
# ``github.ref_name`` / ``$CI_COMMIT_REF_NAME`` / ``$BITBUCKET_BRANCH``);
# ``ci_deploy`` requires it and v3 got a 400 back.
#
# v5: a RegisteredApp whose workload is an agent renders a package-validator
# workflow instead of the app Docker-build/deploy workflow. Agent source is
# frozen by the signed push webhook; image publishing remains repo-owned.
#
# v6: GitHub workflows use repo-scoped, per-app concurrency groups and cancel
# superseded in-progress runs. Agent validators also use per-agent filenames
# so independently registered monorepo members cannot overwrite one another.
# Agent source webhooks separately enforce source-SHA freshness because
# Actions cancellation is cooperative and does not order webhook deliveries.
# v7 (#1697): the agent validator required the selected manifest to declare
# exactly ONE workload in total, while the template is selected because the
# app HAS an agent workload. An app+agent repo therefore always failed its
# own CI -- the workflow contradicted the reason it was written -- and the
# only way through was to split the agent into agents/<slug>/astrolift.toml.
# It now requires at least one agent workload and says what it found.
# v8 (#2148): GitHub AWS authentication uses the actual ECR/default-cluster
# region, omits unconfigured deploy-only AWS auth, and quotes YAML inputs.
# v9 (#2139): ordinary GitHub apps have GUID-owned workflow paths and deploy
# secrets, GUID concurrency/PR branches and app/org/repo ownership; cleanup
# retains legacy or edited files and binds the inspected blob SHA. ci_pushed
# renders persisted Dockerfile/context/arguments and refuses
# missing image/build configuration instead of producing a notify-only job.
# v10 (#2300): the notify step's ``secrets['...']`` lookup is single-quoted
# (v9's double quotes failed every run at parse time), and ci_pushed reads
# the manifest's primary-container dockerfile_path/build_context for any
# field the app left at its default, as the platform build does.
TEMPLATE_VERSION = 10

# The stamp is a host-agnostic ``#`` comment so it's inert on GitHub
# Actions / GitLab CI / Bitbucket Pipelines / Gitea alike — it never
# changes what the pipeline does, only records provenance.
_STAMP_PREFIX = "# astrolift-managed:"
_STAMP_RE = re.compile(r"^#\s*astrolift-managed:\s*template-version=(\d+)\s+sha256=([0-9a-fA-F]+)\s*$")


@dataclasses.dataclass(frozen=True, slots=True)
class ParsedStamp:
    """Result of :func:`parse_stamp`.

    ``version`` / ``declared_sha`` are ``None`` when the file carries no
    Astrolift stamp line (a hand-authored or pre-versioning file).
    ``body_without_stamp`` is the file with the stamp line removed — for
    an unstamped file it is the input unchanged, so hashing it is stable
    whether or not a stamp is present.
    """

    version: int | None
    declared_sha: str | None
    body_without_stamp: str


def parse_stamp(file_text: str) -> ParsedStamp:
    """Extract the Astrolift stamp (if any) from a workflow file's text.

    Removes the FIRST matching stamp line so the returned
    ``body_without_stamp`` round-trips: stamping a body and then parsing
    the stamp back off yields the original body byte-for-byte. Line
    structure (including the trailing newline) is preserved because we
    split/join on ``"\n"`` without touching any other line.
    """
    lines = file_text.split("\n")
    for i, line in enumerate(lines):
        m = _STAMP_RE.match(line)
        if m:
            del lines[i]
            return ParsedStamp(
                version=int(m.group(1)),
                declared_sha=m.group(2).lower(),
                body_without_stamp="\n".join(lines),
            )
    return ParsedStamp(version=None, declared_sha=None, body_without_stamp=file_text)


def content_hash(body: str) -> str:
    """SHA-256 hex of ``body`` with any existing stamp line removed.

    Removing the stamp first makes the hash self-consistent: the digest
    of a freshly rendered (unstamped) body equals the digest recomputed
    from the same body AFTER it's been stamped and round-tripped back
    through a repo. That's what lets a later drift check compare a
    fetched file against the template without the stamp line perturbing
    the comparison.
    """
    return hashlib.sha256(parse_stamp(body).body_without_stamp.encode("utf-8")).hexdigest()


def stamp_workflow(body: str, *, version: int, digest: str) -> str:
    """Insert the managed-provenance comment right after the human header.

    The "human header" is the leading run of ``#`` comment lines every
    ``render_astrolift_*`` body opens with. The stamp lands immediately
    below it so it reads as part of the banner, e.g.::

        # Managed by Astrolift — do not edit by hand. ...
        # astrolift-managed: template-version=1 sha256=0123456789abcdef
        name: Astrolift CI

    ``digest`` is the full :func:`content_hash`; only its first 16 hex
    chars go in the stamp (enough to spot a mismatch by eye, and the
    authoritative comparison always recomputes the full hash anyway).
    """
    stamp = f"{_STAMP_PREFIX} template-version={version} sha256={digest[:16]}"
    lines = body.split("\n")
    insert_at = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            insert_at = i + 1
        else:
            break
    lines.insert(insert_at, stamp)
    return "\n".join(lines)


def git_blob_sha(full_file_bytes: bytes) -> str:
    """The SHA-1 git stores for ``full_file_bytes`` as a blob.

    Computed locally — ``sha1("blob <len>\\0" + data)`` — so the value can
    be compared against the ``sha`` a host returns for a file (GitHub's
    contents API, GitLab's, etc.) with no extra round-trip. This is git's
    content-addressing scheme, not a security hash.
    """
    header = b"blob %d\0" % len(full_file_bytes)
    return hashlib.sha1(header + full_file_bytes, usedforsecurity=False).hexdigest()


# ---- branches -------------------------------------------------------

_DEFAULT_BRANCHES = ("main", "master")


def _branch_list(deploy_branch: str | None) -> list[str]:
    """Render the ``push.branches`` list. An explicit deploy branch
    wins; absent that, we accept both ``main`` and ``master`` so the
    workflow fires on whichever convention the repo follows."""
    if deploy_branch and deploy_branch.strip():
        return [deploy_branch.strip()]
    return list(_DEFAULT_BRANCHES)


# ---- GitHub Actions -------------------------------------------------


def render_github_actions_deploy_yml(
    *,
    app_slug: str,
    deploy_branch: str | None,
) -> str:
    """Render the canonical ``.github/workflows/astrolift-deploy.yml``.

    YAML is hand-built (not generated through a library) because the
    file is small, the keys are stable, and operators will diff it
    against their existing CI config. Hand-built keeps the output
    readable for that diff."""
    branches = _branch_list(deploy_branch)
    branches_yaml = "[" + ", ".join(branches) + "]"
    # The slug goes inline; it's already validated against the
    # subdomain regex on the registry side, so quoting via JSON is
    # belt-and-suspenders — never bare-inject untrusted text into YAML.
    slug_literal = json.dumps(app_slug)
    return (
        "# Generated by Astrolift. Edit at your own risk; the wizard\n"
        "# regenerates this file when you re-push from /apps/<slug>.\n"
        "name: Astrolift deploy\n"
        "\n"
        "on:\n"
        "  push:\n"
        f"    branches: {branches_yaml}\n"
        "\n"
        "concurrency:\n"
        f"  group: astrolift-{app_slug}\n"
        "  cancel-in-progress: true\n"
        "\n"
        "jobs:\n"
        "  deploy:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Trigger Astrolift deploy\n"
        "        env:\n"
        "          ASTROLIFT_API_URL: ${{ secrets.ASTROLIFT_API_URL }}\n"
        "          ASTROLIFT_DEPLOY_TOKEN: ${{ secrets.ASTROLIFT_DEPLOY_TOKEN }}\n"
        f"          ASTROLIFT_APP_SLUG: {slug_literal}\n"
        "          ASTROLIFT_COMMIT_SHA: ${{ github.sha }}\n"
        "        run: |\n"
        "          docker run --rm \\\n"
        "            -e ASTROLIFT_API_URL -e ASTROLIFT_DEPLOY_TOKEN -e ASTROLIFT_APP_SLUG -e ASTROLIFT_COMMIT_SHA \\\n"
        "            calliopeai/astrolift-cli:latest ci deploy\n"
    )


# ---- GitLab CI ------------------------------------------------------


def render_gitlab_ci_deploy_yml(
    *,
    app_slug: str,
    deploy_branch: str | None,
) -> str:
    """Render the GitLab equivalent.

    GitLab's CI runs from a single ``.gitlab-ci.yml`` at the repo
    root; the ``only`` / ``rules`` constraint pins the job to the
    deploy branch. Operators with an existing ``.gitlab-ci.yml`` will
    want to include this file via ``include:`` rather than overwrite
    — that's a follow-up; this version assumes a fresh repo."""
    branches = _branch_list(deploy_branch)
    slug_literal = json.dumps(app_slug)
    rules = "\n".join(f"    - if: '$CI_COMMIT_REF_NAME == \"{b}\"'" for b in branches)
    return (
        "# Generated by Astrolift. Operators with an existing\n"
        "# .gitlab-ci.yml should `include:` this file instead of\n"
        "# overwriting their own pipeline.\n"
        "stages:\n"
        "  - deploy\n"
        "\n"
        "astrolift_deploy:\n"
        "  stage: deploy\n"
        "  image: ubuntu:24.04\n"
        "  rules:\n"
        f"{rules}\n"
        "  script:\n"
        "    - docker run --rm -e ASTROLIFT_API_URL -e ASTROLIFT_DEPLOY_TOKEN -e ASTROLIFT_APP_SLUG -e ASTROLIFT_COMMIT_SHA calliopeai/astrolift-cli:latest ci deploy\n"
        "  variables:\n"
        "    ASTROLIFT_API_URL: $ASTROLIFT_API_URL\n"
        "    ASTROLIFT_DEPLOY_TOKEN: $ASTROLIFT_DEPLOY_TOKEN\n"
        f"    ASTROLIFT_APP_SLUG: {slug_literal}\n"
        "    ASTROLIFT_COMMIT_SHA: $CI_COMMIT_SHA\n"
    )


# ---- Bitbucket Pipelines --------------------------------------------


def render_bitbucket_pipelines_deploy_yml(
    *,
    app_slug: str,
    deploy_branch: str | None,
) -> str:
    """Render a minimal ``bitbucket-pipelines.yml`` that runs the
    ``astro app deploy`` CLI on push to the deploy branch.

    This variant delegates image-building to the operator's own
    Bitbucket pipeline steps and only handles the Astrolift deploy
    notification — matching the contract of the GitHub/GitLab deploy
    templates above. The full build + ECR push template (including
    Docker service and AWS credentials) lives in
    ``workflow_sync.render_astrolift_bitbucket_pipeline``."""
    branches = _branch_list(deploy_branch)
    slug_literal = json.dumps(app_slug)
    branch_blocks = ""
    for b in branches:
        branch_blocks += (
            f"    {b}:\n"
            "      - step:\n"
            "          name: Astrolift deploy\n"
            "          script:\n"
            "            - apt-get update -qq && apt-get install -y -qq curl ca-certificates\n"
            "            - curl -fsSL https://get.astrolift.dev | sh\n"
            '            - export PATH="$HOME/.astrolift/bin:$PATH"\n'
            f"            - astro app deploy --app={slug_literal} --image-tag=$BITBUCKET_COMMIT\n"
        )
    return (
        "# Generated by Astrolift. Edit at your own risk; the wizard\n"
        "# regenerates this file when you re-push from /apps/<slug>.\n"
        "image: atlassian/default-image:4\n"
        "\n"
        "pipelines:\n"
        "  branches:\n"
        f"{branch_blocks}"
    )


# ---- Gitea Actions --------------------------------------------------


def render_gitea_actions_deploy_yml(
    *,
    app_slug: str,
    deploy_branch: str | None,
) -> str:
    """Render a ``.gitea/workflows/astrolift-deploy.yml`` for Gitea.

    Gitea Actions uses GitHub Actions syntax — ``${{ github.sha }}``,
    ``${{ secrets.NAME }}``, and ``runs-on: ubuntu-latest`` are all
    honoured by Gitea's act-compatible runner. The only substantive
    difference is the ``on.push.branches`` filter uses the Gitea repo's
    configured deploy branch."""
    branches = _branch_list(deploy_branch)
    branches_yaml = "[" + ", ".join(branches) + "]"
    slug_literal = json.dumps(app_slug)
    return (
        "# Generated by Astrolift. Edit at your own risk; the wizard\n"
        "# regenerates this file when you re-push from /apps/<slug>.\n"
        "name: Astrolift deploy\n"
        "\n"
        "on:\n"
        "  push:\n"
        f"    branches: {branches_yaml}\n"
        "\n"
        "concurrency:\n"
        f"  group: astrolift-deploy-{app_slug}-${{{{ github.ref }}}}\n"
        "  cancel-in-progress: false\n"
        "\n"
        "jobs:\n"
        "  deploy:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Trigger Astrolift deploy\n"
        "        env:\n"
        "          ASTROLIFT_API_URL: ${{ secrets.ASTROLIFT_API_URL }}\n"
        "          ASTROLIFT_DEPLOY_TOKEN: ${{ secrets.ASTROLIFT_DEPLOY_TOKEN }}\n"
        f"          ASTROLIFT_APP_SLUG: {slug_literal}\n"
        "          ASTROLIFT_COMMIT_SHA: ${{ github.sha }}\n"
        "        run: |\n"
        "          docker run --rm \\\n"
        "            -e ASTROLIFT_API_URL -e ASTROLIFT_DEPLOY_TOKEN -e ASTROLIFT_APP_SLUG -e ASTROLIFT_COMMIT_SHA \\\n"
        "            calliopeai/astrolift-cli:latest ci deploy\n"
    )


# ---- dispatcher -----------------------------------------------------


def default_workflow_path_for(source_kind: str) -> str:
    """Conventional path for the workflow file the operator
    overrides per call."""
    if source_kind in {"github"}:
        return ".github/workflows/astrolift-deploy.yml"
    if source_kind in {"gitlab"}:
        return ".gitlab-ci.yml"
    if source_kind in {"bitbucket"}:
        return "bitbucket-pipelines.yml"
    if source_kind in {"gitea"}:
        return ".gitea/workflows/astrolift-deploy.yml"
    raise ValueError(f"no default workflow path for source_kind={source_kind!r}")


def render_workflow_for(
    *,
    source_kind: str,
    app_slug: str,
    deploy_branch: str | None,
) -> str:
    """Pick the right (legacy System-A) deploy-workflow body for the host.

    DEPRECATED (#1212): this and the four ``render_*_deploy_yml`` renderers
    below are the legacy "System A" that produced the unstamped
    ``astrolift-deploy.yml`` (and, on GitLab/Bitbucket, collided with System B
    on the shared ``.gitlab-ci.yml`` / ``bitbucket-pipelines.yml`` path). The
    ``pushCiWorkflow`` mutation that was their only production caller now
    delegates to System B (``workflow_sync.sync_workflow_file_to_repo``), which
    renders the stamped ``astrolift-ci.yml``. These are retained only so their
    unit tests keep pinning the legacy shape; nothing pushes their output. Do
    not wire new callers — use System B.
    """
    if source_kind == "github":
        return render_github_actions_deploy_yml(
            app_slug=app_slug,
            deploy_branch=deploy_branch,
        )
    if source_kind == "gitlab":
        return render_gitlab_ci_deploy_yml(
            app_slug=app_slug,
            deploy_branch=deploy_branch,
        )
    if source_kind == "bitbucket":
        return render_bitbucket_pipelines_deploy_yml(
            app_slug=app_slug,
            deploy_branch=deploy_branch,
        )
    if source_kind == "gitea":
        return render_gitea_actions_deploy_yml(
            app_slug=app_slug,
            deploy_branch=deploy_branch,
        )
    raise ValueError(f"no CI template for source_kind={source_kind!r}")
