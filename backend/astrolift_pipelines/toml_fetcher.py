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
    ref = pipeline_run.trigger_ref or pipeline.default_branch
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

    org = pipeline.organization

    # Look up the GitHub SourceConnection for this org/repo
    token = _get_github_token(org, pipeline.repo_url)
    if not token:
        raise TomlFetchError(
            f"No GitHub credentials configured for repo {pipeline.repo_url!r}. "
            "Add a source connection via the SCM integration settings."
        )

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

    org = pipeline.organization
    token = _get_gitlab_token(org, pipeline.repo_url)
    if not token:
        raise TomlFetchError(f"No GitLab credentials configured for repo {pipeline.repo_url!r}.")

    project_path = _extract_project_path_gitlab(pipeline.repo_url)
    encoded_path = urllib.parse.quote(path, safe="")
    encoded_project = urllib.parse.quote(project_path, safe="")

    gitlab_host = _extract_gitlab_host(pipeline.repo_url)
    api_url = f"https://{gitlab_host}/api/v4/projects/{encoded_project}/repository/files/{encoded_path}/raw?ref={ref}"

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


def _get_github_token(org, repo_url: str) -> str | None:
    """Look up a GitHub access token from the org's source connections."""
    try:
        from astrolift_scm.models import SourceConnection

        conn = SourceConnection.objects.filter(
            organization=org,
            kind="github",
            deleted_at__isnull=True,
        ).first()
        if conn and conn.access_token_ciphertext:
            return _decrypt_token(conn.access_token_ciphertext)
    except Exception:  # noqa: BLE001
        pass
    return None


def _get_gitlab_token(org, repo_url: str) -> str | None:
    """Look up a GitLab access token from the org's source connections."""
    try:
        from astrolift_scm.models import SourceConnection

        conn = SourceConnection.objects.filter(
            organization=org,
            kind__in=["gitlab", "gitea"],
            deleted_at__isnull=True,
        ).first()
        if conn and conn.access_token_ciphertext:
            return _decrypt_token(conn.access_token_ciphertext)
    except Exception:  # noqa: BLE001
        pass
    return None


def _decrypt_token(ciphertext: str) -> str | None:
    """Decrypt an access token ciphertext using the platform's key management."""
    try:
        from core.encryption import decrypt

        return decrypt(ciphertext)
    except Exception:  # noqa: BLE001
        return None


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


def _extract_gitlab_host(url: str) -> str:
    """Extract the GitLab host from a repo URL."""
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            return url[len(prefix) :].split("/")[0]
    if "gitlab.com" in url:
        return "gitlab.com"
    return "gitlab.com"
