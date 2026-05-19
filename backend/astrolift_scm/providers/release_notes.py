"""
Pluggable release-notes fetcher.

Retrieves merged-PR descriptions + non-merge commit subjects between
``base_sha`` and ``head_sha`` for a given registered app's source
connection.  GitHub is the only implementation today; GitLab / Bitbucket
can drop in a ``_ReleaseNotesSource`` subclass without touching the
dispatch surface.

Results are cached 15 minutes keyed by (app_guid, base_sha, head_sha).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.core.cache import cache

logger = logging.getLogger(__name__)

_CACHE_TTL = 60 * 15  # 15 minutes


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseNotesCommit:
    sha: str
    subject: str
    author: str
    is_merge: bool


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseNotesPR:
    number: int
    title: str
    body: str
    author: str
    merged_at: str | None
    pr_url: str


@dataclasses.dataclass(frozen=True, slots=True)
class ReleaseNotes:
    base_sha: str
    head_sha: str
    commits: list[ReleaseNotesCommit]
    pull_requests: list[ReleaseNotesPR]
    compare_url: str


def fetch_release_notes(
    connection,
    *,
    app,
    base_sha: str,
    head_sha: str,
) -> ReleaseNotes | None:
    """Return release notes for the range ``base_sha..head_sha``.

    Returns ``None`` when:
    - the connection kind has no implementation
    - both SHAs are identical
    - the SCM call fails (logged at WARNING)

    Callers always get a structured result or None; never an exception.
    """
    if not base_sha or not head_sha or base_sha == head_sha:
        return None

    cache_key = f"release_notes:{app.guid}:{base_sha[:12]}:{head_sha[:12]}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        result = _fetch(connection, app=app, base_sha=base_sha, head_sha=head_sha)
    except Exception:
        logger.warning(
            "release_notes fetch failed app=%s base=%s head=%s",
            app.slug,
            base_sha[:12],
            head_sha[:12],
            exc_info=True,
        )
        return None

    if result is not None:
        cache.set(cache_key, result, _CACHE_TTL)
    return result


def _fetch(connection, *, app, base_sha: str, head_sha: str) -> ReleaseNotes | None:
    if connection is None:
        return None
    kind = getattr(connection, "kind", "")
    if kind in {"github_app_install", "github_pat", "github_oauth_user"}:
        return _fetch_github(connection, app=app, base_sha=base_sha, head_sha=head_sha)
    # GitLab / Bitbucket: return None for now — plug in here later.
    return None


def _github_token(connection) -> str:
    from astrolift_scm.providers.github import _token

    return _token(connection)


def _github_api_base(connection) -> str:
    from astrolift_scm.providers.github import _api_base

    return _api_base(connection)


def _fetch_github(connection, *, app, base_sha: str, head_sha: str) -> ReleaseNotes | None:
    token = _github_token(connection)
    base_url = _github_api_base(connection)

    repo = getattr(app, "source_repo", "") or ""
    if not repo:
        return None

    safe_repo = "/".join(urllib.parse.quote(p, safe="") for p in repo.split("/", 1))
    safe_range = f"{urllib.parse.quote(base_sha)}...{urllib.parse.quote(head_sha)}"
    url = f"{base_url}/repos/{safe_repo}/compare/{safe_range}?per_page=250"

    is_app_install = connection.kind == "github_app_install"
    auth_header = f"Bearer {token}" if is_app_install else f"token {token}"

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": auth_header,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except urllib.error.URLError:
        raise

    commits: list[ReleaseNotesCommit] = []
    pr_numbers_seen: set[int] = set()
    pull_requests: list[ReleaseNotesPR] = []

    for c in payload.get("commits") or []:
        commit_data = c.get("commit") or {}
        message = (commit_data.get("message") or "").strip()
        subject = message.split("\n")[0]
        sha = c.get("sha") or ""
        author_data = c.get("author") or {}
        author = author_data.get("login") or (commit_data.get("author") or {}).get("name") or ""
        is_merge = subject.lower().startswith("merge pull request")
        commits.append(
            ReleaseNotesCommit(sha=sha, subject=subject, author=author, is_merge=is_merge)
        )

    for c in payload.get("commits") or []:
        for pr_raw in c.get("pull_requests") or []:
            number = int(pr_raw.get("number") or 0)
            if not number or number in pr_numbers_seen:
                continue
            pr_numbers_seen.add(number)
            pull_requests.append(
                ReleaseNotesPR(
                    number=number,
                    title=pr_raw.get("title") or "",
                    body="",
                    author="",
                    merged_at=None,
                    pr_url=pr_raw.get("url") or "",
                )
            )

    source_url = getattr(app, "source_url", "") or ""
    if source_url:
        base_repo_url = source_url.rstrip("/").removesuffix(".git")
        compare_url = f"{base_repo_url}/compare/{base_sha[:12]}...{head_sha[:12]}"
    else:
        compare_url = payload.get("permalink_url") or payload.get("html_url") or ""

    return ReleaseNotes(
        base_sha=base_sha,
        head_sha=head_sha,
        commits=commits,
        pull_requests=pull_requests,
        compare_url=compare_url,
    )
