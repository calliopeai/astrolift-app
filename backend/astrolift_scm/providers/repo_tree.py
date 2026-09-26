"""
Fetch a repo's file tree as a ``{path: contents}`` map (spec 33, PR-3).

The provider dispatcher exposes ``fetch_file`` (one file) and
``fetch_zipball`` (the whole source archive as bytes) but no
directory-listing call. Monorepo agent discovery needs to *enumerate*
the files under ``agents/`` to find every ``agents/<slug>/astrolift.toml``,
so this module is the thin adapter that turns the existing ``fetch_zipball``
bytes into the ``{repo_relative_path: text}`` map the discovery scanner
(:func:`astrolift_manifest.discover.scan_agent_manifests`) consumes.

This is deliberately *not* a new SCM client: it reuses the host-dispatched
``fetch_zipball`` for the network call and only adds in-memory unzip +
top-level-directory stripping. Both the GitHub and GitLab archives nest
everything under a single top-level directory (e.g.
``owner-repo-<sha>/...``), so we strip the first path segment uniformly.

Only small text files are kept in the map (a manifest is a few KB). Large
or clearly-binary blobs are dropped so a big monorepo's archive doesn't
balloon the map — discovery only ever reads ``astrolift.toml`` files, and
the scanner skips any path whose contents aren't ``str`` anyway.
"""

from __future__ import annotations

import io
import logging
import stat
import zipfile
from collections.abc import Callable

import requests

from astrolift_scm.models import SourceConnection
from astrolift_scm.providers.archive_download import read_requests_response

log = logging.getLogger(__name__)

# Cap per-file size kept in the tree map. Manifests are a few KB; anything
# larger is not a manifest and would only bloat memory. Files over the cap
# are dropped from the map entirely (discovery never needs them).
_MAX_FILE_BYTES = 256 * 1024

# Bound the compressed response and retained UTF-8 map as well as individual
# members. A ZIP full of thousands of just-under-the-per-file-limit entries is
# otherwise enough to consume many gigabytes while merely scanning manifests.
_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_RETAINED_TEXT_BYTES = 32 * 1024 * 1024

# Unauthenticated GitHub archive endpoint for a PUBLIC repo. Unlike the
# provider-dispatched ``fetch_zipball`` (which needs a per-org
# ``SourceConnection`` credential), this streams a public repo's archive with
# no auth — the right fit for the built-in skills catalogue, which every
# install must be able to read without first registering an SCM connection.
# The archive nests under a single ``<repo>-<ref>/`` top-level dir, the same
# shape :func:`repo_tree_from_zipball_bytes` already strips.
_GITHUB_ARCHIVE_TIMEOUT_SECONDS = 60

# Cap the total number of entries so a pathological archive (hundreds of
# thousands of tiny files) can't exhaust memory during a scan. Far above any
# realistic ``agents/`` layout.
_MAX_ENTRIES = 50_000


_ZipballFn = Callable[..., bytes]


class RepoTreeArchiveError(ValueError):
    """A source archive is unsafe or too large to inspect deterministically."""


def repo_tree_from_zipball_bytes(data: bytes) -> dict[str, str]:
    """Unpack archive ``data`` into a ``{repo_relative_path: text}`` map.

    Strips the single top-level directory the host archive nests under, so
    paths come out repo-relative (``agents/a/astrolift.toml``, not
    ``owner-repo-deadbeef/agents/a/astrolift.toml``). Directory entries are
    skipped; binary / oversized / undecodable files are dropped. Pure
    in-memory; no temp files.
    """
    if len(data) > _MAX_ARCHIVE_BYTES:
        raise RepoTreeArchiveError(f"source archive exceeds the {_MAX_ARCHIVE_BYTES}-byte compressed limit")

    out: dict[str, str] = {}
    retained_bytes = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        if len(infos) > _MAX_ENTRIES:
            raise RepoTreeArchiveError(f"source archive exceeds the {_MAX_ENTRIES}-entry limit")
        for info in infos:
            if info.is_dir():
                continue
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) == stat.S_IFLNK:
                raise RepoTreeArchiveError(f"source archive contains a symbolic link: {info.filename}")
            if info.file_size > _MAX_FILE_BYTES:
                continue
            # Strip the leading ``<top-level-dir>/`` the host wraps the tree
            # in. An archive entry always has at least the top dir + the
            # file name; an entry with no slash (shouldn't happen) is left
            # as-is rather than dropped.
            name = info.filename
            rel = name.split("/", 1)[1] if "/" in name else name
            if not rel:
                continue
            parts = rel.split("/")
            if rel.startswith("/") or "\\" in rel or any(part in {"", ".", ".."} for part in parts):
                raise RepoTreeArchiveError(f"source archive contains an unsafe path: {name}")
            if rel in out:
                raise RepoTreeArchiveError(f"source archive contains a duplicate path: {rel}")
            try:
                raw = zf.read(info)
            except (RuntimeError, zipfile.BadZipFile):
                # Encrypted / corrupt member — skip it, keep scanning the rest.
                continue
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Binary file — not a manifest; drop it.
                continue
            retained_bytes += len(raw)
            if retained_bytes > _MAX_RETAINED_TEXT_BYTES:
                raise RepoTreeArchiveError(
                    "source archive contains too much retained UTF-8 content "
                    f"(limit {_MAX_RETAINED_TEXT_BYTES} bytes)"
                )
            out[rel] = decoded
    return out


def fetch_repo_tree(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    ref: str,
    zipball_fetcher: _ZipballFn | None = None,
) -> dict[str, str]:
    """Fetch ``repo_full_name`` at ``ref`` and return its file tree map.

    Reuses the provider dispatcher's ``fetch_zipball`` (host-routed by
    ``connection.kind``) for the network call, then unpacks in-memory. The
    fetcher is injectable via ``zipball_fetcher`` so the discovery service /
    tests can drive a fixture tree without touching the network; production
    callers pass nothing and get the real dispatcher. Raises whatever
    ``fetch_zipball`` raises (``ProviderError``) on auth / network / an
    unsupported host so the resolver translates it to a clean envelope.
    """
    fetch = zipball_fetcher
    if fetch is None:
        from astrolift_scm.providers import fetch_zipball

        fetch = fetch_zipball
    data = fetch(connection, repo_full_name=repo_full_name, ref=ref)
    return repo_tree_from_zipball_bytes(data)


def fetch_public_repo_tree(*, repo_full_name: str, ref: str) -> dict[str, str]:
    """Fetch a PUBLIC GitHub repo's file tree map with NO authentication.

    Used for the built-in skills catalogue (``calliopeai/astrolift-skills``):
    a public repo every install must read without first registering an SCM
    ``SourceConnection``. Streams ``https://github.com/<repo>/archive/<ref>.zip``
    (anonymous, no token) and reuses :func:`repo_tree_from_zipball_bytes` to
    unpack into the ``{repo_relative_path: text}`` map the skill loader
    consumes.

    Raises ``requests.HTTPError`` on a non-2xx response and
    ``requests.RequestException`` on a network failure — the caller (the skill
    resolver) catches these and degrades to a clear, non-fatal resolution
    error so a catalogue outage never aborts agent registration.
    """
    owner_repo = repo_full_name.strip("/")
    archive_url = f"https://github.com/{owner_repo}/archive/{ref}.zip"
    resp = requests.get(
        archive_url,
        timeout=_GITHUB_ARCHIVE_TIMEOUT_SECONDS,
        allow_redirects=True,
        stream=True,
    )
    resp.raise_for_status()
    return repo_tree_from_zipball_bytes(read_requests_response(resp, limit=_MAX_ARCHIVE_BYTES))


def fetch_public_file(*, repo_full_name: str, path: str, ref: str) -> str | None:
    """One file of a PUBLIC GitHub repo, read with NO authentication (#2051).

    ``None`` when GitHub answers 404: the file is absent, or the repo is
    private, which an anonymous read cannot tell apart. Raises
    ``requests.HTTPError`` / ``requests.RequestException`` otherwise, so the
    caller reports it as a fetch failure.
    """
    from urllib.parse import quote

    url = "https://raw.githubusercontent.com/{}/{}/{}".format(
        quote(repo_full_name.strip("/"), safe="/"),
        quote(ref, safe="/"),
        quote(path.lstrip("/"), safe="/"),
    )
    resp = requests.get(url, timeout=_GITHUB_ARCHIVE_TIMEOUT_SECONDS, allow_redirects=False, stream=True)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return read_requests_response(resp, limit=_MAX_ARCHIVE_BYTES).decode("utf-8")


def fetch_repo_tree_with_pat(*, repo_full_name: str, ref: str) -> dict[str, str] | None:
    """Fetch a PRIVATE GitHub repo's file tree map using ``settings.GITHUB_PAT``.

    The install-wide PAT is the same credential dispatch-time brief assembly
    (:func:`astrolift_agents.services.brief_assembler._fetch_zipball`) uses, so
    this is the fallback that keeps agent-repo *registration* consistent with
    *dispatch*: a stale, under-scoped, or missing per-org ``SourceConnection``
    can't strand a repo the PAT can otherwise read. Streams the authenticated
    GitHub API zipball and reuses :func:`repo_tree_from_zipball_bytes`.

    Returns ``None`` when no PAT is configured (nothing to fall back to).
    Raises ``requests.HTTPError`` / ``requests.RequestException`` on a bad
    response so the caller can treat it as a non-fatal fallback miss.
    """
    from django.conf import settings

    pat = getattr(settings, "GITHUB_PAT", "") or ""
    if not pat:
        return None

    owner_repo = repo_full_name.strip("/")
    zipball_url = f"https://api.github.com/repos/{owner_repo}/zipball/{ref}"
    resp = requests.get(
        zipball_url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {pat}",
            "User-Agent": "astrolift",
        },
        timeout=_GITHUB_ARCHIVE_TIMEOUT_SECONDS,
        allow_redirects=True,
        stream=True,
    )
    resp.raise_for_status()
    return repo_tree_from_zipball_bytes(read_requests_response(resp, limit=_MAX_ARCHIVE_BYTES))
