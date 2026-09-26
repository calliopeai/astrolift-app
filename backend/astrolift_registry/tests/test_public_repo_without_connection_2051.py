"""A public GitHub repo registers without an SCM connection (#2051)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from astrolift_registry.services import manifest_sync
from astrolift_scm.providers.repo_tree import fetch_public_file

MANIFEST = 'name = "exo-dash"\n\n[[workloads]]\nname = "web"\nkind = "deployment"\n'


def _resp(status: int, body: bytes = b""):
    def _raise():
        if status >= 400:
            raise requests.HTTPError(f"{status}")

    return SimpleNamespace(
        status_code=status, headers={}, raise_for_status=_raise, content=body, iter_content=lambda **_: [body]
    )


def test_fetch_public_file_reads_raw_and_returns_none_on_404():
    calls = []

    def _get(url, **kwargs):
        calls.append((url, kwargs))
        return _resp(200, MANIFEST.encode()) if "astrolift.toml" in url else _resp(404)

    with patch("astrolift_scm.providers.repo_tree.requests.get", _get):
        assert (
            fetch_public_file(repo_full_name="ConflictHQ/exo", path="astrolift.toml", ref="main") == MANIFEST
        )
        assert fetch_public_file(repo_full_name="ConflictHQ/exo", path="missing.toml", ref="main") is None
    assert calls[0][0] == "https://raw.githubusercontent.com/ConflictHQ/exo/main/astrolift.toml"
    assert "headers" not in calls[0][1] and calls[0][1]["allow_redirects"] is False


def test_the_register_scan_falls_back_to_the_public_archive_when_there_is_no_pat():
    tree = {"agents/a/astrolift.toml": "x"}
    with (
        patch("astrolift_scm.providers.repo_tree.fetch_repo_tree_with_pat", return_value=None),
        patch("astrolift_scm.providers.repo_tree.fetch_public_repo_tree", return_value=tree) as public,
    ):
        assert manifest_sync._pat_fallback_tree("ConflictHQ/agent-suite", "main") == (tree, None)
    public.assert_called_once_with(repo_full_name="ConflictHQ/agent-suite", ref="main")


def test_a_private_repo_without_credentials_says_so():
    with (
        patch("astrolift_scm.providers.repo_tree.fetch_repo_tree_with_pat", return_value=None),
        patch(
            "astrolift_scm.providers.repo_tree.fetch_public_repo_tree", side_effect=requests.HTTPError("404")
        ),
    ):
        files, error = manifest_sync._pat_fallback_tree("acme/private", "main")
    assert files == {} and "not publicly readable" in error


def test_a_connection_that_found_nothing_still_means_no_agents():
    with (
        patch("astrolift_scm.providers.repo_tree.fetch_repo_tree_with_pat", return_value=None),
        patch(
            "astrolift_scm.providers.repo_tree.fetch_public_repo_tree", side_effect=requests.HTTPError("404")
        ),
    ):
        assert manifest_sync._pat_fallback_tree("acme/private", "main", report_anonymous=False) == ({}, None)


@pytest.mark.django_db
def test_resync_reads_a_public_manifest_with_no_connection(monkeypatch):
    from astrolift_registry.models import RegisteredApp
    from core.tests.utils.scope_world import ScopeWorld

    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    app = ScopeWorld("2051").medops_app
    app.source_repo = "ConflictHQ/exo"
    monkeypatch.setattr(manifest_sync, "_pick_source_connection", lambda app: None)
    with patch("astrolift_scm.providers.repo_tree.fetch_public_file", return_value=None) as public:
        result = manifest_sync._resync_app_manifest_from_repo(app)
    public.assert_called_once_with(repo_full_name="ConflictHQ/exo", path="astrolift.toml", ref="main")
    assert result.status == "fetch_failed" and "could not be read anonymously" in result.error

    app.source_kind = RegisteredApp.SourceKind.GITLAB
    result = manifest_sync._resync_app_manifest_from_repo(app)
    assert "no active source connection" in result.error
