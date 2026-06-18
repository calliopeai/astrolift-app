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
import zipfile
from collections.abc import Callable

from astrolift_scm.models import SourceConnection

# Cap per-file size kept in the tree map. Manifests are a few KB; anything
# larger is not a manifest and would only bloat memory. Files over the cap
# are dropped from the map entirely (discovery never needs them).
_MAX_FILE_BYTES = 256 * 1024

# Cap the total number of entries so a pathological archive (hundreds of
# thousands of tiny files) can't exhaust memory during a scan. Far above any
# realistic ``agents/`` layout.
_MAX_ENTRIES = 50_000


_ZipballFn = Callable[..., bytes]


def repo_tree_from_zipball_bytes(data: bytes) -> dict[str, str]:
    """Unpack archive ``data`` into a ``{repo_relative_path: text}`` map.

    Strips the single top-level directory the host archive nests under, so
    paths come out repo-relative (``agents/a/astrolift.toml``, not
    ``owner-repo-deadbeef/agents/a/astrolift.toml``). Directory entries are
    skipped; binary / oversized / undecodable files are dropped. Pure
    in-memory; no temp files.
    """
    out: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if len(out) >= _MAX_ENTRIES:
                break
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
            try:
                raw = zf.read(info)
            except (RuntimeError, zipfile.BadZipFile):
                # Encrypted / corrupt member — skip it, keep scanning the rest.
                continue
            try:
                out[rel] = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Binary file — not a manifest; drop it.
                continue
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
