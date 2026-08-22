"""Push the Astrolift CI workflow file to a tenant's source repo (#384).

Companion to ``astrolift_scm.services.workflows`` (#387): where that
module's :func:`dispatch_astrolift_ci_workflow` *triggers* the workflow,
this one *materializes the YAML* into the conventional CI file path on
the configured deploy branch so the dispatch endpoint actually has a
workflow file to run against. The pairing exists because a fresh app
won't have the workflow yet on the operator's side, and the inline
"paste this YAML" affordance on the Settings page is friction we'd
rather skip when we already hold a SourceConnection that can write.

Render → fetch existing → compare → write-or-skip. The GitHub renderer
does a small string-substitution pass against
``astrolift_lifecycle/templates/astrolift-ci.yml.j2``; the GitLab
renderer (#735) builds the equivalent document inline using the same
five variables. Branch-protection handling: a "compliant" repo will
gate direct pushes to the deploy branch. We probe the protections
endpoint before PUT-ing; when the branch is protected, we land the
file on a side branch and open a PR/MR so the change goes through the
repo's review path. ``status="pr_opened"`` is returned in that case.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Final

from django.conf import settings
from django.utils import timezone

from astrolift_registry.models import RegisteredApp
from astrolift_scm.ci_templates import (
    TEMPLATE_VERSION,
    content_hash,
    git_blob_sha,
    stamp_workflow,
)
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers import ProviderError, fetch_file, open_pull_request, put_file
from astrolift_scm.providers.gitea import (
    GiteaProviderError,
)
from astrolift_scm.providers.gitea import (
    _api_base as _gitea_api_base,
)
from astrolift_scm.providers.gitea import (
    _token as _gitea_token,
)
from astrolift_scm.providers.github import GITHUB_API_DEFAULT, GithubProviderError, _token
from astrolift_scm.providers.gitlab import (
    GitlabProviderError,
)
from astrolift_scm.providers.gitlab import (
    _api_base as _gitlab_api_base,
)
from astrolift_scm.providers.gitlab import (
    _token as _gitlab_token,
)

logger = logging.getLogger(__name__)

# Conventional CI workflow paths per host.
WORKFLOW_PATH: Final = ".github/workflows/astrolift-ci.yml"
GITLAB_WORKFLOW_PATH: Final = ".gitlab-ci.yml"
BITBUCKET_WORKFLOW_PATH: Final = "bitbucket-pipelines.yml"
GITEA_WORKFLOW_PATH: Final = ".gitea/workflows/astrolift-ci.yml"

# GitHub App permission hint for the CI-workflow write. The workflow file
# lives under .github/workflows/, so a GitHub App needs the Workflows
# permission on top of Contents: write — naming both makes a 403 on an
# under-permitted App actionable instead of a misleading "reconnect".
# Threaded into put_file → put_github_file; see
# astrolift_scm.auth_errors.github_auth_error_message.
CI_WORKFLOW_WRITE_OPERATION: Final = "write the CI workflow file"
CI_WORKFLOW_WRITE_PERMISSION: Final = "Contents: write and Workflows: write"


@dataclasses.dataclass(frozen=True, slots=True)
class WorkflowSyncResult:
    """Outcome of :func:`sync_workflow_file_to_repo`.

    ``status`` is the discriminator the resolver surfaces verbatim:

    * ``created``      — file was missing on the deploy branch; we
                          committed the rendered content.
    * ``updated``      — file existed but its body diverged from the
                          rendered template; we overwrote it.
    * ``in_sync``      — file existed and matched byte-for-byte; we
                          did nothing.
    * ``pr_opened``    — deploy branch is protected; we landed the
                          content on a side branch and opened a PR.
                          ``pr_url`` is the host-side review link.
    * ``fetch_failed`` — couldn't reach the host or the connection
                          token was rejected. ``error`` carries the
                          host-side message.
    """

    status: str
    commit_sha: str = ""
    pr_url: str = ""
    rendered_size: int = 0
    error: str = ""


class WorkflowSyncError(Exception):
    """Raised for caller-fault conditions (no source repo, unsupported
    host, no usable connection). Host-side failures are returned as a
    ``WorkflowSyncResult`` with ``status="fetch_failed"`` so the
    resolver can map them to a clean ``MutationResult`` envelope."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "astrolift_lifecycle" / "templates" / "astrolift-ci.yml.j2"
)

# Matches ``{{ name }}`` — single substitution variable with optional
# whitespace. Deliberately conservative: any structure beyond a bare
# identifier is left in place so a stray ``${{ github.sha }}`` inside
# the template body (a literal GitHub Actions expression, not a Jinja
# variable) is untouched.
_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# Matches a whole-line ``{% if flag %}`` … ``{% endif %}`` block (both
# markers on their own line). ``{%``/``%}`` never collide with GitHub
# Actions ``${{ … }}`` expressions, so this pass is safe to run before the
# ``{{ var }}`` substitution above. Non-greedy + DOTALL so the innermost
# block is captured and the marker lines (with their trailing newline) are
# consumed cleanly — see :func:`_apply_blocks`.
_BLOCK_RE = re.compile(
    r"\{%\s*if\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*%\}\n(.*?)\{%\s*endif\s*%\}\n",
    re.DOTALL,
)


def _load_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _apply_blocks(template: str, flags: dict[str, bool]) -> str:
    """Resolve ``{% if flag %}…{% endif %}`` blocks against ``flags``.

    A truthy flag keeps the block's inner body (marker lines dropped, so the
    output is byte-identical to a template that never carried the markers); a
    falsy flag drops the whole block. An unknown flag leaves the block in
    place — mirrors the conservative "leave unrecognized tokens alone" stance
    of the ``{{ var }}`` substitution, so a sibling agent can add a block for
    a flag this code doesn't know yet without it rendering broken.
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in flags:
            return match.group(0)
        return match.group(2) if flags[name] else ""

    return _BLOCK_RE.sub(_sub, template)


def _is_agent_app(app: RegisteredApp) -> bool:
    """Whether this RegisteredApp represents a first-class agent package."""
    explicit = getattr(app, "is_agent", None)
    if explicit is not None:
        return bool(explicit)
    workloads = getattr(app, "workloads", None)
    if workloads is None or not getattr(app, "pk", None):
        return False
    try:
        return workloads.filter(kind="agent", deleted_at__isnull=True).exists()
    except (AttributeError, TypeError, ValueError):
        return False


def render_astrolift_agent_ci_workflow(app: RegisteredApp) -> str:
    """Render a safe source-package validator for a registered agent.

    Agent Tasks are not standing app deployments. The source webhook freezes
    the selected manifest/package at the pushed SHA, while image publishing is
    owned by the repo's image workflow. This managed workflow therefore
    validates the selected TOML instead of building the repo root or calling
    the app-deploy endpoint (both are incorrect for a modular agent repo).
    """
    branch = json.dumps((app.deploy_branch or "main").strip() or "main")
    manifest_path = str(app.manifest_path or "astrolift.toml").lstrip("/")
    manifest_literal = json.dumps(manifest_path)
    if "/" in manifest_path:
        package_glob = json.dumps(f"{manifest_path.rsplit('/', 1)[0]}/**")
        path_rows = f'      - {package_glob}\n      - "astrolift.agents.toml"\n'
    else:
        path_rows = f'      - {manifest_literal}\n      - "astrolift.agents.toml"\n'
    body = (
        "# Managed by Astrolift — agent package validation. Source delivery is handled by the signed source webhook.\n"
        "name: astrolift agent package\n"
        "\n"
        "on:\n"
        "  push:\n"
        f"    branches: [{branch}]\n"
        "    paths:\n"
        f"{path_rows}"
        "  workflow_dispatch: {}\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "\n"
        "concurrency:\n"
        f"  group: astrolift-{app.slug}\n"
        "  cancel-in-progress: true\n"
        "\n"
        "jobs:\n"
        "  validate-agent-package:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Checkout\n"
        "        uses: actions/checkout@v4\n"
        "\n"
        "      - name: Validate selected agent manifest\n"
        "        env:\n"
        f"          ASTROLIFT_AGENT_MANIFEST: {manifest_literal}\n"
        "        run: |\n"
        "          python3 - <<'PY'\n"
        "          import os\n"
        "          import pathlib\n"
        "          import tomllib\n"
        "\n"
        "          path = pathlib.Path(os.environ['ASTROLIFT_AGENT_MANIFEST'])\n"
        "          if not path.is_file():\n"
        "              raise SystemExit(f'agent manifest not found: {path}')\n"
        "          data = tomllib.loads(path.read_text(encoding='utf-8'))\n"
        "          agents = [row for row in data.get('workloads', []) if row.get('kind') == 'agent']\n"
        "          if len(agents) != 1 or len(data.get('workloads', [])) != 1:\n"
        "              raise SystemExit('selected manifest must declare exactly one agent workload')\n"
        "          print(f\"validated agent package: {path} ({agents[0].get('name', '?')})\")\n"
        "          PY\n"
        "\n"
        "      - name: Package delivery contract\n"
        "        run: echo 'Astrolift freezes this source slice from the signed push webhook; this workflow does not deploy a standing app.'\n"
    )
    return stamp_workflow(body, version=TEMPLATE_VERSION, digest=content_hash(body))


def github_workflow_path_for(app: RegisteredApp) -> str:
    """Return a collision-free managed workflow path for ``app``.

    A repository can contain many independently registered agents. Their
    validators must not overwrite the ordinary app workflow or one another.
    App repos retain the established path for backward compatibility.
    """
    if _is_agent_app(app):
        return f".github/workflows/astrolift-agent-{app.slug}.yml"
    return WORKFLOW_PATH


def render_astrolift_ci_workflow(app: RegisteredApp) -> str:
    """Render the workflow YAML for ``app`` against the file template.

    The variables are pulled from the app's persisted state:

    * ``app_slug``      — ``RegisteredApp.slug``
    * ``deploy_branch`` — ``app.deploy_branch`` (fallback: ``main``)
    * ``ecr_uri``       — ``app.registry_repo_uri``
    * ``ecr_repo_name`` — repo path within the registry (URI minus host),
                          for the ``describe-images`` skip-if-built probe
    * ``push_role_arn`` — ``app.push_role_ref``
    * ``api_url``       — ``settings.PLATFORM_API_URL`` (trimmed)

    The deploy token is NOT templated — the workflow references it
    through ``${{ secrets.ASTROLIFT_DEPLOY_TOKEN }}`` so plaintext
    never sits in a file in the operator's repo. Same goes for the
    role ARN's session naming — the template uses
    ``${{ github.run_id }}`` directly.

    The notify step POSTs the real CI deploy endpoint
    (``/api/cli/v1/apps/<slug>/deploy/``, #1220) with a wildcard
    ``image_tags`` the server expands against the current manifest, and
    asserts a 2xx status — a redirect from an auth layer in front of the
    platform must fail the run, not masquerade as success.

    **Deploy-only mode.** When the app has no platform-built image —
    ``registry_repo_uri`` is empty/blank — the workflow is rendered
    WITHOUT the ECR-login + build-and-push steps (the image is built by
    a separate pipeline; there is nothing for this workflow to build).
    Checkout, the OIDC credentials step and the Astrolift notify step are
    kept: CI's only job is to tell the platform a new SHA exists.
    """
    if _is_agent_app(app):
        return render_astrolift_agent_ci_workflow(app)
    template = _load_template()
    api_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    ecr_uri = app.registry_repo_uri or ""
    # A non-empty registry_repo_uri is the signal that the platform builds
    # and pushes this app's image; empty ⇒ deploy-only (built elsewhere).
    platform_built = bool(ecr_uri.strip())
    values = {
        "app_slug": app.slug,
        "deploy_branch": (app.deploy_branch or "main").strip() or "main",
        "ecr_uri": ecr_uri,
        "ecr_repo_name": ecr_uri.split("/", 1)[1] if platform_built and "/" in ecr_uri else "",
        "push_role_arn": app.push_role_ref or "",
        "api_url": api_url,
    }

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            # Leave unknown identifiers in place — defensive against
            # a sibling agent extending the template with a variable
            # before this code learns about it.
            return match.group(0)
        return values[name]

    template = _apply_blocks(template, {"platform_built": platform_built})
    rendered = _VAR_RE.sub(_replace, template)
    return stamp_workflow(rendered, version=TEMPLATE_VERSION, digest=content_hash(rendered))


def render_astrolift_bitbucket_pipeline(app: RegisteredApp) -> str:
    """Render the full ``bitbucket-pipelines.yml`` for ``app``.

    Builds the Docker image, pushes to ECR with AWS credentials read
    from Bitbucket repository variables, then notifies Astrolift.
    The operator must configure ``ASTROLIFT_AWS_ACCESS_KEY_ID``,
    ``ASTROLIFT_AWS_SECRET_ACCESS_KEY``, ``ASTROLIFT_AWS_DEFAULT_REGION``,
    and ``ASTROLIFT_DEPLOY_TOKEN`` as Bitbucket repository variables
    (Settings → Pipelines → Repository variables).
    """
    api_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    ecr_uri = app.registry_repo_uri or ""
    ecr_registry = ecr_uri.split("/")[0] if ecr_uri else ""
    # Empty registry_repo_uri ⇒ image built by a separate pipeline; render
    # deploy-only (no docker service, no ECR login, no build/push).
    platform_built = bool(ecr_uri.strip())
    deploy_branch = (app.deploy_branch or "main").strip() or "main"
    api_url_literal = json.dumps(api_url)

    if platform_built:
        definitions_block = "definitions:\n  services:\n    docker:\n      type: docker\n\n"
        step_name = "Build, push, and notify Astrolift"
        services_block = "          services:\n            - docker\n"
        image_export = f'            - export ASTROLIFT_IMAGE="{ecr_uri}:$BITBUCKET_COMMIT"\n'
        pre_curl = (
            f'            - export ECR_REGISTRY="{ecr_registry}"\n'
            "            - apt-get update -qq && apt-get install -y -qq awscli curl ca-certificates\n"
            '            - aws ecr get-login-password --region "$ASTROLIFT_AWS_DEFAULT_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"\n'
            '            - docker build -t "$ASTROLIFT_IMAGE" .\n'
            '            - docker push "$ASTROLIFT_IMAGE"\n'
        )
    else:
        definitions_block = ""
        step_name = "Notify Astrolift"
        services_block = ""
        # No image is built here and the notify body carries the commit SHA
        # itself, so deploy-only has nothing to export.
        image_export = ""
        pre_curl = "            - apt-get update -qq && apt-get install -y -qq curl ca-certificates\n"

    body = (
        "# Managed by Astrolift — do not edit by hand."
        " Re-sync via Settings → CI setup → Sync workflow file.\n"
        "image: atlassian/default-image:4\n"
        "\n" + definitions_block + "pipelines:\n"
        "  branches:\n"
        f"    {deploy_branch}:\n"
        "      - step:\n"
        + f"          name: {step_name}\n"
        + services_block
        + "          script:\n"
        + image_export
        + pre_curl
        + "            - |\n"
        f"              code=$(curl -sS -o /tmp/astrolift-deploy-response.json -w '%{{http_code}}' -X POST {api_url_literal}/api/cli/v1/apps/{app.slug}/deploy/ \\\n"
        '                -H "Authorization: Bearer $ASTROLIFT_DEPLOY_TOKEN" \\\n'
        '                -H "Content-Type: application/json" \\\n'
        '                -d "{\\"image_tags\\":{\\"*\\":\\"$BITBUCKET_COMMIT\\"},\\"commit_sha\\":\\"$BITBUCKET_COMMIT\\",\\"branch\\":\\"$BITBUCKET_BRANCH\\",\\"trigger_kind\\":\\"ci\\"}")\n'
        "              cat /tmp/astrolift-deploy-response.json; echo\n"
        '              case "$code" in 2*) ;; *) echo "Astrolift deploy notification FAILED (HTTP $code)"; exit 1;; esac\n'
    )
    return stamp_workflow(body, version=TEMPLATE_VERSION, digest=content_hash(body))


def render_astrolift_gitea_ci_workflow(app: RegisteredApp) -> str:
    """Render a ``.gitea/workflows/astrolift-ci.yml`` for Gitea Actions.

    Gitea Actions uses GitHub Actions syntax. Builds the Docker image,
    pushes to ECR, then notifies Astrolift. Secrets are read from Gitea
    repository secrets (Settings → Actions → Secrets):
    ``ASTROLIFT_AWS_ACCESS_KEY_ID``, ``ASTROLIFT_AWS_SECRET_ACCESS_KEY``,
    ``ASTROLIFT_AWS_DEFAULT_REGION``, ``ASTROLIFT_DEPLOY_TOKEN``.
    """
    api_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    ecr_uri = app.registry_repo_uri or ""
    ecr_registry = ecr_uri.split("/")[0] if ecr_uri else ""
    # Empty registry_repo_uri ⇒ image built by a separate pipeline; render
    # deploy-only (drop the AWS-cred + build/push steps, keep the notify).
    platform_built = bool(ecr_uri.strip())
    deploy_branch = (app.deploy_branch or "main").strip() or "main"
    api_url_literal = json.dumps(api_url)

    if platform_built:
        build_steps = (
            "      - name: Configure AWS credentials\n"
            "        env:\n"
            "          AWS_ACCESS_KEY_ID: ${{ secrets.ASTROLIFT_AWS_ACCESS_KEY_ID }}\n"
            "          AWS_SECRET_ACCESS_KEY: ${{ secrets.ASTROLIFT_AWS_SECRET_ACCESS_KEY }}\n"
            "          AWS_DEFAULT_REGION: ${{ secrets.ASTROLIFT_AWS_DEFAULT_REGION }}\n"
            "        run: aws --version\n"
            "      - name: Build and push image to ECR\n"
            "        env:\n"
            "          AWS_ACCESS_KEY_ID: ${{ secrets.ASTROLIFT_AWS_ACCESS_KEY_ID }}\n"
            "          AWS_SECRET_ACCESS_KEY: ${{ secrets.ASTROLIFT_AWS_SECRET_ACCESS_KEY }}\n"
            "          AWS_DEFAULT_REGION: ${{ secrets.ASTROLIFT_AWS_DEFAULT_REGION }}\n"
            "        run: |\n"
            f'          aws ecr get-login-password | docker login --username AWS --password-stdin "{ecr_registry}"\n'
            f'          docker build -t "{ecr_uri}:${{{{ github.sha }}}}" .\n'
            f'          docker push "{ecr_uri}:${{{{ github.sha }}}}"\n'
        )
    else:
        build_steps = ""

    body = (
        "# Managed by Astrolift — do not edit by hand."
        " Re-sync via Settings → CI setup → Sync workflow file.\n"
        "name: Astrolift CI\n"
        "\n"
        "on:\n"
        "  push:\n"
        f"    branches: [{deploy_branch}]\n"
        "\n"
        "concurrency:\n"
        f"  group: astrolift-ci-{app.slug}-${{{{ github.ref }}}}\n"
        "  cancel-in-progress: false\n"
        "\n"
        "jobs:\n"
        "  build-and-deploy:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n" + build_steps + "      - name: Notify Astrolift\n"
        "        env:\n"
        "          ASTROLIFT_DEPLOY_TOKEN: ${{ secrets.ASTROLIFT_DEPLOY_TOKEN }}\n"
        "        run: |\n"
        f"          code=$(curl -sS -o /tmp/astrolift-deploy-response.json -w '%{{http_code}}' -X POST {api_url_literal}/api/cli/v1/apps/{app.slug}/deploy/ \\\n"
        '            -H "Authorization: Bearer $ASTROLIFT_DEPLOY_TOKEN" \\\n'
        '            -H "Content-Type: application/json" \\\n'
        '            -d \'{"image_tags":{"*":"${{ github.sha }}"},"commit_sha":"${{ github.sha }}","branch":"${{ github.ref_name }}","trigger_kind":"ci"}\')\n'
        "          cat /tmp/astrolift-deploy-response.json; echo\n"
        '          case "$code" in 2*) ;; *) echo "Astrolift deploy notification FAILED (HTTP $code)"; exit 1;; esac\n'
    )
    return stamp_workflow(body, version=TEMPLATE_VERSION, digest=content_hash(body))


def render_astrolift_gitlab_ci_workflow(app: RegisteredApp) -> str:
    """Render the GitLab CI equivalent of the GitHub Actions template (#735).

    Uses docker:24-dind to build and push to ECR. Static AWS credentials
    are expected as ``ASTROLIFT_AWS_ACCESS_KEY_ID``,
    ``ASTROLIFT_AWS_SECRET_ACCESS_KEY``, and ``ASTROLIFT_AWS_DEFAULT_REGION``
    GitLab CI variables (set by the variables-push mutation, #531). OIDC is
    not assumed here because GitLab runner OIDC with AWS requires additional
    runner-level config the operator may not have.
    """
    api_url = (getattr(settings, "PLATFORM_API_URL", "") or "").rstrip("/")
    ecr_uri = app.registry_repo_uri or ""
    # ECR login endpoint is just the registry host, not the repo path.
    ecr_registry = ecr_uri.split("/")[0] if ecr_uri else ""
    # Empty registry_repo_uri ⇒ image built by a separate pipeline; render
    # deploy-only (no build stage) so we never push a ``:<sha>`` blank tag.
    platform_built = bool(ecr_uri.strip())
    deploy_branch = (app.deploy_branch or "main").strip() or "main"
    slug_literal = json.dumps(app.slug)
    api_url_literal = json.dumps(api_url)
    rules_line = f"    - if: '$CI_COMMIT_REF_NAME == \"{deploy_branch}\"'"

    if platform_built:
        stages_block = "stages:\n  - build\n  - deploy\n"
        image_var = f'  ASTROLIFT_IMAGE: "{ecr_uri}:$CI_COMMIT_SHA"\n'
        build_job = (
            "build-image:\n"
            "  stage: build\n"
            "  image: docker:24-dind\n"
            "  services:\n"
            "    - docker:24-dind\n"
            "  variables:\n"
            "    DOCKER_TLS_CERTDIR: /certs\n"
            "    AWS_ACCESS_KEY_ID: $ASTROLIFT_AWS_ACCESS_KEY_ID\n"
            "    AWS_SECRET_ACCESS_KEY: $ASTROLIFT_AWS_SECRET_ACCESS_KEY\n"
            "    AWS_DEFAULT_REGION: $ASTROLIFT_AWS_DEFAULT_REGION\n"
            "  rules:\n"
            f"{rules_line}\n"
            "  script:\n"
            "    - apk add --no-cache aws-cli\n"
            f'    - aws ecr get-login-password | docker login --username AWS --password-stdin "{ecr_registry}"\n'
            '    - docker build -t "$ASTROLIFT_IMAGE" .\n'
            '    - docker push "$ASTROLIFT_IMAGE"\n'
            "\n"
        )
        needs_line = "  needs: [build-image]\n"
    else:
        stages_block = "stages:\n  - deploy\n"
        image_var = '  ASTROLIFT_IMAGE: "$CI_COMMIT_SHA"\n'
        build_job = ""
        needs_line = ""

    body = (
        "# Managed by Astrolift — do not edit by hand."
        " Re-sync via Settings → CI setup → Sync workflow file.\n"
        + stages_block
        + "\n"
        + "variables:\n"
        + image_var
        + "\n"
        + build_job
        + "notify-astrolift:\n"
        + "  stage: deploy\n"
        + "  image: ubuntu:24.04\n"
        + needs_line
        + "  variables:\n"
        + f"    ASTROLIFT_API_URL: {api_url_literal}\n"
        + f"    ASTROLIFT_APP_SLUG: {slug_literal}\n"
        + "  rules:\n"
        + f"{rules_line}\n"
        + "  script:\n"
        + "    - apt-get update -qq && apt-get install -y -qq curl ca-certificates\n"
        + "    - |\n"
        + "      code=$(curl -sS -o /tmp/astrolift-deploy-response.json -w '%{http_code}' -X POST \"$ASTROLIFT_API_URL/api/cli/v1/apps/$ASTROLIFT_APP_SLUG/deploy/\" \\\n"
        + '        -H "Authorization: Bearer $ASTROLIFT_DEPLOY_TOKEN" \\\n'
        + '        -H "Content-Type: application/json" \\\n'
        + '        -d "{\\"image_tags\\":{\\"*\\":\\"$CI_COMMIT_SHA\\"},'
        + '\\"commit_sha\\":\\"$CI_COMMIT_SHA\\",'
        + '\\"branch\\":\\"$CI_COMMIT_REF_NAME\\",'
        + '\\"trigger_kind\\":\\"ci\\"}")\n'
        + "      cat /tmp/astrolift-deploy-response.json; echo\n"
        + '      case "$code" in 2*) ;; *) echo "Astrolift deploy notification FAILED (HTTP $code)"; exit 1;; esac\n'
    )
    return stamp_workflow(body, version=TEMPLATE_VERSION, digest=content_hash(body))


# ---------------------------------------------------------------------------
# Source-connection picker
# ---------------------------------------------------------------------------


def _pick_source_connection(app: RegisteredApp) -> SourceConnection | None:
    """Resolve the org-level connection for ``app``'s source host.

    Writing a workflow file is a platform-side action that goes out under
    an org identity — never a viewer's personal token — but it is not a
    secrets write, so it uses ``purpose=ORG_REPO_WRITE``: the GitHub App
    is preferred, then an org-level OAuth-user, then a PAT, so an App-less
    org that connected via OAuth/PAT can still land the workflow file.
    Returns None (not raising) so the caller keeps its existing
    no-connection handling.
    """
    from astrolift_scm.services.connection_resolver import (
        ORG_REPO_WRITE,
        ConnectionResolutionError,
        resolve_connection,
    )

    try:
        return resolve_connection(
            app.organization_id,
            purpose=ORG_REPO_WRITE,
            source_kind=app.source_kind,
        )
    except ConnectionResolutionError:
        return None


# ---------------------------------------------------------------------------
# GitHub branch-protection probe + PR fallback
# ---------------------------------------------------------------------------


def _github_api_base(connection: SourceConnection) -> str:
    return (connection.api_base_url or GITHUB_API_DEFAULT).rstrip("/")


def _github_auth_header(connection: SourceConnection, token: str) -> str:
    return f"Bearer {token}" if connection.kind == "github_app_install" else f"token {token}"


def _safe_repo(repo_full_name: str) -> str:
    return "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))


def _is_github_branch_protected(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    branch: str,
) -> bool:
    """GET /repos/{owner}/{repo}/branches/{branch}/protection.

    404 → unprotected; 200 → protected; auth / other errors bubble as
    "treat as unprotected" so we don't unnecessarily push every change
    through a PR. The mutation surface still surfaces a downstream
    write failure cleanly via ``fetch_failed``.
    """
    token = _token(connection)
    base = _github_api_base(connection)
    safe_branch = urllib.parse.quote(branch, safe="")
    url = f"{base}/repos/{_safe_repo(repo_full_name)}/branches/{safe_branch}/protection"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": _github_auth_header(connection, token),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        # 401/403/etc. — fall through to "not protected" so the write
        # path attempts a direct PUT. If the write also auth-fails,
        # the operator sees that error instead of an opaque "PR
        # opened" detour.
        return False
    except urllib.error.URLError:
        return False


def _github_get_branch_sha(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    branch: str,
) -> str:
    """Return the head commit SHA of ``branch``. Raises
    ``GithubProviderError`` on any failure — caller catches and
    surfaces as ``fetch_failed``."""
    token = _token(connection)
    base = _github_api_base(connection)
    url = f"{base}/repos/{_safe_repo(repo_full_name)}/branches/{urllib.parse.quote(branch, safe='')}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": _github_auth_header(connection, token),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GithubProviderError(
            "API_ERROR",
            f"couldn't resolve {branch!r} on {repo_full_name}: {exc.code}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"couldn't reach GitHub: {exc.reason}") from exc
    sha = ((payload or {}).get("commit") or {}).get("sha")
    if not isinstance(sha, str) or not sha:
        raise GithubProviderError("UNEXPECTED_SHAPE", "GitHub /branches response missing commit.sha")
    return sha


def _github_create_ref(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    new_branch: str,
    from_sha: str,
) -> None:
    """POST /repos/{owner}/{repo}/git/refs. Idempotent on 422
    ("Reference already exists") — the same side-branch name from a
    previous attempt is fine to reuse."""
    token = _token(connection)
    base = _github_api_base(connection)
    url = f"{base}/repos/{_safe_repo(repo_full_name)}/git/refs"
    body = {"ref": f"refs/heads/{new_branch}", "sha": from_sha}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": _github_auth_header(connection, token),
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if exc.code == 422 and "already exists" in body_text.lower():
            return
        raise GithubProviderError(
            "API_ERROR",
            f"couldn't create ref {new_branch!r}: {exc.code} {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"couldn't reach GitHub: {exc.reason}") from exc


def _github_open_pull_request(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    head: str,
    base_branch: str,
    title: str,
    body: str,
) -> str:
    """POST /repos/{owner}/{repo}/pulls. Returns the PR's web URL.

    422 with "A pull request already exists" is treated as success —
    we look up the open PR for ``head:base`` and return its URL."""
    token = _token(connection)
    api_base = _github_api_base(connection)
    url = f"{api_base}/repos/{_safe_repo(repo_full_name)}/pulls"
    payload = {"title": title, "head": head, "base": base_branch, "body": body}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": _github_auth_header(connection, token),
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code == 422 and "already exists" in body_text.lower():
            return _github_find_existing_pr(
                connection,
                repo_full_name=repo_full_name,
                head=head,
                base_branch=base_branch,
            )
        raise GithubProviderError(
            "API_ERROR",
            f"couldn't open PR for {head!r}: {exc.code} {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"couldn't reach GitHub: {exc.reason}") from exc
    web_url = (data or {}).get("html_url") or ""
    if not isinstance(web_url, str) or not web_url:
        raise GithubProviderError("UNEXPECTED_SHAPE", "GitHub PR-create response missing html_url")
    return web_url


def _github_find_existing_pr(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    head: str,
    base_branch: str,
) -> str:
    """GET /repos/{owner}/{repo}/pulls?head=owner:branch&base=...
    Returns the first open PR's html_url, or empty string if none."""
    token = _token(connection)
    api_base = _github_api_base(connection)
    owner = repo_full_name.split("/", 1)[0]
    head_param = f"{owner}:{head}"
    url = (
        f"{api_base}/repos/{_safe_repo(repo_full_name)}/pulls"
        f"?state=open&head={urllib.parse.quote(head_param, safe=':')}"
        f"&base={urllib.parse.quote(base_branch, safe='')}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": _github_auth_header(connection, token),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return ""
    except urllib.error.URLError:
        return ""
    if isinstance(rows, list) and rows:
        first = rows[0] or {}
        return first.get("html_url") or ""
    return ""


# ---------------------------------------------------------------------------
# Side-branch name
# ---------------------------------------------------------------------------


def _side_branch_for(app_slug: str) -> str:
    """Deterministic side branch when the deploy branch is protected."""
    return f"astrolift/ci-workflow-{app_slug}"


# ---------------------------------------------------------------------------
# GitLab-specific helpers (#735)
# ---------------------------------------------------------------------------


def _is_gitlab_branch_protected(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    branch: str,
) -> bool:
    """GET /projects/{id}/protected_branches/{branch}.

    200 → protected; 404 → not protected; auth/network errors → treat
    as not protected so the write path runs and surfaces any real error.
    """
    token = _gitlab_token(connection)
    base = _gitlab_api_base(connection)
    project = urllib.parse.quote(repo_full_name, safe="")
    branch_q = urllib.parse.quote(branch, safe="")
    url = f"{base}/api/v4/projects/{project}/protected_branches/{branch_q}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        return False
    except urllib.error.URLError:
        return False


def _gitlab_create_branch(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    new_branch: str,
    from_branch: str,
) -> None:
    """POST /projects/{id}/repository/branches.

    Idempotent on 400 "Branch already exists".
    """
    token = _gitlab_token(connection)
    base = _gitlab_api_base(connection)
    project = urllib.parse.quote(repo_full_name, safe="")
    url = f"{base}/api/v4/projects/{project}/repository/branches"
    body = json.dumps({"branch": new_branch, "ref": from_branch}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        already = exc.code in (409, 422) or (exc.code == 400 and "already exists" in body_text.lower())
        if already:
            return
        raise GitlabProviderError(
            "API_ERROR",
            f"couldn't create branch {new_branch!r}: {exc.code} {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GitlabProviderError("NETWORK", f"couldn't reach GitLab: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# Gitea branch-protection probe
# ---------------------------------------------------------------------------


def _is_gitea_branch_protected(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    branch: str,
) -> bool:
    """GET /api/v1/repos/{owner}/{repo}/branches/{branch}.

    Returns True when the branch has ``protected: true`` in the
    Gitea response. On auth / network errors: return False so the
    write path runs and surfaces the real error."""
    import urllib.parse as _up

    try:
        token = _gitea_token(connection)
        base = _gitea_api_base(connection)
    except GiteaProviderError:
        return False

    owner, repo = repo_full_name.split("/", 1)
    url = (
        f"{base}/api/v1/repos/{_up.quote(owner, safe='')}/{_up.quote(repo, safe='')}"
        f"/branches/{_up.quote(branch, safe='')}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("protected"))
    except (urllib.error.HTTPError, urllib.error.URLError):
        return False


def _gitea_create_branch_direct(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    new_branch: str,
    from_branch: str,
) -> None:
    """POST /api/v1/repos/{owner}/{repo}/branches to create a side branch."""
    import urllib.parse as _up

    try:
        token = _gitea_token(connection)
        base = _gitea_api_base(connection)
    except GiteaProviderError as exc:
        raise GiteaProviderError(
            "AUTH_FAILED",
            f"couldn't obtain Gitea token: {exc.message}",
        ) from exc

    owner, repo = repo_full_name.split("/", 1)
    url = f"{base}/api/v1/repos/{_up.quote(owner, safe='')}/{_up.quote(repo, safe='')}/branches"
    body = json.dumps({"new_branch_name": new_branch, "old_branch_name": from_branch}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _ = resp.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        if exc.code in (409, 422) or "already exists" in body_text.lower():
            return
        raise GiteaProviderError(
            "API_ERROR",
            f"couldn't create branch {new_branch!r}: {exc.code} {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GiteaProviderError("NETWORK", f"couldn't reach Gitea: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# Per-app sync-record persistence (#1209)
# ---------------------------------------------------------------------------

# Statuses that mean "the managed workflow is reconciled onto the repo"
# (either it landed on the deploy branch, already matched, or is sitting in
# a review PR). ``fetch_failed`` is deliberately excluded — nothing landed.
_PERSISTED_SYNC_STATUSES: Final = frozenset({"created", "updated", "in_sync", "pr_opened"})


def _render_and_path(app: RegisteredApp) -> tuple[str, str]:
    """Re-derive the (stamped body, workflow path) the sync just reconciled.

    Rendering is a pure function of the app's persisted state, so this
    reproduces byte-for-byte the body ``_sync_<host>`` created/compared —
    it is used only to compute the per-app sync record's digests, never to
    push again. Mirrors the host dispatch in
    :func:`sync_workflow_file_to_repo`; only ever reached for a host that
    dispatch already accepted.
    """
    if app.source_kind == "github":
        return render_astrolift_ci_workflow(app), github_workflow_path_for(app)
    if app.source_kind == "gitlab":
        return render_astrolift_gitlab_ci_workflow(app), GITLAB_WORKFLOW_PATH
    if app.source_kind == "bitbucket":
        return render_astrolift_bitbucket_pipeline(app), BITBUCKET_WORKFLOW_PATH
    return render_astrolift_gitea_ci_workflow(app), GITEA_WORKFLOW_PATH


def _persist_ci_workflow_stamp(app: RegisteredApp, result: WorkflowSyncResult) -> None:
    """Record which template version + content landed for ``app`` (#1209).

    DB-only bookkeeping run after a successful reconcile: it captures the
    ``TEMPLATE_VERSION`` we synced, the content hash (stamp removed) and the
    git blob SHA (full stamped file) so a later phase can spot template
    staleness or repo drift WITHOUT re-fetching the file. It never touches
    the repo and does not change anything ``_sync_<host>`` pushed.
    """
    body, path = _render_and_path(app)
    state: dict = {
        "synced_hash": content_hash(body),
        "synced_blob_sha": git_blob_sha(body.encode("utf-8")),
        "synced_at": timezone.now().isoformat(),
        "path": path,
        "state": "in_sync",
    }
    # A direct commit landed a base-branch SHA; in_sync / pr_opened did not.
    if result.commit_sha:
        state["last_commit_sha"] = result.commit_sha
    # Protected-branch flow parked the change in a review PR/MR.
    if result.pr_url:
        state["pr_url"] = result.pr_url

    app.ci_workflow_template_version = TEMPLATE_VERSION
    app.ci_workflow_state = state
    app.save(
        update_fields=[
            "ci_workflow_template_version",
            "ci_workflow_state",
            "updated_at",
            "version",
        ]
    )


# ---------------------------------------------------------------------------
# Top-level sync
# ---------------------------------------------------------------------------


def github_repo_numeric_ids(app: RegisteredApp) -> tuple[int, int] | None:
    """Best-effort ``(owner_id, repo_id)`` for the app's source repo.

    Feeds the ID-stamped OIDC ``sub`` pattern that newly created GitHub
    repos present (#1532). None when the lookup can't run (non-github
    source, no connection, host error) — the push role then trusts the
    login-based subject only, which keeps pre-stamping repos working.
    """
    if app.source_kind != "github" or not (app.source_repo or "").strip():
        return None
    conn = _pick_source_connection(app)
    if conn is None:
        return None
    from astrolift_scm.providers.github import fetch_github_repo_numeric_ids

    try:
        return fetch_github_repo_numeric_ids(conn, repo_full_name=app.source_repo)
    except Exception:  # noqa: BLE001 — degrade to the classic pattern, never block provisioning
        logger.warning("push-role ensure: numeric-id lookup failed for %s", app.slug, exc_info=True)
        return None


def ensure_ci_push_role(app: RegisteredApp) -> str:
    """Provision (or reuse) the OIDC CI push role and persist its ARN to
    ``app.push_role_ref`` so the rendered GitHub workflow's
    ``aws-actions/configure-aws-credentials`` step assumes a real role.
    Without this the field is blank and OIDC fails with "Could not load
    credentials from any providers".

    Lives here (not in autowire) so EVERY path that pushes the workflow
    file — Settings "Sync workflow file", autowire, drift repair — heals a
    blank ``push_role_ref`` before rendering (#1219). Idempotent
    (``ensure_ci_push_role`` on the driver reuses the role).

    Returns a non-empty error string only on a genuine provisioning
    FAILURE — a no-op ("") when the app has no bound cluster, no source
    repo, or the registry driver doesn't support push roles (those are
    "unwired", not failures).
    """
    cluster = app.default_tenant_cluster
    if cluster is None or not (app.source_repo or "").strip():
        return ""
    try:
        from core.app_deploy import driver_for_capability

        driver = driver_for_capability(cluster, "registry")
    except Exception as exc:  # noqa: BLE001 — no registry driver here: leave as-is
        logger.warning("push-role ensure: registry driver unavailable for %s: %s", app.slug, exc)
        return ""
    if not hasattr(driver, "ensure_ci_push_role"):
        return ""
    try:
        push_role = driver.ensure_ci_push_role(
            repo=f"{app.organization.slug}/{app.slug}",
            scm_provider=app.source_kind,
            scm_repo_full_name=app.source_repo,
            scm_repo_numeric_ids=github_repo_numeric_ids(app),
        )
    except Exception as exc:  # noqa: BLE001 — surface, don't swallow (the whole point)
        logger.exception("push-role ensure: ensure_ci_push_role failed for %s", app.slug)
        return str(exc) or exc.__class__.__name__
    ref = (getattr(push_role, "role_ref", "") or "").strip()
    if not ref:
        # A driver that supports push roles but returned nothing is a
        # failure, not an unwired no-op — rendering would emit a blank
        # role-to-assume, which is exactly the #1219 breakage.
        return "registry driver returned an empty push-role ref"
    if ref != (app.push_role_ref or ""):
        app.push_role_ref = ref
        app.save(update_fields=["push_role_ref", "updated_at", "version"])
    return ""


def sync_workflow_file_to_repo(
    app: RegisteredApp,
    viewer_user=None,  # noqa: ARG001 — accepted for API symmetry; org-level conn picker is used
    *,
    force_pr: bool = False,
) -> WorkflowSyncResult:
    """Render the CI workflow for ``app`` and reconcile it onto the
    deploy branch of the configured source repo.

    See module docstring for the protocol. ``viewer_user`` is accepted
    for parity with the resolver signature but is not consulted —
    selection runs against the app's organization, mirroring #387.

    ``force_pr`` routes the change through the side-branch PR/MR flow even
    when the deploy branch is unprotected — the reconcile-PR path (#1212):
    a drifted (hand-edited) file must never be overwritten in place, so the
    operator reviews the template overwrite in a PR and merges deliberately.
    Bitbucket has no side-branch PR flow wired, so ``force_pr`` raises there
    rather than silently direct-writing over the operator's edits.

    After a successful reconcile (created / updated / in_sync / pr_opened)
    the per-app sync record is persisted (#1209) so the platform knows which
    ``TEMPLATE_VERSION`` and content landed. This is pure bookkeeping — the
    push/PR behaviour above is unchanged.
    """
    if not app.source_repo:
        raise WorkflowSyncError(
            "NO_SOURCE_REPO",
            "app has no source repo configured; cannot push the CI workflow",
        )
    is_agent = _is_agent_app(app)
    if is_agent and app.source_kind != "github":
        raise WorkflowSyncError(
            "AGENT_CI_UNSUPPORTED",
            "managed agent-package validation is currently supported only for GitHub; "
            f"source_kind={app.source_kind!r} was left unchanged",
        )
    if app.source_kind == "github":
        # The GitHub template authenticates via OIDC role assumption, so a
        # blank ``push_role_ref`` renders a workflow that can never work.
        # Heal it here — every push path funnels through this function
        # (#1219) — and refuse to push when provisioning genuinely failed.
        if not is_agent:
            role_err = ensure_ci_push_role(app)
            if role_err:
                raise WorkflowSyncError(
                    "PUSH_ROLE_PROVISION_FAILED",
                    f"couldn't provision the CI push role: {role_err}",
                )
        result = _sync_github(app, force_pr=force_pr)
    elif app.source_kind == "gitlab":
        result = _sync_gitlab(app, force_pr=force_pr)
    elif app.source_kind == "bitbucket":
        result = _sync_bitbucket(app, force_pr=force_pr)
    elif app.source_kind == "gitea":
        result = _sync_gitea(app, force_pr=force_pr)
    else:
        raise WorkflowSyncError(
            "UNSUPPORTED_SOURCE",
            f"CI workflow push is not yet supported for source_kind={app.source_kind!r}; "
            "supported hosts: github, gitlab, bitbucket, gitea.",
        )

    if result.status in _PERSISTED_SYNC_STATUSES:
        _persist_ci_workflow_stamp(app, result)
    return result


def _has_operator_edits(app: RegisteredApp, existing: str | None) -> bool:
    """Would writing the rendered template over ``existing`` lose someone's edits?

    The destructive default this exists to close: every push entry point --
    ``pushAstroliftCiWorkflowToRepo``, ``pushCiWorkflow``,
    ``resyncAstroliftCiWorkflow`` and the fleet sweep -- lands here, and on an
    unprotected deploy branch this function used to PUT straight over whatever
    the repo had. An operator who edited the managed file lost the edit with no
    warning and no diff, which is exactly the drift the platform can already
    detect.

    So classify first, from the text the caller has ALREADY fetched for its
    equality check (pure function, no second round trip), and let a
    hand-edited file take the side-branch PR route instead. ``absent`` and
    ``template_stale`` are the two states with nothing to lose -- no file, or
    a file the platform itself last wrote -- and they still write directly;
    they are the same two the fleet sweep treats as safe to auto-push.

    Returns False when the state cannot be determined from the persisted
    record. An unknown state routes via PR at the call site, not here.
    """
    # No file on the branch means nothing to lose. Checked before the record
    # is read so a create never depends on the sync record existing at all.
    if existing is None:
        return False

    from astrolift_scm.services.ci_workflow_drift import (
        SyncState,
        _persisted_state_with_version,
        compute_sync_state,
    )

    state = compute_sync_state(
        repo_file_text=existing,
        persisted_state=_persisted_state_with_version(app),
        current_template_version=TEMPLATE_VERSION,
    )
    return state not in (SyncState.ABSENT, SyncState.TEMPLATE_STALE, SyncState.IN_SYNC)


def _sync_github(app: RegisteredApp, *, force_pr: bool = False) -> WorkflowSyncResult:
    connection = _pick_source_connection(app)
    if connection is None:
        raise WorkflowSyncError(
            "NO_CONNECTION",
            "no active source connection for this app's org. "
            "Connect a GitHub identity under Settings → Source connections, then retry.",
        )

    rendered = render_astrolift_ci_workflow(app)
    rendered_size = len(rendered.encode("utf-8"))
    deploy_branch = (app.deploy_branch or "main").strip() or "main"
    workflow_path = github_workflow_path_for(app)

    try:
        existing = fetch_file(
            connection,
            repo_full_name=app.source_repo,
            path=workflow_path,
            ref=deploy_branch,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    if existing is not None and existing == rendered:
        return WorkflowSyncResult(status="in_sync", rendered_size=rendered_size)

    # ``force_pr`` (reconcile) short-circuits the protection probe: the change
    # always goes through a reviewable side-branch PR so the operator's edits
    # aren't clobbered in place. A file carrying operator edits takes the same
    # route without being asked -- see _has_operator_edits.
    route_via_pr = (
        force_pr
        or _has_operator_edits(app, existing)
        or _is_github_branch_protected(
            connection,
            repo_full_name=app.source_repo,
            branch=deploy_branch,
        )
    )

    commit_message = (
        f"chore(astrolift): sync CI workflow for {app.slug}"
        if existing is None
        else f"chore(astrolift): update CI workflow for {app.slug}"
    )

    if route_via_pr:
        side_branch = _side_branch_for(app.slug)
        try:
            head_sha = _github_get_branch_sha(
                connection,
                repo_full_name=app.source_repo,
                branch=deploy_branch,
            )
            _github_create_ref(
                connection,
                repo_full_name=app.source_repo,
                new_branch=side_branch,
                from_sha=head_sha,
            )
            put_file(
                connection,
                repo_full_name=app.source_repo,
                path=workflow_path,
                branch=side_branch,
                content=rendered,
                commit_message=commit_message,
                operation=CI_WORKFLOW_WRITE_OPERATION,
                permission=CI_WORKFLOW_WRITE_PERMISSION,
            )
            pr_url = _github_open_pull_request(
                connection,
                repo_full_name=app.source_repo,
                head=side_branch,
                base_branch=deploy_branch,
                title=f"Astrolift: sync CI workflow for {app.slug}",
                body=(
                    "This PR was opened by Astrolift to keep "
                    f"`{workflow_path}` in sync with the platform's "
                    f"current settings for **{app.slug}**.\n\n"
                    "Merge to enable platform-driven deploys against "
                    f"`{deploy_branch}`."
                ),
            )
        except GithubProviderError as exc:
            return WorkflowSyncResult(
                status="fetch_failed",
                rendered_size=rendered_size,
                error=f"{exc.code}: {exc.message}",
            )
        except ProviderError as exc:
            return WorkflowSyncResult(
                status="fetch_failed",
                rendered_size=rendered_size,
                error=f"{exc.code}: {exc.message}",
            )
        return WorkflowSyncResult(status="pr_opened", pr_url=pr_url, rendered_size=rendered_size)

    try:
        put_result = put_file(
            connection,
            repo_full_name=app.source_repo,
            path=workflow_path,
            branch=deploy_branch,
            content=rendered,
            commit_message=commit_message,
            operation=CI_WORKFLOW_WRITE_OPERATION,
            permission=CI_WORKFLOW_WRITE_PERMISSION,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    return WorkflowSyncResult(
        status="created" if existing is None else "updated",
        commit_sha=put_result.commit_sha,
        rendered_size=rendered_size,
    )


def _sync_gitlab(app: RegisteredApp, *, force_pr: bool = False) -> WorkflowSyncResult:
    """GitLab equivalent of ``_sync_github`` (#735).

    Probes ``/protected_branches`` instead of GitHub's branch-protection
    endpoint; opens an MR (via the providers layer) instead of a PR.
    """
    connection = _pick_source_connection(app)
    if connection is None:
        raise WorkflowSyncError(
            "NO_CONNECTION",
            "no active source connection for this app's org. "
            "Connect a GitLab identity under Settings → Source connections, then retry.",
        )

    rendered = render_astrolift_gitlab_ci_workflow(app)
    rendered_size = len(rendered.encode("utf-8"))
    deploy_branch = (app.deploy_branch or "main").strip() or "main"

    try:
        existing = fetch_file(
            connection,
            repo_full_name=app.source_repo,
            path=GITLAB_WORKFLOW_PATH,
            ref=deploy_branch,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    if existing is not None and existing == rendered:
        return WorkflowSyncResult(status="in_sync", rendered_size=rendered_size)

    # ``force_pr`` (reconcile) short-circuits the protection probe — see
    # ``_sync_github``. Operator edits route via MR/PR the same way.
    route_via_pr = (
        force_pr
        or _has_operator_edits(app, existing)
        or _is_gitlab_branch_protected(
            connection,
            repo_full_name=app.source_repo,
            branch=deploy_branch,
        )
    )

    commit_message = (
        f"chore(astrolift): sync CI workflow for {app.slug}"
        if existing is None
        else f"chore(astrolift): update CI workflow for {app.slug}"
    )

    if route_via_pr:
        side_branch = _side_branch_for(app.slug)
        try:
            _gitlab_create_branch(
                connection,
                repo_full_name=app.source_repo,
                new_branch=side_branch,
                from_branch=deploy_branch,
            )
            put_file(
                connection,
                repo_full_name=app.source_repo,
                path=GITLAB_WORKFLOW_PATH,
                branch=side_branch,
                content=rendered,
                commit_message=commit_message,
            )
            mr_result = open_pull_request(
                connection,
                repo_full_name=app.source_repo,
                head_branch=side_branch,
                base_branch=deploy_branch,
                title=f"Astrolift: sync CI workflow for {app.slug}",
                body=(
                    "This MR was opened by Astrolift to keep "
                    f"`{GITLAB_WORKFLOW_PATH}` in sync with the platform's "
                    f"current settings for **{app.slug}**.\n\n"
                    "Merge to enable platform-driven deploys against "
                    f"`{deploy_branch}`."
                ),
            )
        except GitlabProviderError as exc:
            return WorkflowSyncResult(
                status="fetch_failed",
                rendered_size=rendered_size,
                error=f"{exc.code}: {exc.message}",
            )
        except ProviderError as exc:
            return WorkflowSyncResult(
                status="fetch_failed",
                rendered_size=rendered_size,
                error=f"{exc.code}: {exc.message}",
            )
        return WorkflowSyncResult(
            status="pr_opened",
            pr_url=mr_result.url,
            rendered_size=rendered_size,
        )

    try:
        put_result = put_file(
            connection,
            repo_full_name=app.source_repo,
            path=GITLAB_WORKFLOW_PATH,
            branch=deploy_branch,
            content=rendered,
            commit_message=commit_message,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    return WorkflowSyncResult(
        status="created" if existing is None else "updated",
        commit_sha=put_result.commit_sha,
        rendered_size=rendered_size,
    )


def _sync_bitbucket(app: RegisteredApp, *, force_pr: bool = False) -> WorkflowSyncResult:
    """Bitbucket equivalent of ``_sync_github``.

    Bitbucket's branch-restriction API is more complex than GitHub's /
    GitLab's protection endpoint and requires org-admin permissions to
    read — we skip the protection probe and write directly. If the write
    fails because of branch restrictions the caller receives a
    ``fetch_failed`` result with the provider error code; the operator
    can then use the "Open PR" affordance on the UI.
    """
    if force_pr:
        # Bitbucket has no side-branch PR flow wired here (unlike github /
        # gitlab / gitea), so a reconcile can't route through a review PR.
        # Refuse rather than direct-write over the operator's hand-edits.
        raise WorkflowSyncError(
            "UNSUPPORTED_RECONCILE",
            "opening a reconcile PR isn't supported for Bitbucket yet — no "
            "side-branch PR flow is wired. Resolve the drift by editing the "
            "file directly or adopting the repo copy as the baseline.",
        )
    connection = _pick_source_connection(app)
    if connection is None:
        raise WorkflowSyncError(
            "NO_CONNECTION",
            "no active source connection for this app's org. "
            "Connect a Bitbucket identity under Settings → Source connections, then retry.",
        )

    rendered = render_astrolift_bitbucket_pipeline(app)
    rendered_size = len(rendered.encode("utf-8"))
    deploy_branch = (app.deploy_branch or "main").strip() or "main"

    try:
        existing = fetch_file(
            connection,
            repo_full_name=app.source_repo,
            path=BITBUCKET_WORKFLOW_PATH,
            ref=deploy_branch,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    if existing is not None and existing == rendered:
        return WorkflowSyncResult(status="in_sync", rendered_size=rendered_size)

    # No side-branch PR flow here, so there is no safe route for a file
    # carrying operator edits: refuse instead of overwriting it. The operator
    # pulls the repo copy (making it the baseline) or edits the file, then
    # pushes.
    if _has_operator_edits(app, existing):
        raise WorkflowSyncError(
            "WOULD_OVERWRITE_EDITS",
            "the workflow file in this Bitbucket repo has been edited away "
            "from what Astrolift last wrote, and Bitbucket has no side-branch "
            "PR flow wired to review a replacement. Pull the repo's copy to "
            "keep it, or edit the file in the repo, then push.",
        )

    commit_message = (
        f"chore(astrolift): sync CI workflow for {app.slug}"
        if existing is None
        else f"chore(astrolift): update CI workflow for {app.slug}"
    )

    try:
        put_result = put_file(
            connection,
            repo_full_name=app.source_repo,
            path=BITBUCKET_WORKFLOW_PATH,
            branch=deploy_branch,
            content=rendered,
            commit_message=commit_message,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    return WorkflowSyncResult(
        status="created" if existing is None else "updated",
        commit_sha=put_result.commit_sha,
        rendered_size=rendered_size,
    )


def _sync_gitea(app: RegisteredApp, *, force_pr: bool = False) -> WorkflowSyncResult:
    """Gitea equivalent of ``_sync_github``.

    Probes ``/api/v1/repos/{owner}/{repo}/branches/{branch}`` for the
    ``protected`` flag; opens a PR (via the providers layer) when the
    deploy branch is protected.
    """
    connection = _pick_source_connection(app)
    if connection is None:
        raise WorkflowSyncError(
            "NO_CONNECTION",
            "no active source connection for this app's org. "
            "Connect a Gitea identity under Settings → Source connections, then retry.",
        )

    rendered = render_astrolift_gitea_ci_workflow(app)
    rendered_size = len(rendered.encode("utf-8"))
    deploy_branch = (app.deploy_branch or "main").strip() or "main"

    try:
        existing = fetch_file(
            connection,
            repo_full_name=app.source_repo,
            path=GITEA_WORKFLOW_PATH,
            ref=deploy_branch,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    if existing is not None and existing == rendered:
        return WorkflowSyncResult(status="in_sync", rendered_size=rendered_size)

    # ``force_pr`` (reconcile) short-circuits the protection probe — see
    # ``_sync_github``. Operator edits route via MR/PR the same way.
    route_via_pr = (
        force_pr
        or _has_operator_edits(app, existing)
        or _is_gitea_branch_protected(
            connection,
            repo_full_name=app.source_repo,
            branch=deploy_branch,
        )
    )

    commit_message = (
        f"chore(astrolift): sync CI workflow for {app.slug}"
        if existing is None
        else f"chore(astrolift): update CI workflow for {app.slug}"
    )

    if route_via_pr:
        side_branch = _side_branch_for(app.slug)
        try:
            _gitea_create_branch_direct(
                connection,
                repo_full_name=app.source_repo,
                new_branch=side_branch,
                from_branch=deploy_branch,
            )
            put_file(
                connection,
                repo_full_name=app.source_repo,
                path=GITEA_WORKFLOW_PATH,
                branch=side_branch,
                content=rendered,
                commit_message=commit_message,
            )
            pr_result = open_pull_request(
                connection,
                repo_full_name=app.source_repo,
                head_branch=side_branch,
                base_branch=deploy_branch,
                title=f"Astrolift: sync CI workflow for {app.slug}",
                body=(
                    "This PR was opened by Astrolift to keep "
                    f"`{GITEA_WORKFLOW_PATH}` in sync with the platform's "
                    f"current settings for **{app.slug}**.\n\n"
                    "Merge to enable platform-driven deploys against "
                    f"`{deploy_branch}`."
                ),
            )
        except GiteaProviderError as exc:
            return WorkflowSyncResult(
                status="fetch_failed",
                rendered_size=rendered_size,
                error=f"{exc.code}: {exc.message}",
            )
        except ProviderError as exc:
            return WorkflowSyncResult(
                status="fetch_failed",
                rendered_size=rendered_size,
                error=f"{exc.code}: {exc.message}",
            )
        return WorkflowSyncResult(
            status="pr_opened",
            pr_url=pr_result.url,
            rendered_size=rendered_size,
        )

    try:
        put_result = put_file(
            connection,
            repo_full_name=app.source_repo,
            path=GITEA_WORKFLOW_PATH,
            branch=deploy_branch,
            content=rendered,
            commit_message=commit_message,
        )
    except ProviderError as exc:
        return WorkflowSyncResult(
            status="fetch_failed",
            rendered_size=rendered_size,
            error=f"{exc.code}: {exc.message}",
        )

    return WorkflowSyncResult(
        status="created" if existing is None else "updated",
        commit_sha=put_result.commit_sha,
        rendered_size=rendered_size,
    )
