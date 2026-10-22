"""Read a pull request's head commit and its check runs from GitHub (#2489).

The ``github_checks`` workflow gate reads these through the run
organization's connection, so the PAT, OAuth-user and App-installation
credential kinds all authenticate it. Every request is bounded, and the
check-run listing is paginated up to a fixed page count.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

import jwt
from cryptography.fernet import InvalidToken

from astrolift_scm.providers import _GITHUB_KINDS, ProviderError, github
from astrolift_scm.providers.revisions import is_resolved_commit_sha

_TIMEOUT_SECONDS = 10
_PAGE_SIZE = 100
MAX_CHECK_RUN_PAGES = 10


def _headers(connection) -> dict[str, str]:
    if connection.kind not in _GITHUB_KINDS:
        raise ProviderError(
            "UNSUPPORTED", f"GitHub checks cannot be read through a {connection.kind!r} connection"
        )
    try:
        token = github._token(connection)
    except github.GithubProviderError as exc:
        raise ProviderError(exc.code, exc.message, recoverable=exc.recoverable) from exc
    except (InvalidToken, UnicodeDecodeError, RuntimeError, jwt.PyJWTError) as exc:
        raise ProviderError(
            "CREDENTIAL_UNAVAILABLE",
            "The stored GitHub credential could not be read; check the connection",
            recoverable=True,
        ) from exc
    scheme = "Bearer" if connection.kind == "github_app_install" else "token"
    return {
        "Authorization": f"{scheme} {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "astrolift",
    }


def _get(connection, headers: dict[str, str], path: str, *, what: str) -> object:
    request = urllib.request.Request(f"{github._api_base(connection)}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or github._is_rate_limited(exc):
            raise ProviderError(
                "RATE_LIMITED", f"GitHub rate limit reached while reading {what}", recoverable=True
            ) from exc
        if exc.code in (401, 403):
            raise ProviderError(
                "AUTH_FAILED",
                f"GitHub refused the credential while reading {what}; reconnect or rotate it",
                recoverable=True,
            ) from exc
        if exc.code in (404, 422):
            raise ProviderError("NOT_FOUND", f"GitHub could not find {what}") from exc
        raise ProviderError("API_ERROR", f"GitHub returned {exc.code} while reading {what}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ProviderError("NETWORK", f"Could not reach GitHub to read {what}", recoverable=True) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("UNEXPECTED_SHAPE", f"GitHub returned an invalid response for {what}") from exc


def read_pull_request_checks(connection, *, repo_full_name: str, number: int) -> tuple[str, list[dict]]:
    """Return the pull request's current head SHA and that commit's latest check runs."""
    headers = _headers(connection)
    repo = "/".join(urllib.parse.quote(part, safe="") for part in repo_full_name.split("/"))
    pull = _get(
        connection,
        headers,
        f"/repos/{repo}/pulls/{int(number)}",
        what=f"pull request #{number} in {repo_full_name}",
    )
    head = pull.get("head") if isinstance(pull, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    if not is_resolved_commit_sha(sha):
        raise ProviderError("UNEXPECTED_SHAPE", f"GitHub returned no head commit for pull request #{number}")
    sha = sha.lower()

    runs: list[dict] = []
    for page in range(1, MAX_CHECK_RUN_PAGES + 1):
        query = urllib.parse.urlencode({"filter": "latest", "per_page": _PAGE_SIZE, "page": page})
        listing = _get(
            connection,
            headers,
            f"/repos/{repo}/commits/{sha}/check-runs?{query}",
            what=f"the check runs of {repo_full_name}@{sha[:12]}",
        )
        batch = listing.get("check_runs") if isinstance(listing, dict) else None
        total = listing.get("total_count") if isinstance(listing, dict) else None
        if not isinstance(batch, list) or type(total) is not int:
            raise ProviderError("UNEXPECTED_SHAPE", "GitHub returned an invalid check-run listing")
        runs.extend(run for run in batch if isinstance(run, dict))
        if len(batch) < _PAGE_SIZE or page * _PAGE_SIZE >= total:
            return sha, runs
    raise ProviderError(
        "TOO_MANY_CHECK_RUNS",
        f"{repo_full_name}@{sha[:12]} has more than {MAX_CHECK_RUN_PAGES * _PAGE_SIZE} check runs",
    )
