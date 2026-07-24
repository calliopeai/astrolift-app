"""GITHUB_PAT repo-tree fallback used by agent-repo registration.

``fetch_repo_tree_with_pat`` is the register-scan fallback: when the per-org
SourceConnection path yields no manifests, registration retries the repo tree
with the install-wide ``settings.GITHUB_PAT`` — the same credential
dispatch-time brief assembly uses. The HTTP boundary is patched; we assert it
builds the authenticated GitHub zipball URL, unpacks the archive into the
repo-relative ``{path: contents}`` map, and no-ops (returns None) when no PAT
is configured.
"""

from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings

from astrolift_scm.providers.repo_tree import fetch_repo_tree_with_pat


def _zip_bytes(members: dict[str, str], top: str = "owner-repo-deadbeef") -> bytes:
    """Build an archive nesting members under a single top-level dir, the
    shape GitHub's zipball uses (repo_tree strips the first segment)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, body in members.items():
            zf.writestr(f"{top}/{path}", body)
    return buf.getvalue()


@override_settings(GITHUB_PAT="tok-abc")
def test_pat_fetch_builds_authed_url_and_unpacks_tree():
    captured = {}
    data = _zip_bytes(
        {
            "agents/emr-bug-triage/astrolift.toml": "astrolift_version = 1\n",
            "README.md": "docs",
        }
    )

    def _fake_get(url, headers=None, timeout=None, allow_redirects=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["redirects"] = allow_redirects
        return SimpleNamespace(content=data, raise_for_status=lambda: None)

    with patch("astrolift_scm.providers.repo_tree.requests.get", _fake_get):
        tree = fetch_repo_tree_with_pat(repo_full_name="steadymd/smd-agents", ref="main")

    assert captured["url"] == "https://api.github.com/repos/steadymd/smd-agents/zipball/main"
    assert captured["headers"].get("Authorization") == "Bearer tok-abc"
    assert captured["redirects"] is True
    # Top-level dir stripped → repo-relative paths.
    assert tree == {
        "agents/emr-bug-triage/astrolift.toml": "astrolift_version = 1\n",
        "README.md": "docs",
    }


@override_settings(GITHUB_PAT="")
def test_pat_fetch_returns_none_without_a_pat():
    # No network call should happen when there's no PAT to fall back to.
    with patch("astrolift_scm.providers.repo_tree.requests.get") as mock_get:
        assert fetch_repo_tree_with_pat(repo_full_name="steadymd/smd-agents", ref="main") is None
    mock_get.assert_not_called()
