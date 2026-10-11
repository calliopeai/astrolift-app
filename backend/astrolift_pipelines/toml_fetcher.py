"""TOML fetch from repo at trigger time — repo credentials, validation (#105).

When a pipeline is triggered by a push event, the pipeline TOML must be
fetched from the source repo at the commit SHA that triggered the run.
This ensures the pipeline definition matches the code being tested.

Fetch path:
1. Look up the org's SourceConnection for the repo (reuse astrolift_scm)
2. Use the connection's credentials to authenticate the fetch
3. Fetch the TOML at the trigger ref/SHA
4. Parse and validate the TOML (import the DSL parser from astrolift_ci_convert)
5. Return the PipelineDef for the run

If the TOML can't be fetched (no credentials, 404, parse error), the run
is marked FAILED with a descriptive error.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrolift_pipelines.models import Pipeline, PipelineRun

logger = logging.getLogger(__name__)


class TomlFetchError(Exception):
    """Raised when the pipeline TOML cannot be fetched or parsed."""


def fetch_pipeline_toml(pipeline_run: PipelineRun) -> str:
    """Fetch the pipeline TOML from the source repo at the trigger ref.

    Returns the raw TOML string. Raises TomlFetchError on failure.
    """
    pipeline = pipeline_run.pipeline
    # The commit first, the branch name only as a fallback. A ref is a name
    # that moves: fetching "main" resolves to whatever main points at when
    # the fetch happens, which is not necessarily the code that triggered
    # the run.
    ref = pipeline_run.commit_sha or pipeline_run.trigger_ref or pipeline.default_branch
    toml_path = pipeline.toml_path or f".astrolift/pipelines/{pipeline.name}.toml"

    # Detect provider from repo URL
    repo_url = pipeline.repo_url
    if "github.com" in repo_url:
        return _fetch_from_github(pipeline, ref, toml_path)
    elif "gitlab.com" in repo_url or "gitlab" in repo_url:
        return _fetch_from_gitlab(pipeline, ref, toml_path)
    else:
        return _fetch_generic_git(pipeline, ref, toml_path)


def _fetch_from_github(pipeline: Pipeline, ref: str, path: str) -> str:
    """Fetch TOML from a GitHub repository using the org's source connection."""
    import base64
    import json

    connection = _org_connection(
        pipeline.organization, source_kind="github", repo=_extract_owner_repo_github(pipeline.repo_url)
    )
    token = _connection_token(connection, source_kind="github")

    # Extract owner/repo from URL
    owner_repo = _extract_owner_repo_github(pipeline.repo_url)

    import urllib.request

    api_url = f"https://api.github.com/repos/{owner_repo}/contents/{path}?ref={ref}"
    req = urllib.request.Request(
        api_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8")
        raise TomlFetchError(f"Unexpected encoding {data.get('encoding')!r} from GitHub API")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise TomlFetchError(
                f"Pipeline TOML not found at {path!r} in {owner_repo!r} at ref {ref!r}. "
                "Ensure the file exists and the pipeline's toml_path is correct."
            ) from exc
        raise TomlFetchError(f"GitHub API error {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise TomlFetchError(f"Failed to fetch TOML from GitHub: {exc}") from exc


def _fetch_from_gitlab(pipeline: Pipeline, ref: str, path: str) -> str:
    """Fetch TOML from a GitLab repository."""
    import urllib.parse
    import urllib.request

    connection = _org_connection(pipeline.organization, source_kind="gitlab")
    token = _connection_token(connection, source_kind="gitlab")

    project_path = _extract_project_path_gitlab(pipeline.repo_url)
    encoded_path = urllib.parse.quote(path, safe="")
    encoded_project = urllib.parse.quote(project_path, safe="")

    # The connection's configured API base, not a host guessed from the repo
    # URL. A self-hosted GitLab can serve its API somewhere other than
    # https://<repo-host>/api/v4, and the connection is the only thing that
    # knows where.
    from astrolift_scm.providers.gitlab import _api_base

    base = _api_base(connection).rstrip("/")
    api_url = f"{base}/api/v4/projects/{encoded_project}/repository/files/{encoded_path}/raw?ref={ref}"

    req = urllib.request.Request(
        api_url,
        headers={"PRIVATE-TOKEN": token},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception as exc:
        raise TomlFetchError(f"Failed to fetch TOML from GitLab: {exc}") from exc


def _fetch_generic_git(pipeline: Pipeline, ref: str, path: str) -> str:
    """Fetch TOML via git archive (for generic Git hosts)."""
    # For v1, fall back to the stored toml_path on the Pipeline record.
    # Real fetch from arbitrary Git hosts requires credential storage per-host.
    raise TomlFetchError(
        f"Generic Git TOML fetch not implemented for {pipeline.repo_url!r}. "
        "Use GitHub or GitLab for automatic TOML fetch at trigger time."
    )


def _org_connection(org, *, source_kind: str, repo: str | None = None):
    """The SourceConnection that authenticates a manifest read for ``org``.

    Replaces two hand-rolled lookups that could never succeed (#1608).
    Both read ``conn.access_token_ciphertext``, a column SourceConnection
    has never had, and decrypted it through ``core.encryption``, a module
    that does not exist. Both were wrapped in a bare ``except``, so a
    private-repo fetch reported "no credentials configured" rather than
    failing, and the two real defects stayed invisible.

    ``connection_resolver`` already owns the question the hand-rolled
    version answered with ``.first()``: which connection wins when an org
    has several. It ranks App-install over OAuth-user over PAT per host,
    and names manifest read as one of ORG_REPO_WRITE's own purposes.
    """
    from astrolift_scm.services.connection_resolver import (
        ORG_REPO_WRITE,
        ConnectionResolutionError,
        resolve_connection,
    )

    try:
        return resolve_connection(org, purpose=ORG_REPO_WRITE, source_kind=source_kind, repo=repo)
    except ConnectionResolutionError as exc:
        raise TomlFetchError(exc.message) from exc


def _connection_token(connection, *, source_kind: str) -> str:
    """The bearer credential for ``connection``.

    Delegates to the provider driver, which is the only code that knows
    how each connection kind stores its secret -- a GitHub App install
    mints a short-lived installation token, a PAT is decrypted from the
    envelope. ``astrolift_pipelines.commit_status`` reaches for the same
    accessors for the same reason.
    """
    if source_kind == "github":
        from astrolift_scm.providers.github import _token as github_token

        accessor, label = github_token, "GitHub"
    elif source_kind == "gitlab":
        from astrolift_scm.providers.gitlab import _token as gitlab_token

        accessor, label = gitlab_token, "GitLab"
    else:  # pragma: no cover - the caller picks the kind, not a user
        raise TomlFetchError(f"no TOML fetch driver for source kind {source_kind!r}")

    try:
        token = accessor(connection)
    except Exception as exc:  # noqa: BLE001 - provider errors differ per host
        raise TomlFetchError(
            f"The {label} connection for this organization cannot produce a " f"credential: {exc}"
        ) from exc
    if not token:
        raise TomlFetchError(f"The {label} connection for this organization has no usable credential.")
    return token


def _extract_owner_repo_github(url: str) -> str:
    """Extract owner/repo from a GitHub URL."""
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.split("/")
    return "/".join(parts[-2:])


def _extract_project_path_gitlab(url: str) -> str:
    """Extract the project path from a GitLab URL."""
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # Remove scheme and host
    for prefix in ("https://", "http://", "git@"):
        if url.startswith(prefix):
            url = url[len(prefix) :]
            break
    parts = url.split("/", 1)
    return parts[1] if len(parts) > 1 else url
