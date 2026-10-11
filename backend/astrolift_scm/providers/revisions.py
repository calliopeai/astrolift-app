"""Resolve a source ref to an immutable commit through stored SCM credentials."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from cryptography.fernet import InvalidToken

from astrolift_scm.providers import (
    _BITBUCKET_KINDS,
    _GITEA_KINDS,
    _GITHUB_KINDS,
    _GITLAB_KINDS,
    ProviderError,
    bitbucket,
    gitea,
    github,
    gitlab,
)


def is_resolved_commit_sha(value: object) -> bool:
    """Whether a source host's value is a full immutable SHA-1 or SHA-256 commit."""
    return isinstance(value, str) and re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value) is not None


def fetch_commit(connection, *, repo_full_name: str, ref: str) -> str:
    """Resolve a branch, tag, or commit; never substitute a mutable fallback."""
    if not repo_full_name.strip() or not ref.strip():
        raise ProviderError("VALIDATION", "A source repository and ref are required")
    safe_repo = "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/"))
    safe_ref = urllib.parse.quote(ref, safe="")
    headers = {"Accept": "application/json", "User-Agent": "astrolift"}
    sha_field = "sha"
    try:
        if connection.kind in _GITHUB_KINDS:
            scheme = "Bearer" if connection.kind == "github_app_install" else "token"
            headers.update(
                {
                    "Authorization": f"{scheme} {github._token(connection)}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )
            url = f"{github._api_base(connection)}/repos/{safe_repo}/commits/{safe_ref}"
        elif connection.kind in _GITLAB_KINDS:
            project = urllib.parse.quote(repo_full_name, safe="")
            headers["Authorization"] = f"Bearer {gitlab._token(connection)}"
            url = f"{gitlab._api_base(connection)}/api/v4/projects/{project}/repository/commits/{safe_ref}"
            sha_field = "id"
        elif connection.kind in _BITBUCKET_KINDS:
            headers.update(bitbucket._headers(connection))
            url = f"{bitbucket._BASE}/2.0/repositories/{safe_repo}/commit/{safe_ref}"
            sha_field = "hash"
        elif connection.kind in _GITEA_KINDS:
            headers.update(gitea._headers(gitea._token(connection)))
            url = f"{gitea._api_base(connection)}/api/v1/repos/{safe_repo}/git/commits/{safe_ref}"
        else:
            raise ProviderError(
                "UNSUPPORTED", f"Source revision resolution is unavailable for {connection.kind!r}"
            )
    except (
        github.GithubProviderError,
        gitlab.GitlabProviderError,
        bitbucket.BitbucketProviderError,
        gitea.GiteaProviderError,
    ) as exc:
        raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc
    except (InvalidToken, UnicodeDecodeError, RuntimeError) as exc:
        raise ProviderError(
            "CREDENTIAL_UNAVAILABLE",
            "The stored source credential could not be read; check the connection",
            recoverable=True,
        ) from exc

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or (connection.kind in _GITHUB_KINDS and github._is_rate_limited(exc)):
            raise ProviderError(
                "RATE_LIMITED", "Source host rate limit reached; retry later", recoverable=True
            ) from exc
        if exc.code in (401, 403):
            raise ProviderError(
                "AUTH_FAILED", "Source host rejected the credential; reconnect or rotate it", recoverable=True
            ) from exc
        if exc.code == 404:
            raise ProviderError(
                "NOT_FOUND", f"Source ref {ref!r} was not found or is not accessible"
            ) from exc
        raise ProviderError(
            "API_ERROR", f"Source host returned {exc.code} while resolving the revision"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProviderError(
            "NETWORK", "Could not reach the source host to resolve the revision", recoverable=True
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("UNEXPECTED_SHAPE", "Source host returned an invalid commit response") from exc

    sha = payload.get(sha_field) if isinstance(payload, dict) else None
    if not is_resolved_commit_sha(sha):
        raise ProviderError("UNEXPECTED_SHAPE", "Source host did not return a full commit SHA")
    return sha.lower()
